"""LongCat-Video-Avatar 1.5 on Modal: the on-screen presenter (optional; scripts/presenter.py).

Audio + one picture of the presenter on chroma green -> a talking, lip-synced video. Used for the
hook (frame 0, the presenter large beside the board, 720p on an H200) and the corner wipe (one
clip per later frame, 480p on an H100). MIT weights (Meituan). 1.5 runs distilled: 8 steps,
guidance 1.0 (one DiT pass per step), 25 fps; segment 1 = 93 frames (3.72 s), every continuation
+80 frames (3.2 s). 720p from a 2:3 portrait = 800x1152.

Measured (Oct 2026): 720p needs more than an H100's 80 GB from segment 2 (bf16) -> H200; ~190 s of
H200 per 3.2 s segment (~US$0.075 per output second, ~US$2.40 for a 30 s hook) + ~2 min model load.
480p on an H100: ~58 s per 3.2 s segment (~US$0.02 per output second).

**Gesture direction.** The demo passes the prompt on every segment; `segment_prompts[k]` (when
set) replaces it for segment k, so a clip follows the per-sentence directions.

    python scripts/longcat_avatar_modal.py download   # once: ~57 GB -> Modal Volume longcat-avatar-weights
    (generation is called from scripts/presenter.py)

Imported as a top-level module (scripts/ on sys.path); keep top-level imports to stdlib + modal.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import modal

LC_COMMIT = "6b3f4b8582a8bc3f20f795735f5383716c4ba794"   # meituan-longcat/LongCat-Video main, 2026-05-27
FA_WHEEL = ("https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/"
            "flash_attn-2.7.4.post1+cu12torch2.6cxx11abiFALSE-cp310-cp310-linux_x86_64.whl")
W = "/w"
REF_IMG_INDEX = 30        # demo default 10
MASK_FRAME_RANGE = 5      # demo default 3 ("excessively large values may introduce artifacts")
DEMO = "run_demo_avatar_single_audio_to_video.py"
# Per-segment prompts + save only the final cut (the demo re-encodes the whole clip every segment).
_PATCH = (
    f"cd /lc && python - <<'PY'\n"
    f"p = '{DEMO}'; s = open(p).read()\n"
    "s = s.replace(\"    prompt = input_data['prompt']\\n\", \"    prompt = input_data['prompt']\\n    segment_prompts = input_data.get('segment_prompts') or []\\n\", 1)\n"
    "s = s.replace('            prompt=prompt,\\n            negative_prompt=negative_prompt,\\n            height=height,\\n            width=width,\\n            num_frames=num_frames,\\n            num_cond_frames=num_cond_frames,',"
    " '            prompt=(segment_prompts[segment_idx] if segment_idx < len(segment_prompts) and segment_prompts[segment_idx] else prompt),\\n            negative_prompt=negative_prompt,\\n            height=height,\\n            width=width,\\n            num_frames=num_frames,\\n            num_cond_frames=num_cond_frames,', 1)\n"
    "s = s.replace('        if cp_rank == 0:\\n            output_tensor = torch.from_numpy(np.array(all_generated_frames))', '        if cp_rank == 0 and segment_idx == num_segments - 1:\\n            output_tensor = torch.from_numpy(np.array(all_generated_frames))', 1)\n"
    "assert \"input_data.get('segment_prompts')\" in s and 'segment_prompts[segment_idx]' in s and 'segment_idx == num_segments - 1' in s, 'LongCat demo patch did not apply'\n"
    "open(p, 'w').write(s)\n"
    "PY"
)

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "ffmpeg", "libsndfile1")
    .pip_install("torch==2.6.0", "torchvision==0.21.0", "torchaudio==2.6.0",
                 index_url="https://download.pytorch.org/whl/cu124")
    .pip_install(FA_WHEEL, "ninja", "packaging", "psutil", "hf_transfer", "huggingface_hub[cli]")
    .run_commands(
        f"git clone https://github.com/meituan-longcat/LongCat-Video.git /lc && git -C /lc checkout {LC_COMMIT}",
        "grep -v -E '^(torch|flash-attn|libsndfile1|tritonserverclient)==' /lc/requirements.txt > /tmp/r1.txt",
        "grep -v -E '^(torch|flash-attn|libsndfile1|tritonserverclient)==' /lc/requirements_avatar.txt > /tmp/r2.txt",
        "pip install -r /tmp/r1.txt -r /tmp/r2.txt",
        _PATCH,
    )
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "PYTHONUNBUFFERED": "1",
          "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"})
)
vol = modal.Volume.from_name("longcat-avatar-weights", create_if_missing=True)
app = modal.App("ludium-longcat", image=image)


@app.function(volumes={W: vol}, timeout=3 * 3600, cpu=8, memory=16384)
def _download():
    from huggingface_hub import snapshot_download
    snapshot_download("meituan-longcat/LongCat-Video-Avatar-1.5", local_dir=f"{W}/LongCat-Video-Avatar-1.5",
                      allow_patterns=["config.json", "model_index.json", "base_model/*", "lora/*", "scheduler/*",
                                      "vocal_separator/*", "whisper-large-v3/*.json", "whisper-large-v3/*.txt",
                                      "whisper-large-v3/model.safetensors"])
    snapshot_download("meituan-longcat/LongCat-Video", local_dir=f"{W}/LongCat-Video",
                      allow_patterns=["tokenizer/*", "text_encoder/*", "vae/*", "scheduler/*", "*.json"],
                      ignore_patterns=["dit/*", "lora/*"])
    vol.commit()


def segments_for(seconds: float) -> int:
    n = 1
    while 3.72 + 3.2 * (n - 1) < seconds:
        n += 1
    return n


def segment_window(k: int) -> tuple:
    """Output-time window [start, end) that segment k contributes (k = 0 is the first 93 frames)."""
    return (0.0, 3.72) if k == 0 else (3.72 + 3.2 * (k - 1), 3.72 + 3.2 * k)


def _presenter(image_bytes: bytes, audio_bytes: bytes, prompt: str, segment_prompts: list,
              audio_seconds: float, resolution: str = "720p", ref_img_index: int = None,
              mask_frame_range: int = None) -> dict:
    work = Path("/tmp/job")
    work.mkdir(exist_ok=True)
    (work / "in.png").write_bytes(image_bytes)
    (work / "in.wav").write_bytes(audio_bytes)
    (work / "in.json").write_text(json.dumps({
        "prompt": prompt, "segment_prompts": segment_prompts, "cond_image": str(work / "in.png"),
        "cond_audio": {"person1": str(work / "in.wav")}}))
    nseg = segments_for(audio_seconds)
    cmd = ["torchrun", "--nproc_per_node=1", DEMO, "--context_parallel_size=1",
           f"--checkpoint_dir={W}/LongCat-Video-Avatar-1.5", "--stage_1=ai2v", f"--input_json={work / 'in.json'}",
           f"--output_dir={work / 'out'}", f"--resolution={resolution}", f"--num_segments={nseg}",
           "--use_distill", "--model_type=avatar-v1.5",
           # repo tips: ref index 30 + a wider mask cut repeated actions — a gesture pose (finger at the
           # chin) otherwise carries through the next segments and blocks the next direction
           f"--ref_img_index={REF_IMG_INDEX if ref_img_index is None else ref_img_index}",
           f"--mask_frame_range={MASK_FRAME_RANGE if mask_frame_range is None else mask_frame_range}"]
    t0 = time.time()
    p = subprocess.run(cmd, cwd="/lc", capture_output=True, text=True)
    outs = sorted((work / "out").glob("*.mp4"), key=lambda f: f.stat().st_mtime)
    res = {"returncode": p.returncode, "seconds": round(time.time() - t0, 1), "segments": nseg,
           "log_tail": (p.stdout + p.stderr)[-4000:]}
    if p.returncode == 0 and outs:
        res["mp4"] = outs[-1].read_bytes()
    return res


# 720p (hooks, outro) needs an H200 from segment 2; 480p (the corner wipe) fits an H100. A 10-min wipe is
# ~190 segments x ~58 s ≈ 3 h, so the timeout is generous; videos run in parallel containers.
@app.function(volumes={W: vol}, gpu="H200", timeout=8 * 3600, memory=65536)
def presenter(*a, **k):
    return _presenter(*a, **k)


@app.function(volumes={W: vol}, gpu="H100", timeout=8 * 3600, memory=65536)
def presenter_h100(*a, **k):
    return _presenter(*a, **k)


def run_presenters(jobs: list) -> list:
    """Local entry: jobs = [{key, image (Path), audio (Path), prompt, segment_prompts}] -> results,
    one container per job (480p on an H100, otherwise an H200), all in parallel."""
    args = []
    for j in jobs:
        wav = subprocess.run(["ffmpeg", "-v", "error", "-i", str(j["audio"]), "-ar", "44100", "-ac", "1",
                              "-f", "wav", "pipe:1"], capture_output=True, check=True).stdout
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                    "csv=p=0", str(j["audio"])], capture_output=True, text=True).stdout)
        args.append((Path(j["image"]).read_bytes(), wav, j["prompt"], j.get("segment_prompts") or [], dur,
                     j.get("resolution", "720p"), j.get("ref_img_index"), j.get("mask_frame_range")))
    with modal.enable_output(), app.run():
        calls = [(presenter_h100 if a[5] == "480p" else presenter).spawn(*a) for a in args]
        res = [c.get() for c in calls]
    for j, r in zip(jobs, res):
        r["key"] = j["key"]
    return res


if __name__ == "__main__":
    if sys.argv[1:2] == ["download"]:
        with modal.enable_output(), app.run():
            _download.remote()
