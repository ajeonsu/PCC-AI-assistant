# Web Frontend & Chat Agent (`web/`)

Next.js (App Router, TypeScript) + Vercel AI SDK + single-user Supabase Auth chat application.

## Local Development

1. Ensure `web/.env.local` has:
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `GROQ_API_KEY`
   - `GEMINI_API_KEY`
   - `ALLOWED_USER_EMAIL`
2. Run:
   ```powershell
   pnpm --dir web install
   pnpm --dir web dev
   ```
   Open `http://localhost:3000`.

## Deploying to Vercel (Hobby / Free)

1. Import `https://github.com/ajeonsu/PCC-AI-assistant` into Vercel.
2. Set **Root Directory** to `web`.
3. Add the same 6 environment variables above in Vercel **Project Settings → Environment Variables**.
4. Deploy!
