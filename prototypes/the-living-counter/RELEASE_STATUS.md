# The Living Counter — Prototype 0.1 handoff

Standalone Godot 4.4+ 2.5D fantasy haggling prototype; owner is shopkeeper. Designed to integrate with Hermes V3 on localhost port 8000, with a separate FastAPI game director at 127.0.0.1:8010.

**Current artifact:** Complete runnable-source ZIP delivered in ChatGPT conversation on November 17, 2026. The feature branch is reserved for its source synchronization via the ZIP's `publish_to_hermes.sh`; do not mistake this handoff record for the full committed game.

**Implemented in ZIP:** 10 NPCs and 10 items; authoritative buy/sell rules, hidden reservation thresholds, RNG offers, trust/patience/relationships, gold and inventory persistence; optional hosted AI flavor, offline fallback; typewriter dialogue, expressive procedural sprites, Godot low-level PCM streaming from Hermes, push-to-talk WAV capture, transcription route and barge/reaction system.

**Verified:** 18 automated backend/API tests pass, plus actual HTTP roundtrip for health, encounter, freeform dialogue and accepted transaction. Godot runtime scene parse/render, real microphone, hosted language model, actual Hermes audio playback and Mac M2 8GB TTFA are not verified in authoring environment.

**Release gate:** Import and run in Godot 4.4+ on owner Mac; confirm end-to-end AI conversations, proper TTS playback and barge, mic permissions, voice mappings, memory/swap. Preserve original Hermes V3 one-resident-model architecture. Do not merge prototype into The Last Emporium; standalone first.

**Branch dependency:** This branch is stacked on `feat/tts-studio-v3` draft PR #1. Do not delete the upstream branch until no dependent branches/PRs remain.

See the ZIP's `README.md`, `HANDOFF.md`, `docs/API.md`, and `start_mac.sh` for full setup and source. Once local source is synchronized under `prototypes/the-living-counter/`, keep that folder's HANDOFF and tests as the authoritative release record.
