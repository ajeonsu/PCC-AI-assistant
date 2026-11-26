"""Google Drive API v3 client with OAuth2, recursive crawl, and rate-limit backoff."""

from __future__ import annotations

import io
import json
import random
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
SHORTCUT_MIME_TYPE = "application/vnd.google-apps.shortcut"

T = TypeVar("T")


@dataclass(slots=True)
class DriveFileRecord:
    """Metadata for a single file discovered in Google Drive."""

    file_id: str
    file_name: str
    folder_path: str
    mime_type: str
    size_bytes: int
    md5_checksum: str | None
    modified_time: str | None
    web_view_link: str | None


class DriveClient:
    """Thin wrapper over Google Drive API v3 for personal OAuth2 access."""

    def __init__(self, credentials_path: Path, token_path: Path) -> None:
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._service: Any | None = None

    def authenticate(self) -> Any:
        """Load cached OAuth2 token or run interactive local-server login."""
        if self._service is not None:
            return self._service

        creds: Credentials | None = None
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Google OAuth credentials not found at: {self.credentials_path}\n"
                        "Download your Desktop App OAuth JSON from Google Cloud Console "
                        "and save it there."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                creds = flow.run_local_server(port=0)

            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")

        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    @staticmethod
    def _execute_with_backoff(fn: Callable[[], T], max_retries: int = 5) -> T:
        """Retry transient Drive API rate-limit (403/429) and 5xx errors."""
        for attempt in range(max_retries):
            try:
                return fn()
            except HttpError as exc:
                status = exc.resp.status if exc.resp is not None else None
                retryable = status in (429, 500, 502, 503, 504)
                if status == 403 and exc.content:
                    try:
                        payload = json.loads(exc.content.decode("utf-8"))
                        reasons = [
                            err.get("reason", "")
                            for err in payload.get("error", {}).get("errors", [])
                        ]
                        retryable = any(
                            r in ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded")
                            for r in reasons
                        )
                    except (ValueError, UnicodeDecodeError):
                        retryable = False

                if not retryable or attempt == max_retries - 1:
                    raise

                sleep_seconds = (2**attempt) + random.uniform(0.1, 0.8)
                time.sleep(sleep_seconds)
        raise RuntimeError("Unreachable retry loop exit")

    def get_folder_metadata(self, folder_id: str) -> dict[str, Any]:
        """Fetch name and metadata for a Drive folder."""
        service = self.authenticate()
        return self._execute_with_backoff(
            lambda: service.files()
            .get(
                fileId=folder_id,
                fields="id, name, mimeType, trashed",
                supportsAllDrives=True,
            )
            .execute()
        )

    def iter_folder_files(self, root_folder_id: str) -> Iterator[DriveFileRecord]:
        """Recursively yield all non-trashed files inside root_folder_id."""
        service = self.authenticate()
        root_meta = self.get_folder_metadata(root_folder_id)
        root_name = root_meta.get("name", "DriveRoot")

        queue: deque[tuple[str, str]] = deque([(root_folder_id, f"/{root_name}")])
        visited_folders: set[str] = set()

        fields = (
            "nextPageToken, "
            "files(id, name, mimeType, size, md5Checksum, modifiedTime, webViewLink, shortcutDetails)"
        )

        while queue:
            folder_id, folder_path = queue.popleft()
            if folder_id in visited_folders:
                continue
            visited_folders.add(folder_id)

            page_token: str | None = None
            while True:
                response = self._execute_with_backoff(
                    lambda pt=page_token, fid=folder_id: service.files()
                    .list(
                        q=f"'{fid}' in parents and trashed = false",
                        spaces="drive",
                        fields=fields,
                        pageSize=1000,
                        pageToken=pt,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                    )
                    .execute()
                )

                for item in response.get("files", []):
                    mime_type = item.get("mimeType", "")
                    if mime_type == FOLDER_MIME_TYPE:
                        queue.append((item["id"], f"{folder_path}/{item['name']}"))
                        continue

                    if mime_type == SHORTCUT_MIME_TYPE:
                        details = item.get("shortcutDetails") or {}
                        if details.get("targetMimeType") == FOLDER_MIME_TYPE and details.get("targetId"):
                            queue.append((details["targetId"], f"{folder_path}/{item['name']}"))
                        continue

                    size_raw = item.get("size")
                    yield DriveFileRecord(
                        file_id=item["id"],
                        file_name=item.get("name", "untitled"),
                        folder_path=folder_path,
                        mime_type=mime_type,
                        size_bytes=int(size_raw) if size_raw is not None else 0,
                        md5_checksum=item.get("md5Checksum"),
                        modified_time=item.get("modifiedTime"),
                        web_view_link=item.get("webViewLink"),
                    )

                page_token = response.get("nextPageToken")
                if not page_token:
                    break

    def download_bytes(self, file_id: str, max_bytes: int | None = None) -> bytes:
        """Download binary file contents via files.get_media (optionally capped)."""
        service = self.authenticate()
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request, chunksize=4 * 1024 * 1024)

        done = False
        while not done:
            _, done = self._execute_with_backoff(downloader.next_chunk)
            if max_bytes is not None and buffer.tell() >= max_bytes:
                break

        data = buffer.getvalue()
        return data[:max_bytes] if max_bytes is not None else data

    def get_start_page_token(self) -> str:
        """Return initial Drive changes startPageToken for incremental sync."""
        service = self.authenticate()
        response = self._execute_with_backoff(
            lambda: service.changes()
            .getStartPageToken(supportsAllDrives=True)
            .execute()
        )
        return response["startPageToken"]

    def list_changes(self, page_token: str) -> tuple[list[dict[str, Any]], str]:
        """Fetch all Drive changes since page_token and return (changes, new_start_page_token)."""
        service = self.authenticate()
        all_changes: list[dict[str, Any]] = []
        current_token: str | None = page_token
        new_start_token = page_token

        fields = (
            "nextPageToken, newStartPageToken, "
            "changes(fileId, removed, file(id, name, mimeType, size, md5Checksum, "
            "modifiedTime, webViewLink, parents, trashed))"
        )

        while current_token:
            response = self._execute_with_backoff(
                lambda ct=current_token: service.changes()
                .list(
                    pageToken=ct,
                    spaces="drive",
                    includeRemoved=True,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageSize=1000,
                    fields=fields,
                )
                .execute()
            )
            all_changes.extend(response.get("changes", []))
            if "newStartPageToken" in response:
                new_start_token = response["newStartPageToken"]
            current_token = response.get("nextPageToken")

        return all_changes, new_start_token
