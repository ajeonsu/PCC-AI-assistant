import { createDataStreamResponse, formatDataStreamPart } from "ai";
import {
  execListDriveFiles,
  execQuerySpreadsheetSql,
  execSearchDocuments,
  execSearchSpreadsheets,
} from "@/lib/ai/tools";

interface ChatMessageInput {
  role: string;
  content: unknown;
}

interface GeminiPart {
  text?: string;
  thought?: boolean;
  thoughtSignature?: string;
  functionCall?: {
    name: string;
    args?: Record<string, unknown>;
  };
  functionResponse?: {
    name: string;
    response: Record<string, unknown>;
  };
}

interface GeminiContent {
  role: "user" | "model";
  parts: GeminiPart[];
}

const GEMINI_TOOLS = [
  {
    functionDeclarations: [
      {
        name: "searchDocuments",
        description: "Search indexed PDFs, scanned OCR pages, and Word docs.",
        parameters: {
          type: "OBJECT",
          properties: {
            query: { type: "STRING" },
            fileType: { type: "STRING" },
          },
          required: ["query"],
        },
      },
      {
        name: "searchSpreadsheets",
        description:
          "Find Excel sheets, SQL table names, columns, and sample rows by topic or filename.",
        parameters: {
          type: "OBJECT",
          properties: {
            query: { type: "STRING" },
          },
          required: ["query"],
        },
      },
      {
        name: "querySpreadsheetSql",
        description:
          "Run read-only PostgreSQL SELECT/WITH query on excel_data.* tables.",
        parameters: {
          type: "OBJECT",
          properties: {
            sqlQuery: { type: "STRING" },
          },
          required: ["sqlQuery"],
        },
      },
      {
        name: "listDriveFiles",
        description: "List indexed Google Drive files, sizes, and links.",
        parameters: {
          type: "OBJECT",
          properties: {
            nameQuery: { type: "STRING" },
          },
        },
      },
    ],
  },
];

async function executeToolByName(
  name: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  switch (name) {
    case "searchDocuments":
      return await execSearchDocuments({
        query: String(args.query ?? ""),
        fileType: typeof args.fileType === "string" ? args.fileType : undefined,
      });
    case "searchSpreadsheets":
      return await execSearchSpreadsheets({
        query: String(args.query ?? ""),
      });
    case "querySpreadsheetSql":
      return await execQuerySpreadsheetSql({
        sqlQuery: String(args.sqlQuery ?? ""),
      });
    case "listDriveFiles":
      return await execListDriveFiles({
        nameQuery:
          typeof args.nameQuery === "string" ? args.nameQuery : undefined,
      });
    default:
      return { error: `Unknown tool: ${name}` };
  }
}

function toGeminiContents(messages: ChatMessageInput[]): GeminiContent[] {
  const contents: GeminiContent[] = [];
  for (const msg of messages) {
    if (msg.role !== "user" && msg.role !== "assistant") continue;
    let text = "";
    if (typeof msg.content === "string") {
      text = msg.content;
    } else if (Array.isArray(msg.content)) {
      text = msg.content
        .map((p) =>
          typeof p === "object" && p && "text" in p
            ? String((p as { text?: unknown }).text ?? "")
            : "",
        )
        .join("");
    }
    if (!text.trim()) continue;
    contents.push({
      role: msg.role === "assistant" ? "model" : "user",
      parts: [{ text }],
    });
  }
  return contents;
}

export function runGeminiDataStreamResponse(params: {
  modelName: string;
  system: string;
  messages: ChatMessageInput[];
  maxSteps?: number;
}): Response {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    return Response.json(
      { error: "GEMINI_API_KEY is not configured." },
      { status: 500 },
    );
  }

  const maxSteps = params.maxSteps ?? 6;
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(params.modelName)}:generateContent?key=${encodeURIComponent(apiKey)}`;

  return createDataStreamResponse({
    execute: async (dataStream) => {
      const contents = toGeminiContents(params.messages);

      for (let step = 0; step < maxSteps; step++) {
        const resp = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: AbortSignal.timeout(20000),
          body: JSON.stringify({
            systemInstruction: { parts: [{ text: params.system }] },
            contents,
            tools: GEMINI_TOOLS,
            generationConfig: { temperature: 0.1 },
          }),
        });

        if (!resp.ok) {
          const errBody = await resp.text();
          throw new Error(`Gemini API error (${resp.status}): ${errBody}`);
        }

        const payload = (await resp.json()) as {
          candidates?: Array<{ content?: GeminiContent }>;
        };
        const modelContent = payload.candidates?.[0]?.content;
        if (!modelContent || !Array.isArray(modelContent.parts)) {
          throw new Error("Gemini returned an empty response.");
        }

        // Preserve exact model parts verbatim (including thoughtSignature).
        contents.push({
          role: "model",
          parts: modelContent.parts,
        });

        const toolCalls = modelContent.parts.filter((p) => p.functionCall);
        if (toolCalls.length === 0) {
          const finalText = modelContent.parts
            .filter((p) => typeof p.text === "string" && !p.thought)
            .map((p) => p.text)
            .join("");

          if (finalText) {
            dataStream.write(formatDataStreamPart("text", finalText));
          }
          dataStream.write(
            formatDataStreamPart("finish_message", { finishReason: "stop" }),
          );
          return;
        }

        const responseParts: GeminiPart[] = [];
        for (let i = 0; i < toolCalls.length; i++) {
          const fc = toolCalls[i].functionCall!;
          const toolName = fc.name;
          const args = (fc.args ?? {}) as Record<string, unknown>;
          const toolCallId = `gem_${step}_${i}_${Date.now()}`;

          dataStream.write(
            formatDataStreamPart("tool_call", {
              toolCallId,
              toolName,
              args: args as Record<string, never>,
            }),
          );

          const result = await executeToolByName(toolName, args);

          dataStream.write(
            formatDataStreamPart("tool_result", {
              toolCallId,
              result: result as never,
            }),
          );

          responseParts.push({
            functionResponse: {
              name: toolName,
              response: result,
            },
          });
        }

        contents.push({
          role: "user",
          parts: responseParts,
        });
      }

      dataStream.write(
        formatDataStreamPart(
          "text",
          "Reached maximum tool steps before completing the answer. Please try rephrasing or narrowing your question.",
        ),
      );
      dataStream.write(
        formatDataStreamPart("finish_message", { finishReason: "length" }),
      );
    },
    onError: (err) => {
      if (err instanceof Error) return err.message;
      return "An error occurred while calling Gemini.";
    },
  });
}
