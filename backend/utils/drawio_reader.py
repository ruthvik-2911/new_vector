"""
drawio_reader.py
----------------
Parses .drawio (diagrams.net) files DIRECTLY from their structure instead of
"looking" at them with a vision model. Because the nodes and arrows are exact
data in the file, this is accurate at ANY size - a 50-node workflow reads as
reliably as a 5-node one, with no vision guessing and no CPU/heat cost.

Output format matches diagram_reader.py (TITLE / NODES / CONNECTIONS /
DECISIONS) so the rest of the RAG pipeline (embed -> index -> chat) is
unchanged.

.drawio files store each page as mxGraph XML. The page may be stored either:
  - uncompressed (a plain <mxGraphModel> element), or
  - compressed (base64 + raw-deflate + url-encoded text inside <diagram>).
Both are handled below. No new dependencies (stdlib only).
"""

import base64
import re
import zlib
import urllib.parse
import xml.etree.ElementTree as ET


def _strip_html(value: str) -> str:
    """draw.io labels can contain HTML (<br>, <b>, etc). Flatten to text."""
    if not value:
        return ""
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)        # drop remaining tags
    value = (value.replace("&nbsp;", " ")
                  .replace("&amp;", "&")
                  .replace("&lt;", "<")
                  .replace("&gt;", ">")
                  .replace("&quot;", '"'))
    return value.strip()


def _decompress_diagram(text: str):
    """Decode a compressed <diagram> payload to mxGraphModel XML, or None."""
    try:
        data = base64.b64decode(text)
        xml = zlib.decompress(data, -15).decode("utf-8")   # raw deflate
        return urllib.parse.unquote(xml)
    except Exception:
        return None


def _node_type(style: str) -> str:
    """Best-effort node type from the draw.io shape style string."""
    s = (style or "").lower()
    if "rhombus" in s:
        return "decision"
    if "ellipse" in s or "terminator" in s:
        return "start/end"
    if "mxgraph.flowchart.decision" in s:
        return "decision"
    return "process"


def _parse_model(model: ET.Element, page_name: str) -> str:
    cells = model.findall(".//mxCell")

    labels = {}      # cell id -> label text  (for resolving edge endpoints)
    nodes = []       # (label, type)
    edges = []       # (source_id, target_id, edge_label)

    for c in cells:
        cid = c.get("id")
        val = _strip_html(c.get("value") or "")

        if c.get("vertex") == "1":
            labels[cid] = val if val else f"[unlabeled {cid}]"
            nodes.append((labels[cid], _node_type(c.get("style", ""))))
        elif c.get("edge") == "1":
            edges.append((c.get("source"), c.get("target"), val))

    def name_of(cid):
        return labels.get(cid, f"[{cid}]" if cid else "[dangling]")

    lines = [f"PAGE: {page_name}", "", "NODES:"]
    for label, ntype in nodes:
        lines.append(f'- TYPE={ntype} | TEXT="{label}"')

    lines += ["", "CONNECTIONS (one line per arrow):"]
    for src, tgt, lbl in edges:
        lbl_part = f"[{lbl}]" if lbl else "[]"
        lines.append(f'- "{name_of(src)}" --{lbl_part}--> "{name_of(tgt)}"')

    # group decision branches for easy path-tracing
    decisions = [n for n in nodes if n[1] == "decision"]
    if decisions:
        lines += ["", "DECISIONS:"]
        for label, _ in decisions:
            # find the id for this decision label
            dec_id = next((cid for cid, l in labels.items() if l == label), None)
            outs = [(lbl, name_of(tgt)) for src, tgt, lbl in edges if src == dec_id]
            branch = "; ".join(f'{lbl or "?"} -> "{tgt}"' for lbl, tgt in outs)
            lines.append(f'- At "{label}": {branch}')

    return "\n".join(lines)


def read_drawio(path: str) -> str:
    raw = open(path, encoding="utf-8").read()
    root = ET.fromstring(raw)

    diagrams = root.findall(".//diagram")
    if not diagrams:
        # some files are just a bare <mxGraphModel>
        diagrams = [root]

    blocks = []
    for d in diagrams:
        page_name = d.get("name", "diagram") if d.tag == "diagram" else "diagram"
        inner = d.find("mxGraphModel")
        if inner is not None:
            model = inner
        elif d.tag == "mxGraphModel":
            model = d
        else:
            decoded = _decompress_diagram((d.text or "").strip())
            if not decoded:
                continue
            model = ET.fromstring(decoded)
        blocks.append(_parse_model(model, page_name))

    header = [
        "WORKFLOW DIAGRAM (parsed directly from .drawio structure)",
        f"SOURCE FILE: {path}",
        "",
    ]
    return "\n".join(header) + "\n\n".join(blocks)


# Quick test:  python backend/utils/drawio_reader.py <file.drawio>
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python drawio_reader.py <file.drawio>")
        raise SystemExit(1)
    print(read_drawio(sys.argv[1]))

def parse_drawio_structured(path: str):
    """
    Returns a dict with {"nodes": [...], "edges": [...]} for GraphDB ingestion.
    Each node: {"id": str, "label": str, "type": str}
    Each edge: {"source": str, "target": str, "label": str}
    """
    raw = open(path, encoding="utf-8").read()
    root = ET.fromstring(raw)

    diagrams = root.findall(".//diagram")
    if not diagrams:
        diagrams = [root]

    all_nodes = []
    all_edges = []

    for d in diagrams:
        inner = d.find("mxGraphModel")
        if inner is not None:
            model = inner
        elif d.tag == "mxGraphModel":
            model = d
        else:
            decoded = _decompress_diagram((d.text or "").strip())
            if not decoded:
                continue
            model = ET.fromstring(decoded)
            
        cells = model.findall(".//mxCell")
        
        labels = {}
        for c in cells:
            cid = c.get("id")
            val = _strip_html(c.get("value") or "")
            if c.get("vertex") == "1":
                lbl = val if val else f"node_{cid}"
                labels[cid] = lbl
                all_nodes.append({
                    "id": cid,
                    "label": lbl,
                    "type": _node_type(c.get("style", ""))
                })
        
        for c in cells:
            if c.get("edge") == "1":
                src = c.get("source")
                tgt = c.get("target")
                if src in labels and tgt in labels:
                    all_edges.append({
                        "source": src,
                        "target": tgt,
                        "label": _strip_html(c.get("value") or "")
                    })
                    
    return {"nodes": all_nodes, "edges": all_edges}
