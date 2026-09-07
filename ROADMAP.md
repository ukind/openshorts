Absolutely. This is the point where I would **freeze the architecture and roadmap**.

Below is the **canonical English project specification**. It combines the original requirements you gave at the beginning, the decisions we made afterward, the architecture correction about multimodal candidate detection, the work that has actually been completed, the remaining phases, and the Git/AI-agent workflow.

From this point forward, this document should be treated as the **source of truth**. A future AI should not invent new phases, remove requirements, or reinterpret the architecture unless you explicitly change this specification.

The original project specification explicitly defined the staged architecture, Game Profiles, audio events, multimodal analysis, deterministic scoring, payoff optimization, subtitle enhancement, Twitch metadata, caching, reproducibility, UI, performance, failure handling, testing and documentation.

---

# OPENSHORTS — MASTER PROJECT SPECIFICATION

## Multimodal Twitch Viral Clip Editor

**Status:** Frozen / canonical roadmap
**Purpose:** Long-term handoff document for ChatGPT, Qwen, coding agents, or any future AI.

---

# 1. PROJECT VISION

OpenShorts is being evolved into a **multimodal Twitch gaming clip editor**, optimized primarily for:

* multiplayer gaming;
* horror gaming;
* chaotic/co-op gameplay;
* reaction-heavy Twitch content.

The final system should behave like a human Twitch editor who understands:

* what happened;
* what the streamer said;
* what happened visually;
* what happened acoustically;
* what game is being played;
* where the setup begins;
* where the peak occurs;
* where the payoff happens;
* whether the moment makes sense without the original VOD.

The system must **not** simply search transcripts for exciting sentences.

The core principle is:

> **LLMs provide structured observations and semantic understanding. OpenShorts owns deterministic scoring, weights, ranking, caching, reproducibility, boundaries and rendering.**

This principle comes directly from the original specification.

---

# 2. FINAL TARGET ARCHITECTURE

The canonical pipeline is:

```text
VOD
 │
 ├── Whisper / transcript
 ├── Scene detection
 ├── Audio event extraction
 └── Cheap visual/activity signals
          │
          ▼
 MULTIMODAL EVENT / CANDIDATE DETECTION
          │
          ▼
 Candidate windows with surrounding context
          │
          ▼
 TEXT LLM CONTEXTUAL ANALYSIS
          │
          ▼
 Top candidates
          │
          ▼
 VISION LLM ANALYSIS
          │
          ▼
 Structured observations + component scores
          │
          ▼
 GAME PROFILE + UNIVERSAL VIRAL WEIGHTS
          │
          ▼
 DETERMINISTIC VIRAL SCORING
          │
          ▼
 PAYOFF-AWARE BOUNDARY OPTIMIZATION
          │
          ▼
 FINAL RANKING
          │
          ├───────────────┐
          ▼               ▼
      CLIP OUTPUT     VOD METADATA
          │
          ▼
 SUBTITLE ENHANCEMENT
          │
          ▼
 VIDEO RENDERING
```

The important architectural correction is:

**Candidate generation must NOT depend primarily on transcript significance.**

A moment with:

```text
silence
+
sudden scream
+
visual jumpscare
+
camera movement
+
reaction
```

must still become a candidate even when Whisper provides almost no meaningful text.

The original specification already anticipated this by requiring candidate generation from transcript, word timestamps, scene boundaries, silence, sudden loudness and audio activity, followed by a dedicated audio-event layer.

---

# 3. CORE DESIGN PRINCIPLE — MULTIMODAL CANDIDATE DETECTION

This is now **locked**.

Do NOT implement:

```text
Whisper
↓
Text-only scoring
↓
Candidates
↓
Vision
```

because it can lose visually/acoustically viral moments.

Instead:

```text
Whole VOD
↓
cheap event extraction
↓
candidate generation
↓
Text LLM
↓
Vision LLM
↓
final scoring
```

Candidate generation should use inexpensive signals such as:

* Whisper transcript;
* word-level timestamps;
* speech density;
* silence;
* sudden loudness;
* audio activity;
* scene boundaries;
* scene changes;
* keywords;
* screams;
* laughter;
* gasps;
* basic visual/activity changes;
* suspicious motion/activity peaks.

The candidate detector answers:

> **“Something potentially important happened here.”**

It does NOT answer:

> **“This is definitely a viral clip.”**

That decision belongs to later stages.

---

# 4. GAME PROFILE SYSTEM

## Goal

Game Profiles are persistent reusable entities, **independent from individual VOD processing jobs**.

A Game Profile must be created once and reused across many VODs.

## Required functionality

* Create
* Edit
* Rename
* Duplicate
* Delete
* Import
* Export
* Select
* Reset generated values

## Required data

Each profile should support:

* unique ID
* profile name
* game title
* Steam App ID
* original Steam description
* Steam genres
* Steam tags/categories
* AI-generated game description
* user-editable custom description
* viral scoring weights
* custom AI instructions
* profile version
* creation timestamp
* modification timestamp

Original Steam data must remain separate from user-editable data.

The original Steam description must **never be overwritten**.

---

# 5. STEAM GAME LOOKUP

When creating a Game Profile:

```text
User enters game title
        ↓
Steam search
        ↓
multiple results if necessary
        ↓
user selects game
        ↓
retrieve Steam metadata
        ↓
save permanently
        ↓
Text LLM analyzes game
        ↓
generate initial scoring weights
        ↓
user reviews/edits weights
```

This happens **once when creating/updating the Game Profile**, not every time a VOD is processed.

---

# 6. GAME-SPECIFIC VIRAL WEIGHTS

The profile must adapt scoring to the game.

### Example — Horror

Potentially prioritize:

* fear
* surprise
* player reaction
* screams
* visual reveal
* tension
* suspense
* payoff

### Example — Comedy

Potentially prioritize:

* humor
* timing
* reaction
* absurdity
* punchline
* surprise

### Example — Competitive

Potentially prioritize:

* skill
* clutch
* comeback
* mechanical execution
* surprise
* reaction

### Example — Co-op

Potentially prioritize:

* teamwork
* chaos
* communication
* betrayal
* reaction
* humor
* unexpected events

Weights must remain numeric and manually editable.

The final score must always retain a **universal viral component**, so an exceptional moment is not discarded merely because it does not perfectly fit the game's dominant genre.

---

# 7. AI PROVIDER ARCHITECTURE

The pipeline must be provider-independent.

## Required abstraction

Separate:

```text
TextLLMProvider
VisionLLMProvider
```

Initial providers:

* Gemini
* OpenAI-compatible provider

LM Studio must work through the OpenAI-compatible provider.

Do NOT create a hardcoded LM Studio-specific pipeline.

The architecture should remain compatible with future:

* Ollama
* llama.cpp server
* vLLM
* other OpenAI-compatible servers

without modifying the core analysis pipeline.

---

# 8. OPENAI-COMPATIBLE PROVIDER

Must support:

* configurable base URL
* optional API key
* model
* temperature
* max tokens
* timeout
* retries
* model discovery
* connection testing
* structured JSON
* JSON Schema where supported

Must support:

```text
/v1/chat/completions
```

and model discovery when available.

---

# 9. LOCAL / DOCKER SUPPORT

Local AI must work:

1. natively on host;
2. inside Docker;
3. with LM Studio on host;
4. with LLM in another container;
5. with LLM on another machine.

Do not hardcode:

* localhost;
* 127.0.0.1;
* LM Studio ports;
* model names.

Support configurable endpoints such as:

```text
http://localhost:1234/v1
http://host.docker.internal:1234/v1
```

with appropriate Linux alternatives.

Document Docker networking clearly.

---

# 10. CURRENT LOCAL AI BASELINE

Current verified runtime:

```text
Provider:
OpenAI-compatible

Server:
LM Studio

Endpoint inside Docker:
http://host.docker.internal:1234/v1

Vision model:
qwen3-vl-8b-instruct
```

This is **current environment state**, not a hardcoded project requirement.

---

# 11. WHOLE-VOD CHEAP EVENT EXTRACTION

The entire VOD must be scanned cheaply before expensive multimodal analysis.

Possible signals:

### Transcript

* transcript text
* word timestamps
* speech density
* keywords

### Audio

* silence
* sudden loudness
* screams
* laughter
* gasps
* excited speech
* overlapping speech
* abrupt transitions

### Video/activity

* scene changes
* activity/motion changes
* visual events where cheap enough

The output is a structured event timeline.

Example:

```text
24.1s entity appears
24.3s player turns camera
24.7s sudden loudness
25.0s reaction
25.2s scream
25.8s second player screams
27.1s laughter
```

The original specification explicitly calls for this structure.

---

# 12. TEXT LLM ANALYSIS

For every shortlisted candidate, provide:

* candidate transcript
* surrounding transcript
* word timestamps
* scene boundaries
* audio events
* Game Profile
* game description
* genres/tags
* clip duration

The Text LLM identifies:

* hook
* setup
* escalation
* peak
* payoff
* emotional moments
* humor
* surprise
* fear
* player reaction
* context requirements
* story coherence

Structured JSON is required whenever supported.

---

# 13. VISION LLM ANALYSIS

Vision analysis is expensive.

Therefore:

**Do NOT run full Vision analysis across every candidate.**

Only send the strongest candidates.

Vision input:

* native video when supported;
* otherwise intelligently sampled frames.

Every frame must have a timestamp.

The system must support both:

```text
video mode
image/frame mode
```

Adaptive sampling should concentrate around:

* audio peaks
* screams
* sudden events
* scene changes
* suspected reactions
* peak timestamps
* payoff timestamps

Vision should evaluate:

* facial reactions
* fear
* surprise
* laughter
* player reactions
* gameplay events
* visual reveals
* enemies/entities
* camera movement
* chaos
* visual clarity
* visual context
* visual payoff
* boring sections
* confusing sections
* ending quality.

---

# 14. STRUCTURED VIRAL ANALYSIS

Do NOT ask the LLM for:

> “Give this clip a viral score of 87/100.”

Instead, obtain structured observations/component scores.

Minimum components:

* hook_score
* emotional_score
* humor_score
* surprise_score
* fear_score
* reaction_score
* visual_score
* pacing_score
* context_independence_score
* payoff_score
* ending_strength_score
* rewatch_score
* shareability_score
* audio_impact_score
* story_coherence_score

Also return:

* reason
* confidence
* hook_timestamp
* setup_start
* peak_timestamp
* payoff_timestamp
* recommended_start
* recommended_end

Scores should be normalized 0–100.

The LLM provides observations. OpenShorts performs the final mathematical scoring.

---

# 15. DETERMINISTIC VIRAL SCORING

The final viral score is owned by OpenShorts.

It combines:

```text
Universal Viral Weights
+
Game Profile Weights
```

The mathematics must be deterministic.

Changing weights must **not** trigger another LLM call.

Previously calculated component scores must be reused.

The UI must expose the score breakdown.

---

# 16. PAYOFF-AWARE CLIP OPTIMIZATION

A clip is not necessarily best when it starts at the highest score.

The system must understand:

```text
HOOK
→ SETUP
→ ESCALATION
→ PEAK
→ PAYOFF
```

Potentially start earlier to preserve setup.

Avoid:

* excessive dead time;
* cutting mid-word;
* cutting mid-sentence;
* cutting before reaction;
* cutting before payoff;
* ending immediately after peak when the payoff comes later.

Use Whisper word timestamps.

Minimum/maximum clip duration must be configurable.

---

# 17. CLIP QUALITY FILTERING

Candidate quality must be evaluated independently from viral potential.

Penalize/reject:

* long silence
* black frames
* corrupted frames
* meaningless gameplay
* excessive setup
* missing context
* unfinished sentences
* no payoff
* abrupt endings
* duplicate moments
* near-duplicates
* visually confusing clips

Implement temporal and/or semantic deduplication.

---

# 18. SUBTITLE ENHANCEMENT

UI option:

**Enhance subtitles with LLM**

When enabled:

* send transcript + context to Text LLM;
* correct obvious Whisper mistakes;
* preserve meaning;
* never invent dialogue;
* preserve timestamps;
* preserve timing;
* preserve renderer compatibility;
* improve readability;
* optionally add a small number of contextual emojis.

Emoji use must be conservative.

The original requirement explicitly asks for small contextual emoji use rather than spam.

---

# 19. TWITCH VOD METADATA

After analyzing the whole VOD, generate:

* Twitch VOD title
* Twitch VOD description
* short summary
* chapters
* 3–5 alternative titles

Use:

* full transcript
* major events
* selected clips
* Game Profile
* game description
* game genres/tags

Titles must be natural rather than generic AI clickbait.

Chapters must correspond to real timestamps.

---

# 20. CACHING

Caching is a core architectural requirement.

Cache:

* transcript
* scene analysis
* audio events
* Game Profile
* Text LLM analysis
* Vision LLM analysis
* component scores
* generated metadata

Changing only:

* weights;
* clip duration;
* subtitle settings;
* rendering settings

must NOT unnecessarily rerun expensive AI analysis.

Changing Game Profile weights should allow reranking using existing component scores.

---

# 21. REPRODUCIBILITY

For each analysis preserve:

* provider
* model
* prompt version
* analysis version
* Game Profile ID
* Game Profile version
* scoring configuration
* timestamps

Old analyses must remain reproducible.

Do not overwrite historical analysis metadata.

---

# 22. UI

Required sections:

1. AI Providers
2. Game Profiles
3. Clip Generation
4. Analysis Results
5. VOD Metadata

Clip Generation must allow selection/configuration of:

* Game Profile
* Text LLM
* Vision LLM
* multimodal analysis
* subtitle enhancement
* Twitch metadata

Configurable parameters include:

* candidate count
* Vision candidate count
* minimum duration
* maximum duration
* frame sampling density
* scoring configuration
* temperature
* max tokens.

---

# 23. ANALYSIS RESULTS UI

Each candidate should show:

* viral score
* score breakdown
* reason
* hook
* setup
* peak
* payoff
* recommended start/end
* detected events
* Text LLM model
* Vision LLM model

Timeline UI:

```text
HOOK → SETUP → PEAK → PAYOFF
```

Allow:

* preview;
* manual start/end adjustment;
* duration recalculation;

without rerunning AI analysis.

---

# 24. PERFORMANCE

Optimize for the local GPU.

Support where useful:

* GPU acceleration
* asynchronous processing
* batching
* caching
* safe parallel analysis
* adaptive frame sampling

Never run expensive Vision analysis on every candidate.

The intended architecture for a long VOD remains approximately:

```text
3h VOD
 ↓
cheap whole-VOD analysis
 ↓
many candidates
 ↓
Text LLM
 ↓
smaller shortlist
 ↓
Vision LLM
 ↓
payoff optimization
 ↓
top clips
```

This is deliberately designed to remain practical on local hardware.

---

# 25. FAILURE HANDLING

If an AI provider fails:

* retry where appropriate;
* show a clear error;
* preserve completed stages;
* allow fallback;
* allow resume.

If Vision fails:

```text
Vision
↓
Text fallback
```

If Text LLM fails:

```text
Text LLM
↓
deterministic/basic analysis where possible
```

A later-stage failure must never destroy successfully completed previous stages.

---

# 26. TESTING

Required test coverage includes:

* provider abstraction
* OpenAI-compatible provider
* model discovery
* structured JSON
* malformed LLM responses
* provider failures
* GameProfile creation
* Steam metadata storage
* GameProfile versioning
* duplication
* import/export
* scoring
* weight changes
* candidate ranking
* payoff-aware boundaries
* subtitle transformation
* caching
* resumable jobs

A phase is not complete until existing functionality still works.

---

# 27. DOCUMENTATION

README/configuration documentation must eventually cover:

* Gemini
* OpenAI-compatible provider
* LM Studio
* Docker
* host networking
* Text LLM
* Vision LLM
* Game Profiles
* scoring weights
* multimodal analysis
* subtitle enhancement
* Twitch metadata

Include practical setup examples.

---

# 28. EXPLICITLY EXCLUDED FROM VERSION 1

These are **NOT part of the current project scope**:

### Channel Profile system

Do not implement.

### User feedback loop

Do not implement.

### Preference learning

Do not implement.

### Online learning

Do not implement.

### Fine-tuning

Do not implement.

### Adaptive scoring from user ratings

Do not implement.

The system should remain deterministic and configuration-driven.

However, raw component scores and analysis results should be preserved so a future offline learning/ranking system can be built without rewriting the core pipeline.

---

# 29. CURRENT PROJECT STATUS

## ✅ COMPLETED / VERIFIED

### Phase 0

Repository reconnaissance.

### Phase 1

AI provider foundation:

* OpenAI-compatible provider
* LM Studio
* Docker connectivity
* Qwen3-VL
* structured output
* multimodal provider support

### Phase 2 — Complete (976200a → Phase 4 cheap phase)
GameProfile persistence:
* local JSON repository
* UUID profiles
* CRUD backend (422 Content-Type fix, silent 401 boot)
* duplicate / delete / create
* local/cloud separation
* GameProfile UI (Modal xl, GET /{id} populate, single-X fix)
* Steam backend + Steam Lookup UI (Search + App ID, preserve custom_description)
* AI Game Analysis (Gemini + OpenAI heuristic fallback, provider toggle)
* import/export (bulk + single per-card, icons fixed)
* custom_description separated from steam_description (Text column, Your Notes)
* active_weights sliders with brass linear-gradient trail + accent
* OpenAI selector (Settings BYOK/model/Base URL/Refresh, GET /api/openai/models proxy 7 models)
* per-line HH:MM:SS logs + provider-aware logs + cost badge
* Checkpoint 976200a: openai selector implemented, game profile implemented, steam game search implemented, ai weights implemented

### Phase 4 — Cheap whole-VOD event detection (IMPLEMENTED)
VOD → whisper (always) + scene [toggle] + audio events [toggle] + cheap visual/activity [toggle] → MULTIMODAL CANDIDATE → windows+context → TEXT → TOP → VISION → SCORING → PAYOFF → QUALITY/DEDUP → RANKING → CLIP+VOD METADATA → SUBTITLE → RENDER
Critical rule: silence(15s)→jumpscare(17s)→scream(18s)→reaction(20s) must seed candidate via cheap signals.
Implemented: cheap_events.py + main.py ENABLE_* merging + app.py X-Enable-* forwarding + dashboard card (default ON, all beyond Whisper toggleable). Verified synthetic silence 6.2s → window covering 15-20s PASS; docker [cheap] log verified.

### Phase 5 — Text LLM contextual analysis ✓
Deep whole-VOD analysis via Gemini File API 640 fps1 proxy (or 12 frames qwen) with full transcript + game profile, HOOK→PAYOFF, language-aware titles max 100 + hashtags, VOD title. Also scoring/detail path via TEXT→TOP→DETAIL for vision pool.

### Phase 7 — Vision LLM analysis ✓
Vision of top candidates: 6 frames per window (heuristic), adaptive sampling around peaks, visual/humor/surprise/reaction scores, fallback to text analysis if vision fails. Verified with GRAIN ROT 36m Italian VOD.
VOD → whisper (always) + scene [toggle] + audio events [toggle] + cheap visual/activity [toggle] → MULTIMODAL CANDIDATE → windows+context → TEXT → TOP → VISION → SCORING → PAYOFF → QUALITY/DEDUP → RANKING → CLIP+VOD METADATA → SUBTITLE → RENDER
Critical rule: silence(15s)→jumpscare(17s)→scream(18s)→reaction(20s) must seed candidate via cheap signals.
Implemented: cheap_events.py + main.py ENABLE_* merging + app.py X-Enable-* forwarding + dashboard card (default ON, all beyond Whisper toggleable). Verified synthetic silence 6.2s → window covering 15-20s PASS; docker [cheap] log verified.

### Phase 6 — Core

Multimodal infrastructure:

* frame extraction
* 3-frame sampling
* base64 images
* transcript + vision architecture
* Qwen3-VL integration

### Phase 8 — Core

Deterministic semantic scoring infrastructure.

### Git checkpoint

Stable multimodal checkpoint:

```text
82cbe19
feat: add multimodal semantic clip analysis
```

---

# 30. CURRENT BLOCKERS

## Blocker 1 — GameProfile Update → RESOLVED
Browser PUT now sends application/json via apiJson; verified 200. Silent 401 boot probe fixed.

## (old Content-Type issue was:
```text
text/plain;charset=UTF-8
```)

while direct PowerShell `application/json` PUT works with HTTP 200.

Therefore:

* backend is functional;
* local repository is functional;
* browser request path still needs fixing.

This must be resolved before considering Phase 2 complete.

## Blocker 2 — Complete GameProfile feature set

→ RESOLVED at 976200a: Steam UI ✓, AI Analysis ✓, import/export ✓, custom_description ✓, brass trail ✓, single-X ✓, OpenAI selector ✓, per-line logs ✓. Versioning intentionally skipped (lightweight profiles + export backup) per user decision.

---

# 31. NEXT IMPLEMENTATION ORDER

This is now the official order.

```text
CURRENT
│
├── 1. Fix GameProfile Update ✓
│
├── 2. Complete GameProfile CRUD verification ✓
│
├── 3. Complete GameProfile UI ✓
│      ├── Steam lookup UI ✓
│      ├── metadata population ✓
│      ├── AI analysis ✓
│      ├── editable weights ✓ (brass trail)
│      ├── custom description ✓ (separated)
│      ├── custom instructions (deferred — profiles stay simple per user pref)
│      ├── reset (via export backup)
│      ├── import/export ✓ (bulk+single)
│      └── versioning (intentionally skipped → updated_at + export backup)
│
├── 4. Cheap whole-VOD event detection ✓ (cheap_events.py + toggles, verified)
│
├── 4. Cheap whole-VOD event detection
│      ├── transcript signals
│      ├── scene signals
│      ├── audio events
│      └── cheap activity/visual signals
│
├── 5. Text LLM contextual analysis ✓ (deep + scoring via full transcript, HOOK→PAYOFF)
│
├── 6. Expand structured viral component model
│
├── 7. Vision LLM analysis of top candidates ✓ (frame sampling 6/window, heuristic_score, fallback text analysis)
│
├── 8. Deterministic scoring expansion
│
├── 9. Payoff-aware optimization
│
├── 10. Clip quality filtering + deduplication
│
├── 11. Caching
│
├── 12. Reproducibility
│
├── 13. Complete Clip Generator UI
│
├── 14. Analysis Results UI
│
├── 15. Subtitle enhancement
│
├── 16. Twitch VOD metadata
│
├── 17. Performance optimization
│
├── 18. Failure handling / resumability
│
├── 19. Full integration testing
│
└── 20. Documentation / README
```

This reflects the original implementation order while incorporating the later architectural correction that candidate detection must be driven by **multimodal/cheap events**, not transcript-only significance. The original project explicitly called for staged candidate generation before Text and Vision analysis.

---

# 32. GIT CHECKPOINT POLICY — MANDATORY

This project must be developed incrementally.

For **every major phase**:

```text
IMPLEMENT
   ↓
RUN TESTS
   ↓
INSPECT DIFF
   ↓
VERIFY RUNTIME
   ↓
GIT COMMIT
   ↓
NEXT PHASE
```

Never allow a coding agent to modify the entire roadmap in one giant operation.

The original workflow explicitly called for incremental changes and a Git checkpoint after each phase.

## Before every major phase

Run:

```powershell
git status
git log -5 --oneline
```

If working tree is clean, begin the phase.

## After implementation

Run at minimum:

```powershell
git diff --check
git status --short
git diff --stat
```

Then relevant:

* unit tests;
* compilation;
* frontend build;
* Docker runtime tests;
* actual UI/API tests.

## Only after verification

```powershell
git add -A
git commit -m "feat: <phase description>"
```

Then:

```powershell
git status
git log -1 --oneline
```

The working tree should be clean.

---

# 33. AI CODING AGENT RULES

Any future AI modifying the repository must follow these rules:

1. **Inspect current code before editing.**
2. Never assume previous changes still exist.
3. Never trust an earlier “PASS” without runtime evidence.
4. Never rewrite the project from scratch.
5. Never create a parallel architecture unnecessarily.
6. Preserve existing OpenShorts functionality.
7. Preserve Gemini support.
8. Keep provider-specific logic outside the core pipeline.
9. Prefer small coherent changes.
10. Run tests after every major modification.
11. Inspect Git diff before proceeding.
12. Never commit broken or unverified work.
13. Do not modify unrelated functionality.
14. Never invent missing data.
15. Do not hide errors to make tests pass.
16. Do not introduce new features outside this specification.
17. Never declare completion based only on static inspection when runtime verification is available.

These rules match the original project's incremental implementation methodology.

---

# 34. DEFINITION OF DONE

The project is complete only when:

* existing OpenShorts functionality still works;
* Gemini still works;
* local OpenAI-compatible provider works;
* Docker works;
* Game Profiles are reusable;
* Steam lookup works;
* AI game analysis works;
* weights are editable;
* candidate detection is not transcript-only;
* audio events are used;
* Text LLM contextual analysis works;
* Vision LLM analysis works;
* structured component scoring works;
* final scoring is deterministic;
* Game Profile weights affect ranking without another LLM call;
* payoff optimization works;
* clip quality filtering works;
* duplicates are controlled;
* subtitle enhancement works;
* Twitch metadata generation works;
* caching works;
* analyses are reproducible;
* failures can be resumed/fallbacked;
* analysis results UI works;
* manual boundaries can be adjusted without rerunning AI;
* full integration tests pass;
* documentation is complete.

The original specification explicitly defines completion around preserving existing functionality while implementing the complete staged architecture.

---

# 35. FROZEN PROJECT PRINCIPLE

The final system is **not**:

```text
Whisper
↓
find interesting sentence
↓
clip
```

It is:

```text
WHOLE VOD
    ↓
CHEAP MULTIMODAL EVENT DETECTION
    ↓
CANDIDATES
    ↓
TEXT CONTEXT
    +
VISION
    +
AUDIO
    +
GAME PROFILE
    ↓
STRUCTURED OBSERVATIONS
    ↓
DETERMINISTIC SCORING
    ↓
HOOK / SETUP / ESCALATION / PEAK / PAYOFF
    ↓
QUALITY FILTERING
    ↓
RANKING
    ↓
SUBTITLES
    ↓
RENDER
    ↓
TWITCH METADATA
```

And the key rule is:

> **A viral moment must never be lost merely because its transcript is uninteresting.**

That is why the first-stage candidate detector is driven by **multiple cheap signals**, including silence, sudden loudness, audio events, scene changes and visual/activity signals, before expensive LLM analysis. This is consistent with the original architecture and resolves the exact failure case you identified.

---

## FINAL STATE

**This document is now the canonical roadmap.**

Anything we do from here should answer one question first:

> **Which phase/feature of this frozen specification are we implementing or verifying?**

No Channel Profiles.
No feedback loop.
No random extra features.
No giant rewrites.
No skipping Git checkpoints.
No “PASS” without evidence.

And the **immediate task remains the GameProfile Update 422**, because Phase 2 is not yet closed.
---

# 36. POSSIBLE ADDITIONS (NON-BLOCKING — FUTURE ENHANCEMENTS)

These are **not part of the frozen 20-phase spec**. They may be added incrementally without breaking the pipeline, only if user explicitly requests. Each includes technical details for implementation parity with autoshorts.

## 36.1 AI Funny Voiceover Narration (per-clip vision, like autoshorts)

**Goal:** Add optional AI voiceover that narrates the clip context humorously, not just burned subtitles. Same as autoshorts `story_roast` / `funny` caption mode.

**How autoshorts does it (verified in `src/ai_providers.py` + `story_narrator.py` + `shorts.py:2442`):**

```text
CUT clips (15-60s each)  →  for EACH clip .mp4:
  client.files.upload(clip_path, mime_type=video/mp4)  → poll until ACTIVE (File API, not inline Part)
  model = GEMINI_MODEL (or OPENAI_MODEL for OpenAI)
  prompt = _get_caption_prompt(style, max_captions, duration, language)
    style=funny: "Generate humorous, meme-style captions. Examples: 'skill issue tbh', '*chuckles* I'm in danger' — Be self-aware, Gen-Z humor. Keep 1-5 words, ALL CAPS for action."
    style=story_roast: "Sarcastic roasting across clips: 2-3 sentences per caption, playful mockery, comedic timing"
    prompt core: "Watch this gameplay video and generate {max_captions} captions/narrative segments for key moments. STYLE GUIDE + VIDEO DURATION + RULES: Space throughout, 1-3s (or 4-8s story), focus action/close calls/funny. Return JSON {captions:[{start,end,text,style}]}"
  generate_content([video_file, prompt], response_mime_type=application/json, response_schema=CaptionResult)
  → captions/narration_text per clip
  → subtitle_generator.generate_subtitles(clip_path, story_narration=text) → burn ASS
  → tts_generator (ElevenLabs) → voiceover .mp3 → ffmpeg mix with ducked game audio (config: voiceover_volume, game_audio_volume)
```

**Technicalities for OpenShorts2:**

* **Where in pipeline:** After Phase 10 (clip output) + Phase 15 (subtitle enhancement), before rendering final mix. Gate by new toggle `ENABLE_VOICEOVER` + Settings `TTS_PROVIDER` (ElevenLabs key, voice preset map `CAPTION_STYLE_VOICE_MAP`).
* **Provider parity:** Reuse existing `GEMINI_MODEL` selector (Settings → Gemini model) + `File API` path already built for deep (`scale=640:-2,fps=1` proxy). For per-clip voiceover use **original clip** (no proxy needed, 15-60s file ~5-15MB) or same proxy if <3min VOD.
* **Models:** `gemini-2.0-flash` / `gemini-3.7-flash` for caption, `elevenlabs` for TTS (or `openai-compatible TTS` if LM Studio). Keep `AI_PROVIDER` abstraction.
* **Prompt reuse:** Port `style_guides` dict verbatim from autoshorts: gaming/dramatic/funny/minimal/genz/story_news/story_roast/story_creepypasta/story_dramatic. Language map `it`→`Italian` already used in scoring.
* **Costs/latency:** +1 Gemini File API call per final clip (5 clips → 5 uploads, ~2s each). Gemini timeout 90s already set for deep, reuse. Poll `ACTIVE` up to 60s like deep.
* **Storage:** Voiceover `wav/mp3` in `output/<job>/voiceover/` not overwriting `subtitled_*.mp4`; final mix `voiceover_mix_*.mp4` optional.
* **UI:** Settings card “AI Voiceover” (toggle, style dropdown `funny|gaming|genz|story_roast`, TTS key, volume sliders). Generation panel toggle “Add AI narration” (like autoshorts `1_Generate.py:224`).
* **Not enabled by default:** Keep cheap/deep/VISION toggles independent. Voiceover is extra post-process, not candidate selection.

## 36.2 Other deferred candidates (do not implement without explicit ask)

* **Cross-clip unified story arc** (autoshorts `story_narrator.generate_unified_story` across 5 clips)
* **HUD OCR payoff detection** (transient damage numbers via 640-fps1 proxy already in deep prompt; full OCR layer deferred)
* **Auto-chapters for long VODs** (>2h → segmented proxy generation)

## 36.3 Revise deep for OpenAI/qwen to handle video file like Gemini (deferred)

**Goal:** Parity between `qwen3-vl-8b-instruct` via LM Studio and Gemini for `ENABLE_DEEP_ANALYSIS` — currently Gemini uses native video via `File API` with `640:-2 fps=1` proxy (`scale=640:-2,fps=1, ultrafast crf30, 32k aac`), while qwen uses `12` base64 frames (`extract_frames_from_window` spread `0-{dur}`) because LM Studio `90k` context overflows at `24` frames + `8513` char transcript + game profile JSON.

**Why qwen frames today:** `24` images (~800KB base64 each) + full VOD transcript caused LM Studio `raw {}` / no `moments` (schema `DeepResponse` fails). Fix `030427d` cut to `12` frames (fits `90k`), plus `6d6359d` moved `if not _native_success:` from `32` *inside* `if _used_native_video:` at `28` to `28` sibling so qwen outside gemini block hits `LM Studio` (`Deep sending 13 parts`). `296ad9a` fixed `active_weights` undefined → `_wc_active_weights` so `gp` no longer `0` when `GRAIN ROT` selected.

**Deferred revision:** LM Studio `qwen3-vl` supports `video` input via `OpenAICompatibleProvider` `messages` with `type: video_url` or base64 `video/mp4` (check `ai_provider.py` `OpenAICompatibleProvider` — currently only `text` + `image_url`). Investigate:
* `client.chat.completions.create(model, messages=[{role:user, content:[{type:text, text:prompt},{type:video_url, video_url:{url: data:video/mp4;base64,...}}]}])` with `90k`+ context + `fps1` proxy same as Gemini (tiny `640` proxy `~3-8MB` base64 → `~4-11M` chars → may exceed context anyway).
* Alternative: keep `12` frames but increase to `16` like Gemini native limit, or chunk video into `fps1` images via `video_url` vs `image_url` — compare `LM Studio` `vision` handling of `video` vs `12` images for HUD OCR and motion.
* Verify `LM Studio` `qwen3-vl-8b` video support on `host.docker.internal:1234` — test with small `640` proxy before widening `_deep_nframes` back to `16/24`.

**When to do:** Only after confirming `qwen` video path returns `5` moments with `game_profile` + full transcript like current `12` frames does, without `raw {}` and without doubling `prompt_len`. Keep toggle `ENABLE_DEEP_ANALYSIS` default `OFF`, respect `scene/audio/visual` toggles already fixed (`deep` outside `cheap_enabled`).


