import { createGroq } from "@ai-sdk/groq";
import type { LanguageModelV1 } from "ai";

export interface ModelOption {
  id: string;
  label: string;
  provider: "groq";
  modelName: string;
  badge: string;
}

export const MODEL_OPTIONS: ModelOption[] = [
  {
    id: "groq-gpt-oss-120b",
    label: "Groq GPT-OSS 120B",
    provider: "groq",
    modelName: "openai/gpt-oss-120b",
    badge: "Free Recommended",
  },
  {
    id: "groq-qwen-3.8",
    label: "Groq Qwen 3.8 27B",
    provider: "groq",
    modelName: "qwen/qwen3.8-27b",
    badge: "Free SQL",
  },
  {
    id: "groq-gpt-oss-20b",
    label: "Groq GPT-OSS 20B",
    provider: "groq",
    modelName: "openai/gpt-oss-20b",
    badge: "Free Ultra-Fast",
  },
];

export const DEFAULT_MODEL_ID = MODEL_OPTIONS[0].id;

export function getLanguageModel(modelId?: string): LanguageModelV1 {
  const chosen =
    MODEL_OPTIONS.find((m) => m.id === modelId) ?? MODEL_OPTIONS[0];

  const groq = createGroq({
    apiKey: process.env.GROQ_API_KEY,
  });
  return groq(chosen.modelName);
}
