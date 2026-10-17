"""SoulX-FlashTalk-14B on Modal: the presenter's corner WIPE (optional; scripts/presenter.py).

Audio + one head-and-shoulders picture of the presenter on chroma green -> a lip-synced talking
video at 480x480 / 25 fps, keyed into the bottom-right corner window by `presenter.py`. Apache-2.0
weights (Soul-AILab; InfiniteTalk + Wan 2.1 + Self-Forcing), one H100 80 GB, ~59 GB peak.

Streams in 1.12 s chunks (28 new frames, 5 carried forward, 8 s audio context), so a clip has no
length limit. Measured (Oct 2026): 480x480 at 4 steps = 2.87 s of H100 per chunk = ~US$0.003 per
output second, plus ~3 min of start-up + compile per container (and again for any NEW output size —
keep one size per container). So each video's wipe clips go into ONE `wipes()` call, videos in
parallel (`run_wipe_batches`): a 10-minute video ≈ US$2.50 — about 1/6 of a LongCat wipe.

It lip-syncs, shows facial expressions and nods; it does not do hand gestures (the hands are out of
shot). That is the default wipe: LongCat's hand gestures proved unreliable (a raised hand held to the
end of the clip, a ghost copy of the resting hands, captions drawn in late in long clips), and little
was lost without them.

**Expression direction.** The text prompt is encoded once into `pipe.arg_c['context']`; `schedule`
swaps in a different pre-encoded prompt for the chunks that overlap each [start, end) window. The
motion frames carry across chunks, so an expression blends in and out instead of cutting.

    python scripts/flashtalk_modal.py download        # once: ~54 GB -> Modal Volume flashtalk-weights
    (generation is called from scripts/presenter.py)

Imported as a top-level module (scripts/ on sys.path) so Modal mounts this one file; keep its
top-level imports to the standard library + modal.
"""
import sys
import time
from pathlib import Path

import modal

FT_COMMIT = "2ba2e1e801d59a9f526212a5aae5d374db8f2978"   # Soul-AILab/SoulX-FlashTalk main, 2026-07-30
FT_REPO = "Soul-AILab/SoulX-FlashTalk-14B"
W2V_REPO = "TencentGameMate/chinese-wav2vec2-base"
FA_WHEEL = ("https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.0.post2/"
            "flash_attn-2.8.0.post2+cu12torch2.7cxx11abiTRUE-cp310-cp310-linux_x86_64.whl")
WIPE_SIZE = (480, 480)
WIPE_STEPS = 4
SEED = 9999

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("ffmpeg", "git", "libgl1", "libglib2.0-0")
    .pip_install("torch==2.7.1", "torchvision==0.22.1", index_url="https://download.pytorch.org/whl/cu128")
    .pip_install(
        "xformers==0.0.31", FA_WHEEL,
        "diffusers==0.34.0", "transformers==4.51.3", "tokenizers==0.21.1", "accelerate==1.8.1",
        "huggingface_hub==0.34.4", "safetensors==0.5.3", "xfuser==0.4.5", "yunchang==0.6.3.post1",
        "optimum-quanto==0.2.6", "opencv-python-headless==4.12.0.88", "tqdm", "imageio==2.37.0",
        "imageio-ffmpeg==0.6.0", "easydict==1.13", "ftfy==6.3.1", "scikit-image==0.25.2",
        "loguru==0.7.3", "pyloudnorm==0.1.1", "decord==0.6.0", "librosa==0.11.0", "einops==0.8.1",
        "numpy==2.2.6", "pyyaml==6.0.2", "sentencepiece==0.2.0", "regex",
    )
    .run_commands(f"git clone https://github.com/Soul-AILab/SoulX-FlashTalk.git /ft && git -C /ft checkout {FT_COMMIT}")
    .env({"HF_HOME": "/vol/hf"})
)
vol = modal.Volume.from_name("flashtalk-weights", create_if_missing=True)
app = modal.App("ludium-flashtalk", image=image)


@app.function(volumes={"/vol": vol}, timeout=3600, cpu=4)
def _download():
    from huggingface_hub import snapshot_download
    t0 = time.time()
    snapshot_download(FT_REPO, local_dir="/vol/SoulX-FlashTalk-14B",
                      allow_patterns=["*.json", "*.safetensors", "Wan2.1_VAE.pth",
                                      "models_t5_umt5-xxl-enc-bf16.pth",
                                      "models_clip_open-clip-xlm-roberta-large-vit-huge-14.pth",
                                      "google/*", "xlm-roberta-large/*", "LICENSE.txt"])
    snapshot_download(W2V_REPO, local_dir="/vol/chinese-wav2vec2-base",
                      allow_patterns=["config.json", "preprocessor_config.json", "pytorch_model.bin"])
    vol.commit()
    print(f"downloaded in {time.time() - t0:.0f}s")


@app.function(volumes={"/vol": vol}, gpu="H100", timeout=6 * 3600, cpu=4, memory=65536)
def wipes(image_bytes: bytes, jobs: list, size=WIPE_SIZE, steps=WIPE_STEPS, seed=SEED) -> list:
    """jobs: [{key, wav16 (16 kHz mono wav bytes), prompt, schedule: [[start, end, prompt], ...]}]
    -> [{key, mp4 (green-screen video with the job's audio), seconds, chunks, cue_chunks}]"""
    import os
    import subprocess
    os.chdir("/ft")
    sys.path.insert(0, "/ft")
    import numpy as np
    import torch
    import librosa
    import imageio
    from collections import deque
    from flash_talk.inference import get_pipeline, get_audio_embedding, run_pipeline, infer_params

    Path("/tmp/cond.png").write_bytes(image_bytes)
    t0 = time.time()
    pipe = get_pipeline(world_size=1, ckpt_dir="/vol/SoulX-FlashTalk-14B", wav2vec_dir="/vol/chinese-wav2vec2-base")
    print(f"[flashtalk] model loaded in {time.time() - t0:.0f}s", flush=True)
    sr, fps = infer_params["sample_rate"], infer_params["tgt_fps"]
    frame_num, motion = infer_params["frame_num"], infer_params["motion_frames_num"]
    slice_len = frame_num - motion
    cached = infer_params["cached_audio_duration"]
    chunk_s = slice_len / fps

    def encode(prompt):
        if pipe.cpu_offload:
            pipe.text_encoder.model.to(pipe.device)
        c = pipe.text_encoder([prompt], pipe.device)[0]
        if pipe.cpu_offload:
            pipe.text_encoder.model.cpu()
        return [c]

    out = []
    for job in jobs:
        tj = time.time()
        Path("/tmp/audio.wav").write_bytes(job["wav16"])
        pipe.prepare_params(input_prompt=job["prompt"], cond_image="/tmp/cond.png", target_size=tuple(size),
                            frame_num=frame_num, motion_frames_num=motion, sampling_steps=steps, seed=seed,
                            shift=infer_params["sample_shift"],
                            color_correction_strength=infer_params["color_correction_strength"])
        base_ctx = pipe.arg_c["context"]
        cues = [(float(a), float(b), encode(p)) for a, b, p in (job.get("schedule") or [])]
        a = librosa.load("/tmp/audio.wav", sr=sr, mono=True)[0]
        sl = slice_len * sr // fps
        if len(a) % sl:
            a = np.concatenate([a, np.zeros(sl - len(a) % sl, dtype=a.dtype)])
        end_idx = cached * fps
        start_idx = end_idx - frame_num
        dq = deque([0.0] * (sr * cached), maxlen=sr * cached)
        frames, cue_chunks = [], []
        for ci, piece in enumerate(a.reshape(-1, sl)):
            dq.extend(piece.tolist())
            c0, c1 = ci * chunk_s, (ci + 1) * chunk_s
            ctx = next((c for s, e, c in cues if c0 < e and c1 > s), base_ctx)
            pipe.arg_c["context"] = ctx
            if ctx is not base_ctx:
                cue_chunks.append(ci)
            emb = get_audio_embedding(pipe, np.array(dq), start_idx, end_idx)
            frames.append(run_pipeline(pipe, emb)[motion:].cpu())
        raw, mp4 = "/tmp/raw.mp4", "/tmp/out.mp4"
        with imageio.get_writer(raw, format="mp4", mode="I", fps=fps, codec="h264",
                                ffmpeg_params=["-bf", "0", "-crf", "16"]) as wr:
            for fr in frames:
                fr = fr.numpy().astype(np.uint8)
                for i in range(fr.shape[0]):
                    wr.append_data(fr[i])
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-i", "/tmp/audio.wav", "-c:v", "copy",
                        "-c:a", "aac", "-shortest", mp4], check=True)
        out.append({"key": job["key"], "mp4": Path(mp4).read_bytes(), "seconds": round(time.time() - tj, 1),
                    "chunks": len(frames), "cue_chunks": cue_chunks})
        print(f"[flashtalk] {job['key']}: {len(frames)} chunks ({len(frames) * chunk_s:.0f}s video) in "
              f"{time.time() - tj:.0f}s; {len(cue_chunks)} cue chunks", flush=True)
    return out


def _payload(jobs: list) -> list:
    import subprocess
    out = []
    for j in jobs:
        wav = subprocess.run(["ffmpeg", "-v", "error", "-i", str(j["wav"]), "-ar", "16000", "-ac", "1",
                              "-f", "wav", "pipe:1"], capture_output=True, check=True).stdout
        out.append({"key": j["key"], "wav16": wav, "prompt": j["prompt"], "schedule": j.get("schedule")})
    return out


def run_wipes(image: Path, jobs: list) -> list:
    """Local entry: jobs carry a `wav` Path; returns the remote results (one container)."""
    with modal.enable_output(), app.run():
        return wipes.remote(Path(image).read_bytes(), _payload(jobs))


def run_wipe_batches(image: Path, batches: list, size=WIPE_SIZE) -> list:
    """Several batches (one per video) in parallel containers, one size. A failed container returns
    {key, error} for each of its jobs instead of raising, so the other batches' clips are still saved."""
    img = Path(image).read_bytes()
    with modal.enable_output(), app.run():
        calls = [(b, wipes.spawn(img, _payload(b), tuple(size))) for b in batches]
        out = []
        for b, c in calls:
            try:
                out += c.get()
            except Exception as e:                              # noqa: BLE001 — report per job
                out += [{"key": j["key"], "error": f"{type(e).__name__}: {e}"} for j in b]
    return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["download"]:
        with modal.enable_output(), app.run():
            _download.remote()
