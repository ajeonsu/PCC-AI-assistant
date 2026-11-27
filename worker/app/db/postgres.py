"""PostgreSQL storage, pgvector halfvec persistence, and COPY Excel loader."""

from __future__ import annotations

import json
import math
from typing import Any

import psycopg
from pgvector import HalfVector
from pgvector.psycopg import register_vector
from psycopg import sql

from app.parsers.chunker import DocumentChunk
from app.parsers.excel import ParsedSheet
from app.sync.drive_client import DriveFileRecord


def get_connection(database_url: str) -> psycopg.Connection:
    """Open a psycopg connection with pgvector type support registered."""
    if not database_url:
        raise ValueError("DATABASE_URL is not configured in .env.")
    conn = psycopg.connect(database_url, connect_timeout=15)
    register_vector(conn)
    return conn


def get_sync_state(conn: psycopg.Connection, root_folder_id: str) -> str | None:
    row = conn.execute(
        "select drive_start_page_token from public.sync_state where id = 1 and root_folder_id = %s",
        (root_folder_id,),
    ).fetchone()
    return row[0] if row else None


def save_sync_state(conn: psycopg.Connection, root_folder_id: str, token: str) -> None:
    conn.execute(
        """
        insert into public.sync_state (id, root_folder_id, drive_start_page_token, last_sync_at)
        values (1, %s, %s, now())
        on conflict (id) do update
        set root_folder_id = excluded.root_folder_id,
            drive_start_page_token = excluded.drive_start_page_token,
            last_sync_at = now()
        """,
        (root_folder_id, token),
    )


def get_registry_map(conn: psycopg.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        select file_id, file_name, folder_path, mime_type, size_bytes, md5_checksum,
               modified_time, web_view_link, sync_status
        from public.files_registry
        """
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for r in rows:
        result[r[0]] = {
            "file_id": r[0],
            "file_name": r[1],
            "folder_path": r[2],
            "mime_type": r[3],
            "size_bytes": r[4],
            "md5_checksum": r[5],
            "modified_time": r[6],
            "web_view_link": r[7],
            "sync_status": r[8],
        }
    return result


def upsert_file_registry(
    conn: psycopg.Connection,
    rec: DriveFileRecord,
    status: str,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        insert into public.files_registry (
            file_id, file_name, folder_path, mime_type, size_bytes,
            md5_checksum, modified_time, web_view_link, sync_status,
            error_message, last_indexed_at, updated_at
        )
        values (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, case when %s = 'indexed' then now() else null end, now()
        )
        on conflict (file_id) do update
        set file_name = excluded.file_name,
            folder_path = excluded.folder_path,
            mime_type = excluded.mime_type,
            size_bytes = excluded.size_bytes,
            md5_checksum = excluded.md5_checksum,
            modified_time = excluded.modified_time,
            web_view_link = excluded.web_view_link,
            sync_status = excluded.sync_status,
            error_message = excluded.error_message,
            last_indexed_at = case
                when excluded.sync_status = 'indexed' then now()
                else public.files_registry.last_indexed_at
            end,
            updated_at = now()
        """,
        (
            rec.file_id,
            rec.file_name,
            rec.folder_path,
            rec.mime_type,
            rec.size_bytes,
            rec.md5_checksum,
            rec.modified_time,
            rec.web_view_link,
            status,
            error_message,
            status,
        ),
    )


def update_file_metadata_only(conn: psycopg.Connection, rec: DriveFileRecord) -> None:
    """Update file_name/folder_path/link without re-embedding unchanged content."""
    conn.execute(
        """
        update public.files_registry
        set file_name = %s,
            folder_path = %s,
            web_view_link = %s,
            modified_time = %s,
            updated_at = now()
        where file_id = %s
        """,
        (rec.file_name, rec.folder_path, rec.web_view_link, rec.modified_time, rec.file_id),
    )
    meta_patch = json.dumps(
        {
            "file_name": rec.file_name,
            "folder_path": rec.folder_path,
            "web_view_link": rec.web_view_link,
            "modified_time": rec.modified_time,
        }
    )
    conn.execute(
        """
        update public.document_chunks
        set metadata = metadata || %s::jsonb
        where file_id = %s
        """,
        (meta_patch, rec.file_id),
    )
    conn.execute(
        """
        update public.spreadsheet_catalog
        set file_name = %s,
            web_view_link = %s,
            updated_at = now()
        where file_id = %s
        """,
        (rec.file_name, rec.web_view_link, rec.file_id),
    )


def _split_schema_table(full_name: str) -> tuple[str, str]:
    if "." in full_name:
        schema, table = full_name.split(".", 1)
        return schema, table
    return "excel_data", full_name


def purge_file_artifacts(conn: psycopg.Connection, file_id: str) -> list[str]:
    """Delete all document chunks, spreadsheet catalog entries, and excel_data tables for file_id."""
    rows = conn.execute(
        "select sql_table_name from public.spreadsheet_catalog where file_id = %s",
        (file_id,),
    ).fetchall()
    dropped: list[str] = []
    for (full_name,) in rows:
        schema, table = _split_schema_table(full_name)
        conn.execute(
            sql.SQL("drop table if exists {}.{}").format(
                sql.Identifier(schema), sql.Identifier(table)
            )
        )
        dropped.append(full_name)

    conn.execute("delete from public.document_chunks where file_id = %s", (file_id,))
    conn.execute("delete from public.spreadsheet_catalog where file_id = %s", (file_id,))
    return dropped


def mark_file_deleted(conn: psycopg.Connection, file_id: str) -> list[str]:
    dropped = purge_file_artifacts(conn, file_id)
    conn.execute(
        """
        update public.files_registry
        set sync_status = 'deleted',
            error_message = null,
            updated_at = now()
        where file_id = %s
        """,
        (file_id,),
    )
    return dropped


def insert_document_chunks(
    conn: psycopg.Connection,
    rec: DriveFileRecord,
    chunks: list[DocumentChunk],
    embeddings: list[list[float]],
) -> int:
    """Insert embedded document chunks into public.document_chunks."""
    if not chunks:
        return 0

    meta_json = json.dumps(
        {
            "file_name": rec.file_name,
            "folder_path": rec.folder_path,
            "web_view_link": rec.web_view_link,
            "modified_time": rec.modified_time,
        }
    )

    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into public.document_chunks (
                file_id, chunk_index, page_number, section_heading,
                content, embedding, metadata
            )
            values (%s, %s, %s, %s, %s, %s::halfvec, %s::jsonb)
            """,
            [
                (
                    rec.file_id,
                    chunk.chunk_index,
                    chunk.page_number,
                    chunk.section_heading,
                    chunk.content,
                    HalfVector(emb),
                    meta_json,
                )
                for chunk, emb in zip(chunks, embeddings)
            ],
        )
    return len(chunks)


def _clean_copy_value(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    return val


def load_excel_sheet(
    conn: psycopg.Connection,
    sheet: ParsedSheet,
    rec: DriveFileRecord,
    embedding: list[float],
) -> None:
    """Create excel_data table, bulk-load rows with COPY, and register in spreadsheet_catalog."""
    schema, table = _split_schema_table(sheet.sql_table_name)
    table_ident = sql.Identifier(schema, table)

    col_defs = sql.SQL(", ").join(
        sql.SQL("{} {}").format(sql.Identifier(col_name), sql.SQL(pg_type))
        for col_name, pg_type in sheet.pg_types
    )

    conn.execute(sql.SQL("drop table if exists {}").format(table_ident))
    conn.execute(sql.SQL("create table {} ({})").format(table_ident, col_defs))
    conn.execute(sql.SQL("grant usage on schema {} to authenticated, service_role").format(sql.Identifier(schema)))
    conn.execute(sql.SQL("grant select on {} to authenticated, service_role").format(table_ident))

    col_idents = sql.SQL(", ").join(sql.Identifier(col_name) for col_name, _ in sheet.pg_types)
    copy_stmt = sql.SQL("copy {} ({}) from stdin").format(table_ident, col_idents)

    with conn.cursor() as cur:
        with cur.copy(copy_stmt) as copy:
            for row in sheet.dataframe.itertuples(index=False, name=None):
                copy.write_row(tuple(_clean_copy_value(v) for v in row))

    conn.execute(
        """
        insert into public.spreadsheet_catalog (
            file_id, file_name, sheet_name, sql_table_name,
            row_count, columns_json, sample_rows_json,
            summary_text, embedding, web_view_link, updated_at
        )
        values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::halfvec, %s, now())
        """,
        (
            rec.file_id,
            rec.file_name,
            sheet.sheet_name,
            sheet.sql_table_name,
            sheet.row_count,
            json.dumps(sheet.columns_meta),
            json.dumps(sheet.sample_rows),
            sheet.summary_text,
            HalfVector(embedding),
            rec.web_view_link,
        ),
    )
