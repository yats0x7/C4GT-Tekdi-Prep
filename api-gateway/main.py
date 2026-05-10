# api-gateway/main.py
# Shiksha-MFE AI Backend — FastAPI Gateway
# Author: Yats (yats0x7)

from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uuid
import os
import tempfile
from typing import Optional

from celery_app import celery
from tasks.ingestion import process_pdf_task, process_pptx_task
from tasks.assessment import generate_quiz_task
from db.database import get_task_status, init_db
from services.websocket_manager import WebSocketManager

app = FastAPI(
    title="Shiksha AI Backend",
    description="AI-powered content ingestion, assessment, and micro-learning platform",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "http://localhost:3003"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ws_manager = WebSocketManager()


@app.on_event("startup")
async def startup():
    await init_db()


# ─────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Check if the API gateway and dependencies are alive."""
    import httpx
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{ollama_url}/api/tags")
            ollama_status = "healthy" if response.status_code == 200 else "degraded"
            available_models = [m["name"] for m in response.json().get("models", [])]
    except Exception:
        ollama_status = "unreachable"
        available_models = []

    return {
        "status": "healthy",
        "version": "0.1.0",
        "dependencies": {
            "ollama": ollama_status,
            "available_models": available_models,
        }
    }


# ─────────────────────────────────────────────────────────
# MODULE A — DOCUMENT INGESTION
# ─────────────────────────────────────────────────────────

@app.post("/api/ingest/pdf")
async def ingest_pdf(
    file: UploadFile = File(...),
    tenant_id: str = "default",
    model: str = "llama3:8b"
):
    """
    Ingest a PDF file and trigger async AI processing.

    Returns a task_id immediately. Poll /api/tasks/{task_id}
    or subscribe to WebSocket /ws/{task_id} for updates.
    """
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    task_id = str(uuid.uuid4())

    # Save file to temp location for Celery worker to pick up
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".pdf", prefix=f"ingest_{task_id}_"
    ) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    # Queue the heavy LLM processing — DO NOT block the request
    process_pdf_task.apply_async(
        args=[task_id, tmp_path, tenant_id, model],
        task_id=task_id
    )

    return {
        "task_id": task_id,
        "status": "queued",
        "filename": file.filename,
        "message": "PDF queued for processing. Subscribe to /ws/{task_id} for live updates.",
        "poll_url": f"/api/tasks/{task_id}"
    }


@app.post("/api/ingest/pptx")
async def ingest_pptx(
    file: UploadFile = File(...),
    tenant_id: str = "default",
    model: str = "llama3:8b"
):
    """Ingest a PowerPoint file for AI processing."""
    if not file.filename.endswith((".pptx", ".ppt")):
        raise HTTPException(status_code=400, detail="Only PPTX/PPT files are accepted")

    task_id = str(uuid.uuid4())

    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".pptx", prefix=f"ingest_{task_id}_"
    ) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    process_pptx_task.apply_async(
        args=[task_id, tmp_path, tenant_id, model],
        task_id=task_id
    )

    return {
        "task_id": task_id,
        "status": "queued",
        "filename": file.filename,
        "poll_url": f"/api/tasks/{task_id}"
    }


# ─────────────────────────────────────────────────────────
# MODULE B — ASSESSMENT GENERATION
# ─────────────────────────────────────────────────────────

@app.post("/api/assess/generate")
async def generate_assessment(
    source_task_id: str,
    question_types: list[str] = ["mcq", "fill_in_the_blank", "match_the_pair"],
    num_questions: int = 10,
    model: str = "llama3:8b"
):
    """
    Generate quiz questions from an already-processed document.
    source_task_id must be a completed ingestion task.
    """
    source = await get_task_status(source_task_id)
    if not source or source["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail="Source task not found or not yet completed"
        )

    task_id = str(uuid.uuid4())
    generate_quiz_task.apply_async(
        args=[task_id, source_task_id, question_types, num_questions, model],
        task_id=task_id
    )

    return {
        "task_id": task_id,
        "status": "queued",
        "source_task_id": source_task_id,
        "poll_url": f"/api/tasks/{task_id}"
    }


# ─────────────────────────────────────────────────────────
# TASK STATUS POLLING
# ─────────────────────────────────────────────────────────

@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """Poll task status and results."""
    status = await get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return status


@app.get("/api/tasks/{task_id}/result")
async def get_task_result(task_id: str):
    """Get the full JSON result of a completed task."""
    status = await get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    if status["status"] != "completed":
        raise HTTPException(
            status_code=202,
            detail=f"Task is still {status['status']}"
        )
    return status.get("result", {})


# ─────────────────────────────────────────────────────────
# WEBSOCKET — LIVE UPDATES
# ─────────────────────────────────────────────────────────

@app.websocket("/ws/{task_id}")
async def websocket_task_updates(websocket: WebSocket, task_id: str):
    """
    Subscribe to live processing updates for a task.
    Sends JSON events as stages complete:
    { "stage": "extraction", "progress": 25, "message": "Extracted 47 pages" }
    """
    await ws_manager.connect(websocket, task_id)
    try:
        while True:
            data = await websocket.receive_text()
            # Keep-alive ping/pong
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, task_id)