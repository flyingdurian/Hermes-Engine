# The Living Counter — Exodia integration (v0.2 handoff)

**Milestone:** standalone Godot 4.4+ AI haggling prototype, distinct from The Last Emporium. The full, tested v0.2 project is distributed as `The_Living_Counter_POC_v0.2_Exodia.zip` in the ChatGPT handoff. This repository branch currently holds milestone records; **the entire v0.2 source is not yet synchronized**. Run the ZIP's `publish_to_hermes.sh` in the owner's Hermes-Engine clone to publish the source tree, without secrets or generated PNGs.

## Contract

- **Hermes:** `http://10.0.0.129:8000`, or `:8880` if that is serving `/health` and `/stream`. Godot sends raw PCM streaming TTS here, independent of the dialogue model.
- **Exodia:** `http://127.0.0.1:8765` integrated, or `:8766` Cursor test, whichever has active signed-in ChatGPT/Gemini/DeepSeek browser sessions. Interactive docs: `/api/docs`.
- **Game Director:** `http://127.0.0.1:8010`, bound to loopback, holds NPC profiles, prices, state and the Exodia client. The player UI **never** calls Exodia.
- Every Exodia call sends `X-Exodia-Client: exodia-dashboard`. POST `/api/command/tasks` uses `agent_id:kiko`, `model_choice:deepseek|chatgpt|gemini`, `transport:browser`, `web_research:false`. Do **not** select `:api` names.
- Saved model instructions plus `custom_instructions` must be <=4000 characters; composed message <=12000. The app reads saved-instruction length but does not overwrite any shared provider prompt. Shop NPC is in `custom_instructions`, recent scene/transcript in `prompt`.
- The app POSTs once and polls GET `/api/command/tasks` about every 500ms for the exact id. It cancels unfinished tasks with POST `/api/command/tasks/{id}/cancel` and must not submit overlapping browser tasks.
- The game director's `POST /talk` commits authoritative bargains then returns HTTP **202** and a `job_id` for Godot to poll via `GET /dialogue/{job_id}`; HTML/audio wait only for a completed NPC answer. Late interrupted answers are discarded. On cancellation uncertainty the Exodia adapter fails closed.
- Only the **game engine** approves bargains and commits inventory or money. LLM supplies short price-free character flavor. Prices are appended from verified game state.
- Push-to-talk **STT is separate**, still needs an independent configured provider. Browser-model Exodia does not transcribe audio.

## Verification and acceptance

- Python + simulated Exodia contract: **27 tests passed** at package creation, including task headers, allowed agent/provider, instruction budget, polling, cancellation and economy isolation.
- Actual signed-in Exodia, real Godot runtime, Hermes Mac audio, 8GB memory and microphone remain **Mac acceptance pending**. Test those before merging into a live game.
- Branch publish command, from the extracted ZIP beside the owner's Hermes clone: `./publish_to_hermes.sh`. It excludes `.env`, saves, Python venv, generated art PNGs, and refuses dirty git trees.

**Do not merge this branch into main until the full source is pushed and Mac acceptance has passed.** The V3 Hermes engine baseline is still tracked by draft PR #1.