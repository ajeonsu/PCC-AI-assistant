import { createGroq } from "@ai-sdk/groq";
import type { LanguageModelV1 } from "ai";

export interface ModelOption {
  id: string;
  label: string;
  provider: "google" | "groq";
  modelName: string;
  badge: string;
}

export const MODEL_OPTIONS: ModelOption[] = [
  {
    id: "gemini-3.1-flash-lite",
    label: "Gemini 3.1 Flash Lite",
    provider: "google",
    modelName: "gemini-3.1-flash-lite",
    badge: "Free Recommended",
  },
  {
    id: "groq-gpt-oss-120b",
    label: "Groq GPT-OSS 120B",
    provider: "groq",
    modelName: "openai/gpt-oss-120b",
    badge: "Groq Fast",
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

export function getModelOption(modelId?: string): ModelOption {
  return MODEL_OPTIONS.find((m) => m.id === modelId) ?? MODEL_OPTIONS[0];
}

export function getGroqLanguageModel(modelName: string): LanguageModelV1 {
  const groq = createGroq({
    apiKey: process.env.GROQ_API_KEY,
  });
  return groq(modelName);
}
