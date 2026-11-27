"""Parsers for PDF (with Gemini multimodal OCR fallback), Word (.docx), and plain text."""

from __future__ import annotations

import io
from typing import Any

import docx
import pymupdf
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.ai.gemini_client import GeminiClient
from app.parsers.chunker import DocumentChunk, chunk_text


def parse_pdf_bytes(
    data: bytes,
    file_name: str,
    gemini: GeminiClient | None,
    ocr_scanned: bool = True,
) -> tuple[list[DocumentChunk], dict[str, int]]:
    """Extract text per PDF page, falling back to Gemini OCR for scanned pages."""
    chunks: list[DocumentChunk] = []
    chunk_idx = 0
    total_pages = 0
    ocr_pages = 0

    with pymupdf.open(stream=data, filetype="pdf") as doc:
        total_pages = len(doc)
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if len(text) < 30 and ocr_scanned and gemini is not None:
                png_bytes = page.get_pixmap(dpi=150).tobytes("png")
                text = gemini.ocr_pdf_page_image(png_bytes, file_name, page_num)
                if text:
                    ocr_pages += 1

            for piece in chunk_text(text):
                chunks.append(
                    DocumentChunk(
                        chunk_index=chunk_idx,
                        page_number=page_num,
                        section_heading=None,
                        content=piece,
                    )
                )
                chunk_idx += 1

    return chunks, {"pages": total_pages, "ocr_pages": ocr_pages}


def _table_to_markdown(table: Table) -> str:
    rows_md: list[str] = []
    for row_idx, row in enumerate(table.rows):
        cells = [" ".join(cell.text.split()) for cell in row.cells]
        if not any(cells):
            continue
        rows_md.append("| " + " | ".join(cells) + " |")
        if row_idx == 0:
            rows_md.append("| " + " | ".join("---" for _ in cells) + " |")
    return "\n".join(rows_md)


def parse_docx_bytes(data: bytes, file_name: str) -> list[DocumentChunk]:
    """Extract paragraphs and tables in document order from a .docx file."""
    doc = docx.Document(io.BytesIO(data))
    sections: list[tuple[str | None, list[str]]] = []
    current_heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            sections.append((current_heading, list(buffer)))
            buffer.clear()

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            para = Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue
            style_name = (para.style.name if para.style else "") or ""
            if style_name.lower().startswith("heading"):
                flush()
                current_heading = text
            else:
                buffer.append(text)
        elif isinstance(child, CT_Tbl):
            tbl_md = _table_to_markdown(Table(child, doc))
            if tbl_md:
                buffer.append(tbl_md)

    flush()

    chunks: list[DocumentChunk] = []
    chunk_idx = 0
    for heading, parts in sections:
        joined = "\n\n".join(parts)
        for piece in chunk_text(joined):
            content = f"## {heading}\n\n{piece}" if heading else piece
            chunks.append(
                DocumentChunk(
                    chunk_index=chunk_idx,
                    page_number=None,
                    section_heading=heading,
                    content=content,
                )
            )
            chunk_idx += 1

    return chunks


def parse_text_bytes(data: bytes, file_name: str) -> list[DocumentChunk]:
    """Decode plain text / markdown files into chunks."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")

    return [
        DocumentChunk(
            chunk_index=idx,
            page_number=None,
            section_heading=None,
            content=piece,
        )
        for idx, piece in enumerate(chunk_text(text))
    ]
