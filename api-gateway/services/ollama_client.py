# api-gateway/services/ollama_client.py
# Ollama local LLM client with JSON mode, retries, and cloud fallback

import httpx
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)


class OllamaClient:
    """
    Client for local Ollama LLM inference.

    Key design decisions:
    - JSON mode enforced for structured outputs (no markdown parsing)
    - Automatic retry on timeout (Llama 3 8B can be slow on CPU)
    - Optional cloud fallback (GPT-4o) if local inference fails 3x
    - Streaming disabled — we want complete outputs for H5P packaging
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3:8b",
        timeout: int = 180,  # 3 minutes — generous for CPU inference
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.cloud_fallback_key = os.getenv("OPENAI_API_KEY")  # Optional

    def generate_json(self, prompt: str, max_retries: int = 3) -> dict:
        """
        Generate a JSON response from the local LLM.
        Uses Ollama's format:json mode to enforce valid JSON output.
        """
        for attempt in range(1, max_retries + 1):
            try:
                response = self._call_ollama(prompt, json_mode=True)
                parsed = self._safe_parse_json(response)
                if parsed is not None:
                    return parsed

                logger.warning(
                    f"Attempt {attempt}: JSON parse failed, retrying..."
                )

            except httpx.TimeoutException:
                logger.warning(
                    f"Attempt {attempt}: Ollama timeout after {self.timeout}s"
                )
                if attempt < max_retries:
                    time.sleep(5 * attempt)  # Exponential backoff

            except Exception as e:
                logger.error(f"Attempt {attempt}: Ollama error: {e}")
                if attempt < max_retries:
                    time.sleep(5)

        # Cloud fallback if configured and local failed
        if self.cloud_fallback_key:
            logger.warning("Falling back to cloud LLM (GPT-4o)")
            return self._cloud_fallback_json(prompt)

        raise RuntimeError(
            f"Ollama JSON generation failed after {max_retries} attempts. "
            "Check Ollama service and model availability."
        )

    def generate_text(self, prompt: str, max_retries: int = 2) -> str:
        """Generate plain text response from the local LLM."""
        for attempt in range(1, max_retries + 1):
            try:
                return self._call_ollama(prompt, json_mode=False)
            except httpx.TimeoutException:
                logger.warning(f"Attempt {attempt}: Timeout on text generation")
                if attempt < max_retries:
                    time.sleep(5 * attempt)
            except Exception as e:
                logger.error(f"Attempt {attempt}: Error: {e}")
                if attempt < max_retries:
                    time.sleep(5)

        if self.cloud_fallback_key:
            return self._cloud_fallback_text(prompt)

        raise RuntimeError("Text generation failed after retries")

    def check_model_available(self) -> bool:
        """Check if the configured model is pulled and ready."""
        try:
            with httpx.Client(timeout=5) as client:
                response = client.get(f"{self.base_url}/api/tags")
                if response.status_code == 200:
                    models = [m["name"] for m in response.json().get("models", [])]
                    return any(
                        self.model in m or m.startswith(self.model.split(":")[0])
                        for m in models
                    )
        except Exception:
            pass
        return False

    def _call_ollama(self, prompt: str, json_mode: bool = False) -> str:
        """Raw Ollama API call."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,       # Low temp for factual extraction
                "top_p": 0.9,
                "num_predict": 2048,      # Max tokens in response
                "num_ctx": 8192,          # Context window
            }
        }

        if json_mode:
            payload["format"] = "json"

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/api/generate",
                json=payload
            )
            response.raise_for_status()
            return response.json()["response"]

    def _safe_parse_json(self, text: str) -> Optional[dict]:
        """
        Safely parse JSON from LLM output.
        Handles common LLM JSON formatting issues.
        """
        text = text.strip()

        # Remove markdown code fences if present (some models ignore format:json)
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.debug(f"JSON parse error: {e} — Raw: {text[:200]}")
            return None

    def _cloud_fallback_json(self, prompt: str) -> dict:
        """Optional GPT-4o fallback (requires OPENAI_API_KEY env var)."""
        import openai
        client = openai.OpenAI(api_key=self.cloud_fallback_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Return only valid JSON, no markdown."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        return json.loads(response.choices[0].message.content)

    def _cloud_fallback_text(self, prompt: str) -> str:
        """Optional GPT-4o text fallback."""
        import openai
        client = openai.OpenAI(api_key=self.cloud_fallback_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return response.choices[0].message.content