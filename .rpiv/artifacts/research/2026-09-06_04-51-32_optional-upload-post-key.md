---
date: 2026-09-06T04:51:32+0700
author: Yogiswara Utama
commit: de92009
branch: main
repository: openshorts
topic: "Optional Upload-Post API key"
tags: [research, codebase, upload-post, social-publish, self-host, dashboard, mcp]
status: ready
last_updated: 2026-09-06T04:51:32+0700
last_updated_by: Yogiswara Utama
---

# Research: Optional Upload-Post API key

## Research Question

Make the Upload-Post API key fully optional on both frontend and backend of OpenShorts (self-host scope, cloud/billing flows unchanged). On self-host, the key stops blocking video generation; when a server-side key resolves, the UI shows no missing-key states; keyless publish attempts fail with a structured, machine-readable error that opens a contextual setup modal at the moment of intent; the standing banner becomes a soft, non-blocking suggestion. Chained from the discover FRD: `.rpiv/artifacts/discover/2026-09-06_04-31-13_optional-upload-post-key.md` (all 8 decisions locked there).

## Summary

The entire Upload-Post surface flows from one resolver: `resolve_upload_post` (`app.py:172-188`), which on cloud resolves an entitled user's managed key plus forced profile, and on self-host reads header → body key → `UPLOAD_POST_API_KEY` env. Every gate — three publish endpoints, `/api/social/user`, the analytics/scheduled family — branches on that result. The frontend gate (`keysMissing`, `App.jsx:781`) ORs in `!uploadPostKey` though `/api/process` never checks the key; dropping that term unblocks the pipeline. The server-key signal travels via the exact `llmConfigured` pattern (env-only `/api/config` field → `AuthContext` normalize → `App.jsx` const → prop threading), with the cloud half deliberately skipped: `fetchConfig` sends no token (`AuthContext.jsx:109`) so no user can resolve at `/api/config`, and cloud gates already key off `isManaged`. Publish errors become top-level `{"error": "upload_post_key_missing", "message": ...}` via a shared `JSONResponse` helper (locked FRD Decision 6 shape; a dict-detail would nest the code and render `[object Object]` in `SaaShortsTab.jsx:1463-1465`); the missing-profile 400 gets its own code `upload_post_profile_missing` (checkpoint decision); `mcp_server._api_error` needs a two-line fallback to surface top-level fields. The social user/analytics family flips to empty-state 200s on self-host no-key only (cloud 402 byte-identical; DELETE stays 404); `SocialAnalyticsCard` gains a stay-hidden-when-zero guard (checkpoint decision). The JIT modal is one modal host in `App.jsx` plus one callback prop (`onRequireUploadPostKey`-shaped) threaded to the four publish surfaces — the established callback-prop idiom. New tests land in a new module `tests/test_upload_post_optional.py`; both existing perimeter files get zero edits; `tests/conftest.py:11` freezes `BILLING_ENABLED=0` at import, so cloud-leg tests must stub `managed_keys` + user resolution. A pre-existing cloud ordering bug (profile created on Upload-Post before the managed-key check) is fixed in this feature (checkpoint decision).

## Detailed Findings

### Backend key resolution — the single funnel

- `resolve_upload_post` (`app.py:172-188`) returns `(api_key, forced_profile_or_None)`. Cloud (`app.py:180-185`): entitled user → `ensure_profile(user)` then `managed_keys.upload_post_key()`; non-entitled → `(None, None)` at `app.py:185`; header/body never read on this path. Self-host (`app.py:186-188`): `header or body_key or os.environ.get("UPLOAD_POST_API_KEY")`, profile always `None`.
- **Ordering bug (fixed in this feature, checkpoint decision)**: `ensure_profile` runs at `app.py:183` BEFORE the key read at `app.py:184`. On a misconfigured cloud server (`MANAGED_UPLOAD_POST_API_KEY` unset → `cloud/config.py:228-229` returns `""`), `cloud/social_profiles.py:22-23` builds `Authorization: Apikey ` with the empty setting and `cloud/social_profiles.py:51-55` performs the remote write; the request then fails anyway at the publish 400. Fix: read `managed_keys.upload_post_key()` first, return `(None, None)` when unset, run `ensure_profile` only after a key exists. Safe: all five consumers branch on the falsy key before touching the profile (`app.py:4435`, `:4528`, `:4610`, `:5264`, `:5601`), `resolve_post_profile` runs only after the key check (`app.py:4437`, `:5603`), and no test pins the order (`tests/test_social_tenant_isolation.py:29-84` pins `resolve_post_profile` + the schedule filter only).
- `resolve_post_profile` (`app.py:191-213`) fails closed: cloud without forced profile → 503 (`app.py:202-206`, pinned at `tests/test_social_tenant_isolation.py:36-45`); self-host `forced_profile or client_profile` empty → 400 "Missing Upload-Post user profile" at `app.py:212` (status pinned at `tests/test_social_tenant_isolation.py:56-59`, detail NOT pinned). `thumbnail_publish` inlines the self-host half instead of calling it (`app.py:5266-5268`) — any profile-error change touches both sites.
- Entitlement is precomputed: `cloud/managed_keys.py:11-18` reads `user.entitled`; the managed key maps empty-string env to `None` (`cloud/managed_keys.py:24-25`); `/api/me` already returns `entitled` (`cloud/auth.py:353`).

### `/api/config` flag — the `llmConfigured` pattern, cloud half skipped

- House pattern (from llm-provider-frontend, commits `f83d555`..`79817ac`): derive server-side in `get_config()` (`app.py:1892-1906`), report a boolean beside `llmConfigured` (`app.py:1903`); never the key (`app.py:1900-1902` comment); serve before auth.
- `uploadPostConfigured` = `bool(os.environ.get("UPLOAD_POST_API_KEY"))` on self-host, mirroring `_env_llm_config`'s billing early-return (`app.py:1879-1880`) — on cloud `/api/config` reports `False`, exactly like `llmConfigured` reports `None`-derived false in cloud.
- Why the cloud half is skipped (checkpoint decision): `fetchConfig` uses a bare `fetch` with no bearer (`AuthContext.jsx:103-115`, bare fetch at `:109`; only `apiFetch` attaches the token, `dashboard/src/lib/api.js:22-30`), and `get_config()` takes no `Request` (`app.py:1893`) — a caller there could never resolve. Cloud gates already key off `isManaged` (`AuthContext.jsx` value; `canPost` ORs it at `ResultCard.jsx:651`), so no UI outcome changes. An additive exact field (`entitled AND managed_upload_post_key`) could live on `/api/me` later if the misconfigured-server over-report needs closing (see Open Questions).
- Fetch resilience inherited for free: 3-attempt retry with backoff (`AuthContext.jsx:106-115`, D11); a failed fetch keeps init defaults — flag false on a configured server. Same accepted residual as `llmConfigured`.

### Publish endpoints — structured 400s and their five consumers

- Three no-key raises today: `post_to_socials` (`app.py:4435-4436`), `thumbnail_publish` (`app.py:5264-5265`), `saasshorts_post_to_socials` (`app.py:5601-5602`). All have the identical guard shape `if not upload_key:` after `resolve_upload_post`. Request models already accept an absent key: `SocialPostRequest.api_key` Optional (`app.py:4415`), thumbnail Form defaults `None` (`app.py:5256-5257`), `SaaSPostRequest.api_key` Optional.
- **Emission mechanism: shared helper next to `resolve_upload_post` returning a `JSONResponse`** — body `{"error": "upload_post_key_missing", "message": "Missing Upload-Post API key"}`. Top-level `error` matches locked FRD Decision 6 literally; `JSONResponse` is already imported/used (`app.py:25`, `:1865`, `:2280-2289`); no `@app.exception_handler` machinery exists in `app.py`. Precedent for a shared error factory: `gemini_missing_error()` (`app.py:216-228`). A dict-detail variant would nest the code under `detail` and render `[object Object]` in `SaaShortsTab.jsx:1463-1465` (`new Error(err.detail || 'Failed')`) — rejected.
- Companion fix required: `_api_error` (`mcp_server.py:337-342`) extracts only `resp.json().get("detail")`; with no `detail` key it degrades to `{"error": "HTTP 400", "http_status": 400}` — both code and message vanish for agents. Add `body.get("detail") or body.get("message")` (and surface a top-level `error` code).
- Five body consumers verified; none string-match the old copy (repo-wide grep for `Missing Upload-Post` matches only `app.py:212`, `:4436`, `:5265`, `:5268`, `:5602`):
  - `ResultCard.handlePost` parser (`ResultCard.jsx:701-708`) — the `jsonErr.detail || errText` fallback is dead code (inner throw re-caught by the same try's catch, re-throws raw text); the fixed idiom with `if (e.message !== errText) throw e;` exists at `ResultCard.jsx:619-628`; a third buggy copy sits at `ResultCard.jsx:341-348`.
  - `ScheduleWeekModal.jsx:156-158` — raw text throw, lands in per-clip `results[i].error`.
  - `ThumbnailStudio.jsx:411-412` — raw text throw.
  - `SaaShortsTab.jsx:1462-1465` — `err.detail || 'Failed'`.
  - `cli/openshorts_cli.py:185-189` posts to `/api/social/post`; `_die` prints `payload.get("detail", payload)` and `json.dumps` dict details (`cli/openshorts_cli.py:53-58`) — top-level body prints whole JSON, no change needed.
- **Profile error gets its own code (checkpoint decision)**: `upload_post_profile_missing` at `app.py:212` and the inline check `app.py:5266-5268`. Rationale: FRD Req 4 makes the profile 400 UI-reachable for the first time (server-key user, empty localStorage passes `canPost`, sends no `user_id`); MCP agents hit it today — the `publish_clip` tool schema exposes no `user_id` (`mcp_server.py:305-316`), so self-host env-key agent publishes die at `app.py:212`; and "plain 400 without a code" cannot be discriminated because the same handlers raise other plain 400s ("Job result not available" `app.py:4443-4444`, "No video available for this job" `app.py:5606-5607`).
- Guard order in each handler: job-existence 404 → key guard → profile resolution (`app.py:4428-4437`, `:5259-5268`, `:5597-5603`). Tests must seed the job/session dicts to reach the key guard.

### Social user + analytics/scheduled family — empty states, self-host only

- `get_social_user` (`app.py:4522-4579`): keyless self-host branch at `:4528-4531` flips 400 → `200 {"profiles": []}`. Cloud non-entitled keeps today's 400 (scope). In-family precedent: `{"profiles": [], "error": "No profiles found"}` at `app.py:4577` (vendor-empty case; the keyless body omits `error`). The vendor call is inline httpx (`app.py:4534+`), NOT the `_upload_post_get` seam — with-key tests must stub `app_module.httpx.AsyncClient`.
- `_social_analytics_auth` (`app.py:4608-4623`) has three exits: 402 `no_plan` cloud-no-key (`:4611-4617`), 400 self-host-no-key (`:4618`), normal return `resolve_post_profile(...)` (`:4623`). The split goes inside the existing `if not api_key:` block after the cloud check — replace the raise at `:4618` with `return None, None`. Cloud 402 stays byte-identical by construction: the cloud check runs first, entitled users never enter the block, and `BILLING_ENABLED` is read at call time (`app.py:4611`) so the monkeypatch pattern (`tests/test_social_tenant_isolation.py:32`) controls it. Keyless returns skip `_check_analytics_rate` (`:4619-4622`).
- Per-endpoint empty bodies (each consumer adds a guard right after its auth call, before any `_upload_post_get` call): `/api/social/user` → `{"profiles": []}`; `/api/social/analytics` (verbatim vendor proxy, `app.py:4635-4648`) → `{}`; `/api/social/analytics/posts` → `{"posts": []}` (consumer falls back `posts || data || items`, `SocialAnalyticsCard.jsx:43-45`; vendor shape uses `posts` first, `app.py:4728`); `/api/social/analytics/impressions` → `{"profile_username": null, "total_impressions": 0, "per_platform": {}}` (mirrors `app.py:4741-4745`); `/api/social/scheduled` → `{"profile_username": null, "scheduled_posts": []}` (the n8n flow computes the next free slot from this list — empty must mean "earliest slot is now", `examples/n8n/openshorts-content-machine.json:669`, `examples/n8n/README.md:53`).
- DELETE `/api/social/scheduled/{job_id}` (`app.py:4774-4790`): keyless stays **404 "Scheduled post not found"** — identical status and bytes to the membership raise at `app.py:4781`. A 200 would fabricate success (vendor DELETE never ran), and the never-confirm property ("no key" and "not yours" indistinguishable) requires it; a keyless GET returns `[]` so honest flows never obtain an id.
- **Exit 3 (missing profile with key resolved) keeps its 400 (checkpoint decision)**: `resolve_post_profile` at `app.py:4623` → `app.py:212`. The user chose "keep card hidden" over "uniform empty state", so the no-profile case is NOT extended to empty 200s; it now carries the structured `upload_post_profile_missing` code instead.
- **`SocialAnalyticsCard` gets a stay-hidden-when-zero guard (checkpoint decision)**: today any failure hides the card (`SocialAnalyticsCard.jsx:51` catch-all); with no-key 200s it would render "Nothing published yet" for every keyless self-hoster. Guard: when posts are empty AND impressions are zero, render null — preserving today's UX. Note the card calls with no `user` param (`SocialAnalyticsCard.jsx:29-30`) and `apiJson`/`apiFetch` never attach `X-Upload-Post-Key` (`dashboard/src/lib/api.js:22-30`), so on self-host the card only ever worked via the env key + auto-selected profile path — with the FRD change, keyless self-host gets 200-zeros (hidden by the guard), env-key-no-profile keeps the structured 400 (hidden by the catch).

### Frontend gating — keysMissing, canPost, the profiles chain

- `keysMissing = !billingEnabled && (needsAiBackend || !uploadPostKey)` (`App.jsx:781`); `handleProcess` aborts to the modal on it (`App.jsx:842-849`); `setShowKeyModal(true)` has exactly one caller (`App.jsx:847-849`). Drop the `!uploadPostKey` term — generation unblocks. The modal (`App.jsx:2013-2110`) collapses to AI-key-only: title ladder (`:2018-2021`), intro sentence naming Upload-Post (`:2044-2048`) and the Upload-Post block (`:2082-2110`, step copy `:2093-2096`) move out. The chip (`App.jsx:1239-1253`) and banner (`App.jsx:1259-1272`) compute the same ladder independently and must be reworded to a soft suggestion hidden when a local or server key exists. Copy surfaces escape fenced line ranges — enumerate banner lead-in, chip label, chip `title`, modal title (lesson from llm-provider).
- **The profiles chain is the load-bearing side effect.** `fetchUserProfiles` refuses when `!uploadPostKey && !isManaged` (`App.jsx:746`) and the auto-fetch effect gates identically (`App.jsx:660-665`). If `canPost` flips on the server flag while these stay localStorage-gated, `userProfiles` stays empty → `connectedPlatforms` prop is `null` (`App.jsx:1974`) → unknown-not-gated (`ResultCard.jsx:246-250`) → post leaves with `user_id: ''` and dies at the backend profile 400 (`app.py:210-213`). Fix: add the flag term to BOTH the guard (`App.jsx:746`) and the effect condition + deps (`App.jsx:661-665` — deps change is not optional; the flag lands after the async config fetch). The header logic already omits `X-Upload-Post-Key` for keyless callers (`App.jsx:749`) and the backend resolves env at `app.py:187-188`. Auto-select then populates `uploadUserId` (`App.jsx:756-758`) and `canPost` completes without reload.
- `canPost` fold (both `ResultCard.jsx:651` and `ScheduleWeekModal.jsx:116`): `isManaged || ((uploadPostKey || uploadPostConfigured) && uploadUserId)` — keep `uploadUserId` required; the backend demands a profile on self-host (`app.py:210-213`, `:5266-5268`). Downstream surfaces key off `canPost` automatically: disabled clauses (`ResultCard.jsx:1017`, `ScheduleWeekModal.jsx:187`), warning blocks (`ResultCard.jsx:1027-1032`, `ScheduleWeekModal.jsx:227-232`), refusal branch (`ResultCard.jsx:654-657`).
- Three gating idioms across the four surfaces:
  - Boolean pair: `ResultCard.jsx:650-651`, `ScheduleWeekModal.jsx:115-116` — fold as above.
  - Guard + render panel: `ThumbnailStudio.jsx:386-387` (alert guard) and `:1154-1167` (render ternary replacing the button; its "Go to Settings" button is dead code, `onClick={() => { }}` at `:1162-1166`) — keep both in sync via one local `socialsReady`-style value; prefer always-rendering the button and routing the click to the JIT callback (deletes the dead code, matches moment-of-intent).
  - Render-only ternary: `SaaShortsTab.jsx:1384-1385` checks `!uploadPostKey` alone — fold `&& !uploadPostConfigured`. This ternary also ignores the `managed` prop (`SaaShortsTab.jsx:44`) — a real pre-existing bug (managed cloud users see "Set your Upload-Post API key in Settings"): folding the flag fixes the entitled+key case as a consequence of FRD Req 4 and changes nothing for non-entitled cloud (flag false). Do NOT add a bare `|| managed`: an entitled user on a misconfigured cloud server would pass the gate and the publish would 400 anyway (cloud ignores body keys, `app.py:180-185`); that residual is cloud-only and documented.
  - Local `needsAiBackend` in `SaaShortsTab.jsx:48` and `ThumbnailStudio.jsx:78` is prop-derived by design (leaves add `!managed` because they can't see `billingEnabled`) — deliver the flag the same way, as a prop beside `uploadPostKey`.
- JIT modal wiring (checkpoint-free, structural): callback prop + modal host in `App.jsx`. Every leaf already receives social props drilled from `App` (render sites: SaaShortsTab `App.jsx:1557`, ThumbnailStudio `App.jsx:1694-1707`, ResultCard `App.jsx:1958-1981`, ScheduleWeekModal `App.jsx:2114-2121`); callback props are the established idiom (`onConnectSocials` `App.jsx:1975`/`ResultCard.jsx:251-255`, `onCreateClips` `App.jsx:1702-1707`). One new prop per mount routes a failed key check to the modal; the modal hosts beside the existing one (`App.jsx:2013`) where `setUploadPostKey` (`App.jsx:241-245`) and the `Modal` component live. The Upload-Post block moves (not copies) out of the old modal — one copy of the steps survives. Paste-Enter writes through `setUploadPostKey` (same shape as `App.jsx:2103-2107`), the persistence effect stores it (`App.jsx:639-646`), and the auto-fetch effect turns it into profiles + `uploadUserId` without reload (`App.jsx:660-665`). Two trigger layers: preemptive (click routes a failed `canPost`-equivalent check to the callback instead of disabling/alerting) and reactive (structured 400 `error === "upload_post_key_missing"` maps to the same callback — also catches stale-flag races). Reactive parsing must read the parsed code field, not stringify the body.

### MCP / agent surface

- `_tool_publish_clip` (`mcp_server.py:473-482`) posts only `job_id`, `clip_index`, `platforms` (+ optional title/description/scheduled_date/timezone) in-process via `httpx.ASGITransport` (`mcp_server.py:327-334`); `_FORWARD_HEADERS` includes `x-upload-post-key` (`mcp_server.py:53-56`) so an agent CAN pass a BYOK key as a client header even though the tool schema has no key parameter. Errors flow through `_api_error` (`mcp_server.py:337-342`) into tool `structuredContent` with `isError: true` (`mcp_server.py:580-589` area) — shape preserved by the FRD; only the `_api_error` fallback fix is needed.
- Tool description (`mcp_server.py:296-301`) contains no error copy; stays factually true. Self-host env-key agents still fail on the missing profile (`app.py:212`) — now structured `upload_post_profile_missing`; giving the tool a `user_id` param is a future enhancement, out of scope.
- Docs that reference the endpoints without parsing the body: `README.md:412` ("required, for social posting" — needs the optional clarification), `CLAUDE.md` endpoint table, `skills/openshorts/SKILL.md:54`, `skills/openshorts/reference.md:154/168` (verify response examples during implementation), `examples/n8n/README.md:50,96`, `examples/n8n/*.json`, `dashboard/seo/pages.js:951`.

### Test perimeter

- New module `tests/test_upload_post_optional.py` owns all HTTP-level cases (house pattern: `httpx.ASGITransport` + `AsyncClient`, cf. `tests/test_agent_uploads.py:14-16`; or sync `TestClient`, cf. `tests/test_llm_endpoints.py:21`). Both existing files get **zero edits**: `tests/test_social_tenant_isolation.py` is multi-tenant isolation charter (its `_upload_post_get` monkeypatch seam at `:80` is reusable knowledge); `tests/test_billing_states.py` never imports `app` (`tests/test_billing_states.py:13-14`) and stays the cloud-unchanged tripwire by simply passing.
- **Load-bearing harness fact**: `tests/conftest.py:11` sets `BILLING_ENABLED=0` before any import, so `app.py` imports no cloud package and `managed_keys` is `None`. Cloud-leg tests must stub BOTH `app_module.managed_keys` (e.g. `SimpleNamespace(has_active_entitlement=...)`) and the user resolution (`_user_from_request`/`get_current_user_optional`) or the cloud branch raises `AttributeError`, not 402.
- Every keyless test must `monkeypatch.delenv("UPLOAD_POST_API_KEY", raising=False)` — a developer machine with the env set flips outcomes silently. Env is read at request time (`app.py:188`), so per-test `setenv` works for the flag tests.
- Matrix: `/api/config` flag (env set/unset, assert beside `llmConfigured` at `app.py:1903`); three structured 400s (seed `jobs`/`thumbnail_sessions`/`saas_jobs` first — existence 404s run before the key guard); `/api/social/user` keyless 200 + cloud 400 unchanged; analytics family keyless bodies (section above); DELETE keyless 404 byte-identical to `app.py:4781`; cloud 402 exact-body `{"detail": {"error": "no_plan", "message": "Social analytics needs an active plan."}}`; with-key passthrough via the `_upload_post_get` seam (impressions sums, schedule filters `OTHER_TENANT` as pinned at `tests/test_social_tenant_isolation.py:83-84`); MCP `/mcp` tools/call passthrough of the structured 400 (harness `tests/test_mcp_endpoint.py:20-21`). No `_analytics_times` cleanup needed — keyless returns skip the rate limiter (`app.py:4619-4622`).

## Code References

- `app.py:172-188` — `resolve_upload_post`: key funnel, precedence header→body→env, managed path + ordering bug
- `app.py:191-213` — `resolve_post_profile`: fail-closed 503 cloud / 400 self-host (`:212` profile error site)
- `app.py:216-228` — `gemini_missing_error()`: shared error-factory precedent (dict detail, 402)
- `app.py:1869-1906` — `_env_llm_config` + `get_config`: the flag pattern to mirror (`:1879-1880` billing early-return, `:1903` flag field)
- `app.py:4410-4445` — `SocialPostRequest` (`:4415` Optional api_key) + `post_to_socials` guard order
- `app.py:4522-4579` — `get_social_user`: no-key raise `:4528-4531`, inline vendor call `:4534+`, empty precedent `:4577`
- `app.py:4608-4623` — `_social_analytics_auth`: three exits, cloud 402 `:4611-4617`, self-host 400 `:4618`
- `app.py:4626-4631` — `_upload_post_get`: the monkeypatch seam (four of five consumers route through it)
- `app.py:4690-4746` — impressions endpoint, composed result `:4741-4745`
- `app.py:4752-4766` — `_scheduled_posts_for`: profile filter (tenant isolation, do not simplify)
- `app.py:4765-4790` — `social_scheduled` + `social_cancel_scheduled` (404-never-confirms `:4781`)
- `app.py:5248-5268` — `thumbnail_publish`: no-key `:5264-5265`, inlined profile check `:5266-5268`
- `app.py:5590-5603` — `saasshorts_post_to_socials`: no-key `:5601-5602`
- `cloud/managed_keys.py:11-25` — `has_active_entitlement`, `upload_post_key` (empty→None)
- `cloud/config.py:220-231` — `managed_gemini_key` / `managed_upload_post_key` env properties
- `cloud/social_profiles.py:22-23` — `_auth_headers` builds the Apikey header from the setting
- `cloud/auth.py:345-360` — `/api/me` payload incl. `entitled` at `:353`
- `dashboard/src/App.jsx:241-245` — `uploadPostKey` encrypted-localStorage init; `setUploadPostKey`
- `dashboard/src/App.jsx:639-646` — persistence effect (encrypted key + plain user id)
- `dashboard/src/App.jsx:660-665` — profiles auto-fetch effect (guard + deps to extend)
- `dashboard/src/App.jsx:745-766` — `fetchUserProfiles`: guard `:746`, header `:749`, auto-select `:756-758`
- `dashboard/src/App.jsx:779-782` — `llmActive` / `needsAiBackend` / `keysMissing` derivations
- `dashboard/src/App.jsx:842-849` — `handleProcess` abort-to-modal (only `setShowKeyModal` caller)
- `dashboard/src/App.jsx:1205-1211` — header profile selector (appears once profiles load)
- `dashboard/src/App.jsx:1239-1272` — chip + banner (ladder copy to reword)
- `dashboard/src/App.jsx:1392-1424` — Settings Upload-Post section (BYOK home, unchanged)
- `dashboard/src/App.jsx:1557` — SaaShortsTab mount (props site)
- `dashboard/src/App.jsx:1694-1707` — ThumbnailStudio mount
- `dashboard/src/App.jsx:1876` — schedule-week open button
- `dashboard/src/App.jsx:1958-1981` — ResultCard mount; `connectedPlatforms` prop at `:1974`
- `dashboard/src/App.jsx:2013-2110` — blocking modal: title `:2018-2021`, intro `:2044-2048`, UP block `:2082-2110`, steps `:2093-2096`, paste `:2103-2107`
- `dashboard/src/App.jsx:2114-2121` — ScheduleWeekModal mount
- `dashboard/src/contexts/AuthContext.jsx:103-131` — `fetchConfig` (bare fetch `:109`, 3-attempt retry)
- `dashboard/src/contexts/AuthContext.jsx:152-160` — `llmConfigured` normalize + context value (add flag beside)
- `dashboard/src/lib/api.js:22-44` — `apiFetch`: bearer-only, 402→QuotaError; never `X-Upload-Post-Key`
- `dashboard/src/components/ResultCard.jsx:39` — props signature
- `dashboard/src/components/ResultCard.jsx:246-255` — `connectedPlatforms` null-vs-empty semantics, connect fallback
- `dashboard/src/components/ResultCard.jsx:650-663` — `canPost` + refusal branches
- `dashboard/src/components/ResultCard.jsx:699-708` — error parser (dead `jsonErr.detail` fallback); fixed idiom `:619-628`; buggy copy `:341-348`
- `dashboard/src/components/ResultCard.jsx:1010-1032` — publish button disabled clause + warning block
- `dashboard/src/components/ScheduleWeekModal.jsx:76,115-119` — props + `canPost` + silent return
- `dashboard/src/components/ScheduleWeekModal.jsx:156-158,187,227-232` — raw parser, disabled clause, warning block
- `dashboard/src/components/ThumbnailStudio.jsx:74-78` — props + local `needsAiBackend`
- `dashboard/src/components/ThumbnailStudio.jsx:386-412` — publish guard + raw parser
- `dashboard/src/components/ThumbnailStudio.jsx:1154-1167` — "Not Configured" panel with dead button `:1162-1166`
- `dashboard/src/components/SaaShortsTab.jsx:44-48` — props + local `needsAiBackend`
- `dashboard/src/components/SaaShortsTab.jsx:1384-1385` — render ternary ignoring `managed` (pre-existing gap)
- `dashboard/src/components/SaaShortsTab.jsx:1445-1466` — publish payload + `err.detail || 'Failed'` parser
- `dashboard/src/components/SocialAnalyticsCard.jsx:19-56` — card fetch (no user param `:29-30`), fail-hidden `:51`, fallbacks `:43-45`
- `mcp_server.py:53-56` — `_FORWARD_HEADERS` includes `x-upload-post-key`
- `mcp_server.py:296-316` — `publish_clip` description + schema (no user_id/api_key)
- `mcp_server.py:327-342` — in-process client + `_api_error` (detail-only extraction)
- `mcp_server.py:473-482` — `_tool_publish_clip`
- `cli/openshorts_cli.py:53-58,185-189` — CLI publish + `_die` detail printing
- `tests/conftest.py:11` — `BILLING_ENABLED=0` frozen pre-import (cloud-leg stub requirement)
- `tests/test_social_tenant_isolation.py:29-84` — pinned contracts + reusable monkeypatch patterns
- `tests/test_billing_states.py:13-30` — cloud-only imports; untouched tripwire
- `README.md:405-414` — env table; `:412` "required" wording to soften

## Integration Points

### Inbound References
- `dashboard/src/App.jsx:1958-1981,1557,1694-1707,2114-2121` — the four publish surfaces consume `uploadPostKey`/`uploadUserId`/`isManaged` props; each gains the flag + JIT callback
- `dashboard/src/App.jsx:746,660-665` — profiles chain consumes the flag (guard + effect)
- `mcp_server.py:473-482` — `publish_clip` tool → `/api/social/post` in-process with forwarded headers
- `cli/openshorts_cli.py:185-189` — CLI publish → `/api/social/post`
- `examples/n8n/openshorts-content-machine.json:669` + `examples/n8n/README.md:53,96-98` — scheduled GET (empty list = slot free) and DELETE flows
- `dashboard/src/components/SocialAnalyticsCard.jsx:29-30` — impressions + posts endpoints, no user param
- `dashboard/src/contexts/AuthContext.jsx:103-131` — `/api/config` consumer (flag transport)

### Outbound Dependencies
- Upload-Post vendor API via httpx: `api.upload-post.com/api/uploadposts/users` (`app.py:4534+`), `/schedule` (`app.py:4758-4759` area), impressions/posts proxies via `_upload_post_get` (`app.py:4626-4631`)
- `cloud/social_profiles.py` remote profile creation + `_auth_headers` (managed key)
- `cloud/managed_keys.py` + `cloud/config.py` settings for entitlement and managed keys

### Infrastructure Wiring
- Routes: `/api/config` (GET), `/api/social/post`, `/api/social/user`, `/api/social/analytics{,/posts,/impressions}`, `/api/social/scheduled` (+DELETE), `/api/thumbnail/publish`, `/api/saasshorts/post`, `/mcp`
- Env: `UPLOAD_POST_API_KEY` (self-host server key), `MANAGED_UPLOAD_POST_API_KEY` (cloud managed), `BILLING_ENABLED` (mode split; read at call time in `_social_analytics_auth`, frozen at import in `app.py`)
- Test wiring: `tests/conftest.py:11` env freeze; `monkeypatch.setattr(app_module, "BILLING_ENABLED", ...)` pattern; `_upload_post_get` seam

## Architecture Insights

- **One resolver, every gate.** `resolve_upload_post` + `resolve_post_profile` are the single seam for key/profile policy; all handler branches are downstream of their return. Changes concentrate there; the `llmConfigured`-style config flag is its read-only projection.
- **`llmConfigured` is the house pattern for server-config → UI**: mode-split derivation (self-host env truth; cloud reports false), never the key, served pre-auth, normalized in `AuthContext`, consumed as an `App.jsx` const, threaded as props. `uploadPostConfigured` copies it end to end; cloud relies on `isManaged` instead (checkpoint).
- **BYOK precedence is stable**: header → body → env on self-host; cloud ignores client keys entirely (entitlement + managed key only). The feature never reorders precedence — it only reports the env tier and changes failure shapes.
- **Fail-closed posture must survive empty states**: self-host owns the account behind its key; cloud never trusts client values (`resolve_post_profile` docstring). Empty 200s are truthful reads (empty list), DELETE stays 404-never-confirms; `upload_post_key_missing` / `upload_post_profile_missing` / `402 no_plan` stay distinct codes.
- **No frontend error choke point**: `apiFetch` only special-cases 402; each surface parses errors itself, in three idioms (two buggy dead-fallback copies). The JIT reactive trigger must parse the `error` code field per surface; the fixed parser idiom exists at `ResultCard.jsx:619-628` to copy.
- **Three gating idioms, one flag fold**: boolean `canPost` pair (keep `uploadUserId`), guard+panel pair (keep in sync via one local value), render-only ternary (fold; also fixes a managed-gap as a side effect). Prop threading beats context here — every leaf already takes social props; `AuthContext` carries config/auth, not UI actions.
- **`BILLING_ENABLED` has two lives**: frozen at import (`app.py` module init, `tests/conftest.py:11` depends on it) and read at call time in `_social_analytics_auth` (`app.py:4611`) — which is what makes the cloud/self-host branch split test-controllable and why cloud-leg tests need `managed_keys` stubbed.

## Precedents & Lessons

5 similar past changes analyzed.

### Precedent: LLM-provider frontend — gate drop + `llmConfigured` + banner/badge/modal copy rework
**Commit(s)**: `f83d555` "Phase 1: Backend — status channel + connection probe", `448d042` "Phase 2: Browser state", `a30694e` "Phase 3: The AI Provider settings card", `94d8659` "Phase 4: Unblock the app — headers, gates, copy", `79817ac` "Phase 5: Per-feature capability split" (2026-09-05)
**Blast radius**: 11 files across backend, context, components — app.py, llm_client.py, tests, AuthContext.jsx, App.jsx, ThumbnailStudio.jsx, SaaShortsTab.jsx

**Follow-up fixes**:
- `56707b7` "fix(process): provider-only jobs no longer die at launch on a None GEMINI_API_KEY env" (2026-09-05) — `None` written into job subprocess env crashed every provider-only job; latent since the gate first required the key; found by manual run, not the suite.

**Lessons from docs**:
- `.rpiv/artifacts/research/2026-09-04_20-47-42_connect-llm-provider-frontend.md` — failed config fetch left `llmConfigured` false forever → permanent banner on a configured server; retry with backoff (already in `AuthContext`). Copy surfaces escape fenced line ranges.
- `.rpiv/artifacts/validation/2026-09-05_15-07-32_connect-llm-provider-frontend.md` — `test_existing_fields_are_unchanged` pins `/api/config` fields; key never echoed.

**Takeaway**: Widening a gate exposes latent "the key is always present" assumptions downstream — e2e a real keyless persona, not just the suite.

### Precedent: Managed users could not post — gates read only localStorage
**Commit(s)**: `0311b61` "fix(social): let managed users post without BYOK Upload-Post key" (2026-07-17) — ResultCard/ScheduleWeekModal gates gained `isManaged ||`.
**Follow-up fixes**:
- `54bb701` "fix(social): stop 'Error fetching User Profiles' alert loop" (2026-07-17) — background fetch without token alerted on every load; fix: `apiFetch`, keyless fetch allowed, silent auto-fetch.

**Takeaway**: Server-resolved keys must reach the frontend gate, and background fetches fail silent — exactly the profiles-chain risk this feature must handle at `App.jsx:746,660-665`.

### Precedent: Publish gated at point of intent with guided connect flow
**Commit(s)**: `8d41112` "feat(ui): gate publishing when no social accounts are linked" (2026-07-18); follow-up `9c7cae2` (2026-08-29) — copy gaps across sibling surfaces read as broken integration.

**Takeaway**: Gate at the publish action with an actionable path — and carry honest copy into EVERY surface sharing the action (four surfaces here).

### Precedent: Social analytics/scheduling batch + fail-closed profile posture
**Commit(s)**: `be5b727`, `149ca85` (2026-08-21), `36cf2e4` (+ `tests/test_social_tenant_isolation.py`), follow-up `05c0238` — vendor impressions endpoint returned account-wide numbers for a zero-post profile (cross-tenant leak).

**Takeaway**: Empty-state 200s must not weaken fail-closed posture; keep `upload_post_key_missing` distinct from `402 no_plan` and from fail-closed refusals.

### Precedent: API keys + MCP server
**Commit(s)**: `f10655f` (2026-08-03) — in-process MCP; `e96407b` (2026-09-04) added BYOK-header forwarding.

**Takeaway**: A changed publish error shape flows into `publish_clip` through the in-process callback — test the MCP path when changing the 400 body.

### Composite Lessons
- Wide gates expose latent key-presence assumptions — both prior gate-widenings shipped same-day follow-ups (`54bb701`, `56707b7`). E2e the keyless persona: generate → edit → download → attempt publish. (High)
- A `/api/config` flag is only as good as its fetch path — reuse the 3-attempt retry; pin empty-state behavior in a test. (High)
- Self-host empty states must not leak into cloud paths — `BILLING_ENABLED` split verified per branch; run `tests/test_social_tenant_isolation.py` + `tests/test_billing_states.py` unchanged. (High)
- Copy escapes fenced line ranges — enumerate banner lead-in, chip label, chip `title`, modal title when rewording. (Medium)
- Background fetches fail silent; only manual actions alert. (Medium)

## Historical Context (from `.rpiv/artifacts/`)
- `.rpiv/artifacts/discover/2026-09-06_04-31-13_optional-upload-post-key.md` — the FRD this research answers (8 locked decisions, requirements, acceptance criteria)
- `.rpiv/artifacts/research/2026-09-04_20-47-42_connect-llm-provider-frontend.md` — the `llmConfigured` precedent research (config fetch resilience, copy-surface lessons)
- `.rpiv/artifacts/validation/2026-09-05_15-07-32_connect-llm-provider-frontend.md` — validation doc for the pattern this feature copies
- `.rpiv/artifacts/research/2026-08-30_16-03-23_connect-llm-provider-frontend.md` — earlier iteration of the same research
- `.rpiv/artifacts/research/2026-08-30_15-13-19_openai-compatible-third-party-llm-endpoint-alongside-gemini-additive.md` — additive-endpoint precedent (config precedence style)

## Developer Context

**Q (discover: Pipeline works keyless end-to-end): on self-host a missing Upload-Post key alone blocks generation (`keysMissing` ORs it in) though `/api/process` never checks the key. Keep as a goal: the pipeline must work keyless from ingest to download?**
A: Yes — free pipeline.

**Q (discover: Server-resolved keys count as configured): when a server-side key resolves, must the UI avoid missing-key states?**
A: Yes — report server key (self-host env tier via `/api/config`, following `llmConfigured`).

**Q (discover: "Backend optional" = structured 4xx + empty connection state): keyless publish attempts fail with a clear, actionable error — and connection checks stop hard-failing?**
A: Yes — JIT error + empty state, no queueing (draft-queue rejected).

**Q (discover: Scope: self-host surface): which deployment surface does this feature target?**
A: Self-host focus — cloud flows stay as-is.

**Q (discover: Banner + chip reworded to soft suggestion): what happens to the banner/chip once generation is keyless?**
A: Reword to soft, non-blocking suggestion (preserve the attach-rate signal; hidden when a key resolves).

**Q (discover: Structured error code): what response shape for keyless publish failures?**
A: Structured code — `{"error": "upload_post_key_missing"}` on the three publish 400s (top-level per the FRD body shape).

**Q (discover: JIT contextual setup modal): what does the user see on Publish with no key?**
A: Contextual modal with the existing 4-step copy + inline paste field; Cancel keeps them on the card.

**Q (discover: Old modal becomes AI-key only): fate of the current blocking modal?**
A: AI-key only — fires for `needsAiBackend` alone; Upload-Post block moves out (Settings + JIT modal own it).

**Q (`app.py:212`, `app.py:5266-5268`, `mcp_server.py:305-316`): FRD Req 4 makes the missing-profile 400 reachable for the first time, and MCP agents hit it today (tool schema has no `user_id`). Should the profile error get its own structured code alongside `upload_post_key_missing`?**
A: Yes — own code `upload_post_profile_missing` at both sites; JIT flow and agents discriminate without string matching (pinned test checks status only).

**Q (`app.py:1892-1906`, `AuthContext.jsx:109`): `/api/config` cannot resolve a cloud user (tokenless bare fetch, no `Request`) — how is FRD Req 4's cloud clause satisfied?**
A: Skip it for cloud — `/api/config` reports the env tier only (exact `llmConfigured` pattern, which is also self-host-only); cloud gates keep using `isManaged`, which already enables publishing. Optional future: additive exact field on `/api/me`.

**Q (`app.py:183-184`, `cloud/social_profiles.py:22-23`): entitled user's first call creates a remote profile under an empty key before the managed-key check — fix in this feature?**
A: Yes, fix now — read the key first, return `(None, None)` when unset, `ensure_profile` only after a key exists; verified response-identical and unpinned.

**Q (`SocialAnalyticsCard.jsx:51`, `app.py:4618`, `app.py:4623`): empty-state 200s would render "Nothing published yet" for every keyless self-hoster — what behavior?**
A: Keep the card hidden when zero (frontend guard); no-key branches flip to 200 per the FRD; the missing-profile case (exit 3) keeps its 400, now with the structured profile code.

## Related Research
- `.rpiv/artifacts/research/2026-09-04_20-47-42_connect-llm-provider-frontend.md` — the config-flag pattern this feature copies (`llmConfigured` transport end to end)
- `.rpiv/artifacts/research/2026-08-30_15-13-19_openai-compatible-third-party-llm-endpoint-alongside-gemini-additive.md` — additive config/env precedence style

## Open Questions

From the FRD (carried forward verbatim):
- None — every node resolved during the interview; nothing was deferred.

Residuals from research (documented, not blocking):
- `SaaShortsTab.jsx:1384` managed-gap residual: an entitled cloud user on a server with `MANAGED_UPLOAD_POST_API_KEY` unset still sees "Set your Upload-Post API key in Settings" and, if the gate were opened via `|| managed`, publish would 400 anyway (cloud ignores body keys). Cloud-only, pre-existing; folding the flag fixes the configured-server case; an exact `/api/me` field would close the rest later.
- `publish_clip` tool schema still exposes no `user_id` (`mcp_server.py:305-316`), so self-host env-key agent publishes fail with the (now structured) profile error. A tool-level param is a future enhancement.
- `fetchUserProfiles` alerts "No profiles found for this API Key." in non-silent mode (`App.jsx:760-763`) — candidate for the same JIT treatment later (FRD follow-up).
- `README.md:412` still says "required, for social posting" — docs update lands with this feature (one-line optional clarification).
- The empty-state flip's card-visibility change is handled by the stay-hidden guard; if the "Nothing published yet" surface is ever wanted as an attach-rate signal, it is a one-line guard removal.
