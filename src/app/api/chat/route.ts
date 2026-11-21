import { NextRequest } from "next/server";
import { asc, eq } from "drizzle-orm";
import { db } from "@/db";
import { conversations, messages } from "@/db/schema";
import { MODEL_BASE_URL, MODEL_NAME, hasModelKey } from "@/lib/model";

export const maxDuration = 60;

export async function POST(req: NextRequest) {
  let body: { conversationId?: string; content?: string };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON" }), { status: 400 });
  }

  const { conversationId, content } = body;
  if (
    typeof conversationId !== "string" ||
    !/^[0-9a-f-]{36}$/i.test(conversationId) ||
    typeof content !== "string" ||
    !content.trim()
  ) {
    return new Response(JSON.stringify({ error: "conversationId and content are required" }), {
      status: 400,
    });
  }

  const [conversation] = await db
    .select()
    .from(conversations)
    .where(eq(conversations.id, conversationId));
  if (!conversation) {
    return new Response(JSON.stringify({ error: "Conversation not found" }), { status: 404 });
  }

  const userText = content.trim();
  await db.insert(messages).values({ conversationId, role: "user", content: userText });

  const history = await db
    .select()
    .from(messages)
    .where(eq(messages.conversationId, conversationId))
    .orderBy(asc(messages.createdAt));

  // First user message becomes the conversation title.
  const isFirstMessage =
    history.filter((m) => m.role === "user").length === 1;
  if (isFirstMessage && conversation.title === "New chat") {
    await db
      .update(conversations)
      .set({ title: userText.slice(0, 80), updatedAt: new Date() })
      .where(eq(conversations.id, conversationId));
  } else {
    await db
      .update(conversations)
      .set({ updatedAt: new Date() })
      .where(eq(conversations.id, conversationId));
  }

  if (!hasModelKey()) {
    return new Response(
      JSON.stringify({
        error:
          "No model API key configured. Set OPENAI_API_KEY in Vault (Preview scope) and retry.",
      }),
      { status: 503, headers: { "content-type": "application/json" } },
    );
  }

  const upstream = await fetch(`${MODEL_BASE_URL}/chat/completions`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${process.env.OPENAI_API_KEY}`,
    },
    body: JSON.stringify({
      model: MODEL_NAME,
      stream: true,
      messages: history.map((m) => ({ role: m.role, content: m.content })),
    }),
  });

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => "");
    await db.insert(messages).values({
      conversationId,
      role: "system",
      content: `Model request failed (${upstream.status}).`,
    });
    return new Response(
      JSON.stringify({ error: `Model request failed: ${upstream.status} ${detail.slice(0, 300)}` }),
      { status: 502 },
    );
  }

  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  let full = "";

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        const reader = upstream.body!.getReader();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed.startsWith("data:")) continue;
            const data = trimmed.slice(5).trim();
            if (data === "[DONE]") continue;
            try {
              const parsed = JSON.parse(data);
              const delta = parsed?.choices?.[0]?.delta?.content;
              if (typeof delta === "string" && delta) {
                full += delta;
                controller.enqueue(encoder.encode(delta));
              }
            } catch {
              // ignore malformed keep-alive chunks
            }
          }
        }
      } finally {
        if (full.trim()) {
          await db
            .insert(messages)
            .values({ conversationId, role: "assistant", content: full })
            .catch(() => {});
        }
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-cache",
    },
  });
}
