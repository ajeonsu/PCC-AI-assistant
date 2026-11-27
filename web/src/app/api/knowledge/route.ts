import { createAdminClient } from "@/lib/supabase/admin";
import { getAuthorizedUser } from "@/lib/supabase/server";

export async function GET() {
  const user = await getAuthorizedUser();
  if (!user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const supabase = createAdminClient();

  const [filesRes, chunksCountRes, catalogCountRes, syncStateRes] =
    await Promise.all([
      supabase
        .from("files_registry")
        .select(
          "file_id, file_name, folder_path, mime_type, size_bytes, modified_time, web_view_link, sync_status",
        )
        .eq("sync_status", "indexed")
        .order("file_name", { ascending: true })
        .limit(100),
      supabase
        .from("document_chunks")
        .select("chunk_id", { count: "exact", head: true }),
      supabase
        .from("spreadsheet_catalog")
        .select("catalog_id", { count: "exact", head: true }),
      supabase
        .from("sync_state")
        .select("last_sync_at")
        .eq("id", 1)
        .maybeSingle(),
    ]);

  if (filesRes.error) {
    return Response.json({ error: filesRes.error.message }, { status: 500 });
  }

  const files = filesRes.data ?? [];
  return Response.json({
    totalFiles: files.length,
    totalChunks: chunksCountRes.count ?? 0,
    totalExcelTables: catalogCountRes.count ?? 0,
    lastSyncAt: syncStateRes.data?.last_sync_at ?? null,
    files,
  });
}
