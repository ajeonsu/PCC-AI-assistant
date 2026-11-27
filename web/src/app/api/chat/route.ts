import { streamText } from "ai";
import { runGeminiDataStreamResponse } from "@/lib/ai/geminiAgent";
import {
  DEFAULT_MODEL_ID,
  getGroqLanguageModel,
  getModelOption,
} from "@/lib/ai/models";
import { SYSTEM_PROMPT } from "@/lib/ai/systemPrompt";
import { createAssistantTools } from "@/lib/ai/tools";
import { getAuthorizedUser } from "@/lib/supabase/server";

export const maxDuration = 60;

export async function POST(request: Request) {
  const user = await getAuthorizedUser();
  if (!user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = (await request.json()) as {
    messages?: Parameters<typeof streamText>[0]["messages"];
    modelId?: string;
  };

  if (!Array.isArray(body.messages) || body.messages.length === 0) {
    return Response.json({ error: "Messages are required" }, { status: 400 });
  }

  const option = getModelOption(body.modelId ?? DEFAULT_MODEL_ID);
  const recentMessages = body.messages.slice(-6);

  if (option.provider === "google") {
    return runGeminiDataStreamResponse({
      modelName: option.modelName,
      system: SYSTEM_PROMPT,
      messages: recentMessages as Array<{ role: string; content: unknown }>,
      maxSteps: 6,
    });
  }

  const model = getGroqLanguageModel(option.modelName);
  const tools = createAssistantTools();

  const result = streamText({
    model,
    system: SYSTEM_PROMPT,
    messages: recentMessages,
    tools,
    maxSteps: 5,
    maxRetries: 3,
    temperature: 0.1,
  });

  return result.toDataStreamResponse({
    getErrorMessage: (error) => {
      if (error instanceof Error) return error.message;
      return "An unexpected error occurred while generating a response.";
    },
  });
}
