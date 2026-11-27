import { streamText } from "ai";
import { DEFAULT_MODEL_ID, getLanguageModel } from "@/lib/ai/models";
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

  const model = getLanguageModel(body.modelId ?? DEFAULT_MODEL_ID);
  const tools = createAssistantTools();
  const recentMessages = body.messages.slice(-8);

  const result = streamText({
    model,
    system: SYSTEM_PROMPT,
    messages: recentMessages,
    tools,
    maxSteps: 6,
    maxRetries: 1,
    temperature: 0.1,
  });

  return result.toDataStreamResponse({
    getErrorMessage: (error) => {
      if (error instanceof Error) return error.message;
      return "An unexpected error occurred while generating a response.";
    },
  });
}
