# api-gateway/tasks/ingestion.py
# Module A — Intelligent Document Ingestion
# PDF/PPT → Structured JSON + Key Takeaways + Glossary + Narration Script

import os
import json
import tempfile
import logging
from typing import Optional
from celery import shared_task
from celery_app import celery

from services.pdf_extractor import PDFExtractor
from services.pptx_extractor import PPTXExtractor
from services.ollama_client import OllamaClient
from db.database import update_task_status, save_task_result

logger = logging.getLogger(__name__)


@celery.task(bind=True, name="tasks.ingestion.process_pdf")
def process_pdf_task(self, task_id: str, file_path: str, tenant_id: str, model: str):
    """
    Full Module A pipeline for PDF ingestion.

    Stages:
    1. extract    — PyMuPDF text + image extraction
    2. structure  — LLM JSON structuring (headers, body, metadata)
    3. summarize  — Key Takeaways generation
    4. glossary   — Domain-specific term extraction
    5. narrate    — Narration script generation
    6. complete   — Result saved to DB
    """
    ollama = OllamaClient(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        model=model
    )

    try:
        # ── Stage 1: Extract ───────────────────────────────────
        _update(task_id, "processing", "extraction", 10,
                "Extracting text and structure from PDF...")

        extractor = PDFExtractor(file_path)
        raw_data = extractor.extract()

        if not raw_data.get("pages"):
            raise ValueError("PDF extraction returned empty content")

        _update(task_id, "processing", "extraction", 25,
                f"Extracted {raw_data['page_count']} pages, "
                f"{raw_data['word_count']} words")

        # ── Stage 2: Structure ─────────────────────────────────
        _update(task_id, "processing", "structuring", 35,
                "Structuring content with local LLM...")

        structure_prompt = _build_structure_prompt(raw_data["full_text"])
        structured = ollama.generate_json(structure_prompt)

        _update(task_id, "processing", "structuring", 50,
                f"Identified {len(structured.get('sections', []))} sections")

        # ── Stage 3: Key Takeaways ─────────────────────────────
        _update(task_id, "processing", "summarization", 60,
                "Generating key takeaways...")

        takeaways_prompt = _build_takeaways_prompt(raw_data["full_text"])
        takeaways = ollama.generate_json(takeaways_prompt)

        # ── Stage 4: Glossary ──────────────────────────────────
        _update(task_id, "processing", "glossary", 72,
                "Extracting domain glossary...")

        glossary_prompt = _build_glossary_prompt(raw_data["full_text"])
        glossary = ollama.generate_json(glossary_prompt)

        # ── Stage 5: Narration Script ──────────────────────────
        _update(task_id, "processing", "narration", 85,
                "Generating narration script...")

        narration_prompt = _build_narration_prompt(structured)
        narration = ollama.generate_text(narration_prompt)

        # ── Stage 6: Assemble + Save ───────────────────────────
        result = {
            "task_id": task_id,
            "tenant_id": tenant_id,
            "source_type": "pdf",
            "metadata": raw_data["metadata"],
            "page_count": raw_data["page_count"],
            "word_count": raw_data["word_count"],
            "structure": structured,
            "key_takeaways": takeaways.get("takeaways", []),
            "glossary": glossary.get("terms", []),
            "narration_script": narration,
            "model_used": model,
        }

        save_task_result(task_id, result)
        _update(task_id, "completed", "complete", 100,
                "Document processed successfully")

        logger.info(f"Task {task_id} completed successfully")
        return result

    except Exception as e:
        logger.error(f"Task {task_id} failed: {str(e)}", exc_info=True)
        update_task_status(task_id, "failed", error=str(e))
        raise self.retry(exc=e, countdown=30, max_retries=2)

    finally:
        # Always clean up temp file
        if os.path.exists(file_path):
            os.remove(file_path)


@celery.task(bind=True, name="tasks.ingestion.process_pptx")
def process_pptx_task(self, task_id: str, file_path: str, tenant_id: str, model: str):
    """Module A pipeline for PPTX ingestion (same stages, different extractor)."""
    ollama = OllamaClient(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        model=model
    )

    try:
        _update(task_id, "processing", "extraction", 10,
                "Extracting slides from PPTX...")

        extractor = PPTXExtractor(file_path)
        raw_data = extractor.extract()

        _update(task_id, "processing", "extraction", 30,
                f"Extracted {raw_data['slide_count']} slides")

        # Same LLM pipeline as PDF after extraction
        structure_prompt = _build_structure_prompt(raw_data["full_text"])
        structured = ollama.generate_json(structure_prompt)
        _update(task_id, "processing", "structuring", 50, "Content structured")

        takeaways = ollama.generate_json(_build_takeaways_prompt(raw_data["full_text"]))
        _update(task_id, "processing", "summarization", 65, "Takeaways generated")

        glossary = ollama.generate_json(_build_glossary_prompt(raw_data["full_text"]))
        _update(task_id, "processing", "glossary", 80, "Glossary extracted")

        # For PPT: use speaker notes as narration base
        narration = _build_pptx_narration(raw_data, ollama)
        _update(task_id, "processing", "narration", 90, "Narration script ready")

        result = {
            "task_id": task_id,
            "tenant_id": tenant_id,
            "source_type": "pptx",
            "metadata": raw_data["metadata"],
            "slide_count": raw_data["slide_count"],
            "slides": raw_data["slides"],
            "structure": structured,
            "key_takeaways": takeaways.get("takeaways", []),
            "glossary": glossary.get("terms", []),
            "narration_script": narration,
            "model_used": model,
        }

        save_task_result(task_id, result)
        _update(task_id, "completed", "complete", 100, "PPTX processed successfully")
        return result

    except Exception as e:
        logger.error(f"Task {task_id} failed: {str(e)}", exc_info=True)
        update_task_status(task_id, "failed", error=str(e))
        raise self.retry(exc=e, countdown=30, max_retries=2)

    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


# ─────────────────────────────────────────────────────────
# PROMPT BUILDERS
# ─────────────────────────────────────────────────────────

def _build_structure_prompt(text: str) -> str:
    # Truncate to avoid context window overflow (≈ 6000 tokens safe limit)
    truncated = text[:8000] if len(text) > 8000 else text
    return f"""You are an educational content structuring AI.
Analyze the following document text and return a structured JSON object.

RULES:
- Return ONLY valid JSON, no explanation, no markdown fences
- Do not hallucinate content not present in the source text
- Preserve the original meaning faithfully

REQUIRED JSON SCHEMA:
{{
  "title": "string — inferred document title",
  "subject": "string — primary subject or course domain",
  "level": "beginner|intermediate|advanced",
  "sections": [
    {{
      "heading": "string",
      "content": "string — condensed section content",
      "learning_objectives": ["string"]
    }}
  ],
  "total_learning_time_minutes": number
}}

DOCUMENT TEXT:
{truncated}"""


def _build_takeaways_prompt(text: str) -> str:
    truncated = text[:8000] if len(text) > 8000 else text
    return f"""You are an educational content AI.
Extract the 5-8 most important key takeaways from the document below.

RULES:
- Return ONLY valid JSON, no markdown
- Each takeaway must be directly supported by the source text
- Write takeaways as concise, learner-friendly statements

REQUIRED JSON SCHEMA:
{{
  "takeaways": [
    {{
      "point": "string — the key learning point",
      "explanation": "string — 1-2 sentence elaboration",
      "importance": "high|medium"
    }}
  ]
}}

DOCUMENT TEXT:
{truncated}"""


def _build_glossary_prompt(text: str) -> str:
    truncated = text[:8000] if len(text) > 8000 else text
    return f"""You are an educational content AI.
Extract all domain-specific technical terms and jargon from the document.

RULES:
- Return ONLY valid JSON, no markdown
- Only include terms that appear in the source text
- Definitions must be based on how the term is used in context

REQUIRED JSON SCHEMA:
{{
  "terms": [
    {{
      "term": "string",
      "definition": "string — clear, learner-friendly definition",
      "context": "string — sentence from source where this term appears"
    }}
  ]
}}

DOCUMENT TEXT:
{truncated}"""


def _build_narration_prompt(structured: dict) -> str:
    content_summary = json.dumps(structured, indent=2)[:4000]
    return f"""You are an educational narrator.
Write a natural, engaging narration script for an e-learning course based on the structured content below.

RULES:
- Write in a warm, conversational teaching voice
- Include section transitions ("Now let's look at...", "Building on that...")
- Approximately 150-200 words per section
- Do not add information not in the source

STRUCTURED CONTENT:
{content_summary}

Return only the narration script as plain text."""


def _build_pptx_narration(raw_data: dict, ollama: "OllamaClient") -> str:
    """Use speaker notes as base for narration, enhance with LLM if needed."""
    notes = []
    for slide in raw_data.get("slides", []):
        if slide.get("speaker_notes"):
            notes.append(f"Slide {slide['index']}: {slide['speaker_notes']}")

    if notes:
        notes_text = "\n".join(notes)
        prompt = f"""Polish these presentation speaker notes into a smooth narration script.
Keep the original intent but improve flow and clarity.

SPEAKER NOTES:
{notes_text}

Return only the polished narration script as plain text."""
        return ollama.generate_text(prompt)
    else:
        # No speaker notes — generate from slide content
        return ollama.generate_text(
            _build_narration_prompt(raw_data.get("structure", {}))
        )


def _update(task_id: str, status: str, stage: str, progress: int, message: str):
    """Update task status in DB and broadcast to WebSocket subscribers."""
    update_task_status(task_id, status, stage=stage, progress=progress, message=message)
    # WebSocket broadcast is handled by DB trigger via Redis pub/sub
    from services.websocket_manager import broadcast_update
    broadcast_update(task_id, {
        "stage": stage,
        "progress": progress,
        "message": message,
        "status": status
    })