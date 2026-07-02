from backend.agents.registry import register
from backend.services.graph_service import run_query, driver
import requests
import json
import re
import logging

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
                
            if records:
                return {
                    "agent": "graph",
                    "status": "success",
                    "context": f"Cypher Execution Result:\n{json.dumps(records, indent=2)}",
                    "sources": [{"file_name": "Neo4j Database", "chunk_number": "GraphDB"}],
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
