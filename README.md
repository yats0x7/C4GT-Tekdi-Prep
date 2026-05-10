# Shiksha-MFE AI Backend — C4GT 2026 Prototype
### by Yats (yats0x7) — biru-codeastromer

Pre-program prototype for the C4GT 2026 Tekdi / Shiksha-MFE contribution.
Demonstrates working Module A (PDF ingestion) + Module B (Assessment) infrastructure.

---

## What This Is

This repository is a **proof-of-work prototype** built before the C4GT program starts.
It demonstrates:

1. **Technical audit** of the existing `shiksha-mfe` frontend
2. **Working Module A skeleton** (FastAPI + Celery + Ollama + PyMuPDF)
3. **H5P packager** with schema validation (Module B)
4. **Architecture Decision Records** explaining engineering choices
5. **15+ tests** covering extraction, LLM parsing, H5P validation, and API endpoints

---

## Quick Start

### Prerequisites
- Docker + Docker Compose
- 8GB+ RAM (for Ollama models)

### Run Everything
```bash
git clone https://github.com/biru-codeastromer/shiksha-ai-backend
cd shiksha-ai-backend
docker compose up -d
```

This starts:
- **FastAPI gateway** → http://localhost:8000
- **API docs** → http://localhost:8000/docs
- **Celery Flower** (queue monitor) → http://localhost:5555
- **MinIO** (storage) → http://localhost:9001
- **Ollama** (LLMs) → http://localhost:11434

Ollama will automatically pull `llama3:8b`, `mistral:7b`, and `nomic-embed-text` on first start (~5GB download).

### Test PDF Ingestion
```bash
# Upload a PDF
curl -X POST http://localhost:8000/api/ingest/pdf \
  -F "file=@your-document.pdf" \
  -F "tenant_id=demo"

# Response: {"task_id": "abc-123", "status": "queued"}

# Poll for result
curl http://localhost:8000/api/tasks/abc-123

# Or subscribe to live updates
wscat -c ws://localhost:8000/ws/abc-123
```

### Run Tests
```bash
cd api-gateway
pip install -r requirements.txt
pytest tests/ -v
```

---

## Repository Structure

```
shiksha-ai-backend/
├── AUDIT_REPORT.md              ← Technical audit of shiksha-mfe frontend
├── PROPOSAL.md                  ← C4GT 2026 proposal document
├── docker-compose.yml           ← Full stack: FastAPI + Celery + Redis + Ollama + Postgres
│
├── api-gateway/
│   ├── main.py                  ← FastAPI app (Module A + B endpoints)
│   ├── celery_app.py            ← Celery configuration
│   ├── requirements.txt
│   ├── Dockerfile
│   │
│   ├── tasks/
│   │   ├── ingestion.py         ← Module A: PDF/PPTX pipeline (Celery tasks)
│   │   └── assessment.py        ← Module B: Quiz generation (Celery tasks)
│   │
│   ├── services/
│   │   ├── pdf_extractor.py     ← PyMuPDF extraction (no LLM)
│   │   ├── ollama_client.py     ← Ollama JSON/text generation with retries
│   │   └── h5p_packager.py      ← H5P + SCORM 1.2 packaging with validation
│   │
│   ├── db/
│   │   └── database.py          ← Async PostgreSQL task state management
│   │
│   ├── migrations/
│   │   └── init.sql             ← Database schema
│   │
│   └── tests/
│       └── test_pdf_ingestion.py ← 15+ pytest test cases
│
└── docs/
    ├── ADR-001-Async-LLM-Processing.md   ← Why Celery, not sync endpoints
    └── ADR-002-H5P-Packaging-Strategy.md ← Why pure Python, not h5p-nodejs-library
```

---

## API Reference

### Module A — Document Ingestion

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ingest/pdf` | Upload PDF for AI processing |
| POST | `/api/ingest/pptx` | Upload PPTX for AI processing |
| GET | `/api/tasks/{task_id}` | Poll task status |
| GET | `/api/tasks/{task_id}/result` | Get completed result |
| WS | `/ws/{task_id}` | Live progress updates |

### Module B — Assessment

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/assess/generate` | Generate quiz from ingested document |

### Utility

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check + Ollama model status |
| GET | `/docs` | Swagger UI |

---

## Module A Output Format

```json
{
  "task_id": "abc-123",
  "source_type": "pdf",
  "page_count": 47,
  "word_count": 12043,
  "metadata": {
    "title": "Introduction to Machine Learning",
    "author": "Dr. Smith",
    "creation_date": "2024-01-15"
  },
  "structure": {
    "title": "Introduction to Machine Learning",
    "subject": "Computer Science / AI",
    "level": "beginner",
    "sections": [
      {
        "heading": "Supervised Learning",
        "content": "...",
        "learning_objectives": ["Define supervised learning", "Identify labeled datasets"]
      }
    ],
    "total_learning_time_minutes": 45
  },
  "key_takeaways": [
    {
      "point": "Machine learning enables systems to learn from data",
      "explanation": "Unlike traditional programming...",
      "importance": "high"
    }
  ],
  "glossary": [
    {
      "term": "Gradient Descent",
      "definition": "An optimization algorithm...",
      "context": "Gradient descent is used to minimize the loss function..."
    }
  ],
  "narration_script": "Welcome to Introduction to Machine Learning...",
  "model_used": "llama3:8b"
}
```

---

## Design Principles

1. **Async-First:** All LLM inference via Celery. No request ever waits for Ollama.
2. **Local-First:** Ollama runs locally. Cloud fallback is opt-in via `OPENAI_API_KEY`.
3. **Validate-Before-Package:** H5P manifests are schema-validated before packaging.
4. **Anti-Hallucination:** Every LLM prompt requires `source_reference` field — a verifiable quote from source text.
5. **Hardware-Aware:** Concurrency limits prevent OOM on low-RAM servers.

---

## Related

- [Technical Audit Report](./AUDIT_REPORT.md)
- [C4GT Proposal](./PROPOSAL.md)
- [ADR-001: Async LLM Processing](./docs/ADR-001-Async-LLM-Processing.md)
- [ADR-002: H5P Packaging Strategy](./docs/ADR-002-H5P-Packaging-Strategy.md)
- [Original Repository: tekdi/shiksha-mfe](https://github.com/tekdi/shiksha-mfe)
- [Issue #7](https://github.com/tekdi/shiksha-mfe/issues/7)