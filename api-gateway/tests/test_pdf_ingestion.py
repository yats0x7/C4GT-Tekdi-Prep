# api-gateway/tests/test_pdf_ingestion.py
# Integration tests for Module A — PDF ingestion pipeline

import pytest
import json
import os
import tempfile
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ─────────────────────────────────────────────────────────
# PDF EXTRACTOR TESTS
# ─────────────────────────────────────────────────────────

class TestPDFExtractor:

    def test_extract_returns_required_fields(self, sample_pdf_path):
        from services.pdf_extractor import PDFExtractor
        extractor = PDFExtractor(sample_pdf_path)
        result = extractor.extract()

        assert "page_count" in result
        assert "word_count" in result
        assert "metadata" in result
        assert "pages" in result
        assert "full_text" in result
        assert result["page_count"] > 0
        assert result["word_count"] > 0
        assert isinstance(result["pages"], list)

    def test_extract_nonexistent_file_raises(self):
        from services.pdf_extractor import PDFExtractor
        with pytest.raises(FileNotFoundError):
            PDFExtractor("/nonexistent/path.pdf")

    def test_heading_detection_returns_list(self, sample_pdf_path):
        from services.pdf_extractor import PDFExtractor
        extractor = PDFExtractor(sample_pdf_path)
        result = extractor.extract()

        for page in result["pages"]:
            assert isinstance(page["headings"], list)


# ─────────────────────────────────────────────────────────
# OLLAMA CLIENT TESTS (mocked)
# ─────────────────────────────────────────────────────────

class TestOllamaClient:

    @patch("httpx.Client")
    def test_generate_json_parses_valid_response(self, mock_client):
        from services.ollama_client import OllamaClient

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": json.dumps({
                "title": "Test Document",
                "sections": [{"heading": "Intro", "content": "..."}]
            })
        }
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        client = OllamaClient(model="llama3:8b")
        result = client.generate_json("Test prompt")

        assert result["title"] == "Test Document"
        assert len(result["sections"]) == 1

    @patch("httpx.Client")
    def test_generate_json_retries_on_invalid_json(self, mock_client):
        from services.ollama_client import OllamaClient

        # First call returns invalid JSON, second returns valid
        mock_response_bad = MagicMock()
        mock_response_bad.status_code = 200
        mock_response_bad.json.return_value = {"response": "not json at all"}

        mock_response_good = MagicMock()
        mock_response_good.status_code = 200
        mock_response_good.json.return_value = {"response": '{"key": "value"}'}

        mock_client.return_value.__enter__.return_value.post.side_effect = [
            mock_response_bad, mock_response_good
        ]

        client = OllamaClient(model="llama3:8b")
        result = client.generate_json("Test prompt", max_retries=2)

        assert result == {"key": "value"}

    @patch("httpx.Client")
    def test_generate_json_strips_markdown_fences(self, mock_client):
        from services.ollama_client import OllamaClient

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": '```json\n{"title": "Test"}\n```'
        }
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        client = OllamaClient(model="llama3:8b")
        result = client.generate_json("Test prompt")

        assert result["title"] == "Test"


# ─────────────────────────────────────────────────────────
# H5P PACKAGER TESTS
# ─────────────────────────────────────────────────────────

class TestH5PPackager:

    def test_manifest_validation_passes_valid_manifest(self):
        from services.h5p_packager import H5PPackager
        packager = H5PPackager()
        manifest = packager._build_manifest("Test Quiz")

        # Should not raise
        packager._validate_manifest(manifest)

    def test_manifest_validation_fails_missing_mainLibrary(self):
        from services.h5p_packager import H5PPackager
        import jsonschema

        packager = H5PPackager()
        bad_manifest = {
            "title": "Test",
            "language": "en",
            "preloadedDependencies": [
                {"machineName": "H5P.QuestionSet", "majorVersion": 1, "minorVersion": 20}
            ]
            # Missing "mainLibrary"
        }

        with pytest.raises(ValueError, match="H5P manifest validation failed"):
            packager._validate_manifest(bad_manifest)

    def test_mcq_question_has_required_fields(self):
        from services.h5p_packager import H5PPackager
        packager = H5PPackager()

        q = {
            "type": "mcq",
            "question": "What is the capital of France?",
            "options": [
                {"text": "Paris", "is_correct": True},
                {"text": "London", "is_correct": False},
                {"text": "Berlin", "is_correct": False},
                {"text": "Madrid", "is_correct": False},
            ]
        }

        result = packager._build_mcq(q)

        assert result["library"] == "H5P.MultiChoice 1.16"
        assert "params" in result
        assert "subContentId" in result
        assert len(result["params"]["answers"]) == 4

    def test_package_quiz_creates_valid_zip(self, tmp_path):
        from services.h5p_packager import H5PPackager
        import zipfile

        packager = H5PPackager()
        quiz_data = {
            "questions": [
                {
                    "type": "mcq",
                    "question": "Test question?",
                    "options": [
                        {"text": "A", "is_correct": True},
                        {"text": "B", "is_correct": False},
                        {"text": "C", "is_correct": False},
                        {"text": "D", "is_correct": False},
                    ]
                }
            ]
        }

        output_path = str(tmp_path / "test.h5p")
        packager.package_quiz(quiz_data, output_path, "Test Quiz")

        assert os.path.exists(output_path)

        with zipfile.ZipFile(output_path) as zf:
            names = zf.namelist()
            assert "h5p.json" in names
            assert "content/content.json" in names

            h5p_json = json.loads(zf.read("h5p.json"))
            assert h5p_json["mainLibrary"] == "H5P.QuestionSet"
            assert h5p_json["title"] == "Test Quiz"


# ─────────────────────────────────────────────────────────
# API ENDPOINT TESTS
# ─────────────────────────────────────────────────────────

class TestAPIEndpoints:

    def test_health_check_returns_200(self):
        with patch("httpx.AsyncClient") as mock:
            mock.return_value.__aenter__.return_value.get.return_value.status_code = 200
            mock.return_value.__aenter__.return_value.get.return_value.json.return_value = {
                "models": [{"name": "llama3:8b"}]
            }
            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"

    def test_ingest_pdf_rejects_non_pdf(self):
        fake_file = b"this is not a pdf"
        response = client.post(
            "/api/ingest/pdf",
            files={"file": ("test.txt", fake_file, "text/plain")}
        )
        assert response.status_code == 400
        assert "PDF" in response.json()["detail"]

    @patch("tasks.ingestion.process_pdf_task.apply_async")
    def test_ingest_pdf_returns_task_id(self, mock_apply_async):
        fake_pdf = b"%PDF-1.4 fake pdf content"
        response = client.post(
            "/api/ingest/pdf",
            files={"file": ("document.pdf", fake_pdf, "application/pdf")}
        )
        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["status"] == "queued"
        assert "poll_url" in data
        mock_apply_async.assert_called_once()


# ─────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────

@pytest.fixture
def sample_pdf_path(tmp_path):
    """Create a minimal valid PDF for testing."""
    import fitz
    pdf_path = str(tmp_path / "sample.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (50, 72),
        "Introduction to Machine Learning\n\n"
        "Machine learning is a subset of artificial intelligence. "
        "It enables computers to learn from data without being explicitly programmed. "
        "Key concepts include supervised learning, unsupervised learning, "
        "and reinforcement learning.\n\n"
        "Supervised Learning\n\n"
        "In supervised learning, the model is trained on labeled data. "
        "The algorithm learns to map input features to output labels.",
        fontsize=12
    )
    doc.save(pdf_path)
    doc.close()
    return pdf_path