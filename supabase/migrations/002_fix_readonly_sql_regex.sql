-- 002_fix_readonly_sql_regex.sql
-- Fix PostgreSQL POSIX regex word boundaries (\y instead of \b, which means backspace in Postgres).

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

    cleaned_sql := regexp_replace(trim(query_text), ';\s*$', '');

    if cleaned_sql = '' then
        raise exception 'Query cannot be empty';
    end if;

    if cleaned_sql ~ ';' then
        raise exception 'Multiple SQL statements are not allowed';
    end if;

    if cleaned_sql !~* '^(select|with)\y' then
        raise exception 'Only SELECT or WITH queries are permitted';
    end if;

    if cleaned_sql ~* '\y(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|execute|call|do|set|reset|vacuum|analyze|lock)\y' then
        raise exception 'Disallowed SQL keyword detected';
    end if;

    if cleaned_sql ~* '\y(pg_catalog|pg_tables|pg_roles|pg_stat|information_schema|auth\.|storage\.|public\.)' then
        raise exception 'Access to system or non-excel_data schemas is forbidden';
    end if;

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

revoke all on function public.run_readonly_excel_sql(text, int) from public, anon;
grant execute on function public.run_readonly_excel_sql(text, int) to authenticated, service_role;
