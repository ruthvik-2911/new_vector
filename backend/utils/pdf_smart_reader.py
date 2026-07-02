"""
pdf_smart_reader.py  (v8.1 - + JSON graph export for query-layer path tracing)
--------------------------------------------------------------------------------------
Builds on v4. Adds two things needed for PATH TRACING in chat:

  * CROSS-PAGE STITCHING: all pages are parsed into ONE global node/edge graph
    (keyed by node label), and sequentially-numbered nodes that got split across
    a page boundary are reconnected. The graph is now whole, not per-page.

  * PATH PRE-COMPUTATION: downstream paths are traced IN CODE (exact graph walk)
    and emitted as natural-language text ("From Receive: Receive -> Validate -> ...").
    These path lines get indexed, so when a user asks "what's the path from X" or
    "what happens after X", RAG retrieves the pre-computed path and the LLM just
    reads it back. The tracing is done by code; the model only narrates it - which
    a small local model can do reliably.

NO VISION MODEL. Cascade per file:
  1. VECTOR DIAGRAM -> global graph reconstruction + path tracing (pdfplumber)
  2. TEXT DOCUMENT  -> plain text
  3. SCANNED PDF    -> OCR text fallback (pdf_ocr_reader)

HONEST LIMITS: geometric/heuristic extraction; very dense pages can merge a few
shapes. Path tracing is exact over the recovered graph, but the LLM's context
limits how long a path it can read back at once - short/medium paths trace
cleanly, a full 320-node single answer will be partial. Cross-page stitch links
sequential nodes; non-sequential cross-page arrows are matched within-page only.
For guaranteed-exact structure use the .drawio source.

Dependency: pdfplumber, pymupdf (fitz), pypdf.
"""

import math
import re
import os
import json
import pdfplumber
import fitz
from pypdf import PdfReader
from collections import defaultdict, deque

TEXT_THRESHOLD = 200
MIN_LINES_FOR_DIAGRAM = 8
ENDPOINT_MAX_DIST = 70
EDGE_LABEL_MAX_WORDS = 3
EDGE_LABEL_MAX_DIST = 55
MAX_PATH_LEN = 40          # cap on a single traced path
MAX_PATHS_EMITTED = 30     # cap how many start-node paths we write out


def _pypdf_text(path):
    try:
        r = PdfReader(path)
        return "\n".join((p.extract_text() or "") for p in r.pages)
    except Exception:
        return ""


def _wc(w):
    return ((w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2)


def _shape_bboxes(page):
    shapes = []
    for r in page.rects:
        w, h = r["x1"] - r["x0"], r["bottom"] - r["top"]
        if 20 < w < 420 and 10 < h < 140 and (w / max(h, 1)) < 14:
            shapes.append((r["x0"], r["top"], r["x1"], r["bottom"]))
    for cv in page.curves:
        w, h = cv["x1"] - cv["x0"], cv["bottom"] - cv["top"]
        if 20 < w < 420 and 10 < h < 140 and (w / max(h, 1)) < 14:
            shapes.append((cv["x0"], cv["top"], cv["x1"], cv["bottom"]))
    uniq = []
    for s in shapes:
        dup = False
        for u in uniq:
            ix0, iy0 = max(s[0], u[0]), max(s[1], u[1])
            ix1, iy1 = min(s[2], u[2]), min(s[3], u[3])
            if ix1 > ix0 and iy1 > iy0:
                ia = (ix1 - ix0) * (iy1 - iy0)
                sa = (s[2] - s[0]) * (s[3] - s[1])
                ua = (u[2] - u[0]) * (u[3] - u[1])
                if ia > 0.5 * min(sa, ua):
                    dup = True
                    break
        if not dup:
            uniq.append(s)
    return uniq


def _num(label):
    m = re.match(r"\s*(\d+)\s*:", label)
    return int(m.group(1)) if m else None


def _build_global_graph(pdf):
    """Parse ALL pages into one graph. Returns (page_blocks, edges, nodes_order)."""
    edges = defaultdict(list)            # label -> [(branch_label, target_label)]
    nodes_order = []                     # preserve first-seen order
    seen_labels = set()
    page_blocks = []                     # per-page text for the structured dump

    for pageno, page in enumerate(pdf.pages, 1):
        shapes = _shape_bboxes(page)
        words = page.extract_words()

        nodes = []
        inside = set()
        for bb in shapes:
            ws = []
            for i, w in enumerate(words):
                cx, cy = _wc(w)
                if bb[0] <= cx <= bb[2] and bb[1] <= cy <= bb[3]:
                    ws.append((i, w))
            if not ws:
                continue
            for i, _ in ws:
                inside.add(i)
            ws.sort(key=lambda t: (round(t[1]["top"] / 6), t[1]["x0"]))
            label = " ".join(t[1]["text"] for t in ws).strip()
            nodes.append({"label": label, "c": ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)})
            if label not in seen_labels:
                seen_labels.add(label)
                nodes_order.append(label)

        edge_labels = []
        for i, w in enumerate(words):
            if i in inside:
                continue
            t = w["text"].strip()
            if t and len(t.split()) <= EDGE_LABEL_MAX_WORDS:
                edge_labels.append((_wc(w), t))

        def nearest_node(pt):
            best, bd = None, 1e9
            for n in nodes:
                d = math.hypot(pt[0] - n["c"][0], pt[1] - n["c"][1])
                if d < bd:
                    bd, best = d, n
            return best, bd

        def nearest_label(mid):
            best, bd = None, EDGE_LABEL_MAX_DIST
            for lc, t in edge_labels:
                d = math.hypot(mid[0] - lc[0], mid[1] - lc[1])
                if d < bd:
                    bd, best = d, t
            return best

        page_edges = set()
        for ln in page.lines:
            a = (ln["x0"], ln["top"]); b = (ln["x1"], ln["bottom"])
            na, da = nearest_node(a); nb, db = nearest_node(b)
            if na and nb and na is not nb and da < ENDPOINT_MAX_DIST and db < ENDPOINT_MAX_DIST:
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                lbl = nearest_label(mid) or ""
                edges[na["label"]].append((lbl, nb["label"]))
                page_edges.add((na["label"], lbl, nb["label"]))

        # per-page structured block
        blk = [f"=== PAGE {pageno} ==="]
        blk.append("NODES:")
        for n in nodes:
            blk.append(f'- "{n["label"]}"')
        blk.append("")
        blk.append("CONNECTIONS:")
        for a, lbl, b in sorted(page_edges):
            if lbl:
                blk.append(f'- If "{a}" is {lbl}, then go to "{b}"')
            else:
                blk.append(f'- "{a}" connects to "{b}"')
        page_blocks.append("\n".join(blk))

    return page_blocks, edges, nodes_order


def _stitch_cross_page(edges, nodes_order):
    """Reconnect sequentially-numbered nodes split across page boundaries."""
    by_num = {}
    for lab in nodes_order:
        n = _num(lab)
        if n is not None:
            by_num[n] = lab
    stitched = 0
    for n in sorted(by_num):
        if (n + 1) in by_num:
            src, tgt = by_num[n], by_num[n + 1]
            if tgt not in [t for _, t in edges[src]]:
                edges[src].append(("(next)", tgt))
                stitched += 1
    return stitched


def _short(label):
    # "12:Deploy" -> "Deploy"; keep full label if no numeric prefix
    return label.split(":", 1)[-1].strip() if ":" in label else label


def _downstream_path(start, edges, maxlen=MAX_PATH_LEN):
    path = [start]
    cur = start
    seen = {start}
    for _ in range(maxlen):
        nxts = edges.get(cur, [])
        if not nxts:
            break
        nxt = None
        for bl, t in nxts:            # prefer the sequential main line
            if bl == "(next)":
                nxt = t
                break
        if not nxt:
            nxt = nxts[0][1]
        if nxt in seen:
            break
        path.append(nxt)
        seen.add(nxt)
        cur = nxt
    return path



def _path_between(a_label, b_label, edges):
    """BFS shortest path between two node labels over the edge graph."""
    q = deque([[a_label]])
    seen = {a_label}
    while q:
        p = q.popleft()
        if p[-1] == b_label:
            return p
        for _, nxt in [(bl, t) for bl, t in _out_edges(edges, p[-1])]:
            if nxt not in seen:
                seen.add(nxt)
                q.append(p + [nxt])
    return None


def _out_edges(edges, node):
    # edges stores label -> [(branch_label, target)]
    return edges.get(node, [])


def _emit_paths(edges, nodes_order):
    """Natural-language downstream paths + decision branches, for indexing."""
    out = ["WORKFLOW PATHS (downstream route from each step; use these to trace "
           "'what happens after X' or 'path from X'):"]
    count = 0
    for start in nodes_order:
        if count >= MAX_PATHS_EMITTED:
            break
        p = _downstream_path(start, edges)
        if len(p) < 2:
            continue
        readable = " -> ".join(_short(x) for x in p)
        out.append(f'- From "{_short(start)}": {readable}')
        count += 1

    # decision branches - emit in MULTIPLE natural phrasings so questions like
    # "what happens if X fails / doesn't happen / is rejected" retrieve a ready
    # answer instead of needing the model to reason about the negative branch.
    NEG = {"no", "invalid", "fail", "failed", "failure", "reject", "rejected",
           "timeout", "cache miss", "fallback", "error"}
    dec = []
    for src_lbl, outs in edges.items():
        branches = [(bl, t) for bl, t in outs if bl and bl != "(next)"]
        if not branches:
            continue
        s = _short(src_lbl)
        compact = "; ".join(f"if {bl} then {_short(t)}" for bl, t in branches)
        dec.append(f'- At "{s}": {compact}')
        # expanded per-branch phrasings
        for bl, t in branches:
            tgt = _short(t)
            low = bl.strip().lower()
            dec.append(f'  - If "{s}" is {bl}, the workflow goes to "{tgt}".')
            if low in NEG:
                dec.append(f'  - If "{s}" fails or does not happen (branch: {bl}), '
                           f'the workflow goes to "{tgt}".')
                dec.append(f'  - What happens if "{s}" does not succeed: go to "{tgt}".')
            else:
                dec.append(f'  - If "{s}" succeeds or happens (branch: {bl}), '
                           f'the workflow goes to "{tgt}".')
    if dec:
        out.append("")
        out.append("DECISION POINTS (with outcomes if a step succeeds or fails):")
        out.extend(dec)

    # Exact X-to-Y segments between numbered nodes, so "step X to Y" / "from step X
    # to step Y" retrieves a precomputed exact path segment the LLM just reads back.
    by_num = {}
    for lab in nodes_order:
        m = re.match(r"\s*(\d+)\s*:", lab)
        if m:
            by_num[int(m.group(1))] = lab
    if len(by_num) >= 4:
        nums = sorted(by_num)
        out.append("")
        out.append("PATH SEGMENTS (exact route between two steps; use for "
                    "'from step X to step Y' or 'steps X to Y'):")
        seg_count = 0
        # emit segments from several anchor starts to a spread of targets
        anchors = nums[::max(1, len(nums) // 20)]  # ~20 start points for wider coverage
        for a in anchors:
            for b in nums:
                if b <= a:
                    continue
                if (b - a) < 3:
                    continue
                if seg_count >= 150:
                    break
                p = _path_between(by_num[a], by_num[b], edges)
                if p and len(p) >= 2:
                    readable = " -> ".join(_short(x) for x in p)
                    out.append(f'- Path from step {a} to step {b}: {readable}')
                    seg_count += 1
            if seg_count >= 150:
                break

    return "\n".join(out)



def _export_graph_json(path, nodes_order, edges, stitched, page_count):
    """Write the reconstructed graph as JSON next to the source PDF, so the query
    layer can load nodes+edges and compute exact paths on demand (BFS) instead of
    relying on RAG retrieval. Node ids are the labels; numeric prefix kept as 'num'."""
    node_list = []
    for lab in nodes_order:
        m = re.match(r"\s*(\d+)\s*:", lab)
        node_list.append({
            "id": lab,
            "num": int(m.group(1)) if m else None,
            "text": _short(lab),
        })
    edge_list = []
    for src_lbl, outs in edges.items():
        for branch, tgt in outs:
            edge_list.append({
                "from": src_lbl,
                "to": tgt,
                "branch": branch if branch and branch != "(next)" else None,
            })
    graph = {
        "source_file": os.path.basename(path),
        "node_count": len(node_list),
        "edge_count": len(edge_list),
        "page_count": page_count,
        "cross_page_links_stitched": stitched,
        "nodes": node_list,
        "edges": edge_list,
    }
    out_path = os.path.splitext(path)[0] + ".graph.json"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(graph, f, indent=2)
        print(f"[PDF Smart Reader] Graph exported: {out_path} "
              f"({len(node_list)} nodes, {len(edge_list)} edges)")
    except Exception as e:
        print(f"[PDF Smart Reader] Graph export failed: {e}")
    return out_path


def read_pdf_smart(path):
    # Count node-like SHAPES (rects + curves) with pdfplumber - the reliable
    # diagram signal. Real draw.io PDF exports render connectors as thousands of
    # tiny line segments, which wrecks a words-per-line heuristic; shape count does
    # not have that problem. If there are many boxes/diamonds, it is a diagram.
    total_rects = total_curves = total_plumber_lines = 0
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            total_rects += len(pg.rects)
            total_curves += len(pg.curves)
            total_plumber_lines += len(pg.lines)
    total_shapes = total_rects + total_curves

    text = _pypdf_text(path)
    text_len = len(text.strip())

    print(f"[PDF Smart Reader] {path}: rects={total_rects}, curves={total_curves}, "
          f"lines={total_plumber_lines}, shapes={total_shapes}, text_chars={text_len}")

    # Diagram if there are enough node-like shapes (boxes/diamonds), regardless of
    # how many stray line segments the export produced.
    if total_shapes >= 15:
        print("[PDF Smart Reader] Vector diagram -> reconstructing (pdfplumber, all pages + path tracing)")
        with pdfplumber.open(path) as pdf:
            pc = len(pdf.pages)
            page_blocks, edges, nodes_order = _build_global_graph(pdf)
        stitched = _stitch_cross_page(edges, nodes_order)
        _export_graph_json(path, nodes_order, edges, stitched, pc)
        total_edges = sum(len(v) for v in edges.values())
        paths_text = _emit_paths(edges, nodes_order)

        header = [
            "WORKFLOW DIAGRAM PDF (structure reconstructed from vector geometry)",
            f"SOURCE FILE: {path}",
            f"SUMMARY: {len(nodes_order)} nodes and {total_edges} connections across "
            f"{pc} page(s); {stitched} cross-page links stitched.",
            "NOTE: connections inferred from geometry; paths traced in code. For exact "
            "structure use the .drawio source if available.",
            "",
        ]
        return ("\n".join(header) + "\n" + paths_text + "\n\n"
                + "\n\n".join(page_blocks))

    if text_len >= TEXT_THRESHOLD:
        print("[PDF Smart Reader] Text-rich PDF -> text extraction")
        return text

    print("[PDF Smart Reader] Scanned/image PDF -> OCR")
    from backend.utils.pdf_ocr_reader import read_pdf_ocr
    return read_pdf_ocr(path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python pdf_smart_reader.py <file.pdf>"); raise SystemExit(1)
    print(read_pdf_smart(sys.argv[1]))
