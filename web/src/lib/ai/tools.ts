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

export function createAssistantTools() {
  const supabase = createAdminClient();

  return {
    searchDocuments: tool({
      description:
        "Hybrid semantic + keyword search across indexed PDFs, scanned OCR pages, and Word documents.",
      parameters: z.object({
        query: z.string().describe("Natural-language search query or keywords"),
        folderFilter: z
          .string()
          .optional()
          .describe("Optional folder path prefix filter"),
        fileType: z
          .string()
          .optional()
          .describe("Optional file extension or type filter (e.g., pdf, docx)"),
        topK: z
          .number()
          .int()
          .min(1)
          .max(8)
          .default(4)
          .describe("Maximum number of document chunks to return"),
      }),
      execute: async ({ query, folderFilter, fileType, topK }) => {
        const queryEmbedding = await embedQuery(query);
        const { data, error } = await supabase.rpc("hybrid_search_documents", {
          query_text: query,
          query_embedding: queryEmbedding,
          match_count: topK,
          folder_filter: folderFilter ?? null,
          file_type_filter: fileType ?? null,
        });

        if (error) {
          return { error: error.message, results: [] };
        }
        const results = (
          (data ?? []) as Array<Record<string, unknown>>
        ).map((row) => ({
          ...row,
          content: truncateText(row.content, 1400),
        }));
        return { results };
      },
    }),

    searchSpreadsheets: tool({
      description:
        "Search the Excel spreadsheet catalog by file name, worksheet name, column names, or topic to find candidate SQL tables.",
      parameters: z.object({
        query: z.string().describe("Topic, file name, sheet name, or column keywords"),
        topK: z.number().int().min(1).max(8).default(4),
      }),
      execute: async ({ query, topK }) => {
        const queryEmbedding = await embedQuery(query);
        const { data, error } = await supabase.rpc("search_spreadsheets", {
          query_text: query,
          query_embedding: queryEmbedding,
          match_count: topK,
        });

        if (error) {
          return { error: error.message, spreadsheets: [] };
        }
        const spreadsheets = (
          (data ?? []) as Array<Record<string, unknown>>
        ).map((s) => ({
          file_name: s.file_name,
          sheet_name: s.sheet_name,
          sql_table_name: s.sql_table_name,
          row_count: s.row_count,
          web_view_link: s.web_view_link,
        }));
        return { spreadsheets };
      },
    }),

    describeSpreadsheet: tool({
      description:
        "Inspect exact SQL column names, PostgreSQL data types, original Excel headers, row count, and sample rows for a spreadsheet table before writing SQL.",
      parameters: z.object({
        sqlTableName: z
          .string()
          .describe("Exact sql_table_name from searchSpreadsheets, e.g. excel_data.xl_..."),
      }),
      execute: async ({ sqlTableName }) => {
        const normalized = sqlTableName.startsWith("excel_data.")
          ? sqlTableName
          : `excel_data.${sqlTableName}`;

        const { data, error } = await supabase
          .from("spreadsheet_catalog")
          .select(
            "file_name, sheet_name, sql_table_name, row_count, columns_json, sample_rows_json, web_view_link",
          )
          .eq("sql_table_name", normalized)
          .maybeSingle();

        if (error) {
          return { error: error.message };
        }
        if (!data) {
          return { error: `Spreadsheet table '${normalized}' not found in catalog.` };
        }

        const cols = Array.isArray(data.columns_json)
          ? (
              data.columns_json as Array<{
                name?: string;
                pg_type?: string;
                original_header?: string;
              }>
            ).map((c) => {
              const name = c.name ?? "";
              const pgType = c.pg_type ?? "TEXT";
              const orig = c.original_header ?? "";
              const normOrig = orig.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
              return orig && normOrig !== name
                ? `${name} (${pgType}; header="${orig}")`
                : `${name} (${pgType})`;
            })
          : [];

        const sampleRows = Array.isArray(data.sample_rows_json)
          ? (data.sample_rows_json as Array<Record<string, unknown>>)
              .slice(0, 2)
              .map((row) =>
                Object.fromEntries(
                  Object.entries(row).map(([k, v]) => [k, truncateText(v, 80)]),
                ),
              )
          : [];

        return {
          file_name: data.file_name,
          sheet_name: data.sheet_name,
          sql_table_name: data.sql_table_name,
          row_count: data.row_count,
          columns: cols,
          sample_rows: sampleRows,
          web_view_link: data.web_view_link,
        };
      },
    }),

    querySpreadsheetSql: tool({
      description:
        "Execute a read-only PostgreSQL SELECT (or WITH) query against an excel_data.* spreadsheet table for exact sums, counts, filters, and aggregations.",
      parameters: z.object({
        sqlQuery: z
          .string()
          .describe("Single read-only PostgreSQL SELECT or WITH query targeting excel_data.*"),
        maxRows: z.number().int().min(1).max(200).default(50),
      }),
      execute: async ({ sqlQuery, maxRows }) => {
        let cleanedSql: string;
        try {
          cleanedSql = validateReadOnlyExcelSql(sqlQuery);
        } catch (err) {
          return {
            error: err instanceof Error ? err.message : "Invalid SQL query",
            rows: [],
          };
        }

        const { data, error } = await supabase.rpc("run_readonly_excel_sql", {
          query_text: cleanedSql,
          max_rows: maxRows,
        });

        if (error) {
          return {
            error: error.message,
            executedSql: cleanedSql,
            rows: [],
          };
        }

        const rows = Array.isArray(data) ? data : [];
        return {
          executedSql: cleanedSql,
          rowCountReturned: rows.length,
          rows,
        };
      },
    }),

    listDriveFiles: tool({
      description:
        "List indexed Google Drive files from files_registry (filenames, folders, sizes, modified times, and Drive links).",
      parameters: z.object({
        nameQuery: z.string().optional().describe("Optional substring to match in file_name"),
        fileType: z
          .string()
          .optional()
          .describe("Optional filter such as pdf, docx, xlsx, csv"),
        limit: z.number().int().min(1).max(50).default(30),
      }),
      execute: async ({ nameQuery, fileType, limit }) => {
        let queryBuilder = supabase
          .from("files_registry")
          .select(
            "file_id, file_name, folder_path, mime_type, size_bytes, modified_time, web_view_link, sync_status",
          )
          .eq("sync_status", "indexed")
          .order("modified_time", { ascending: false })
          .limit(limit);

        if (nameQuery?.trim()) {
          queryBuilder = queryBuilder.ilike("file_name", `%${nameQuery.trim()}%`);
        }
        if (fileType?.trim()) {
          queryBuilder = queryBuilder.ilike("file_name", `%.${fileType.trim()}`);
        }

        const { data, error } = await queryBuilder;
        if (error) {
          return { error: error.message, files: [] };
        }
        return { files: data ?? [] };
      },
    }),
  };
}
