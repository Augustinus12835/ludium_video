#!/usr/bin/env python3
"""
TTS Audio Generation Script — ElevenLabs (default), Cartesia or HeyGen

Generates one MP3 per frame from the script's flat narration text, spoken verbatim
(a LEGACY math frame's natural_narration in math_verification.json is still honoured).
Word-level timestamps come back with each synthesis and are saved next to the audio
(audio/frame_N_timestamps.json) for downstream steps (animate, subtitles).

Providers (all return the same mp3 + word-timestamp shape, so nothing downstream branches):
  elevenlabs  default — ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID (override per run: --voice-id)
  cartesia    --profile cartesia  — CARTESIA_API_KEY + CARTESIA_VOICE_ID (CARTESIA_TTS_MODEL,
              default sonic-3.6)
  heygen      --profile heygen    — HEYGEN_API_KEY + HEYGEN_VOICE_ID
TTS_PROVIDER=cartesia|heygen|elevenlabs sets the default for every run instead.

There is no provider pronunciation dictionary: narration is voiced VERBATIM, and a token voices
misread is respelled in the narration itself from the alias library
(scripts/utils/tts_aliases.py), which the subtitles map back to the written form. Switching
providers therefore needs no per-provider setup.

audio/tts_meta.json records the provider/voice/model that voiced a video. A resumed video
finishes in that narrator, and fix_tts_sentence.py re-voices sentences in it.
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, Optional, List
from dotenv import load_dotenv
from mutagen.mp3 import MP3
from elevenlabs.client import ElevenLabs

# Add parent to path for utils
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.utils.script_parser import load_script, Frame as ScriptFrame

# Load environment variables from project .env file
# Try multiple locations: project root, parent directory
project_root = Path(__file__).parent.parent
env_paths = [
    project_root / '.env',
    Path.home() / '.env',
]

for env_path in env_paths:
    if env_path.exists():
        load_dotenv(env_path, override=True)
        break

# API Configuration
ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY')
ELEVENLABS_VOICE_ID = os.getenv('ELEVENLABS_VOICE_ID')
# eleven_multilingual_v2 by default; eleven_turbo_v2_5 costs half the credits and sounds close
# with a cloned voice — set ELEVENLABS_TTS_MODEL to switch.
ELEVENLABS_MODEL_ID = os.getenv('ELEVENLABS_TTS_MODEL', 'eleven_multilingual_v2')
OUTPUT_FORMAT = "mp3_44100_192"  # 44.1kHz, 192kbps — matches final AAC 192k target

# Cartesia (Sonic). A Pro Voice Clone ignores speed/volume (it learned them from its training
# audio), so no generation_config is sent. Pin a dated snapshot (e.g. sonic-3.6-2026-08-27) in
# CARTESIA_TTS_MODEL if later sentence fixes must use exactly the same model.
CARTESIA_API_KEY = os.getenv('CARTESIA_API_KEY')
CARTESIA_VOICE_ID = os.getenv('CARTESIA_VOICE_ID')
CARTESIA_MODEL_ID = os.getenv('CARTESIA_TTS_MODEL', 'sonic-3.6')
CARTESIA_VERSION = '2026-08-14'
CARTESIA_TTS_URL = 'https://api.cartesia.ai/tts/sse'
CARTESIA_SAMPLE_RATE = 44100
CARTESIA_MAX_CHARS = int(os.getenv('CARTESIA_MAX_CHARS', '3000'))   # longer frames: sentence chunks

# HeyGen Voice. Word timestamps come only from the STREAMING endpoint; 5,000 characters per
# request (longer frames are chunked at sentence ends). expressiveness_boost applies to instant
# voices only (0-1; API default 1.0, HeyGen Studio's 0.5).
HEYGEN_API_KEY = os.getenv('HEYGEN_API_KEY')
HEYGEN_VOICE_ID = os.getenv('HEYGEN_VOICE_ID')
HEYGEN_MODEL_ID = 'heygen-voice-1'
HEYGEN_TTS_URL = 'https://api.heygen.com/v3/models/audio/tts/stream'
HEYGEN_MAX_CHARS = 5000
HEYGEN_EXPRESSIVENESS = float(os.getenv('HEYGEN_EXPRESSIVENESS', '0.5'))

PROVIDERS = {
    'elevenlabs': ('ELEVENLABS_API_KEY', 'ELEVENLABS_VOICE_ID'),
    'cartesia': ('CARTESIA_API_KEY', 'CARTESIA_VOICE_ID'),
    'heygen': ('HEYGEN_API_KEY', 'HEYGEN_VOICE_ID'),
}
TTS_META = 'tts_meta.json'   # audio/tts_meta.json: which provider/voice/model voiced this video
PROVIDER = 'elevenlabs'
VOICE_ID = ELEVENLABS_VOICE_ID
MODEL_ID = ELEVENLABS_MODEL_ID
EXPLICIT_PROVIDER = False    # set by --profile / --voice-id: never auto-adopt a video's recorded voice


def activate_provider(name: str) -> None:
    """Route every synthesis in this process through `name` with its .env voice/model."""
    global PROVIDER, VOICE_ID, MODEL_ID
    if name not in PROVIDERS:
        raise SystemExit(f"✗ Unknown TTS provider {name!r} ({' | '.join(PROVIDERS)})")
    PROVIDER = name
    VOICE_ID, MODEL_ID = {
        'elevenlabs': (ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL_ID),
        'cartesia': (CARTESIA_VOICE_ID, CARTESIA_MODEL_ID),
        'heygen': (HEYGEN_VOICE_ID, HEYGEN_MODEL_ID),
    }[name]


def check_provider_config() -> None:
    """Exit with a clear message if the active provider's key or voice is missing."""
    key_var, voice_var = PROVIDERS[PROVIDER]
    if not os.getenv(key_var):
        raise SystemExit(f"✗ {PROVIDER} TTS needs {key_var} in .env")
    if not VOICE_ID:
        raise SystemExit(f"✗ {PROVIDER} TTS needs {voice_var} in .env (the voice to narrate in)")


_env_provider = os.getenv('TTS_PROVIDER', '').strip().lower()
if _env_provider:
    activate_provider(_env_provider)


def current_tts_meta() -> Dict:
    meta = {'provider': PROVIDER, 'voice_id': VOICE_ID, 'model_id': MODEL_ID}
    if PROVIDER == 'heygen':
        meta['expressiveness'] = HEYGEN_EXPRESSIVENESS
    return meta


def read_tts_meta(audio_dir) -> Optional[Dict]:
    p = Path(audio_dir) / TTS_META
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def apply_tts_meta(meta: Dict) -> None:
    """Adopt a video's recorded provider/voice/model (re-voice in the same narrator)."""
    global VOICE_ID, MODEL_ID, HEYGEN_EXPRESSIVENESS
    activate_provider(meta.get('provider', 'elevenlabs'))
    VOICE_ID = meta.get('voice_id') or VOICE_ID
    MODEL_ID = meta.get('model_id') or MODEL_ID
    if PROVIDER == 'heygen':
        HEYGEN_EXPRESSIVENESS = float(meta.get('expressiveness', HEYGEN_EXPRESSIVENESS))


_client = None


def _elevenlabs_client():
    # Generous read timeout (default is too short for long single-frame narration —
    # e.g. worked-example frames of ~6 min / ~6000 chars time out on read).
    global _client
    if _client is None:
        _client = ElevenLabs(api_key=ELEVENLABS_API_KEY, timeout=600)
    return _client


class TTSFrame:
    """Represents a single frame with narration for TTS generation"""
    def __init__(self, number: int, start_seconds: float, end_seconds: float, word_count: int, text: str):
        self.number = number
        self.start_seconds = start_seconds
        self.end_seconds = end_seconds
        self.word_count = word_count
        self.text = text
        self.duration = end_seconds - start_seconds

    def __repr__(self):
        return f"Frame {self.number}: {self.duration:.0f}s, {self.word_count} words"


def parse_script(script_path: str) -> List[TTSFrame]:
    """
    Parse script file (JSON or MD) to extract frame information.

    Uses the shared script_parser utility for consistent parsing.
    """
    script_dir = Path(script_path).parent
    script_data = load_script(script_dir)

    frames = []
    for frame in script_data.frames:
        tts_frame = TTSFrame(
            number=frame.number,
            start_seconds=frame.start_seconds,
            end_seconds=frame.end_seconds,
            word_count=frame.word_count,
            text=frame.narration  # Already clean - no visual annotations in JSON
        )
        frames.append(tts_frame)

    return frames


def load_math_verification(script_dir: str) -> Optional[Dict]:
    """Load math_verification.json if it exists."""
    verification_path = os.path.join(script_dir, "math_verification.json")
    if not os.path.exists(verification_path):
        return None
    try:
        with open(verification_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def get_natural_narration(frame_num: int, original_text: str, math_data: Optional[Dict]) -> str:
    """
    Get the spoken narration text for a frame.

    The norm is the script.json narration (`original_text`) — since 2026-08-30
    verify_math no longer writes a `natural_narration` TTS rewrite, so every
    frame class is voiced verbatim from the script. A verified
    `natural_narration` is still honoured when present so LEGACY videos (whose
    script narration predates the TTS-safety rules) keep their spoken text —
    fix_tts_sentence.py relies on this to match the existing audio.
    """
    if math_data:
        frame_key = str(frame_num)
        frame_data = math_data.get("frames", {}).get(frame_key)
        if frame_data:
            natural = frame_data.get("natural_narration")
            if natural and frame_data.get("verification_status") in ("correct", "corrected"):
                return natural

    return original_text


def clean_narration_text(text: str) -> str:
    """Clean narration text - remove any markdown formatting."""
    import re
    # Remove markdown bold/italic
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)

    # Remove markdown links [text](url)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)

    # Remove excess whitespace
    text = ' '.join(text.split())

    return text


def words_from_alignment(characters: List[str], starts: List[float], ends: List[float]) -> List[Dict]:
    """
    Group ElevenLabs character-level alignment into word-level timestamps.

    Returns list of {'word': str, 'start': float, 'end': float}.
    """
    words = []
    current = ""
    w_start = None
    w_end = None

    for ch, s, e in zip(characters, starts, ends):
        if ch.isspace():
            if current:
                words.append({'word': current, 'start': round(w_start, 3), 'end': round(w_end, 3)})
                current = ""
                w_start = None
        else:
            if not current:
                w_start = s
            current += ch
            w_end = e

    if current:
        words.append({'word': current, 'start': round(w_start, 3), 'end': round(w_end, 3)})

    return words


def call_elevenlabs_api(text, voice_id=None, speed=None, language_code=None):
    """
    Call ElevenLabs API to generate audio from text, with word-level timestamps.

    Word timestamps come back with the synthesis itself (no extra API call),
    letting downstream steps (animate, subtitles) skip Scribe re-transcription.

    voice_id overrides the default narration voice (an ElevenLabs voice); speed, when
    given, is passed as a voice setting; language_code, when given, enforces a language
    on the model (disables per-request language auto-detection).

    With the Cartesia or HeyGen provider active (and no voice_id), the call is routed
    there — same return shape, so callers never branch on the provider.

    Returns:
        (bytes, list): (audio file content, word timestamps [{'word','start','end'}, ...]
                        — empty list if alignment was unavailable)
    """
    if PROVIDER == 'cartesia' and not voice_id:
        return call_cartesia_api(text)
    if PROVIDER == 'heygen' and not voice_id:
        return call_heygen_api(text)
    if not ELEVENLABS_API_KEY:
        raise ValueError("ELEVENLABS_API_KEY not found in environment variables")

    try:
        import base64

        kwargs = dict(
            text=text,
            voice_id=voice_id or VOICE_ID,
            model_id=MODEL_ID if PROVIDER == 'elevenlabs' else ELEVENLABS_MODEL_ID,
            output_format=OUTPUT_FORMAT,
            # Per-request read timeout — long single-frame narration
            # (~6 min / ~6000 chars worked examples) exceeds the SDK default.
            request_options={"timeout_in_seconds": 900},
        )
        if speed:
            from elevenlabs import VoiceSettings
            kwargs['voice_settings'] = VoiceSettings(speed=speed)
        if language_code:
            kwargs['language_code'] = language_code

        response = _elevenlabs_client().text_to_speech.convert_with_timestamps(**kwargs)

        audio_bytes = base64.b64decode(response.audio_base_64)

        words = []
        alignment = response.alignment  # character timing of the INPUT text (not normalized)
        if alignment and alignment.characters:
            words = words_from_alignment(
                alignment.characters,
                alignment.character_start_times_seconds,
                alignment.character_end_times_seconds,
            )

        return audio_bytes, words

    except Exception as e:
        raise Exception(f"ElevenLabs API error: {str(e)}")


def _sentence_chunks(text, max_chars, provider):
    """Split text over a per-request limit at sentence ends (whitespace kept attached)."""
    import re as _re
    if len(text) <= max_chars:
        return [text]
    limit = max_chars - 500
    chunks, cur = [], ''
    for sent in _re.split(r'(?<=[.!?])(?=\s)', text):
        if cur and len(cur) + len(sent) > limit:
            chunks.append(cur)
            cur = sent
        else:
            cur += sent
    chunks.append(cur)
    if any(len(c) > max_chars for c in chunks):
        raise Exception(f"{provider}: a single sentence exceeds {max_chars} characters")
    return chunks


def _pcm16_to_mp3(pcm: bytes, rate: int) -> bytes:
    """Mono s16le PCM -> 44.1 kHz / 192k mp3 (the format every provider path returns)."""
    import subprocess
    return subprocess.run(['ffmpeg', '-v', 'error', '-f', 's16le', '-ar', str(rate), '-ac', '1',
                           '-i', 'pipe:0', '-ar', '44100', '-c:a', 'libmp3lame', '-b:a', '192k',
                           '-f', 'mp3', 'pipe:1'],
                          input=pcm, capture_output=True, check=True).stdout


def _post_with_retry(url, body, headers, name):
    """POST a streaming request, retrying 429 / 5xx with backoff."""
    import requests
    for attempt in range(6):
        r = requests.post(url, json=body, headers=headers, stream=True, timeout=900)
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get('Retry-After') or 5 * (attempt + 1))
            print(f"  [{name}] HTTP {r.status_code}, retrying in {wait:.0f}s")
            time.sleep(wait)
            continue
        if r.status_code != 200:
            raise Exception(f"{name} API error: HTTP {r.status_code}: {r.text[:300]}")
        return r
    raise Exception(f"{name} API error: still rate-limited / failing after 6 attempts")


def _cartesia_stream(text):
    """One Cartesia SSE request -> (pcm s16le mono, words).

    use_normalized_timestamps=false makes the words the transcript's own whitespace tokens,
    punctuation included — the shape words_from_alignment yields."""
    import base64
    body = {"model_id": MODEL_ID, "transcript": text, "voice": {"mode": "id", "id": VOICE_ID},
            "language": "en", "add_timestamps": True, "use_normalized_timestamps": False,
            "output_format": {"container": "raw", "encoding": "pcm_s16le",
                              "sample_rate": CARTESIA_SAMPLE_RATE}}
    headers = {"Cartesia-Version": CARTESIA_VERSION, "Authorization": f"Bearer {CARTESIA_API_KEY}"}
    r = _post_with_retry(CARTESIA_TTS_URL, body, headers, 'Cartesia')
    pcm, toks, starts, ends = b'', [], [], []
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith('data:'):
            continue
        ev = json.loads(line[5:])
        if ev.get('type') == 'chunk':
            pcm += base64.b64decode(ev['data'])
        elif ev.get('type') == 'timestamps':
            wt = ev.get('word_timestamps') or {}
            toks += wt.get('words', [])
            starts += wt.get('start', [])
            ends += wt.get('end', [])
        elif ev.get('type') == 'error':
            raise Exception(f"Cartesia stream error: {ev.get('title')}: {ev.get('message')}")
        elif ev.get('type') == 'done':
            break
    if not pcm:
        raise Exception("Cartesia stream returned no audio")
    src = text.split()
    if len(toks) != len(src):
        raise Exception(f"Cartesia returned {len(toks)} word timestamps for {len(src)} narration words")
    # Keep the narration's own spelling — fix_tts_sentence matches sentences on the source text.
    words = [{'word': w, 'start': round(a, 3), 'end': round(b, 3)}
             for w, a, b in zip(src, starts, ends)]
    return pcm, words


def call_cartesia_api(text):
    """Cartesia synthesis with word timestamps -> (mp3 bytes, words)."""
    if not CARTESIA_API_KEY:
        raise ValueError("CARTESIA_API_KEY not found in environment variables")
    pcm, words = b'', []
    for chunk in _sentence_chunks(text, CARTESIA_MAX_CHARS, 'Cartesia'):
        c_pcm, c_words = _cartesia_stream(chunk.strip())
        offset = len(pcm) / 2 / CARTESIA_SAMPLE_RATE
        words += [{'word': w['word'], 'start': round(w['start'] + offset, 3),
                   'end': round(w['end'] + offset, 3)} for w in c_words]
        pcm += c_pcm
    return _pcm16_to_mp3(pcm, CARTESIA_SAMPLE_RATE), words


def _heygen_stream(text):
    """One HeyGen streaming request -> (pcm s16le mono, sample rate, words).

    Server-sent events carry base64 WAV parts and `character_alignment` events whose
    `original_characters` match the input text exactly."""
    import base64
    import io
    import wave
    body = {"model": MODEL_ID, "voice_id": VOICE_ID, "text": text, "language": "en",
            "with_timestamps": True, "expressiveness_boost": HEYGEN_EXPRESSIVENESS}
    headers = {"X-Api-Key": HEYGEN_API_KEY, "Content-Type": "application/json"}
    r = _post_with_retry(HEYGEN_TTS_URL, body, headers, 'HeyGen')
    pcm, rate, chars = b'', None, []
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith('data:'):
            continue
        payload = line[5:].strip()
        if payload == '[DONE]':
            break
        ev = json.loads(payload)
        if ev.get('type') == 'audio':
            w = wave.open(io.BytesIO(base64.b64decode(ev['audio'])))
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise Exception("HeyGen returned non-mono/16-bit audio")
            rate = w.getframerate()
            pcm += w.readframes(w.getnframes())
        elif ev.get('type') == 'character_alignment':
            chars += ev['original_characters']
        elif ev.get('type') == 'error' or 'error' in ev:
            raise Exception(f"HeyGen stream error: {ev}")
    if rate is None:
        raise Exception("HeyGen stream returned no audio")
    if ''.join(c['text'] for c in chars) != text:
        raise Exception("HeyGen alignment text does not match the narration")
    words = words_from_alignment([c['text'] for c in chars],
                                 [c['start_time'] for c in chars],
                                 [c['end_time'] for c in chars])
    return pcm, rate, words


def call_heygen_api(text):
    """HeyGen Voice synthesis with word timestamps -> (mp3 bytes, words)."""
    if not HEYGEN_API_KEY:
        raise ValueError("HEYGEN_API_KEY not found in environment variables")
    pcm, rate, words = b'', None, []
    for chunk in _sentence_chunks(text, HEYGEN_MAX_CHARS, 'HeyGen'):
        c_pcm, rate, c_words = _heygen_stream(chunk.strip())
        offset = len(pcm) / 2 / rate
        words += [{'word': w['word'], 'start': round(w['start'] + offset, 3),
                   'end': round(w['end'] + offset, 3)} for w in c_words]
        pcm += c_pcm
    return _pcm16_to_mp3(pcm, rate), words


def save_timestamps(output_dir: str, frame_number: int, text: str, words: List[Dict]) -> None:
    """Persist word timestamps next to the frame audio for downstream consumers."""
    path = os.path.join(output_dir, f"frame_{frame_number}_timestamps.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({
            'source': PROVIDER,
            'text': text,
            'words': words,
        }, f, indent=2, ensure_ascii=False)


def get_audio_duration(file_path):
    """Get duration of MP3 file in seconds"""
    try:
        audio = MP3(file_path)
        return audio.info.length
    except Exception as e:
        print(f"  Warning: Could not read audio duration: {e}")
        return None


def generate_audio_for_frames(frames: List[TTSFrame], output_dir: str, math_data: Optional[Dict] = None):
    """
    Generate audio files for all frames

    Args:
        frames: List of Frame objects
        output_dir: Directory to save audio files
        math_data: Optional math verification data for natural narration

    Returns:
        list: Report entries for each frame
    """
    results = []

    # Create audio directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # One video = one narrator. A video already part-voiced finishes in its recorded voice
    # (audio/tts_meta.json; no meta = voiced by ElevenLabs before metas existed) unless a
    # provider/voice was requested explicitly — then a mismatch is refused, never mixed.
    meta = read_tts_meta(output_dir)
    have_audio = any(f.startswith('frame_') and f.endswith('.mp3') for f in os.listdir(output_dir))
    if meta and not have_audio:
        meta = None          # a meta with no audio yet pins nothing
    if have_audio and not EXPLICIT_PROVIDER:
        if meta and (meta.get('provider', 'elevenlabs'), meta.get('voice_id'),
                     meta.get('model_id')) != (PROVIDER, VOICE_ID, MODEL_ID):
            apply_tts_meta(meta)
            print(f"  [tts_meta] finishing in the recorded voice ({PROVIDER} {VOICE_ID} / {MODEL_ID})")
        elif not meta and PROVIDER != 'elevenlabs':
            activate_provider('elevenlabs')
            print(f"  [tts_meta] audio without {TTS_META} — finishing in ElevenLabs")
    cur = current_tts_meta()
    if meta and (meta.get('provider', 'elevenlabs'), meta.get('voice_id'),
                 meta.get('model_id')) != (cur['provider'], cur['voice_id'], cur['model_id']):
        raise SystemExit(f"✗ {output_dir} was voiced with {meta.get('provider', 'elevenlabs')} "
                         f"{meta.get('voice_id')} / {meta.get('model_id')}; this run is "
                         f"{cur['provider']} {cur['voice_id']} / {cur['model_id']}. "
                         f"Move audio/ aside to re-voice the whole video.")
    if not meta and have_audio and PROVIDER != 'elevenlabs':
        raise SystemExit(f"✗ {output_dir} already holds audio from before {TTS_META} (ElevenLabs). "
                         f"Move audio/ aside to re-voice the whole video in {PROVIDER}.")
    if not meta and not have_audio:
        Path(output_dir, TTS_META).write_text(json.dumps(cur, indent=2) + "\n")
    check_provider_config()

    for frame in frames:
        frame_filename = f"frame_{frame.number}.mp3"
        output_path = os.path.join(output_dir, frame_filename)

        # Skip if audio already exists
        if os.path.exists(output_path):
            actual_duration = get_audio_duration(output_path)
            print(f"\n  Frame {frame.number}: ✓ {frame_filename} exists ({actual_duration:.1f}s), skipping")
            results.append({
                'frame': frame.number,
                'filename': frame_filename,
                'status': 'skipped',
                'actual_duration': actual_duration,
                'target_duration': frame.duration,
            })
            continue

        # Get the best narration (prefer natural_narration from verification)
        narration_text = get_natural_narration(frame.number, frame.text, math_data)

        # Update frame text if we got natural narration
        if narration_text != frame.text:
            print(f"\n  Frame {frame.number}: Using natural narration from math_verification.json")
            frame.text = narration_text

        print(f"\nProcessing Frame {frame.number}...")
        print(f"  Target duration: {frame.duration:.0f}s")
        print(f"  Word count: {frame.word_count}")
        # Clean the text before display and TTS
        frame.text = clean_narration_text(frame.text)
        print(f"  Text preview: {frame.text[:60]}...")

        try:
            # Generate audio (word timestamps come back with the same call)
            audio_data, word_timestamps = call_elevenlabs_api(frame.text)

            # Save to file
            with open(output_path, 'wb') as f:
                f.write(audio_data)

            if word_timestamps:
                save_timestamps(output_dir, frame.number, frame.text, word_timestamps)
                print(f"  ✓ Saved to {frame_filename} (+{len(word_timestamps)} word timestamps)")
            else:
                print(f"  ✓ Saved to {frame_filename} (no alignment returned)")

            # Verify duration
            actual_duration = get_audio_duration(output_path)

            if actual_duration:
                difference = actual_duration - frame.duration

                result = {
                    'frame': frame.number,
                    'filename': frame_filename,
                    'target': frame.duration,
                    'actual': actual_duration,
                    'difference': difference,
                    'status': 'success'
                }

                # Check if timing is acceptable (within 2 seconds)
                if abs(difference) > 2:
                    result['warning'] = True
                else:
                    result['warning'] = False

                results.append(result)
                print(f"  Duration: {actual_duration:.1f}s (diff: {difference:+.1f}s)")
            else:
                results.append({
                    'frame': frame.number,
                    'filename': frame_filename,
                    'target': frame.duration,
                    'status': 'success',
                    'warning': False,
                    'note': 'Could not verify duration'
                })

        except Exception as e:
            print(f"  ✗ Failed: {str(e)}")
            results.append({
                'frame': frame.number,
                'filename': frame_filename,
                'target': frame.duration,
                'status': 'failed',
                'error': str(e)
            })

        # Small delay between frames to be respectful to the API
        time.sleep(0.5)

    return results


def print_report(results):
    """Print final generation report"""
    print("\n" + "=" * 60)
    print(f"TTS Generation Complete ({PROVIDER})")
    print("=" * 60)
    print()

    successful = 0
    failed = 0
    needs_adjustment = 0
    total_actual_duration = 0
    total_target_duration = 0

    for result in results:
        if result['status'] == 'success':
            successful += 1

            if 'actual' in result:
                total_actual_duration += result['actual']
                total_target_duration += result['target']

                if result.get('warning', False):
                    needs_adjustment += 1
                    print(f"⚠ {result['filename']}: {result['actual']:.1f}s (target: {result['target']}s) - "
                          f"{abs(result['difference']):.1f}s {'short' if result['difference'] < 0 else 'long'}")
                else:
                    print(f"✓ {result['filename']}: {result['actual']:.1f}s (target: {result['target']}s) - OK")
            else:
                print(f"✓ {result['filename']}: Saved ({result.get('note', '')})")
        elif result['status'] == 'skipped':
            successful += 1
        else:
            failed += 1
            print(f"✗ {result['filename']}: FAILED - {result.get('error', 'unknown')}")

    print()
    print("Summary:")
    print(f"- Total frames: {len(results)}")
    print(f"- Successful: {successful}")
    print(f"- Need adjustment: {needs_adjustment}")
    print(f"- Failed: {failed}")

    if total_actual_duration > 0:
        print(f"- Total audio duration: {format_time(total_actual_duration)} "
              f"(target: {format_time(total_target_duration)})")

    print()
    if failed == 0 and needs_adjustment == 0:
        print("✓ All frames generated successfully!")
        print("Next step: Run video compilation")
    elif failed == 0:
        print(f"⚠ Review {needs_adjustment} frame(s) with timing issues")
    else:
        print(f"✗ {failed} frame(s) failed. Review errors above.")


def format_time(seconds):
    """Format seconds as MM:SS"""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def main():
    """Main execution function"""
    if len(sys.argv) < 2:
        print("Usage: python generate_tts_elevenlabs.py <path_to_script> "
              "[--profile elevenlabs|cartesia|heygen] [--voice-id VOICE_ID]")
        print()
        print("Example:")
        print("  python generate_tts_elevenlabs.py Week-1/Video-1/script.json")
        print()
        print("Options:")
        print("  --profile    TTS provider (default: elevenlabs, or TTS_PROVIDER)")
        print("  --voice-id   override the active provider's voice from .env")
        sys.exit(1)

    global VOICE_ID, EXPLICIT_PROVIDER

    script_path = sys.argv[1]

    if "--profile" in sys.argv:
        idx = sys.argv.index("--profile")
        activate_provider(sys.argv[idx + 1] if idx + 1 < len(sys.argv) else '')
        EXPLICIT_PROVIDER = True

    # Check for --voice-id override (a voice of the active provider)
    if "--voice-id" in sys.argv:
        idx = sys.argv.index("--voice-id")
        if idx + 1 < len(sys.argv):
            VOICE_ID = sys.argv[idx + 1]
            EXPLICIT_PROVIDER = True

    # Determine output directory (same directory as script, in 'audio' subfolder)
    script_dir = os.path.dirname(script_path)
    audio_dir = os.path.join(script_dir, 'audio')

    print("=" * 60)
    print(f"TTS Audio Generator ({PROVIDER})")
    print("=" * 60)
    print(f"Script: {script_path}")
    print(f"Output: {audio_dir}")
    print(f"Voice ID: {VOICE_ID}")
    print(f"Model: {MODEL_ID}")
    print("=" * 60)

    # Verify API key and voice ID (generate_audio_for_frames re-checks after adopting a
    # part-voiced video's recorded provider)
    if EXPLICIT_PROVIDER:
        check_provider_config()

    try:
        # Parse script
        print("\nParsing script...")
        frames = parse_script(script_path)
        print(f"✓ Found {len(frames)} frames")

        # Load math verification if available
        math_data = load_math_verification(script_dir)
        if math_data:
            verified_count = len(math_data.get("frames", {}))
            print(f"✓ Math verification available ({verified_count} frames)")

        # Generate audio
        results = generate_audio_for_frames(frames, audio_dir, math_data)

        # Print report
        print_report(results)

    except FileNotFoundError as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
