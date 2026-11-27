-- 003_grant_service_role_permissions.sql
-- Grant explicit PostgREST table/schema/RPC privileges to service_role (and read/execute to authenticated).

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
grant execute on function public.run_readonly_excel_sql(text, int)
    to service_role, authenticated;
