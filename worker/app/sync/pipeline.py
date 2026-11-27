"""Incremental and full-rescan Google Drive ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psycopg
from rich.console import Console

from app.ai.gemini_client import GeminiClient
from app.db.postgres import (
    get_registry_map,
    get_sync_state,
    insert_document_chunks,
    load_excel_sheet,
    mark_file_deleted,
    purge_file_artifacts,
    save_sync_state,
    update_file_metadata_only,
    upsert_file_registry,
)
from app.parsers.documents import parse_docx_bytes, parse_pdf_bytes, parse_text_bytes
from app.parsers.excel import parse_spreadsheet_bytes
from app.sync.drive_client import DriveClient, DriveFileRecord, FOLDER_MIME_TYPE

MIME_GOOGLE_DOC = "application/vnd.google-apps.document"
MIME_GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
MIME_PDF = "application/pdf"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_XLS = "application/vnd.ms-excel"
MIME_XLSM = "application/vnd.ms-excel.sheet.macroEnabled.12"
MIME_CSV = "text/csv"
MIME_TEXT = {"text/plain", "text/markdown"}


@dataclass(slots=True)
class SyncStats:
    """Counters summarizing one sync execution."""

    mode: str
    added: int = 0
    updated: int = 0
    metadata_only: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    chunks_created: int = 0
    excel_tables_created: int = 0
    ocr_pages: int = 0
    errors: list[str] = field(default_factory=list)


def _classify_handler(rec: DriveFileRecord) -> str | None:
    mime = rec.mime_type
    name = rec.file_name.lower()

    if mime == MIME_PDF or name.endswith(".pdf"):
        return "pdf"
    if mime == MIME_DOCX or name.endswith(".docx"):
        return "docx"
    if mime == MIME_GOOGLE_DOC:
        return "gdoc"
    if mime == MIME_CSV or name.endswith(".csv"):
        return "csv"
    if mime in (MIME_XLSX, MIME_XLS, MIME_XLSM) or name.endswith((".xlsx", ".xls", ".xlsm")):
        return "excel"
    if mime == MIME_GOOGLE_SHEET:
        return "gsheet"
    if mime in MIME_TEXT or name.endswith((".txt", ".md")):
        return "text"
    return None


def _parse_iso_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def _content_changed(existing: dict[str, Any], rec: DriveFileRecord) -> bool:
    if existing.get("sync_status") != "indexed":
        return True
    if rec.md5_checksum and existing.get("md5_checksum"):
        return rec.md5_checksum != existing["md5_checksum"]
    old_dt = _parse_iso_dt(existing.get("modified_time"))
    new_dt = _parse_iso_dt(rec.modified_time)
    return old_dt != new_dt


def _metadata_changed(existing: dict[str, Any], rec: DriveFileRecord) -> bool:
    return (
        existing.get("file_name") != rec.file_name
        or existing.get("folder_path") != rec.folder_path
        or existing.get("web_view_link") != rec.web_view_link
    )


def _ingest_single_file(
    conn: psycopg.Connection,
    drive: DriveClient,
    gemini: GeminiClient,
    rec: DriveFileRecord,
    skip_ocr: bool,
    console: Console,
) -> tuple[int, int, int]:
    """Purge old artifacts, parse, embed, and write to Supabase in one transaction."""
    handler = _classify_handler(rec)
    if handler is None:
        return 0, 0, 0

    upsert_file_registry(conn, rec, "indexing")
    purge_file_artifacts(conn, rec.file_id)

    chunks_added = 0
    tables_added = 0
    ocr_pages = 0

    if handler in ("pdf", "docx", "gdoc", "text"):
        if handler == "gdoc":
            raw = drive.export_bytes(rec.file_id, MIME_DOCX)
            chunks = parse_docx_bytes(raw, rec.file_name)
        elif handler == "docx":
            raw = drive.download_bytes(rec.file_id)
            chunks = parse_docx_bytes(raw, rec.file_name)
        elif handler == "pdf":
            raw = drive.download_bytes(rec.file_id)
            chunks, pdf_stats = parse_pdf_bytes(
                raw, rec.file_name, gemini=gemini, ocr_scanned=not skip_ocr
            )
            ocr_pages += pdf_stats["ocr_pages"]
        else:
            raw = drive.download_bytes(rec.file_id)
            chunks = parse_text_bytes(raw, rec.file_name)

        if chunks:
            embeddings = gemini.embed_texts(
                [c.content for c in chunks], task_type="RETRIEVAL_DOCUMENT"
            )
            chunks_added = insert_document_chunks(conn, rec, chunks, embeddings)

    elif handler in ("excel", "csv", "gsheet"):
        if handler == "gsheet":
            raw = drive.export_bytes(rec.file_id, MIME_XLSX)
            sheets = parse_spreadsheet_bytes(
                raw, rec.file_id, rec.file_name, rec.folder_path, rec.modified_time, is_csv=False
            )
        else:
            raw = drive.download_bytes(rec.file_id)
            sheets = parse_spreadsheet_bytes(
                raw,
                rec.file_id,
                rec.file_name,
                rec.folder_path,
                rec.modified_time,
                is_csv=(handler == "csv"),
            )

        if sheets:
            embeddings = gemini.embed_texts(
                [s.summary_text for s in sheets], task_type="RETRIEVAL_DOCUMENT"
            )
            for sheet, emb in zip(sheets, embeddings):
                load_excel_sheet(conn, sheet, rec, emb)
                tables_added += 1

    upsert_file_registry(conn, rec, "indexed")
    console.print(
        f"  [green]Indexed[/green] {rec.file_name} "
        f"(chunks={chunks_added}, sql_tables={tables_added}, ocr_pages={ocr_pages})"
    )
    return chunks_added, tables_added, ocr_pages


def run_sync(
    conn: psycopg.Connection,
    drive: DriveClient,
    gemini: GeminiClient,
    root_folder_id: str,
    full_rescan: bool = False,
    target_file_id: str | None = None,
    skip_ocr: bool = False,
    dry_run: bool = False,
    console: Console | None = None,
) -> SyncStats:
    """Run either initial/full crawl or incremental changes.list sync."""
    console = console or Console()
    saved_token = None if full_rescan else get_sync_state(conn, root_folder_id)
    registry = get_registry_map(conn)

    if saved_token is None or target_file_id is not None:
        stats = SyncStats(mode="full_crawl" if target_file_id is None else "single_file")
        start_token = drive.get_start_page_token() if not dry_run and target_file_id is None else None
        records = list(drive.iter_folder_files(root_folder_id))
        if target_file_id is not None:
            records = [r for r in records if r.file_id == target_file_id]

        seen_ids: set[str] = set()
        for rec in records:
            seen_ids.add(rec.file_id)
            if _classify_handler(rec) is None:
                stats.skipped += 1
                continue

            existing = registry.get(rec.file_id)
            if existing is None:
                action = "add"
            elif _content_changed(existing, rec):
                action = "update"
            elif _metadata_changed(existing, rec):
                action = "metadata"
            else:
                stats.skipped += 1
                continue

            if dry_run:
                console.print(f"[dim]DRY RUN ({action}):[/dim] {rec.folder_path}/{rec.file_name}")
                continue

            if action == "metadata":
                update_file_metadata_only(conn, rec)
                conn.commit()
                stats.metadata_only += 1
                continue

            try:
                c_add, t_add, o_pages = _ingest_single_file(
                    conn, drive, gemini, rec, skip_ocr=skip_ocr, console=console
                )
                conn.commit()
                if action == "add":
                    stats.added += 1
                else:
                    stats.updated += 1
                stats.chunks_created += c_add
                stats.excel_tables_created += t_add
                stats.ocr_pages += o_pages
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                upsert_file_registry(conn, rec, "failed", error_message=str(exc)[:1000])
                conn.commit()
                stats.failed += 1
                stats.errors.append(f"{rec.file_name}: {exc}")
                console.print(f"  [red]Failed[/red] {rec.file_name}: {exc}")

        if target_file_id is None and not dry_run:
            # Purge any previously indexed files no longer present in root_folder_id
            for old_id, old_row in registry.items():
                if old_id not in seen_ids and old_row.get("sync_status") != "deleted":
                    mark_file_deleted(conn, old_id)
                    conn.commit()
                    stats.deleted += 1
                    console.print(f"  [yellow]Deleted[/yellow] {old_row['file_name']}")

            if start_token:
                save_sync_state(conn, root_folder_id, start_token)
                conn.commit()

        return stats

    # Incremental sync via changes.list
    stats = SyncStats(mode="incremental")
    changes, new_token = drive.list_changes(saved_token)
    folder_cache: dict[str, dict[str, Any]] = {}

    for ch in changes:
        file_id = ch.get("fileId")
        if not file_id:
            continue
        file_obj = ch.get("file") or {}
        removed = bool(ch.get("removed")) or bool(file_obj.get("trashed"))

        if file_obj.get("mimeType") == FOLDER_MIME_TYPE and not removed:
            continue

        if removed:
            if file_id in registry and registry[file_id].get("sync_status") != "deleted":
                if not dry_run:
                    mark_file_deleted(conn, file_id)
                    conn.commit()
                stats.deleted += 1
                console.print(f"  [yellow]Deleted[/yellow] {registry[file_id]['file_name']}")
            continue

        folder_path = drive.resolve_folder_path(
            file_obj.get("parents"), root_folder_id, folder_cache
        )
        if folder_path is None:
            # File moved outside the watched root folder
            if file_id in registry and registry[file_id].get("sync_status") != "deleted":
                if not dry_run:
                    mark_file_deleted(conn, file_id)
                    conn.commit()
                stats.deleted += 1
                console.print(f"  [yellow]Moved out -> purged[/yellow] {registry[file_id]['file_name']}")
            continue

        size_raw = file_obj.get("size")
        rec = DriveFileRecord(
            file_id=file_id,
            file_name=file_obj.get("name", "untitled"),
            folder_path=folder_path,
            mime_type=file_obj.get("mimeType", ""),
            size_bytes=int(size_raw) if size_raw is not None else 0,
            md5_checksum=file_obj.get("md5Checksum"),
            modified_time=file_obj.get("modifiedTime"),
            web_view_link=file_obj.get("webViewLink"),
        )
        if _classify_handler(rec) is None:
            stats.skipped += 1
            continue

        existing = registry.get(rec.file_id)
        if existing and not _content_changed(existing, rec):
            if _metadata_changed(existing, rec):
                if not dry_run:
                    update_file_metadata_only(conn, rec)
                    conn.commit()
                stats.metadata_only += 1
            else:
                stats.skipped += 1
            continue

        if dry_run:
            console.print(f"[dim]DRY RUN (change):[/dim] {rec.folder_path}/{rec.file_name}")
            continue

        try:
            c_add, t_add, o_pages = _ingest_single_file(
                conn, drive, gemini, rec, skip_ocr=skip_ocr, console=console
            )
            conn.commit()
            if existing is None:
                stats.added += 1
            else:
                stats.updated += 1
            stats.chunks_created += c_add
            stats.excel_tables_created += t_add
            stats.ocr_pages += o_pages
            registry[rec.file_id] = {
                "file_id": rec.file_id,
                "file_name": rec.file_name,
                "folder_path": rec.folder_path,
                "mime_type": rec.mime_type,
                "size_bytes": rec.size_bytes,
                "md5_checksum": rec.md5_checksum,
                "modified_time": rec.modified_time,
                "web_view_link": rec.web_view_link,
                "sync_status": "indexed",
            }
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            upsert_file_registry(conn, rec, "failed", error_message=str(exc)[:1000])
            conn.commit()
            stats.failed += 1
            stats.errors.append(f"{rec.file_name}: {exc}")
            console.print(f"  [red]Failed[/red] {rec.file_name}: {exc}")

    if not dry_run and new_token:
        save_sync_state(conn, root_folder_id, new_token)
        conn.commit()

    return stats
