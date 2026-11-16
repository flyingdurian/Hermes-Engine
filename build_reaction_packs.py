import os
import soundfile as sf
import numpy as np
from mlx_audio.tts.utils import load_model

VOICES_DIR = os.path.expanduser("~/Projects/npc-tts-studio/voices")
REACTIONS_DIR = os.path.expanduser("~/Projects/npc-tts-studio/npc_reactions")
os.makedirs(REACTIONS_DIR, exist_ok=True)

# Maps 6 broad archetypes to 1 existing seed voice clip each
ARCHETYPES = {
    "female_anime_bright": "f01_sakura_genki.wav",
    "female_anime_sharp":  "f02_reina_tsundere.wav",
    "female_warm":         "f06_mira_innkeeper.wav",
    "female_mature":       "f09_tessa_blacksmith.wav",
    "male_young":          "m01_kenji_shonen.wav",
    "male_deep":           "m03_garrick_dwarf.wav",
}

# Short carrier phrases force Chatterbox to perform the tag naturally; we keep the non-verbal burst
PROMPTS = {
    "laugh":   "[laugh] Ha ha, oh that is rich!",
    "chuckle": "[chuckle] Heh, well now.",
    "sigh":    "[sigh] Oh, alright then.",
    "gasp":    "[gasp] What?!",
    "groan":   "[groan] Ugh, come on...",
}

print("Loading Chatterbox Turbo 4-bit to auto-generate archetype reactions...", flush=True)
model = load_model("mlx-community/chatterbox-turbo-4bit")

for arch, seed_file in ARCHETYPES.items():
    arch_dir = os.path.join(REACTIONS_DIR, arch)
    os.makedirs(arch_dir, exist_ok=True)
    seed_path = os.path.join(VOICES_DIR, seed_file)

    for tag, prompt in PROMPTS.items():
        out_wav = os.path.join(arch_dir, f"{tag}.wav")
        if os.path.exists(out_wav):
            continue
        print(f"Synthesizing {arch}/{tag}.wav...", flush=True)
        kwargs = {"text": prompt, "temperature": 0.65, "repetition_penalty": 1.3}
        if not os.path.exists(seed_path):
            print(f"Skipping missing seed: {seed_path}", flush=True)
            continue
        kwargs["ref_audio"] = seed_path

        chunks = [np.asarray(r.audio).reshape(-1) for r in model.generate(**kwargs)]
        if not chunks:
            raise RuntimeError(f"No audio for {arch}/{tag}")
        audio = np.concatenate(chunks)
        # Trim trailing silence and keep the first ~1.35 seconds (the non-verbal vocalization)
        max_samples = int(24000 * 1.35)
        sf.write(out_wav, audio[:max_samples], 24000)

print("Done! All archetype reaction packs saved to ~/Projects/npc-tts-studio/npc_reactions/!")
