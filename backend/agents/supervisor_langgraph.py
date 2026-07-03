"""
supervisor_langgraph.py — LangGraph based routing pipeline.

This implements a true Agentic Workflow using LangGraph.
A router node decides which sub-agent to invoke, and a generator node formats the final response.
"""

from typing import TypedDict, List, Dict, Any, Optional
from langgraph.graph import StateGraph, END
from backend.agents.registry import get_agent
from backend.agents import document_agent, graph_agent, analytics_agent, memory_agent, email_agent
from backend.services.activity_service import log_activity
import requests
import json
import time

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"

class AgentState(TypedDict):
    question: str
    context: dict  # Original context passed from FastAPI (e.g. session_id)
    agent_name: str
    agent_status: str
    retrieved_context: str
    sources: List[Dict[str, Any]]
    confidence: float
    selected_document: Optional[str]
    summary: str
    keywords: List[str]
    answer: str

def router_node(state: AgentState):
    question = state["question"].lower()
    
    # Simple heuristic router for speed and reliability, falling back to LLM if needed
    if any(w in question for w in ["email", "inbox", "message", "send", "mail"]):
        agent = "email"
    elif any(w in question for w in ["graph", "node", "connection", "relationship", "dependency", "neo4j", "diagram", "drawio", "route", "flow", "workflow", "fallback", "path", "branch", "where does"]):
        agent = "graph"
    elif any(w in question.split() for w in ["average", "count", "sum", "total", "plot", "chart", "trend", "analytics", "data", "dataset", "excel", "csv", "powerbi", "pbix", "dashboard", "table", "sku", "price", "inventory", "stock", "orders"]):
        agent = "analytics"
    else:
        # Default to document search which handles generic queries, pdfs, word, images
        agent = "document"
        
    print(f"\n[LangGraph Router] Routing question to: {agent}_agent")
    return {"agent_name": agent}

def call_agent_node(state: AgentState):
    agent_name = state.get("agent_name", "document")
    agent_func = get_agent(agent_name)
    
    print(f"[LangGraph] Invoking {agent_name}_agent...")
    
    if not agent_func:
        return {
            "agent_status": "failed",
            "retrieved_context": "Agent not found.",
            "sources": [],
            "confidence": 0.0
        }
        
    result = agent_func(state["question"], state["context"])
    
    return {
        "agent_status": result.get("status", "failed"),
        "retrieved_context": result.get("context", ""),
        "sources": result.get("sources", []),
        "confidence": result.get("confidence", 0.0),
        "selected_document": result.get("selected_document", None),
        "summary": result.get("summary", ""),
        "keywords": result.get("keywords", [])
    }

def generator_node(state: AgentState):
    total_context = state.get("retrieved_context", "")
    question = state["question"]
    
    if not total_context or state.get("agent_status") == "failed":
        total_context = "No relevant context was found by the agent."
        
    log_activity(f"LangGraph -> {state.get('agent_name')} Agent", f"Status: {state.get('agent_status')} (Conf: {state.get('confidence', 0.0):.2f})")
    
    prompt = f"""You are an Enterprise AI Assistant. Answer using ONLY the context below.

Context from {state.get('agent_name')} agent:

{total_context}

Question: {question}

Instructions:
- Answer using the provided context.
- Be concise and professional.
- It is okay to match partial names (e.g. "Kafka" matches "Kafka Queue").
- If the answer is truly not in the context, say: "I could not find this information."
"""

    try:
        print("[LangGraph Generator] Generating final response...")
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_ctx": 4096}
            }
        )
        answer = response.json().get("response", "")
    except Exception as e:
        answer = f"Error contacting Ollama: {str(e)}"
        
    return {"answer": answer}

# Build the Graph
workflow = StateGraph(AgentState)

workflow.add_node("router", router_node)
workflow.add_node("agent_executor", call_agent_node)
workflow.add_node("generator", generator_node)

workflow.set_entry_point("router")
workflow.add_edge("router", "agent_executor")
workflow.add_edge("agent_executor", "generator")
workflow.add_edge("generator", END)

app_graph = workflow.compile()

def run(question: str, context: dict):
    start_time = time.time()
    
    initial_state = {
        "question": question,
        "context": context,
        "agent_name": "",
        "agent_status": "",
        "retrieved_context": "",
        "sources": [],
        "confidence": 0.0,
        "selected_document": None,
        "summary": "",
        "keywords": [],
        "answer": ""
    }
    
    final_state = app_graph.invoke(initial_state)
    
    print(f"[LangGraph] Execution completed in {time.time() - start_time:.2f}s")
    
    return {
        "question": final_state["question"],
        "answer": final_state["answer"],
        "selected_document": final_state["selected_document"],
        "confidence": final_state["confidence"],
        "summary": final_state["summary"],
        "keywords": final_state["keywords"],
        "sources": final_state["sources"]
    }
