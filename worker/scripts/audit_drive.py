"""Phase 0 read-only Google Drive audit script.

Counts files by category (PDF, Word, Excel, Google Docs/Sheets, Other),
calculates sizes, lists the largest files, and optionally samples PDFs
with PyMuPDF to estimate how many pages are scanned images vs. text.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import pymupdf
from rich.console import Console
from rich.table import Table

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from app.core.config import get_settings
from app.sync.drive_client import DriveClient, DriveFileRecord

MIME_GOOGLE_DOC = "application/vnd.google-apps.document"
MIME_GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
MIME_PDF = "application/pdf"
WORD_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
EXCEL_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
}

MAX_SAMPLE_PDF_BYTES = 50 * 1024 * 1024  # 50 MB cap when sampling PDFs


def classify_file(record: DriveFileRecord) -> str:
    """Group a DriveFileRecord into a reporting category."""
    name_lower = record.file_name.lower()
    mime = record.mime_type

    if mime == MIME_PDF or name_lower.endswith(".pdf"):
        return "PDF"
    if mime in WORD_MIMES or name_lower.endswith((".docx", ".doc")):
        return "Word (.docx/.doc)"
    if mime in EXCEL_MIMES or name_lower.endswith((".xlsx", ".xls", ".csv")):
        return "Excel/CSV"
    if mime == MIME_GOOGLE_DOC:
        return "Google Docs"
    if mime == MIME_GOOGLE_SHEET:
        return "Google Sheets"
    return "Other/Unsupported"


def format_size(size_bytes: int) -> str:
    """Format byte count into human-readable MB/GB."""
    if size_bytes >= 1024**3:
        return f"{size_bytes / (1024**3):.2f} GB"
    if size_bytes >= 1024**2:
        return f"{size_bytes / (1024**2):.2f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def sample_pdfs(
    client: DriveClient,
    pdf_records: list[DriveFileRecord],
    sample_count: int,
    console: Console,
) -> dict[str, int | float | list[dict[str, object]]]:
    """Download up to sample_count PDFs (<= 50 MB) and classify text vs. scanned pages."""
    eligible = [r for r in pdf_records if 0 < r.size_bytes <= MAX_SAMPLE_PDF_BYTES][:sample_count]
    total_pages = 0
    text_pages = 0
    scanned_pages = 0
    sampled_details: list[dict[str, object]] = []

    for rec in eligible:
        try:
            pdf_bytes = client.download_bytes(rec.file_id)
            with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
                file_pages = len(doc)
                file_scanned = 0
                for page in doc:
                    if len(page.get_text("text").strip()) >= 30:
                        text_pages += 1
                    else:
                        scanned_pages += 1
                        file_scanned += 1
                total_pages += file_pages
                sampled_details.append(
                    {
                        "file_id": rec.file_id,
                        "file_name": rec.file_name,
                        "pages": file_pages,
                        "scanned_pages": file_scanned,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Warning:[/yellow] Could not sample {rec.file_name}: {exc}")

    scanned_pct = (scanned_pages / total_pages * 100.0) if total_pages else 0.0
    return {
        "sampled_files": len(sampled_details),
        "total_pages": total_pages,
        "text_pages": text_pages,
        "scanned_pages": scanned_pages,
        "scanned_percentage": round(scanned_pct, 1),
        "details": sampled_details,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Phase 0 audit of a Google Drive folder for RAG ingestion planning."
    )
    parser.add_argument(
        "--folder-id",
        type=str,
        default=None,
        help="Target Google Drive folder ID (defaults to GOOGLE_DRIVE_ROOT_FOLDER_ID in .env).",
    )
    parser.add_argument(
        "--sample-pdfs",
        type=int,
        default=10,
        help="Number of PDFs (<= 50 MB) to inspect for text vs. scanned pages (0 to skip).",
    )
    parser.add_argument(
        "--export-json",
        type=str,
        default=None,
        help="Optional output path to save the full audit report and file inventory JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    console = Console()
    settings = get_settings()

    folder_id = args.folder_id or settings.google_drive_root_folder_id
    if not folder_id:
        console.print(
            "[red]Error:[/red] No folder ID provided. Pass --folder-id <ID> or set "
            "GOOGLE_DRIVE_ROOT_FOLDER_ID in your root .env file."
        )
        return 1

    client = DriveClient(
        credentials_path=settings.credentials_file,
        token_path=settings.token_file,
    )

    console.print(f"[bold]Auditing Google Drive folder:[/bold] {folder_id}")
    console.print("Authorizing Google Drive access...")
    client.authenticate()
    records: list[DriveFileRecord] = []
    with console.status("Scanning Google Drive folders recursively..."):
        for rec in client.iter_folder_files(folder_id):
            records.append(rec)

    if not records:
        console.print("[yellow]No files found in the target folder.[/yellow]")
        return 0

    category_counts: dict[str, int] = defaultdict(int)
    category_sizes: dict[str, int] = defaultdict(int)
    pdf_records: list[DriveFileRecord] = []

    for rec in records:
        cat = classify_file(rec)
        category_counts[cat] += 1
        category_sizes[cat] += rec.size_bytes
        if cat == "PDF":
            pdf_records.append(rec)

    total_bytes = sum(r.size_bytes for r in records)

    summary_table = Table(title="Google Drive Audit Summary")
    summary_table.add_column("Category", style="cyan")
    summary_table.add_column("File Count", justify="right")
    summary_table.add_column("Total Size", justify="right")
    summary_table.add_column("% of Size", justify="right")

    for cat in sorted(category_counts.keys()):
        size_b = category_sizes[cat]
        pct = (size_b / total_bytes * 100.0) if total_bytes else 0.0
        summary_table.add_row(cat, str(category_counts[cat]), format_size(size_b), f"{pct:.1f}%")

    summary_table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{len(records)}[/bold]",
        f"[bold]{format_size(total_bytes)}[/bold]",
        "[bold]100.0%[/bold]",
    )
    console.print(summary_table)

    largest_table = Table(title="Top 15 Largest Files")
    largest_table.add_column("Size", justify="right", style="magenta")
    largest_table.add_column("Category", style="cyan")
    largest_table.add_column("File Name")
    largest_table.add_column("Folder Path", style="dim")

    top_files = sorted(records, key=lambda r: r.size_bytes, reverse=True)[:15]
    for rec in top_files:
        largest_table.add_row(
            format_size(rec.size_bytes),
            classify_file(rec),
            rec.file_name,
            rec.folder_path,
        )
    console.print(largest_table)

    pdf_sample_report: dict[str, object] = {"sampled_files": 0}
    if args.sample_pdfs > 0 and pdf_records:
        with console.status(f"Sampling up to {args.sample_pdfs} PDFs for OCR/scan detection..."):
            pdf_sample_report = sample_pdfs(client, pdf_records, args.sample_pdfs, console)
        console.print(
            f"\n[bold]PDF Scan Estimate:[/bold] Sampled {pdf_sample_report['sampled_files']} PDFs "
            f"({pdf_sample_report['total_pages']} pages) -> "
            f"[yellow]{pdf_sample_report['scanned_percentage']}%[/yellow] appear to be scanned/image pages."
        )

    console.print(
        "\n[bold green]Pilot Sizing Tip:[/bold green] Pick 20-40 representative files "
        "(~200 MB-1 GB raw) including your 5,000-row Excel sheets for the Stage A Supabase Free pilot "
        "(500 MB database limit)."
    )

    if args.export_json:
        export_path = Path(args.export_json)
        payload = {
            "root_folder_id": folder_id,
            "total_files": len(records),
            "total_bytes": total_bytes,
            "categories": {
                cat: {"count": category_counts[cat], "bytes": category_sizes[cat]}
                for cat in sorted(category_counts.keys())
            },
            "pdf_scan_sample": pdf_sample_report,
            "files": [
                {
                    "file_id": r.file_id,
                    "file_name": r.file_name,
                    "folder_path": r.folder_path,
                    "mime_type": r.mime_type,
                    "category": classify_file(r),
                    "size_bytes": r.size_bytes,
                    "md5_checksum": r.md5_checksum,
                    "modified_time": r.modified_time,
                    "web_view_link": r.web_view_link,
                }
                for r in records
            ],
        }
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"Saved JSON audit report to: [bold]{export_path}[/bold]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
