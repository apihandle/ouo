"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Conversation, Message } from "@/db/schema";

type StreamState = "idle" | "streaming" | "error";

export default function Home() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<StreamState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [loadingThread, setLoadingThread] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const refreshConversations = useCallback(async () => {
    const res = await fetch("/api/conversations");
    if (res.ok) {
      const data = await res.json();
      setConversations(data.conversations);
    }
  }, []);

  useEffect(() => {
    refreshConversations();
  }, [refreshConversations]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function openConversation(id: string) {
    setActiveId(id);
    setSidebarOpen(false);
    setMessages([]);
    setError(null);
    setLoadingThread(true);
    const res = await fetch(`/api/conversations/${id}`);
    setLoadingThread(false);
    if (res.ok) {
      const data = await res.json();
      setMessages(data.messages);
    } else {
      setError("Could not load this conversation.");
    }
  }

  async function newChat() {
    setSidebarOpen(false);
    const res = await fetch("/api/conversations", { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      setConversations((prev) => [data.conversation, ...prev]);
      setActiveId(data.conversation.id);
      setMessages([]);
      setError(null);
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || status === "streaming") return;

    let convId = activeId;
    if (!convId) {
      const res = await fetch("/api/conversations", { method: "POST" });
      if (!res.ok) {
        setError("Could not start a new chat.");
        return;
      }
      const data = await res.json();
      convId = data.conversation.id;
      setConversations((prev) => [data.conversation, ...prev]);
      setActiveId(convId);
    }

    setInput("");
    setError(null);
    setStatus("streaming");
    // Optimistic user message; the server persists its own copy.
    setMessages((prev) => [
      ...prev,
      {
        id: `local-${Date.now()}`,
        conversationId: convId!,
        role: "user",
        content: text,
        createdAt: new Date(),
      } as unknown as Message,
    ]);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ conversationId: convId, content: text }),
      });

      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.error ?? `Request failed (${res.status})`);
      }

      setMessages((prev) => [
        ...prev,
        {
          id: `stream-${Date.now()}`,
          conversationId: convId!,
          role: "assistant",
          content: "",
          createdAt: new Date(),
        } as unknown as Message,
      ]);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === "assistant") {
            next[next.length - 1] = { ...last, content: last.content + chunk };
          }
          return next;
        });
      }
      setStatus("idle");
      refreshConversations();
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : "Something went wrong.");
    }
  }

  const canSend = input.trim().length > 0 && status !== "streaming";

  return (
    <div className={`shell${sidebarOpen ? " show-sidebar" : ""}`}>
      <aside className="sidebar">
        <button className="new-chat" onClick={newChat}>
          + New chat
        </button>
        <nav className="conversation-list">
          {conversations.length === 0 && <p className="muted">No chats yet</p>}
          {conversations.map((c) => (
            <button
              key={c.id}
              className={`conversation-item${c.id === activeId ? " active" : ""}`}
              onClick={() => openConversation(c.id)}
            >
              {c.title}
            </button>
          ))}
        </nav>
        <div className="brand">open</div>
      </aside>

      <main className="chat">
        <button
          className="menu-toggle"
          aria-label="Toggle conversations"
          onClick={() => setSidebarOpen((v) => !v)}
        >
          ☰
        </button>

        {messages.length === 0 && !loadingThread ? (
          <div className="empty">
            <h1>open</h1>
            <p>Ask anything. Pick up any chat from the sidebar later.</p>
          </div>
        ) : (
          <div className="messages">
            {loadingThread && <p className="muted">Loading…</p>}
            {messages.map((m) => (
              <div key={m.id} className={`bubble ${m.role}`}>
                <div className="bubble-content">{m.content}</div>
              </div>
            ))}
            {status === "streaming" && <p className="muted">open is typing…</p>}
            <div ref={bottomRef} />
          </div>
        )}

        {error && (
          <div className="error-banner" role="alert">
            {error}
          </div>
        )}

        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <textarea
            value={input}
            placeholder="Message open…"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={1}
          />
          <button type="submit" disabled={!canSend} aria-label="Send">
            Send
          </button>
        </form>
      </main>
    </div>
  );
}
