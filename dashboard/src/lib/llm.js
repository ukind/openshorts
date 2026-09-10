// The dual-family BYOK header builders — the single emitter of both config
// families (design: unified AI provider config).
//
// Built per call site — the same shape X-Gemini-Key uses — never inside
// apiFetch, which would leak the provider key to uploads and social
// posting (design D1).

// D2: llm_client.config_from goes silently inert on a half-configured
// provider, so the UI must never treat one as ready. All three fields,
// trimmed. Satellites need the complete triple.
export const llmConfigComplete = (llmConfig) =>
  !!(llmConfig?.baseUrl?.trim() && llmConfig?.apiKey?.trim() && llmConfig?.model?.trim());

// D10: the unified card is "set" when the endpoint URL exists. The key is
// optional (OpenAI-compatible local servers need none — resolve_openai
// accepts a keyless triple) and the model is optional ("server default").
export const aiProviderSet = (cfg) => !!cfg?.baseUrl?.trim();

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

// The pipeline family, derived from the SAME store: resolve_openai (app.py)
// tolerates a missing key and a missing model, so each header rides only
// when its field is set. The base URL is the anchor — without it the job
// would run on the server's env, which is not what a saved card means.
export const openaiHeaders = (cfg) => {
  const baseUrl = cfg?.baseUrl?.trim();
  if (!baseUrl) return {};
  const headers = { 'X-OpenAI-Base-Url': baseUrl };
  const apiKey = cfg?.apiKey?.trim();
  if (apiKey) headers['X-OpenAI-Key'] = apiKey;
  const model = cfg?.model?.trim();
  if (model) headers['X-OpenAI-Model'] = model;
  return headers;
};
