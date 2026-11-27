-- 001_initial_schema.sql
-- Hybrid RAG + Structured Excel SQL schema for Supabase PostgreSQL
-- Uses pgvector halfvec(768) (requires pgvector >= 0.7.0, standard on Supabase)
-- to cut vector and HNSW index storage by ~50% with negligible recall impact.

create extension if not exists pgcrypto;
create extension if not exists vector;

-- Isolated schema for per-sheet Excel SQL tables (excel_data.xl_<short_id>_<sheet>)
create schema if not exists excel_data;

-- ============================================================================
-- 1. Sync Checkpoints
-- ============================================================================
create table if not exists public.sync_state (
    id int primary key default 1 check (id = 1),
    root_folder_id text not null,
    drive_start_page_token text,
    last_sync_at timestamptz
);

-- ============================================================================
-- 2. Authoritative Google Drive File Inventory
-- ============================================================================
create table if not exists public.files_registry (
    file_id text primary key,
    file_name text not null,
    folder_path text not null default '/',
    mime_type text not null,
    size_bytes bigint default 0,
    md5_checksum text,
    modified_time timestamptz,
    web_view_link text,
    sync_status text not null default 'pending'
        check (sync_status in ('pending', 'indexing', 'indexed', 'failed', 'deleted')),
    error_message text,
    last_indexed_at timestamptz,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists idx_files_registry_sync_status on public.files_registry (sync_status);
create index if not exists idx_files_registry_folder_path on public.files_registry (folder_path);
create index if not exists idx_files_registry_mime_type on public.files_registry (mime_type);

-- ============================================================================
-- 3. Unstructured Document Chunks (PDF / Word / Google Docs)
-- ============================================================================
create table if not exists public.document_chunks (
    chunk_id uuid primary key default gen_random_uuid(),
    file_id text not null references public.files_registry (file_id) on delete cascade,
    chunk_index int not null,
    page_number int,
    section_heading text,
    content text not null,
    embedding halfvec(768),
    tsv tsvector generated always as (
        to_tsvector('english', coalesce(section_heading, '') || ' ' || content)
    ) stored,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz default now()
);

create index if not exists idx_document_chunks_file_id
    on public.document_chunks (file_id);
create index if not exists idx_document_chunks_tsv
    on public.document_chunks using gin (tsv);
create index if not exists idx_document_chunks_embedding_hnsw
    on public.document_chunks using hnsw (embedding halfvec_cosine_ops);

-- ============================================================================
-- 4. Spreadsheet Catalog (Excel / CSV / Google Sheets)
-- ============================================================================
create table if not exists public.spreadsheet_catalog (
    catalog_id uuid primary key default gen_random_uuid(),
    file_id text not null references public.files_registry (file_id) on delete cascade,
    file_name text not null,
    sheet_name text not null,
    sql_table_name text not null unique,
    row_count int not null default 0,
    columns_json jsonb not null default '[]'::jsonb,
    sample_rows_json jsonb not null default '[]'::jsonb,
    summary_text text not null default '',
    embedding halfvec(768),
    tsv tsvector generated always as (
        to_tsvector(
            'english',
            coalesce(file_name, '') || ' ' || coalesce(sheet_name, '') || ' ' || coalesce(summary_text, '')
        )
    ) stored,
    web_view_link text,
    updated_at timestamptz default now()
);

create index if not exists idx_spreadsheet_catalog_file_id
    on public.spreadsheet_catalog (file_id);
create index if not exists idx_spreadsheet_catalog_tsv
    on public.spreadsheet_catalog using gin (tsv);
create index if not exists idx_spreadsheet_catalog_embedding_hnsw
    on public.spreadsheet_catalog using hnsw (embedding halfvec_cosine_ops);

-- ============================================================================
-- 5. Hybrid Document Search RPC (pgvector + tsvector Reciprocal Rank Fusion)
-- ============================================================================
create or replace function public.hybrid_search_documents(
    query_text text,
    query_embedding halfvec(768),
    match_count int default 6,
    folder_filter text default null,
    file_type_filter text default null,
    rrf_k int default 60
)
returns table (
    chunk_id uuid,
    file_id text,
    file_name text,
    folder_path text,
    page_number int,
    section_heading text,
    content text,
    web_view_link text,
    score double precision
)
language sql
stable
as $$
with filtered as (
    select
        dc.chunk_id,
        dc.file_id,
        fr.file_name,
        fr.folder_path,
        dc.page_number,
        dc.section_heading,
        dc.content,
        fr.web_view_link,
        dc.embedding,
        dc.tsv
    from public.document_chunks dc
    join public.files_registry fr on fr.file_id = dc.file_id
    where fr.sync_status = 'indexed'
      and (folder_filter is null or fr.folder_path ilike folder_filter || '%')
      and (
          file_type_filter is null
          or fr.mime_type ilike '%' || file_type_filter || '%'
          or fr.file_name ilike '%.' || file_type_filter
      )
),
semantic as (
    select
        f.chunk_id,
        row_number() over (order by f.embedding <=> query_embedding) as rank_ix
    from filtered f
    where query_embedding is not null and f.embedding is not null
    order by f.embedding <=> query_embedding
    limit greatest(match_count * 5, 30)
),
keyword as (
    select
        f.chunk_id,
        row_number() over (
            order by ts_rank_cd(f.tsv, websearch_to_tsquery('english', query_text)) desc
        ) as rank_ix
    from filtered f
    where nullif(trim(query_text), '') is not null
      and f.tsv @@ websearch_to_tsquery('english', query_text)
    order by ts_rank_cd(f.tsv, websearch_to_tsquery('english', query_text)) desc
    limit greatest(match_count * 5, 30)
),
combined as (
    select
        coalesce(s.chunk_id, k.chunk_id) as chunk_id,
        coalesce(1.0::double precision / (rrf_k + s.rank_ix), 0.0)
            + coalesce(1.0::double precision / (rrf_k + k.rank_ix), 0.0) as rrf_score
    from semantic s
    full outer join keyword k on s.chunk_id = k.chunk_id
)
select
    f.chunk_id,
    f.file_id,
    f.file_name,
    f.folder_path,
    f.page_number,
    f.section_heading,
    f.content,
    f.web_view_link,
    c.rrf_score as score
from combined c
join filtered f on f.chunk_id = c.chunk_id
order by c.rrf_score desc
limit greatest(match_count, 1);
$$;

-- ============================================================================
-- 6. Spreadsheet Catalog Search RPC
-- ============================================================================
create or replace function public.search_spreadsheets(
    query_text text,
    query_embedding halfvec(768),
    match_count int default 8
)
returns table (
    catalog_id uuid,
    file_id text,
    file_name text,
    sheet_name text,
    sql_table_name text,
    row_count int,
    columns_json jsonb,
    summary_text text,
    web_view_link text,
    score double precision
)
language sql
stable
as $$
with q as (
    select
        case
            when nullif(plainto_tsquery('english', coalesce(query_text, ''))::text, '') is not null
                then replace(plainto_tsquery('english', query_text)::text, ' & ', ' | ')::tsquery
            else null::tsquery
        end as tsq
),
filtered as (
    select
        sc.catalog_id,
        sc.file_id,
        sc.file_name,
        sc.sheet_name,
        sc.sql_table_name,
        sc.row_count,
        sc.columns_json,
        sc.summary_text,
        sc.web_view_link,
        sc.embedding,
        sc.tsv
    from public.spreadsheet_catalog sc
    join public.files_registry fr on fr.file_id = sc.file_id
    where fr.sync_status = 'indexed'
),
semantic as (
    select
        f.catalog_id,
        row_number() over (order by f.embedding <=> query_embedding) as rank_ix
    from filtered f
    where query_embedding is not null and f.embedding is not null
    order by f.embedding <=> query_embedding
    limit greatest(match_count * 4, 25)
),
keyword as (
    select
        f.catalog_id,
        row_number() over (order by ts_rank_cd(f.tsv, q.tsq) desc) as rank_ix
    from filtered f
    cross join q
    where q.tsq is not null and f.tsv @@ q.tsq
    order by ts_rank_cd(f.tsv, q.tsq) desc
    limit greatest(match_count * 4, 25)
),
combined as (
    select
        coalesce(s.catalog_id, k.catalog_id) as catalog_id,
        coalesce(1.0::double precision / (60 + s.rank_ix), 0.0)
            + coalesce(1.0::double precision / (60 + k.rank_ix), 0.0) as rrf_score
    from semantic s
    full outer join keyword k on s.catalog_id = k.catalog_id
)
select
    f.catalog_id,
    f.file_id,
    f.file_name,
    f.sheet_name,
    f.sql_table_name,
    f.row_count,
    f.columns_json,
    f.summary_text,
    f.web_view_link,
    c.rrf_score as score
from combined c
join filtered f on f.catalog_id = c.catalog_id
order by c.rrf_score desc
limit greatest(match_count, 1);
$$;

-- ============================================================================
-- 7. Read-Only Excel SQL Execution RPC
-- ============================================================================
create or replace function public.run_readonly_excel_sql(
    query_text text,
    max_rows int default 200
)
returns jsonb
language plpgsql
security definer
set search_path = excel_data, pg_temp
as $$
declare
    cleaned_sql text;
    clamped_rows int;
    result_json jsonb;
begin
    if query_text is null then
        raise exception 'Query cannot be null';
    end if;

    -- Trim whitespace and optional single trailing semicolon
    cleaned_sql := regexp_replace(trim(query_text), ';\s*$', '');

    if cleaned_sql = '' then
        raise exception 'Query cannot be empty';
    end if;

    -- Disallow multi-statement queries
    if cleaned_sql ~ ';' then
        raise exception 'Multiple SQL statements are not allowed';
    end if;

    -- Require SELECT or WITH CTE
    if cleaned_sql !~* '^(select|with)\y' then
        raise exception 'Only SELECT or WITH queries are permitted';
    end if;

    -- Block DDL, DML, and administrative statements
    if cleaned_sql ~* '\y(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|execute|call|do|set|reset|vacuum|analyze|lock)\y' then
        raise exception 'Disallowed SQL keyword detected';
    end if;

    -- Block access to system catalogs or non-excel schemas
    if cleaned_sql ~* '\y(pg_catalog|pg_tables|pg_roles|pg_stat|information_schema|auth\.|storage\.|public\.)' then
        raise exception 'Access to system or non-excel_data schemas is forbidden';
    end if;

    -- Require explicit excel_data schema qualification
    if cleaned_sql !~* '\yexcel_data\.' then
        raise exception 'Queries must target tables in the excel_data schema';
    end if;

    clamped_rows := least(greatest(coalesce(max_rows, 200), 1), 200);

    set local statement_timeout = '8000';

    execute format(
        'select coalesce(jsonb_agg(row_to_json(t)), ''[]''::jsonb) from (select * from (%s) as _sub limit %s) as t',
        cleaned_sql,
        clamped_rows
    )
    into result_json;

    return result_json;
end;
$$;

-- ============================================================================
-- 8. Row-Level Security & Permissions
-- ============================================================================
alter table public.sync_state enable row level security;
alter table public.files_registry enable row level security;
alter table public.document_chunks enable row level security;
alter table public.spreadsheet_catalog enable row level security;

revoke all on function public.run_readonly_excel_sql(text, int) from public, anon;
grant execute on function public.run_readonly_excel_sql(text, int) to authenticated, service_role;

grant usage on schema public to service_role, authenticated;
grant usage on schema excel_data to service_role, authenticated;

grant select, insert, update, delete on
    public.sync_state,
    public.files_registry,
    public.document_chunks,
    public.spreadsheet_catalog
to service_role;

grant select on all tables in schema excel_data to service_role, authenticated;
alter default privileges in schema excel_data
    grant select on tables to service_role, authenticated;

grant execute on function public.hybrid_search_documents(text, halfvec(768), int, text, text, int)
    to service_role, authenticated;
grant execute on function public.search_spreadsheets(text, halfvec(768), int)
    to service_role, authenticated;
