"""Gemini client for 768-dim halfvec embeddings and multimodal PDF OCR."""

from __future__ import annotations

import math
import random
import time
from typing import Callable, TypeVar

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

T = TypeVar("T")


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        return vec
    return [v / norm for v in vec]


class GeminiClient:
    """Thin wrapper around google-genai for embeddings and scanned PDF OCR."""

    def __init__(self, api_key: str, embedding_dim: int = 768) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required in .env.")
        self.embedding_dim = embedding_dim
        self._client = genai.Client(api_key=api_key)

    @staticmethod
    def _retry(fn: Callable[[], T], max_retries: int = 6) -> T:
        for attempt in range(max_retries):
            try:
                return fn()
            except genai_errors.APIError as exc:
                code = getattr(exc, "code", None)
                retryable = code in (429, 500, 502, 503, 504)
                if not retryable or attempt == max_retries - 1:
                    raise
                delay = min(30.0, (2**attempt) * 1.5 + random.uniform(0.2, 1.0))
                time.sleep(delay)
        raise RuntimeError("Unreachable retry loop exit")

    def ping(self) -> str:
        """Quick check that chat generation works with the configured key."""
        resp = self._retry(
            lambda: self._client.models.generate_content(
                model="gemini-3.8-flash",
                contents="Reply with OK only.",
            )
        )
        return (resp.text or "").strip()

    def embed_texts(
        self,
        texts: list[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
        batch_size: int = 40,
    ) -> list[list[float]]:
        """Embed texts in batches using gemini-embedding-001 and L2-normalize."""
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = [t if t.strip() else " " for t in texts[start : start + batch_size]]
            resp = self._retry(
                lambda b=batch: self._client.models.embed_content(
                    model="gemini-embedding-001",
                    contents=b,
                    config=types.EmbedContentConfig(
                        output_dimensionality=self.embedding_dim,
                        task_type=task_type,
                    ),
                )
            )
            for emb in resp.embeddings or []:
                vectors.append(_l2_normalize(list(emb.values or [])))
            if start + batch_size < len(texts):
                time.sleep(0.3)
        return vectors

    def ocr_pdf_page_image(self, png_bytes: bytes, file_name: str, page_number: int) -> str:
        """Transcribe a scanned PDF page image into verbatim Markdown."""
        prompt = (
            f"Transcribe ALL readable text from page {page_number} of '{file_name}' verbatim into clean Markdown. "
            "Preserve headings, bullet lists, tables (as Markdown tables), names, dates, signatures/labels, and exact numbers. "
            "Do NOT summarize, interpret, or invent unreadable text. Return only the transcribed content."
        )
        time.sleep(1.5)  # Respect Gemini free-tier RPM pacing
        resp = self._retry(
            lambda: self._client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[
                    types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                    prompt,
                ],
                config=types.GenerateContentConfig(temperature=0.0),
            )
        )
        return (resp.text or "").strip()
