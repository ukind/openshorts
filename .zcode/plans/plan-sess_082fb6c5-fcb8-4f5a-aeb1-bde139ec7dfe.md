# Plan: Voice tags = style tags + matching reorder + generic rename + Gemini model fix

## 1. Gemini model fix (unchanged)
- `App.jsx`: pass `geminiModel` to `VoiceOverPage`; `VoiceOverPage.byokHeaders()` adds `X-Gemini-Model`. VoiceOver then uses the exact Settings model.

## 2. Voice presets: tags come from style presets
- **Data model**: voice presets get a `tags` array; every tag must be the id of an existing style preset (gaming, dramatic, funny, minimal, genz, story_news, story_roast, story_creepypasta, story_dramatic, auto). The old voice `kind` grouping (classic/genz/story/custom) is dropped — all voices get `kind: "generic"`.
- **Seed voices tagged** (backend seeds updated): action→[gaming, auto], gaming→[gaming], dramatic→[dramatic], funny→[funny], minimal→[minimal], genz→[genz], story_news→[story_news], story_roast→[story_roast], story_creepypasta→[story_creepypasta], story_dramatic→[story_dramatic].
- **Rename for styles too**: style preset kinds `classic` → `generic`, `custom` → `generic` (read-time normalization in `PresetRepository.list_presets` so existing saved files migrate automatically; seed data updated).
- **Backend (`voice_presets.py`)**: `create_preset`/`update_preset` accept `tags`; voice repo validates each tag against current style preset ids and strips unknown ones; `kind` normalization + slugify as planned.

## 3. Presets page (`VoiceStylePresetsPage.jsx`)
- Voice edit/create modal: **tag selector** — checkbox chips built from the current style presets (requires fetching style ids there); at least one tag required to save a voice preset (per your rule). No free-text group for voices anymore.
- Style edit/create modal: Group input as planned, default `generic` (no more `custom`).
- Voice list: cards show tag badges; filter chips are the distinct tags + `Generic` (untagged) + counts. Style list: filter chips by kind (All / generic / genz / story / auto / user groups).

## 4. VoiceOver window (`VoiceOverPage.jsx`)
- When a **style preset** is selected, the Voice Preset dropdown reorders: voices whose tags include that style id appear first under an optgroup like "★ Matches {Style title}" (highlighted), everything else under "All voices". Switching style preset re-sorts instantly.
- Grouped `<optgroup>` display stays for style presets dropdown (by kind).

## 5. Verification
- Backend: syntax + repo test (tag validation strips unknown, kind migration classic/custom→generic, seeds tagged).
- `npx vite build`; restart backend container and curl preset endpoints if Docker is up.

Files: `voice_presets.py`, `dashboard/src/components/VoiceStylePresetsPage.jsx`, `dashboard/src/components/VoiceOverPage.jsx`, `dashboard/src/App.jsx` (one line).