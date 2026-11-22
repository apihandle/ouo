export type ProviderKind = "openai-compatible" | "anthropic";

type ProviderDefinition = {
  id: string;
  label: string;
  kind: ProviderKind;
  envPrefix: string;
  baseUrl: string;
  model: string;
};

const PROVIDERS: ProviderDefinition[] = [
  {
    id: "openai",
    label: "OpenAI",
    kind: "openai-compatible",
    envPrefix: "OPENAI",
    baseUrl: "https://api.openai.com/v1",
    model: "gpt-6-luna",
  },
  {
    id: "xai",
    label: "xAI Grok",
    kind: "openai-compatible",
    envPrefix: "XAI",
    baseUrl: "https://api.x.ai/v1",
    model: "grok-4.7",
  },
  {
    id: "anthropic",
    label: "Anthropic Claude",
    kind: "anthropic",
    envPrefix: "ANTHROPIC",
    baseUrl: "https://api.anthropic.com/v1",
    model: "claude-haiku-5-5",
  },
  {
    id: "gemini",
    label: "Google Gemini",
    kind: "openai-compatible",
    envPrefix: "GEMINI",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    model: "gemini-3.8-flash",
  },
  {
    id: "groq",
    label: "Groq",
    kind: "openai-compatible",
    envPrefix: "GROQ",
    baseUrl: "https://api.groq.com/openai/v1",
    model: "llama-3.3-70b-versatile",
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    kind: "openai-compatible",
    envPrefix: "OPENROUTER",
    baseUrl: "https://openrouter.ai/api/v1",
    model: "openrouter/auto",
  },
];

const ANTHROPIC_VERSION = "2026-10-21";
const ANTHROPIC_MAX_TOKENS = 4096;

export type ProviderInfo = {
  id: string;
  label: string;
  model: string;
  configured: boolean;
};

export type Provider = {
  id: string;
  label: string;
  kind: ProviderKind;
  keyEnv: string;
  apiKey: string | undefined;
  baseUrl: string;
  model: string;
};

export type ChatTurn = { role: "user" | "assistant"; content: string };

function resolve(def: ProviderDefinition): Provider {
  const baseUrl = process.env[`${def.envPrefix}_BASE_URL`] || def.baseUrl;
  return {
    id: def.id,
    label: def.label,
    kind: def.kind,
    keyEnv: `${def.envPrefix}_API_KEY`,
    apiKey: process.env[`${def.envPrefix}_API_KEY`] || undefined,
    baseUrl: baseUrl.replace(/\/+$/, ""),
    model: process.env[`${def.envPrefix}_MODEL`] || def.model,
  };
}

export function getProvider(id: string): Provider | null {
  const def = PROVIDERS.find((p) => p.id === id);
  return def ? resolve(def) : null;
}

export function listProviders(): ProviderInfo[] {
  return PROVIDERS.map(resolve).map((p) => ({
    id: p.id,
    label: p.label,
    model: p.model,
    configured: Boolean(p.apiKey),
  }));
}

export function defaultProviderId(): string {
  const configured = PROVIDERS.map(resolve).find((p) => p.apiKey);
  return (configured ?? resolve(PROVIDERS[0])).id;
}

export function buildChatRequest(
  provider: Provider,
  turns: ChatTurn[],
): { url: string; init: RequestInit } {
  if (provider.kind === "anthropic") {
    return {
      url: `${provider.baseUrl}/messages`,
      init: {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-api-key": provider.apiKey ?? "",
          "anthropic-version": ANTHROPIC_VERSION,
        },
        body: JSON.stringify({
          model: provider.model,
          max_tokens: ANTHROPIC_MAX_TOKENS,
          stream: true,
          messages: turns,
        }),
      },
    };
  }
  return {
    url: `${provider.baseUrl}/chat/completions`,
    init: {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${provider.apiKey}`,
      },
      body: JSON.stringify({
        model: provider.model,
        stream: true,
        messages: turns,
      }),
    },
  };
}

export function extractDelta(kind: ProviderKind, event: unknown): string {
  const e = event as {
    type?: string;
    delta?: { type?: string; text?: unknown };
    choices?: { delta?: { content?: unknown } }[];
  } | null;
  if (kind === "anthropic") {
    if (e?.type !== "content_block_delta" || e.delta?.type !== "text_delta") return "";
    return typeof e.delta.text === "string" ? e.delta.text : "";
  }
  const content = e?.choices?.[0]?.delta?.content;
  return typeof content === "string" ? content : "";
}
