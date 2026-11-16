# Hermes Engine V3 — HTTP API contract

**Status:** V3 release candidate on `feat/tts-studio-v3` (draft PR #1); Mac MLX runtime acceptance pending. This document describes the actual `game_tts_server.py` on that branch. The `main` branch may still contain an older server. Last verified against the V3 code on 2026-11-16.

**Audience:** The Last Emporium (Godot 4.x) gameplay integration and other trusted, local-game clients.

## Base URL and discovery

- Default LAN server: `http://10.0.0.129:8000`. On the same Mac, prefer `http://127.0.0.1:8000`. IP is configurable; it is not service discovery.
- Interactive FastAPI reference: `GET /docs`; alternative `GET /redoc`; schema `GET /openapi.json`.
- HTTP JSON request bodies use `Content-Type: application/json`; success responses may be binary.
- This is a **trusted-LAN only** development service: no authentication, permissive CORS, and `0.0.0.0:8000` binding. Do not port-forward or expose it to the public internet.
- Launch with **one Python process / one Uvicorn worker**. The server keeps one MLX model resident and rejects concurrent synthesis.

## Endpoints

| Method | Path | Response | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | JSON | Server state, model IDs, installed clone stems, reaction packs |
| POST | `/v1/audio/speech` | `audio/wav` | Generate a complete WAV before responding — easiest Godot integration |
| POST | `/stream` | `audio/pcm` | Stream raw, headerless PCM16 little-endian, mono. **Not** a WAV or MP3 |
| GET | `/reaction/{archetype}/{tag}` | `audio/wav` | Pre-rendered reaction pack, if file exists |
| GET | `/runs/{run_id}` | JSON | In-memory generation telemetry; status, timing, packets/chunks |
| GET | `/docs` | HTML | Swagger interactive reference |
| GET | `/openapi.json` | JSON | OpenAPI schema |

### Shared POST request schema

Pydantic rejects unknown fields (HTTP 422). Engine-specific unsupported controls may be **accepted but ignored**; inspect `/runs/{run_id}.ignored_controls`.

| JSON field | Type / default | Constraints or semantics |
| --- | --- | --- |
| `input` | string, required | Nonblank spoken text (after tag stripping), 1–10,000 characters |
| `engine` | `"qwen"` | One of `qwen`, `chatterbox`, `kokoro` |
| `voice` | `"Vivian"` | Engine-specific speaker ID; **not** a cross-engine universal ID |
| `archetype` | `"female_warm"` | Reaction archetype metadata; letters/digits/`_`/`-` only |
| `mood` | `"neutral"` | String; Chatterbox maps recognized moods to an inline reaction tag; Kokoro adjusts punctuation |
| `instruct` | `""` | Qwen native acting text; Chatterbox only keyword/tag mapping, Kokoro does not use semantic instruct |
| `speed` | `1.0` | Range 0.6–1.6. Native Kokoro speed; post-process for Qwen/Chatterbox |
| `pitch` OR `pitch_shift` | `0.0` | Range -4 to +4 semitones, processing cost may affect TTFA |
| `temperature` | `0.45` | 0.2–1.1; used by Qwen/Chatterbox, ignored by Kokoro |
| `repetition_penalty` | `1.3` | 1.0–1.8; used by Qwen/Chatterbox, ignored by Kokoro |
| `streaming_interval` | `0.5` | 0.1–5; only native Qwen streaming if model supports it |
| `min_chunk_chars` OR `stream_chunk_min` | `85` | 10–1000; sentence-grouping target for non-native `/stream`, **not** a packet size |
| `model_id` | null | Optional `owner/checkpoint`; changing it reloads model and risks compatibility/latency |
| `force_reload` | false | Explicit eviction/reload; do not use during live dialogue |

Send either alias spelling, not both. The default server-owned model IDs are:

- `qwen`: `mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit`
- `chatterbox`: `mlx-community/chatterbox-turbo-4bit`
- `kokoro`: `mlx-community/Kokoro-82M-bf16`

**Voice IDs:** Qwen uses its installed named speakers (for example `Vivian`). Chatterbox uses the filename stem of a local reference recording in `~/Projects/npc-tts-studio/voices/`, e.g. `f09_tessa_blacksmith` for `f09_tessa_blacksmith.wav`. A missing clone fails 422; use `"default"` only if intentionally requesting an unconditioned Chatterbox voice. Kokoro uses IDs such as `af_heart`. The game must map each NPC to an actual voice per engine; `tests/voice_manifest.json` records the Studio's 30 sample profiles, not automatic game-character mapping.

### Complete speech (recommended first)

`POST /v1/audio/speech` returns an ordinary WAV only **after** the generation completes, not progressive audio.

~~~bash
curl -fsS http://10.0.0.129:8000/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
    "engine": "qwen",
    "voice": "Vivian",
    "input": "You want that sword for three coins? That barely covers the steel.",
    "mood": "suspicious",
    "instruct": "A skeptical merchant, measured and slightly offended",
    "speed": 1.0,
    "pitch_shift": 0,
    "temperature": 0.45,
    "repetition_penalty": 1.3
  }' --output npc_line.wav
~~~

Expected on success: HTTP 200, `Content-Type: audio/wav`, a real playable WAV byte buffer. On Godot 4.4+ use `AudioStreamWAV.load_from_buffer(body)`; for broader Godot 4.x compatibility see `examples/godot/HermesTTS.gd`.

### Progressive PCM streaming (advanced)

`POST /stream` accepts the *same JSON body* and emits **signed little-endian 16-bit mono PCM** with no WAV headers. The output sample rate comes from `X-Sample-Rate` (the model reports its rate; default fallback is 24000 Hz). Audio begins as packets are synthesized; TCP/HTTP chunk boundaries may differ from synthesis chunk boundaries.

~~~bash
curl --no-buffer -sS http://10.0.0.129:8000/stream \
  -H 'Content-Type: application/json' \
  -d '{"engine":"qwen","voice":"Vivian","input":"Welcome back, traveler!"}' \
  --output voice.pcm
~~~

**Do not** load the raw PCM bytes with `AudioStreamWAV.load_from_buffer` or a browser HTML `<audio>` tag. A Godot streaming client needs `HTTPClient`'s incremental body handling and an `AudioStreamGenerator` / `AudioStreamGeneratorPlayback` buffer, with sample conversion `int16 / 32768.0`. `HTTPRequest.request_completed` provides the whole body and does not deliver useful incremental playback. Handle even-byte boundaries, buffering/underruns and cancellation in the client. A server-disconnect during streaming may leave a partial audio clip.

### Cached nonverbal reactions

`GET /reaction/{archetype}/{tag}` returns a pre-rendered WAV if present, otherwise HTTP 404. Valid tags: `laugh`, `chuckle`, `sigh`, `gasp`, `groan`. Supported archetypes depend on generated files under `~/Projects/npc-tts-studio/npc_reactions/`; inspect `GET /health` for the actual inventory. Reactions are independent from the speaking endpoint and can be preloaded by the game.

### Health response

~~~bash
curl -s http://10.0.0.129:8000/health
~~~

Actual JSON fields: `status` (normally `ok`), `api_version` (`3.0`), `active_engine`, `active_model_id`, `native_stream`, `model_caps`, `models`, `busy`, `chatterbox_voices` (present file stems), and `reactions` (available archetype → tags). Startup warms Qwen. `busy` is informational; synthesis itself enforces nonconcurrency.

### Headers and run telemetry

Successful speech and stream responses include:

- `X-Run-Id`: identifier for `GET /runs/{run_id}`.
- `X-Active-Engine`, `X-Model-Id`, `X-Sample-Rate`.
- `X-Model-State`: `warm`, `cold`, `swap`, or `forced-reload`.
- `X-Load-Seconds`: model acquisition/reload duration.
- `X-Reaction-Archetype`, `X-Reaction-Tag`, `X-Resolved-Instruct`.

`GET /runs/{run_id}` returns one bounded in-memory run object with fields such as `status` (`running`/`complete`/`error`/`cancelled`), `engine`, `model_id`, `model_state`, `load_s`, `sample_rate`, `native_stream`, `ttfa_s`, `total_s`, `audio_duration_s`, `effective_text`, `resolved_instruct`, `reaction_tag`, `request`, `ignored_controls`, `chunk_count`, and `chunks`. Each chunk records `packet`, logical text chunk `chunk` / `of`, `elapsed_s`, `chunk_elapsed_s` and `samples`.

Limit: **300 most recent runs, memory-only**. No stable history across restart; do not store sensitive dialogue without review. Timing is inference/stream-server timing, not player-heard latency. `ttfa_s` excludes separately measured model load; use client stopwatch for perceived latency. Reported output audio duration is `samples / sample_rate`. Conventional RTF is `total_s / audio_duration_s`.

### Error and concurrency behavior

| Code | When | Game behavior |
| --- | --- | --- |
| 200 | Normal binary audio or JSON response | Play or parse; don't JSON-decode WAV/PCM |
| 404 | Reaction/run does not exist | Fallback or skip reaction; don't block bargaining |
| 409 | TTS engine already synthesizing | Queue locally or retry **after** active generation; don't fire several simultaneous requests |
| 422 | Invalid field, missing voice clone, unknown default Qwen voice, empty speech, invalid model ID | Correct mapping or request; don't retry unchanged |
| 5xx / disconnect | Inference or transport failure | Show dialogue text immediately, use fallback/cached clip, log diagnostic |

Only one active inference/model resides in 8GB unified RAM. Engine swaps are potentially expensive; **pin one engine for gameplay sessions and keep it warm**. Do not switch engines for each NPC or send simultaneous synthesis requests. Cancelling an HTTP request may not immediately stop ongoing MLX computation. Streaming inference errors can arise after HTTP 200; inspect `/runs` and manage incomplete audio.

## Game-facing recommendations

**The Last Emporium:** the AI bargaining logic generates text and mood, then a separate Godot speech queue converts them to `voice`, `archetype`, and `instruct`. Hermes is a speech synthesizer, **not** a dialogue/negotiation model. Show text before the TTS result. Tie facial expressions to the game emotion state and audio playback, not to an inferred waveform.

Start with WAV to prove NPC mapping and audio; switch to the raw PCM endpoint only after a tested Godot `HTTPClient` streaming player exists. See [The Last Emporium integration guide](LAST_EMPORIUM_INTEGRATION.md) and [Godot adapter](../examples/godot/HermesTTS.gd).

## Current limits

V3 has **not** passed actual MLX M2/8GB or end-to-end Godot acceptance in this environment. The documented speaker IDs are mappings; local clone recordings and voices must be checked on the Mac. No authentication, automatic retries, server-side priority queue, persistent cache, text-to-voice identity registry, timestamped visemes, or official game-level cancellation job API is present. These are future integration features, not promises of the existing endpoint.
