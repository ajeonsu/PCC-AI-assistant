"""Continuous polling watcher for Google Drive -> Supabase incremental sync."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from rich.console import Console

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
        description="Continuously poll Google Drive changes and sync to Supabase."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Polling interval in seconds (default: 300).",
    )
    parser.add_argument(
        "--skip-ocr",
        action="store_true",
        help="Skip Gemini multimodal OCR for scanned PDF pages.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    console = Console()
    settings = get_settings()

    drive = DriveClient(settings.credentials_file, settings.token_file)
    drive.authenticate()
    gemini = GeminiClient(settings.gemini_api_key, embedding_dim=settings.embedding_dim)

    console.print(
        f"[bold green]Watching Google Drive[/bold green] ({settings.google_drive_root_folder_id}) "
        f"every {args.interval}s. Press Ctrl+C to stop."
    )

    try:
        while True:
            conn = get_connection(settings.database_url)
            try:
                stats = run_sync(
                    conn=conn,
                    drive=drive,
                    gemini=gemini,
                    root_folder_id=settings.google_drive_root_folder_id,
                    skip_ocr=args.skip_ocr,
                    console=console,
                )
                if any((stats.added, stats.updated, stats.deleted, stats.metadata_only, stats.failed)):
                    console.print(
                        f"[cyan]Sync cycle:[/cyan] +{stats.added} ~{stats.updated} "
                        f"meta={stats.metadata_only} -{stats.deleted} fail={stats.failed}"
                    )
            finally:
                conn.close()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped Drive watcher.[/yellow]")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
