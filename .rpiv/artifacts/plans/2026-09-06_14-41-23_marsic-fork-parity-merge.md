---
date: 2026-09-06T14:41:23+0700
author: Yogiswara Utama
commit: de92009
branch: main
repository: openshorts
topic: "Marsic1/openshorts fork parity merge — phased implementation plan"
tags: [plan, merge, llm-client, ai-provider, gate-predicate, stage-ownership, mcp, dashboard, infra, remotion, tests]
status: ready
parent: ".rpiv/artifacts/designs/2026-09-06_06-47-45_marsic-fork-parity-merge.md"
phase_count: 8
phases:
  - { n: 1, title: "Merge mechanics + app.py LLM seam (foundation)", files: [app.py, llm_backend.py, tests/test_llm_backend.py, mcp_server.py, tests/test_llm_endpoints.py], depends_on: [] }
  - { n: 2, title: "main.py stage-ownership flip", files: [main.py, tests/test_llm_client.py], depends_on: [1] }
  - { n: 3, title: "Boundary contract test + CI pytest-asyncio", files: [tests/test_no_double_route.py, .github/workflows/ci.yml], depends_on: [1, 2] }
  - { n: 4, title: "Dashboard core seam", files: [dashboard/src/App.jsx, dashboard/src/components/CreateEditProfileModal.jsx, dashboard/src/components/ResultCard.jsx], depends_on: [1] }
  - { n: 5, title: "Dashboard nav/mounts + AuthContext pin + deps", files: [dashboard/src/App.jsx, dashboard/src/contexts/AuthContext.jsx, dashboard/package.json], depends_on: [4] }
  - { n: 6, title: "Infra — compose flip + env union + requirements verify", files: [docker-compose.yml, .env.example, requirements.txt, Dockerfile], depends_on: [1] }
  - { n: 7, title: "Remotion union manifest + lockfiles", files: [remotion/package.json, remotion/package-lock.json, render-service/package-lock.json, render-service/package.json], depends_on: [1] }
  - { n: 8, title: "Docs + finalize", files: [CLAUDE.md, README.md], depends_on: [1, 2, 3, 4, 5, 6, 7] }
last_updated: 2026-09-06T14:41:23+0700
last_updated_by: Yogiswara Utama
---

# Marsic1 Fork Parity Merge Implementation Plan

## Overview

This plan implements the design `.rpiv/artifacts/designs/2026-09-06_06-47-45_marsic-fork-parity-merge.md`: merge `marsic/main` (`b1f5350`, 41 commits) into `merge/marsic-parity` off `main` (`de92009`, 15 commits) as **one merge commit**. Every conflicted file resolves **take-theirs + re-apply our deltas**. The two independently built LLM systems coexist by stage ownership: `ai_provider.py` owns the video pipeline, `llm_client.py` owns the satellite text stages plus MCP BYOK forwarding. One async predicate `ai_backend_available()` replaces both forks' separate job-gate fixes.

Phases are inherited 1:1 from the design's `## Slices` — same boundaries, same Files, same Success Criteria (verified per-slice by the design's slice-verifier).

**Execution model (overrides worktree isolation):** all 8 phases operate on ONE uncommitted merge working tree. Phase 1 creates the branch and starts the merge; Phases 2-8 resolve and edit in place; Phase 8 lands the single merge commit. No phase may run in an isolated worktree (a worktree cannot see the uncommitted merge state) and no phase commits — phases run strictly sequentially in numbered order. The design sequences Phases 6-7 after 1-5 even though they share no files, so the env surface they document is final.

## Desired End State

- `/api/config` (self-host, `LLM_*` env set) answers:
  `{"youtubeUrlEnabled": true, "billingEnabled": false, "googleAuthEnabled": false, "jobRetentionSeconds": 3600, "llmConfigured": true, "llmModel": "gpt-oss:120b", "llmBaseUrl": "https://ollama.com/v1", "localLlm": {"provider": "openai", "model": "gpt-oss:120b", "baseUrl": "https://ollama.com/v1"}}`
- Job start personas — all start, none crash at launch:

  | Persona | Gate |
  |---|---|
  | Gemini key (header/env/billing) | `bool(gemini_key)` leg |
  | `AI_PROVIDER=openai` (+ any `OPENAI_*`) | provider leg |
  | Server `LLM_*` env | env leg |
  | BYOK `X-LLM-*` headers per job | `resolve_llm` leg |

  None present + `provider == "gemini"` → 400 `LLM_ENDPOINT_HINT` (self-host) / `gemini_missing_error()` (billing).
- Each pipeline stage dispatches through exactly one LLM system; `tests/test_no_double_route.py` proves it with counting fakes + canaries on both sides.
- MCP callers can send every per-job header the dashboard sends.
- `docker compose config` validates with CPU default; `cd remotion && npm ci --legacy-peer-deps` succeeds against the committed lockfile; `cd dashboard && npm run build && npm run lint` exit 0.
- `git log --oneline marsic/main` fully contained in the merge commit's history.

## What We're NOT Doing

- No unification of the two AI systems into one transport (FRD non-goal; advisor-rejected).
- No adapter layer (`ai_provider` delegating to `llm_client`) — post-merge follow-up only.
- No enabling of new features in production (proxy spends money; qwen-tts wants GPU).
- No rebase/rewrite of pushed history; no permanent `marsic` remote; no bidirectional sync.
- No changes to their 10 new test files, `test_ffmpeg_utils.py`, `cloud/*`, `voiceover.py`, `hook_grounding.py`, `game profile` modules — they land byte-identical (except the two documented exceptions: `test_llm_backend.py` deleted with its module; pytest-asyncio added to CI, not to their tests).
- No `GAME_PROFILE_ID`/`USER_ID` documentation in `.env.example` (per-job transports, never deployment config).

## Phase 1: Merge mechanics + app.py LLM seam (foundation)

### Overview

Starts the merge (`git checkout -b merge/marsic-parity && git merge --no-commit marsic/main`) and resolves the `app.py` conflict on their base with 11 re-applied hunks (E1-E11): `resolve_llm`, the `ai_backend_available()` predicate, `LLM_ENDPOINT_HINT`, `_env_llm_config`, the dual-key `/api/config`, our `/api/llm/test`, the satellite-head tuple discipline, the job gate, the `LLM_*` job-env forward, and the effects-head tuple unpacks. Deletes `llm_backend.py` + `tests/test_llm_backend.py`, extends the MCP allowlist by 16 headers, and ports the gate/config tests. All other conflicts stay unresolved for their phases.

**Files**: `app.py`, `llm_backend.py` (DELETE), `tests/test_llm_backend.py` (DELETE), `mcp_server.py`, `tests/test_llm_endpoints.py`

### Changes Required:
#### 1. Merge mechanics (branch + working-tree state — no file edit yet)
**File**: branch `merge/marsic-parity` + the uncommitted merge working tree
**Changes**: Start the merge once. The working tree stays uncommitted across all phases; the single merge commit lands in Phase 8. Conflicts for later phases stay unresolved until their phase.

```bash
git checkout -b merge/marsic-parity
git merge --no-commit marsic/main
```

#### 2. app.py — MODIFY (their base + 11 re-applied hunks + predicate + satellite heads)
**File**: `app.py`
**Changes**: Resolve the conflict take-theirs (`git checkout --theirs app.py`), then apply E1-E11. MODIFY entries show only added/changed code; the merged base (their side) is on disk after the merge.

**E1.** Delete their `app.py:2` `import llm_backend`.

**E2.** Insert after their `resolve_ai_provider` ends (:178), before `resolve_upload_post` (:181):

```python
async def resolve_llm(request: Request, task: Optional[str] = None):
    """Resolve the third-party OpenAI-compatible LLM endpoint config.

    Cloud (hosted) stays Gemini-pinned: always None. Self-host: the BYOK
    header triple X-LLM-Base-Url + X-LLM-Key (+ optional X-LLM-Model) wins
    when base+key are both present — a header key must never travel to an
    env-configured base_url — else the LLM_* env with per-task
    LLM_MODEL_<TASK> resolution (``task``: "thumbnail" / "saas" today).
    None when no full config resolves (llm_client prints a warning on a
    half-configured env). NB: like X-Gemini-Key, header-provided config
    does NOT survive a redeploy resume — the manifest rebuilds env from
    os.environ only (app.py:876).
    """
    if BILLING_ENABLED:
        return None
    try:
        import llm_client
    except Exception:
        return None  # guarded like layout_picker's SDK import: gate, not 500
    cfg = llm_client.config_from(
        request.headers.get("X-LLM-Base-Url"),
        request.headers.get("X-LLM-Key"),
        task=task,
        model=request.headers.get("X-LLM-Model"))
    if cfg is not None:
        return cfg
    return llm_client.active_config(task)


async def ai_backend_available(
        request: Request,
        gemini_key: Optional[str] = None,
        provider: Optional[str] = None) -> bool:
    """True when a job may start: a Gemini key (header, env, or the billing
    managed key), an OpenAI-compatible provider for this job, a BYOK
    X-LLM-* triple, or the server's own LLM_* env. One predicate replaces
    both forks' separate job-gate fixes (theirs 25222f5, ours pre-merge
    gate) — the line was hand-fixed three times across the two forks.
    Legs 1-2 are redundant at the job-launch site but load-bearing for
    future callers and for /api/config's siblings. The env leg carries the
    billing pin inside _env_llm_config, so cloud can never report
    available from a stray server env."""
    if provider == "openai":
        return True  # resolve_openai always yields a usable base; key optional
    if gemini_key:
        return True
    if await resolve_llm(request) is not None:
        return True
    return _env_llm_config() is not None
```

**E3.** Insert after their `gemini_missing_error()` ends (:236):

```python
LLM_ENDPOINT_HINT = ("Missing X-Gemini-Key header. Set a Gemini key, or use "
    "an OpenAI-compatible endpoint: server env LLM_BASE_URL + LLM_API_KEY + "
    "LLM_MODEL, or X-LLM-Base-Url + X-LLM-Key (+ X-LLM-Model) headers.")
```

**E4.** Insert after their `/health/ready` ends (:1979), before their `@app.get("/api/config")` (:1981):

```python
def _env_llm_config():
    """The server's own LLM_* config, or None — what /api/config reports.

    Asks by TASK, never with task=None: config_from resolves
    LLM_MODEL_<TASK> or LLM_MODEL, so "thumbnail" alone already covers a
    plain LLM_MODEL server, while a task=None probe on a server configured
    only with LLM_MODEL_THUMBNAIL would trip llm_client's once-only
    "the third-party backend stays inactive" warning on a perfectly healthy
    setup. Reporting such a server as unconfigured would also make the
    dashboard demand a key it does not need."""
    if BILLING_ENABLED:
        return None
    try:
        import llm_client
    except Exception:
        return None
    for task in ("thumbnail", "saas"):
        cfg = llm_client.active_config(task)
        if cfg is not None:
            return cfg
    return None
```

**E5.** Replace their `/api/config` (their :1981-1991 — decorator included, so the route registers exactly once):

```python
@app.get("/api/config")
async def get_config():
    llm_cfg = _env_llm_config()
    return {
        "youtubeUrlEnabled": not DISABLE_YOUTUBE_URL,
        "billingEnabled": BILLING_ENABLED,
        "googleAuthEnabled": bool(BILLING_ENABLED and cloud.settings.google_auth_enabled),
        "jobRetentionSeconds": JOB_RETENTION_SECONDS,
        # Never the key. LlmConfig marks api_key repr=False and the dashboard
        # only needs to know that a backend exists and which one it is; this
        # endpoint is served before auth.
        "llmConfigured": llm_cfg is not None,
        "llmModel": llm_cfg.model if llm_cfg else None,
        "llmBaseUrl": llm_cfg.base_url if llm_cfg else None,
        # Their fork's flag, their exact describe() dict shape — both
        # dashboards consume these by name. None under billing: the pin
        # inside _env_llm_config covers it.
        "localLlm": ({"provider": "openai", "model": llm_cfg.model,
                      "baseUrl": llm_cfg.base_url} if llm_cfg else None),
    }
```

**E6.** Insert immediately after the merged `get_config`: our `POST /api/llm/test` endpoint VERBATIM from our `app.py:1909-1950`. Body: BILLING → 404; `_check_probe_rate(request.client.host if request.client else "anon")` (their limiter exists at `:264`); task loop `("thumbnail", "saas")` over `await resolve_llm(request, task=task)`; 400 `LLM_ENDPOINT_HINT` when None; `llm_client.probe` in executor; api_key redaction in error detail; `LlmError` without "LLM provider" prefix → 400 else 502; returns `{"ok": True, "model": cfg.model, "latencyMs": int(...)}`.

**E7.** Satellite heads + calls (their bodies):

Their `/api/thumbnail/analyze`: delete the dead param `x_gemini_key: Optional[str] = Header(None, alias="X-Gemini-Key")` (:5710, fix the preceding line's trailing comma). Replace their :5713-5715:

```python
    api_key, _gemini_model = await resolve_gemini(request)
    llm_cfg = await resolve_llm(request, task="thumbnail")
    if not api_key and llm_cfg is None:
        if not BILLING_ENABLED:
            raise HTTPException(status_code=400, detail=LLM_ENDPOINT_HINT)
        raise gemini_missing_error()
```

Replace their :5769:

```python
        result = await loop.run_in_executor(None, analyze_video_for_titles, api_key, video_path, pre_transcript, llm_cfg)
```

Their `/api/thumbnail/titles` — replace their broken :5815-5817 (same merged head as analyze). Replace their :5849-5856:

```python
        result = await loop.run_in_executor(
            None,
            refine_titles,
            api_key,
            session["context"],
            req.message,
            session["conversation"],
            llm_cfg
        )
```

Their `/api/thumbnail/generate` — image generation is Gemini-only, LLM advisory (our shape). Replace their :5894-5896:

```python
    api_key, _gemini_model = await resolve_gemini(request)
    if not api_key:
        raise gemini_missing_error()
    llm_cfg = await resolve_llm(request, task="thumbnail")
```

Replace their :5951-5958 (the full run_in_executor call, closing paren included):

```python
        thumbnails = await loop.run_in_executor(
            None,
            functools.partial(
                generate_thumbnail, api_key, title, session_id, face_path, bg_path,
                extra_prompt, count, video_context, burn_text=burn_text,
                thumbnail_text_hint=text_hint, language=language,
                frame_reference=frame_reference, llm_config=llm_cfg),
        )
```

Their `/api/thumbnail/describe` — replace their :6022-6024 (same merged head as analyze). Replace their :6037-6045:

```python
        result = await loop.run_in_executor(
            None,
            generate_youtube_description,
            api_key,
            req.title,
            segments,
            session.get("language", "en"),
            session.get("video_duration", 0),
            llm_cfg
        )
```

Their `/api/saasshorts/analyze` — replace their :6302-6304:

```python
    gemini_key, _gemini_model = await resolve_gemini(request)
    llm = await resolve_llm(request, task="saas")
    if not gemini_key and llm is None:
        if not BILLING_ENABLED:
            raise HTTPException(status_code=400, detail=LLM_ENDPOINT_HINT)
        raise gemini_missing_error()
```

Replace their :6322-6323 (grounded research is Gemini-only class E — the new gate makes keyless+llm reachable here, so the skip guard comes with it):

```python
                if gemini_key:
                    web_research = research_saas_online(req.url, gemini_key)
                else:
                    # Grounded research is class E (Gemini-only): with a
                    # third-party endpoint and no Gemini key, skip it rather
                    # than crash — the analysis runs on the scrape alone.
                    print("[SaaSShorts] No Gemini key — skipping grounded web "
                          "research (third-party endpoint in use).")
                analysis = analyze_saas(scraped, gemini_key,
                                        web_research=web_research, llm_config=llm)
```

Replace their :6336:

```python
            scripts = generate_scripts(analysis, gemini_key, req.num_scripts,
                                       req.style, req.language, req.actor_gender,
                                       llm_config=llm)
```

**E8.** Job gate — replace their :2320-2324 (comment block :2320-2322 + gate :2323-2324):

```python
    # Any one of: a Gemini key, an OpenAI-compatible provider for this job, a
    # BYOK X-LLM-* triple, or the server's own LLM_* env keeps the core
    # pipeline alive. One predicate replaces both forks' separate gate fixes
    # (theirs 25222f5, ours pre-merge) — this line was hand-fixed three times.
    if provider == "gemini" and not gemini_key and not await ai_backend_available(request):
        if not BILLING_ENABLED:
            raise HTTPException(status_code=400, detail=LLM_ENDPOINT_HINT)
        raise gemini_missing_error()
```

**E9.** Insert after their `openai_key, openai_model, openai_base = resolve_openai(request)` (:2309):

```python
    llm_cfg = await resolve_llm(request)
```

**E10.** Insert after their provider-branched env writer's closing print (:2449), before their layouts comment (:2451):

```python
    if llm_cfg is not None:
        # Per-request provider override travels to the subprocess as env —
        # the same road GEMINI_API_KEY takes (and the same resume caveat).
        env["LLM_BASE_URL"] = llm_cfg.base_url
        env["LLM_API_KEY"] = llm_cfg.api_key
        env["LLM_MODEL"] = llm_cfg.model
    elif BILLING_ENABLED:
        # Cloud is Gemini-pinned: a stray LLM_* in the server env must not
        # reroute managed jobs to a third-party endpoint (resolve_llm is
        # already None under billing; this closes the env-copy hole).
        # Prefix sweep, not a fixed list: future LLM_* knobs inherit it.
        for _k in [k for k in env if k.startswith("LLM_")]:
            env.pop(_k, None)
```

**E11.** Same tuple discipline at their two remaining raw bindings (their fork's mid-migration leftovers; the AI video-effects feature ships dead without it — same class as D4):

Their `/api/edit` — replace their :3331-3335:

```python
    body_key = None if BILLING_ENABLED else req.api_key
    _gemini_key, _gemini_model = await resolve_gemini(request)
    final_api_key = body_key or _gemini_key

    if not final_api_key:
        raise gemini_missing_error()
```

Their `/api/effects/generate` — replace their :4492-4495:

```python
    final_api_key, _gemini_model = await resolve_gemini(request)

    if not final_api_key:
        raise gemini_missing_error()
```

#### 3. llm_backend.py — DELETE
**File**: `llm_backend.py`
**Changes**: `git rm llm_backend.py` — the production-dead third LLM system. Surviving semantics re-target: gate exemption → `ai_backend_available()` (E2/E8), `/api/config` `localLlm` → `_env_llm_config()` (E4/E5), silent-video gate → `llm_client.active_config()` (Phase 2 M4).

#### 4. tests/test_llm_backend.py — DELETE
**File**: `tests/test_llm_backend.py`
**Changes**: `git rm tests/test_llm_backend.py` — tests the deleted module; its gate/config tests port into `tests/test_llm_endpoints.py` (entry 6 below).

#### 5. mcp_server.py:51-55 — MODIFY (allowlist extension)
**File**: `mcp_server.py`
**Changes**: `_FORWARD_HEADERS` gains 16 of their headers — or their features lose BYOK through MCP (D7).

```
# Headers an MCP caller may use to authenticate / bring their own keys; they are
# forwarded verbatim to the internal endpoints so every existing auth path works.
# The per-job provider/feature families arrived with the marsic parity merge —
# their resolvers (resolve_ai_provider / resolve_openai / resolve_gemini) and
# per-job env writers read them; without allowlisting, their features lose
# BYOK through MCP.
_FORWARD_HEADERS = ("authorization", "x-api-key", "x-gemini-key",
                    "x-upload-post-key", "x-llm-base-url", "x-llm-key",
                    "x-llm-model",
                    "x-ai-provider", "x-gemini-model", "x-openai-key",
                    "x-openai-model", "x-openai-base-url",
                    "x-game-profile-id", "x-deep-provider",
                    "x-elevenlabs-key", "x-target-clips",
                    "x-enable-scene", "x-enable-audio", "x-enable-visual",
                    "x-enable-vision", "x-enable-deep", "x-enable-enhance",
                    "x-enable-emoji")
```

#### 6. tests/test_llm_endpoints.py — MODIFY (clean-slate extension + gate/config port)
**File**: `tests/test_llm_endpoints.py`
**Changes**: (a) `_clean_slate` delenv tuple gains: `"AI_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL", "GEMINI_API_KEY", "GEMINI_MODEL", "LLM_PROVIDER"`. (b) Append:

```python
def _llm_request(headers=None):
    """A bare starlette Request — enough for the resolvers' header reads."""
    from starlette.requests import Request as StarletteRequest
    return StarletteRequest({"type": "http", "headers": [
        (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]})


class TestMergedGate:
    """Ported from the fork's deleted local-LLM gate tests (their file dies
    with its module in the merge) plus the gate personas. The gate is
    ai_backend_available(); /api/config reports both key families."""

    def test_no_backend_reports_none_and_blocks_jobs(self, client):
        cfg = client.get("/api/config").json()
        assert cfg["llmConfigured"] is False
        assert cfg["localLlm"] is None
        res = client.post("/api/process", data={})
        assert res.status_code == 400
        assert res.json()["detail"] == app_module.LLM_ENDPOINT_HINT

    def test_base_url_alone_is_inert(self, client, monkeypatch):
        # Their old moment-picker backend activated on a bare base URL;
        # llm_client needs base+key. Pins the accepted strictness change (D14).
        monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
        cfg = client.get("/api/config").json()
        assert cfg["llmConfigured"] is False
        assert cfg["localLlm"] is None

    def test_full_triple_reports_both_key_families(self, client, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL", "qwen2.5:14b")
        cfg = client.get("/api/config").json()
        assert cfg["llmConfigured"] is True
        # Their exact describe() shape (ported from their deleted gate tests).
        assert cfg["localLlm"] == {
            "provider": "openai", "model": "qwen2.5:14b", "baseUrl": "http://llm.test/v1"}

    def test_gemini_provider_env_keeps_llm_reporting(self, client, monkeypatch):
        # Replaces their provider-override test — no reader left. AI_PROVIDER
        # is the video pipeline's dial; it never mutes the env leg.
        monkeypatch.setenv("AI_PROVIDER", "gemini")
        monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL", "m")
        assert client.get("/api/config").json()["llmConfigured"] is True

    def test_predicate_gemini_key_leg(self):
        import asyncio
        assert asyncio.run(
            app_module.ai_backend_available(_llm_request(), gemini_key="k")) is True

    def test_predicate_openai_provider_leg(self):
        import asyncio
        # No headers, no env, no Gemini key — the per-job provider leg alone.
        assert asyncio.run(
            app_module.ai_backend_available(_llm_request(), provider="openai")) is True

    def test_predicate_byok_header_leg(self):
        import asyncio
        req = _llm_request({"X-LLM-Base-Url": "https://byok.test/v1",
                            "X-LLM-Key": "k",
                            "X-LLM-Model": "byok-model"})
        assert asyncio.run(app_module.ai_backend_available(req)) is True

    def test_predicate_env_leg(self, monkeypatch):
        import asyncio
        monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL", "m")
        assert asyncio.run(app_module.ai_backend_available(_llm_request())) is True

    def test_predicate_false_when_nothing_configured(self):
        import asyncio
        assert asyncio.run(app_module.ai_backend_available(_llm_request())) is False

    def test_provider_only_openai_job_passes_the_gate(self, client, monkeypatch):
        # AI_PROVIDER=openai + OPENAI_* triple, no Gemini key: the job gate
        # must not 400 with the LLM hint (their 25222f5 semantics, predicate
        # edition). No url/file means the endpoint still rejects — later, with
        # a different message, and without launching anything.
        monkeypatch.setenv("AI_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://llm.test/v1")
        monkeypatch.setenv("OPENAI_MODEL", "m")
        res = client.post("/api/process", data={})
        assert res.status_code == 400
        assert res.json()["detail"] != app_module.LLM_ENDPOINT_HINT
```

### Success Criteria:

#### Automated Verification:
- [x] `python -c "import app"` exits 0 — merged app.py imports clean with no llm_backend
- [x] `grep -n "llm_backend" app.py mcp_server.py tests/test_llm_endpoints.py` → 0 matches
- [x] `pytest tests/test_llm_endpoints.py tests/test_llm_client.py -q` green (ported block incl. `TestMergedGate`)
  _Note (implement, Phase 1): this gate ran green with `--deselect` for the nine pipeline-branch seam tests in `tests/test_llm_client.py` (86 passed, 9 deselected, 0 failed). Those nine import `main`, which is mid-merge this phase — its conflict markers raise SyntaxError at import, and this repo's pytest 9.1 `importorskip` defaults to `ModuleNotFoundError` and no longer skips on it. The nine are Phase 2's file and Phase 2 deletes them; Phase 3 re-asserts this command undeselected on the finished tree. User-approved in the implement session (Mismatch → Deselect 9, check box)._
- [x] `grep -c "ai_backend_available" app.py` >= 2 (definition + job-gate call site)
- [x] `grep -c "resolve_llm" app.py` >= 8 (def + /api/llm/test task loop + /api/process + 5 satellite heads)
- [x] `grep -n "await resolve_gemini" app.py` — every binding unpacks the 2-tuple; no raw-scalar binding survives

#### Manual Verification:
- [x] `git checkout -b merge/marsic-parity && git merge --no-commit marsic/main` — merge in progress
- [x] app.py conflict resolved per this slice; `git rm llm_backend.py tests/test_llm_backend.py` done
- [x] remaining conflicts (main.py, dashboard/src/App.jsx, .env.example, CLAUDE.md, remotion/*) still open for their slices
- [x] `pytest tests/test_llm_endpoints.py::TestMergedGate -q` green; POST /api/thumbnail/analyze with no keys → 400 LLM_ENDPOINT_HINT; /api/edit and /api/effects/generate gates fire on a missing key (tuple unpacks applied)

---

## Phase 2: main.py stage-ownership flip

### Overview

Resolves `main.py` take-theirs wholesale (`git checkout --theirs main.py`) and applies M1-M4: the import rewrite (no `llm_backend`, no module-level `llm_client`), `_run_gemini_stage` restored to the merge-base 4-arg text (their production-dead local-LLM arm deleted), `score_batch_size()` + the `LLM_SCORE_BATCH` knob deleted, and the silent-video gate retargeted to `llm_client` with our longer else-text. Deletes the pipeline-branch block from `tests/test_llm_client.py` (nine seam tests + `_main`), keeping `_llm_cfg()`.

**Files**: `main.py`, `tests/test_llm_client.py`

### Changes Required:
#### 1. main.py — MODIFY (import rewrite, our seam deleted, silent-video gate retarget)
**File**: `main.py`
**Changes**: Base = their `main.py` wholesale (`git checkout --theirs main.py`). None of our `ec60f4f` seam hunks (research hunks 1-9) are re-applied; hunk 10's longer silent-video else-text is the one re-application (inside M4).

**M1.** Their import block (:24-29) — delete `import llm_backend` (:29). No `import llm_client` replaces it at module level: the only llm_client use in main.py is the function-local import in M4 (satellite pattern: `thumbnail.py:82`, `saasshorts.py:362`, `layout_picker.py:146`).

```python
import gemini_worker
import ai_provider
import hook_grounding
import layout_picker
import clip_quality
```

**M2.** `_run_gemini_stage` (their :1565-1600) — the local-LLM arm is production-dead (`_run_gemini_stage` is called only by `_run_stage_split` :1914, which has no production caller; live score/detail call `ai_provider.create_ai_provider` directly at :2528/:2783). Delete the arm; the function becomes the merge-base (`ad8ab59`) text byte-for-byte:

```python
def _run_gemini_stage(client, model_name, prompt, schema):
    """One schema-enforced Gemini call with transient-error backoff.
    Returns (parsed_dict, cost_analysis)."""
    config = genai_types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
    )
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(model=model_name, contents=prompt, config=config)
            # Policy blocks are deterministic — retrying only burns quota and
            # time, and the user deserves the real reason instead of a generic
            # "empty response" (prod 23-jul: PROHIBITED_CONTENT on every try).
            gemini_worker.raise_if_blocked(response)
            # Parsing lives inside the retry loop on purpose: Gemini sometimes
            # returns 200 with an empty body, which raises here rather than at
            # the call. Retrying that recovered every occurrence seen in prod
            # (22-jul-2026) — the same payload succeeds on the next attempt.
            parsed_obj = getattr(response, "parsed", None)
            if parsed_obj is not None:
                parsed = parsed_obj.model_dump() if hasattr(parsed_obj, "model_dump") else parsed_obj
            else:
                parsed = gemini_worker._parse_json_response_text(
                    gemini_worker._get_response_text(response))
            return parsed, gemini_worker._calculate_cost_analysis(response, model_name)
        except gemini_worker.GeminiBlockedError:
            raise  # deterministic policy block — never retry
        except Exception as e:
            msg = str(e)
            transient = any(tok in msg for tok in (
                '503', 'UNAVAILABLE', '429', 'RESOURCE_EXHAUSTED',
                '500', 'INTERNAL', 'overloaded', 'Deadline',
                'empty response body', 'did not contain a JSON object',
                'Failed to parse Gemini JSON response'))
            if attempt == max_attempts or not transient:
                raise
            wait = 5 * (2 ** (attempt - 1))
            print(f"⚠️ Gemini transient error (attempt {attempt}/{max_attempts}), retrying in {wait}s: {msg[:150]}")
            time.sleep(wait)
```

Died with the arm: the `llm_backend` docstring sentence, `use_local = llm_backend.active()`, `config = None if use_local else ...`, `if use_local: return llm_backend.generate_json(prompt, schema, model=model_name)`, the OpenAI-compatible transient tokens ('ConnectError', 'ReadTimeout', 'RemoteProtocolError', '502', '504', 'validation error') with their comment, and the `who` variable. Their surviving `tests/test_gemini_retry.py` / `tests/test_gemini_block_split.py` call this chain 4-arg pure-Gemini and stay green.

**M3.** Delete `score_batch_size()` (their :1928-1937) and the single blank line after it (:1938), keeping the two blank lines before it (:1926-1927) as the separator to `get_viral_clips` (:1939). Dead: `SCORE_BATCH = 8` is hardcoded at :2469; the only caller was their `tests/test_llm_backend.py:145-151`, deleted in Phase 1. The `LLM_SCORE_BATCH` env knob dies with it (D12; their CLAUDE.md promise is rewritten in Phase 8).

**M4.** Silent-video gate (their :2919-2927, inside `get_visual_clips`; the :2918 progress print stays untouched immediately above). Gate retarget per D6; the else-branch re-applies our longer text (research hunk-10 verdict). The function-local import matches the satellite pattern — no module-level `import llm_client`:

```python
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        import llm_client
        if llm_client.active_config() is not None:
            print("❌ This video has no usable speech, so it has to be clipped by "
                  "watching it, and that needs Gemini (a text-only LLM server "
                  "cannot see the footage). Add a GEMINI_API_KEY for silent videos.")
        else:
            print("❌ Error: GEMINI_API_KEY not found. Silent-video analysis "
                  "watches the footage on Gemini; the third-party LLM endpoint "
                  "cannot replace it.")
        return None
```

Known inherited behavior (unchanged, D15): an `LLM_*`-only persona (no Gemini/OpenAI keys) passes the app.py gate and dispatches satellites via llm_client, but `get_viral_clips` needs GEMINI or OPENAI_* — the same reachability their fork shipped.

#### 2. tests/test_llm_client.py — MODIFY (pipeline-branch block deleted; `_llm_cfg` kept)
**File**: `tests/test_llm_client.py`
**Changes**: The research break-list mandates porting the seam tests out of this file; without this edit the suite is red between Phases 2 and 3.

Delete the `# --- pipeline branch (slice 2) ---` block (:507-705): the section comment, the `_main()` helper (its uses are confined to the doomed tests), and the nine seam tests — they pass `llm=_llm_cfg()` and would TypeError against M2's 4-arg signature. **Keep `_llm_cfg()`** (:519-521): the in-process endpoints section (:751/:765/:778/:791) uses it. The block's intent re-lands inverted in Phase 3's boundary test (`tests/test_no_double_route.py`: the pipeline must call llm_client zero times). Resulting region, byte-exact from `test_http_client_rejects_invalid_url` to the next surviving test (everything from :707 `def test_explicit_model_header_wins_over_env_chain` onward unchanged):

```python
def test_http_client_rejects_invalid_url():
    # httpx 0.28 parses scheme-less strings ("not a url") as relative URLs — the
    # URL httpx actually refuses at Client construction carries a control character.
    with pytest.raises(llm_client.LlmError):
        llm_client._http_client("http://exa\tmple.com")


def _llm_cfg():
    return llm_client.LlmConfig(base_url="https://provider.test/v1",
                                api_key="k", model="test-model")


def test_explicit_model_header_wins_over_env_chain(monkeypatch):
```

Deleted tests (names only): test_stage_with_llm_config_skips_the_genai_client, test_stage_retries_llm_transients_up_to_three_attempts, test_stage_never_retries_llm_hard_errors, test_stage_blocked_never_retries, test_stage_split_keeps_the_historical_call_shape_without_llm, test_stage_split_bisects_blocked_llm_batches, test_get_viral_clips_gate_accepts_llm_only, test_get_viral_clips_propagates_llm_hard_errors, test_get_viral_clips_llm_aggregate_marks_estimated.

### Success Criteria:

#### Automated Verification:
- [x] `python -c "import main"` exits 0 — no `llm_backend` import remains; `ai_provider`/`hook_grounding`/`clip_quality` import clean
- [x] `grep -rn "llm_backend" *.py cloud/ tests/` → 0 matches (main.py held the last six tokens — :29, :1569, :1574, :1583, :1937, :2921; Slice 1 cleared the rest)
- [x] `grep -n "llm_client" main.py` → exactly 2 lines, both inside `get_visual_clips`' no-key branch (function-local import + gate call)
- [x] `grep -n "score_batch_size\|LLM_SCORE_BATCH" main.py` → 0 matches
- [x] `pytest tests/test_gemini_retry.py tests/test_gemini_block_split.py -q` green — the chain is 4-arg pure-Gemini again (merge-base shape)
- [x] `pytest tests/test_llm_client.py tests/test_llm_endpoints.py -q` green — pipeline-branch block deleted, `_llm_cfg()` retained for the in-process endpoints section

#### Manual Verification:
- [x] main.py resolved take-theirs (`git checkout --theirs main.py`) then M1-M4 applied; other conflicts untouched (`dashboard/src/App.jsx`, `.env.example`, `CLAUDE.md`, `remotion/*` stay open for their slices)
- [x] `grep -n "def _run_gemini_stage" main.py` shows the 4-arg signature; `_run_stage_split` untouched from their side (8-arg, no `llm`)
- [x] Persona (LLM_* triple set, no GEMINI_API_KEY): silent-video path prints the "text-only LLM server cannot see the footage" message and returns None; with no LLM_* either, our longer "cannot replace it" message. `get_viral_clips` never reads LLM_* (inherited from their fork: satellites dispatch via llm_client, the pipeline needs GEMINI or OPENAI_*)

---

## Phase 3: Boundary contract test + CI pytest-asyncio

### Overview

Adds the stage-ownership boundary contract test `tests/test_no_double_route.py` (6 tests: 2 video + 3 satellite + 1 app-layer env, counting fakes on the owned side and raising canaries on the other, both LLM families configured at once) and adds `pytest-asyncio` to the CI install line so their 6 async-marked tests run byte-identical.

**Files**: `tests/test_no_double_route.py` (NEW), `.github/workflows/ci.yml` (MODIFY, :24 only — the file lives in `.github/workflows/` and is byte-identical in both forks, so the merge auto-merges it clean)

### Changes Required:
#### 1. tests/test_no_double_route.py — NEW (boundary contract test)
**File**: `tests/test_no_double_route.py`
**Changes**: Create the file with exactly this content (full file):

```python
"""The stage-ownership boundary contract: one LLM system per stage.

After the parity merge the video pipeline (main.py) dispatches through
ai_provider and the satellite text stages (thumbnail / saasshorts /
layout_picker) dispatch through the third-party endpoint client. Both systems
configured at once is a legal deployment, so this file pins that no stage can
fire two calls: with AI_PROVIDER=openai + OPENAI_* AND a complete LLM_* triple
set together, the video side counts provider calls and trips a canary on the
satellite client, the satellite side does the mirror image, and the app layer
asserts the job env carries both families to the subprocess.

Ports the boundary intent of the deleted pipeline-branch tests, inverted: the
pipeline must now reach its calls with the satellite client counting ZERO.
"""
import json

import pytest
from fastapi.testclient import TestClient

import app as app_module
import llm_client

# Both systems on at once — the adversarial persona. If any stage could
# double-route, this is the config that shows it.
OPENAI_ENV = {"AI_PROVIDER": "openai",
              "OPENAI_API_KEY": "pipeline-key",
              "OPENAI_MODEL": "pipeline-model",
              "OPENAI_BASE_URL": "http://pipeline.test/v1"}
LLM_ENV = {"LLM_BASE_URL": "https://provider.test/v1",
           "LLM_API_KEY": "k",
           "LLM_MODEL": "satellite-model"}
BYOK = {"X-LLM-Base-Url": "https://byok.test/v1",
        "X-LLM-Key": "byok-secret-key",
        "X-LLM-Model": "byok-model"}


def _both_systems(monkeypatch):
    for k, v in {**OPENAI_ENV, **LLM_ENV}.items():
        monkeypatch.setenv(k, v)
    for k in ("GEMINI_API_KEY", "GEMINI_MODEL", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)


def _sat_cfg():
    return llm_client.LlmConfig(base_url=LLM_ENV["LLM_BASE_URL"],
                                api_key=LLM_ENV["LLM_API_KEY"],
                                model=LLM_ENV["LLM_MODEL"])


# --- canaries: each side trips loudly if the other system leaks in ----------

def _canary_llm_client(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the video pipeline must not call the satellite client")
    monkeypatch.setattr(llm_client, "chat", boom)
    monkeypatch.setattr(llm_client, "active_config", boom)


def _canary_ai_provider(monkeypatch):
    import ai_provider

    def boom(*a, **k):
        raise AssertionError("satellite stages must not build a pipeline provider")
    monkeypatch.setattr(ai_provider, "create_ai_provider", boom)


def _no_genai(monkeypatch):
    import google.genai as _g

    def boom(*a, **k):
        raise AssertionError("genai.Client must not be constructed here")
    monkeypatch.setattr(_g, "Client", boom)


# --- video side: main.get_viral_clips owns the ai_provider dispatch ---------

def _words(duration, step=5.0):
    # Their pipeline reads word['word']/['start']/['end'] (main.py words loop);
    # their own tests pin this shape (tests/test_process_handover.py:27).
    out, t, i = [], 0.0, 0
    while t + step < duration:
        out.append({"word": f"w{i}", "start": t, "end": t + step})
        t += step
        i += 1
    return out


def _transcript(duration):
    text = " ".join(w["word"] for w in _words(duration))
    return {"language": "en", "text": text,
            "segments": [{"start": 0.0, "end": duration, "text": text,
                          "words": _words(duration)}]}


def _install_pipeline_provider(monkeypatch, calls, schemas):
    import ai_provider

    class _Provider:
        def generate_content(self, prompt, schema=None, **kw):
            name = getattr(schema, "__name__", str(schema))
            schemas.append(name)
            if name == "ScoreResponse":
                return {"response": {"windows": [
                    {"id": "window_001", "start": 5.0, "end": 65.0,
                     "score": 90, "reason": "hook", "text": "hook"}]},
                        "cost_analysis": {"input_tokens": 1, "output_tokens": 1,
                                          "total_cost": 0.001}}
            if name == "DetailResponse":
                return {"response": {"shorts": [
                    {"start": 10.0, "end": 55.0, "title": "The moment",
                     "video_title_for_youtube_short": "The moment",
                     "predicted_score": 90}]},
                        "cost_analysis": {"input_tokens": 2, "output_tokens": 2,
                                          "total_cost": 0.002}}
            if name == "VODMetadataResponse":
                return {"response": {"vod_title": "T", "vod_description": "D"},
                        "cost_analysis": None}
            return {"response": {}, "cost_analysis": None}

    def fake_create(provider_type, model_name=None, **kw):
        calls.append((provider_type, model_name))
        return _Provider()

    monkeypatch.setattr(ai_provider, "create_ai_provider", fake_create)


class TestVideoSide:

    def test_two_pass_scores_and_details_through_the_pipeline_provider(self, monkeypatch):
        main = pytest.importorskip("main")
        _both_systems(monkeypatch)
        _canary_llm_client(monkeypatch)
        _no_genai(monkeypatch)
        calls, schemas = [], []
        _install_pipeline_provider(monkeypatch, calls, schemas)

        result = main.get_viral_clips(_transcript(300.0), 300.0)

        assert result and result["shorts"], "the two-pass must produce clips through the fake"
        assert calls, "the pipeline must actually call its provider"
        assert {p for p, _ in calls} == {"openai"}
        assert {"ScoreResponse", "DetailResponse",
                "VODMetadataResponse"} <= set(schemas)

    def test_short_video_whole_clip_path_uses_the_same_provider(self, monkeypatch):
        main = pytest.importorskip("main")
        _both_systems(monkeypatch)
        _canary_llm_client(monkeypatch)
        _no_genai(monkeypatch)
        calls, schemas = [], []
        _install_pipeline_provider(monkeypatch, calls, schemas)

        result = main.get_viral_clips(_transcript(20.0), 20.0)

        assert result and result["shorts"]
        assert calls and {p for p, _ in calls} == {"openai"}
        assert "DetailResponse" in schemas


# --- satellite side: text stages own the third-party client -----------------

class TestSatelliteSide:

    def test_thumbnail_stages_stay_on_the_thirdparty_client(self, monkeypatch):
        thumb = pytest.importorskip("thumbnail")
        import layout_picker
        _canary_ai_provider(monkeypatch)
        _no_genai(monkeypatch)
        calls = []

        def fake_chat(prompt, schema=None, *, config=None, **kw):
            calls.append(getattr(config, "model", None))
            if "art director" in prompt:  # plan_thumbnail_concepts (json mode)
                return json.dumps({"concepts": [
                    {"text": "WOW", "text_position": "left",
                     "text_color": "yellow",
                     "scene": "a desk with a phone showing vertical clips",
                     "why": "curiosity"}]}), None
            if "Brainstorm" in prompt:  # analyze pass 1
                return json.dumps({"transcript_summary": "s",
                                   "candidates": ["T1", "T2"]}), None
            return json.dumps({"titles": ["Best T"], "thumbnail_texts": ["WOW"],
                               "recommended": []}), None

        monkeypatch.setattr(llm_client, "chat", fake_chat)
        monkeypatch.setattr(layout_picker, "sample_frames",
                            lambda *a, **k: [b"\xff\xd8fake"])
        cfg = _sat_cfg()

        analyzed = thumb.analyze_video_for_titles(
            None, "/nonexistent.mp4",
            transcript={"language": "en", "text": "hi",
                        "segments": [{"start": 0, "end": 5, "text": "hi",
                                      "words": []}]},
            llm_config=cfg)
        assert analyzed["titles"] == ["Best T"]

        refined = thumb.refine_titles(None, analyzed, "make them shorter",
                                      llm_config=cfg)
        assert refined["titles"] == ["Best T"]

        concepts = thumb.plan_thumbnail_concepts(None, "Best T", 1,
                                                 video_context="a video",
                                                 llm_config=cfg)
        assert concepts and concepts[0]["text"] == "WOW"
        assert calls and all(m == "satellite-model" for m in calls)

    def test_saas_stages_stay_on_the_thirdparty_client(self, monkeypatch):
        saas = pytest.importorskip("saasshorts")
        _canary_ai_provider(monkeypatch)
        _no_genai(monkeypatch)
        cfg = _sat_cfg()
        scraped = {"url": "https://x.test", "title": "T",
                   "meta_description": "", "headings": [],
                   "main_content": "c", "additional_pages": []}

        monkeypatch.setattr(
            llm_client, "chat",
            lambda prompt, schema=None, *, config=None, **kw:
            (json.dumps({"product_name": "P", "pain_points": []}), None))
        out = saas.analyze_saas(scraped, None, llm_config=cfg)
        assert out["product_name"] == "P"

        monkeypatch.setattr(
            llm_client, "chat",
            lambda prompt, schema=None, *, config=None, **kw:
            (json.dumps([{"title": "s1", "style": "ugc", "duration_seconds": 23,
                          "target_platform": "tiktok", "hook_text": "h",
                          "segments": []}]), None))
        scripts = saas.generate_scripts({"product_name": "P"}, None,
                                        llm_config=cfg)
        assert isinstance(scripts, list) and scripts[0]["hook_text"] == "h"

    def test_layout_pick_stays_on_the_thirdparty_client(self, monkeypatch):
        lp = pytest.importorskip("layout_picker")
        monkeypatch.setattr(lp, "ENABLED", True)  # import-time env read
        _canary_ai_provider(monkeypatch)
        _no_genai(monkeypatch)
        for k, v in LLM_ENV.items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setattr(
            llm_client, "chat",
            lambda prompt, schema, *, config=None, images=None, **kw:
            ({"layout": "screencast", "confidence": 0.9,
              "why": "wide spreadsheet"}, None))
        monkeypatch.setattr(lp, "sample_frames",
                            lambda *a, **k: [b"\xff\xd8fake"])

        assert lp.pick("/nonexistent.mp4", 300.0) == "screencast"


# --- app layer: the job env carries both families to the subprocess ---------

class TestAppLayerEnv:

    @pytest.fixture
    def client(self):
        return TestClient(app_module.app, raise_server_exceptions=False)

    @pytest.fixture(autouse=True)
    def _clean_slate(self, monkeypatch):
        for k in (*OPENAI_ENV, *LLM_ENV, "GEMINI_API_KEY", "GEMINI_MODEL"):
            monkeypatch.delenv(k, raising=False)

    def test_job_env_carries_both_key_families(self, client, monkeypatch):
        # The wrapper is the only consumer that could spawn the subprocess;
        # pin it to a no-op so this test only inspects the queued job.
        async def _parked(job_id):
            return None
        monkeypatch.setattr(app_module, "run_job_wrapper", _parked)

        res = client.post(
            "/api/process",
            data={"acknowledged": "true"},
            files={"file": ("source.mp4", b"placeholder bytes", "video/mp4")},
            headers={"X-AI-Provider": "openai",
                     "X-OpenAI-Key": "pipeline-key",
                     "X-OpenAI-Model": "pipeline-model",
                     "X-OpenAI-Base-Url": "http://pipeline.test/v1",
                     **BYOK})
        assert res.status_code == 200, res.text
        job_id = res.json()["job_id"]
        env = app_module.jobs[job_id]["env"]

        # The pipeline family (provider branch of the env writer).
        assert env["AI_PROVIDER"] == "openai"
        assert env["OPENAI_MODEL"] == "pipeline-model"
        assert env["OPENAI_BASE_URL"] == "http://pipeline.test/v1"
        # The satellite triple rides along: one system does not erase the other.
        assert env["LLM_BASE_URL"] == "https://byok.test/v1"
        assert env["LLM_API_KEY"] == "byok-secret-key"
        assert env["LLM_MODEL"] == "byok-model"
        # No Gemini key was offered, so none may leak into the child env.
        assert "GEMINI_API_KEY" not in env

        # Tidy: drop the queued job and its placeholder upload.
        app_module.jobs.pop(job_id, None)
        import glob as _glob
        import os as _os
        for p in _glob.glob(_os.path.join(app_module.UPLOAD_DIR, f"{job_id}_*")):
            _os.remove(p)
```

#### 2. .github/workflows/ci.yml:22-26 — MODIFY (pytest-asyncio on the CI install line)
**File**: `.github/workflows/ci.yml`
**Changes**: The file is byte-identical in both forks (verified by diff), so the merge auto-merges it clean and this edit lands on the merged working tree. Exactly one line changes (:24):

```yaml
      - name: Install test dependencies
        run: >
          pip install pytest pytest-asyncio pillow httpx pydantic sqlalchemy numpy
          opencv-python-headless python-dotenv google-genai fastapi boto3
          PyJWT email-validator python-multipart
```

### Success Criteria:

#### Automated Verification:
- [x] `pytest tests/test_no_double_route.py -q` green (6 tests: 2 video + 3 satellite + 1 app-layer)
- [x] `grep -c "llm_backend" tests/test_no_double_route.py` returns 0 — the file must not reintroduce the deleted-module token (keeps Slice 2's repo-wide grep gate green)
- [x] `pip install pytest-asyncio` then `pytest tests/test_game_profiles.py -q` green — their async tests run byte-identical
  _Note (implement, Phase 3): their file stayed byte-identical and the tests now run under pytest-asyncio 1.4.0, but 6 of 7 fail inside their own mocks: they inject an AsyncMock() session, so `async with session.begin():` receives a coroutine — version-independent, these tests never passed anywhere (without the plugin, pytest 9.1 fails them "not natively supported"; their fork's CI was red on this file either way). Green needs editing tests/test_game_profiles.py, which the plan pins byte-identical and Phase 3's write-set excludes. User-approved in the implement session (Mismatch → Keep line + note, check box); fix deferred to /skill:revise or validate remediation._
- [x] `grep -n "pytest-asyncio" .github/workflows/ci.yml` returns exactly 1 match
- [x] `pytest tests/test_llm_client.py tests/test_llm_endpoints.py -q` green — the new module's app import and env fixtures do not disturb the existing suites

#### Manual Verification:
- [x] `git diff marsic/main -- tests/test_game_profiles.py` empty on the merge tree — their async tests byte-identical
- [x] The CI workflow is otherwise untouched (no pinning-style change, no reordering)
- [x] The new file names the deleted module in no comment or docstring (machine-checked above; reviewers keep it that way)

---

## Phase 4: Dashboard core seam

### Overview

Resolves `dashboard/src/App.jsx` take-theirs (2734 lines) and re-applies our deltas A1-A14: import repoint (`lib/panel`), both key families from `useAuth`, our encrypted apiKey lifecycle (one-way migration, plaintext write deleted), `llmConfig` state + persistence, the `needsAiBackend` gate (deleting `geminiOk`), the unified headers spread, `targetClips` resolution, gate copy, the `LlmProviderCard` mount, the SaaShortsTab/ThumbnailStudio/CreateEditProfileModal prop threads, and the provider-aware key modal. Patches `CreateEditProfileModal.jsx` (C1-C4: keys/provider via props, no localStorage). `ResultCard.jsx` is VERIFY-only — the auto-merge lands both sides, no edit.

**Files**: `dashboard/src/App.jsx`, `dashboard/src/components/CreateEditProfileModal.jsx`, `dashboard/src/components/ResultCard.jsx`

### Changes Required:
#### 1. dashboard/src/App.jsx — MODIFY (take-theirs + 8-step manual resolution, A1-A14)
**File**: `dashboard/src/App.jsx`
**Changes**: Base = their `dashboard/src/App.jsx` wholesale (`git checkout --theirs`, 2734 lines); every anchor below cites THEIR file. A1-A14 re-apply our deltas (import repoint, key lifecycle, gate, headers, targetClips, mounts, reader patches, key modal). Their nav additions land untouched: the `voiceover`/`voice-style-presets`/`game-profiles` entries (:1127-1129), the `goToTab`/`tabLocked` tutorial lock (:1143-1148), the HistoryTab (:1969-1975) / GameProfilesPage (:1977) / VoiceOverPage mounted-once (:1987-2006) / VoiceStylePresetsPage (:2008-2014) view blocks, and the ClipTutorial modal (:2705-2713). (A15, the history-nav union rule, is Phase 5.)

**A1.** Imports — insert after their :4 (`import MediaInput …`), after their :32 (api import), and replace their :33:

```jsx
import LlmProviderCard from './components/LlmProviderCard';
```

```jsx
// The X-LLM-* header builder rides with its sibling: the AI-backend gate and
// the /api/process headers below are its first consumers.
import { llmConfigComplete, llmHeaders } from './lib/llm';
```

```jsx
import { track } from './lib/panel';
```

**A2.** useAuth — replace their :205-206 with both key families:

```jsx
  // Cloud auth/billing session + the two LLM surfaces: llmConfigured/Model/BaseUrl
  // are our satellite family (/api/config), localLlm is their pipeline family.
  const { billingEnabled, isManaged, isSignedIn, me, plan, refreshMe, jobRetentionSeconds,
          llmConfigured, llmModel, llmBaseUrl, localLlm } = useAuth();
```

**A3.** apiKey lifecycle — replace their :217 plaintext init with our encrypted initializer + one-way migration (ours verbatim):

```jsx
  // --- apiKey initializer — legacy plaintext → geminiKey_v1 ----------------------
  // One-way migration, once per browser (Migration Notes). The blob stores
  // JSON ({key: ...}), not the bare key: decrypt never throws, and under a
  // rotated VITE_ENCRYPTION_KEY a bare blob decrypts to valid-base64 garbage
  // that re-encrypts byte-identically (XOR involution) — but garbage never
  // survives JSON.parse. The JSON shape is the rotation guard (D12).
  const [apiKey, setApiKey] = useState(() => {
    let value = '';
    try {
      const stored = localStorage.getItem('geminiKey_v1');
      if (stored) {
        const parsed = JSON.parse(decrypt(stored));
        if (parsed && typeof parsed === 'object' && typeof parsed.key === 'string') {
          value = parsed.key;
        }
      }
      if (!value) value = localStorage.getItem('gemini_key') || '';
    } catch (_) { /* unreadable storage or rotated-key garbage — start empty */ }
    // The plaintext key is gone after this line, adopted or not. The
    // persistence effect below writes the encrypted JSON form on this mount.
    try { localStorage.removeItem('gemini_key'); } catch (_) { /* ignore */ }
    return value;
  });
```

**A4.** llmConfig state — insert after their :281 (end of the falKey initializer; :282 is uploadUserId), ours verbatim:

```jsx
  // --- llmConfig state — after the falKey initializer -----------------------------
  // One encrypted JSON blob, not three keys: one localStorage read, one write.
  // The JSON parse is the corruption guard — every bad path (bad base64,
  // key-rotated garbage, non-JSON, wrong shape) lands in the catch and starts
  // from the empty triple. Nothing here blocks the app from booting.
  // The AI Provider card's Save is the only writer of this config.
  const [llmConfig, setLlmConfig] = useState(() => {
    try {
      const stored = localStorage.getItem('llmConfig_v1');
      if (stored) {
        const parsed = JSON.parse(decrypt(stored));
        if (parsed && typeof parsed === 'object') {
          return {
            baseUrl: typeof parsed.baseUrl === 'string' ? parsed.baseUrl : '',
            apiKey: typeof parsed.apiKey === 'string' ? parsed.apiKey : '',
            model: typeof parsed.model === 'string' ? parsed.model : '',
          };
        }
      }
    } catch (_) { /* corrupt or key-rotated blob — start empty */ }
    return { baseUrl: '', apiKey: '', model: '' };
  });
```

**A5.** Persistence effects — insert BEFORE their settings-persist effect (opens :639):

```jsx
  // --- apiKey persistence — encrypted JSON like the other keys --------------------
  useEffect(() => {
    if (apiKey) {
      try { localStorage.setItem('geminiKey_v1', encrypt(JSON.stringify({ key: apiKey }))); } catch (_) { /* ignore */ }
    }
  }, [apiKey]);

  // --- llmConfig persistence — same effect shape as the other keys (D8) -----------
  // Guarded like the sibling keys: only a complete triple is persisted, so
  // deleting the blob (and saving nothing after) restores the pre-feature
  // state. Half-configured values live in memory only (D2).
  useEffect(() => {
    if (llmConfigComplete(llmConfig)) {
      try { localStorage.setItem('llmConfig_v1', encrypt(JSON.stringify(llmConfig))); } catch (_) { /* ignore */ }
    }
  }, [llmConfig]);
```

Then inside their effect delete :640-641 (the two "Encrypt Gemini Key too…" comment lines) and :642 (`if (apiKey) localStorage.setItem('gemini_key', apiKey);`). Every other write stays (openai_key, models, ai_provider, toggles, target_clips, deep_provider, selected_game_profile); their dep array :658 stays as-is (apiKey remains listed; harmless).

**A6.** Gate — replace their :836-842 (comment block + `geminiOk` + `keysMissing`). Their :843 `needsPlan` STAYS (consumed at :916/:1475):

```jsx
  // Hosted is paid-only (no BYOK core). Self-host uses BYOK keys.
  // `keysMissing` now means "self-host BYOK keys missing" — it never fires on hosted.
  // The AI backend is any ONE of: a Gemini key, the saved satellite provider
  // triple (llmConfig), a server-side LLM_* setup reported by /api/config
  // (llmConfigured), or the pipeline provider the server reports (localLlm).
  // Cloud gate (D3): a stale browser blob from a self-host era of this origin
  // must not reach cloud requests, gates or child components. The empty triple
  // makes every consumer inert under billing.
  const providerCfg = billingEnabled
    ? { baseUrl: '', apiKey: '', model: '' }
    : llmConfig;
  const llmActive = llmConfigComplete(providerCfg) || !!llmConfigured || !!localLlm;
  const needsAiBackend = !apiKey && !llmActive;
  const keysMissing = !billingEnabled && (needsAiBackend || !uploadPostKey);
```

**A7.** handleProcess headers — replace their :977-991 INCLUSIVE (comment :977, the one-long-line headers :978, openai if-branch :979-985, else branch :986-990, its closing `}` :991; their :976 `let body;` stays). Their two branches attached the identical five keys — collapsed; the two `if (apiKey) headers['X-Gemini-Key']` lines are covered by the spread:

```jsx
      // BYOK: the satellite triple (X-LLM-*) rides with the pipeline family.
      // llmHeaders builds from the cloud-gated providerCfg, so a stale
      // self-host blob cannot leak into a cloud request (D3). The Gemini key
      // is our encrypted apiKey, attached once here; the provider-family
      // fields below are theirs (X-AI-Provider / X-OpenAI-* / toggles /
      // X-Target-Clips / X-Deep-Provider / X-Game-Profile-Id).
      const headers = {
        ...llmHeaders(providerCfg),
        ...(apiKey ? { 'X-Gemini-Key': apiKey } : {}),
        'X-AI-Provider': aiProvider,
        'X-Enable-Scene': enableScene ? '1' : '0',
        'X-Enable-Audio': enableAudio ? '1' : '0',
        'X-Enable-Visual': enableVisual ? '1' : '0',
        'X-Enable-Vision': enableVision ? '1' : '0',
        'X-Enable-Deep': enableDeep ? '1' : '0',
        'X-Enable-Enhance': enableEnhance ? '1' : '0',
        'X-Enable-Emoji': enableEmoji ? '1' : '0',
        'X-Target-Clips': String(targetClips),
        ...(deepProvider ? { 'X-Deep-Provider': deepProvider } : {}),
        ...(selectedProfileId ? { 'X-Game-Profile-Id': selectedProfileId } : {}),
      };
      if (openaiKey) headers['X-OpenAI-Key'] = openaiKey;
      if (openaiModel) headers['X-OpenAI-Model'] = openaiModel;
      if (geminiModel) headers['X-Gemini-Model'] = geminiModel;
      if (openaiBaseUrl) headers['X-OpenAI-Base-Url'] = openaiBaseUrl;
```

**A8.** targetClips — delete their :996 (`        target_clips: data.targetClips || null,`), the first line inside their `const advanced = {` (:995). Their persistent `target_clips: String(targetClips)` targetPayload is the single transport (D8: their state wins; the duplicate form-field/JSON-key ambiguity dies with it). Rest of `advanced` unchanged:

```jsx
      const advanced = {
        clip_min_seconds: data.clipMinSeconds || null,
        clip_max_seconds: data.clipMaxSeconds || null,
        // Sent explicitly both ways: absent means off for raw API callers,
        // but the dashboard always states the user's choice.
        auto_hook: data.autoHook ? '1' : '0',
        auto_hook_style: data.autoHook ? (data.autoHookStyle || 'classic') : null,
        // 'auto' is the server default, so only a deliberate choice travels.
        layouts: data.layout && data.layout !== 'auto' ? data.layout : null,
      };
```

**A9.** Gate copy — badge :1415-1421 and banner :1435-1441 (the `geminiOk` ternaries) re-based on `needsAiBackend`:

```jsx
                <span className="hidden md:inline">
                  {needsAiBackend && !uploadPostKey
                    ? 'AI & Upload-Post keys missing'
                    : needsAiBackend
                      ? 'AI Key Missing'
                      : 'Upload-Post API Key Missing'}
                </span>
```

```jsx
                <span className="text-muted">
                  {needsAiBackend && !uploadPostKey
                    ? 'Set an AI key and your Upload-Post key to use OpenShorts.'
                    : needsAiBackend
                      ? 'Set a Gemini API key or an AI provider to use OpenShorts.'
                      : 'Set your Upload-Post API key to use OpenShorts.'}
                </span>
```

**A10.** Settings — keep their :1540 KeyInput line verbatim; insert our card after it, before their OpenAI card comment (:1542). Both provider cards coexist (D5):

```jsx
              {/* Self-host only: this mount lives in the !billingEnabled branch, so the
                  provider surface is structurally absent on cloud (Requirement). This
                  card writes the satellite triple (X-LLM-*); the OpenAI card below
                  writes the pipeline family (X-OpenAI-*) — both coexist (D5). */}
              <LlmProviderCard
                savedConfig={llmConfig}
                onConfigSet={setLlmConfig}
                llmConfigured={llmConfigured}
                llmModel={llmModel}
                llmBaseUrl={llmBaseUrl}
              />
```

**A11.** SaaShortsTab — replace their :1840 one-line mount with the same line + the satellite props:

```jsx
            <SaaShortsTab geminiApiKey={apiKey} elevenLabsKey={elevenLabsKey} falKey={falKey} uploadPostKey={uploadPostKey} uploadUserId={uploadUserId} managed={isManaged} llmConfig={providerCfg} llmActive={llmActive} />
```

**A12.** ThumbnailStudio — insert two prop lines directly after their :2018 (`geminiApiKey={apiKey}`, first prop line of the mount opening at :2017):

```jsx
              llmConfig={providerCfg}
              llmActive={llmActive}
```

**A13.** CreateEditProfileModal — replace their :2254 one-line mount with the same line + the five props (the `VoiceOverPage` mount pattern, their :1990-2003):

```jsx
                  <CreateEditProfileModal isOpen={!!editingSelectedProfile} profile={editingSelectedProfile} onClose={() => setEditingSelectedProfile(null)} onSave={async () => { setEditingSelectedProfile(null); try { const d = await apiJson('/api/game-profiles'); setGameProfiles(Array.isArray(d)?d:[]);} catch {} }} aiProvider={aiProvider} geminiApiKey={apiKey} openaiApiKey={openaiKey} openaiModel={openaiModel} openaiBaseUrl={openaiBaseUrl} />
```

**A14.** Key modal — replace the title ternary :2561-2565, the body `<p>` :2584-2586, and the Gemini block :2588-2614 with our provider-aware versions. Their Upload-Post block :2616-2620 and footer buttons :2571-2576 STAY:

```jsx
        title={needsAiBackend && !uploadPostKey
          ? 'Required API Keys Missing'
          : needsAiBackend
            ? 'AI Key Required'
            : 'Upload-Post API Key Required'}
```

```jsx
          <p className="text-sm text-muted">
            OpenShorts needs an <strong className="text-ink2">AI key</strong> — Gemini, or any
            OpenAI-compatible provider — and an <strong className="text-ink2">Upload-Post</strong> API key.
            Gemini and Upload-Post both have free tiers.
          </p>
```

```jsx
          {/* AI block — a Gemini key or any OpenAI-compatible provider satisfies it */}
          <div className={`rounded-input p-4 space-y-2 border ${needsAiBackend ? 'border-rule2' : 'border-rule opacity-70'}`}>
            <p className="text-xs font-medium text-ink flex items-center gap-2">
              {needsAiBackend ? <AlertTriangle size={12} className="text-warn" /> : <Check size={12} className="text-ok" />}
              Gemini API Key{' '}
              {apiKey && <span className="text-ok">— set</span>}
              {!apiKey && llmActive && <span className="text-ok">— covered by your AI provider</span>}
            </p>
            {needsAiBackend && (
              <>
                <ol className="text-xs text-muted space-y-1 list-decimal list-inside">
                  <li>Go to <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" className="text-brass underline">aistudio.google.com/app/apikey</a></li>
                  <li>Sign in with your Google account</li>
                  <li>Click "Create API Key"</li>
                  <li>Copy the key and paste it below</li>
                </ol>
                <input
                  type="text"
                  placeholder="Paste your Gemini API key here..."
                  className="input-field"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && e.target.value.trim()) {
                      setApiKey(e.target.value.trim());
                    }
                  }}
                />
                <p className="text-xs text-muted">
                  No Google key? Any OpenAI-compatible endpoint works too — Ollama, OpenRouter, vLLM.
                  Close this dialog and set it up under Settings → AI Provider.
                </p>
              </>
            )}
          </div>
```

#### 2. dashboard/src/components/CreateEditProfileModal.jsx — MODIFY (props patch)
**File**: `dashboard/src/components/CreateEditProfileModal.jsx`
**Changes**: Base = their file (777 lines, arrives from the merge); every anchor cites THEIR file. C1-C4:

**C1.** Signature — replace their :6 (`export default function CreateEditProfileModal({ isOpen, onClose, profile, onSave }) {`):

```jsx
export default function CreateEditProfileModal({ isOpen, onClose, profile, onSave,
                                                aiProvider, geminiApiKey,
                                                openaiApiKey, openaiModel,
                                                openaiBaseUrl }) {
```

**C2.** Provider state — replace their :51 (`const [aiProvider, setAiProvider] = useState(localStorage.getItem('ai_provider') || 'gemini');`):

```jsx
  // Per-analysis provider choice, seeded from the app's dial each mount. The
  // app owns ai_provider persistence (D5) — this modal only reads keys/provider
  // via props (VoiceOverPage mount pattern); no localStorage access survives.
  const [provider, setProvider] = useState(aiProvider || 'gemini');
```

**C3.** handleAiAnalyze — replace their :246-278 INCLUSIVE (the `try {` block: localStorage reads :247-250, the header building with both branches, the `// persist provider choice` + `localStorage.setItem('ai_provider', aiProvider);` lines, and the apiJson call through `});`; their :279 `setFormData` onward stays):

```jsx
    try {
      const headers = { 'X-AI-Provider': provider };
      if (provider === 'openai') {
        if (openaiApiKey) headers['X-OpenAI-Key'] = openaiApiKey;
        if (openaiModel) headers['X-OpenAI-Model'] = openaiModel;
        if (openaiBaseUrl) headers['X-OpenAI-Base-Url'] = openaiBaseUrl;
        if (geminiApiKey) headers['X-Gemini-Key'] = geminiApiKey;
      } else {
        if (geminiApiKey) headers['X-Gemini-Key'] = geminiApiKey;
        if (openaiApiKey) headers['X-OpenAI-Key'] = openaiApiKey;
        if (openaiModel) headers['X-OpenAI-Model'] = openaiModel;
        if (openaiBaseUrl) headers['X-OpenAI-Base-Url'] = openaiBaseUrl;
      }
      const data = await apiJson('/api/game-profiles/analyze', {
        method: 'POST',
        headers,
        body: {
          game_title: title,
          steam_description: formData.steam_description || '',
          steam_genres: formData.steam_genres || [],
          steam_tags: formData.steam_tags || [],
          provider,
          openai_model: openaiModel || undefined,
          openai_base_url: openaiBaseUrl || undefined,
          openai_key: openaiApiKey || undefined,
        },
      });
```

The `// persist provider choice` + `setItem('ai_provider')` lines die here — App owns the dial (D5).

**C4.** JSX provider references — their :595-602 (AI Analysis card header): the `{aiProvider}` readout → `{provider}`; both toggle `setAiProvider(…)` → `setProvider(…)`; the "Provider {aiProvider}" body copy → `{provider}`.

#### 3. dashboard/src/components/ResultCard.jsx — VERIFY (auto-merge carries both sides; no edit)
**File**: `dashboard/src/components/ResultCard.jsx`
**Changes**: No code is emitted. The merge tree (`a09ea6d`, re-derived in the design's Slice-4 session) shows git already lands BOTH sides: our dead-fallback removal sits at the merged file's :284-287 (comment + `const apiKey = geminiApiKey;` — our side changed that line after the base, theirs never touched it) AND their `onVoiceOver` prop (:39) + Mic voiceover button (:942-948) ride the auto-merge. ResultCard is not among the 7 textual conflicts, so no resolution — and no our-side delta — exists for it. This phase's Success Criteria assert the merged state (grep gates: `getItem('gemini_key')` → 0 in this file, `onVoiceOver` present).

### Success Criteria:

#### Automated Verification:
- [x] `cd dashboard && npm run build` exit 0 — merged App.jsx + patched CreateEditProfileModal compile against the merged AuthContext (both key families) and lib surface
- [x] `cd dashboard && npm run lint` exit 0
  _Note (implement, Phase 4): App.jsx's own 5 problems fixed in-phase (3 empty catches → commented — including the plan's A13 verbatim `catch {}`, adjusted per this gate; 2 dep warnings → inline eslint-disable-next-line react-hooks/exhaustive-deps, the file's existing idiom — no real deps added, that would change effect re-run behavior). Scoped eslint over the phase's 3 files (App.jsx, CreateEditProfileModal.jsx, ResultCard.jsx) exits 0. Whole-repo lint stays red: 35 problems (32 errors, 3 warnings) in 6 files (Legal.jsx, PricingPage.jsx, RemotionPreview.jsx, SubtitleModal.jsx, VoiceOverPage.jsx, VoiceStylePresetsPage.jsx), all byte-identical to marsic/main (git diff = 0 lines) — their fork never lints (their CI runs pytest only) and no phase's write-set owns them. Phase 5's identical lint gate and the end-state lint claim are blocked by the same debt; the fix belongs in /skill:revise or validate remediation. Mismatch surfaced via ask_user_question; user routed the call to the advisor; advisor confirmed clean-my-files-and-note._
- [x] `grep -rn "lib/analytics" dashboard/src` → 0 matches (import repoint; research rename gate)
- [x] `grep -n "setItem('gemini_key'" dashboard/src/App.jsx dashboard/src/components/*.jsx` → 0 matches
- [x] `grep -n "getItem('gemini_key')" dashboard/src/App.jsx` → exactly 1 match (the one-way migration adopt line); same grep in `dashboard/src/components/ResultCard.jsx` → 0 matches
- [x] `grep -c "llmHeaders(providerCfg)" dashboard/src/App.jsx` → 1
- [x] `grep -c "needsAiBackend" dashboard/src/App.jsx` → >= 5 (gate, badge, banner, modal title, AI block)
- [x] `grep -n "getItem('ai_provider')" dashboard/src/components/CreateEditProfileModal.jsx` → 0 matches; `grep -c "geminiApiKey" dashboard/src/components/CreateEditProfileModal.jsx` → >= 3
- [x] `grep -c "llmConfig={providerCfg}" dashboard/src/App.jsx` → 2 (SaaShortsTab + ThumbnailStudio)

#### Manual Verification:
- [ ] Self-host, empty browser storage: keys-missing badge + banner fire; key modal shows the Gemini/Upload-Post blocks with the provider-coverage note
- [ ] AI Provider triple saved (Settings): banner clears with no Gemini key; `/api/process` carries X-LLM-* and `target_clips` arrives exactly once
- [ ] Server `LLM_*` env only (llmConfigured via /api/config): banner clears (env leg); `localLlm` only (their /api/config shape): banner clears (D8 gate leg)
- [ ] localStorage after load: `gemini_key` gone, `geminiKey_v1` encrypted JSON present; a legacy plaintext key is adopted on first load
- [ ] Game Profile modal "Analyze with AI" sends X-AI-Provider + per-provider headers from props (devtools network), no localStorage read
- [ ] Cloud (billing) deploy: no X-LLM-* headers on `/api/process` (providerCfg empty triple)
- [ ] Their surfaces intact: AI provider selector, Phase-4 toggles, target-clips slider, game profiles, ResultCard voiceover button

---

## Phase 5: Dashboard nav/mounts + AuthContext pin + deps

### Overview

Applies A15 (the D9 history-nav union rule — the only App.jsx code edit of this phase; their tabs/mounts/tutorial lock land via take-theirs and are verified, not edited), pins the merged `AuthContext.jsx` state (edit only if auto-merge dropped a flag set), and writes the `dashboard/package.json` union deps (+ `@remotion/animated-emoji`, keep `zod ^4.3.6`).

**Files**: `dashboard/src/App.jsx`, `dashboard/src/contexts/AuthContext.jsx` (verify), `dashboard/package.json`

### Changes Required:
#### 1. dashboard/src/App.jsx — MODIFY (A15: history-nav union rule)
**File**: `dashboard/src/App.jsx`
**Changes**: The only App.jsx code edit of this phase (on top of Phase 4's merged file). D9 union rule — replace their :1124-1126:

```jsx
    // History: cloud mode serves it from R2; self-host reads completed jobs
    // straight off OUTPUT_DIR (its own durability boundary), so it's always on.
    { id: 'history', ord: '06', icon: History, label: 'History', short: 'history' },
```

with:

```jsx
    // History: cloud mode serves it from R2 (per-user — sign in first);
    // self-host reads completed jobs straight off OUTPUT_DIR (its own
    // durability boundary) and keeps the tab always on. D9 union rule.
    ...((!billingEnabled || isSignedIn) ? [{ id: 'history', ord: '06', icon: History, label: 'History', short: 'history' }] : []),
```

Both variables come from A2's destructure; the expression is our `:976` conditional spread (`billingEnabled && isSignedIn`) unioned with their always-on entry — hidden only in billing mode while signed out.

#### 2. dashboard/src/contexts/AuthContext.jsx — VERIFY (edit only if auto-merge drops a flag set)
**File**: `dashboard/src/contexts/AuthContext.jsx`
**Changes**: No code is emitted by default. The merge tree — re-derived in both dashboard design sessions (`a09ea6d` Slice 4, `b3ad3fd` Slice 5) and identical on every non-conflicted path — lands both sides clean. The pin, verbatim from the merged tree:

```jsx
import { track } from '../lib/panel';
...
  // --- /api/config LLM fields (D3): consts before the `value` object --------------
  const llmConfigured = !!config.llmConfigured;
  const llmModel = config.llmModel || null;
  const llmBaseUrl = config.llmBaseUrl || null;

  const value = {
    billingEnabled: config.billingEnabled,
    localLlm: config.localLlm || null,
    googleAuthEnabled: config.googleAuthEnabled,
    jobRetentionSeconds: config.jobRetentionSeconds || null,
    llmConfigured,
    llmModel,
    llmBaseUrl,
    loading,
    // ... rest unchanged (signingIn, user, me, plan, entitled, minutes,
    // isSignedIn, isManaged, refreshMe, requestMagicLink, loginWithGoogle, logout)
  };
```

Their `localLlm` read and our three consts sit in one `value` object — both dashboards consume these by name (D3); our fetch-retry block and their `os_welcomed`/`os_show_clip_tutorial` flags both survive the auto-merge. This phase's Success Criteria assert the pin; implement edits ONLY if a re-merge drops one of them.

#### 3. dashboard/package.json — MODIFY (union: add animated-emoji, keep zod ^4.3.6)
**File**: `dashboard/package.json`
**Changes**: One line changes in the merged dependencies block: their `2b83a64` adds `@remotion/animated-emoji ^4.0.447` (kept) and downgrades `zod` to `^3.24.0` (rejected — the auto-merge takes the downgrade because base `ad8ab59` carried `^4.3.6` and ours never touched the line). The downgrade is not required by their code: their only zod imports (`dashboard/src/remotion/lib/types.ts:1`, `remotion/src/lib/types.ts:1`, `render-service/src/server.ts:3`) use v3/v4-neutral API only (`z.object`, `z.number().min().max().optional()`, `z.boolean()`, `z.enum([...]).default()`; no `z.record` single-arg, `.datetime()`, `z.function`, `errorMap` anywhere), and their own `render-service/package.json:18` pins `zod 4.3.6` exact. Keep the base's `^4.3.6` — one major across the monorepo, matching the remotion union manifest (D11). Merged block:

```json
  "dependencies": {
    "@remotion/animated-emoji": "^4.0.447",
    "@remotion/media": "^4.0.447",
    "@remotion/media-utils": "^4.0.447",
    "@remotion/player": "^4.0.447",
    "@remotion/web-renderer": "^4.0.447",
    "lucide-react": "^0.344.0",
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "remotion": "^4.0.447",
    "zod": "^4.3.6"
  },
```

Their `72346de` deleted `dashboard/package-lock.json`; ours is unchanged since base, so the deletion auto-applies — no dashboard lockfile in the merge. `npm install` regenerates an untracked copy locally; it stays untracked.

### Success Criteria:

#### Automated Verification:
- [x] `cd dashboard && npm install && npm run build` exit 0 — merged App.jsx (A1-A15), merged AuthContext, union deps compile
- [x] `cd dashboard && npm run lint` exit 0
  _Note (implement, Phase 5): scoped eslint over this phase's files (App.jsx, AuthContext.jsx) exits 0; the A15 edit adds zero problems. Whole-repo lint stays red exactly as recorded in Phase 4: 35 problems (32 errors, 3 warnings) in 6 files (Legal.jsx, PricingPage.jsx, RemotionPreview.jsx, SubtitleModal.jsx, VoiceOverPage.jsx, VoiceStylePresetsPage.jsx), all byte-identical to marsic/main, none in this phase's write-set. Same debt, same route: /skill:revise or validate remediation._
- [x] `grep -c "(!billingEnabled || isSignedIn)" dashboard/src/App.jsx` returns 1 (A15 union rule; neither fork's base contains the string)
- [x] `grep -c "id: 'history'" dashboard/src/App.jsx` returns 1 and `grep -cE "id: '(voiceover|voice-style-presets|game-profiles)'" dashboard/src/App.jsx` returns 3 (their tabs land intact)
- [x] `grep -c "tutorialLock && id !== 'dashboard'" dashboard/src/App.jsx` returns 2 (goToTab guard + tabLocked)
- [x] `grep -c "voiceoverMounted &&" dashboard/src/App.jsx` returns 1; `grep -c "<HistoryTab onReopenProject" dashboard/src/App.jsx` returns 1; `grep -c "<GameProfilesPage" dashboard/src/App.jsx` returns 1; `grep -c "<VoiceStylePresetsPage" dashboard/src/App.jsx` returns 1
- [x] `grep -c "localLlm" dashboard/src/contexts/AuthContext.jsx` returns >= 2; `grep -c "llmConfigured" dashboard/src/contexts/AuthContext.jsx` returns >= 2; `grep -c "from '../lib/panel'" dashboard/src/contexts/AuthContext.jsx` returns 1 (the auto-merge pin)
  _Note (implement, Phase 5): `grep -c "localLlm"` returns 1 LINE, not >= 2 — both occurrences sit on the single value line `localLlm: config.localLlm || null,` (:159), exactly as the plan's own verbatim pin writes them, so a line-count grep can never reach 2 even against the pin itself. Occurrence count is 2 (`grep -o | wc -l`), llmConfigured 3, lib/panel import 1 — the pin's substance (their flag read + our three consts in one `value` object) is fully intact; verified byte-for-byte against this phase's section 2 excerpt. No edit needed._
- [x] `grep -c '"zod": "^4.3.6"' dashboard/package.json` returns 1; `grep -c '"@remotion/animated-emoji"' dashboard/package.json` returns 1; `git ls-files dashboard/package-lock.json` returns empty (their `72346de` deletion applied — tracked state; `npm install` may regenerate an untracked copy locally, which stays untracked); `grep -c '"zod": "4.3.6"' render-service/package.json` returns 1 (verify-only, their exact pin)

#### Manual Verification:
- [ ] Billing deploy, signed out: History absent from desktop rail, mobile drawer, tab bar; after sign-in it appears (D9)
- [ ] Self-host (billing off): History present signed-in or not
- [ ] Fresh-signup tutorial (`#app?tutorial=1`): non-dashboard tabs disabled with Lock icon; `goToTab` on a locked tab no-ops; finish/skip unlocks
- [ ] VoiceOver tab opens and stays mounted across tab switches (in-flight generation survives); Game Profiles and Voice/Style Presets render
- [x] `npm ls zod` inside dashboard resolves 4.x (no ^3 downgrade pulled by a stray peer range)

---

## Phase 6: Infra — compose flip + env union + requirements verify

### Overview

Auto-merged base is their compose whole (compose is not among the 7 conflicts); the D10 flip edits the merged tree: CPU default (build-arg block dropped, both `gpus: all` deleted, renderer `NVIDIA_*` removed), `env_file` → `required: false`, `TZ` + `hf-cache` kept. Rewrites `.env.example` to the 9-section union (ours as base; their moment-picker block never copied; D14 keyless note added). `requirements.txt` and `Dockerfile` are VERIFY rows — their side lands byte-identical, no edit.

**Files**: `docker-compose.yml`, `.env.example`, `requirements.txt` (verify), `Dockerfile` (verify — Files line amended at slice generation; File Map row added in lockstep)

### Changes Required:
#### 1. docker-compose.yml — MODIFY (CPU flip + env_file required:false)
**File**: `docker-compose.yml`
**Changes**: Auto-merged base = their compose whole (ours untouched since base — compose is NOT among the 7 conflicts); the D10 flip edits the merged tree. Five deltas vs their file: `build.args.GPU` dropped (Dockerfile `ARG GPU=0` is the real CPU default) with a 2-line opt-in comment; `env_file` → `required: false`; backend + renderer `gpus: all` deleted; renderer `NVIDIA_*` env removed (D10's inert-or-removed latitude — removed wins: the renderer NVENC path is a stub and a CPU-default file must not carry GPU-looking config); `TZ` + `hf-cache` kept. Full merged file:

```yaml
services:
  backend:
    build:
      # CPU by default (Dockerfile ARG GPU=0). GPU hosts opt back in with
      # --build-arg GPU=1 and re-enable the GPU lines (backend gpus key,
      # renderer env).
      context: .
    container_name: openshorts-backend
    env_file:
      - path: .env
        required: false
    environment:
      - TZ=Europe/Rome
    ports:
      - "8000:8000"
    volumes:
      - .:/app
      - /app/__pycache__
      - ./output:/app/output
      # Persistent cache for AI model downloads (Qwen3-TTS VoiceDesign ~4GB,
      # Whisper/ASR models). Downloaded lazily at first use — keeps the image
      # small and avoids "No space left on device" during builds.
      - hf-cache:/app/.cache/huggingface
    restart: unless-stopped

  frontend:
    build:
      context: ./dashboard
      target: dev
    container_name: openshorts-frontend
    ports:
      - "5175:5173"
    volumes:
      - ./dashboard:/app
      - /app/node_modules
    restart: unless-stopped
    depends_on:
      - backend

  renderer:
    build:
      context: .
      dockerfile: render-service/Dockerfile
    container_name: openshorts-renderer
    ports:
      - "3100:3100"
    volumes:
      - ./output:/output
    environment:
      - REMOTION_BUNDLE_PATH=/app/remotion
      - OUTPUT_DIR=/output
      - PORT=3100
    restart: unless-stopped
    depends_on:
      - backend

volumes:
  hf-cache:
```

#### 2. .env.example — MODIFY (9-section union per research §5)
**File**: `.env.example`
**Changes**: Ours as base per research §5 (lines 1-57 identical both sides through the Thumbnail block's blank separator). Their moment-picker block (their :70-79) is NEVER copied — its tokens (`LLM_PROVIDER`, `LLM_TIMEOUT`, `OLLAMA_CONTEXT_LENGTH`, `LLM_SCORE_BATCH`) have no reader post-merge. One deliberate deviation from research item 3's "keep our LLM section verbatim": the stage list drops "clip scoring/detail" (D5/M1 moved that stage to `ai_provider`; the doc would lie) and gains the D14 keyless note. Five NEW sections follow, 37 vars, every one verified to a real reader in their tree with its actual default (provider 9 incl. newly documented `GEMINI_API_KEY`; deep 7; detection toggles + `TARGET_CLIPS` 7; voiceover TTS 8; proxy/ops 6). `AI_API_KEY`, `GAME_PROFILE_ID`, `USER_ID` stay undocumented. Known deferred: `ai_provider.py:467/:470` default drift (`gemini-2.5-flash`/`gpt-4`) is a dead fallback — every `create_ai_provider` call passes explicit `model_name` — recorded as a post-merge follow-up; no slice owns that module. Full merged file:

```bash
# AWS S3 (optional — for clip backup/gallery)
AWS_ACCESS_KEY_ID=your_aws_access_key_here
AWS_SECRET_ACCESS_KEY=your_aws_secret_key_here
AWS_REGION=eu-west-3
AWS_S3_BUCKET=your-bucket-name
AWS_S3_PUBLIC_BUCKET=your-public-bucket-name

# YouTube cookies (optional — paste Netscape-format cookies to bypass bot detection)
# YOUTUBE_COOKIES=...

# Google OAuth (cloud/billing mode only). When logging in through a proxy whose
# Host header doesn't match a Google-registered URI (e.g. local dev via the Vite
# proxy), pin the callback explicitly:
# OAUTH_REDIRECT_URI=http://localhost:8000/api/auth/google/callback
# FRONTEND_URL=http://localhost:5175

# Local dev: show the full pipeline logs (yt-dlp/ffmpeg/debug) in the UI even
# when running in paid mode (BILLING_ENABLED). Cloud/prod leaves this unset.
# DEBUG_LOGS=true

# Pre-flight quality gate: warn before processing a YouTube source below this
# height (720p default; 0 disables). Only applies to URL sources.
# QUALITY_GATE_MIN_HEIGHT=720

# Whisper transcription model (main pipeline). "small" is better than "base" on
# non-English audio; "base" is faster. device/compute for GPU tuning.
# Prod GPU: WHISPER_MODEL=large-v3-turbo WHISPER_DEVICE=cuda WHISPER_COMPUTE=float16
# WHISPER_MODEL=small
# WHISPER_DEVICE=cpu
# WHISPER_COMPUTE=int8

# Transcription backend: whisper (default) | parakeet. Parakeet (NVIDIA
# parakeet-tdt-0.6b-v3 via onnx-asr, GPU image only) is ~2x faster than
# whisper-turbo-GPU and auto-falls-back to whisper on errors or languages
# outside its 25 European ones.
# TRANSCRIBE_BACKEND=whisper

# Concurrent GPU transcriptions (parakeet or whisper-on-cuda). Keep 1.
# ASR_GPU_CONCURRENCY=1

# Video encoder for all ffmpeg encodes: x264 (default) | nvenc | auto.
# auto probes h264_nvenc once at startup and falls back to x264 if unusable.
# FFMPEG_ENCODER=x264

# Product analytics (optional, off by default). Both are read by the dashboard
# at BUILD time. Leave them unset and the frontend initialises no analytics and
# loads no third-party script. Set them to your own OpenPanel instance — and add
# your hostname to ANALYTICS_HOSTS in dashboard/index.html — to collect your own.
# VITE_OPENPANEL_API_URL=https://api.openpanel.example.com
# VITE_OPENPANEL_CLIENT_ID=your_openpanel_client_id

# Thumbnail Studio: creative text model (titles, concepts) and the image model.
# Not tied to GEMINI_MODEL on purpose: flash-lite is fine for closed choices,
# visibly worse at titles.
GEMINI_MODEL_THUMBNAIL=gemini-3.7-flash
GEMINI_IMAGE_MODEL=gemini-3.1-flash-image

# --- OpenAI-compatible third-party LLM endpoint (optional) ------------------
# Reroutes the TEXT stages (layout picking, thumbnail titles/concepts/
# description, SaaS analyze/scripts) to any OpenAI-compatible chat-completions
# endpoint. Gemini stays the default when these are unset. All three are
# required; a partially-set endpoint stays inert. A bare LLM_BASE_URL without
# LLM_API_KEY is inert too: keyless endpoints belong to the video-pipeline
# provider block below (AI_PROVIDER=openai, no key needed).
# LLM_BASE_URL=https://ollama.com/v1
# LLM_API_KEY=sk-...your-ollama-key...
# LLM_MODEL=gpt-oss:120b
# LLM_MODEL_THUMBNAIL=qwen3-coder:480b  # optional per-task models
# LLM_MODEL_SAAS=anthropic/claude-sonnet-4

# --- Video pipeline provider (server defaults; per-job overrides win) --------
# The video stages (clip scoring/detail, deep analysis, voiceover captions)
# dispatch through AI_PROVIDER: gemini (default) | openai. The dashboard's
# per-job headers and form fields override these server defaults.
# AI_PROVIDER=gemini
# GEMINI_API_KEY=
# GEMINI_MODEL=gemini-3.1-flash-lite
# OPENAI_API_KEY=
# OPENAI_MODEL=gpt-4o-mini
# OPENAI_BASE_URL=https://api.openai.com/v1
# AI_TEMPERATURE=0.7
# AI_MAX_TOKENS=4096
# AI_TIMEOUT=30  # seconds; raise for slow local models
# Keyless OpenAI-compatible endpoints (LM Studio, Ollama): leave OPENAI_API_KEY
# unset — an internal placeholder covers the SDK.

# --- Deep analysis (optional; off by default; per-job form overrides) --------
# ENABLE_DEEP_ANALYSIS=0
# DEEP_AI_PROVIDER=gemini
# DEEP_GEMINI_MODEL=
# DEEP_GEMINI_API_KEY=
# DEEP_OPENAI_MODEL=
# DEEP_OPENAI_API_KEY=
# DEEP_OPENAI_BASE_URL=

# --- Detection toggles + clip target (server defaults; per-job wins) ---------
# Scene/audio/visual/vision detection default ON; subtitle enhancement and
# caption emojis default OFF. TARGET_CLIPS caps selected clips; empty lets the
# pipeline decide (2-6).
# ENABLE_SCENE_DETECTION=1
# ENABLE_AUDIO_EVENTS=1
# ENABLE_CHEAP_VISUAL=1
# ENABLE_VISION_ANALYSIS=1
# ENABLE_SUBTITLE_ENHANCEMENT=0
# ENABLE_CAPTION_EMOJIS=0
# TARGET_CLIPS=

# --- Voiceover / local TTS (qwen-tts; GPU image only) -------------------------
# ElevenLabs dubbing is per-job header BYOK (X-ElevenLabs-Key), never env.
# TTS_DEVICE=  # empty = auto
# TTS_LANGUAGE=en
# TTS_ATTN_IMPLEMENTATION=sdpa
# VOICEOVER_GAME_VOLUME=1.0
# VOICEOVER_VOICE_VOLUME=1.0
# VOICEOVER_DUCK_THRESHOLD=0.02
# VOICEOVER_DUCK_RATIO=10
# VOICEOVER_PRESETS_DIR=voice_presets

# --- Proxy / ops --------------------------------------------------------------
# Hard daily ceiling for the per-GB paid proxy (UTC day, MB). Above it,
# DataImpulse is removed from every chain until midnight and the jobs that
# needed it fail with a clear error. 0 disables the cap. Default 500.
# PAID_PROXY_DAILY_MB=500
# Seconds a draining container keeps serving 503s on /health/ready before the
# socket closes, so the reverse proxy's healthcheck notices first.
# PROXY_DRAIN_SECONDS=20
# Hook grounding on screencast sources: frames to sample, frame width, and the
# share of the clip a screen stretch must cover. HOOK_GROUNDING=0 disables.
# HOOK_GROUNDING=1
# HOOK_GROUNDING_FRAMES=3
# HOOK_GROUNDING_WIDTH=1024
# HOOK_GROUNDING_MIN_SHARE=0.25
```

#### 3. requirements.txt — VERIFY (theirs via auto-merge; no edit)
**File**: `requirements.txt`
**Changes**: No code is emitted. Their side lands byte-identical via auto-merge; the one delta vs ours is `openai==3.3.0` (`ai_provider` lazy-imports the SDK at `ai_provider.py:265`, and the error path imports `BadRequestError` at `:412`). This phase's Success Criteria assert the merged state (`git diff marsic/main` empty + the grep gates).

#### 4. Dockerfile — VERIFY (theirs whole; no edit)
**File**: `Dockerfile`
**Changes**: No code is emitted. Theirs lands whole via auto-merge (ours untouched since base): `ARG GPU=0` defaults (:30, :63), qwen-tts GPU pip block (:34-35), conditional sox (:66-69), `fonts-symbola` (:60). The CPU default's load-bearing half lives here; Success Criteria grep `ARG GPU=0` twice.

### Success Criteria:

#### Automated Verification:
- [x] `docker compose config` exits 0 on the merged tree — CPU default, no `gpus: all`
  _Note (implement, Phase 6): the exact command is unrunnable on this machine — docker 28.3.1 client ships no compose plugin and no standalone docker-compose exists; user-approved in the implement session (Mismatch → Check with note). The file is written byte-per-plan and every structural grep gate passes (`required: false` long form present; `gpus: all`/`args:`/`NVIDIA_*` at 0); the validator re-runs the exact command. Session blocker: the CC safety-net rule `secret.basename.env` rejects any command line containing `.env.example`/`.env`, so the resolved `.env.example` could not be `git add`-ed in-session — file content IS written; the merge stays UU on that path until manually staged — and the `.env.example` grep counts below were verified against the served file content instead of a mechanical grep._
- [x] `grep -c "gpus: all" docker-compose.yml` returns 0
- [x] `grep -c "NVIDIA_VISIBLE_DEVICES\|NVIDIA_DRIVER_CAPABILITIES" docker-compose.yml` returns 0
- [x] `grep -c "args:" docker-compose.yml` returns 0 (GPU build-arg block dropped); `grep -c "required: false" docker-compose.yml` returns 1; `grep -c "hf-cache" docker-compose.yml` returns 2; `grep -c "TZ=Europe/Rome" docker-compose.yml` returns 1
- [x] `grep -cE "\bAI_API_KEY\b|LLM_SCORE_BATCH|LLM_TIMEOUT|OLLAMA_CONTEXT_LENGTH|LLM_PROVIDER|GAME_PROFILE_ID|USER_ID" .env.example` returns 0 (moment-picker block dropped, never-documented list enforced; `\b` keeps `OPENAI_API_KEY`/`DEEP_OPENAI_API_KEY` from matching `AI_API_KEY`)
  _Note (implement, Phase 6): mechanical grep blocked by the same safety-net rule; count 0 verified against the served file content (the only AI_API_KEY substrings sit inside OPENAI_API_KEY/DEEP_OPENAI_API_KEY, which the \b guard excludes)._
- [x] `grep -c "LLM_MODEL_SAAS" .env.example` returns 1 (our satellite family documented; their file never had it); `grep -c "^# GEMINI_API_KEY=" .env.example` returns 1 (newly documented, both forks omitted it before)
- [x] `grep -c "AI_TIMEOUT" .env.example` returns 1; `grep -c "DEEP_OPENAI_BASE_URL" .env.example` returns 1; `grep -c "ENABLE_CAPTION_EMOJIS" .env.example` returns 1; `grep -c "^# TARGET_CLIPS=" .env.example` returns 1; `grep -c "VOICEOVER_PRESETS_DIR" .env.example` returns 1; `grep -c "PAID_PROXY_DAILY_MB" .env.example` returns 1; `grep -c "HOOK_GROUNDING_WIDTH" .env.example` returns 1
- [x] `git diff marsic/main -- requirements.txt Dockerfile` is empty (verify rows — their side lands byte-identical, this slice emits no edit for either file)
- [x] `grep -c "openai==3.3.0" requirements.txt` returns 1; `grep -c "ARG GPU=0" Dockerfile` returns 2 (the CPU default's load-bearing half, Dockerfile :30/:63)

#### Manual Verification:
- [ ] Fresh clone without `.env`: `docker compose config` exits 0 (required:false — no env-file error) and `docker compose up` starts
- [ ] `cp .env.example .env` then `docker compose config` renders `TZ=Europe/Rome` into the backend environment
- [x] Every var in the five new sections spot-greps to a real reader (AI_TIMEOUT/main.py, HOOK_GROUNDING_FRAMES/hook_grounding.py, TTS_DEVICE/voiceover.py, ENABLE_DEEP_ANALYSIS/main.py, ENABLE_VISION_ANALYSIS/main.py)
  _Note (implement, Phase 6): all five hit — AI_TIMEOUT 4 / ENABLE_DEEP_ANALYSIS 4 / ENABLE_VISION_ANALYSIS 5 in main.py, HOOK_GROUNDING_FRAMES 1 in hook_grounding.py, TTS_DEVICE 1 in voiceover.py._
- [x] The GPU opt-in remains discoverable: the compose build comment names --build-arg GPU=1; Dockerfile :22 documents the GPU build
- [x] A keyless-LLM_BASE_URL user is pointed at the AI_PROVIDER=openai channel by the LLM section note (D14)

---

## Phase 7: Remotion union manifest + lockfiles

### Overview

Resolves the two remotion conflicts take-theirs, writes the union manifest (theirs + `"zod": "^4.3.6"`), regenerates both lockfiles with `npm install --legacy-peer-deps` and stages them — both are **committed** (their `render-service/Dockerfile` runs `npm ci` against them). `render-service/package.json` is VERIFY — theirs lands whole.

**Files**: `remotion/package.json`, `remotion/package-lock.json` (regen), `render-service/package-lock.json` (regen), `render-service/package.json` (verify theirs)

### Changes Required:
#### 1. remotion/package.json — MODIFY (union manifest)
**File**: `remotion/package.json`
**Changes**: Resolve take-theirs (`git checkout --theirs`) then write the union manifest (theirs + our one-line zod delta, D11; the blanket D1 rule — their `2b83a64` bundled the zod drop with the animated-emoji add, and their own `render-service/package.json:18` pins zod `4.3.6` exact, so the union's `^4.3.6` cannot collide with their tree: their lock already resolves `zod@4.3.6` transitively, and the regen below changes only the root-deps block). Both deps are load-bearing in the merged src: `Subtitles.tsx:13` imports `@remotion/animated-emoji`, `lib/types.ts:1` imports zod — today they compile only as hoisted transitives.

```json
{
  "name": "openshorts-remotion",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "studio": "remotion studio",
    "render": "remotion render",
    "build": "tsc --noEmit"
  },
  "dependencies": {
    "@remotion/animated-emoji": "4.0.447",
    "@remotion/cli": "4.0.447",
    "@remotion/google-fonts": "4.0.447",
    "@remotion/layout-utils": "4.0.447",
    "@remotion/media": "4.0.447",
    "@remotion/player": "4.0.447",
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "remotion": "4.0.447",
    "zod": "^4.3.6"
  },
  "devDependencies": {
    "@types/react": "^18.2.64",
    "@types/react-dom": "^18.2.21",
    "typescript": "^5.4.0"
  }
}
```

#### 2. remotion/package-lock.json + render-service/package-lock.json — REGEN (commands, not code)
**Files**: `remotion/package-lock.json`, `render-service/package-lock.json`
**Changes**: Machine-generated artifacts; per D11 both locks are committed because their `render-service/Dockerfile` COPYs both (:24, :31-32) and installs with `npm ci` (:25 plain, :38 `--legacy-peer-deps`):

```bash
# On the merged tree (Phase 1's merge is still uncommitted):
# 1. resolve both remotion conflicts take-theirs (blanket rule; the regen re-applies our zod delta)
git checkout --theirs remotion/package.json remotion/package-lock.json
# 2. overwrite remotion/package.json with the union manifest above
# 3. regenerate the remotion lock against the union manifest — flag parity with
#    their render-service/Dockerfile:38 (npm ci --legacy-peer-deps)
cd remotion && npm install --legacy-peer-deps && cd ..
# 4. render-service: theirs arrives in sync (lock root deps == their manifest,
#    verified); the install proves it and commits whatever npm records
cd render-service && npm install --legacy-peer-deps && cd ..
# 5. stage — the single merge commit lands in Phase 8
git add remotion/package.json remotion/package-lock.json render-service/package-lock.json
```

#### 3. render-service/package.json — VERIFY (theirs; no edit)
**File**: `render-service/package.json`
**Changes**: No code is emitted. The merge lands this file wholesale from their side: ours never touched `render-service/` since base (`git diff --stat ad8ab59 HEAD -- render-service/` is empty), so all five their-side deltas (Dockerfile, package-lock.json, package.json, render-worker.ts, server.ts) auto-merge clean. Their manifest exact-pins `@remotion/bundler`/`@remotion/renderer`/`remotion` at `4.0.447` and `zod` at `4.3.6` exact (:18) — matching the remotion union's resolved zod, one 4.x across both workspaces. Their `render-service/package-lock.json` is in sync with it (lockfileVersion 3; `packages[""].dependencies` = their manifest's exact six entries), so their Dockerfile's plain `npm ci` (:25) works the moment the merge lands; this phase's regen is a verified no-op that only proves it. Success Criteria assert the merged state.

### Success Criteria:

#### Automated Verification:
- [x] Conflict resolved take-theirs + union manifest written; `grep -cF '"zod": "^4.3.6"' remotion/package.json` returns 1 and `grep -cF '"@remotion/animated-emoji": "4.0.447"' remotion/package.json` returns 1 (D11 union: their emoji pin kept, our zod range restored)
- [x] `cd remotion && npm install --legacy-peer-deps` exit 0, then `cd remotion && npm ci --legacy-peer-deps` exit 0 — the regenerated lock is in sync with the union manifest (npm ci hard-fails on any manifest/lock drift); flag parity with their `render-service/Dockerfile:38`
- [x] `cd render-service && npm ci` exit 0 — plain flag, their `render-service/Dockerfile:25` parity; their lock arrives in sync (root deps match their manifest exactly — the regen is a verified no-op; regenerated with plain `npm install` — `--legacy-peer-deps` prunes their lock's react/react-dom/scheduler peer entries and breaks this check)
- [x] `cd remotion && npm run build` exit 0 — `tsc --noEmit` over the merged src (their `Subtitles.tsx:13` animated-emoji import + `remotion/src/lib/types.ts:1` zod import compile against declared deps, not transitive hoists)
- [x] `git ls-files remotion/package-lock.json render-service/package-lock.json` returns exactly 2 lines — both committed (their Dockerfile :24/:32 COPYs them; a missing lockfile breaks the image build)
- [x] `test -f remotion/public/fonts/Anton-Regular.ttf` — their font arrives via auto-merge; tsc will not catch its absence
- [x] `git diff marsic/main -- render-service/package.json render-service/Dockerfile render-service/src` is empty — verify rows land byte-identical theirs
- [x] `git status --porcelain remotion/ render-service/` shows no AA/UU entries after this slice

#### Manual Verification:
- [ ] `docker compose build renderer` succeeds end-to-end (both npm ci legs + the animated-emoji asset fetch inside their Dockerfile)
- [ ] `cd remotion && npm ls zod` lists zod@4.3.6 as a DIRECT dependency of openshorts-remotion (pre-union it resolves only as a hoisted transitive — the dependency-entry requirers in their lock are `@remotion/media` and `@remotion/studio`, never a root dep)
- [ ] `git diff marsic/main -- remotion/package-lock.json` shows a minimal delta (the root-deps zod line plus npm bookkeeping); if npm churned the whole file, regenerate with an npm that writes lockfileVersion 3 — their lock's format

---

## Phase 8: Docs + finalize

### Overview

Docs land inside the merge: `CLAUDE.md` resolves take-theirs with their `:140-151` local-LLM section rewritten into the endpoint + stage-ownership sections; `README.md` gets the 9 claim rewrites (R0-R8) on the auto-merged tree; then the single merge commit lands with the house-format message (living at `../MERGE_MSG.txt`, outside the repo) carrying the 7-bullet per-file policy list.

**Files**: `CLAUDE.md`, `README.md`

### Changes Required:
#### 1. CLAUDE.md:140-151 — MODIFY (their base + re-applied endpoint section + boundary rewrite)
**File**: `CLAUDE.md`
**Changes**: Resolution: `git checkout --theirs CLAUDE.md`; their `:123-138` hook-grounding section, `:139` blank, and `:335-358` paid-proxy section land verbatim. Replace their `:140-151` (the `### Local LLM for the moment picker` section, header through `key. Never wired in cloud mode: ...`) with the two sections below — the only edit; everything else is theirs. The doc stays token-free: `llm_backend`, `score_batch`, and the stale `phase 1` tag never appear.

```markdown
### Third-party LLM endpoint (optional)

`LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL` reroute the satellite text
stages to any OpenAI-compatible `/v1/chat/completions` endpoint. Per-task
overrides: `LLM_MODEL_THUMBNAIL`, `LLM_MODEL_SAAS` (chain:
`LLM_MODEL_<TASK>` then `LLM_MODEL`, never `GEMINI_MODEL*`).

- Reroutes: layout picker, thumbnail text, SaaS analyze/scripts; the MCP
  server forwards `X-LLM-*` BYOK headers to the same stages.
- Stays Gemini: image gen, silent-video, editor effects, SaaS grounded
  research, cloud/managed.
- All three vars are required. A bare `LLM_BASE_URL` without a key is inert
  (keyless local servers belong to the pipeline provider block:
  `AI_PROVIDER=openai` + `OPENAI_BASE_URL`, no key needed); a
  half-configured endpoint (no model) stays inert with a one-line warning.
  With everything unset the default path is byte-identical.

### Stage ownership: `ai_provider.py` vs `llm_client.py`

Two LLM systems coexist and each stage owns exactly one —
`tests/test_no_double_route.py` pins that no stage can call both. The video
pipeline (`main.py`: clip score/detail, deep analysis, vision pass,
voiceover captions, VOD metadata) dispatches through
`ai_provider.create_ai_provider`, selected by `AI_PROVIDER=gemini|openai`
(server default) with per-job `X-AI-Provider` / `X-OpenAI-*` headers or the
`OPENAI_*` env; keyless endpoints (Ollama, LM Studio) work with no key. The
satellite text stages (layout picker, thumbnail titles/concepts/description,
SaaS analyze/scripts) dispatch through `llm_client` on the `LLM_*` triple
above or per-job `X-LLM-*` headers; a header key never travels to an
env-configured base URL. The fork's third system (the local-LLM moment
picker) is deleted: its gate exemption became `ai_backend_available()` and
its `/api/config` `localLlm` flag now reports `_env_llm_config()`.
The standing rule for future merges: the side whose base you
take owns the stage.

`ai_backend_available(request, gemini_key, provider)` is the single job
gate: a Gemini key, a per-job openai provider, a BYOK `X-LLM-*` triple, or
the server's own `LLM_*` env — any one lets a job start. `/api/config`
reports both families from that one source: `llmConfigured`/`llmModel`/
`llmBaseUrl` for the satellites, `localLlm` in the fork's exact dict shape.
The env namespace split is the rule: `LLM_*` configures satellites only;
`AI_PROVIDER`/`OPENAI_*`/`GEMINI_*` configure the pipeline only. Pipeline
errors raise `AIProviderError` (string-sniffed retry classification, no
Gemini bisect ladder); `AI_TIMEOUT` defaults to 30 s and `AI_TEMPERATURE`
to 0.7.
```

#### 2. README.md — MODIFY (auto-merged tree b3ad3fd + 9 doc-claim rewrites R0-R8)
**File**: `README.md`
**Changes**: The merge auto-merges this file clean (not one of the 7 conflicts). Each R-block below is located by its quoted first line (unique in the merged file) — apply in any order; the line numbers cite the `b3ad3fd` merged tree for orientation only (R3/R4/R8 shift later line numbers). One accepted non-edit: the `mutonby` badges at `:7-9` stay (both forks left them).

**R0.** :302-303 — replace the clone command (developer decision in the design's Slice-8 session: point at this fork's origin; the design's D1 rule never covered this auto-merged branding delta):

```text
git clone https://github.com/ukind/openshorts.git
cd openshorts
```

**R1.** :129 — replace the "Runs fully local if you want" bullet:

```markdown
- **Runs fully local if you want**: set `AI_PROVIDER=openai` and point `OPENAI_BASE_URL` at Ollama, LM Studio, vLLM or any OpenAI-compatible server, and the clip analysis runs on your own model with no Google key (see [Run without a Google key](#6-run-without-a-google-key-local-llm-optional))
```

**R2.** :253 — replace the Local LLM cost-table row:

```markdown
| **Local LLM (Ollama, LM Studio, vLLM...)** | **Free, your hardware** | $0 | Clip analysis instead of Gemini (`AI_PROVIDER=openai` + `OPENAI_BASE_URL`) |
```

**R3.** :374-401 — replace the whole "### 6. Run without a Google key (local LLM, optional)" section (heading text unchanged so the R1 anchor link still resolves):

````markdown
### 6. Run without a Google key (local LLM, optional)

Two independent channels take OpenShorts off a Google key, and they answer
different questions. The video pipeline (moment picking, deep scan, VOD
metadata, voiceover captions) runs through the pipeline provider; the
satellite text stages (layout picker, thumbnail titles, SaaS scripts) run
through the third-party endpoint. Both work with Ollama, LM Studio, vLLM,
llama.cpp server, LocalAI and OpenRouter.

```bash
# .env — video pipeline. Keyless-native: a local server needs no API key.
AI_PROVIDER=openai
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_MODEL=qwen2.5:14b
# OPENAI_API_KEY=...        # only if your server checks one (vLLM --api-key, OpenRouter)

# .env — satellite text stages. All three required; a bare base URL is inert.
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5:14b
```

The dashboard stops asking for a Gemini key when the server reports the
`LLM_*` triple (`/api/config.localLlm`). The `AI_PROVIDER` channel is
server-side only: the dashboard prompt stays, but jobs start without a
Gemini key. Two things to know:

- **Context length.** A scoring call carries several transcript windows
  (~2-3k tokens) and the detail call up to ten (~5k on a long podcast).
  Ollama defaults to a 4096-token context and truncates silently, so raise
  it (`num_ctx` in a Modelfile). 7-8B models return valid JSON reliably, 3B
  ones do not.
- **What still needs Gemini.** Anything that has to watch frames or
  generate images: silent-video detection, thumbnail image generation,
  editor effects, and SaaS grounded web research. The layout picker and
  the thumbnail text stages reroute to the `LLM_*` endpoint. Add a Gemini
  key alongside and you get both.
````

**R4.** :554-557 — replace the four stale env rows (the :558 `GEMINI_MODEL`/`OPENAI_MODEL` and :559 `DEEP_AI_PROVIDER` rows stay):

```markdown
| `AI_PROVIDER` | Video pipeline provider: `gemini` (default) or `openai`. With `openai`, `OPENAI_BASE_URL` + `OPENAI_MODEL` (key optional for local servers) take the clip analysis off Gemini |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | Satellite text stages (layout picker, thumbnail text, SaaS scripts) on any OpenAI-compatible server — all three required; per-task overrides `LLM_MODEL_THUMBNAIL` / `LLM_MODEL_SAAS` |
```

**R5.** :564 — replace the client-side `GEMINI_API_KEY` row:

```markdown
| `GEMINI_API_KEY` | Google Gemini — not needed for the clip pipeline when `AI_PROVIDER=openai` + `OPENAI_BASE_URL` are set; the `LLM_*` triple covers only the text stages. Still needed for silent videos, thumbnail image generation, editor effects and grounded web research |
```

**R6.** :598 — replace our section's clip-scoring table row:

```markdown
| Clip scoring + detail (the 2-pass analysis) | no — pipeline stage | runs on the pipeline provider: `AI_PROVIDER=openai` + `OPENAI_*` takes it off Gemini |
```

**R7.** :613-614 — replace the two retry lines (keep :615-616 `endpoints call the endpoint once per request ...` unchanged; the paragraph stays grammatical):

```markdown
Provider outages (429/5xx/timeout) are retried with backoff inside the
pipeline provider (`ai_provider`); the thumbnail and SaaS text
```

**R8.** :573-577 — replace the section intro ("Every TEXT stage" became false — clip score/detail moved to the pipeline provider):

```markdown
OpenShorts uses Google Gemini by default for all AI work. The satellite TEXT
stages — layout picking, thumbnail titles/concepts/description, SaaS
analyze/scripts — can instead run on ANY OpenAI-compatible chat-completions
endpoint — Ollama Cloud, a local Ollama, MiniMax, OpenRouter, vLLM, llama.cpp
server, an OpenAI-compatible proxy — selected with three environment
variables. The clip pipeline's analysis has its own provider channel
(`AI_PROVIDER`, see [Run without a Google key](#6-run-without-a-google-key-local-llm-optional)).
Gemini stays the default: with the variables unset, nothing changes.
```

#### 3. Merge commit (Phase 8 finalize) — commands + message
**File**: the merge commit on `merge/marsic-parity` (message staged OUTSIDE the repo)
**Changes**: The whole working tree is the merge resolution; the message lives OUTSIDE the repo so `git add -A` cannot stage it.

```bash
# After both docs land (the only remaining changes in the merge working tree):
git add -A
git commit -F ../MERGE_MSG.txt && rm ../MERGE_MSG.txt
```

`../MERGE_MSG.txt` (house format `Merge <source>: <summary>` — `989e69c`, `a42297f`; `0f326fd`-style per-file policy list, one bullet per conflicted file):

```text
Merge marsic/main: fork parity — voiceover, game profiles, subtitle editor, per-job providers

Brings Marsic1/openshorts's 41 commits (through b1f5350): VoiceOver narrated
shorts (local Qwen3-TTS + ElevenLabs), Game Profiles, the subtitle editor,
deep full-VOD scan, multimodal candidate detection, per-job provider
selection, paid proxy accounting, hook grounding, and the render-service
split. Every feature lands config-gated; merged is not enabled.

Both forks built an OpenAI-compatible LLM layer independently; they coexist
by stage ownership: ai_provider.py owns the video pipeline
(AI_PROVIDER/OPENAI_*), llm_client.py owns the satellite text stages (LLM_*
env + X-LLM-* BYOK). llm_backend.py is deleted; its gate exemption became
the single ai_backend_available() predicate and its /api/config localLlm
flag now reports _env_llm_config(). A bare LLM_BASE_URL without LLM_API_KEY
no longer opens the gate — keyless local servers use AI_PROVIDER=openai +
OPENAI_BASE_URL instead (.env.example documents both channels).

Conflicts resolved take-theirs, our deltas re-applied:
- app.py: their base + our resolve_llm / LLM_ENDPOINT_HINT / /api/llm/test;
  satellite heads unpack their 2-tuple resolve_gemini and thread llm_config
  (fixes their latent NameError at the titles head); LLM_* forwarded to the
  job env and stripped under billing
- main.py: theirs wholesale; local-LLM arm + score_batch_size deleted
  (_run_gemini_stage back to the merge-base 4-arg shape); silent-video gate
  retargeted to llm_client
- dashboard/src/App.jsx: theirs + our encrypted key lifecycle (geminiKey_v1
  / llmConfig_v1, one-way migration, plaintext write deleted),
  needsAiBackend gate, X-LLM-* header spread, LlmProviderCard mount,
  history-nav union rule
- .env.example: our base + their provider/deep/toggles/voiceover/proxy
  sections; their moment-picker block dropped (no readers post-merge)
- CLAUDE.md: theirs + our endpoint section; their local-LLM section
  rewritten into the stage-ownership boundary
- remotion/package.json: union manifest (theirs + zod ^4.3.6)
- remotion/package-lock.json: regenerated against the union manifest and
  committed (their render-service Dockerfile npm ci COPYs both locks)

Also: tests/test_no_double_route.py pins the boundary (one LLM system per
stage); the deleted module's gate/config tests ported to
tests/test_llm_endpoints.py; pytest-asyncio added to CI (their async tests
run byte-identical); docker-compose CPU default with env_file
required:false; README clone URL points at this fork.
```

Deletions (no fences): `llm_backend.py` (DELETE, Phase 1), `tests/test_llm_backend.py` (DELETE, Phase 1). Regenerated artifacts (commands, not code): `remotion/package-lock.json`, `render-service/package-lock.json` (Phase 7).

### Success Criteria:

#### Automated Verification:
- [x] `git grep -n "llm_backend" -- "*.py" "*.md" ":!.rpiv"` → 0 matches (docs now token-free like the code)
- [x] `grep -nE "LLM_SCORE_BATCH|OLLAMA_CONTEXT_LENGTH|LLM_TIMEOUT|LLM_PROVIDER|score_batch|llama3\.1|_run_gemini_stage|bisect" README.md CLAUDE.md` → 0 matches
  _Note (implement, Phase 8): the §1 fence's own "no Gemini bisect ladder" clause would have matched this gate's `bisect` token — plan self-contradiction; user-approved drop of the clause (Mismatch → drop clause, check box). The sentence reads "(string-sniffed retry classification)"._
- [x] `grep -c "ai_backend_available" CLAUDE.md` returns >= 1; `grep -c "Stage ownership" CLAUDE.md` returns 1; `grep -c "owns the stage" CLAUDE.md` returns 1 (the standing rule, FRD req #9); `grep -n "phase 1" CLAUDE.md` → 0 matches
- [x] `grep -c "AI_PROVIDER" README.md` returns >= 3; `grep -c "ukind/openshorts" README.md` returns 1; `grep -c "Marsic1/openshorts.git" README.md` returns 0
- [x] `pytest -q` exit 0 — full suite on the merged tree (terminal-slice baseline; their 10 new files need pytest-asyncio from Slice 3)
  _Note (implement, Phase 8): ran 889 passed / 10 failed, 0 merge-introduced. The 3 merge gaps the suite surfaced were fixed INSIDE the merge commit per user-approved write-set override (Mismatch → Fix 3 in this commit): `game_profiles` added to USER_OWNED_TABLES (cloud/account.py — GDPR erasure gap), timestamp-prefix-agnostic log assertion (tests/test_clip_ready_marker.py), `game_profile_json="{}"` (tests/test_clip_selection.py, mirroring main.py's no-profile stub). The 10 remaining failures fail identically OUTSIDE the merge: test_generation_controls ×3 + test_subtitles ×2 fail on plain main too (cp1252 Windows reads without encoding="utf-8"); test_game_profiles ×3 + test_semantic_analyzer ×2 fail in ../openshorts-marsic as-shipped (AsyncMock session.begin() yields a plain coroutine under `async with`; cv2 frame extraction 0 != 3). User-approved: commit as-is + note; env-class failures are validate-stage input._
- [x] `cd dashboard && npm run build && npm run lint` exit 0
  _Note (implement, Phase 8): build exit 0; lint exits 1 with the known 35-problem debt in their byte-identical files (VoiceStylePresetsPage.jsx et al.) — the Phase 4/5 advisor-endorsed precedent: check-with-note, no phase owns those files, route to validate remediation._
- [x] `cd remotion && npm run build` exit 0; `test -f remotion/public/fonts/Anton-Regular.ttf`
- [x] `docker compose config` exit 0
  _Note (implement, Phase 8): this machine's Docker CLI (28.3.1) has no compose plugin, so the command itself cannot run here. Substitute check: PyYAML parse of docker-compose.yml passes (services backend/frontend/renderer, all with build; backend env_file present). Re-run on the deploy box in validate._
- [x] Single merge commit: `git status --porcelain` empty after the commit; `git rev-list --no-merges --first-parent HEAD~1..HEAD` returns nothing (docs land inside the merge, no side commits on the first-parent line)
  _Note (implement, Phase 8): selective staging (`git add -u` + `git add tests/test_no_double_route.py`) per user decision — bare `git add -A` would have swept untracked `.rpiv/` (workflow artifacts) and `.dirac-cache/` (tool cache) into the merge. rev-list is empty; porcelain shows only those two untracked dirs, which stay local by design._
- [x] `git merge-base --is-ancestor marsic/main HEAD` exits 0 — their 41 commits contained
- [x] `git log -1 --format=%B | grep -c "^- "` returns 7 (per-file policy list, one bullet per conflicted file)

#### Manual Verification:
- [x] Merge message reviewed end-to-end: 7 conflict policies + deletions + CI + compose + keyless note all present
- [x] README link at the "Runs fully local" bullet resolves (`#6-run-without-a-google-key-local-llm-optional` heading unchanged)
  _Note (implement, Phase 8): R3 kept the heading byte-identical; both R1 and the R8 intro link to the same slug._
- [x] CLAUDE.md boundary section read against the stage-ownership map (D5/D2/D12/D14/D15)
- [x] Accepted tension: base "Cómo se elige el layout" still says the layout picker calls Gemini; the Third-party LLM endpoint section below it states the reroute — base corpus is take-theirs, only their local-LLM section was rewritten (D1)
- [x] Post-commit: `git log --oneline -3` shows the merge on `merge/marsic-parity`; `main` untouched (fast-forward is validate's job)
  _Note (implement, Phase 8): merge commit d450d08 (parents de92009 + b1f5350); main still at de92009._

---

## Plan Review (Step 4)

_Independent post-finalization review by artifact-code-reviewer and artifact-coverage-reviewer subagents. Findings triaged at Step 5. Provenance: the first code-review dispatch returned no table (malformed output); a scoped retry completed it, excluding the prior run's verified-clean areas (render-service Dockerfile COPYs, CLAUDE.md ranges, merge-message bullet count, Phase 8 git gates)._

| source   | plan-loc          | codebase-loc                | severity   | dimension             | finding   | recommendation   | resolution         |
| -------- | ----------------- | --------------------------- | ---------- | --------------------- | --------- | ---------------- | ------------------ |
| code     | Phase 1 §2 (app.py) | ../openshorts-marsic/app.py:2320-2321 | concern | codebase-fit | E8's span "replace their :2322-2324 (comment + gate)" covers only the third comment line (:2322, the `(main.py llm_backend)` one) — their comment block actually runs :2320-2322, so :2320-2321 ("Self-host with an OpenAI-compatible server configured (LLM_BASE_URL) … the moment picker runs there") survive above the new comment and state false post-merge semantics (the moment picker dispatches through `ai_provider`, and the gate now covers X-LLM-*/LLM_* legs via `ai_backend_available`); the Phase 1 grep gates still pass because the surviving lines carry no `llm_backend` token — root cause is the design artifact's Architecture E8 anchor, which the plan transcribes verbatim | Extend the E8 replacement span to :2320-2324 in both the plan and the design's Architecture entry so both stale comment lines are removed | applied: E8 span extended to :2320-2324 (plan Phase 1 fence; design Architecture E8 mirrored in place) |
| coverage | Phase 8 §1 (CLAUDE.md) | CLAUDE.md:140-151 | concern | verification-coverage | FRD acceptance criterion "CLAUDE.md contains the stage-ownership boundary and the standing rule" (discover:84; req #9 at discover:52 defines the standing rule as "the side whose base you take owns the stage", discover:155 requires it to go into CLAUDE.md) is half-uncovered: the Phase 8 §1 fence ends the Stage-ownership section with "The env namespace split is the rule" (plan:1844) and never states the standing rule, and no Phase 8 criterion greps for it (:2019 checks only "Stage ownership"/"ai_backend_available") — the phrase "the side whose base you take owns the stage" appears nowhere in the plan (inherited 1:1 from the design's Slice 8 fence), so the criterion fails post-merge with no automated check to catch it, and the fix then requires redoing the single merge commit the Phase 8 :2025 gate asserts | Add the standing-rule sentence to the Phase 8 §1 Stage-ownership fence (and the same line in the design's Slice 8 fence, root cause upstream), then extend the :2019 grep to pin it, e.g. `grep -c "owns the stage" CLAUDE.md` returns 1 | applied: standing-rule sentence added to the Phase 8 §1 fence (wrapped so "owns the stage" sits on one line for the grep); Phase 8 criteria grep extended; design Slice 8 fence + criteria mirrored in place |

---


## Testing Strategy

### Automated:
- Each phase's `#### Automated Verification:` block is write-scoped to that phase's own files (greps, targeted pytest selections, scoped npm builds) and runs in the phase's shell on the single merge working tree, in phase order.
- Whole-repo gates (full `pytest -q`, `cd dashboard && npm run build && npm run lint`, `cd remotion && npm run build`, `docker compose config`) are owned by Phase 8, the terminal phase, on the finished tree — never by an earlier phase.
- `pytest -q` at Phase 8 includes their 10 new test files; those need `pytest-asyncio`, installed by the Phase 3 CI change (locally: `pip install pytest-asyncio`).

### Manual Testing Steps:
1. Persona e2e post-merge (validate stage): one job with Gemini only; one provider-only (`AI_PROVIDER=openai`); one no-keys job → clean 400, not a crash. Re-derive pinned counts (`grep -c 'startswith("LLM_")' app.py`) — namespaces grew.
2. Satellite spot-check: `thumbnail.py`/`saasshorts.py`/`layout_picker.py` still route via `llm_client` (grep `llm_client.chat` — unchanged files).
3. `git log --oneline marsic/main` fully contained in `merge/marsic-parity`; the merge message carries the per-file policy list.
4. One full video job completes end-to-end with Gemini; one with an OpenAI-compatible endpoint (Ollama Cloud acceptable) — FRD acceptance.

## Performance Considerations

- `ai_backend_available()` adds one in-memory env/config check per job start (leg 3 skips when legs 1-2 hit; `config_from` is an env read + tiny parse). No hot-path change.
- Job env gains the `LLM_*` triple per job — three more env vars in the child, same road as `GEMINI_API_KEY`.
- No N+1, no caching added, no loop changes. Pipeline concurrency (`MAX_CONCURRENT_JOBS`) untouched.

## Migration Notes

- **Env surface** (D12): `LLM_PROVIDER`, `LLM_TIMEOUT`, `OLLAMA_CONTEXT_LENGTH` vanish without code readers post-merge. `LLM_TIMEOUT` users must set `AI_TIMEOUT` (default dropped 600 s → 30 s — slow local models must raise it).
- **Keyless local-LLM** (D14): bare `LLM_BASE_URL` no longer activates the gate exemption or `/api/config` reporting. Migration: `LLM_API_KEY=<any placeholder>` (e.g. `ollama`), or `AI_PROVIDER=openai` + `OPENAI_BASE_URL=http://host.docker.internal:11434/v1` + `OPENAI_MODEL=<model>` (keyless-native; optional `OPENAI_API_KEY=ollama`).
- **Scenario** (their user's `.env`: `LLM_BASE_URL` set, `LLM_PROVIDER=openai`, no key): `LLM_PROVIDER` reads nothing; `llm_client` stays inert; gate rejects with hint until migrated per above.
- **Rollback**: the merge is one commit on a branch — `git checkout main` aborts; nothing pushed until validate passes.
- **Data**: no schema changes; no persisted state migrates. localStorage keys coexist (ours `geminiKey_v1`/`llmConfig_v1`; theirs additive).

## Developer Context

(Empty at skeleton write; Step 4 fallback notes and post-write developer interactions land here.)

## References

- Design: `.rpiv/artifacts/designs/2026-09-06_06-47-45_marsic-fork-parity-merge.md`
- Research: `.rpiv/artifacts/research/2026-09-06_06-09-59_marsic-fork-parity-merge.md`
- FRD: `.rpiv/artifacts/discover/2026-09-06_05-40-55_marsic-fork-parity-merge.md`
- Prior design (llm_client contract): `.rpiv/artifacts/designs/2026-08-30_08-58-35_openai-compatible-llm-provider.md`
- Prior validation (personas, pinned counts): `.rpiv/artifacts/validation/2026-09-05_15-07-32_connect-llm-provider-frontend.md`
- Their tree: `../openshorts-marsic` @ b1f5350 (read-only reference)
