import io
import os
import re
import gc
import time
import inspect
import numpy as np
import soundfile as sf
from scipy.signal import resample
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
import mlx.core as mx
from mlx_audio.tts.utils import load_model

VOICES_DIR = os.path.expanduser("~/Projects/npc-tts-studio/voices")
REACTIONS_DIR = os.path.expanduser("~/Projects/npc-tts-studio/npc_reactions")
os.makedirs(VOICES_DIR, exist_ok=True)
os.makedirs(REACTIONS_DIR, exist_ok=True)

MODELS = {
    "qwen": "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit",
    "chatterbox": "mlx-community/chatterbox-turbo-4bit",
    "kokoro": "mlx-community/Kokoro-82M-bf16",
}

active_engine = None
active_model = None
model_caps = {"stream": False, "interval": False, "lang": False, "lang_code": False}

def get_model(engine_name: str):
    """Keeps strictly ONE model in 8GB unified RAM at a time to prevent SSD swap."""
    global active_engine, active_model, model_caps
    eng = engine_name.lower()
    if eng not in MODELS:
        eng = "qwen"

    if active_engine == eng and active_model is not None:
        return active_model

    if active_model is not None:
        print(f"[SWAP] Unloading {active_engine} to keep 8GB unified RAM clean...", flush=True)
        del active_model
        active_model = None
        gc.collect()
        mx.clear_cache()

    model_id = MODELS[eng]
    print(f"[LOAD] Loading {eng} ({model_id})...", flush=True)
    t0 = time.perf_counter()
    active_model = load_model(model_id)
    active_engine = eng

    sig = inspect.signature(active_model.generate)
    model_caps = {
        "stream": "stream" in sig.parameters,
        "interval": "streaming_interval" in sig.parameters,
        "lang": "language" in sig.parameters,
        "lang_code": "lang_code" in sig.parameters,
    }
    print(f"[READY] {eng} loaded in {time.perf_counter() - t0:.2f}s (native_stream={model_caps['stream']})", flush=True)
    return active_model

qwen_startup = get_model("qwen")
warm_kwargs = {"text": "Greetings traveler, what brings you to my stall today?", "voice": "Vivian", "instruct": "Warm, welcoming merchant voice", "temperature": 0.45}
if model_caps["lang"]:
    warm_kwargs["language"] = "English"
elif model_caps["lang_code"]:
    warm_kwargs["lang_code"] = "en"
for _ in qwen_startup.generate(**warm_kwargs):
    pass
print("[WARMUP] Qwen3-TTS Metal shaders ready!", flush=True)

app = FastAPI(title="Unified Multi-Engine Game NPC TTS Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Reaction-Archetype", "X-Reaction-Tag", "X-Resolved-Instruct", "X-Active-Engine"],
)

REACTION_TAGS = {"laugh", "chuckle", "sigh", "gasp", "groan"}

EMOTION_INSTRUCTS = {
    "angry": "Speak in an angry, sharp, irritated voice with strong emphasis",
    "furious": "Speak in a furious, loud, outraged, shouting voice",
    "mad": "Speak in an angry, frustrated, harsh tone",
    "happy": "Speak in a happy, warm, cheerful, enthusiastic voice",
    "excited": "Speak in an excited, energetic, upbeat, lively voice",
    "sad": "Speak in a sad, sorrowful, heavy, downcast tone",
    "nervous": "Speak in a nervous, hesitant, anxious, shaky voice",
    "scared": "Speak in a frightened, panicked, trembling voice",
    "sarcastic": "Speak in a sarcastic, mocking, dry, condescending tone",
    "smug": "Speak in a smug, arrogant, proud, superior tone",
    "whisper": "Speak in a quiet, secretive, hushed whisper",
    "tired": "Speak in an exhausted, weary, low-energy tone",
    "friendly": "Speak in a friendly, welcoming, warm merchant voice",
    "suspicious": "Speak in a suspicious, guarded, skeptical tone",
    "laugh": "Speak with an amused, laughing, cheerful tone",
    "chuckle": "Speak with a warm, amused, chuckling undertone",
    "sigh": "Speak in a tired, resigned, sighing tone",
    "gasp": "Speak in a shocked, startled, breathless tone",
    "groan": "Speak in an exasperated, annoyed, complaining tone",
}

class SpeechRequest(BaseModel):
    engine: str = "qwen"
    input: str
    voice: str = "Vivian"
    archetype: str = "female_warm"
    instruct: str = ""
    speed: float = 1.0
    pitch: float = 0.0
    temperature: float = 0.45
    repetition_penalty: float = 1.3
    streaming_interval: float = 0.5
    min_chunk_chars: int = 85

def parse_script(raw_text: str, base_instruct: str, keep_reaction_tags: bool = False):
    tags = [t.strip().lower() for t in re.findall(r'\[([^\]]+)\]', raw_text)]
    reaction_tag = next((t for t in tags if t in REACTION_TAGS), "")

    if keep_reaction_tags:
        def keep_or_strip(match):
            t = match.group(1).strip().lower()
            return f"[{t}]" if t in REACTION_TAGS else ""
        cleaned = re.sub(r'\[([^\]]+)\]', keep_or_strip, raw_text)
    else:
        cleaned = re.sub(r'\[[^\]]+\]', '', raw_text)

    cleaned = re.sub(r'\?+!+|!+\?+', '?', cleaned)
    cleaned = re.sub(r'!{2,}', '!', cleaned)
    cleaned = re.sub(r'\?{2,}', '?', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    acting_parts = [EMOTION_INSTRUCTS.get(t, f"Speak in a {t} tone") for t in tags]
    resolved_instruct = ", ".join(acting_parts) if acting_parts else base_instruct.strip()

    return cleaned, reaction_tag, resolved_instruct

def shape_audio(audio: np.ndarray, speed: float, pitch: float, native_speed: bool = False) -> np.ndarray:
    if abs(pitch) > 0.05:
        factor = 2.0 ** (pitch / 12.0)
        shifted = resample(audio, max(1, int(len(audio) / factor)))
        audio = resample(shifted, len(audio)).astype(np.float32)
    if not native_speed and abs(speed - 1.0) > 0.02:
        audio = resample(audio, max(1, int(len(audio) / speed))).astype(np.float32)
    return audio

def find_ref_audio(voice_name: str):
    if not voice_name or voice_name == "default":
        return None
    for ext in ("wav", "mp3", "flac", "m4a"):
        p = os.path.join(VOICES_DIR, f"{voice_name}.{ext}")
        if os.path.exists(p):
            return p
    return None

def smart_chunks(text: str, min_chars: int = 85):
    if min_chars >= len(text):
        return [text]
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks, current = [], ""
    for s in sentences:
        if not s:
            continue
        if len(current) + len(s) < min_chars:
            current = f"{current} {s}".strip()
        else:
            if current:
                chunks.append(current)
            current = s
    if current:
        chunks.append(current)
    return chunks or [text]

def run_engine_audio(model, eng: str, cleaned_text: str, resolved_instruct: str, req: SpeechRequest, stream_mode: bool):
    if eng == "qwen":
        kwargs = {
            "text": cleaned_text,
            "voice": req.voice or "Vivian",
            "temperature": req.temperature,
            "repetition_penalty": req.repetition_penalty,
        }
        if model_caps["lang"]:
            kwargs["language"] = "English"
        elif model_caps["lang_code"]:
            kwargs["lang_code"] = "en"
        if resolved_instruct:
            kwargs["instruct"] = resolved_instruct
        if stream_mode and model_caps["stream"]:
            kwargs["stream"] = True
            if model_caps["interval"]:
                kwargs["streaming_interval"] = req.streaming_interval
        for res in model.generate(**kwargs):
            yield shape_audio(np.array(res.audio, copy=False), req.speed, req.pitch, native_speed=False)

    elif eng == "chatterbox":
        kwargs = {
            "text": cleaned_text,
            "temperature": req.temperature,
            "repetition_penalty": req.repetition_penalty,
        }
        ref_path = find_ref_audio(req.voice)
        if ref_path:
            kwargs["ref_audio"] = ref_path
        for res in model.generate(**kwargs):
            yield shape_audio(np.array(res.audio, copy=False), req.speed, req.pitch, native_speed=False)

    else:
        for res in model.generate(text=cleaned_text, voice=req.voice or "af_heart", speed=req.speed):
            yield shape_audio(np.array(res.audio, copy=False), req.speed, req.pitch, native_speed=True)

@app.get("/health")
def health():
    archetypes = {}
    if os.path.exists(REACTIONS_DIR):
        for arch in sorted(os.listdir(REACTIONS_DIR)):
            p = os.path.join(REACTIONS_DIR, arch)
            if os.path.isdir(p):
                archetypes[arch] = sorted([os.path.splitext(f)[0] for f in os.listdir(p) if f.endswith(".wav")])
    custom_wavs = sorted([os.path.splitext(f)[0] for f in os.listdir(VOICES_DIR) if f.endswith(".wav")])
    return {"status": "ok", "active_engine": active_engine, "native_stream": model_caps["stream"], "reactions": archetypes, "chatterbox_voices": custom_wavs}

@app.get("/reaction/{archetype}/{tag}")
def get_reaction(archetype: str, tag: str):
    wav_path = os.path.join(REACTIONS_DIR, archetype, f"{tag.lower()}.wav")
    if not os.path.exists(wav_path):
        raise HTTPException(status_code=404, detail=f"Reaction {archetype}/{tag}.wav not found")
    return FileResponse(wav_path, media_type="audio/wav")

@app.post("/v1/audio/speech")
def full_speech(req: SpeechRequest):
    eng = req.engine.lower()
    model = get_model(eng)
    cleaned, reaction_tag, resolved_instruct = parse_script(req.input, req.instruct, keep_reaction_tags=(eng == "chatterbox"))
    print(f"--> [FULL] engine={eng} | voice={req.voice} | chars={len(cleaned)} | reaction={reaction_tag or 'none'}", flush=True)
    t0 = time.perf_counter()

    chunks = list(run_engine_audio(model, eng, cleaned, resolved_instruct, req, stream_mode=False))
    audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

    buf = io.BytesIO()
    sf.write(buf, audio, 24000, format="WAV")
    mx.clear_cache()
    print(f"<-- [DONE FULL] engine={eng} | total={time.perf_counter() - t0:.2f}s", flush=True)

    return Response(
        content=buf.getvalue(),
        media_type="audio/wav",
        headers={"X-Active-Engine": eng, "X-Reaction-Archetype": req.archetype, "X-Reaction-Tag": reaction_tag, "X-Resolved-Instruct": resolved_instruct},
    )

@app.post("/stream")
def stream_speech(req: SpeechRequest):
    eng = req.engine.lower()
    model = get_model(eng)
    cleaned, reaction_tag, resolved_instruct = parse_script(req.input, req.instruct, keep_reaction_tags=(eng == "chatterbox"))
    print(f"--> [STREAM] engine={eng} | voice={req.voice} | chars={len(cleaned)} | reaction={reaction_tag or 'none'}", flush=True)

    def pcm_generator():
        t0 = time.perf_counter()
        first_time = None
        packets = 0

        if eng == "qwen" and model_caps["stream"]:
            for audio_f32 in run_engine_audio(model, eng, cleaned, resolved_instruct, req, stream_mode=True):
                packets += 1
                audio_i16 = np.clip(audio_f32 * 32767.0, -32768, 32767).astype(np.int16)
                if first_time is None:
                    first_time = time.perf_counter() - t0
                    print(f"    [FIRST AUDIO - NATIVE STREAM] TTFA={first_time:.2f}s", flush=True)
                yield audio_i16.tobytes()
        else:
            grouped = smart_chunks(cleaned, min_chars=req.min_chunk_chars)
            for idx, chunk_text in enumerate(grouped, 1):
                c_start = time.perf_counter()
                for audio_f32 in run_engine_audio(model, eng, chunk_text, resolved_instruct, req, stream_mode=False):
                    packets += 1
                    audio_i16 = np.clip(audio_f32 * 32767.0, -32768, 32767).astype(np.int16)
                    if first_time is None:
                        first_time = time.perf_counter() - t0
                        print(f"    [FIRST AUDIO] TTFA={first_time:.2f}s (chunk 1/{len(grouped)})", flush=True)
                    yield audio_i16.tobytes()
                print(f"    [CHUNK {idx}/{len(grouped)}] took {time.perf_counter() - c_start:.2f}s", flush=True)

        mx.clear_cache()
        print(f"<-- [DONE STREAM] engine={eng} | packets={packets} | TTFA={first_time:.2f}s | total={time.perf_counter() - t0:.2f}s", flush=True)

    return StreamingResponse(
        pcm_generator(),
        media_type="audio/pcm",
        headers={"X-Active-Engine": eng, "X-Reaction-Archetype": req.archetype, "X-Reaction-Tag": reaction_tag, "X-Resolved-Instruct": resolved_instruct},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
