"""
ElevenLabs Scribe speech-to-text — the project's transcription engine.

Replaced AssemblyAI on 2026-08-15: one vendor for both TTS and STT, billed
against the existing ElevenLabs Scale-plan credits ($0.22/audio-hour vs
AssemblyAI's ~$0.37, no separate bill). Uses the same ELEVENLABS_API_KEY as
generate_tts_elevenlabs.py.

transcribe() accepts audio OR video files directly (MP4, MKV, WebM, MP3,
FLAC, WAV, …) up to 3 GB / 10 h and returns word timestamps in SECONDS
(AssemblyAI returned milliseconds — call sites that keep ms-based file
formats convert explicitly). Scribe's word list interleaves `word`,
`spacing`, and `audio_event` entries; only `word` entries are returned here.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Batch Scribe model (scribe_v2 verified available on this plan 2026-08-15);
# override via ELEVENLABS_STT_MODEL.
DEFAULT_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2")

# Long files upload slowly and transcribe server-side in one blocking call —
# allow up to an hour before the HTTP client gives up.
REQUEST_TIMEOUT_SECONDS = 3600


def _client():
    from elevenlabs.client import ElevenLabs

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY not found in .env file")
    return ElevenLabs(api_key=api_key)


def transcribe(file_path: str,
               language_code: Optional[str] = "en",
               model_id: Optional[str] = None) -> Dict:
    """
    Transcribe an audio/video file with ElevenLabs Scribe.

    Returns:
        {
            'text':  full punctuated transcript text,
            'words': [{'word': str, 'start': float, 'end': float}, ...]
                     (timestamps in seconds),
            'language_code': detected/requested language,
        }
    """
    client = _client()
    path = Path(file_path)
    file_mb = path.stat().st_size / (1024 * 1024)
    print(f"  Transcribing {path.name} ({file_mb:.1f} MB) with ElevenLabs Scribe...",
          flush=True)

    with open(path, "rb") as f:
        resp = client.speech_to_text.convert(
            model_id=model_id or DEFAULT_STT_MODEL,
            file=f,
            language_code=language_code,
            tag_audio_events=False,   # keep (laughter) etc. out of the text
            diarize=False,
            request_options={"timeout_in_seconds": REQUEST_TIMEOUT_SECONDS},
        )

    words = []
    for w in resp.words or []:
        if w.type != "word":
            continue
        words.append({
            "word": (w.text or "").strip(),
            "start": float(w.start) if w.start is not None else 0.0,
            "end": float(w.end) if w.end is not None else 0.0,
        })

    return {
        "text": resp.text or "",
        "words": words,
        "language_code": getattr(resp, "language_code", None) or language_code,
    }


def transcribe_to_srt(file_path: str,
                      max_characters_per_line: int = 80,
                      language_code: Optional[str] = "en",
                      model_id: Optional[str] = None) -> str:
    """
    Transcribe a file and return Scribe's own SRT export (no script alignment).
    """
    import base64

    client = _client()
    with open(file_path, "rb") as f:
        resp = client.speech_to_text.convert(
            model_id=model_id or DEFAULT_STT_MODEL,
            file=f,
            language_code=language_code,
            tag_audio_events=False,
            diarize=True,   # the API requires diarization for additional_formats
            additional_formats=[{
                "format": "srt",
                "max_characters_per_line": max_characters_per_line,
                "include_speakers": False,
                "include_timestamps": True,
            }],
            request_options={"timeout_in_seconds": REQUEST_TIMEOUT_SECONDS},
        )

    for fmt in resp.additional_formats or []:
        if fmt.requested_format == "srt":
            content = fmt.content
            if fmt.is_base_64_encoded:
                content = base64.b64decode(content).decode("utf-8")
            return content
    raise RuntimeError("Scribe response contained no SRT export")


def words_to_sentences(words: List[Dict]) -> List[Dict]:
    """
    Group a transcribe() word list into sentences on terminal punctuation.

    Returns [{'start': float, 'end': float, 'text': str}, ...] in seconds.
    """
    sentences = []
    current: List[Dict] = []
    for w in words:
        current.append(w)
        if w["word"].rstrip('"”’)').endswith((".", "?", "!")):
            sentences.append({
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "text": " ".join(x["word"] for x in current),
            })
            current = []
    if current:
        sentences.append({
            "start": current[0]["start"],
            "end": current[-1]["end"],
            "text": " ".join(x["word"] for x in current),
        })
    return sentences
