// The X-LLM-* header triple for the OpenAI-compatible provider.
//
// Built per call site — the same shape X-Gemini-Key uses — never inside
// apiFetch, which would leak the provider key to uploads and social
// posting (design D1).

// D2: llm_client.config_from goes silently inert on a half-configured
// provider, so the UI must never treat one as ready. All three fields,
// trimmed.
export const llmConfigComplete = (llmConfig) =>
  !!(llmConfig?.baseUrl?.trim() && llmConfig?.apiKey?.trim() && llmConfig?.model?.trim());

// D9: X-LLM-Model is omitted, never sent empty — a whitespace-only model
// falls back to the server's env chain (tests/test_llm_client.py:707).
// Base + key alone still resolve server-side, so they travel whenever set.
export const llmHeaders = (llmConfig) => {
  const baseUrl = llmConfig?.baseUrl?.trim();
  const apiKey = llmConfig?.apiKey?.trim();
  if (!baseUrl || !apiKey) return {};
  const model = llmConfig?.model?.trim();
  const headers = { 'X-LLM-Base-Url': baseUrl, 'X-LLM-Key': apiKey };
  if (model) headers['X-LLM-Model'] = model;
  return headers;
};
