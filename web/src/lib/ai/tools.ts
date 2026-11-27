import { tool } from "ai";
import { z } from "zod";
import { embedQuery } from "@/lib/ai/embeddings";
import { createAdminClient } from "@/lib/supabase/admin";
import { validateReadOnlyExcelSql } from "@/lib/supabase/sqlGuard";

function truncateText(val: unknown, maxLen: number): unknown {
  if (typeof val === "string" && val.length > maxLen) {
    return `${val.slice(0, maxLen)}…`;
  }
  return val;
}

function formatColumns(colsJson: unknown): string {
  if (!Array.isArray(colsJson)) return "";
  return (colsJson as Array<{ name?: string; pg_type?: string }>)
    .slice(0, 30)
    .map((c) => `${c.name ?? ""}:${c.pg_type ?? "TEXT"}`)
    .join(", ");
}

export async function execSearchDocuments(args: {
  query: string;
  fileType?: string;
}) {
  const supabase = createAdminClient();
  const queryEmbedding = await embedQuery(args.query);
  const { data, error } = await supabase.rpc("hybrid_search_documents", {
    query_text: args.query,
    query_embedding: queryEmbedding,
    match_count: 4,
    folder_filter: null,
    file_type_filter: args.fileType ?? null,
  });

  if (error) {
    return { error: error.message, results: [] };
  }
  const results = ((data ?? []) as Array<Record<string, unknown>>).map(
    (r) => ({
      file: r.file_name,
      page: r.page_number,
      heading: r.section_heading,
      url: r.web_view_link,
      content: truncateText(r.content, 1000),
    }),
  );
  return { results };
}

export async function execSearchSpreadsheets(args: { query: string }) {
  const supabase = createAdminClient();
  const queryEmbedding = await embedQuery(args.query);
  const { data, error } = await supabase.rpc("search_spreadsheets", {
    query_text: args.query,
    query_embedding: queryEmbedding,
    match_count: 15,
  });

  if (error) {
    return { error: error.message, spreadsheets: [], sampleRows: {} };
  }

  const rawRows = (data ?? []) as Array<Record<string, unknown>>;

  // Keep at most 2 sheets per file so multi-sheet workbooks don't crowd out other files.
  const perFileCount = new Map<string, number>();
  const diverseRows: Array<Record<string, unknown>> = [];
  for (const row of rawRows) {
    const fileName = String(row.file_name ?? "");
    const count = perFileCount.get(fileName) ?? 0;
    if (count >= 2) continue;
    perFileCount.set(fileName, count + 1);
    diverseRows.push(row);
    if (diverseRows.length >= 6) break;
  }

  const spreadsheets = diverseRows.map((s) => ({
    file: s.file_name,
    sheet: s.sheet_name,
    table: s.sql_table_name,
    rows: s.row_count,
    url: s.web_view_link,
    cols: formatColumns(s.columns_json),
  }));

  // Fetch 1 truncated sample row for the top 2 distinct tables.
  const sampleRows: Record<string, Record<string, unknown>> = {};
  const topTables = spreadsheets
    .map((s) => String(s.table ?? ""))
    .filter(Boolean)
    .slice(0, 2);

  if (topTables.length > 0) {
    const { data: catRows } = await supabase
      .from("spreadsheet_catalog")
      .select("sql_table_name, sample_rows_json")
      .in("sql_table_name", topTables);

    for (const cat of catRows ?? []) {
      const firstRow = Array.isArray(cat.sample_rows_json)
        ? (cat.sample_rows_json[0] as Record<string, unknown> | undefined)
        : undefined;
      if (firstRow && cat.sql_table_name) {
        sampleRows[cat.sql_table_name] = Object.fromEntries(
          Object.entries(firstRow).map(([k, v]) => [k, truncateText(v, 50)]),
        );
      }
    }
  }

  return { spreadsheets, sampleRows };
}

export async function execQuerySpreadsheetSql(args: { sqlQuery: string }) {
  let cleanedSql: string;
  try {
    cleanedSql = validateReadOnlyExcelSql(args.sqlQuery);
  } catch (err) {
    return {
      error: err instanceof Error ? err.message : "Invalid SQL query",
      rows: [],
    };
  }

  const supabase = createAdminClient();
  const { data, error } = await supabase.rpc("run_readonly_excel_sql", {
    query_text: cleanedSql,
    max_rows: 50,
  });

  if (error) {
    return {
      error: error.message,
      sql: cleanedSql,
      rows: [],
    };
  }

  const rows = Array.isArray(data) ? data : [];
  return {
    sql: cleanedSql,
    count: rows.length,
    rows,
  };
}

export async function execListDriveFiles(args: { nameQuery?: string }) {
  const supabase = createAdminClient();
  let queryBuilder = supabase
    .from("files_registry")
    .select("file_name, size_bytes, modified_time, web_view_link")
    .eq("sync_status", "indexed")
    .order("modified_time", { ascending: false })
    .limit(25);

  if (args.nameQuery?.trim()) {
    queryBuilder = queryBuilder.ilike(
      "file_name",
      `%${args.nameQuery.trim()}%`,
    );
  }

  const { data, error } = await queryBuilder;
  if (error) {
    return { error: error.message, files: [] };
  }
  const files = (data ?? []).map((f) => ({
    file: f.file_name,
    size_kb: Math.round((f.size_bytes ?? 0) / 1024),
    modified: f.modified_time,
    url: f.web_view_link,
  }));
  return { files };
}

export function createAssistantTools() {
  return {
    searchDocuments: tool({
      description: "Search indexed PDFs, scanned OCR pages, and Word docs.",
      parameters: z.object({
        query: z.string(),
        fileType: z.string().optional(),
      }),
      execute: execSearchDocuments,
    }),

    searchSpreadsheets: tool({
      description:
        "Find Excel sheets, SQL table names, columns, and sample rows by topic or filename.",
      parameters: z.object({
        query: z.string(),
      }),
      execute: execSearchSpreadsheets,
    }),

    querySpreadsheetSql: tool({
      description:
        "Run read-only PostgreSQL SELECT/WITH query on excel_data.* tables.",
      parameters: z.object({
        sqlQuery: z.string(),
      }),
      execute: execQuerySpreadsheetSql,
    }),

    listDriveFiles: tool({
      description: "List indexed Google Drive files, sizes, and links.",
      parameters: z.object({
        nameQuery: z.string().optional(),
      }),
      execute: execListDriveFiles,
    }),
  };
}
