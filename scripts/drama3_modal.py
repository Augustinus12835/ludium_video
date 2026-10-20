"""Drama 3 on Modal — open-weights TTS on a rented GPU (TTS_PROVIDER=drama3).

Fish Audio Drama 3 (open weights, 35B MoE / 3B active; Fish Audio Community License — free
commercial use while your annual revenue is under US$500k, show "Built with Fish Audio"; clone
only voices you have consent for) served by sglang-omni on one Modal H100. It clones the
narrator zero-shot from 1-3 reference clips of the voice you want (DRAMA3_REFS_DIR, default
inputs/voice/drama3_refs/: ref_1.wav + ref_1.txt …, each clip <= 60 s, the .txt its exact
transcript). The server returns audio only, so every clip is force-aligned on the same GPU
(wav2vec2 CTC over the narration's own tokens) and comes back with word timestamps in the same
shape as the hosted providers'.
Every clip is also checked before it ships: clip QA (cut off mid-word, silent stretches) and a
hear-back (Whisper large-v3 + the CTC model listen for misreads such as a hyphen read as "minus");
a failing chunk is re-voiced in the same container, and a misread that survives the retakes is
kept and listed in audio/stt_flags.json.

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
import re
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
ENGINE_MEM_FRACTION = "0.60"   # engine share of the H100; the rest must also hold Whisper + aligner
MAX_CHUNK_CHARS = 900     # longer frames are voiced and aligned in sentence-aligned chunks. Drama 3
                          # stops a request at ~95 s of audio (~15 chars/s): 1500-char chunks silently
                          # lost the tail of long frames in production; 900 ≈ 60 s.
MAX_CLIP_SECONDS = 90.0   # a chunk this long has probably hit that cap — fail it, never ship it
GAP_SECONDS = 0.25        # silence between chunks of one frame
# Clip QA. Drama 3 sometimes stops a clip mid-word ("tangent to each oth–") or goes silent part-way
# and pads the rest with near-silence (one 62 s frame spoke only its first 21 s). Forced alignment
# places every token whatever the audio holds, so the timestamps cannot show either. Each chunk is
# therefore checked against what the aligner's own CTC model HEARS (greedy decode, no language
# model — Whisper-style ASR autocompletes a cut "oth–" to "other") and against its loudness, and a
# failed chunk is re-requested in the same warm container.
QA_RETRIES = 3            # extra takes per failing chunk before the frame fails
QA_TAIL_CHARS = 3         # script letters after the last thing heard -> the clip ended early
QA_GAP_CHARS = 25         # a run of script letters this long never heard -> words dropped
QA_SILENCE_SECONDS = 3.0  # a stretch this long 35 dB under the speech level -> dropout
QA_END_CUT_DB = -32.0     # last 30 ms this close to the loudest 100 ms -> cut mid-sound

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


# Drama 3 directions (technical report, Appendix C) are ordinary text the model reads and never
# speaks: point tags `[pause]`, scoped tags `<emphasis>…</emphasis>`, a style prefix `[warm] line`,
# or a director's note `Direction: …\nLine: …`. They steer delivery; the aligner places only the
# spoken words, so markup is stripped first (`spoken_text`).
_NOTE = re.compile(r"^\s*Direction:.*?\nLine:\s*", re.S)
_MARKUP = re.compile(r"\[[^\[\]\n]*\]|</?[A-Za-z][^<>\n]*>")


def line_direction(text):
    """(line-level direction to repeat on every chunk, the rest): a director's note or a
    leading style prefix applies to the whole line, so a long frame's chunks all get it."""
    m = _NOTE.match(text)
    if m:
        return m.group(0), text[m.end():]
    m = re.match(r"^\s*(\[[^\[\]\n]*\])\s+", text)
    if m and not re.fullmatch(r"\[(pause|breath|sighs?|laughs?)\]", m.group(1)):
        return m.group(1) + " ", text[m.end():]
    return "", text


def spoken_text(text):
    """The words actually spoken: the text minus every direction."""
    t = " ".join(_MARKUP.sub(" ", _NOTE.sub("", text)).split())
    return re.sub(r"\s+([.,;:!?)\]])", r"\1", t)          # "sixteen</emphasis>." -> "sixteen."


def _chunks(text, limit=MAX_CHUNK_CHARS):
    """Sentence-aligned chunks of at most ~limit characters (a lone long sentence stays whole);
    a line-level direction is repeated on each chunk."""
    head, body = line_direction(text)
    if len(body) <= limit:
        return [head + body]
    out, cur = [], ""
    for s in re.split(r"(?<=[.!?])\s+", body.strip()):
        if cur and len(cur) + 1 + len(s) > limit:
            out.append(cur); cur = s
        else:
            cur = f"{cur} {s}" if cur else s
    return [head + c for c in (out + [cur] if cur else out)]


def _emission(model, processor, audio16k):
    """Per-frame CTC log-probabilities of 16 kHz audio (T x vocab, on the CPU)."""
    import torch
    with torch.inference_mode():
        dev, dt = next(model.parameters()).device, next(model.parameters()).dtype
        x = processor(audio16k, sampling_rate=16000, return_tensors="pt").input_values.to(dev, dt)
        return torch.log_softmax(model(x).logits[0].float(), dim=-1).cpu()


def heard_text(processor, emission):
    """Greedy CTC decode: what the acoustic model hears, with no language model to fill gaps."""
    ids = emission.argmax(-1).tolist()
    blank = processor.tokenizer.get_vocab()["<pad>"]
    toks = processor.tokenizer.convert_ids_to_tokens(
        [i for k, i in enumerate(ids) if i != blank and (k == 0 or i != ids[k - 1])])
    return " ".join("".join(toks).replace("|", " ").split())


def _letters(text):
    return re.sub(r"[^A-Z']", "", text.upper())


def check_clip(audio16k, words, heard):
    """Problems with one voiced chunk ([] = fine). audio16k: float mono 16 kHz; words: the chunk's
    spoken tokens; heard: heard_text() of the same audio. Pure numpy + difflib (also run locally)."""
    import difflib
    import numpy as np
    probs = []
    exp, got = _letters(" ".join(words)), _letters(heard)
    if exp:
        covered = np.zeros(len(exp), bool)
        sm = difflib.SequenceMatcher(None, exp, got, autojunk=False)
        got_end = 0                               # end of the last real match in what was heard
        for b in sm.get_matching_blocks():
            if b.size >= 3:                       # 1-2 letter "matches" are coincidence
                covered[b.a:b.a + b.size] = True
                got_end = b.b + b.size
        owner = [wi for wi, w in enumerate(words) for _ in _letters(w)]
        hit = np.flatnonzero(covered)
        tail = len(exp) - (hit[-1] + 1 if len(hit) else 0)
        # A misheard last word ("theta" -> SATA, "phi" -> FI) still leaves letters after the last
        # match; a clip that stopped early leaves (almost) none ("…EACH" for "each other").
        if tail >= QA_TAIL_CHARS and len(got) - got_end < tail / 2:
            probs.append("ends early — not heard: " + repr(" ".join(words[owner[len(exp) - tail]:])[-80:]))
        run, start = 0, 0
        for i, c in enumerate(covered[:len(exp) - tail] if tail else covered):
            if not c:
                if run == 0:
                    start = i
                run += 1
                if run == QA_GAP_CHARS:
                    probs.append("words not heard: " + repr(" ".join(words[owner[start]:owner[start] + 8])[:80] + "…"))
            else:
                run = 0
    a = np.asarray(audio16k, dtype=np.float64)
    nz = np.flatnonzero(np.abs(a) > 1e-4)
    if len(nz) > 4800:
        a = a[:nz[-1] + 1]
        w = 160                                   # 10 ms frames
        n = len(a) // w
        db = 20 * np.log10(np.sqrt((a[:n * w].reshape(n, w) ** 2).mean(1)) + 1e-9)
        quiet = db < np.percentile(db, 95) - 35
        longest = run = 0
        for q in quiet:
            run = run + 1 if q else 0
            longest = max(longest, run)
        if longest * 0.01 >= QA_SILENCE_SECONDS:
            probs.append(f"{longest * 0.01:.1f} s of silence inside the clip")
        peak = 10 * np.log10(np.convolve(a ** 2, np.ones(1600) / 1600, "valid").max() + 1e-18)
        end = 10 * np.log10((a[-480:] ** 2).mean() + 1e-18)
        if end - peak > QA_END_CUT_DB:
            probs.append(f"cut off mid-sound (last 30 ms at {end - peak:.0f} dB of peak)")
    return probs


# Hear-back. Drama 3 also MISREADS, which check_clip cannot see (the right amount of speech is
# there): an in-token hyphen read as "minus" or "dash" ("T-v" -> "T minus v": in production ~12% of
# sentences with a dashed token) or garbled ("T mass v"). Two listeners hear every clip: Whisper
# large-v3 (a language model: it writes clean words) and the aligner's own CTC model (letters, no
# language model). Calibrated on ~1,300 human-labelled clips:
#   a stray "minus"/"dash"/"hyphen" heard by EITHER listener: 136 of 140 misreads caught, 2 of
#   1,007 clean sentences flagged, 0 of 140 corrected takes;
#   a stretch where BOTH listeners hear something other than the script: caught the garbles (all
#   real on listening), 0 of 140 corrected takes. (Whisper alone glues letters, "T v" -> "TV", and
#   drops stretches of maths; CTC alone misspells; where both disagree with the script, the voice
#   usually did.)
# A flagged chunk is re-voiced like a QA failure; one still flagged after QA_RETRIES keeps its last
# take and the flags travel back with it (audio/stt_flags.json) for a person to listen to.
WHISPER_MODEL = "openai/whisper-large-v3"
HB_BAD = ("minus", "dash", "dashes", "hyphen")
HB_GAP_LETTERS = 8        # script letters in a row that BOTH listeners missed
HB_INSERT_LETTERS = 4     # letters BOTH listeners heard at the same spot that the script lacks
HB_TOLERANCE = 4          # "the same spot", in script letters
_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
         "fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _say(n):
    n = int(n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + _say(n % 100))
    if n < 10 ** 6:
        return _say(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + _say(n % 1000))
    return str(n)


def hb_words(text):
    """Lower-case words with digits spelled out (Whisper writes "2x", the script "two x")."""
    t = text.lower().replace("%", " percent ")
    t = re.sub(r"(\d+)\.(\d+)", lambda m: _say(m.group(1)) + " point " + " ".join(_ONES[int(c)] for c in m.group(2)), t)
    t = re.sub(r"\d+", lambda m: " " + _say(m.group(0)) + " ", t)
    return [w.strip("'") for w in re.sub(r"[^a-z' ]+", " ", t).split() if w.strip("'")]


def _covered(script, heard, minblock=3):
    """script/heard: letter strings -> (covered[] per script letter, [(script_pos, extra_letters)])."""
    import difflib
    sm = difflib.SequenceMatcher(None, script, heard, autojunk=False)
    cov, ins, pa, pb = [False] * len(script), [], 0, 0
    for b in sm.get_matching_blocks():
        if b.size >= minblock or b.size == 0:
            if (b.b - pb) - (b.a - pa) > 0:
                ins.append((b.a, (b.b - pb) - (b.a - pa)))
            cov[b.a:b.a + b.size] = [True] * b.size
            pa, pb = b.a + b.size, b.b + b.size
    return cov, ins


def _near(script_w, heard_w, i, pad=3):
    """The heard words around script word i (word-level alignment)."""
    import difflib
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, script_w, heard_w, autojunk=False).get_opcodes():
        if i1 <= i < max(i2, i1 + 1):
            if op == "equal":
                j1 = j2 = j1 + (i - i1)
            return " ".join(heard_w[max(0, j1 - pad):j2 + pad])
    return ""


def hearback(words, whisper, ctc):
    """Misreads in one clip ([] = fine). words: the spoken tokens; whisper/ctc: what each heard.
    -> [{"kind", "word" (index into words), "whisper", "ctc"}]. Pure python (also runs locally)."""
    import collections
    import difflib
    sw, ww, cw = [], hb_words(whisper), hb_words(ctc)
    owner_w = []                                   # normalised word -> token index
    for ti, t in enumerate(words):
        for w in hb_words(t):
            sw.append(w); owner_w.append(ti)
    if not sw:
        return []
    out = []
    script_n = collections.Counter(sw)
    for heard in (ww, cw):
        if not any(collections.Counter(heard)[b] > script_n[b] for b in HB_BAD):
            continue
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sw, heard, autojunk=False).get_opcodes():
            if op != "equal" and any(heard[j1:j2].count(b) > sw[i1:i2].count(b) for b in HB_BAD):
                i = min(i1, len(sw) - 1)
                out.append({"kind": "stray word", "word": owner_w[i],
                            "whisper": _near(sw, ww, i), "ctc": _near(sw, cw, i)})
                break
        if out:
            break
    s_letters, owner_l = "", []
    for i, w in enumerate(sw):
        s_letters += w; owner_l += [i] * len(w)
    cov_w, ins_w = _covered(s_letters, "".join(ww))
    cov_c, ins_c = _covered(s_letters, "".join(cw))
    run = 0
    for k in range(len(s_letters)):
        run = run + 1 if not (cov_w[k] or cov_c[k]) else 0
        if run == HB_GAP_LETTERS:
            i = owner_l[k - run + 1]
            out.append({"kind": "not heard", "word": owner_w[i], "whisper": _near(sw, ww, i), "ctc": _near(sw, cw, i)})
    for pa, la in ins_w:
        if la >= HB_INSERT_LETTERS and any(abs(pa - pb) <= HB_TOLERANCE and lb >= HB_INSERT_LETTERS for pb, lb in ins_c):
            i = owner_l[min(pa, len(owner_l) - 1)]
            out.append({"kind": "something else heard", "word": owner_w[i],
                        "whisper": _near(sw, ww, i), "ctc": _near(sw, cw, i)})
    seen, uniq = set(), []                         # one flag per token
    for f in out:
        if f["word"] not in seen:
            seen.add(f["word"]); uniq.append(f)
    return uniq


def sentence_at(words, i):
    """The sentence (of the spoken tokens) holding token i."""
    s = i
    while s > 0 and not words[s - 1].endswith((".", "!", "?")):
        s -= 1
    e = i
    while e < len(words) - 1 and not words[e].endswith((".", "!", "?")):
        e += 1
    return " ".join(words[s:e + 1])


def _load_whisper():
    import torch
    from transformers import pipeline
    return pipeline("automatic-speech-recognition", model=WHISPER_MODEL, torch_dtype=torch.float16, device="cuda")


def _whisper_text(asr, audio16k):
    return asr({"raw": audio16k, "sampling_rate": 16000}, chunk_length_s=30, batch_size=8,
               generate_kwargs={"language": "en", "task": "transcribe"})["text"]


def _align(model, processor, audio16k, words, emission=None):
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
    if emission is None:
        emission = _emission(model, processor, audio16k)
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
        # The engine's default share (0.70 of the card) + vocoder (0.10, can overrun) + preprocessing
        # (6 GiB) left no room for the hear-back listeners (Whisper large-v3 + wav2vec2, ~6 GiB in this
        # process): the first full batch with hear-back went CUDA OOM. 0.60 frees ~8 GiB.
        self.srv = subprocess.Popen(["sgl-omni", "serve", "--model-path", "/vol/drama-3", "--port", "8000",
                                     "--tts_engine.gpu_memory_fraction", ENGINE_MEM_FRACTION],
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
        self.whisper = _load_whisper()
        vol.commit()
        self.refs_written = False
        print(f"Drama 3 up in {time.time() - t0:.0f}s", flush=True)

    @modal.method()
    def voice(self, refs: list, items: list) -> list:
        """refs: [(wav bytes, transcript)]; items: [(key, text)]
        -> [(key, mp3|None, words|None, error|None, hear-back flags)]."""
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

        audio, flags = {}, {}
        t0 = time.time()
        pending, takes = jobs, 0
        with cf.ThreadPoolExecutor(CONCURRENCY) as ex:
            while pending:
                takes += 1
                retry = []
                for job, r in ex.map(one, pending):
                    key, ci, chunk = job
                    if r.status_code != 200:
                        audio[(key, ci)] = RuntimeError(f"{r.status_code} {r.text[:1000]}")
                        continue
                    pcm, sr = sf.read(io.BytesIO(r.content), dtype="float32")
                    if len(pcm) / sr >= MAX_CLIP_SECONDS:
                        audio[(key, ci)] = RuntimeError(
                            f"clip is {len(pcm) / sr:.0f} s — likely cut off at Drama 3's ~95 s cap "
                            f"({len(chunk)} chars); lower MAX_CHUNK_CHARS")
                        continue
                    pcm = pcm.mean(1) if pcm.ndim > 1 else pcm
                    a16 = librosa.resample(pcm, orig_sr=sr, target_sr=16000)
                    toks = spoken_text(chunk).split()
                    em = _emission(self.aligner, self.processor, a16)
                    ctc = heard_text(self.processor, em)
                    probs = check_clip(a16, toks, ctc)
                    misread = [] if probs else hearback(toks, _whisper_text(self.whisper, a16), ctc)
                    if misread and takes <= QA_RETRIES:
                        print(f"  hear-back take {takes} of {key}#{ci}: " + "; ".join(
                            f"{m['kind']} at {toks[m['word']]!r} (whisper: {m['whisper']!r})" for m in misread)
                              + " — re-voicing", flush=True)
                        retry.append(job)
                    elif probs and takes <= QA_RETRIES:
                        print(f"  QA take {takes} of {key}#{ci}: {'; '.join(probs)} — re-voicing", flush=True)
                        retry.append(job)
                    elif probs:
                        audio[(key, ci)] = RuntimeError(
                            f"failed clip QA on {takes} takes: {'; '.join(probs)} — {chunk[:60]!r}")
                    else:
                        audio[(key, ci)] = (pcm, sr, chunk, _align(self.aligner, self.processor, a16, toks, em))
                        if misread:               # still misheard after every retake: ship it, flag it
                            flags.setdefault(key, []).extend(dict(m, sentence=sentence_at(toks, m["word"]))
                                                             for m in misread)
                pending = retry
        print(f"voiced {len(jobs)} chunks in {time.time() - t0:.0f}s ({takes} round(s))", flush=True)
        out = []
        for key, text in items:
            parts = [audio[(k, ci)] for (k, ci, _) in jobs if k == key]
            err = next((p for p in parts if isinstance(p, Exception)), None)
            if err:
                out.append((key, None, None, str(err), [])); continue
            sr = parts[0][1]
            gap = np.zeros(int(GAP_SECONDS * sr), dtype=np.float32)
            pcm, words, offset = [], [], 0.0
            for i, (p, _, chunk, spans) in enumerate(parts):
                if i:
                    pcm.append(gap); offset += GAP_SECONDS
                toks = spoken_text(chunk).split()
                words += [{"word": w, "start": round(s + offset, 3), "end": round(e + offset, 3)}
                          for w, (s, e) in zip(toks, spans)]
                pcm.append(p); offset += len(p) / sr
            wav = io.BytesIO(); sf.write(wav, np.concatenate(pcm), sr, format="WAV", subtype="PCM_16")
            mp3 = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-ar", "44100", "-ac", "1",
                                  "-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3", "pipe:1"],
                                 input=wav.getvalue(), capture_output=True, check=True).stdout
            out.append((key, mp3, words, None, flags.get(key, [])))
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
        # spawn + short polls, not .remote(): a .remote() call left pending past ~2-3 min (the
        # container start alone is ~3 min) can die client-side with "Deadline exceeded".
        call = self._d.voice.spawn(self._refs, list(items))
        while True:
            try:
                res = call.get(timeout=10)
                break
            except TimeoutError:
                continue
        print(f"  [drama3] {len(res)} clip(s) in {time.time() - t0:.0f}s (incl. any container start)")
        out, errs = {}, {}
        self.flags = {}                    # {key: [hear-back flags]} — misreads that survived the retakes
        for key, mp3, words, err, flags in res:
            if flags:
                self.flags[key] = flags
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
