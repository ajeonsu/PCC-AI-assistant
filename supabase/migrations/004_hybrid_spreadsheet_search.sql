-- 004_hybrid_spreadsheet_search.sql
-- Add tsvector GIN index and RRF hybrid search to public.spreadsheet_catalog
-- so multi-word queries match sheet names, filenames, and column headers reliably.

alter table public.spreadsheet_catalog
    add column if not exists tsv tsvector generated always as (
        to_tsvector(
            'english',
            coalesce(file_name, '') || ' ' || coalesce(sheet_name, '') || ' ' || coalesce(summary_text, '')
        )
    ) stored;

create index if not exists idx_spreadsheet_catalog_tsv
    on public.spreadsheet_catalog using gin (tsv);

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

grant execute on function public.search_spreadsheets(text, halfvec(768), int)
    to service_role, authenticated;
