# OpenShorts – TTS VoiceOver, Caption Styles & Voice/Style Presets

You are working on the **OpenShorts project**.

## CRITICAL PROJECT SCOPE

**OpenShorts is the project that must be modified.**

The current working directory/repository you are operating in is **OpenShorts**. All implementation work, code changes, UI changes, new files, refactoring, testing, and integration must be performed in the **current OpenShorts project**.

There is a separate project located at:

`C:/AI_Studio/autoshorts`

**AutoShorts is NOT the project to modify.**

AutoShorts is provided **only as a reference implementation** from which you should study and adapt the existing TTS/voiceover functionality.

### The relationship between the two projects is:

```text
CURRENT WORKING DIRECTORY
└── OpenShorts
    └── THIS IS THE PROJECT TO MODIFY

REFERENCE ONLY
└── C:/AI_Studio/autoshorts
    └── DO NOT MODIFY
    └── Study and reuse/adapt its TTS implementation
```

You must **not make any changes to `C:/AI_Studio/autoshorts`**.

Do not treat AutoShorts as the main application.

Do not copy the entire AutoShorts architecture into OpenShorts.

Instead:

1. Understand the existing architecture and workflow of OpenShorts.
2. Inspect AutoShorts to identify the existing TTS/voiceover implementation and related functionality.
3. Port/adapt only the relevant functionality from AutoShorts into OpenShorts.
4. Integrate that functionality using OpenShorts' existing architecture, components, services, settings, media pipeline, UI conventions, and abstractions.
5. Preserve all existing OpenShorts functionality.

The goal is:

> **Add AutoShorts-like TTS VoiceOver functionality to OpenShorts, not turn OpenShorts into AutoShorts.**

---

# 1. First: Analyze Both Projects, But Modify Only OpenShorts

Before modifying any code, thoroughly inspect the current OpenShorts project.

Understand at minimum:

* current application architecture
* frontend/backend structure
* routing/navigation
* sidebar
* Main workflow
* clip generation
* Game Profiles
* caption generation
* subtitle generation
* subtitle editor
* Remotion rendering
* FFmpeg/media processing
* existing ElevenLabs integration
* Settings
* API key management
* file/storage handling
* existing provider abstractions

Then inspect:

`C:/AI_Studio/autoshorts`

specifically to understand and extract/adapt:

* TTS implementation
* Qwen/local TTS
* ElevenLabs TTS behavior
* voice configuration
* voice presets
* style prompts
* caption generation
* caption timing
* voiceover generation
* audio ducking
* audio mixing
* subtitle/caption integration
* relevant utility functions

### Important

When studying AutoShorts, distinguish between:

**Functionality that should be ported:**

* TTS logic
* Qwen voice generation
* voice configuration
* relevant prompt/style definitions
* caption generation behavior
* caption timing
* ducking/mixing behavior
* other directly relevant voiceover functionality

and:

**Architecture/UI/code that should NOT be blindly copied:**

* unrelated application structure
* unrelated routes
* unrelated components
* duplicate settings systems
* duplicate project/profile systems
* duplicate media infrastructure
* unrelated business logic

Adapt the useful functionality to OpenShorts instead.

---

# 2. Absolute Rule: Never Modify AutoShorts

Do not:

* edit files in `C:/AI_Studio/autoshorts`
* rename files in AutoShorts
* delete files in AutoShorts
* change AutoShorts configuration
* use AutoShorts as the runtime target
* make OpenShorts depend on arbitrary local AutoShorts source files at runtime

AutoShorts should be treated as a **reference/source of implementation ideas and functionality**.

The final OpenShorts implementation must be self-contained within OpenShorts.

If code is ported from AutoShorts, adapt/rewrite it as necessary so OpenShorts does not require the AutoShorts repository to run.

---

# 3. Before Coding: Produce an Integration Plan

Before changing files in OpenShorts:

1. Analyze OpenShorts.
2. Analyze the relevant AutoShorts implementation.
3. Compare the architectures.
4. Identify the exact AutoShorts functionality that needs to be ported.
5. Explain how that functionality will fit into OpenShorts.

Provide a concise plan containing:

* OpenShorts files/components to modify
* new OpenShorts files/components
* AutoShorts functionality being ported
* how Qwen TTS will integrate
* how ElevenLabs will integrate
* how caption generation will integrate
* how audio ducking will integrate
* how Remotion will integrate
* data models
* UI changes
* provider abstractions
* testing strategy

Only after this analysis should implementation begin.

---

# 4. Main Feature

The feature to implement is:

**AutoShorts-style TTS VoiceOver + caption workflow inside OpenShorts.**

The intended final flow is:

```text
OpenShorts MP4
    ↓
Caption generation
(OpenAI / Gemini)
    +
Style Preset
    +
Game Profile context
    ↓
Generate 4 caption suggestions
    ↓
User selects and edits one
    ↓
Voice generation
(Qwen Local TTS / ElevenLabs)
    +
Voice Preset
    ↓
Generate voiceover
    ↓
Duck original audio
    +
Mix voiceover
    ↓
<filename>_voiceover.mp4
    ↓
Generate timed captions
    ↓
Render with OpenShorts Remotion pipeline
    ↓
<filename>_voiceover_captioned.mp4
```

The remainder of the implementation requirements are as follows.

---

# 5. New Sidebar Menu: "Voice/Style Presets"

Add a new OpenShorts sidebar item:

**Voice/Style Presets**

The concept should be similar to the existing **Game Profiles** system.

The user must be able to:

* add
* edit
* delete
* import
* export

voice presets and style presets.

Reuse OpenShorts' existing persistence and UI patterns where possible.

---

# 6. Voice Presets

Voice presets are used for voice generation.

They must contain at minimum:

* `title`
* `prompt`

They should also contain the actual voice configuration required by the selected provider.

For ElevenLabs, reuse the existing OpenShorts ElevenLabs integration and its existing API key stored in Settings.

Do not create a second ElevenLabs API key system.

For the initial presets, inspect AutoShorts and recreate its existing voice presets in OpenShorts.

Do not invent replacement voice configurations if the actual AutoShorts definitions are available.

---

# 7. Style Presets

Style presets control caption generation.

They must work with:

* OpenAI
* Gemini

Each style preset must contain at minimum:

* `title`
* `prompt`

Populate the initial OpenShorts styles from the corresponding AutoShorts prompts:

### Classic

* `gaming`
* `dramatic`
* `funny`
* `minimal`

### GenZ Mode

* `genz`

### Story Modes

* `story_news`
* `story_roast`
* `story_creepypasta`
* `story_dramatic`

### Auto

* `auto`

Use the **actual AutoShorts prompts** rather than inventing new prompts.

Keep stable internal IDs while allowing human-readable display names.

---

# 8. New Sidebar Menu: "VoiceOver"

Add a new OpenShorts sidebar item:

**VoiceOver**

This is a dedicated workflow for creating TTS voiceover and captioned output from an existing MP4.

---

# 9. VoiceOver Source

Support two sources.

## Manual upload

Allow the user to upload an MP4 manually.

## Existing generated clip

Every generated OpenShorts clip must have a new:

**VoiceOver**

button.

When clicked:

1. Navigate to the VoiceOver screen.
2. Automatically select the corresponding clip.
3. Use the clip's **uncaptioned/raw version** as the source.
4. Do not use an already burned/captioned clip as the TTS source.

---

# 10. VoiceOver UI

The VoiceOver page should contain:

### Caption provider

* OpenAI
* Gemini

Reuse the same provider/settings mechanism used by OpenShorts Main.

### Voice provider

* Local TTS
* ElevenLabs

### Local TTS

Port/adapt the **Qwen-based TTS implementation from AutoShorts** into OpenShorts.

Do not make OpenShorts depend on the AutoShorts repository at runtime.

### ElevenLabs

Reuse the existing OpenShorts ElevenLabs API key/configuration.

---

# 11. Voice Preset Selector

Provide a Voice Preset dropdown.

After selection:

* show preset content/prompt
* show an Edit button
* allow temporary modification
* provide a clear way to save changes back to the preset if desired

The interaction should resemble Game Profile editing.

---

# 12. Style Preset Selector

Provide a Style Preset dropdown.

After selection:

* show the prompt
* show an Edit button
* allow temporary modification
* optionally save changes to the stored preset

---

# 13. Game Profile Selector

Provide a Game Profile dropdown using the existing OpenShorts Game Profiles.

When generating captions, send only:

* game title
* game description

as Game Profile context unless existing OpenShorts behavior requires otherwise.

---

# 14. Caption Generation

Send the selected MP4 to:

* OpenAI, or
* Gemini

using:

* the selected Style Preset prompt
* game title
* game description
* the video itself

Ask the model to generate:

**4 distinct caption options.**

The user must be able to:

* inspect all 4
* select one
* edit its text

---

# 15. Continue to TTS

After selecting/editing a caption, the user clicks:

**Continue**

Then generate the voiceover using:

* Qwen Local TTS, or
* ElevenLabs

using the selected Voice Preset.

The TTS implementation should reproduce AutoShorts behavior as closely as possible while conforming to OpenShorts architecture.

---

# 16. Audio Ducking

After generating TTS:

1. Take the source video's original audio.
2. Duck the original audio while the voiceover is speaking.
3. Overlay the generated voiceover.
4. Preserve the original audio appropriately outside the speech intervals.
5. Generate:

`<filename>_voiceover.mp4`

Use OpenShorts' existing FFmpeg/media pipeline where appropriate.

---

# 17. Caption Burn

After producing the voiceover video:

1. Generate caption timing.
2. Use OpenShorts' existing Remotion subtitle/caption rendering.
3. Burn captions onto:

`<filename>_voiceover.mp4`

Result:

`<filename>_voiceover_captioned.mp4`

---

# 18. Edit Subtitles

Create a result card containing:

**Edit Subtitles**

Clicking it opens an editor similar to OpenShorts' existing Subtitle window.

Allow the user to modify:

* caption text
* font
* size
* color
* position
* animation
* timing
* other existing caption styling options

The editor must load the generated VoiceOver captions.

When the user saves/re-renders:

**Always render from:**

`<filename>_voiceover.mp4`

Never render from:

`<filename>_voiceover_captioned.mp4`

This prevents caption layers from stacking.

---

# 19. Provider Abstractions

Where appropriate, organize the integration as:

```text
CaptionProvider
├── OpenAI
└── Gemini

VoiceProvider
├── QwenLocal
└── ElevenLabs
```

Reuse OpenShorts' existing abstractions wherever possible.

Do not create duplicate implementations if suitable provider/service code already exists.

---

# 20. Important Architecture Principle

The final implementation should conceptually be:

```text
OpenShorts Architecture
        +
AutoShorts TTS Functionality
        ↓
New OpenShorts VoiceOver Feature
```

NOT:

```text
AutoShorts Architecture
        ↓
Copied into OpenShorts
```

OpenShorts remains the source of truth for:

* application architecture
* navigation
* UI
* settings
* profiles
* media handling
* storage
* Remotion
* existing provider integrations

AutoShorts is the reference for:

* Qwen TTS
* voiceover behavior
* voice presets
* style prompts
* caption-generation behavior
* timing
* ducking/mixing
* other relevant TTS-specific implementation details.

---

# 21. Testing

Test the feature inside OpenShorts, including:

* manual MP4 upload
* generated clip → VoiceOver
* OpenAI caption generation
* Gemini caption generation
* four caption suggestions
* caption editing
* Qwen TTS
* ElevenLabs
* voice presets
* style presets
* Game Profiles
* audio ducking
* `<filename>_voiceover.mp4`
* Remotion rendering
* `<filename>_voiceover_captioned.mp4`
* Edit Subtitles
* repeated subtitle re-rendering
* preset import/export
* preset CRUD

Also verify that the existing OpenShorts workflows still work.

---

# 22. Final Requirement

At the end, provide a concise summary of:

* OpenShorts files modified
* OpenShorts files added
* AutoShorts functionality ported/adapted
* TTS provider implementation
* caption-generation implementation
* audio ducking implementation
* output file pipeline
* subtitle editor integration
* tests performed
* remaining limitations/TODOs

**Remember: the current OpenShorts repository is the only project being modified. `C:/AI_Studio/autoshorts` is a read-only reference implementation and must not be changed.**
