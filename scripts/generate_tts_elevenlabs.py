#!/usr/bin/env python3
"""
TTS Audio Generation Script using ElevenLabs API

Generates one MP3 per frame from the script's flat narration text, preferring the
TTS-safe natural_narration from math_verification.json for verified math frames.
Word-level timestamps come back with each synthesis and are saved next to the audio
(audio/frame_N_timestamps.json) for downstream steps (animate, subtitles).

Set ELEVENLABS_VOICE_ID in .env (override per run with --voice-id).
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
VOICE_ID = os.getenv('ELEVENLABS_VOICE_ID')
MODEL_ID = "eleven_multilingual_v2"  # Multilingual model
OUTPUT_FORMAT = "mp3_44100_192"  # 44.1kHz, 192kbps — matches final AAC 192k target

# Optional ElevenLabs pronunciation dictionary, applied to every synthesis so
# math/Greek tokens spoken as words come out right ("rho"->roe, "pi"->pie, hyperbolic
# functions, etc. — see docs/elevenlabs_pronunciation_dict.pls). A web/dashboard dictionary
# is NOT auto-applied to the API; it must be attached per request via a locator. We key by
# dictionary ID only (no version) so the LATEST dictionary version is always used — editing
# the dict's rules needs no code/env change. Unset -> disabled. The dashboard returns the
# alignment against the ORIGINAL input text, so on-screen subtitles keep the spelled-out word
# (e.g. "rho") while only the spoken audio changes.
PRONUNCIATION_DICT_ID = os.getenv('ELEVENLABS_PRONUNCIATION_DICT_ID', '').strip()


def _pronunciation_locators():
    """Build the pronunciation-dictionary locator list (latest version), or None if unset."""
    if not PRONUNCIATION_DICT_ID:
        return None
    try:
        from elevenlabs import PronunciationDictionaryVersionLocator as _Loc
        return [_Loc(pronunciation_dictionary_id=PRONUNCIATION_DICT_ID)]
    except Exception:
        return [{"pronunciation_dictionary_id": PRONUNCIATION_DICT_ID}]


# Initialize ElevenLabs client
# Generous read timeout (default is too short for long single-frame narration —
# e.g. worked-example frames of ~6 min / ~6000 chars time out on read).
client = ElevenLabs(api_key=ELEVENLABS_API_KEY, timeout=600)

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
    Get the best narration text for a frame.

    Prefers natural_narration from math_verification.json if available,
    otherwise falls back to the original text.
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

    voice_id overrides the default narration voice; speed, when given, is passed
    as a voice setting; language_code, when given, enforces a language on the
    model (disables per-request language auto-detection).

    Returns:
        (bytes, list): (audio file content, word timestamps [{'word','start','end'}, ...]
                        — empty list if alignment was unavailable)
    """
    if not ELEVENLABS_API_KEY:
        raise ValueError("ELEVENLABS_API_KEY not found in environment variables")

    try:
        import base64

        kwargs = dict(
            text=text,
            voice_id=voice_id or VOICE_ID,
            model_id=MODEL_ID,
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
        locators = _pronunciation_locators()
        if locators:
            kwargs['pronunciation_dictionary_locators'] = locators

        response = client.text_to_speech.convert_with_timestamps(**kwargs)

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


def save_timestamps(output_dir: str, frame_number: int, text: str, words: List[Dict]) -> None:
    """Persist word timestamps next to the frame audio for downstream consumers."""
    path = os.path.join(output_dir, f"frame_{frame_number}_timestamps.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({
            'source': 'elevenlabs',
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
    print("TTS Generation Complete (ElevenLabs)")
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
        print("Usage: python generate_tts_elevenlabs.py <path_to_script> [--voice-id VOICE_ID]")
        print()
        print("Example:")
        print("  python generate_tts_elevenlabs.py Week-1/Video-1/script.json")
        print()
        print("Options:")
        print("  --voice-id   override the ELEVENLABS_VOICE_ID from .env")
        sys.exit(1)

    global VOICE_ID

    script_path = sys.argv[1]

    # Check for --voice-id override
    if "--voice-id" in sys.argv:
        idx = sys.argv.index("--voice-id")
        if idx + 1 < len(sys.argv):
            VOICE_ID = sys.argv[idx + 1]

    # Determine output directory (same directory as script, in 'audio' subfolder)
    script_dir = os.path.dirname(script_path)
    audio_dir = os.path.join(script_dir, 'audio')

    print("=" * 60)
    print("ElevenLabs TTS Audio Generator")
    print("=" * 60)
    print(f"Script: {script_path}")
    print(f"Output: {audio_dir}")
    print(f"Voice ID: {VOICE_ID}")
    print(f"Model: {MODEL_ID}")
    print("=" * 60)

    # Verify API key and voice ID
    if not ELEVENLABS_API_KEY:
        print("\n✗ Error: ELEVENLABS_API_KEY not found in environment")
        print("  Please check your .env file")
        sys.exit(1)

    if not VOICE_ID:
        print("\n✗ Error: ELEVENLABS_VOICE_ID not found in environment")
        print("  Please add ELEVENLABS_VOICE_ID=your_voice_id to your .env file")
        sys.exit(1)

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
