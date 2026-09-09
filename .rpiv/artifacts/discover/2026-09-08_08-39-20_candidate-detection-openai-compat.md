---
date: 2026-09-08T08:39:20+0700
author: Yogiswara Utama
commit: 51c1861
branch: main
repository: openshorts
topic: "candidate detection works on OpenAI-compatible endpoints (Ollama Cloud)"
tags: [intent, frd, candidate-detection, ai-provider, openai-compatible, ollama, cheap-events, vision, deep-analysis]
status: ready
last_updated: 2026-09-08T08:39:20+0700
last_updated_by: Yogiswara Utama
---

# FRD: candidate detection works on OpenAI-compatible endpoints (Ollama Cloud)

## Summary

The candidate-detection panel — three signal toggles (scene detection, audio events, visual activity), the vision pass, and the deep full-VOD scan — already ships in the dashboard. The maintainer's fork added an OpenAI-compatible provider path (Ollama Cloud and friends) as an alternative to Gemini. This FRD targets proof and completion of that compatibility: verify with live runs that the panel works under `AI_PROVIDER=openai`, and close the four gaps where the panel's promises do not hold on that path. The UI stays untouched.

## Problem & Intent

The developer's framing, verbatim:

> "in my original git commit, i integrate alternative to google gemini, via open ai compatible / ollama cloud . and i'm afraid it does not connect with that feature"

"that feature" is the candidate-detection card in the clip generator menu ("fine-tune what the AI looks for"). The fear: signals and vision that work under Gemini break or degrade silently under the OpenAI-compatible path.

Terminology (agreed during the interview): `AI_PROVIDER=openai` means the OpenAI-compatible chat-completions protocol, not OpenAI-the-vendor. The endpoint comes from `OPENAI_BASE_URL` (default `https://api.openai.com/v1`, mirrored into `DEEP_OPENAI_BASE_URL` at `app.py:2717`), the model from `OPENAI_MODEL`, and the key is optional (Ollama, LM Studio run keyless). Per-job override uses `X-AI-Provider` / `X-OpenAI-*` headers.

Probe verdict (code evidence, not yet exercised live):

- The three cheap signals are computed locally (ffmpeg / OpenCV / PySceneDetect — no LLM) and enter **identical prompt text** for both providers (`cheap_events.py:107-196`, merge at `main.py:2329` and `main.py:2392-2403`; the same score prompt string goes to whichever client `main.py:2502` builds).
- Vision frames reach the OpenAI-compatible client as `image_url` parts (`ai_provider.py:294-298`; call site `main.py:2577-2594`).
- Deep scan has a real frame fallback for the OpenAI-compatible path: 12 sampled frames (`main.py:1638-1639`, `main.py:2255-2263`); Gemini keeps native video.

## Goals

- Prove with live runs on Ollama Cloud (and one local compatible server) that candidate detection works end to end under `AI_PROVIDER=openai`.
- Close the four gaps so the panel's promises hold on any provider (see Functional Requirements).
- Keep the shipped dashboard UI untouched.
- Keep the Gemini path's behavior unchanged (pinned by tests).

## Non-Goals

- No redesign or extension of the candidate-detection panel UI (copy, toggles, layout stay as shipped).
- No cost accounting for OpenAI-compatible stages (`ai_provider.py:441-446` returns `None`; matters for cloud metering only).
- No schema-400 resilience work beyond the existing LM-Studio retry branch (`ai_provider.py:371-380`).
- No new provider integrations or config surfaces.

## Functional Requirements

1. **Silent-video path through the provider abstraction.** The system SHALL process a video with no speech under an OpenAI-compatible job (no `GEMINI_API_KEY`) through the provider abstraction's frames path and produce clips, instead of the current Gemini-pinned hard failure (`main.py:2888-2905`).
2. **Vision capability check.** When vision or deep is ON and the configured OpenAI-compatible model does not accept images, the system SHALL check capability once per job, print one clear warning to the job log/status, and skip the frame calls — replacing the current silent per-window failures (`cloud/semantic_analyzer.py:311-315`) that waste latency and return empty signals.
3. **Honor sampling parameters.** `create_ai_provider` SHALL pass `temperature`, `max_tokens`, and `timeout` through to both provider clients (today they are dropped, `ai_provider.py:450-473`); `AI_TIMEOUT` and the deep pass's `timeout=90` MUST take effect on OpenAI-compatible jobs.
4. **Cheap signals on short videos.** Videos of 120 seconds or less SHALL receive the same cheap-signal merge as long videos, for both providers (today the merge block sits after the early return behind a dead guard — `'cheap_events' in locals()` is always False — `main.py:1991-2002` vs block at `main.py:2066-2075`).

## Non-Functional Requirements

- **Performance**: the capability check of FR2 runs once per job, not per window; frame batching sizes stay as shipped (6 per vision window, 12 deep frames).
- **Security**: no new endpoints; BYOK header handling unchanged; a header key never travels to an env-configured base URL.
- **UX / Accessibility**: no dashboard changes; warnings surface in the job log the way the existing `[toggles]` / `[cheap]` lines do (`app.py:2719-2721`).
- **Reliability**: the full test suite stays at its baseline (889 passed / 10 pre-existing environment failures) and `tests/test_no_double_route.py` pins hold — an openai job dispatches only `{"openai"}`, `genai.Client` is never constructed on that path, `llm_client.chat` is never called on the pipeline path.

## Constraints & Assumptions

- Acceptance endpoints: Ollama Cloud (primary) plus one local OpenAI-compatible server (LM Studio or local Ollama).
- Vision/deep acceptance runs need a vision-capable model — the qwen3-vl family the code and UI copy already reference (`App.jsx:2227`, `main.py:2109`).
- Assumption: cheap signals and the deep frame fallback work under OpenAI-compatible endpoints. Evidence is code reading only; the live runs must confirm or flip this.
- Assumption: the four fixes need no new env gates — they repair shipped, merged behavior rather than add features.
- Graph evidence comes from a fresh full re-index of this commit; `dashboard/src/App.jsx` carries parse_partial ranges (1800, 2047, 2461), so claims about it rest on direct source reads.

## Acceptance Criteria

- [ ] `pytest` full suite matches baseline (889 passed; the 10 known environment failures unchanged), and `tests/test_no_double_route.py` passes after the fixes.
- [ ] New test: an openai-provider job with a no-speech video and no `GEMINI_API_KEY` completes and produces at least one clip via the frames path.
- [ ] New test: vision ON with a text-only mock model — the job completes, exactly one clear "model has no vision" line lands in the job log, and zero per-window frame calls fire after the capability check.
- [ ] New test: `create_ai_provider` forwards `temperature` / `max_tokens` / `timeout` to the client constructor for both providers.
- [ ] New test: a ≤120 s video job emits the cheap-events merge (scene/audio/visual hints) into the score prompt for both providers.
- [ ] Live Ollama Cloud run (vision-capable model, all toggles ON): the job completes; logs show the `[toggles]` and `[cheap]` lines with scene/audio/visual/vision/deep values, deep runs in frames mode, and clips are produced.
- [ ] Live local compatible server (LM Studio or local Ollama): the same toggles run completes.
- [ ] Gemini control run on the same input produces clips with no behavior change versus pre-fix code.

## Recommended Approach

Modify `ai_provider.py` (parameter passthrough plus a one-shot image-capability probe helper), `main.py` (route the silent-video branch through the provider abstraction's frames mode; move the cheap-events block ahead of the ≤120 s early return), and `cloud/semantic_analyzer.py` (fail-fast capability check feeding the single warning). No dashboard changes.

## Decisions

### FRD target: verify + fix, not build
**Question**: The candidate-detection panel, toggles, and copy already exist in the tree (`App.jsx:2192-2260`) — what should this FRD cover?
**Recommended**: n/a — anti-rescope `intent` question, no recommendation offered.
**Chosen**: Verify + fix compat gaps; UI stays untouched.
**Rationale**: evidence `dashboard/src/App.jsx:2192-2260` + developer choice.

### Provider terminology
**Question**: Does `AI_PROVIDER=openai` mean an OpenAI-compatible endpoint like Ollama Cloud?
**Recommended**: n/a — clarification, answered from code.
**Chosen**: Yes — the OpenAI-compatible chat-completions protocol; endpoint from `OPENAI_BASE_URL`, model from `OPENAI_MODEL`, key optional.
**Rationale**: evidence `app.py:2717`, `main.py:2265-2279` (LM-Studio retry), `ai_provider.py:294-298`.

### Cheap signals stay as-is
**Question**: Pre-resolved — the three cheap signals are provider-neutral local computations entering identical prompt text for both providers. Keep as-is?
**Recommended**: Confirm — no change.
**Chosen**: No code change. Developer delegated the judgment ("i'm not yet run it or test it, if you think it's enough or really work, then no change").
**Rationale**: evidence `cheap_events.py:107-196`, `main.py:2329`, `main.py:2392-2403`, `main.py:2502` + live-run acceptance criterion added.

### Deep-scan frame fallback stays as-is
**Question**: Pre-resolved — deep scan under OpenAI-compatible inspects 12 sampled frames; Gemini keeps native video. Keep as-is?
**Recommended**: Confirm — no change.
**Chosen**: No code change. Same delegated judgment as above.
**Rationale**: evidence `main.py:1638-1639`, `main.py:2255-2263` + live-run acceptance criterion added.

### Scope: four gaps in, two out
**Question**: Which probe-found gaps become requirements?
**Recommended**: all four pre-selected options.
**Chosen**: Silent-video wall, no-vision warning, honor timeout/params, short-video signals. Cost accounting and schema-400 resilience excluded.
**Rationale**: each chosen item maps to a concrete defect with `file:line`; the two excluded items were named in the question and not selected.

### Acceptance endpoints
**Question**: Which OpenAI-compatible endpoint(s) must the acceptance runs use?
**Recommended**: Ollama Cloud + one local server.
**Chosen**: Ollama Cloud + local.
**Rationale**: developer choice; local servers catch wire quirks the same way the LM-Studio retry branch was born.

### Gemini must not regress
**Question**: Must the fixes leave the existing Gemini path untouched?
**Recommended**: Yes.
**Chosen**: Yes — suite baseline and `tests/test_no_double_route.py` pins are acceptance criteria.
**Rationale**: developer choice; repo rule that merged behavior is config-gated and pinned by tests.

## Open Questions

- None — the developer deferred nothing explicitly.

## Suggested Follow-ups

- Cost accounting returns `None` for every OpenAI-compatible stage — job totals under-report to $0 (`ai_provider.py:441-446`).
- Schema-strict 400s abort score/detail jobs; the no-schema retry fires only when the 400 text mentions `response_format` (`ai_provider.py:371-380`, abort at `main.py:2866-2868`).
- `apply_game_profile_scoring` returns scores unchanged when profile weights are absent — makes the vision toggle partly cosmetic on both providers (`main.py:77-82`).
- `_run_gemini_stage` / `_run_stage_split` are production-dead (no non-recursive callers, `main.py:1564`, `main.py:1885`) — deletion candidates.
- Deep ON forces vision OFF only when the `X-Enable-Vision` header is absent (`app.py:2682-2683`); the dashboard always sends it, so both run — a cost surprise waiting for API users.

## References

- Input: free-text feature description (the panel copy pasted from `dashboard/src/App.jsx:2192-2197`) + the provider-compatibility question.
- `dashboard/src/App.jsx:2192-2260` — the existing candidate-detection UI.
- `cheap_events.py`, `ai_provider.py`, `main.py`, `cloud/semantic_analyzer.py` — the probed seam.
- `tests/test_no_double_route.py` — stage-ownership pins.
- `.rpiv/artifacts/plans/2026-09-06_14-41-23_marsic-fork-parity-merge.md` — fork-merge context.
- CLAUDE.md — stage-ownership and env-namespace rules.
