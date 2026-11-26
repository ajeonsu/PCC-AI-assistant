# PCC AI Assistant — Google Drive Hybrid RAG + Excel SQL

A single-user AI knowledge assistant connected to a personal Google Drive folder (PDFs, Word documents, and 5,000+ row Excel spreadsheets), built as a single monorepo:

- **`web/`** — Next.js (TypeScript) + Vercel AI SDK + Supabase Auth chat UI (deploy to Vercel Hobby with **Root Directory = `web`**)
- **`worker/`** — Python (`uv`) worker for personal Google Drive OAuth2 sync (`changes.list`), PDF/Word parsing, and Excel-to-SQL loading
- **`supabase/`** — PostgreSQL migrations (`pgvector`, `tsvector` hybrid RRF search, `excel_data` schema, and read-only SQL RPC)

See [rag_system_plan.md](./rag_system_plan.md) for the full architecture and phased roadmap.

---

## Phase 0 Quickstart: Audit Your Google Drive Folder

1. **Create Google Cloud OAuth2 Credentials**
   - In Google Cloud Console, create a project and enable **Google Drive API**.
   - Configure the **OAuth Consent Screen** (External), add your Google email as a test user—or set publishing status to **In Production** (private to your account) so refresh tokens do not expire after 7 days.
   - Create **OAuth client ID → Desktop app**, download the JSON, and save it to `worker/credentials.json`.

2. **Configure `.env`**
   - Copy `.env.example` to `.env` in the repo root and set `GOOGLE_DRIVE_ROOT_FOLDER_ID` to your target Google Drive folder ID (from the folder URL).

3. **Run the Phase 0 Drive Audit (Read-Only)**
   ```powershell
   uv sync --directory worker
   uv run --directory worker python scripts/audit_drive.py --sample-pdfs 10 --export-json audit_report.json
   ```
   The first run opens your browser to authorize `drive.readonly` access and saves `worker/token.json`. It then prints a breakdown of file types, total sizes, largest files, and an estimated scanned-PDF ratio.

---

## Phase 1 Quickstart: Apply Supabase Schema

1. Create a free project in Supabase.
2. Open the **SQL Editor** in Supabase and run `supabase/migrations/001_initial_schema.sql`.
3. Add your `DATABASE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `GROQ_API_KEY`, and `GEMINI_API_KEY` to `.env`.
