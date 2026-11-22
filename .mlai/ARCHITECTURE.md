# Architecture

"open" is a small streaming chat app: Next.js App Router, Postgres through Drizzle.

## Layout
- `src/app/page.tsx`: chat UI (sidebar, streaming answer, provider picker).
- `src/app/api/chat/route.ts`: saves the user message, calls the chosen provider, streams plain text back, saves the reply.
- `src/app/api/providers/route.ts`: lists providers with a `configured` flag and the default provider. Never returns keys.
- `src/app/api/conversations/`: create, list and open conversations.
- `src/db/schema.ts`: `conversations` and `messages` tables.
- `src/lib/model.ts`: provider registry and request/stream adapters.

## Model providers
Each provider in `src/lib/model.ts` has an id, a kind and an env prefix. It reads `<PREFIX>_API_KEY`, and optionally `<PREFIX>_BASE_URL` and `<PREFIX>_MODEL`.

| id | prefix | kind |
| --- | --- | --- |
| openai | OPENAI | openai-compatible |
| xai | XAI | openai-compatible |
| anthropic | ANTHROPIC | anthropic |
| gemini | GEMINI | openai-compatible |
| groq | GROQ | openai-compatible |
| openrouter | OPENROUTER | openai-compatible |

- `openai-compatible` posts to `<base>/chat/completions` with a Bearer key and reads `choices[0].delta.content`.
- `anthropic` posts to `<base>/messages` with `x-api-key` and `anthropic-version`, and reads `content_block_delta` / `text_delta` events.
- `/api/chat` takes an optional `provider` id. Without one it uses the first provider that has a key.
- Rows with role `system` in `messages` are local failure markers and are not sent to the model.

To add an OpenAI-compatible provider, add one entry to `PROVIDERS`. A new wire format needs a new kind in `buildChatRequest` and `extractDelta`.
