from backend.agents.registry import register
from backend.services.graph_service import run_query, driver
import requests
import json
import re
import logging
import os
import glob
from collections import deque

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"

def serialize_neo4j(obj):
    if hasattr(obj, 'nodes') and hasattr(obj, 'relationships'):
        return {
            "nodes": [serialize_neo4j(n) for n in obj.nodes],
            "edges": [serialize_neo4j(r) for r in obj.relationships]
        }
    elif hasattr(obj, 'labels'):
        return {"labels": list(obj.labels), "properties": dict(obj.items())}
    elif hasattr(obj, 'type'):
        return {"type": obj.type, "properties": dict(obj.items())}
    elif isinstance(obj, list):
        return [serialize_neo4j(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: serialize_neo4j(v) for k, v in obj.items()}
    else:
        return obj

def run_json_bfs(question: str) -> str:
    # 1. Very fast LLM check to see if we're asking for a path
    prompt = f"""Extract the source and target nodes from this question.
    Question: {question}
    Return ONLY a JSON dictionary: {{"source": "node A", "target": "node B"}}
    If it's not asking for a path between two nodes, return {{"source": null, "target": null}}
    """
    try:
        response = requests.post(OLLAMA_URL, json={"model": MODEL, "prompt": prompt, "stream": False, "format": "json"}, timeout=30)
        if response.status_code == 200:
            data = response.json().get("response", "")
            parsed = json.loads(data)
            src_query = parsed.get("source")
            tgt_query = parsed.get("target")
            
            if not src_query or not tgt_query:
                return ""
                
            src_query = src_query.lower()
            tgt_query = tgt_query.lower()
            
            # Find all .graph.json files
            data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data")
            json_files = glob.glob(os.path.join(data_dir, "**", "*.graph.json"), recursive=True)
            
            all_paths = []
            
            for jf in json_files:
                try:
                    with open(jf, "r", encoding="utf-8") as f:
                        g = json.load(f)
                except Exception as e:
                    continue
                
                # Find start and end nodes
                start_id = None
                end_id = None
                for node in g.get("nodes", []):
                    nt = str(node.get("text", "")).lower()
                    if src_query in nt and not start_id:
                        start_id = node["id"]
                    if tgt_query in nt and not end_id:
                        end_id = node["id"]
                        
                if start_id and end_id:
                    # Run BFS
                    adj = {}
                    for e in g.get("edges", []):
                        if e["from"] not in adj:
                            adj[e["from"]] = []
                        # store (target, branch_label)
                        adj[e["from"]].append((e["to"], e.get("branch")))
                        
                    q = deque([(start_id, [start_id])])
                    visited = {start_id}
                    found_path = None
                    
                    while q:
                        curr, path = q.popleft()
                        if curr == end_id:
                            found_path = path
                            break
                            
                        for neighbor, branch in adj.get(curr, []):
                            if neighbor not in visited:
                                visited.add(neighbor)
                                # append branch label if exists
                                step = f"-[{branch}]-> {neighbor}" if branch else f"-> {neighbor}"
                                q.append((neighbor, path + [step]))
                                
                    if found_path:
                        # Format path for LLM
                        formatted = f"Found path in {g.get('source_file')}: "
                        for i, step in enumerate(found_path):
                            if i == 0:
                                formatted += step
                            else:
                                formatted += f" {step}"
                        all_paths.append(formatted)
            
            if all_paths:
                return "\n".join(all_paths)
                
    except Exception as e:
        logging.error(f"BFS JSON failed: {e}")
    return ""

def run(question: str, context: dict):
    if not driver:
        return {"agent": "graph", "status": "failed", "context": "Neo4j is offline.", "confidence": 0.0}
        
    schema_context = """
    You are an expert Neo4j Cypher query generator. 
    Node Label: `DiagramNode` (Properties: `id`, `label`, `type`, `file_name`)
    Relationship: `[:ROUTES_TO]` (Properties: `condition`)
    
    CRITICAL: You MUST use `WHERE toLower(n.label) CONTAINS '...'` instead of exact `{label: '...'}` because labels contain newlines!
    
    Examples:
    Q: What nodes are connected to the LLM Orchestrator?
    Cypher: MATCH (n:DiagramNode)-[r:ROUTES_TO]->(m:DiagramNode) WHERE toLower(n.label) CONTAINS 'llm orchestrator' RETURN n, r, m
    
    Q: What is the exact path from 'API Gateway' to the 'Database'?
    Cypher: MATCH p=(n:DiagramNode)-[r:ROUTES_TO*1..15]->(m:DiagramNode) WHERE toLower(n.label) CONTAINS 'api gateway' AND toLower(m.label) CONTAINS 'database' RETURN p LIMIT 5
    
    Q: Explain the entire workflow starting from step 1 to the end.
    Cypher: MATCH (n:DiagramNode)-[r:ROUTES_TO]->(m:DiagramNode) RETURN n, r, m
    """
    
    prompt = f"{schema_context}\n\nQ: {question}\nCypher:"
    
    try:
        response = requests.post(
            OLLAMA_URL, 
            json={"model": MODEL, "prompt": prompt, "stream": False},
            timeout=30
        )
        if response.status_code == 200:
            cypher_query = response.json().get("response", "").strip()
            cypher_query = re.sub(r"^```cypher\n|```$", "", cypher_query, flags=re.MULTILINE).strip()
            
            logging.info(f"Generated Cypher: {cypher_query}")
            
            with driver.session() as session:
                result = session.run(cypher_query)
                records = [serialize_neo4j(record.data()) for record in result]
            
            bfs_context = run_json_bfs(question)
            
            final_context_str = ""
            if records:
                final_context_str += f"Cypher Execution Result (Neo4j / DrawIO):\n{json.dumps(records, indent=2)}\n\n"
            if bfs_context:
                final_context_str += f"BFS Computed Paths (PDF JSON Graphs):\n{bfs_context}\n\n"
                
            if final_context_str:
                return {
                    "agent": "graph",
                    "status": "success",
                    "context": final_context_str.strip(),
                    "sources": [{"file_name": "Architecture DB", "chunk_number": "Graph"}],
                    "confidence": 0.95
                }
            else:
                return {
                    "agent": "graph",
                    "status": "failed",
                    "context": "The Cypher query returned no results.",
                    "confidence": 0.2
                }
        else:
            return {"agent": "graph", "status": "failed", "context": "LLM failed to generate Cypher.", "confidence": 0.0}
            
    except Exception as e:
        return {"agent": "graph", "status": "failed", "context": f"Error executing Graph logic: {str(e)}", "confidence": 0.0}

register("graph", run)
