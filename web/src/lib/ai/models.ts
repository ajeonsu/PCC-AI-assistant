import { createGoogleGenerativeAI } from "@ai-sdk/google";
import { createGroq } from "@ai-sdk/groq";
import type { LanguageModelV1 } from "ai";

export interface ModelOption {
  id: string;
  label: string;
  provider: "groq" | "google";
  modelName: string;
  badge: string;
}

export const MODEL_OPTIONS: ModelOption[] = [
  {
    id: "gemini-3.8-flash",
    label: "Gemini 3.8 Flash",
    provider: "google",
    modelName: "gemini-3.8-flash",
    badge: "Free Recommended",
  },
  {
    id: "groq-gpt-oss-120b",
    label: "Groq GPT-OSS 120B",
    provider: "groq",
    modelName: "openai/gpt-oss-120b",
    badge: "Groq Free",
  },
  {
    id: "groq-qwen-3.8",
    label: "Groq Qwen 3.8 27B",
    provider: "groq",
    modelName: "qwen/qwen3.8-27b",
    badge: "Groq SQL",
  },
];

export const DEFAULT_MODEL_ID = MODEL_OPTIONS[0].id;

export function getLanguageModel(modelId?: string): LanguageModelV1 {
  const chosen =
    MODEL_OPTIONS.find((m) => m.id === modelId) ?? MODEL_OPTIONS[0];

  if (chosen.provider === "groq") {
    const groq = createGroq({
      apiKey: process.env.GROQ_API_KEY,
    });
    return groq(chosen.modelName);
  }

  const google = createGoogleGenerativeAI({
    apiKey: process.env.GEMINI_API_KEY,
  });
  return google(chosen.modelName);
}
