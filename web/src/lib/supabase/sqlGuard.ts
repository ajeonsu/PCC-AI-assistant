const FORBIDDEN_KEYWORDS =
  /\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|execute|call|do|set|reset|vacuum|analyze|lock)\b/i;

const FORBIDDEN_SCHEMAS =
  /\b(pg_catalog|pg_tables|pg_roles|pg_stat|information_schema|auth\.|storage\.|public\.)/i;

export function validateReadOnlyExcelSql(rawSql: string): string {
  const cleaned = rawSql.trim().replace(/;\s*$/, "").trim();
  if (!cleaned) {
    throw new Error("SQL query cannot be empty.");
  }

  if (cleaned.includes(";")) {
    throw new Error("Multiple SQL statements are not allowed.");
  }

  if (!/^(select|with)\b/i.test(cleaned)) {
    throw new Error("Only SELECT or WITH queries are permitted.");
  }

  if (FORBIDDEN_KEYWORDS.test(cleaned)) {
    throw new Error("Disallowed SQL keyword detected.");
  }

  if (FORBIDDEN_SCHEMAS.test(cleaned)) {
    throw new Error("Access to system or non-excel_data schemas is forbidden.");
  }

  if (!/\bexcel_data\./i.test(cleaned)) {
    throw new Error("Queries must explicitly target tables in the excel_data schema.");
  }

  return cleaned;
}
