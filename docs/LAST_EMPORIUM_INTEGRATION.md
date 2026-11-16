# Connecting The Last Emporium (Godot 4) to Hermes Engine V3

**Status:** Proposed game integration on top of the existing V3 API; NOT yet integrated into The Last Emporium's codebase. Godot adapter included for a first local proof-of-voice; actual Mac/Godot validation remains to be done. Read [the endpoint contract](API.md) first.

## Intended bargaining-to-speech flow

1. **Game AI / bargaining logic** chooses the NPC reply text, an expression/mood (neutral, pleased, suspicious, furious, etc.), and the known game NPC ID.
2. **Voice registry owned by the game** maps that NPC ID to Hermes engine, engine-specific voice ID, stable acting prompt and reaction archetype. **Do not send an unrecognized `npc_id` JSON field to Hermes**; V3 rejects extra fields. Keep game ID client-side.
3. **Speech queue (one at a time)** issues `POST /v1/audio/speech` with `input`/`engine`/`voice`/`archetype`/`mood`/`instruct` plus tuned options. Show the written dialogue immediately; never wait for TTS to display it.
4. Godot receives a WAV, creates an `AudioStreamWAV`, and calls `AudioStreamPlayer.play()` or a positional player if the scene needs it. Drive expression changes from game state; sync mouth animation to playback state.
5. Cache repeated greeting/bargain/accept/reject lines and pre-generated nonverbal reactions; do not make routine NPC barks wait for live synthesis if avoidable.
6. When the stable WAV path works, optionally add incremental `POST /stream` PCM playback via `HTTPClient` and `AudioStreamGenerator` for lower *perceived* latency.

## Voice registry is required

V3 Studio's 30 example profiles do **not** auto-register the seven in-game customers. Map the Last Emporium character identifiers explicitly. Example template (not an approved casting choice):

~~~json
{
  "example_shopkeeper_id": {
    "engine": "qwen",
    "voice": "Vivian",
    "archetype": "female_warm",
    "instruct": "Warm but firm shop merchant",
    "speed": 1.0,
    "pitch_shift": 0.0
  }
}
~~~

For Chatterbox, set `voice` to a *local recording filename stem* present in the `/health` inventory. For Kokoro, use the engine's built-in ID such as `af_heart`. A voice ID from one engine is **not** valid for another.

Mood and emotion are best driven by the actual bargaining state: suspicious offer → `suspicious`, insulting lowball → `furious`, accepted fair deal → `happy`, very favorable deal → `excited`. This is a recommendation for gameplay wiring, not a claim that Kokoro can naturally act every emotion.

## Minimal Godot integration

Add a node to the scene:

~~~text
HermesTTS (Node, script: res://HermesTTS.gd)
├── HTTPRequest
└── VoicePlayer (AudioStreamPlayer)
~~~

Copy [HermesTTS.gd](../examples/godot/HermesTTS.gd) and attach it. Its `speak_line(npc_id, text, voice_id, mood, instruction, engine, archetype)` method sends one full-WAV request; `speech_started` / `speech_finished` / `speech_failed` signals are available for scene orchestration. It parses the server's PCM16 WAV into an `AudioStreamWAV` without relying on `load_from_buffer` so it can work across Godot 4.x versions. Test in your particular Godot build.

Example call from the counter scene:

~~~gdscript
var accepted: bool = $HermesTTS.speak_line(
    "example_shopkeeper_id",
    "Five gold? I cannot sell it for that price.",
    "Vivian",
    "suspicious",
    "A skeptical merchant who speaks firmly",
    "qwen",
    "female_warm"
)
if not accepted:
    # Speech slot already occupied; dialogue text should still appear.
    pass
~~~

This adapter is for a **single audio player** and one outstanding HTTP request. It is a first-pass connector, not a full playback scheduler, dialogue turn arbiter, or lip-sync system.

## Queue and model residency — important for the M2 8GB Mac

- Start one Hermes server process. When a bargaining scene begins, keep Qwen warm if it is our chosen main engine; no per-customer engine swapping.
- Do not send speech for two NPCs simultaneously; V3 returns HTTP 409 when busy. Queue their dialogue or prefer cached clips.
- Use short sentences and avoid unnecessary postprocessing for low TTFA. `speed=1.0` and `pitch_shift=0` are ideal performance-baseline controls.
- `/health.busy` is a hint, not a reservation. Even after a healthy poll, the game must handle 409.
- Repeated fixed nonverbal reactions from `/reaction/{archetype}/{tag}` can be cached at game load; 404 means pack not installed.
- Prefer stable single-engine casting over dynamic engine fallback to avoid minutes of user frustration from memory/model swaps.
- Stop/skip playback without assuming it interrupts active MLX inference instantly.
- If TTS fails, gameplay must continue with text/dialogue and a neutral expression. Never block a transaction on speech service availability.

## Sound and performance acceptance

Before merging an integration PR into The Last Emporium, manually verify:

- Godot Mac editor can reach `/health`; inspect server IP, firewall, port 8000.
- A complete WAV generates, decodes and plays with correct pitch, duration and no click/glitch.
- A missing clone returns a visible validation error, not a generic replacement voice.
- 409 and server-offline states don't freeze the bargaining loop.
- TTS starts only for current, accepted dialogue turns; outdated AI messages are not voiced after player moves on.
- NPC mapping and emotion presets match the game character sheet; compare against Studio auditions.
- Memory pressure stays reasonable with the game and Hermes sharing the same Mac; measure actual Qwen warm TTFA, full WAV latency, and model residency.
- For future PCM streaming, verify the first audible sample, buffering/underruns, stop/skip semantics and response-body framing on desktop and web builds separately.

## Open design decisions for the game team

- Which of the seven game customer IDs and expression states map to which Hermes voice IDs?
- Does the production game run native Mac Godot, web export, or both? Browser `http`/`https` mixed-content rules and cross-origin security can affect LAN TTS.
- Is Qwen the default gameplay voice for all NPCs, or do we intentionally dedicate Chatterbox to characters with approved, available clones?
- Should server-side job queue, cancellation, cache and expression/phoneme timeline support be part of a future V3.1 game API?

These are not required to run the sample; they are deliberate integration gates before production.
