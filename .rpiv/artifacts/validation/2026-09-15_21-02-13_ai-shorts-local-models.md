---
template_version: 1
date: 2026-09-15T21:02:13+0700
author: Yogiswara Utama
commit: 73f670a
branch: main
repository: openshorts
topic: "Validation of AI Shorts local video_mode arm - per-stage local adapters"
status: ready
verdict: pass
parent: ".rpiv/artifacts/plans/2026-09-15_13-46-14_ai-shorts-local-models.md"
tags: [validation, saasshorts, comfyui, tts, video-mode, local-models]
last_updated: 2026-09-15T21:02:13+0700
---

## Validation Report: AI Shorts local video_mode arm - per-stage local adapters

Validated against the working tree at HEAD 73f670a. The implementation is uncommitted, so the run's delta is the full dirty set: six modified files plus eight new files. Every dirty file belongs to the plan's declared write-set. Two unrelated untracked files (`.rlm/models_cache.json`, `pi-session-2026-09-10T23-22-15-789Z_*.html`) predate this run and are recorded under Potential Issues for visibility only. No `--goal`, `--baseline`, `--scope`, or `--acceptance` files were provided. The plan carries no `risks:` frontmatter.

### Implementation Status

- ✓ Phase 1: Local clients + adapters (foundation) — Fully implemented
- ✓ Phase 2: Dispatch + gates + validation — Fully implemented
- ✓ Phase 3: Wizard + gallery surface — Fully implemented
- ✓ Phase 4: GPU-host calibration + docs — Fully implemented on its automated scope (docs, env block, finalized lipsync template). The seven GPU-host manual criteria remain open by design. See Manual Testing Required.

### Automated Verification Results

- ✓ Phase 1/2/4 suite: `py -3.12 -m pytest tests/test_saasshorts_local.py -q` — 36 passed in 4.04s. Note: the bare `python` on PATH is an unrelated venv without pytest, so the plan's `python` spelling resolves here to the `py -3.12` interpreter this repo tests with.
- ✓ Templates parse: `python -c "import json,glob; [json.load(open(p)) for p in glob.glob('workflows/*.json')]"` — exit 0.
- ✓ Cycle guard holds: `import saasshorts_local; assert 'saasshorts' not in sys.modules` — exit 0.
- ✓ Lipsync template contract (Phase 4 validator) — exit 0. String upload refs on `load_video` and `load_audio`, exactly one save node (`save` = `VHS_VideoCombine`).
- ✓ i2v template anchors — exit 0. Nodes 5/9/13 present, node 8 is `Wan22ImageToVideoLatent`.
- ✓ FR6 error copy: `grep -c "service:" comfyui_client.py tts_client.py` — 13 + 4 = 17 (plan wants >= 8).
- ✓ No poll deadline: `grep -n "timeout" comfyui_client.py` — only `_UPLOAD_TIMEOUT` (line 33) and `_DOWNLOAD_TIMEOUT` (line 209). No deadline inside `wait_for_output`. `test_poll_has_no_deadline` proves the loop keeps polling.
- ✓ Gates present: `video_mode != "local"` appears exactly 2 times in app.py (lines 6697, 8000). `Literal["premium", "lowcost", "local"]` appears exactly 2 times (lines 6647, 7982).
- ✓ Dispatch, serialization, cost, route, voices, SEO criteria — covered by the 36 passing tests: zero cloud calls under `video_mode="local"` (silent-spend guard), D7 interval ordering, premium/lowcost arms unchanged, `$0` cost table, 200/400/422 route matrix on both routes, actor-options `/videos/` URLs without S3, voices local branch with the TTS-down empty-plus-error shape, LOCAL badge surface on `/gallery` and `/video/{id}`, and the `_local_arm_startup_warnings` half-config probe.
- ✓ Dashboard lint: `cd dashboard && npm run lint` — exit 0 with zero warnings under `--max-warnings 0`.
- ✓ Dashboard build: `cd dashboard && npm run build` — built in 5.32s. Only the pre-existing chunk-size advisory remains.
- ✓ Env block: `COMFYUI_WORKFLOW_LIPSYNC` count 1 and `TTS_BASE_URL` count 1. The new block sits at `.env.example:119-133`, before the `# --- Proxy / ops ---` block now at line 135, matching the planned insertion point.
- ✓ README section: "AI Shorts fully local" appears 3 times (line 53 feature link, line 269 requirements link, line 414 section heading). The heading precedes `## Technical Pipeline` (line 444) as planned, and both links carry the correct GitHub anchor.
- ✓ No regressions detected — the cloud premium/lowcost paths stay byte-identical per TestDispatchMatrix, TestRouteModeMatrix, and TestVoicesLocalBranch.

### Code Review Findings

#### Matches Plan:

- saasshorts.py:26, 1353 — `import saasshorts_local` at the locked anchor. `video_mode` is read once via `config.get` with the planned comment.
- saasshorts.py:1386-1401 — D7 serialization verbatim, VRAM rationale comment included. The cloud `ThreadPoolExecutor` branch is preserved in the else-arm (Hunk 3 shape).
- saasshorts.py:1418, 1421 — the `_exists(talking_head)` D6 cache guard is re-emitted, followed by the three-way head dispatch (local / lowcost / premium).
- saasshorts.py:1461-1468 — the b-roll loop body picks `generate_broll_local` under the mode check while the pool and futures dict stay untouched (Hunk 5 shape).
- saasshorts.py:1510-1530 — the third `$0` cost branch carries the five planned keys. `cost["total"]` is re-emitted after the branch.
- app.py:6532-6558 — A2 import at the corrected anchor plus `_local_arm_startup_warnings` and the module-level warning loop (review finding C1 applied).
- app.py:6697-6712 — A4 mode-aware gate plus the local arm via `functools.partial`, returning `/videos/saas_actors_*` URLs with no S3 upload.
- app.py:8170-8196 — A7 voices local branch verbatim per D9, including the TTS-down `{voices: [], source: "local", error}` shape with HTTP 200.
- app.py:7829-7831, 7910 — A8a LOCAL badge in sky `#0ea5e9` and A8b `mode_label` with the third value.
- saasshorts_local.py — all planned symbols present, including `_loop_clip_to_duration`, `_mux_audio`, and the D6 retryable caches at lines 24-26. The ffprobe audio gate on the muxed head sits at line 255 before the final head write.
- workflows/latentsync_lipsync_api.json — finalized from the Phase 1 skeleton: `load_video` = `VHS_LoadVideo`, `load_audio` = `LoadAudio`, `lipsync` = `LatentSyncNode`, `save` = `VHS_VideoCombine`. The patch contract holds (string refs, exactly one save node).
- dashboard/src/components/SaaShortsTab.jsx — COST_PREVIEWS and COST_TOTALS tables at lines 20-46, the T2b local no-op guard at line 170, and the free-GPU button label at line 1262. All 8 `videoMode !== 'local'` guards are present, and every `X-Fal-Key`/`X-ElevenLabs-Key` occurrence (lines 351, 397, 1135) sits inside a mode-guarded spread.
- dashboard/src/components/UGCGallery.jsx:112, 204-210 — G1 modal eyebrow plus G2 card badge third branch in sky `#0ea5e9`, matching the server-side badge.
- tests/test_saasshorts_local.py — the fixture/install idiom mirrors tests/test_llm_client.py (MockTransport patched onto the module's client factory). The suite covers every planned class, including the ffmpeg-gated mux, head-atomicity, and Ken Burns tests. TestTemplates confirms exactly four templates.

#### Deviations from Plan:

- tests/test_saasshorts_local.py:25-29 — the mock fixtures mint a fresh `httpx.Client` per call over a shared `MockTransport`. The plan's listing shared one client instance. This is an improvement, not a gap: the modules call `with _client(base) as c`, which closes the client, so the plan's shared-instance lambda would fail on the second request. The Phase 1 lesson was applied during implementation and the file carries an explanatory comment.

#### Pattern Conformance:

- ✓ The client seam (`_client` factory patch point), the FR6 error copy pattern ("service: X at URL"), the MockTransport fixture idiom, and the pytest class layout follow the established tests/test_llm_client.py and saasshorts conventions.
- Minor observation: the wizard's local cost row names mirror the server cost keys in display prose. Acceptable variation, not a deviation.

#### Potential Issues:

- tests/test_saasshorts_local.py:332 — stale comment "# documented skeleton contract". The lipsync template was finalized in Phase 4 and is no longer a skeleton. The assertion itself still holds. Cosmetic only.
- The working tree carries two untracked files outside the plan's write-set: `.rlm/models_cache.json` and `pi-session-2026-09-10T23-22-15-789Z_01a08da1-076d-7447-8ce2-fecfac39fffc.html`. The session dump predates this run and no `--scope` verdict was provided for adjudication. Recorded for visibility, not treated as run writes.
- The Phase 4 i2v fallback knobs (length 121 to 81, then 480x832) are deliberately not applied. The shipped template stays at 704x1280 and 121 frames, which is correct per plan until RTX 3080 calibration demands otherwise.

### Manual Testing Required:

GPU host (RTX 3080) — the plan's Phase 4 manual criteria, still open:

1. LatentSync wrapper:
   - [ ] Wrapper pinned to 1.5 (commit 920c15ea); the LatentSync node is visible in ComfyUI and the checkpoint loads
   - [ ] Offline end-to-end with NO cloud keys in env: one local job completes actor → voice → head (wan → loop → lipsync → mux) → b-roll → composite with narration audible
   - [ ] Wan i2v per-clip seconds logged from the `[local]` lines and recorded in the PR description
2. Performance and VRAM:
   - [ ] LatentSync 1.5 vs 1.6 A/B on the 10 GB card; the pin stays 1.5 unless 1.6 is faster and artifact-clean
   - [ ] Steps 1-2 observed serialized (no OOM across actor → voice → head); b-roll fanout rides the ComfyUI queue
3. Wizard surface on the live stack:
   - [ ] TTS voice list renders in the wizard; a non-default voice is used by the job
   - [ ] Fallback knobs exercised only if calibration demands: length 121→81, then 480x832

Phase 3 also lists four manual wizard checks (Local card with no key warnings, TTS-down picker copy, gallery LOCAL badge on a shared job, cloud modes unchanged). The server side of each is covered by the route and SEO tests above; the visual pass needs the live local stack.

### Recommendations:

- Reword the stale "documented skeleton contract" comment at tests/test_saasshorts_local.py:332 when the file is next touched.
- Run the seven GPU-host manual items and record the Wan i2v timing in the PR description before presenting the local mode as production-ready.
- Ready to commit — the implementation is complete and validated for everything that does not require the physical GPU host.
