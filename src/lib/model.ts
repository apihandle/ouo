export const MODEL_NAME = process.env.OPENAI_MODEL ?? "gpt-4o-mini";
export const MODEL_BASE_URL = process.env.OPENAI_BASE_URL ?? "https://api.openai.com/v1";

export function hasModelKey() {
  return Boolean(process.env.OPENAI_API_KEY);
}
