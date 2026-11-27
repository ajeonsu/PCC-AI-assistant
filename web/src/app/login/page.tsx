"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Database, Lock, Mail } from "lucide-react";
import { createBrowserSupabaseClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"password" | "magic">("password");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setNotice(null);

    const supabase = createBrowserSupabaseClient();

    if (mode === "magic") {
      const { error: otpError } = await supabase.auth.signInWithOtp({
        email: email.trim(),
        options: {
          emailRedirectTo: `${window.location.origin}/`,
        },
      });
      setLoading(false);
      if (otpError) {
        setError(otpError.message);
        return;
      }
      setNotice("Check your email for the sign-in magic link.");
      return;
    }

    const { error: signInError } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    });
    setLoading(false);

    if (signInError) {
      setError(signInError.message);
      return;
    }

    router.push("/");
    router.refresh();
  }

  return (
    <main className="min-h-screen flex items-center justify-center p-4 bg-slate-950">
      <div className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900/80 p-7 shadow-2xl">
        <div className="flex items-center gap-3 mb-6">
          <div className="h-10 w-10 rounded-xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
            <Database className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-white">
              PCC AI Assistant
            </h1>
            <p className="text-xs text-slate-400">
              Private Google Drive RAG &amp; Excel SQL Knowledge Base
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">
              Email
            </label>
            <div className="relative">
              <Mail className="w-4 h-4 text-slate-500 absolute left-3 top-3" />
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@gmail.com"
                className="w-full rounded-lg bg-slate-950 border border-slate-800 pl-9 pr-3 py-2 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-emerald-500"
              />
            </div>
          </div>

          {mode === "password" && (
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1.5">
                Password
              </label>
              <div className="relative">
                <Lock className="w-4 h-4 text-slate-500 absolute left-3 top-3" />
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full rounded-lg bg-slate-950 border border-slate-800 pl-9 pr-3 py-2 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-emerald-500"
                />
              </div>
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-red-500/40 bg-red-950/40 px-3 py-2 text-xs text-red-300">
              {error}
            </div>
          )}

          {notice && (
            <div className="rounded-lg border border-emerald-500/40 bg-emerald-950/40 px-3 py-2 text-xs text-emerald-300">
              {notice}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 py-2.5 text-sm font-medium text-white transition"
          >
            {loading
              ? "Signing in..."
              : mode === "password"
                ? "Sign In"
                : "Send Magic Link"}
          </button>
        </form>

        <div className="mt-4 text-center">
          <button
            type="button"
            onClick={() => {
              setMode(mode === "password" ? "magic" : "password");
              setError(null);
              setNotice(null);
            }}
            className="text-xs text-slate-400 hover:text-slate-200 underline underline-offset-2"
          >
            {mode === "password"
              ? "Use email magic link instead"
              : "Use email & password instead"}
          </button>
        </div>
      </div>
    </main>
  );
}
