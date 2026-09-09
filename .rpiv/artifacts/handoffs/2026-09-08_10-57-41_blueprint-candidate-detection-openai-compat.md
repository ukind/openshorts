---
date: 2026-09-08T10:57:41+0700
author: Yogiswara Utama
commit: 51c1861
branch: main
repository: openshorts
topic: "Blueprint candidate-detection OpenAI-compatible endpoints — mid Phase 1 slice verification"
tags: [handoff, blueprint, ai-provider, openai-compatible, ollama, silent-video, vision-probe, cheap-events]
status: complete
last_updated: 2026-09-08T10:57:41+0700
last_updated_by: Yogiswara Utama
type: feature_development
---

# Handoff: blueprint mid-flight — candidate detection on OpenAI-compatible endpoints (Phase 1 of 6, inside Step 6.2)

## Task(s)

Executing `/skill:blueprint` with input `.rpiv/artifacts/research/2026-09-08_09-13-45_candidate-detection-openai-compat.md` (research verified at HEAD 51c1861). The skill turns that research into an implement-ready phased plan in `.rpiv/artifacts/plans/2026-09-08_10-21-18_candidate-detection-openai-compat.md`.

Status of blueprint steps:

- Steps 1-3: COMPLETE. Research artifact read fully; all cited source regions read directly into context (ai_provider.py full, main.py regions, log_view.py full, cloud/semantic_analyzer.py full, tests/test_no_double_route.py full, gemini_worker.py + app.py regions, cheap_events extract function); one codebase-pattern-finder dispatched (returned the llm_client conditional-forwarding idiom + recording-fake test patterns); dimension sweep done; check_index_coverage clean on all 13 cited paths.
- Step 4 COMPLETE. Four directional confirms answered (all "Recommended" options taken) + one genuine ambiguity resolved. First ask came back "i dont understand, re explain" ×3 — re-asked in button-press terms, then landed. One genuine ambiguity asked one-at-a-time and resolved. Advisor consulted (user explicitly delegated the param-order question to it). All 10 decisions are recorded in the artifact's `## Decisions` (D1-D10).
- Design summary confirmed (Proceed). Decomposition approved (6 slices).
- Step 5.4 DONE: skeleton artifact written (24 KB) — all prose sections filled, Phase 1-6 headings with EMPTY code fences and empty Success Criteria, frontmatter `status: in-progress`, `unresolved_phase_count: 6`.
- Step 6 Phase 1: 6.1 code drafted IN CONVERSATION ONLY (not yet written to artifact). 6.2 PARTIAL: `slice-overlap.mjs "Phase 1"` ran → `overlapping: 0` (no priors). **slice-verifier NOT yet dispatched — that is the exact resume point.**

The four user-facing fixes (FR1 silent-video provider routing, FR2 vision capability probe, FR3 param passthrough, FR4 cheap events ≤120s) + docs sync decompose into 6 phases (see artifact frontmatter `phases`).

## Critical References

- `.rpiv/artifacts/plans/2026-09-08_10-21-18_candidate-detection-openai-compat.md` — the living plan artifact; source of truth for decisions D1-D10, phase layout, verification notes
- `.rpiv/artifacts/research/2026-09-08_09-13-45_candidate-detection-openai-compat.md` — parent research; its "Verified line-number corrections" table supersedes the FRD's cites
- `C:\Users\utama\.pi\agent\npm\node_modules\@juicesharp\rpiv-pi\skills\blueprint\SKILL.md` — the skill contract being executed (Steps 6.2→6.3→6.4 per slice; never fill code fences at Step 7)

## Recent changes

No source-file changes (blueprint never edits source). Artifact writes only:

- `.rpiv/artifacts/plans/2026-09-08_10-21-18_candidate-detection-openai-compat.md` — created (skeleton, all phases empty-fenced)

## Learnings

- **Phase 1 code is already designed and drafted in the dead session's context — regenerate from these pins** (all grounded in the artifact's Decisions):
  - ai_provider.py: add module-level `_sanitize_temperature` / `_sanitize_max_tokens` / `_sanitize_timeout` (garbage/empty/None → None; bounds temp 0-2, tokens 1-8192, timeout 5-600); `AIProvider.__init__` gains `temperature`/`max_tokens`/`timeout` Optional slots stored sanitized; both constructor signatures pass them via `super().__init__`; `OpenAICompatibleProvider.generate_content` params dict becomes precedence chain (per-call kwarg, else stored, else frozen 0.7/4096; explicit-None kwarg counts as unset; `timeout` key only present when a value exists — today's shape at `ai_provider.py:327-329`); factory (`ai_provider.py:450-474`) forwards the three kwargs to both branches, `base_url` handling unchanged.
  - tests/test_ai_provider.py (NEW): sanitize bounds tests; factory forwarding with stubbed `__init__` (avoids lazy SDK imports); request-precedence tests via a fake `openai` module injected through `monkeypatch.setitem(sys.modules, "openai", fake)` where `OpenAI(api_key=, base_url=)` returns a recorder client (`chat.completions.create(**params)` → SimpleNamespace response with `content='{"ok": true}'`); Gemini never-applies pin via `inspect.getsource(GeminiProvider.generate_content)` asserting no `kwargs` reads (chosen over a fake-genai behavioral test because `gemini_worker._calculate_cost_analysis` response shape is unverified).
- **New discovery from this session (already approved as D4)**: Gemini silent path uploads the video, polls to ACTIVE, then calls `contents=[prompt]` — the file is never attached (`main.py:2943-2948`). Developer chose to FIX it (`contents=[file_upload, prompt]`) in the Phase 4 rewrite, accepting ~300 video tokens/second.
- **Probe subtlety (D5, must survive into Phase 2)**: `OpenAICompatibleProvider.generate_content` unconditionally `json.loads` the reply (`ai_provider.py:330-341`) — a success-path probe must demand a JSON reply ("Reply with exactly this JSON: {\"ok\": true}") or a plain "yes" text reply raises `AIProviderError("Failed to parse JSON...")` and misclassifies a vision-capable model. Image-reject substring `"does not support image"` catches both verified wire texts (Ollama Cloud 400 `this model does not support image input`, LM Studio `does not support images`). Classification transient-first using the exact token list `ai_provider.py:420-424`. Cache: module dict `_VISION_PROBE_CACHE` keyed `(provider_type, model_name, base_url, api_key)`. Probe: max_tokens=16, timeout=15, 8x8 data-URL image.
- **User interaction**: first Step-4 batch (4 directional questions) got "i dont understand, re explain" x3 + "ask advisor" x1. Re-asks in button-press terms ("what happens when you submit a silent video with a text-only model picked in the provider card") landed all four on Recommended. Draft checkpoint questions concrete-first; jargon never gets sent. The remaining genuine ambiguity (contents defect) was asked the same way and landed.
- Codebase-memory graph: project name `openshorts` is canonical for `H:/fork/openshorts` (a stale duplicate `H-fork-openshorts` also exists). Graph freshness: `metadata_changed` vs live files — plan cites rest on direct live reads, which were done. `dashboard/src/App.jsx` is parse_partial (ranges 1800, 2047, 2461); untouched by this plan.
- Test-suite baseline on this Windows machine: 889 passed / ~10 pre-existing env failures (cp1252 reads missing `encoding="utf-8"`, AsyncMock session issues). Acceptance = no NEW failures. No docker compose here.
- STE strict mode active for technical prose; `ask_user_question` payloads ASCII-clean (no raw `\r`), headers ≤16 chars.

## Artifacts

- `.rpiv/artifacts/plans/2026-09-08_10-21-18_candidate-detection-openai-compat.md` — skeleton plan; Phase 1-6 headings + empty fences; Decisions D1-D10; Verification Notes; Plan History all pending
- `.rpiv/artifacts/research/2026-09-08_09-13-45_candidate-detection-openai-compat.md` — input research (read fully)
- `.rpiv/artifacts/discover/2026-09-08_08-39-20_candidate-detection-openai-compat.md` — source FRD (referenced, not re-read)

## Action Items & Next Steps

Resume inside `/skill:blueprint` at Step 6.2 for Phase 1:

1. Re-read the plan artifact + research artifact fully; re-read `ai_provider.py` and `tests/test_no_double_route.py` fully (context was lost).
2. Regenerate Phase 1 code per the Learnings spec + artifact Decisions D1/D5/D7/D10 (sanitize helpers + constructor storage + precedence chain + factory forwarding; NEW tests/test_ai_provider.py).
3. Dispatch `slice-verifier` (`artifact_path` = plan path, `slice_id: Phase 1`, inline code + Success Criteria, `overlapping_priors` omitted — helper reported 0 overlapping priors).
4. Step 6.3: present condensed slice review (summary/signatures/key blocks + mandatory Fit line) via `ask_user_question`; on Approve run 6.4 (Edit Phase 1 fences + Success Criteria, Plan History entry, decrement `unresolved_phase_count` to 5).
5. Phases 2-6 same loop (6.1 → 6.2 slice-overlap + slice-verifier → 6.3 → 6.4). Phase 2/3/4/5 code shapes are described in the artifact's Decisions + Phase Overviews; deep-dive reads already done for every touched region.
6. Step 7 finalize (verify fences filled, rebuild `phases:` frontmatter, `status: in-review`), Step 8 (parallel artifact-code-reviewer + artifact-coverage-reviewer, persist merged table), Step 9 (developer triage, flip `status: ready`), Step 10 handoff message with `/skill:implement` next step.

## Other Notes

- `theo_mode` is the committed execution mode for this phase (bottleneck: provider-routing contracts without regressing the 889-test Gemini baseline).
- Phase 1 slice-overlap output (already computed, reuse): `slice_id: Phase 1; current_files: ai_provider.py, tests/test_ai_provider.py; prior_units: 0; overlapping: 0`.
- Windows PowerShell box: prefer `python -m pytest ... -q` scoped per phase (write-scope rule in the skill); fixture files written with `encoding="utf-8"`.
- Live acceptance endpoints (Ollama Cloud + one local server) run OUTSIDE this plan (FRD live-run gates) — listed in the artifact's Verification Notes as manual gates.
- The user speaks plain language; all remaining 6.3 micro-checkpoints should follow the button-press framing that worked ("silent video", "screenshots", "provider card", "job log").