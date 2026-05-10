# api-gateway/tasks/assessment.py
# Module B — Automated Assessment Suite
# MCQ, Fill-in-the-Blank, Match-the-Pair generation + H5P/SCORM packaging

import os
import json
import logging
import tempfile
from celery_app import celery
from services.ollama_client import OllamaClient
from services.h5p_packager import H5PPackager, SCORMWrapper
from db.database import update_task_status, save_task_result, get_task_result

logger = logging.getLogger(__name__)


@celery.task(bind=True, name="tasks.assessment.generate_quiz")
def generate_quiz_task(
    self,
    task_id: str,
    source_task_id: str,
    question_types: list,
    num_questions: int,
    model: str
):
    """
    Module B: Generate quiz questions from ingested document content.

    Input: Completed Module A task result (structured JSON)
    Output: H5P Question Set + SCORM 1.2 package

    Design principle: ALL questions must be grounded in the source text.
    The prompt explicitly forbids hallucination — answers must be
    verifiable against the ingested content.
    """
    ollama = OllamaClient(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        model=model
    )

    try:
        # ── Load source document content ───────────────────────
        _update(task_id, "processing", "loading", 10,
                "Loading source document content...")

        source_result = get_task_result(source_task_id)
        if not source_result:
            raise ValueError(f"Source task {source_task_id} result not found")

        source_text = _extract_source_text(source_result)
        doc_title = source_result.get("structure", {}).get("title", "Untitled")

        # ── Generate questions per type ────────────────────────
        all_questions = []
        type_count = len(question_types)
        questions_per_type = max(1, num_questions // type_count)

        for i, q_type in enumerate(question_types):
            progress = 15 + (i * 25)
            _update(task_id, "processing", "generation", progress,
                    f"Generating {q_type} questions...")

            if q_type == "mcq":
                questions = _generate_mcq(ollama, source_text, questions_per_type)
            elif q_type == "fill_in_the_blank":
                questions = _generate_fitb(ollama, source_text, questions_per_type)
            elif q_type == "match_the_pair":
                questions = _generate_match(ollama, source_text, questions_per_type)
            elif q_type == "true_false":
                questions = _generate_true_false(ollama, source_text, questions_per_type)
            else:
                logger.warning(f"Unknown question type: {q_type}")
                continue

            all_questions.extend(questions)

        _update(task_id, "processing", "validation", 75,
                f"Generated {len(all_questions)} questions, validating...")

        # ── Package as H5P ────────────────────────────────────
        _update(task_id, "processing", "packaging", 85,
                "Packaging as H5P Question Set...")

        quiz_data = {
            "title": doc_title,
            "description": f"Auto-generated quiz from: {doc_title}",
            "questions": all_questions
        }

        packager = H5PPackager()
        with tempfile.TemporaryDirectory() as tmpdir:
            h5p_path = os.path.join(tmpdir, f"{task_id}_quiz.h5p")
            packager.package_quiz(quiz_data, h5p_path, doc_title)

            # Also generate SCORM wrapper
            scorm_path = os.path.join(tmpdir, f"{task_id}_quiz_scorm.zip")
            SCORMWrapper().wrap_h5p_in_scorm(h5p_path, scorm_path, doc_title)

            # Upload to MinIO (or save locally for now)
            h5p_url = _save_package(h5p_path, task_id, "h5p")
            scorm_url = _save_package(scorm_path, task_id, "scorm")

        result = {
            "task_id": task_id,
            "source_task_id": source_task_id,
            "document_title": doc_title,
            "total_questions": len(all_questions),
            "question_types": question_types,
            "questions": all_questions,
            "packages": {
                "h5p": h5p_url,
                "scorm": scorm_url,
            },
            "model_used": model,
        }

        save_task_result(task_id, result)
        _update(task_id, "completed", "complete", 100,
                f"Quiz ready: {len(all_questions)} questions packaged as H5P + SCORM")

        return result

    except Exception as e:
        logger.error(f"Assessment task {task_id} failed: {e}", exc_info=True)
        update_task_status(task_id, "failed", error=str(e))
        raise self.retry(exc=e, countdown=30, max_retries=2)


# ─────────────────────────────────────────────────────────
# QUESTION GENERATORS
# ─────────────────────────────────────────────────────────

def _generate_mcq(ollama: OllamaClient, source_text: str, count: int) -> list:
    """Generate MCQ questions strictly from source content."""
    prompt = f"""You are an educational assessment AI.
Generate exactly {count} multiple-choice questions from the source text below.

STRICT RULES:
1. Every question and answer MUST be directly supported by the source text
2. Do NOT invent facts, names, dates, or concepts not in the source
3. Each question must have exactly 4 options with exactly 1 correct answer
4. Wrong options must be plausible but clearly incorrect based on the text
5. Return ONLY valid JSON, no markdown

REQUIRED JSON SCHEMA:
{{
  "questions": [
    {{
      "type": "mcq",
      "question": "string — clear question",
      "options": [
        {{
          "text": "string — option text",
          "is_correct": true/false,
          "feedback_correct": "string — why this is correct",
          "feedback_incorrect": "string — why this is wrong"
        }}
      ],
      "explanation": "string — explanation of correct answer",
      "source_reference": "string — verbatim phrase from source text that supports this Q"
    }}
  ]
}}

SOURCE TEXT:
{source_text[:6000]}"""

    result = ollama.generate_json(prompt)
    return result.get("questions", [])


def _generate_fitb(ollama: OllamaClient, source_text: str, count: int) -> list:
    """Generate Fill-in-the-Blank questions."""
    prompt = f"""You are an educational assessment AI.
Generate exactly {count} fill-in-the-blank questions from the source text below.

STRICT RULES:
1. Use actual sentences from the source text with key terms blanked out
2. The blank must be a specific, unambiguous term or phrase
3. Use *asterisks* to mark the blank in H5P format: "The *answer* goes here"
4. Return ONLY valid JSON, no markdown

REQUIRED JSON SCHEMA:
{{
  "questions": [
    {{
      "type": "fill_in_the_blank",
      "question": "string — sentence with *blank* marked using asterisks",
      "answer": "string — the correct word/phrase for the blank",
      "hint": "string — a helpful hint (optional)",
      "source_reference": "string — original sentence from source"
    }}
  ]
}}

SOURCE TEXT:
{source_text[:6000]}"""

    result = ollama.generate_json(prompt)
    return result.get("questions", [])


def _generate_match(ollama: OllamaClient, source_text: str, count: int) -> list:
    """Generate Match-the-Pair questions (terms to definitions)."""
    prompt = f"""You are an educational assessment AI.
Generate exactly {count} term-definition pairs for a match-the-pair exercise.
Use concepts and definitions directly from the source text.

STRICT RULES:
1. Only use terms and definitions that appear in the source text
2. Each term must have a unique, unambiguous definition
3. Return ONLY valid JSON, no markdown

REQUIRED JSON SCHEMA:
{{
  "questions": [
    {{
      "type": "match_the_pair",
      "pairs": [
        {{
          "term": "string — concept or term from source",
          "definition": "string — definition or explanation from source"
        }}
      ]
    }}
  ]
}}

SOURCE TEXT:
{source_text[:6000]}"""

    result = ollama.generate_json(prompt)
    return result.get("questions", [])


def _generate_true_false(ollama: OllamaClient, source_text: str, count: int) -> list:
    """Generate True/False questions."""
    prompt = f"""You are an educational assessment AI.
Generate exactly {count} true/false questions from the source text below.

STRICT RULES:
1. Statements must be directly verifiable from the source text
2. Include roughly equal true and false statements
3. False statements should be subtly wrong (not obviously absurd)
4. Return ONLY valid JSON, no markdown

REQUIRED JSON SCHEMA:
{{
  "questions": [
    {{
      "type": "true_false",
      "question": "string — a statement that is true or false",
      "correct_answer": true/false,
      "explanation": "string — why this statement is true or false",
      "source_reference": "string — text from source that supports this"
    }}
  ]
}}

SOURCE TEXT:
{source_text[:6000]}"""

    result = ollama.generate_json(prompt)
    return result.get("questions", [])


# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────

def _extract_source_text(source_result: dict) -> str:
    """Extract full text from Module A result for question generation."""
    structure = source_result.get("structure", {})
    sections = structure.get("sections", [])

    if sections:
        return "\n\n".join(
            f"{s.get('heading', '')}\n{s.get('content', '')}"
            for s in sections
        )

    # Fallback to raw text if structure extraction failed
    return source_result.get("full_text", "")


def _save_package(file_path: str, task_id: str, format: str) -> str:
    """
    Save H5P/SCORM package to MinIO or local filesystem.
    Returns URL/path to the saved file.
    """
    # TODO: Upload to MinIO for production
    # For prototype: save locally
    output_dir = "/tmp/shiksha-packages"
    os.makedirs(output_dir, exist_ok=True)

    ext = ".h5p" if format == "h5p" else ".zip"
    dest = os.path.join(output_dir, f"{task_id}_{format}{ext}")

    import shutil
    shutil.copy2(file_path, dest)

    # In production: return MinIO presigned URL
    return f"/api/packages/{task_id}/{format}"


def _update(task_id: str, status: str, stage: str, progress: int, message: str):
    update_task_status(task_id, status, stage=stage, progress=progress, message=message)
    from services.websocket_manager import broadcast_update
    broadcast_update(task_id, {
        "stage": stage, "progress": progress,
        "message": message, "status": status
    })