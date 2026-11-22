import { NextRequest } from "next/server";
import { asc, eq } from "drizzle-orm";
import { db } from "@/db";
import { conversations, messages } from "@/db/schema";
import {
  buildChatRequest,
  defaultProviderId,
  extractDelta,
  getProvider,
  type ChatTurn,
} from "@/lib/model";

export const maxDuration = 60;

export async function POST(req: NextRequest) {
  let body: { conversationId?: string; content?: string; provider?: string };
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

  if (body.provider !== undefined && typeof body.provider !== "string") {
    return new Response(JSON.stringify({ error: "provider must be a string" }), { status: 400 });
  }
  const provider = getProvider(body.provider ?? defaultProviderId());
  if (!provider) {
    return new Response(JSON.stringify({ error: `Unknown provider: ${body.provider}` }), {
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

  if (!provider.apiKey) {
    return new Response(
      JSON.stringify({
        error: `No API key configured for ${provider.label}. Set ${provider.keyEnv} in Vault (Preview scope) and retry.`,
      }),
      { status: 503, headers: { "content-type": "application/json" } },
    );
  }

  // Stored "system" rows are local failure markers, not prompts.
  const turns: ChatTurn[] = history
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({ role: m.role as ChatTurn["role"], content: m.content }));

  const { url, init } = buildChatRequest(provider, turns);
  let upstream: Response;
  try {
    upstream = await fetch(url, init);
  } catch {
    return new Response(
      JSON.stringify({ error: `Could not reach ${provider.label} at ${provider.baseUrl}.` }),
      { status: 502 },
    );
  }

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => "");
    await db.insert(messages).values({
      conversationId,
      role: "system",
      content: `${provider.label} request failed (${upstream.status}).`,
    });
    return new Response(
      JSON.stringify({
        error: `${provider.label} request failed: ${upstream.status} ${detail.slice(0, 300)}`,
      }),
      { status: 502 },
    );
  }

  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  let full = "";

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      let failure: Error | null = null;
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
            let parsed: unknown;
            try {
              parsed = JSON.parse(data);
            } catch {
              // ignore malformed keep-alive chunks
              continue;
            }
            const streamError = (parsed as { type?: string; error?: { message?: string } })
              ?.type === "error"
              ? (parsed as { error?: { message?: string } }).error
              : null;
            if (streamError) {
              throw new Error(
                `${provider.label} stream error: ${streamError.message ?? "unknown"}`,
              );
            }
            const delta = extractDelta(provider.kind, parsed);
            if (delta) {
              full += delta;
              controller.enqueue(encoder.encode(delta));
            }
          }
        }
      } catch (e) {
        failure = e instanceof Error ? e : new Error("Stream failed");
      } finally {
        if (full.trim()) {
          await db
            .insert(messages)
            .values({ conversationId, role: "assistant", content: full })
            .catch(() => {});
        }
        if (failure) controller.error(failure);
        else controller.close();
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
