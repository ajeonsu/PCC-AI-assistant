"""Section-aware text chunking utilities."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class DocumentChunk:
    """Normalized chunk ready for embedding and PostgreSQL storage."""

    chunk_index: int
    page_number: int | None
    section_heading: str | None
    content: str


def chunk_text(text: str, max_chars: int = 1800, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks preferring paragraph and sentence boundaries."""
    cleaned = re.sub(r"\r\n?", "\n", text).strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    total = len(cleaned)

    while start < total:
        end = min(total, start + max_chars)
        if end < total:
            window = cleaned[start:end]
            split_at = -1
            for sep in ("\n\n", "\n", ". ", "; ", " "):
                pos = window.rfind(sep)
                if pos > max_chars // 3:
                    split_at = start + pos + len(sep)
                    break
            if split_at > start:
                end = split_at

        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)

        if end >= total:
            break
        next_start = max(end - overlap, start + 1)
        start = next_start

    return chunks
