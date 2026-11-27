"""Run a single Google Drive -> Supabase sync pass (initial crawl or incremental)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from app.ai.gemini_client import GeminiClient
from app.core.config import get_settings
from app.db.postgres import get_connection
from app.sync.drive_client import DriveClient
from app.sync.pipeline import run_sync


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Google Drive files (PDF, Word, Excel) into Supabase PostgreSQL."
    )
    parser.add_argument(
        "--full-rescan",
        action="store_true",
        help="Force a full recursive crawl instead of incremental changes.list sync.",
    )
    parser.add_argument(
        "--file-id",
        type=str,
        default=None,
        help="Re-index only a single Google Drive file ID.",
    )
    parser.add_argument(
        "--skip-ocr",
        action="store_true",
        help="Skip Gemini multimodal OCR for scanned PDF pages.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview pending changes without modifying Supabase.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    console = Console()
    settings = get_settings()

    if not settings.google_drive_root_folder_id:
        console.print("[red]Error:[/red] GOOGLE_DRIVE_ROOT_FOLDER_ID is empty in .env")
        return 1

    drive = DriveClient(settings.credentials_file, settings.token_file)
    drive.authenticate()

    gemini = GeminiClient(settings.gemini_api_key, embedding_dim=settings.embedding_dim)
    conn = get_connection(settings.database_url)

    try:
        stats = run_sync(
            conn=conn,
            drive=drive,
            gemini=gemini,
            root_folder_id=settings.google_drive_root_folder_id,
            full_rescan=args.full_rescan,
            target_file_id=args.file_id,
            skip_ocr=args.skip_ocr,
            dry_run=args.dry_run,
            console=console,
        )
    finally:
        conn.close()

    table = Table(title=f"Drive Sync Summary ({stats.mode})")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right")
    table.add_row("Added", str(stats.added))
    table.add_row("Updated", str(stats.updated))
    table.add_row("Metadata Only", str(stats.metadata_only))
    table.add_row("Deleted / Purged", str(stats.deleted))
    table.add_row("Skipped", str(stats.skipped))
    table.add_row("Failed", str(stats.failed))
    table.add_row("Document Chunks Created", str(stats.chunks_created))
    table.add_row("Excel SQL Tables Created", str(stats.excel_tables_created))
    table.add_row("PDF Pages OCR'd", str(stats.ocr_pages))
    console.print(table)

    return 1 if stats.failed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
