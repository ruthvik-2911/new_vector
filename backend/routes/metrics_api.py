"""
metrics_api.py - richer metrics endpoint.
Reads the FULL activity log (not just the last 10) and computes real aggregates:
routing confidence, low-confidence routing count, true success/fail, and full
event/agent breakdowns.

Wire it in main.py with:
    from backend.routes import metrics_api
    app.include_router(metrics_api.router)
"""

import os
import re
import json
import glob
from fastapi import APIRouter

router = APIRouter()

DATA_DIR = "data/Policies"


def _docs_by_type():
    """Count files in the data folder by extension."""
    counts = {}
    if os.path.isdir(DATA_DIR):
        for f in os.listdir(DATA_DIR):
            if f.endswith(".graph.json"):
                continue  # derived artifact, not a source document
            ext = os.path.splitext(f)[1].lower().lstrip(".") or "other"
            # group emails separately if in emails folder handled elsewhere
            counts[ext] = counts.get(ext, 0) + 1
    # include emails folder if present
    if os.path.isdir("emails"):
        n = len([f for f in os.listdir("emails") if f.endswith((".txt", ".eml"))])
        if n:
            counts["email"] = counts.get("email", 0) + n
    return counts


def _diagram_stats():
    """Aggregate node/edge counts from any *.graph.json the reader wrote."""
    total_nodes = 0
    total_edges = 0
    diagrams = 0
    biggest = {"file": None, "nodes": 0}
    for jp in glob.glob(os.path.join(DATA_DIR, "*.graph.json")):
        try:
            g = json.load(open(jp, encoding="utf-8"))
            n = g.get("node_count", 0)
            e = g.get("edge_count", 0)
            total_nodes += n
            total_edges += e
            diagrams += 1
            if n > biggest["nodes"]:
                biggest = {"file": os.path.basename(jp).replace(".graph.json", ""), "nodes": n}
        except Exception:
            pass
    return {
        "diagram_count": diagrams,
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "biggest": biggest,
    }


def _confidence_buckets(confs):
    buckets = {"0.0-0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0, "0.6-0.8": 0, "0.8-1.0": 0}
    for c in confs:
        if c < 0.2: buckets["0.0-0.2"] += 1
        elif c < 0.4: buckets["0.2-0.4"] += 1
        elif c < 0.6: buckets["0.4-0.6"] += 1
        elif c < 0.8: buckets["0.6-0.8"] += 1
        else: buckets["0.8-1.0"] += 1
    return buckets



ACTIVITY_LOG = "backend/storage/activity_log.json"
LOW_CONF_THRESHOLD = 0.35


@router.get("/api/metrics/detailed")
def detailed_metrics():
    try:
        with open(ACTIVITY_LOG, "r", encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        log = []

    total = len(log)
    ok = sum(1 for e in log if str(e.get("status", "")).lower() == "success")
    failed = total - ok
    success_rate = round(ok / total * 100) if total else 0

    confs = []
    for e in log:
        m = re.search(r"Conf:\s*([0-9.]+)", str(e.get("source", "")))
        if m:
            confs.append(float(m.group(1)))
    avg_conf = round(sum(confs) / len(confs), 2) if confs else None
    low_conf = sum(1 for c in confs if c < LOW_CONF_THRESHOLD)

    events = {}
    agents = {}
    for e in log:
        ev = e.get("event", "Unknown")
        events[ev] = events.get(ev, 0) + 1
        m = re.search(r"->\s*(\w+)\s*Agent", ev) or re.search(r"\u2192\s*(\w+)\s*Agent", ev)
        if m:
            agents[m.group(1)] = agents.get(m.group(1), 0) + 1

    recent = sorted(log, key=lambda e: e.get("timestamp", ""), reverse=True)[:15]

    return {
        "total_events": total,
        "success_count": ok,
        "failed_count": failed,
        "success_rate": success_rate,
        "avg_confidence": avg_conf,
        "total_routings": len(confs),
        "low_confidence_count": low_conf,
        "low_confidence_threshold": LOW_CONF_THRESHOLD,
        "event_types": events,
        "agent_usage": agents,
        "recent": recent,
        "confidence_buckets": _confidence_buckets(confs),
        "docs_by_type": _docs_by_type(),
        "diagram_stats": _diagram_stats(),
    }
