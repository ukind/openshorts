---
date: 2026-09-09T08:41:39+0700
author: Yogiswara Utama
commit: fde9338
branch: main
repository: openshorts
topic: "OpenAI-compatible endpoint integration audit: deep full-VOD scan and 6-frame vision analysis"
tags: [intent, frd, ai-provider, deep-scan, vision-analysis, openai-compatible, ollama-cloud]
status: ready
last_updated: 2026-09-09T08:41:39+0700
last_updated_by: Yogiswara Utama
---

# FRD: OpenAI-compatible endpoint integration audit: deep full-VOD scan and 6-frame vision analysis

## Summary

Audit of whether the two advanced-analysis toggles work through an OpenAI-compatible endpoint such as Ollama Cloud. Verdict: both features are integrated, committed at `fde9338`, and validated on 2026-09-08. The developer's stop condition fired. No feature work follows.

## Problem & Intent

Developer's words: "the open ai compatible endpoint is new implementation, i'm afraid it does not integrated with the those feature, if it well integrated, then stop."

The question behind it: "if using open-ai compatible endpoint like from ollama cloud, does it work as intended?" — with "as intended" defined by the two dashboard tooltips quoted in the input: 6 frames per candidate window (3 uniform + 3 around peaks), and a whole-VOD watch before the candidate search that uses a frame sample instead of the footage on OpenAI-compatible models.

## Goals

- Confirm whether deep full-VOD scan and the 6-frame vision pass route through the OpenAI-compatible provider.
- Confirm what each feature sends on such an endpoint, and how a text-only model degrades.
- Stop with no further pipeline stages if integration is confirmed.

## Non-Goals

- No code changes. The integration exists and passed validation.
- No live run against a real Ollama Cloud endpoint (recorded as a follow-up, not scope).
- Native-video parity for the OpenAI deep scan (ROADMAP 36.3) stays deferred.

## Functional Requirements

Each requirement is already satisfied by HEAD `fde9338`. This FRD records verification, not new work.

1. The deep full-VOD scan SHALL build its provider through `ai_provider.create_ai_provider` from the `DEEP_*` config namespace (`main.py:1611-1644`, `main.py:2201`).
2. On an OpenAI-compatible provider, the deep scan SHALL send a frame sample — 12 frames spread 0 to duration, peak-biased — plus the full transcript, not the video file (`main.py:2127`, `main.py:2154`).
3. The per-window vision pass SHALL send its 6 frames as multimodal `image_url` content parts through the same provider interface (`cloud/semantic_analyzer.py:207-224`, `ai_provider.py:344-370`).
4. A text-only model SHALL produce a visible skip of both features via `probe_vision_support` returning `no_vision`, and the job SHALL continue on the transcript path (`main.py:2135-2141`, `main.py:2549`).

## Non-Functional Requirements

- **Performance**: the vision probe uses an 8x8 PNG, `max_tokens=16`, `timeout=15`, cached per provider identity so deep and vision share one probe (`ai_provider.py:538-618`).
- **Reliability**: deep-scan failure falls back to the standard pipeline with a warning (`main.py:2359-2362`); vision-pass failure returns empty signals and keeps the existing score (`cloud/semantic_analyzer.py:240-245`).
- **Security**: keyless endpoints receive a placeholder key and never expose it as a credential (`ai_provider.py:344-346`).

## Constraints & Assumptions

- Verification target: HEAD `fde9338`, branch `main`. Working tree clean except `.rpiv/` and `.dirac-cache/` artifacts. `git diff 51c1861..fde9338 --stat` matches the validation run's declared write-set exactly (8 files).
- The 2026-09-08 validation run tested the same content that became `fde9338`: 117 targeted tests passed; full suite 935 passed with 10 pre-existing failures.
- The codebase-memory graph is stale (indexed 2026-09-08T01:46Z). Every claim in this FRD was verified against live source.

## Acceptance Criteria

- [ ] `git log -1 --oneline` prints `fde9338 feat(ai): silent-video and vision analysis run on the configured provider`.
- [ ] `.venv/Scripts/python.exe -m pytest tests/test_ai_provider.py tests/test_no_double_route.py -q` exits 0 (51 passed: 37 + 14).
- [ ] `grep -n "probe_vision_support" main.py ai_provider.py` shows the deep gate (`main.py:2135`), the vision gate (`main.py:2549`), and the implementation (`ai_provider.py:584`).
- [ ] With `DEEP_AI_PROVIDER=openai` and a text-only model, job logs print `👁️ Deep scan skipped: the selected model cannot see images (text-only).` and the job completes.

## Recommended Approach

None. The audit verdict is stop. Downstream stages are not warranted unless a Suggested Follow-up is promoted to scope by a future invocation.

## Decisions

### Audit intent and stop condition
**Question**: "Your input ends with: 'if using open-ai compatible endpoint like from ollama cloud, does it work as intended?' — what outcome do you want from answering that?"
**Recommended**: n/a — `intent` question.
**Chosen**: "the open ai compatible endpoint is new implementation, i'm afraid it does not integrated with the those feature, if it well integrated, then stop"
**Rationale**: developer's framing; success = a verdict, not a build.

### Verdict: integrated — stop
**Question**: "Probe verdict: deep full-vod scan and 6-frame vision analysis ARE integrated with the OpenAI-compatible endpoint — committed in fde9338, validated yesterday, text-only models degrade gracefully via the vision probe. Your stop condition says stop. Stop here, or did you observe something in practice that contradicts the code?"
**Recommended**: Stop — integrated.
**Chosen**: Stop — integrated.
**Rationale**: developer's pre-stated stop condition; evidence: `.rpiv/artifacts/validation/2026-09-08_21-05-02_candidate-detection-works-on-openai-compatible-endpoints-ollama-cloud.md` (verdict pass), commit `fde9338`, citations in Functional Requirements.

### Pre-resolved from codebase evidence — confirmed
**Question**: "Pre-resolved from codebase evidence — confirmed in Step 4."
**Recommended**: keep current behavior for both features on OpenAI-compatible endpoints.
**Chosen**: confirmed by the Stop decision.
**Rationale**: evidence: `main.py:2201` (shared factory), `main.py:2127,2154` (12-frame sample), `cloud/semantic_analyzer.py:207-224` (multimodal parts), `main.py:2135-2141` (text-only skip) + confirmed.

## Open Questions

None. The developer deferred nothing.

## Suggested Follow-ups

- Stale comment near `main.py:2119` says "qwen 24 (16 uniform + 8 peak)" but the code sends 12 frames (`main.py:2127`); the cut to 12 is documented in ROADMAP 36.3.
- No live run against a real Ollama Cloud endpoint is recorded; the first live run must confirm the server accepts the new `timeout` field on every request (validation artifact, Potential Issues).
- `log_view.py` `^`-anchored verbatim rules never match timestamped production lines — pre-existing cloud bug, unchanged by the plan (validation artifact, Potential Issues).
- `main.py:2458` big-try handler prints "⚠️ Cheap event seeding skipped" though it now guards deep-scan/merge failures only (validation artifact, Phase 5 follow-up candidate).
- ROADMAP 36.3: native-video parity for the OpenAI deep scan stays deferred (LM Studio 90k context cannot hold 24 frames).

## References

- Input: free-text feature description (two dashboard tooltips + the Ollama Cloud question), `/skill:discover` invocation 2026-09-09.
- `.rpiv/artifacts/validation/2026-09-08_21-05-02_candidate-detection-works-on-openai-compatible-endpoints-ollama-cloud.md`
- `.rpiv/artifacts/plans/2026-09-08_10-21-18_candidate-detection-openai-compat.md`
- `ROADMAP.md` section 36.3
- `main.py`, `ai_provider.py`, `cloud/semantic_analyzer.py`, `tests/test_ai_provider.py`, `tests/test_no_double_route.py`
