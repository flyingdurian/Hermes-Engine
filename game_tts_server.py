import io
import os
import re
import gc
import time
import inspect
import threading
import anyio
import uuid
from collections import OrderedDict
from typing import Literal
from scipy.signal import stft, istft
from starlette.background import BackgroundTask
import numpy as np
import soundfile as sf
from scipy.signal import resample
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, AliasChoices, ConfigDict
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
active_model_id = None
INFERENCE = threading.Lock()
RUNS = OrderedDict()
RUN_LOCK = threading.Lock()
API_VERSION = "3.0"
active_model = None
model_caps = {"stream": False, "interval": False, "lang": False, "lang_code": False}

def get_model(engine_name: str, model_id=None):
    """Keeps strictly ONE model in 8GB unified RAM at a time to prevent SSD swap."""
    global active_engine, active_model, active_model_id, model_caps
    eng = engine_name.lower()
    if eng not in MODELS:
        raise ValueError(f"Unknown engine: {eng}")

    model_id = model_id or MODELS[eng]
    if active_engine == eng and active_model_id == model_id and active_model is not None:
        return active_model

    if active_model is not None:
        print(f"[SWAP] Unloading {active_engine} to keep 8GB unified RAM clean...", flush=True)
        del active_model
        active_model = None
        active_engine = active_model_id = None
        gc.collect()
        mx.clear_cache()

    print(f"[LOAD] Loading {eng} ({model_id})...", flush=True)
    t0 = time.perf_counter()
    active_model = load_model(model_id)
    active_engine = eng
    active_model_id = model_id

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
del qwen_startup
_ = None
print("[WARMUP] Qwen3-TTS Metal shaders ready!", flush=True)

app = FastAPI(title="Unified Multi-Engine Game NPC TTS Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Reaction-Archetype", "X-Reaction-Tag", "X-Resolved-Instruct", "X-Active-Engine", "X-Run-Id", "X-Model-Id", "X-Sample-Rate", "X-Load-Seconds", "X-Model-State"],
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
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    engine: Literal["qwen", "chatterbox", "kokoro"] = "qwen"
    input: str = Field(min_length=1, max_length=10000)
    voice: str = "Vivian"
    archetype: str = Field("female_warm", pattern=r"^[A-Za-z0-9_-]+$")
    instruct: str = ""
    mood: str = "neutral"
    model_id: str | None = None
    speed: float = Field(1.0, ge=0.6, le=1.6)
    pitch: float = Field(0.0, ge=-4, le=4, validation_alias=AliasChoices("pitch", "pitch_shift"))
    temperature: float = Field(0.45, ge=0.2, le=1.1)
    repetition_penalty: float = Field(1.3, ge=1, le=1.8)
    streaming_interval: float = Field(0.5, ge=0.1, le=5)
    min_chunk_chars: int = Field(85, ge=10, le=1000, validation_alias=AliasChoices("min_chunk_chars", "stream_chunk_min"))
    force_reload: bool = False

MOOD_TAGS = {"happy":"chuckle", "suspicious":"sigh", "irritated":"groan", "furious":"groan", "sad":"sigh", "excited":"laugh", "scared":"gasp", "nervous":"gasp"}

def delivery(req):
    cleaned, tag, instruct = parse_script(req.input, req.instruct, req.engine == "chatterbox")
    # Explicit tags win over auto-insertion; do not repeat a reaction in every chunk.
    if req.engine == "chatterbox" and not tag:
        mood = req.mood.lower()
        inferred = next((v for k,v in MOOD_TAGS.items() if re.search(r"\b"+k+r"\b", instruct.lower())), "")
        tag = MOOD_TAGS.get(mood, inferred)
        if tag: cleaned = f"[{tag}] {cleaned}"
    if req.engine == "kokoro":
        # Punctuation pacing, not an imitation of Qwen's semantic acting.
        if req.mood in ("suspicious", "sad", "nervous"):
            cleaned = re.sub(r"[,;]", "...", cleaned)
        elif req.mood in ("happy", "excited", "furious", "irritated"):
            cleaned = re.sub(r"\.(?=\s|$)", "!", cleaned)
    return cleaned, tag, instruct

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
    resolved_instruct = ", ".join(p for p in [base_instruct.strip(), *acting_parts] if p)

    return cleaned, reaction_tag, resolved_instruct

def stretch_audio(audio, rate):
    """Phase-vocoder duration change at fixed pitch; bypass tiny packets."""
    n = min(1024, len(audio))
    if n < 64: return resample(audio, max(1, round(len(audio)/rate)))
    hop = n // 4
    _, _, spec = stft(audio, nperseg=n, noverlap=n-hop)
    spec = np.pad(spec, ((0,0),(0,2)))
    steps = np.arange(0, spec.shape[1]-2, rate)
    phase = np.angle(spec[:,0]); advance = 2*np.pi*hop*np.arange(spec.shape[0])/n
    out = np.empty((spec.shape[0],len(steps)), dtype=np.complex64)
    for j,t in enumerate(steps):
        i=int(t); f=t-i
        out[:,j] = ((1-f)*np.abs(spec[:,i])+f*np.abs(spec[:,i+1]))*np.exp(1j*phase)
        delta=np.angle(spec[:,i+1])-np.angle(spec[:,i])-advance
        phase += advance + (delta+np.pi)%(2*np.pi)-np.pi
    _, y = istft(out, nperseg=n, noverlap=n-hop)
    size=max(1,round(len(audio)/rate))
    return np.pad(y,(0,max(0,size-len(y))))[:size].astype(np.float32)

def shape_audio(audio, speed, pitch, native_speed=False):
    audio=np.asarray(audio,dtype=np.float32).reshape(-1)
    if not len(audio): return audio
    if not native_speed and abs(speed-1)>0.02:
        # Preserve the original low-cost speed path; speed also changes pitch.
        audio=resample(audio,max(1,round(len(audio)/speed))).astype(np.float32)
    if abs(pitch)>0.05:
        factor=2**(pitch/12)
        audio=resample(stretch_audio(audio,1/factor),len(audio)).astype(np.float32)
    return audio

def find_ref_audio(voice_name: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", voice_name or "") or voice_name == "default":
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
        current = f"{current} {s}".strip()
        if len(current) >= min_chars:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks or [text]

def generate_checked(model, kwargs):
    sig=inspect.signature(model.generate)
    if not any(p.kind==inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        unknown=set(kwargs)-set(sig.parameters)
        if unknown: raise ValueError(f"Model does not accept: {sorted(unknown)}")
    return model.generate(**kwargs)

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
        for res in generate_checked(model,kwargs):
            yield shape_audio(np.asarray(res.audio), req.speed, req.pitch, native_speed=False)

    elif eng == "chatterbox":
        kwargs = {
            "text": cleaned_text,
            "temperature": req.temperature,
            "repetition_penalty": req.repetition_penalty,
        }
        ref_path = find_ref_audio(req.voice)
        if ref_path:
            kwargs["ref_audio"] = ref_path
        for res in generate_checked(model,kwargs):
            yield shape_audio(np.asarray(res.audio), req.speed, req.pitch, native_speed=False)

    else:
        for res in generate_checked(model,{"text":cleaned_text,"voice":req.voice,"speed":req.speed}):
            yield shape_audio(np.asarray(res.audio), req.speed, req.pitch, native_speed=True)

@app.get("/health")
def health():
    archetypes = {}
    if os.path.exists(REACTIONS_DIR):
        for arch in sorted(os.listdir(REACTIONS_DIR)):
            p = os.path.join(REACTIONS_DIR, arch)
            if os.path.isdir(p):
                archetypes[arch] = sorted([os.path.splitext(f)[0] for f in os.listdir(p) if f.endswith(".wav")])
    custom_wavs = sorted({os.path.splitext(f)[0] for f in os.listdir(VOICES_DIR) if os.path.splitext(f)[1] in (".wav", ".mp3", ".flac", ".m4a")})
    return {"status": "ok", "active_engine": active_engine, "native_stream": model_caps["stream"], "reactions": archetypes, "chatterbox_voices": custom_wavs, "models": MODELS, "active_model_id": active_model_id, "api_version": API_VERSION, "busy": INFERENCE.locked(), "model_caps": model_caps}

@app.get("/reaction/{archetype}/{tag}")
def get_reaction(archetype: str, tag: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", archetype) or tag.lower() not in REACTION_TAGS:
        raise HTTPException(404, "Unknown reaction")
    wav_path = os.path.join(REACTIONS_DIR, archetype, f"{tag.lower()}.wav")
    if not os.path.exists(wav_path):
        raise HTTPException(status_code=404, detail=f"Reaction {archetype}/{tag}.wav not found")
    return FileResponse(wav_path, media_type="audio/wav")

def save_run(run):
    with RUN_LOCK:
        RUNS[run["id"]] = dict(run)
        while len(RUNS)>300: RUNS.popitem(last=False)

@app.get("/runs/{run_id}")
def get_run(run_id: str):
    with RUN_LOCK:
        if run_id not in RUNS: raise HTTPException(404,"Unknown run")
        return RUNS[run_id]

def prepare(req, streaming):
    global active_model, active_engine, active_model_id
    if not req.input.strip(): raise HTTPException(422,"Empty input")
    if not req.voice.strip(): raise HTTPException(422,"Voice ID required")
    if req.engine == "chatterbox" and req.voice != "default" and not find_ref_audio(req.voice):
        raise HTTPException(422,f"Missing clone recording: {req.voice}")
    cleaned, tag, instruct = delivery(req)
    if not cleaned or not re.sub(r"\[[^\]]+\]", "", cleaned).strip(): raise HTTPException(422,"No spoken text")
    model_id = req.model_id or MODELS[req.engine]
    if req.engine == "qwen" and model_id == MODELS["qwen"] and req.voice.lower() not in {"vivian","serena","uncle_fu","dylan","eric","ryan","aiden","ono_anna","sohee"}: raise HTTPException(422,"Unknown stock Qwen speaker")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",model_id): raise HTTPException(422,"Expected Hugging Face owner/model ID")
    if not INFERENCE.acquire(blocking=False): raise HTTPException(409,"Engine busy; retry after current inference ends")
    try:
        state = "warm" if active_model is not None and active_engine==req.engine and active_model_id==model_id else "swap" if active_model is not None else "cold"
        if req.force_reload:
            state="forced-reload"; active_model=None; active_engine=active_model_id=None; gc.collect(); mx.clear_cache()
        t=time.perf_counter()
        model=get_model(req.engine, model_id)
        load_seconds=time.perf_counter()-t
        sample_rate=int(getattr(model,"sample_rate",24000) or 24000)
        native=streaming and req.engine=="qwen" and model_caps["stream"]
        groups=smart_chunks(cleaned, req.min_chunk_chars) if streaming and not native else [cleaned]
        run={"id":uuid.uuid4().hex,"engine":req.engine,"model_id":model_id,"model_state":state,"load_s":load_seconds,"sample_rate":sample_rate,"native_stream":bool(native),"status":"running","chunks":[],"ttfa_s":None,"total_s":None,"audio_duration_s":None,"effective_text":cleaned,"resolved_instruct":instruct,"reaction_tag":tag,"request":req.model_dump(),"ignored_controls":(["temperature","repetition_penalty","instruct (semantic)"] if req.engine=="kokoro" else ["instruct (semantic; tag mapping only)"] if req.engine=="chatterbox" else []) + (["stream_chunk_min"] if native or not streaming else []) + (["streaming_interval"] if not native or not model_caps["interval"] else []),"chunk_count":len(groups)}
        save_run(run)
        headers={"X-Run-Id":run["id"],"X-Model-Id":model_id,"X-Sample-Rate":str(sample_rate),"X-Load-Seconds":str(load_seconds),"X-Model-State":state,"X-Active-Engine":req.engine,"X-Reaction-Archetype":req.archetype,"X-Reaction-Tag":tag,"X-Resolved-Instruct":instruct.encode("ascii","backslashreplace").decode("ascii").replace("\n"," ").replace("\r"," ")}
        return model, groups, native, run, headers, instruct
    except Exception:
        INFERENCE.release(); raise

def packets(model, groups, native, run, req, instruct):
    t=time.perf_counter(); samples=0; index=0
    try:
        for i, group in enumerate(groups,1):
            start=time.perf_counter()
            for audio in run_engine_audio(model, req.engine, group, instruct, req, native):
                if not len(audio): continue
                now=time.perf_counter(); index+=1; samples+=len(audio)
                if run["ttfa_s"] is None: run["ttfa_s"]=now-t
                run["chunks"].append({"packet":index,"chunk":i,"of":len(groups),"elapsed_s":now-t,"chunk_elapsed_s":now-start,"samples":len(audio)})
                yield audio
        if not samples: raise RuntimeError("Model returned no audio")
        run["status"]="complete"
    except BaseException as exc:
        run["status"]="cancelled" if isinstance(exc,GeneratorExit) else "error"; run["error"]=str(exc); raise
    finally:
        run["total_s"]=time.perf_counter()-t; run["audio_duration_s"]=samples/run["sample_rate"]
        save_run(run); mx.clear_cache()

@app.post("/v1/audio/speech")
def full_speech(req: SpeechRequest):
    model, groups, native, run, headers, instruct=prepare(req,False)
    try:
        audio=np.concatenate(list(packets(model,groups,native,run,req,instruct)))
        buf=io.BytesIO(); sf.write(buf,audio,run["sample_rate"],format="WAV")
        return Response(buf.getvalue(),media_type="audio/wav",headers=headers)
    finally:
        del model
        INFERENCE.release()

@app.post("/stream")
def stream_speech(req: SpeechRequest):
    model, groups, native, run, headers, instruct=prepare(req,True)
    released=False
    def release():
        nonlocal released, model
        if not released:
            released=True; model=None; INFERENCE.release()
    gen=packets(model,groups,native,run,req,instruct)
    def next_packet():
        audio=next(gen,None)
        return None if audio is None else np.clip(audio*32767,-32768,32767).astype("<i2").tobytes()
    async def pcm():
        try:
            while True:
                data=await anyio.to_thread.run_sync(next_packet)
                if data is None: break
                yield data
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(gen.close)
                release()
    def cleanup():
        gen.close(); release()
    return StreamingResponse(pcm(),media_type="audio/pcm",headers=headers,background=BackgroundTask(cleanup))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
