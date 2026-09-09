---
date: 2026-09-06T05:20:29+0700
author: Yogiswara Utama
commit: de92009
branch: main
repository: openshorts
topic: "Optional Upload-Post API key"
confidence: high
complexity: medium
status: ready
verdict: pass
tags: [solutions, upload-post, social-publish, self-host, dashboard, mcp, delivery-strategy]
last_updated: 2026-09-06T05:20:29+0700
last_updated_by: Yogiswara Utama
---

# Solution Analysis: Optional Upload-Post API key

**Date**: 2026-09-06T05:20:29+0700
**Author**: Yogiswara Utama
**Commit**: de92009
**Branch**: main
**Repository**: openshorts

## Research Question

Make the Upload-Post API key fully optional on frontend and backend of OpenShorts. Self-host scope. Cloud and billing flows stay unchanged. The key stops blocking video generation. A server-side key suppresses missing-key UI. Keyless publish fails with a structured, machine-readable error that opens a contextual setup modal at the moment of intent. The standing banner becomes a soft, non-blocking suggestion.

Source: `.rpiv/artifacts/research/2026-09-06_04-51-32_optional-upload-post-key.md`. Behavior locked by the FRD: `.rpiv/artifacts/discover/2026-09-06_04-31-13_optional-upload-post-key.md` (8 decisions).

## Summary

**Problem**: All behavior is locked. The open question was the delivery and code-organization shape: phased in-place folds, one atomic slice, a capability-object refactor, or an env-flag rollout.
**Recommended**: Phased in-place folds - the recorded house behavior for this exact feature shape, with verified-safe phase boundaries.
**Effort**: Medium (~2-3 days)
**Confidence**: High

## Problem Statement

**Requirements:**
- Generation works keyless from ingest to download (`keysMissing` drops the `!uploadPostKey` term, `App.jsx:781`).
- A server-resolved key reports through `uploadPostConfigured` on `/api/config`, following the `llmConfigured` pattern end to end.
- Keyless publish returns `400 {"error": "upload_post_key_missing"}`. Missing profile returns `upload_post_profile_missing`.
- Self-host no-key reads return empty-state 200s. Cloud behavior stays byte-identical.
- A just-in-time setup modal opens on all four publish surfaces. The old modal keeps only the AI key.
- Banner and chip become soft suggestions, hidden when a key resolves.
- `tests/test_social_tenant_isolation.py` and `tests/test_billing_states.py` pass with zero edits.

**Constraints:**
- Hard: cloud and billing flows unchanged. Error bodies follow locked FRD Decision 6 shape. BYOK stays in encrypted localStorage.
- Hard: every push to main redeploys prod, ~5 min build plus rolling handover (`CLAUDE.md:318-361`).
- Soft: repo lint gate is `eslint . --max-warnings 0` (`dashboard/package.json:9`). Unused bindings fail it.
- Soft: no frontend test runner exists. Frontend verification is manual plus lint plus build.

**Success criteria:**
- FRD acceptance checklist passes, including the keyless-persona run (generate, edit, download, attempt publish).
- `curl /api/config` prints `uploadPostConfigured` false without env key, true with it.
- Publish click with no key opens the contextual modal. Cancel dismisses without navigation.
- Both perimeter test files pass unchanged.

## Current State

**Existing implementation:**
One resolver funnels every gate: `resolve_upload_post` (`app.py:172-188`) and `resolve_post_profile` (`app.py:191-213`). Five consumers branch on its result. The frontend gate `keysMissing` (`App.jsx:781`) blocks generation on a missing Upload-Post key although `/api/process` never checks it. Gates read only localStorage. The `llmConfigured` flag (`app.py:1903`, `AuthContext.jsx:152-160`, `App.jsx:779-782`) is the house pattern for server-config transport, landed 2026-09-05 (`f83d555..79817ac`).

**Relevant patterns:**
- Config-flag transport: `app.py:1892-1906` → `AuthContext.jsx:152-160` → `App.jsx:779-782` → prop threading. Used once.
- Shared error factory: `gemini_missing_error()` (`app.py:216-228`). Used once.
- Callback-prop idiom: `onConnectSocials` (`App.jsx:1975`), `onCreateClips` (`App.jsx:1702-1707`). Used on all leaf mounts.
- Phased same-day delivery: `f83d555..79817ac`, five phases, each green and committed (validation doc `2026-09-05_15-07-32`). Used once for the closest analogue.
- Same-day commit batch: `30ce67e..35e9d7e`, five logical commits in 34 seconds. Used once.

**Integration points:**
- `app.py:172-213` - resolver family: ordering fix, structured errors, flag input.
- `app.py:1892-1906` - `/api/config` gains `uploadPostConfigured` beside `llmConfigured`.
- `app.py:4435,4528,4618,5264,5601` - publish guards and analytics auth split.
- `mcp_server.py:337-342` - `_api_error` fallback for top-level `error`/`message`.
- `App.jsx:781,746,660-665` - gate drop, profiles chain guard and effect deps.
- `App.jsx:2013-2110,1239-1272` - modal collapse and copy reword.
- Four surfaces: `ResultCard.jsx`, `ScheduleWeekModal.jsx`, `ThumbnailStudio.jsx`, `SaaShortsTab.jsx`.
- Docs: `README.md:412`, `CLAUDE.md`, `skills/openshorts/*`, `examples/n8n/*`, `dashboard/seo/pages.js:951`.

**Coverage note**: the code graph (project `H-fork-openshorts`, generation 2026-08-30) predates HEAD `de92009`. The llm-provider changes of 2026-09-05 are invisible to it. Every material claim in this document was verified against live source by the per-candidate agents.

## Solution Options

### Option 1: Phased in-place folds
**How it works:**
Fold `uploadPostConfigured` into each existing gating idiom where it lives today. Deliver in five sequentially shippable phases: P1 backend (flag, structured 400s, empty-state 200s, ordering fix, new test module) → P2 transport (AuthContext normalize, App const) → P3 gates, copy, profiles chain, leaf props → P4 JIT modal and surface wiring → P5 MCP fallback and docs. Each phase commits lint-clean and green, mirroring `f83d555..79817ac`.

**Pros:**
- The repo shipped this exact shape for the closest analogue. The validation doc records each phase green with its commit hash.
- Phase 1 landing alone is verified safe on all five body consumers. The old UI never sees the new 200s or 400s unscreened.
- Review units stay small. The keyless-persona run can happen at P3, when the gate opens, not only at the end. The `56707b7` lesson (green suite, broken persona) is addressed at the moment of risk.
- No throwaway code at this ordering. The Upload-Post block moves out of the old modal in P3 and into the JIT modal in P4. Settings stays the setup home between them.

**Cons:**
- P1 is wider than any single precedent phase: about ten `app.py` sites across five endpoint families.
- P2 needs lint-deferral discipline. A const or leaf prop destructured before its consumer fails the zero-warning gate. The precedent plan pinned deferrals with grep-zero checks.
- Five pytest runs and four lint-and-build runs. Minutes total, but real.
- Between P1 and P5, MCP agents get the degraded `{"error": "HTTP 400"}` body. Degraded, not broken.

**Complexity:** Medium (~2-3 days)
- Files to create: 1 (`tests/test_upload_post_optional.py`, ~350-450 lines)
- Files to modify: ~11-15 (`app.py`, `AuthContext.jsx`, `App.jsx`, 5 components, `mcp_server.py`, docs)
- Risk level: Low-Medium

### Option 2: Single vertical slice
**How it works:**
The same code changes as Option 1, delivered as one commit set, one review, one deploy. Logical commits inside the set, following the `30ce67e..35e9d7e` batch shape.

**Pros:**
- One container ships both halves, so atomicity removes intermediate states rather than hiding them. There is no window where old frontend meets new backend.
- Rollback is one revert. Cost is one ~5 min deploy, the same as any phase push.
- It includes the exact couplings whose omission caused the two past forced follow-ups (`54bb701`, `9c7cae2`): the profiles chain and all four surfaces.
- Matches the demonstrated maximum single-session batch.

**Cons:**
- ~16 files, ~600-750 lines, at the upper edge of demonstrated single-review practice. Half sits in what becomes the largest recent test module.
- The manual persona sweep happens once, at the end. Past faults here were found by manual runs, not by the suite.
- Zero incremental safety. If the single review misses a harness fault, nothing catches it before prod. The verification event and the deploy event are the same event.

**Complexity:** Medium (~2 days, concentrated review)
- Files to create: 1 (same test module)
- Files to modify: ~16
- Risk level: Medium

### Option 3: Capability-context refactor
**How it works:**
Derive one social-capability object (`keyConfigured`, `canPost`, `onRequireUploadPostKey`) in `App` or a hook. All four surfaces plus the profiles chain consume it. The three gating idioms collapse.

**Pros:**
- One truth for "can I publish". Today the same predicate lives in five leaf forms plus two silent App derivations.
- Near parity in total lines with the in-place fold, ~15 lines either way.
- Prop wiring is sound in the App-derived variant. Every leaf already takes social props.

**Cons:**
- The literal formulas break locked behavior. `canPost` requiring `uploadUserId` regresses the managed short-circuit (`ResultCard.jsx:651`, `ScheduleWeekModal.jsx:116`). A `keyConfigured` containing `isManaged`, read by the `SaaShortsTab.jsx:1384` ternary, reopens the explicitly rejected `|| managed` case. A faithful object needs two predicates, not one.
- The `AuthContext` variant is dead on arrival. `uploadPostKey` state, its persistence effect, and the profiles chain all live in `App`.
- `SocialAnalyticsCard` mounts under `AccountPage`, a hash-routed sibling of `App`. No prop path exists. A hooks file creates a repo-first convention for a three-line derivation.
- First four-component signature rewrite in repo history. No frontend tests, no PropTypes: a member typo reads as `undefined` and fails silent. Manual matrix grows to ~48 cells including a new managed-persona row.

**Complexity:** Medium-High (~3 days)
- Files to create: 1 hook (optional) plus the test module
- Files to modify: ~14-17
- Risk level: High

### Option 4: Env-flagged rollout
**How it works:**
Land the full feature behind `UPLOAD_POST_OPTIONAL`, default off. Flip after a bake period. Remove the flag later.

**Pros:**
- A kill-switch without a git revert.
- The frontend half is sound: a `/api/config` field is runtime data, unlike build-time Vite env.
- Test control is cheap: the module-attribute monkeypatch pattern and the `conftest.py:11` freeze shape both exist.

**Cons:**
- No precedent: every env flag in this repo is a permanent operator knob. The one flipped knob (`AUTO_LAYOUT`, 2026-08-25) was never removed. The removal step stays open with no owner.
- The backend flag is import-frozen like `BILLING_ENABLED` (`app.py:91`). Every flip is a redeploy anyway, so the kill-switch costs the same ~5 min as a revert.
- About 18 sites gain a doubled branch. The test matrix needs ~10-12 twin pins of the old behavior, written then deleted. Three full matrix runs across the lifecycle.
- The rolling handover makes frontend-backend disagreement structural: both containers serve traffic during a deploy.

**Complexity:** High (~3-4 days across three deploys)
- Files to create: same test module plus twins
- Files to modify: same set plus ~50-60 gating lines
- Risk level: Medium

## Comparison

| Criteria | O1 Phased folds | O2 Vertical slice | O3 Capability object | O4 Env flag |
|----------|----------|----------|----------|----------|
| Approach shape | Extend in place, 5 sequenced phases | Extend in place, one atomic step | New abstraction layer (repo-first) | Behavioral fork plus removal lifecycle |
| Precedent fit | Fit | Fit | Concern | Concern |
| Integration risk | Fit | Fit | Concern (locked-decision break) | Concern |
| Migration cost | Fit | Fit (upper edge) | Concern | Concern (scope exceed) |
| Verification cost | Mild concern | Concern | Concern | Concern |
| Codebase fit | High | High | Low | Low |
| Risk | Low-Med | Med | High | Med |

## Recommendation

**(A) At least one candidate cleared the fit filter.**

**Selected:** Option 1 - Phased in-place folds.

**Rationale:**
- It is the recorded house behavior for the closest analogue. The llm-provider feature shipped five same-day phases, each green with its commit hash in the validation doc.
- Phase boundaries are verified safe, not assumed. Agent trace: all five body consumers handle the new bodies without crash. The empty-state 200s are invisible to the old frontend because the analytics card never mounts on self-host (`AccountPage.jsx:187`, `refreshMe` runs only when `billingEnabled` at `AuthContext.jsx:122-125`, unsigned users redirected at `main.jsx:41-43`). All three anchors grep-verified at HEAD.
- The riskiest boundary is named: P4, the JIT modal across four surfaces with three parser idioms, two of them buggy dead-fallback copies. Slicing puts the manual UX pass exactly there.
- The zero-edit perimeter constraint is verified compatible. `tests/test_social_tenant_isolation.py` pins status codes only and never issues an HTTP request. `tests/test_billing_states.py` never imports `app`.
- Verification cost stays at the one-shot baseline: the same single test module, spread over smaller review units.

**Why not alternatives:**
- Option 2: cleared the filter and is the runner-up. Its unique benefit, no intermediate states, buys little here because the intermediate states were verified safe or coherent. Its cost is concentration: the largest recent test module and the one-shot manual sweep land in a single review. Choose it if you want exactly one review and one deploy.
- Option 3: failed. The candidate's literal formulas violate locked behavior on two points. A faithful two-predicate object remains possible, but it buys near-zero line savings for a repo-first signature rewrite with no automated frontend safety net.
- Option 4: failed. Migration cost exceeds the topic scope. Every env flag here is a permanent knob. The flip costs the same redeploy as a revert.

**Trade-offs:**
- Accepting five deploys instead of one, for smaller review units and an early persona checkpoint.
- Accepting a degraded MCP error window between P1 and P5, for backend-first sequencing.
- Accepting three idioms kept in place, for the lowest-risk diff.

**Implementation approach:**
1. Phase 1 backend: `uploadPostConfigured` in `/api/config` beside `llmConfigured` (`app.py:1892-1906`, self-host env truth only). Structured 400s via a shared `JSONResponse` factory beside `resolve_upload_post` (precedent `gemini_missing_error`, `app.py:216-228`). Codes `upload_post_key_missing` and `upload_post_profile_missing`. Empty-state 200s on the self-host no-key branches (`app.py:4528-4531`, `:4618`). Cloud ordering fix at `app.py:183-184`. New `tests/test_upload_post_optional.py`.
2. Phase 2 transport: `AuthContext` normalize beside `llmConfigured` (`AuthContext.jsx:152-160`), App const. Defer leaf destructures to their consumer phases for the zero-warning lint gate.
3. Phase 3 gates and copy: drop the `!uploadPostKey` term (`App.jsx:781`). Extend the profiles chain guard and effect deps (`App.jsx:746`, `:660-665`). Fold `canPost` on both surfaces keeping the `isManaged ||` short-circuit and `uploadUserId` required: `isManaged || ((uploadPostKey || uploadPostConfigured) && uploadUserId)`. Fold the SaaShortsTab ternary (`&& !uploadPostConfigured`, never a bare `|| managed`). Reword chip and banner, hidden when any key resolves. Collapse the old modal to AI-key only. Run the keyless-persona e2e here.
4. Phase 4 JIT modal: one modal host in `App.jsx`, one callback prop per mount. Preemptive trigger routes a failed gate check to the callback. Reactive trigger parses `error === "upload_post_key_missing"` per surface, copying the fixed idiom at `ResultCard.jsx:619-628`. Rewrite the SaaShortsTab parser (`err.detail || 'Failed'` reads nothing from the new body).
5. Phase 5 MCP and docs: `_api_error` fallback to top-level `message` plus `error` (`mcp_server.py:337-342`). Docs: `README.md:412`, `CLAUDE.md` table, `skills/openshorts/*`, `examples/n8n/*`, `dashboard/seo/pages.js:951`.

**Integration points:**
- `app.py:172-213` - resolver family reorder plus error factory.
- `app.py:1903` - flag emission.
- `AuthContext.jsx:152-160` - flag normalize.
- `App.jsx:781,746,660-665,1239-1272,2013-2110` - gate, chain, copy, modals.
- Four leaf components - gate folds plus callback props.
- `mcp_server.py:337-342` - error fallback.

**Patterns to follow:**
- Config flag end to end: `app.py:1892-1906` → `AuthContext.jsx:152-160` → `App.jsx:779-782`.
- Error factory: `app.py:216-228`.
- Callback prop: `onConnectSocials` (`App.jsx:1975`).
- Parser idiom: `ResultCard.jsx:619-628`.
- Lint-clean phase commits with pinned deferrals: llm-provider plan `2026-09-05_12-20-26`, lines 27 and 280-284.

**Risks:**
- Phase 4 modal wiring: mitigated by the reactive trigger also catching stale-flag races, plus a manual four-surface pass.
- Flag lands after the async config fetch: the effect-deps change at `App.jsx:661-665` is mandatory, not optional.
- Copy escapes fenced line ranges: enumerate banner lead-in, chip label, chip title, modal title before editing.
- Keyless persona: e2e at Phase 3, per the `56707b7` lesson that a green suite does not prove the persona.

## Scope Boundaries

**What we are building:**
- The nine FRD functional requirements, delivered as five phases of in-place folds.

**What we are NOT doing:**
- No cloud or billing flow changes. No draft queue. No `publish_clip` user_id parameter. No `/api/me` additive field.
- No frontend test framework introduction. No capability object or hook file.
- No `fetchUserProfiles` alert JIT treatment (FRD follow-up candidate).
- No SaaShortsTab managed-gap fix beyond what the flag fold covers. The misconfigured-cloud-server residual stays documented.

## Testing Strategy

**Unit tests:**
- New module `tests/test_upload_post_optional.py`, ~20-28 cases: `/api/config` flag env set and unset. Three structured 400s with seeded jobs, sessions, and SaaS jobs. Profile codes discriminated from sibling plain 400s. `/api/social/user` keyless 200 plus cloud 400. Analytics family keyless bodies. DELETE keyless 404 byte-identity. Cloud 402 exact body. With-key passthrough via the `_upload_post_get` seam. MCP `/mcp` passthrough.
- Harness: `monkeypatch.delenv("UPLOAD_POST_API_KEY")` in every keyless test. Cloud-leg tests stub `managed_keys` and user resolution, because `tests/conftest.py:11` freezes `BILLING_ENABLED=0` before import. The with-key social-user test stubs `app_module.httpx.AsyncClient`, because that vendor call is inline.

**Integration tests:**
- `tests/test_social_tenant_isolation.py` and `tests/test_billing_states.py` pass with zero edits, as cloud-unchanged tripwires.
- `cd dashboard && npm run lint && npm run build` exit 0 at every frontend phase.

**Manual verification:**
- [ ] Keyless persona at Phase 3: generate, edit, subtitle, download, attempt publish.
- [ ] Phase 4 modal on all four surfaces: opens at intent, Cancel dismisses without navigation, paste-Enter persists, profiles auto-populate without reload.
- [ ] Env-key persona with empty localStorage: publish button enabled, no missing-key states.
- [ ] Banner and chip copy: soft suggestion, hidden when a key resolves.
- [ ] Full FRD acceptance checklist at Phase 5.

## Open Questions

**Resolved during research:**
- Does phasing show "Nothing published yet" to keyless self-hosters between backend and card-guard phases? No. The analytics card mounts only under `AccountPage`, which self-host users never reach signed-in, and unsigned users are redirected away. The two per-candidate agents disagreed. The mount-path trace (`AccountPage.jsx:187`, `AuthContext.jsx:122-125`, `main.jsx:41-43`, grep-verified at HEAD) resolves it.
- Can Phase 1 land before the frontend? Yes. All five body consumers were traced. None crash on the new bodies. The keyless 200 branches are unreachable from the old frontend.
- Is an env flag viable? No. The backend flag is import-frozen, every flip is a redeploy, and the repo has no flag-removal precedent.
- Does a capability object serve all consumers? No. One `keyConfigured` cannot serve both the canPost fold and the SaaShortsTab ternary without breaking the managed short-circuit or reopening the rejected `|| managed` case.

**Requires user input:**
- Push each phase as it lands, or batch the five phases into one push? Default: push per phase, following the llm-provider precedent. State your preference in chat if you prefer the batch.

**Blockers:**
- None.

## References

- `.rpiv/artifacts/research/2026-09-06_04-51-32_optional-upload-post-key.md` - the research this analysis consumed
- `.rpiv/artifacts/discover/2026-09-06_04-31-13_optional-upload-post-key.md` - locked FRD with 8 decisions
- `.rpiv/artifacts/plans/2026-09-05_12-20-26_connect-llm-provider-frontend.md` - phased-delivery precedent plan
- `.rpiv/artifacts/validation/2026-09-05_15-07-32_connect-llm-provider-frontend.md` - per-phase green record
- `app.py:172-213` - resolver family
- `dashboard/src/App.jsx:779-782` - derivation cluster
