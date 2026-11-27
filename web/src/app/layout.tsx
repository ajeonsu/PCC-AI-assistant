import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PCC AI Assistant — Google Drive Knowledge Base",
  description:
    "Hybrid RAG and Excel SQL Assistant connected to Google Drive and Supabase",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="bg-slate-950 text-slate-100 antialiased">{children}</body>
    </html>
  );
}
