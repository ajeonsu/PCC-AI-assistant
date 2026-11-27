const EMBEDDING_MODEL = "models/gemini-embedding-001";

function l2Normalize(values: number[]): number[] {
  let normSq = 0;
  for (const v of values) normSq += v * v;
  const norm = Math.sqrt(normSq);
  if (norm <= 0) return values;
  return values.map((v) => v / norm);
}

export async function embedQuery(text: string): Promise<string> {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    throw new Error("GEMINI_API_KEY is not configured.");
  }

  const dim = Number(process.env.EMBEDDING_DIM || "768");
  const url = `https://generativelanguage.googleapis.com/v1beta/${EMBEDDING_MODEL}:embedContent?key=${encodeURIComponent(apiKey)}`;

  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: EMBEDDING_MODEL,
      content: { parts: [{ text: text.trim() || " " }] },
      taskType: "RETRIEVAL_QUERY",
      outputDimensionality: dim,
    }),
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`Gemini embedding failed (${response.status}): ${errText}`);
  }

  const payload = (await response.json()) as {
    embedding?: { values?: number[] };
  };
  const values = payload.embedding?.values;
  if (!values || values.length === 0) {
    throw new Error("Gemini embedding returned empty vector.");
  }

  const normalized = l2Normalize(values);
  return `[${normalized.join(",")}]`;
}
