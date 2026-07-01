from neo4j import GraphDatabase
from backend.services.activity_service import log_activity

import logging

try:
    driver = GraphDatabase.driver(
        "bolt://localhost:7687",
        auth=("neo4j", "password")
    )
except Exception as e:
    logging.warning(f"Neo4j driver initialization failed: {e}")
    driver = None

def run_query(query, parameters=None):
    if not driver:
        return
    try:
        with driver.session() as session:
            session.run(query, parameters or {})
    except Exception as e:
        logging.warning(f"Neo4j query failed (Neo4j may be offline): {e}")

def create_document(profile):
    query = """
    MERGE (d:Document {file_name:$file_name})
    SET d.display_name=$display_name,
        d.summary=$summary,
        d.folder=$folder,
        d.file_type=$file_type
    """
    run_query(query, profile)
    log_activity("Neo4j Updated", profile.get("file_name", "unknown"))

def delete_document(file_name):
    query = """
    MATCH (d:Document {file_name:$file_name})
    OPTIONAL MATCH (d)-[r:HAS_KEYWORD]->(k:Keyword)
    DELETE r, d
    WITH k
    WHERE k IS NOT NULL AND NOT (k)<-[:HAS_KEYWORD]-()
    DELETE k
    """
    run_query(query, {"file_name": file_name})
    log_activity("Neo4j Deleted", file_name)

def create_keyword(keyword):
    query = """
    MERGE (k:Keyword {name:$keyword})
    """
    run_query(query, {"keyword": keyword})

def create_relationship(file_name, keyword):
    query = """
    MATCH (d:Document {file_name:$file_name})
    MATCH (k:Keyword {name:$keyword})
    MERGE (d)-[:HAS_KEYWORD]->(k)
    """
    run_query(query, {
        "file_name": file_name,
        "keyword": keyword
    })

def search_keywords(question):
    if not driver:
        return []
    query = """
    MATCH (d:Document)-[:HAS_KEYWORD]->(k:Keyword)
    WHERE size(k.name) > 2 AND (toLower($question) CONTAINS toLower(k.name) OR toLower(k.name) CONTAINS toLower($question))
    RETURN d.file_name AS file,
           collect(k.name) AS keywords
    """
    try:
        with driver.session() as session:
            result = session.run(
                query,
                {"question": question}
            )
            return result.data()
    except Exception as e:
        logging.warning(f"Neo4j search_keywords failed: {e}")
        return []

def ingest_diagram(file_name, nodes, edges):
    if not driver:
        return
    
    # Create nodes
    node_query = """
    UNWIND $nodes AS n
    MERGE (node:DiagramNode {id: n.id, file_name: $file_name})
    SET node.label = n.label,
        node.type = n.type
    """
    
    # Create edges
    edge_query = """
    UNWIND $edges AS e
    MATCH (src:DiagramNode {id: e.source, file_name: $file_name})
    MATCH (tgt:DiagramNode {id: e.target, file_name: $file_name})
    MERGE (src)-[r:ROUTES_TO]->(tgt)
    SET r.condition = e.label
    """
    
    try:
        with driver.session() as session:
            session.run(node_query, {"nodes": nodes, "file_name": file_name})
            session.run(edge_query, {"edges": edges, "file_name": file_name})
            logging.info(f"Ingested diagram {file_name} into Neo4j: {len(nodes)} nodes, {len(edges)} edges.")
    except Exception as e:
        logging.warning(f"Failed to ingest diagram into Neo4j: {e}")
