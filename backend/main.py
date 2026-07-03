from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi import Request
from fastapi.responses import StreamingResponse
from backend.routes import chat, upload, documents
from backend.services.event_manager import start_watching, get_new_events
import asyncio
import time
import os
import json
from backend.services.activity_service import get_activities
from backend.utils.dependencies import QDRANT_CLIENT, COLLECTION_NAME
from backend.routes import chat, upload, documents, metrics_api

# ----------------------------------
# FASTAPI APP
# ----------------------------------

app = FastAPI(
    title="Enterprise Knowledge Assistant"
)

# ----------------------------------
# SYSTEM APIs
# ----------------------------------

@app.get("/health")
def health():
    return {
        "qdrant": "connected",
        "neo4j": "connected",
        "ollama": "running",
        "planner": "active",
        "supervisor": "active"
    }

@app.get("/activity")
def activity():
    return get_activities(limit=10)

@app.get("/metrics")
def metrics():
    total_docs = 0
    if os.path.exists("backend/storage/document_profiles.json"):
        with open("backend/storage/document_profiles.json", "r") as f:
            try:
                docs = json.load(f)
                total_docs = len(docs)
            except:
                pass
    
    try:
        q_count = QDRANT_CLIENT.count(COLLECTION_NAME).count
    except:
        q_count = 0
        
    return {
        "total_docs": total_docs,
        "total_vectors": q_count,
        "agents": 4
    }

# ----------------------------------
# ROUTERS & EVENTS
# ----------------------------------

@app.on_event("startup")
def startup_event():
    start_watching()

@app.get("/api/events")
async def sse_events(request: Request):
    async def event_generator():
        last_timestamp = time.time()
        while True:
            if await request.is_disconnected():
                break
            new_events = get_new_events(last_timestamp)
            for e in new_events:
                yield f"data: {e['message']}\n\n"
                last_timestamp = max(last_timestamp, e["timestamp"])
            await asyncio.sleep(1)
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

app.include_router(chat.router)
app.include_router(upload.router)
app.include_router(documents.router)
app.include_router(metrics_api.router)

# Mount frontend directory
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
