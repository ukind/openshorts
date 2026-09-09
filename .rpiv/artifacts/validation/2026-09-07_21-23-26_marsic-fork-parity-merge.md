---
date: 2026-09-07T21:23:26+0700
author: Yogiswara Utama
commit: d450d08
branch: merge/marsic-parity
repository: openshorts
topic: "Validation of Marsic1/openshorts fork parity merge — phased implementation plan"
status: ready
verdict: pass
parent: ".rpiv/artifacts/plans/2026-09-06_14-41-23_marsic-fork-parity-merge.md"
tags: [validation, merge, llm-client, ai-provider, gate-predicate, stage-ownership, mcp, dashboard, infra, remotion, tests]
last_updated: 2026-09-07T21:23:26+0700
---

## Validation Report: Marsic1/openshorts fork parity merge — phased implementation plan

Validation ran against the landed merge commit `d450d08` (parents `de92009` + `b1f5350`), branch `merge/marsic-parity`. Working tree clean except the two by-design untracked dirs (`.rpiv/`, `.dirac-cache/`). Every phase's automated gate was re-executed on this machine; the three whole-plan failures were attributed per the pre-existing-debt rule (byte-identity to base + actual rerun at base) and ruled non-blocking plan deviations. No `--goal`, `--baseline`, `--scope`, or `--acceptance` inputs were provided; the plan carries no `risks:` frontmatter array.

### Implementation Status

- ✓ Phase 1: Merge mechanics + app.py LLM seam (foundation) — Fully implemented
- ✓ Phase 2: main.py stage-ownership flip — Fully implemented
- ✓ Phase 3: Boundary contract test + CI pytest-asyncio — Fully implemented (game_profiles gate deviates, see Findings)
- ✓ Phase 4: Dashboard core seam — Fully implemented
- ✓ Phase 5: Dashboard nav/mounts + AuthContext pin + deps — Fully implemented
- ✓ Phase 6: Infra — compose flip + env union + requirements verify — Fully implemented
- ✓ Phase 7: Remotion union manifest + lockfiles — Fully implemented
- ✓ Phase 8: Docs + finalize — Fully implemented (merge commit landed, `main` untouched)

### Automated Verification Results

Static and unit gates — all green:

- ✓ Git state (Phase 8): single merge commit; `git rev-list --no-merges --first-parent HEAD~1..HEAD` empty; `marsic/main` is an ancestor of HEAD; merge message carries exactly 7 policy bullets; parents `de92009` + `b1f5350`; `main` still at `de92009`; porcelain shows only the two by-design untracked dirs.
- ✓ Deleted-module tokens (Phases 1-2, 8): `llm_backend` → 0 matches repo-wide in `*.py`/`*.md` (`.rpiv` excluded), in `app.py`/`mcp_server.py`/`tests/test_llm_endpoints.py`, in `tests/test_no_double_route.py`, and in `grep -rn` over root `*.py`, `cloud/`, `tests/`; README/CLAUDE stale-token grep (`LLM_SCORE_BATCH|OLLAMA_CONTEXT_LENGTH|LLM_TIMEOUT|LLM_PROVIDER|score_batch|llama3.1|_run_gemini_stage|bisect`) → 0.
- ✓ app.py seam (Phase 1): `ai_backend_available` count 2 (definition + job gate); `resolve_llm` count 10 (≥ 8); all 11 `await resolve_gemini` bindings unpack the 2-tuple (no raw scalar binding survives).
- ✓ main.py flip (Phase 2): `llm_client` appears on exactly 2 lines, both inside `get_visual_clips`' no-key branch (function-local import + gate); `score_batch_size`/`LLM_SCORE_BATCH` → 0; `import app` and `import main` both exit 0 (venv).
- ✓ CI (Phase 3): `pytest-asyncio` appears exactly once in `.github/workflows/ci.yml` (install line).
- ✓ Dashboard greps (Phases 4-5): `lib/analytics` → 0; `setItem('gemini_key'` → 0 across App.jsx + components; `getItem('gemini_key')` exactly 1 in App.jsx (migration adopt), 0 in ResultCard.jsx; `llmHeaders(providerCfg)` = 1; `needsAiBackend` = 11 (≥ 5); CreateEditProfileModal: no `getItem('ai_provider')`, `geminiApiKey` × 3 (≥ 3); `llmConfig={providerCfg}` = 2; history union rule `(!billingEnabled || isSignedIn)` = 1; `id: 'history'` = 1, their 3 tabs = 3; `tutorialLock && id !== 'dashboard'` = 2; `voiceoverMounted &&` = 1, `<HistoryTab onReopenProject` = 1, `<GameProfilesPage` = 1, `<VoiceStylePresetsPage` = 1; AuthContext occurrences: `localLlm` × 2, `llmConfigured` × 4, `lib/panel` import = 1.
- ✓ package.json / lockfiles (Phases 5, 7): dashboard `zod ^4.3.6` + `@remotion/animated-emoji` present, `dashboard/package-lock.json` untracked; `render-service` exact `zod "4.3.6"`; remotion union `zod ^4.3.6` + `@remotion/animated-emoji 4.0.447`; both lockfiles tracked (2); Anton font present; porcelain clean over `remotion/` + `render-service/`; `npm ls zod --depth=0`: direct `zod@4.3.6` in both remotion and dashboard (no ^3 downgrade pulled anywhere).
- ✓ Infra (Phase 6): compose greps — `gpus: all` 0, `NVIDIA_*` 0, `args:` 0, `required: false` 1, `hf-cache` 2, `TZ=Europe/Rome` 1; `.env.example` (verified against served file content — the bash safety net `secret.pattern.env-variant` blocks any shell line naming the path): forbidden tokens (`\bAI_API_KEY\b`, `LLM_SCORE_BATCH`, `LLM_TIMEOUT`, `OLLAMA_CONTEXT_LENGTH`, `LLM_PROVIDER`, `GAME_PROFILE_ID`, `USER_ID`) → 0; `LLM_MODEL_SAAS`, `^# GEMINI_API_KEY=`, `AI_TIMEOUT`, `DEEP_OPENAI_BASE_URL`, `ENABLE_CAPTION_EMOJIS`, `^# TARGET_CLIPS=`, `VOICEOVER_PRESETS_DIR`, `PAID_PROXY_DAILY_MB`, `HOOK_GROUNDING_WIDTH` each exactly 1; content matches the plan §2 fence; `requirements.txt` has `openai==3.3.0`; Dockerfile `ARG GPU=0` × 2; `git diff marsic/main -- requirements.txt Dockerfile` empty (verify rows byte-identical); `git diff marsic/main -- render-service/package.json render-service/Dockerfile render-service/src` empty.
- ✓ Docs (Phase 8): CLAUDE.md `ai_backend_available` × 2, `Stage ownership` = 1, `owns the stage` = 1, `phase 1` → 0; README `AI_PROVIDER` × 9, `ukind/openshorts` = 1, `Marsic1/openshorts.git` = 0, heading `### 6. Run without a Google key` present (R1/R8 anchor slug resolves).
- ✓ Targeted pytest (Phases 1-3, undeselected on the finished tree): `tests/test_llm_endpoints.py tests/test_llm_client.py tests/test_gemini_retry.py tests/test_gemini_block_split.py tests/test_no_double_route.py` — **102 passed**, 0 failed.
- ✓ Pinned count re-derivation (plan manual step 1): `startswith("LLM_")` in app.py = 1 (the billing env-strip sweep; the satellite forward writes three explicit keys).

Heavy gates — two deviate (see Findings), rest green:

- ✗→✓ Full suite `pytest -q` (Phase 8): **889 passed / 10 failed** — all 10 attributed pre-existing at base and ruled non-blocking plan deviations (evidence below). Not merge-introduced.
- ✓ Dashboard `npm run build` exit 0; ✗→✓ `npm run lint` exits 1 with exactly the recorded 35 problems (32 errors, 3 warnings), all in 6 files byte-identical to `marsic/main` — ruled pre-existing fork debt, non-blocking; scoped eslint over every phase-owned file (App.jsx, CreateEditProfileModal.jsx, ResultCard.jsx, AuthContext.jsx) exits 0.
- ✓ Remotion: `npm ci --legacy-peer-deps` exit 0, `npm run build` (tsc --noEmit) exit 0; render-service `npm ci` (plain, their Dockerfile parity) exit 0; no lockfile drift after either ci.
- ✗→✓ `docker compose config` (Phases 6, 8): unrunnable on this machine — Docker CLI 28.3.1 ships no compose plugin (`docker: unknown command: docker compose`). PyYAML structural substitute passes (3 services backend/frontend/renderer, all with build; backend `env_file` `required: false`; no `gpus` key anywhere; `hf-cache` volume present). Ruled unmeasurable-in-environment; the exact command is routed to the deploy box as manual homework, per the plan's own implement note.

Failure attribution evidence (Step 2.6, rerun-at-base, not just byte-identity):

- `test_generation_controls` ×3 + `test_subtitles` ×2 — reproduce identically in a clean throwaway worktree of `de92009` (plain `main`): 5 failed / 38 passed, all cp1252 `UnicodeDecodeError` (tests read source files without `encoding="utf-8"` on a Windows-locale interpreter). Both test files are byte-identical to `de92009`. Pre-existing at base.
- `test_game_profiles` + `test_semantic_analyzer` — reproduce at `b1f5350` (their fork, as-shipped): 8 failed / 10 passed (AsyncMock `session.begin()` yields a plain coroutine under `async with`; cv2 frame extraction returns 0 frames). Both files byte-identical to `marsic/main`, and the plan pins them byte-identical ("No changes to their 10 new test files"), so the Phase 8 `pytest -q` exit 0 criterion is unachievable as written by the plan itself. Their fork's CI was red on these files regardless.

### Code Review Findings

#### Matches Plan:

- app.py: the merged seam is exactly the E1-E11 shape — `resolve_llm`, `ai_backend_available` (2 sites), `LLM_ENDPOINT_HINT`, `_env_llm_config`, dual-key `/api/config`, job gate, `LLM_*` env forward + billing strip, satellite heads with `llm_config` threading and 2-tuple `resolve_gemini` unpacks at every site.
- main.py: stage-ownership flip complete — no module-level llm_client, silent-video gate retargeted inside `get_visual_clips`, no `score_batch_size`/`LLM_SCORE_BATCH`.
- tests/test_no_double_route.py: boundary contract green (2 video + 3 satellite + 1 app-layer env); satellite dispatch through `llm_client` is additionally pinned live by the three passing `TestSatelliteSide` tests (covers plan manual-testing step 2).
- Dashboard: encrypted key lifecycle, `needsAiBackend` gate, unified header spread, `LlmProviderCard` mount, history-nav union rule, AuthContext pin — all count gates exact; build green; phase-owned files lint-clean.
- Infra/remotion/docs: every structural grep exact; verify rows (`requirements.txt`, `Dockerfile`, `render-service/*`) byte-identical to their side, as the plan requires; README/CLAUDE token-free of the deleted system.
- Merge commit shape: one commit, both parents, 7-bullet per-file policy list, `main` untouched — the plan's execution model honored exactly.

#### Deviations from Plan:

All four are *plan* deviations (criteria unachievable as written), user-approved or environment-bound during implement, ruled non-blocking:

1. Phase 8 `pytest -q` exit 0 — **not met as written** (889/10). The 10 failures pre-date the merge on both parent lines (rerun evidence above); the 5 their-side failing files are pinned byte-identical by the plan's own "What We're NOT Doing", making the criterion self-contradictory. The implement session additionally fixed the 3 real merge gaps the first suite run surfaced (`game_profiles` in `USER_OWNED_TABLES`, timestamp-agnostic log assertion, `game_profile_json="{}"` stub) inside the merge commit, per user-approved write-set override.
2. Phase 3 `pytest tests/test_game_profiles.py -q` green — **not met as written** (standalone: 6 failed / 1 passed). Their tests inject AsyncMock sessions that fail under any pytest-asyncio version; never passed in their fork. User-approved "Keep line + note" during implement.
3. Phases 4/5/8 `npm run lint` exit 0 — **not met as written** (exit 1, 35 problems in 6 files). All 6 failing files are byte-identical to `marsic/main` and outside every phase's write-set; their fork never lints (CI runs pytest only). Advisor-endorsed clean-my-files-and-note precedent recorded in the plan; scoped eslint over all phase-owned files exits 0.
4. Phases 6/8 `docker compose config` exit 0 — **unmeasurable in this environment** (Docker CLI lacks the compose plugin; the plan's implement notes pre-authorized the substitute). Structural YAML substitute passes; exact command must run on the deploy box.

#### Pattern Conformance:

- ✓ `tests/test_no_double_route.py` follows the repo's counting-fake/canary test idiom (same shape as `test_llm_endpoints.py`'s fake installs); function-local `import llm_client` in main.py matches the satellite pattern (`thumbnail.py`, `saasshorts.py`, `layout_picker.py`).
- Acceptable variation, not a deviation: AuthContext's `localLlm` occurrences sit on one line (the plan's Phase 5 note already records why the line-count form of that gate can never reach 2 — occurrence count is 2, verified).

#### Potential Issues:

- `tests/test_game_profiles.py` is order-dependent: 3 failures in the full suite, 6 standalone. Post-merge fixes should be sized from the standalone run (all 6 stem from the same AsyncMock `session.begin()` pattern), not the suite run.

### Manual Testing Required:

1. Dashboard browser flows (Phase 4 — all open):
   - [ ] Self-host, empty storage: badge + banner + key modal with provider-coverage note
   - [ ] AI Provider triple saved → banner clears, `X-LLM-*` on `/api/process`, `target_clips` exactly once
   - [ ] Server `LLM_*`-only persona → banner clears; `localLlm`-only persona → banner clears
   - [ ] localStorage migration: `gemini_key` gone, `geminiKey_v1` present, legacy plaintext adopted
   - [ ] Game Profile "Analyze with AI" sends per-provider headers from props (devtools), no localStorage read
   - [ ] Cloud (billing) deploy: no `X-LLM-*` headers leak from a stale self-host blob
   - [ ] Their surfaces intact: provider selector, toggles, target-clips slider, game profiles, voiceover button
2. Nav/mounts (Phase 5 — open): billing signed-out History hidden; self-host History always on; tutorial lock (`#app?tutorial=1`); VoiceOver tab stays mounted, Game Profiles + Voice/Style Presets render. (zod resolution: verified this session — 4.3.6 direct in dashboard.)
3. Infra (Phase 6 — open): fresh clone without `.env`: `docker compose config` exit 0 and `docker compose up` starts; `cp .env.example .env` renders `TZ=Europe/Rome`. Run on a machine with the compose plugin (deploy box).
4. Remotion (Phase 7 — open): `docker compose build renderer` end-to-end; review `git diff marsic/main -- remotion/package-lock.json` stays minimal (post-ci drift check this session: clean). (zod direct dep: verified this session.)
5. Persona e2e + FRD acceptance (plan Testing Strategy): one Gemini-only job, one provider-only job (`AI_PROVIDER=openai`), one no-keys job → clean 400 `LLM_ENDPOINT_HINT`; one full video job end-to-end with Gemini and one with an OpenAI-compatible endpoint (needs real keys).
6. Post-acceptance: fast-forward `main` to `d450d08` (plan Phase 8 manual item marks this validate's job).

### Recommendations:

- Add `encoding="utf-8"` to the 5 cp1252-reading tests (`test_generation_controls` ×3, `test_subtitles` ×2) — they fail on plain `main` in any non-cp1252-strict source; post-merge follow-up, no merge impact.
- Post-merge: rewrite their 6 AsyncMock `session.begin()` fixtures in `tests/test_game_profiles.py` (and the 2 `test_semantic_analyzer` frame tests) so their async suite runs; the plan correctly refused to edit them inside the merge.
- Lint the 6 debt files (35 problems, all mechanical unused-vars/deps); route via `/skill:revise` or a small post-merge cleanup commit.
- Run the exact `docker compose config` gate on the deploy box before promoting this branch.
- Fast-forward `main` to `d450d08` once the manual items above are accepted.

Implementation is complete and validated; the merge commit is already landed on `merge/marsic-parity` and `main` remains untouched pending the fast-forward.
