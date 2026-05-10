# ADR-001: Asynchronous LLM Processing Architecture

**Status:** Accepted  
**Date:** May 2026  
**Author:** Yats (yats0x7)

---

## Context

The Shiksha AI platform must run Llama 3 8B and Whisper large-v3 locally inside Docker containers. On typical server hardware (8–16 GB RAM, no GPU), inference times are:

| Operation | Expected Time |
|-----------|--------------|
| Llama 3 8B — 1000-word document summary | 45–90 seconds |
| Llama 3 8B — 10 MCQ questions | 60–120 seconds |
| Whisper large-v3 — 10-minute video | 3–8 minutes |
| Full Module A pipeline (50-page PDF) | 2–4 minutes |

FastAPI's default request handling is coroutine-based and non-blocking for I/O, but these LLM operations are **CPU-bound**. A naive implementation will block the event loop or hit HTTP timeout (default 60 seconds) on the first real inference request.

---

## Decision

**All LLM inference runs asynchronously via Celery + Redis.**

The API gateway endpoint (`POST /api/ingest/pdf`) does three things only:
1. Validates the uploaded file
2. Saves it to temp storage
3. Queues a Celery task and **immediately returns** `{ "task_id": "...", "status": "queued" }`

The frontend subscribes to either:
- **WebSocket** (`/ws/{task_id}`) for live stage-by-stage progress updates
- **Polling** (`GET /api/tasks/{task_id}`) as a fallback

---

## Consequences

**Positive:**
- No HTTP timeouts, regardless of hardware speed
- Workers can be scaled horizontally (add more Celery workers for concurrent users)
- Hardware-aware concurrency: `worker_concurrency=2` prevents OOM on low-RAM servers
- Retry policy handles transient Ollama failures gracefully
- Progress broadcasting gives users live feedback (UX improvement over spinner)

**Negative:**
- Added infrastructure complexity (Redis, Celery, Flower monitoring)
- Frontend must handle async state (polling or WebSocket)
- Debugging distributed task failures is harder than synchronous failures

**Mitigations:**
- Flower UI (port 5555) provides full task queue visibility
- Task state stored in PostgreSQL (survives Redis restart)
- Celery `task_acks_late=True` prevents task loss on worker crash

---

## Alternatives Considered

### 1. Synchronous FastAPI Endpoint
```python
# ❌ REJECTED
@app.post("/api/ingest/pdf")
async def ingest_pdf(file: UploadFile):
    text = extract_text(file)        # 2-5s — OK
    summary = ollama.generate(text)  # 45-120s — TIMEOUT
    return summary
```
**Rejected:** Will timeout at 60 seconds. Blocks the server during inference. Single concurrent request saturates the server.

### 2. FastAPI Background Tasks
```python
# ❌ REJECTED
@app.post("/api/ingest/pdf")
async def ingest_pdf(file: UploadFile, background_tasks: BackgroundTasks):
    background_tasks.add_task(process_pdf, file)
    return {"status": "processing"}
```
**Rejected:** FastAPI background tasks have no persistence, no retry, no monitoring, and no cross-process scaling. If the server restarts, all in-progress tasks are silently lost.

### 3. Server-Sent Events (SSE) instead of WebSocket
```python
# ✅ VIABLE ALTERNATIVE
@app.get("/api/stream/{task_id}")
async def stream_updates(task_id: str):
    async def event_generator():
        while True:
            status = await get_task_status(task_id)
            yield f"data: {json.dumps(status)}\n\n"
            await asyncio.sleep(2)
    return EventSourceResponse(event_generator())
```
**Decision:** SSE is simpler than WebSocket (no handshake, works through HTTP/2 proxies) and sufficient for one-way progress updates. **Will implement SSE as primary, WebSocket as secondary** for bidirectional use cases (HITL review approval).

### 4. External Cloud LLM (GPT-4o)
**Rejected:** Defeats the core platform requirement of self-hosted, local-first AI inference. Adds external dependency, data privacy concerns, and ongoing API costs. **Retained as optional fallback only** via `OPENAI_API_KEY` environment variable.

---

## Implementation Notes

**Concurrency limits (critical for hardware safety):**
```python
# celery_app.py
celery.conf.update(
    worker_prefetch_multiplier=1,   # Process one task at a time
    worker_max_tasks_per_child=10,  # Restart worker every 10 tasks (memory leak prevention)
    task_soft_time_limit=300,       # 5-min soft limit
    task_time_limit=600,            # 10-min hard kill
)
```

**WebSocket progress broadcast:**
```python
# tasks/ingestion.py
def _update(task_id, status, stage, progress, message):
    update_task_status(task_id, status, stage, progress, message)
    broadcast_update(task_id, {"stage": stage, "progress": progress})
```

**Frontend consumption:**
```typescript
// Next.js — subscribe to task progress
const ws = new WebSocket(`ws://localhost:8000/ws/${taskId}`);
ws.onmessage = (event) => {
  const update = JSON.parse(event.data);
  setProgress(update.progress);
  setStage(update.stage);
};
```