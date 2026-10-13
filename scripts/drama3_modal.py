"""Drama 3 on Modal — open-weights TTS on a rented GPU (TTS_PROVIDER=drama3).

Fish Audio Drama 3 (open weights, 35B MoE / 3B active; Fish Audio Community License — free
commercial use while your annual revenue is under US$500k, show "Built with Fish Audio"; clone
only voices you have consent for) served by sglang-omni on one Modal H100. It clones the
narrator zero-shot from 1-3 reference clips of the voice you want (DRAMA3_REFS_DIR, default
inputs/voice/drama3_refs/: ref_1.wav + ref_1.txt …, each clip <= 60 s, the .txt its exact
transcript). The server returns audio only, so every clip is force-aligned on the same GPU
(wav2vec2 CTC over the narration's own tokens) and comes back with word timestamps in the same
shape as the hosted providers'.

Cost: Modal's Starter plan includes US$30/month of free compute. A session pays ~3-4 min of H100
start-up (~US$0.25), then ~15 s (~US$0.02) per 10 min of narration with 16 requests in flight,
so batch videos into one session (scripts/tts_drama3_batch.py).

Setup, once:
    pip install modal && modal setup                 # Modal account + token (~/.modal.toml)
    python scripts/drama3_modal.py download          # ~40 GB of weights -> Modal Volume (CPU, minutes)

Imported as the top-level module `drama3_modal` (scripts/ on sys.path), so Modal mounts this one
file into the container — keep its top-level imports to the standard library + modal.
"""

import contextlib
import hashlib
import os
import subprocess
import time
from pathlib import Path

import modal

SGO_COMMIT = "b6ce00edef3047ec39dd72897aa41ebee4c23600"   # sglang-omni main, 2026-10-13
BASE = "lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469"
PY = "/opt/sglang/bin/python3"
ALIGN_MODEL = "facebook/wav2vec2-large-960h-lv60-self"   # English character CTC (A-Z, ', |)
MODEL_ID = f"fishaudio/drama-3@sglang-omni-{SGO_COMMIT[:7]}"
REPO = Path(__file__).resolve().parent.parent
REFS_DIR = Path(os.getenv("DRAMA3_REFS_DIR") or REPO / "inputs" / "voice" / "drama3_refs")
if not REFS_DIR.is_absolute():
    REFS_DIR = REPO / REFS_DIR
CONCURRENCY = 16          # the engine's max_running_requests default
MAX_CHUNK_CHARS = 1500    # longer frames are voiced and aligned in sentence-aligned chunks
GAP_SECONDS = 0.25        # silence between chunks of one frame

image = (
    modal.Image.from_registry(BASE)
    .apt_install("sox", "git", "ffmpeg")
    .run_commands(
        f"git clone https://github.com/sgl-project/sglang-omni.git /sgo && git -C /sgo checkout {SGO_COMMIT}",
        f"cd /sgo && {PY} -c 'import tomllib; print(\"\\n\".join(tomllib.load(open(\"pyproject.toml\",\"rb\"))[\"tool\"][\"uv\"][\"override-dependencies\"]))' > /tmp/ov.txt",
        f"cd /sgo && uv pip install --python {PY} --prerelease=allow --overrides /tmp/ov.txt -e '.[fishaudio]'",
    )
    .env({"HF_HOME": "/vol/hf", "PATH": "/opt/sglang/bin:/usr/local/bin:/usr/bin:/bin"})
)
vol = modal.Volume.from_name("drama3-weights", create_if_missing=True)
app = modal.App("ludium-drama3", image=image)


def _chunks(text, limit=MAX_CHUNK_CHARS):
    """Sentence-aligned chunks of at most ~limit characters (a lone long sentence stays whole)."""
    import re
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for s in re.split(r"(?<=[.!?])\s+", text.strip()):
        if cur and len(cur) + 1 + len(s) > limit:
            out.append(cur); cur = s
        else:
            cur = f"{cur} {s}" if cur else s
    return out + [cur] if cur else out


def _align(model, processor, audio16k, words):
    """CTC forced alignment of `words` (whitespace tokens) onto 16 kHz audio -> [(start, end)]."""
    import torch
    vocab = processor.tokenizer.get_vocab()
    blank, sep = vocab["<pad>"], vocab["|"]
    tokens, owner = [], []          # owner[i] = word index of token i (-1 for separators)
    for wi, w in enumerate(words):
        letters = [c for c in w.upper() if c in vocab and c not in ("|", "<pad>")]
        if tokens:
            tokens.append(sep); owner.append(-1)
        for c in letters:
            tokens.append(vocab[c]); owner.append(wi)
    with torch.inference_mode():
        x = processor(audio16k, sampling_rate=16000, return_tensors="pt").input_values.to("cuda").half()
        emission = torch.log_softmax(model(x).logits[0].float(), dim=-1).cpu()
    T, J = emission.shape[0], len(tokens)
    # Viterbi trellis (torchaudio forced-alignment tutorial formulation)
    trellis = torch.full((T + 1, J + 1), -float("inf"))
    trellis[0, 0] = 0
    trellis[1:, 0] = torch.cumsum(emission[:, blank], 0)
    tok = torch.tensor(tokens)
    for t in range(T):
        trellis[t + 1, 1:] = torch.maximum(trellis[t, 1:] + emission[t, blank],
                                           trellis[t, :-1] + emission[t, tok])
    j, t, path = J, T, []
    while j > 0 and t > 0:
        stay = trellis[t - 1, j] + emission[t - 1, blank]
        move = trellis[t - 1, j - 1] + emission[t - 1, tokens[j - 1]]
        if move > stay:
            path.append((j - 1, t - 1)); j -= 1
        t -= 1
    sec = len(audio16k) / 16000 / T
    spans = {}
    for ti, fr in path:
        wi = owner[ti]
        if wi >= 0:
            a, b = spans.get(wi, (fr, fr))
            spans[wi] = (min(a, fr), max(b, fr))
    out, last = [], 0.0
    for wi in range(len(words)):          # a word with no alignable letters inherits the gap
        if wi in spans:
            s, e = spans[wi][0] * sec, (spans[wi][1] + 1) * sec
            out.append((round(s, 3), round(e, 3))); last = e
        else:
            out.append((round(last, 3), round(last, 3)))
    return out


@app.cls(gpu="H100", volumes={"/vol": vol}, timeout=3600, scaledown_window=300)
class Drama3:
    @modal.enter()
    def start(self):
        import requests
        log = open("/tmp/serve.log", "w")
        self.srv = subprocess.Popen(["sgl-omni", "serve", "--model-path", "/vol/drama-3", "--port", "8000"],
                                    stdout=log, stderr=subprocess.STDOUT)
        t0 = time.time()
        while True:
            if self.srv.poll() is not None:
                raise RuntimeError("Drama 3 server died:\n" + open("/tmp/serve.log").read()[-6000:])
            try:
                if requests.get("http://localhost:8000/health", timeout=2).status_code < 500:
                    break
            except requests.RequestException:
                pass
            if time.time() - t0 > 1800:
                raise RuntimeError("Drama 3 server not up in 30 min:\n" + open("/tmp/serve.log").read()[-6000:])
            time.sleep(5)
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        self.processor = Wav2Vec2Processor.from_pretrained(ALIGN_MODEL)
        self.aligner = Wav2Vec2ForCTC.from_pretrained(ALIGN_MODEL).to("cuda").eval().half()
        vol.commit()
        self.refs_written = False
        print(f"Drama 3 up in {time.time() - t0:.0f}s", flush=True)

    @modal.method()
    def voice(self, refs: list, items: list) -> list:
        """refs: [(wav bytes, transcript)]; items: [(key, text)] -> [(key, mp3|None, words|None, error|None)]."""
        import io, concurrent.futures as cf
        import numpy as np, requests, soundfile as sf, librosa
        if not self.refs_written:
            for i, (wav, _) in enumerate(refs):
                open(f"/tmp/ref{i}.wav", "wb").write(wav)
            self.refs_written = True
        references = [{"audio_path": f"/tmp/ref{i}.wav", "text": t} for i, (_, t) in enumerate(refs)]
        jobs = [(key, ci, chunk) for key, text in items for ci, chunk in enumerate(_chunks(text))]

        def one(job):
            key, ci, chunk = job
            r = requests.post("http://localhost:8000/v1/audio/speech", timeout=1200,
                              json={"input": chunk, "references": references})
            return job, r

        audio = {}
        t0 = time.time()
        with cf.ThreadPoolExecutor(CONCURRENCY) as ex:
            for (key, ci, chunk), r in ex.map(one, jobs):
                if r.status_code != 200:
                    audio[(key, ci)] = RuntimeError(f"{r.status_code} {r.text[:1000]}")
                    continue
                pcm, sr = sf.read(io.BytesIO(r.content), dtype="float32")
                audio[(key, ci)] = (pcm.mean(1) if pcm.ndim > 1 else pcm, sr, chunk)
        print(f"voiced {len(jobs)} chunks in {time.time() - t0:.0f}s", flush=True)
        out = []
        for key, text in items:
            parts = [audio[(k, ci)] for (k, ci, _) in jobs if k == key]
            err = next((p for p in parts if isinstance(p, Exception)), None)
            if err:
                out.append((key, None, None, str(err))); continue
            sr = parts[0][1]
            gap = np.zeros(int(GAP_SECONDS * sr), dtype=np.float32)
            pcm, words, offset = [], [], 0.0
            for i, (p, _, chunk) in enumerate(parts):
                if i:
                    pcm.append(gap); offset += GAP_SECONDS
                toks = chunk.split()
                spans = _align(self.aligner, self.processor, librosa.resample(p, orig_sr=sr, target_sr=16000), toks)
                words += [{"word": w, "start": round(s + offset, 3), "end": round(e + offset, 3)}
                          for w, (s, e) in zip(toks, spans)]
                pcm.append(p); offset += len(p) / sr
            wav = io.BytesIO(); sf.write(wav, np.concatenate(pcm), sr, format="WAV", subtype="PCM_16")
            mp3 = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-ar", "44100", "-ac", "1",
                                  "-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3", "pipe:1"],
                                 input=wav.getvalue(), capture_output=True, check=True).stdout
            out.append((key, mp3, words, None))
        return out


@app.function(volumes={"/vol": vol}, timeout=3600, cpu=4)
def _download():
    from huggingface_hub import snapshot_download
    snapshot_download("fishaudio/drama-3", local_dir="/vol/drama-3")
    vol.commit()


def download():
    with modal.enable_output(), app.run():
        _download.remote()


def ref_files():
    """[(wav path, transcript path)] for ref_*.wav in REFS_DIR (1-3 clips)."""
    pairs = [(w, w.with_suffix(".txt")) for w in sorted(REFS_DIR.glob("ref_*.wav"))]
    if not pairs:
        raise SystemExit(f"✗ Drama 3 needs reference clips: {REFS_DIR}/ref_1.wav + ref_1.txt "
                         f"(1-3 clips of the narrator, <= 60 s each; set DRAMA3_REFS_DIR to move them)")
    for w, t in pairs:
        if not t.exists():
            raise SystemExit(f"✗ {w.name} has no transcript {t.name}")
    return pairs[:3]


def refs_id():
    """The narrator's identity in tts_meta.json: a hash of the clips + transcripts, so a video
    voiced with one reference set never silently mixes with another."""
    h = hashlib.sha1()
    for w, t in ref_files():
        h.update(w.read_bytes()); h.update(t.read_text().strip().encode())
    return "refs-" + h.hexdigest()[:10]


def load_refs():
    return [(w.read_bytes(), t.read_text().strip()) for w, t in ref_files()]


class Session:
    """A warm Drama 3 container for the life of the `with` block (the start-up is paid once)."""

    def __enter__(self):
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(app.run())
        self._d = Drama3()
        self._refs = load_refs()
        return self

    def synthesize(self, items):
        """items: [(key, text)] -> ({key: (mp3 bytes, words)}, {key: error})."""
        t0 = time.time()
        res = self._d.voice.remote(self._refs, list(items))
        print(f"  [drama3] {len(res)} clip(s) in {time.time() - t0:.0f}s (incl. any container start)")
        out, errs = {}, {}
        for key, mp3, words, err in res:
            if err:
                errs[key] = err
            else:
                out[key] = (mp3, words)
        return out, errs

    def __exit__(self, *exc):
        self._stack.close()
        return False


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["download"]:
        download()
        print("✓ Drama 3 weights are on the Modal Volume drama3-weights")
    else:
        print(__doc__)
