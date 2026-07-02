from backend.agents.registry import register
from backend.services.graph_service import run_query, driver
import requests
import json
import re
import logging

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"

def run(question: str, context: dict):
    if not driver:
        return {"agent": "graph", "status": "failed", "context": "Neo4j is offline.", "confidence": 0.0}
        
    schema_context = """
    You are an expert Neo4j Cypher query generator. 
    The graph database contains workflow diagrams with the following schema:
    - Node Label: `DiagramNode`
      - Properties: `id` (string), `label` (string, e.g. 'API Gateway\\n(Load Balancer)'), `type` (string), `file_name` (string)
    - Relationship: `[:ROUTES_TO]` connects DiagramNodes.
      - Properties: `condition` (string, e.g. 'Success', 'Fallback', 'Valid')
    
    Given a user's question, write ONLY a valid Cypher query that answers the question. 
    Do not include markdown blocks, explanations, or backticks. Just the raw Cypher string.
    
    CRITICAL RULES:
    1. NEVER use exact matching for labels (e.g. {label: 'LLM Orchestrator'}). The labels contain newlines! ALWAYS use `WHERE toLower(n.label) CONTAINS 'llm orchestrator'`.
    2. To find connected nodes, you MUST traverse the edge and return the target nodes. 
       Example: MATCH (n:DiagramNode)-[r:ROUTES_TO]->(m:DiagramNode) WHERE toLower(n.label) CONTAINS 'llm orchestrator' RETURN m.label, r.condition
    3. To find where a node comes from, traverse backwards: 
       Example: MATCH (m:DiagramNode)-[r:ROUTES_TO]->(n:DiagramNode) WHERE toLower(n.label) CONTAINS 'database' RETURN m.label, r.condition
    4. RETURN the matched nodes properties (e.g. `RETURN m.label, m.type, r.condition`). NEVER just `RETURN m` or `RETURN n`.
    """
    
    prompt = f"{schema_context}\n\nUser Question: {question}\nCypher Query:"
    
    try:
        response = requests.post(
            OLLAMA_URL, 
            json={"model": MODEL, "prompt": prompt, "stream": False},
            timeout=30
        )
        if response.status_code == 200:
            cypher_query = response.json().get("response", "").strip()
            # Clean up potential markdown formatting if the LLM disobeyed
            cypher_query = re.sub(r"^```cypher\n|```$", "", cypher_query, flags=re.MULTILINE).strip()
            
            logging.info(f"Generated Cypher: {cypher_query}")
            
            with driver.session() as session:
                result = session.run(cypher_query)
                records = [record.data() for record in result]
                
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
