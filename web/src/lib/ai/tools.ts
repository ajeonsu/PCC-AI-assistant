import { tool } from "ai";
import { z } from "zod";
import { embedQuery } from "@/lib/ai/embeddings";
import { createAdminClient } from "@/lib/supabase/admin";
import { validateReadOnlyExcelSql } from "@/lib/supabase/sqlGuard";

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
          .max(10)
          .default(6)
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
        return { results: data ?? [] };
      },
    }),

    searchSpreadsheets: tool({
      description:
        "Search the Excel spreadsheet catalog by file name, worksheet name, column names, or topic to find candidate SQL tables.",
      parameters: z.object({
        query: z.string().describe("Topic, file name, sheet name, or column keywords"),
        topK: z.number().int().min(1).max(10).default(6),
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
        return { spreadsheets: data ?? [] };
      },
    }),

    describeSpreadsheet: tool({
      description:
        "Inspect exact SQL column names, PostgreSQL data types, original Excel headers, row count, and 5 sample rows for a spreadsheet table before writing SQL.",
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
        return data;
      },
    }),

    querySpreadsheetSql: tool({
      description:
        "Execute a read-only PostgreSQL SELECT (or WITH) query against an excel_data.* spreadsheet table for exact sums, counts, filters, and aggregations.",
      parameters: z.object({
        sqlQuery: z
          .string()
          .describe("Single read-only PostgreSQL SELECT or WITH query targeting excel_data.*"),
        maxRows: z.number().int().min(1).max(200).default(100),
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
