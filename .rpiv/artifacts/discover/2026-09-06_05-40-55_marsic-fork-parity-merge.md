---
date: 2026-09-06T05:40:55+0700
author: Yogiswara Utama
commit: de92009
branch: main
repository: openshorts
topic: "Merge Marsic1/openshorts fork for full feature parity"
tags: [intent, frd, merge, llm-client, ai-provider, marsic-parity]
status: ready
last_updated: 2026-09-06T05:40:55+0700
last_updated_by: Yogiswara Utama
---

# FRD: Merge Marsic1/openshorts fork for full feature parity

## Summary

Merge the sibling fork `Marsic1/openshorts` (41 commits, 121 files: game profiles + Steam import, voiceover/TTS, subtitle editor, deep analysis with provider selectors, paid-proxy budget, legal/invoice pages, history, first-login tutorial) into `ukind/openshorts` via a merge commit on a feature branch. Only 6 files conflict textually; each is resolved by taking Marsic1's side as base and re-apping our smaller changes. The duplicated "OpenAI-compatible LLM backend" feature — built independently in both forks — is reconciled by stage ownership, not unification.

## Problem & Intent

Developer's words: "i just discover another fork of my current project (my project is also fork from the original) … it have massive additional feature. my idea is i wanna do pull that to my current branch. and it will conflict. and i will resolved it one by one. is this approach sound ? as my current project also has changes from my side. i wanna keep my idea and their idea."

Success chosen: **Full feature parity** — "Your app can do everything Marsic1's fork does AND your own changes keep working." The conflict-by-conflict resolution is just the cost of getting there. The original question ("is this approach sound?") is answered by probe evidence: yes, with one modification — the real work is not the 6 visible conflicts but one invisible semantic collision (two LLM backends) that git will not flag.

## Goals

- All 41 Marsic1 commits' features present and functional in our `main`, each behind its own env/config switch (merged ≠ enabled).
- Our 15 commits' work intact: AI Provider settings card (`llm_client.py`), thumbnail/SaaS/layout-picker LLM routing, MCP BYOK header forwarding, native dev scripts (`dev.bat`/`dev.sh`), `panel.js` rename, Remotion 4.0.447 pin, AAC 48 kHz fix, provider-only job fix.
- One coherent repo: one job-start gate, zero double-routed LLM stages.
- Future parity pulls from Marsic1 stay cheap (Marsic1 is an active upstream — 41 commits in ~1 week).

## Non-Goals

- Unifying the two AI systems into one transport/client this merge (advisor-rejected: turns a merge into a refactor and maximizes future conflict surface).
- Adapter layer (`ai_provider` delegating transport to `llm_client`) — post-merge follow-up only.
- Enabling new features in production (proxy billing spends money; qwen-tts wants a GPU).
- Rebasing or rewriting our pushed history.
- Bidirectional sync — one-way parity pulls only.
- Long-term remote management tooling — the developer drives pulls ("use my git").

## Functional Requirements

1. The system SHALL merge `marsic/main` (b1f5350) into branch `merge/marsic-parity` off `main` (de92009) as a merge commit; `main` fast-forwards only after verification passes.
2. The system SHALL resolve the 6 textual conflicts (`app.py`, `main.py`, `dashboard/src/App.jsx`, `.env.example`, `CLAUDE.md`) by taking Marsic1's version as base per file and re-applying our smaller changes on top.
3. `remotion/package.json` SHALL take Marsic1's manifest (identical 4.0.447 pins + `@remotion/animated-emoji`); both lockfiles deleted, one `npm install` regenerates.
4. The merge SHALL delete `llm_backend.py` (Marsic1's production-dead third LLM system; only caller chain `_run_stage_split` has no production callers) and port its tests into a boundary-contract test.
5. A single gate predicate `ai_backend_available()` (Gemini key OR `llm_client` configured OR per-job provider set) SHALL replace both forks' separate job-start gate fixes, and SHALL be used by both the job-launch path and `/api/config`. Ollama Cloud and any OpenAI-compatible base URL count as "endpoint".
6. Stage ownership SHALL be enforced: `ai_provider.py` owns video-pipeline stages (clip score/detail, VOD metadata/chunks, deep analysis, transcript enhance/emoji); `llm_client.py` owns satellite text stages (thumbnail titles/concepts, SaaS analyze/scripts, layout picker) + MCP BYOK headers. Our duplicate clip score/detail routes in `main.py` SHALL be deleted as superseded.
7. `docker-compose.yml` SHALL default to `GPU: 0` with no `gpus: all` line; GPU stays available via `--build-arg GPU=1` on a GPU host.
8. In `app.py` the merge SHALL re-apply: `X-LLM-*` request-header handling (~lines 162–166), `LLM_*` env forwarding to job subprocess (~2322–2324), and the gate swap to the unified predicate.
9. `CLAUDE.md` and `.env.example` SHALL document the stage-ownership boundary, the env namespace split (ours `LLM_*`; theirs `AI_PROVIDER`/`OPENAI_*`/`DEEP_*`), and the standing rule "the side whose base you take owns the stage".
10. All Marsic1 features SHALL land config-gated: game profiles, Steam auto-import, voiceover/TTS, subtitle editor + presets, deep analysis + per-job/deep provider selectors, proxy daily budget, legal/invoices/consent pages, history, tutorial, UGC gallery fixes.
11. No permanent `marsic` git remote SHALL be added; the already-fetched `refs/remotes/marsic/main` suffices for this merge.

## Non-Functional Requirements

- **Performance**: no hot-path regression; the merge adds features but does not alter pipeline concurrency (`MAX_CONCURRENT_JOBS`).
- **Security**: env namespaces stay disjoint so no key leaks across systems; BYOK `X-LLM-*` headers forwarded only for our satellite stages; Marsic1's legal/AI-act disclosures land as-is.
- **UX / Accessibility**: two AI settings surfaces coexist this merge (their per-job/per-profile selectors + our AI Provider card) — documented in CLAUDE.md, cosmetic debt accepted.
- **Reliability**: no pipeline stage may fire two LLM calls (double-routing is a correctness bug, pinned by test); both forks' test suites green post-merge.

## Constraints & Assumptions

- Merge-base confirmed: `ad8ab59` (Aug 29 2026). Ours: 15 commits / 30 files. Theirs: 41 commits / 121 files.
- Marsic1 rewrote the hot files 10–30× more than us (`app.py` +2368 vs +172; `main.py` +1846 vs +63; `App.jsx` +693 vs +126) — hence take-theirs-then-reapply.
- Production deploys via Coolify on a CPU VPS (Traefik front) — no NVIDIA runtime available.
- Development happens on Windows (`dev.bat`) — native dev scripts must survive the merge.
- Marsic1's fork already absorbed a third upstream (mutonby, squashed in `0f326fd`) — assumed append-only history; if they rewrite, re-derive the merge base.
- A read-only worktree of their tree exists at `../openshorts-marsic` for reference; remove after merge.
- Assumption: their new tests pass CPU-only (their CI imports lazily, commit `7803065`).

## Acceptance Criteria

- [ ] `git log --oneline marsic/main` fully contained in `merge/marsic-parity` history; merge commit present; `main` fast-forwarded after verification.
- [ ] `grep -rn "llm_backend" *.py cloud/ tests/` returns zero matches (module deleted, tests ported).
- [ ] Boundary-contract test passes: with `AI_PROVIDER=openai` AND `LLM_*` all configured, each pipeline stage routes through exactly one system.
- [ ] A job starts with (a) Gemini key only, (b) `llm_client` config only, (c) per-job provider set — and fails with a clear error when none is present.
- [ ] `pytest` exits 0 including their new files (`test_game_profiles`, `test_game_profile_update_http`, `test_local_game_profiles`, `test_semantic_analyzer`, `test_semantic_integration`, `test_semantic_scoring`, `test_hook_grounding`, `test_proxy_ledger`, `test_source_access`) and ours (`test_llm_client`, `test_llm_endpoints`).
- [ ] `cd dashboard && npm run build && npm run lint` exit 0.
- [ ] `docker compose config` validates with `GPU` defaulting to CPU and no `gpus: all`.
- [ ] `cd remotion && npm ci` succeeds against the regenerated lockfile.
- [ ] Spot-check call sites still route via `llm_client`: `thumbnail.py:263`, `saasshorts.py:362`, `layout_picker.py:146`.
- [ ] CLAUDE.md contains the stage-ownership boundary and the standing rule.
- [ ] One full video job completes end-to-end with Gemini; one with an OpenAI-compatible endpoint (Ollama Cloud acceptable).

## Recommended Approach

Merge commit of `marsic/main` into `merge/marsic-parity`; per conflicted file take-theirs-then-reapply-ours; delete `llm_backend.py`; replace both job gates with one `ai_backend_available()` predicate; enforce stage ownership between `ai_provider.py` (video pipeline) and `llm_client.py` (satellite text stages + BYOK); flip compose to CPU default; regenerate the Remotion lockfile from their manifest.

## Decisions

### Conflict-resolution tactic
**Question**: Per conflicted file (6 total), whose version is the base?
**Recommended**: Take theirs, re-apply our smaller changes on top.
**Chosen**: Take theirs, re-apply ours.
**Rationale**: evidence: `git merge-tree` dry run + diffstat (their hunks 10–30× ours) + confirmed.

### Delete Marsic1's dead llm_backend.py
**Question**: Their `llm_backend.py` is a third LLM system only reachable from tests — throw it away during the merge?
**Recommended**: Delete + port its tests to the surviving layer.
**Chosen**: Delete it.
**Rationale**: evidence: analyzer verified only caller chain `_run_stage_split` has no production callers; their features use `ai_provider.py`.

### Unified job-start gate
**Question**: One rule for starting a job (Gemini key OR any configured OpenAI-compatible endpoint — Ollama Cloud included)?
**Recommended**: Single predicate `ai_backend_available()`.
**Chosen**: Gemini OR any endpoint.
**Rationale**: Both forks fixed the same crash independently (ours `app.py:2317` `if api_key:`; theirs `app.py:2323` llm_backend-accepting); one predicate prevents the crash returning via either path. User confirmed Ollama Cloud must work.

### Parity scope
**Question**: Full parity = all 41 commits' features land, config-gated (proxy spend and GPU stay off until enabled)?
**Recommended**: All features, config-gated.
**Chosen**: All features, config-gated.
**Rationale**: Developer's stated intent is full parity; merged code ≠ enabled in prod.

### LLM reconciliation boundary (advisor-delegated)
**Question**: How do the two surviving AI systems coexist?
**Recommended**: Divide by stage (developer transferred judgment to advisor).
**Chosen**: Divide by stage, advisor-hardened ("A+"): `ai_provider.py` owns video-pipeline stages; `llm_client.py` owns satellite text stages (thumbnail/SaaS/layout) + MCP BYOK. Single gate predicate; no-double-route contract test; boundary written into CLAUDE.md; env namespaces disjoint after `llm_backend.py` deletion; short re-apply list in `app.py`.
**Rationale**: advisor verdict — Marsic1 is an active upstream; every future pull re-conflicts refactored seams, so keep our footprint in files they barely touch. Deletion-heavy resolution in `main.py`. Double-routing is the correctness bug to defend; two config surfaces are cosmetic debt.

### Merge mechanics
**Question**: How does the merge physically land?
**Recommended**: Merge commit on feature branch `merge/marsic-parity`, verify, fast-forward `main`.
**Chosen**: Merge commit on branch.
**Rationale**: Preserves both histories; conflicts resolved once in 3-way context; rebase would replay 15 commits individually onto a restructured base and rewrite pushed history.

### GPU default in compose
**Question**: Their compose ships `GPU: "1"` + `gpus: all` — what does the merged default do on your CPU Coolify host?
**Recommended**: CPU default, GPU opt-in via build-arg.
**Chosen**: CPU default, GPU opt-in.
**Rationale**: Production is Coolify CPU VPS; Dockerfile already supports `ARG GPU=0` with graceful voiceover degradation.

### Remotion conflict
**Question**: `package.json`/lock conflict (add/add) — take their manifest and regenerate the lock?
**Recommended**: Take theirs + `npm install` regen.
**Chosen**: Take theirs + regen lock.
**Rationale**: evidence: both sides independently pin exactly 4.0.447; their manifest is a superset (+`@remotion/animated-emoji`).

### Marsic remote persistence
**Question**: Keep Marsic1 configured as a permanent git remote?
**Recommended**: Keep as remote for future parity pulls.
**Chosen**: "use my git" — no permanent remote; developer drives future pulls through their own workflow. Already-fetched `marsic/main` ref used for this merge.
**Rationale**: Developer's explicit custom answer.

## Open Questions

None — no deferred items.

## Suggested Follow-ups

- Adapter step (post-merge, reversible): `ai_provider` optionally delegating transport to `llm_client` for unified cost accounting — advisor Option C.
- Unify `/api/config` keys (`llmConfigured` vs `localLlm`) and the two AI settings surfaces into one UX — cosmetic, next merge cycle.
- If Marsic1 later routes thumbnail/SaaS through `ai_provider`, cede those stages per the standing rule (goes into CLAUDE.md).
- Their fork absorbed upstream mutonby squashed (`0f326fd`) — if you ever track mutonby directly, expect duplicate-content surprises.
- Remove the reference worktree after merge: `git worktree remove ../openshorts-marsic`.
- Their `25222f5` gate test path references `llm_backend.active()` — the ported test must cover the unified predicate instead.

## References

- Input: developer free-text via `/skill:discover` (this session).
- https://github.com/Marsic1/openshorts @ `b1f5350` (fetched as `marsic/main`).
- Session probe (2026-09-06): `git merge-tree` dry run; diffstat vs base `ad8ab59`; codebase-analyzer report comparing `llm_client.py` vs `ai_provider.py`/`llm_backend.py` across both checkouts.
- Reference worktree: `../openshorts-marsic` (their tree at `b1f5350`).
