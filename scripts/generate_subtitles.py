#!/usr/bin/env python3
"""
Subtitle Generation Script for Ludium Video
Generates SRT subtitles from word-level timestamps.

Primary path: per-frame ElevenLabs word timestamps saved at TTS time
(audio/frame_N_timestamps.json), shifted onto the final-video timeline by
cumulative frame audio durations — exact timing, zero transcription calls.
compile_video.py clamps each segment to its audio duration, so the final
timeline IS the concatenation of frame audio durations (verified against
final_video.mp4 duration as a guard).

Fallback path (legacy videos without timestamp files): transcribe
final_video.mp4 with ElevenLabs Scribe, then align script text (ground truth)
to those timestamps using sequence matching.

Spoken text = script.json narration for every frame class; a LEGACY (pre-2026-08-30)
math frame's natural_narration in math_verification.json is still honoured so the
subtitles match the audio that was actually voiced.

Usage:
    python generate_subtitles.py pipeline/LECTURE
    python generate_subtitles.py pipeline/LECTURE --video 3
    python generate_subtitles.py pipeline/LECTURE --force

Output:
    - Video-N/subtitles.srt (separate subtitle file for each video)
"""

import os
import sys
import json
import argparse
import difflib
from typing import List, Dict, Tuple, Optional
from pathlib import Path
from dotenv import load_dotenv

# Add parent to path for utils
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.utils.script_parser import load_script

# Load environment variables
project_root = Path(__file__).parent.parent
load_dotenv(project_root / '.env')

# Max tolerated disagreement between the summed frame durations and the compiled
# video's audio stream before the stored-timestamp path is abandoned for the
# Scribe fallback. Long videos accumulate ~19ms/frame of 30fps quantization, so
# a 30-frame video lands near 0.6s with a perfectly good word timeline; raise
# this (e.g. SUBTITLE_DRIFT_TOLERANCE=1.0) to keep the stored ElevenLabs
# timestamps instead of paying for a re-transcription.
# Drift is cumulative, so the mid-video error is only half the reported figure.
DRIFT_TOLERANCE = float(os.getenv('SUBTITLE_DRIFT_TOLERANCE', '0.5'))

# Subtitles appear slightly before speech so viewers can read along.
# 300ms is a common broadcast standard (EBU-TT, Netflix guidelines).
SUBTITLE_LEAD_TIME = 0.3  # seconds


def get_full_narration(video_folder: str) -> str:
    """
    Get the complete narration text for a video, using natural_narration
    from math_verification.json where available.

    Returns the full narration as a single string (space-separated frames).
    """
    script_data = load_script(Path(video_folder))

    # Load math verification for natural narration (math videos)
    math_data = None
    verification_path = os.path.join(video_folder, "math_verification.json")
    if os.path.exists(verification_path):
        try:
            with open(verification_path, "r", encoding="utf-8") as f:
                math_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    narration_parts = []
    for frame in script_data.frames:
        text = frame.narration

        # Prefer natural_narration from math_verification if available
        if math_data:
            frame_key = str(frame.number)
            frame_info = math_data.get("frames", {}).get(frame_key)
            if frame_info:
                natural = frame_info.get("natural_narration")
                if natural and frame_info.get("verification_status") in ("correct", "corrected"):
                    text = natural

        narration_parts.append(text)

    return " ".join(narration_parts)


def get_media_duration_ffprobe(media_path: str) -> float:
    """Get media duration in seconds using ffprobe."""
    import subprocess
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        media_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


def get_decoded_audio_duration(media_path: str) -> float:
    """Sample-accurate DECODED duration of an audio file.

    MP3 container metadata overstates the decoded length by ~30-40ms per file
    (encoder delay/padding that ffmpeg trims on decode). compile_video's concat
    consumes decoded samples, so subtitle frame offsets must sum decoded
    durations — summing container metadata accumulated ~33ms/frame of lag,
    silently eating the 300ms subtitle lead by the end of a 10-frame video.
    """
    import subprocess
    probe = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'a:0',
         '-show_entries', 'stream=channels,sample_rate', '-of', 'csv=p=0', media_path],
        capture_output=True, text=True)
    channels, sample_rate = (int(x) for x in probe.stdout.strip().split(','))
    decoded = subprocess.run(
        ['ffmpeg', '-v', 'error', '-i', media_path, '-f', 's16le', '-'],
        capture_output=True)
    return len(decoded.stdout) / (2 * channels * sample_rate)


def build_words_from_stored_timestamps(video_folder: str) -> Optional[List[Dict]]:
    """
    Assemble absolute-timeline word timestamps from the per-frame ElevenLabs
    timestamp files written at TTS time (audio/frame_N_timestamps.json).

    Frame N's words are shifted by the cumulative audio duration of frames
    before it — compile_video.py clamps each segment to its audio duration,
    so this matches the final-video timeline.

    Returns None (caller falls back to Scribe) if any frame lacks a
    timestamp file, or if the summed durations disagree with the compiled
    video's actual duration by more than 2 seconds.
    """
    script_data = load_script(Path(video_folder))
    audio_dir = os.path.join(video_folder, 'audio')

    words: List[Dict] = []
    offset = 0.0
    for frame in script_data.frames:
        ts_path = os.path.join(audio_dir, f"frame_{frame.number}_timestamps.json")
        mp3_path = os.path.join(audio_dir, f"frame_{frame.number}.mp3")
        if not os.path.exists(ts_path) or not os.path.exists(mp3_path):
            return None
        try:
            with open(ts_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
        frame_words = data.get('words') or []
        if not frame_words:
            return None

        for w in frame_words:
            words.append({
                'word': w['word'],
                'start': w['start'] + offset,
                'end': w['end'] + offset,
            })
        # Decoded duration, NOT container metadata — see get_decoded_audio_duration
        offset += get_decoded_audio_duration(mp3_path)

    # Guard: summed frame durations must match the compiled video's AUDIO
    # stream — words ride the audio, and with decoded durations the agreement
    # is sample-accurate modulo one AAC priming window. Do NOT compare against
    # the container/video duration: compile_video builds each PNG segment's
    # video stream from the mp3's container duration (~35ms/frame longer than
    # the decoded audio), so the video stream legitimately outruns the audio
    # by n_frames × ~35ms — a 16-frame video drifts ~0.5s without the word
    # timeline being wrong at all.
    video_path = os.path.join(video_folder, 'final_video.mp4')
    audio_stream_duration = get_decoded_audio_duration(video_path)
    drift = audio_stream_duration - offset
    print(f"      Timeline check: frames sum {offset:.3f}s vs video audio stream "
          f"{audio_stream_duration:.3f}s (drift {drift*1000:+.0f}ms)")
    if abs(drift) > DRIFT_TOLERANCE:
        print(f"      Warning: drift exceeds {DRIFT_TOLERANCE*1000:.0f}ms — "
              f"falling back to Scribe transcription "
              f"(raise SUBTITLE_DRIFT_TOLERANCE to keep stored timestamps)")
        return None

    return words


def transcribe_video_with_scribe(video_path: str) -> List[Dict]:
    """
    Transcribe final_video.mp4 with ElevenLabs Scribe to get word-level
    timestamps already aligned to the actual video timeline.

    Returns list of word dicts: [{'word': str, 'start': float, 'end': float}, ...]
    """
    from scripts.utils.stt import transcribe

    # Scribe word timestamps are already in seconds
    return transcribe(video_path)['words']


def _normalize(word: str) -> str:
    """Normalize a word for comparison: lowercase, strip punctuation."""
    return word.lower().strip(".,;:!?\"'()-–—")


def align_script_to_transcription(script_text: str,
                                  stt_words: List[Dict]) -> List[Dict]:
    """
    Align script text (ground truth) to transcribed word timestamps using
    sequence matching (difflib).

    Handles insertions/deletions gracefully — if the transcriber merges two words
    or hallucinates an extra word, the surrounding words still get correct
    timestamps instead of the entire sequence going proportional.

    Returns list of: [{'word': str, 'start': float, 'end': float}, ...]
    """
    script_words = script_text.replace('\n', ' ').split()

    if not stt_words or not script_words:
        return []

    # Normalize both sides for matching
    script_normalized = [_normalize(w) for w in script_words]
    stt_normalized = [_normalize(w['word']) for w in stt_words]

    # Use SequenceMatcher to find matching blocks
    matcher = difflib.SequenceMatcher(None, script_normalized, stt_normalized, autojunk=False)
    opcodes = matcher.get_opcodes()

    aligned = []
    for tag, s_start, s_end, a_start, a_end in opcodes:
        s_count = s_end - s_start
        a_count = a_end - a_start

        if tag == 'equal':
            # Perfect match — direct 1:1 timestamp mapping
            for i in range(s_count):
                aligned.append({
                    'word': script_words[s_start + i],
                    'start': stt_words[a_start + i]['start'],
                    'end': stt_words[a_start + i]['end']
                })

        elif tag == 'replace':
            # Different words on both sides — spread script words across the
            # time span of the corresponding transcribed words
            time_start = stt_words[a_start]['start']
            time_end = stt_words[a_end - 1]['end']
            time_per_word = (time_end - time_start) / s_count if s_count > 0 else 0

            t = time_start
            for i in range(s_count):
                aligned.append({
                    'word': script_words[s_start + i],
                    'start': t,
                    'end': t + time_per_word
                })
                t += time_per_word

        elif tag == 'delete':
            # Script has words the transcriber didn't detect — interpolate timestamps
            # from surrounding context. Guarantee a minimum duration per word
            # so subtitles containing these words don't flash by instantly.
            MIN_WORD_DURATION = 0.15  # seconds

            if aligned:
                time_start = aligned[-1]['end']
            elif a_end < len(stt_words):
                time_start = stt_words[a_end]['start'] - s_count * 0.3
                time_start = max(0, time_start)
            else:
                time_start = 0.0

            if a_end < len(stt_words):
                time_end = stt_words[a_end]['start']
            elif aligned:
                time_end = time_start + s_count * 0.3
            else:
                time_end = s_count * 0.3

            # If the gap is too small, expand it so each word gets at least
            # MIN_WORD_DURATION. This may cause a slight shift but prevents
            # zero-duration words that appear as missing subtitles.
            min_needed = s_count * MIN_WORD_DURATION
            if (time_end - time_start) < min_needed:
                time_end = time_start + min_needed

            time_per_word = (time_end - time_start) / s_count if s_count > 0 else 0
            t = time_start
            for i in range(s_count):
                aligned.append({
                    'word': script_words[s_start + i],
                    'start': t,
                    'end': t + time_per_word
                })
                t += time_per_word

        elif tag == 'insert':
            # Transcriber detected words not in script — skip them (no script word to assign)
            pass

    return aligned


def convert_to_srt_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)

    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt(aligned_words: List[Dict], output_path: str,
                 max_chars_per_line: int = 42) -> int:
    """
    Generate SRT subtitle file from aligned words.

    Groups words into 2-line subtitle chunks respecting max line length.
    Applies lead time to start timestamps and clamps end timestamps to
    prevent overlap between consecutive entries.
    Returns number of subtitle entries created.
    """
    # First pass: collect chunks with their raw start/end times
    chunks = []  # list of (text, raw_start, raw_end)

    current_chunk = []
    current_line = []
    current_length = 0
    chunk_start_time = None
    chunk_end_time = None

    for word_info in aligned_words:
        word = word_info['word'].strip()
        if not word:
            continue

        if chunk_start_time is None:
            chunk_start_time = word_info['start']

        word_length = len(word)

        # Check if adding this word exceeds line length
        if current_length + word_length + (1 if current_line else 0) > max_chars_per_line:
            # Start new line
            if current_line:
                current_chunk.append(' '.join(current_line))
                current_line = [word]
                current_length = word_length
            else:
                # Word too long, add anyway
                current_chunk.append(word)
                current_line = []
                current_length = 0

            # Filled 2 lines — emit chunk
            if len(current_chunk) >= 2:
                text = '\n'.join(current_chunk)
                chunks.append((text, chunk_start_time, chunk_end_time))

                current_chunk = []
                chunk_start_time = None
                chunk_end_time = None
                if current_line:
                    chunk_start_time = word_info['start']
        else:
            current_line.append(word)
            current_length += word_length + (1 if len(current_line) > 1 else 0)

        chunk_end_time = word_info['end']

    # Flush remaining words
    if current_line:
        current_chunk.append(' '.join(current_line))

    if current_chunk and chunk_start_time is not None:
        text = '\n'.join(current_chunk)
        chunks.append((text, chunk_start_time, chunk_end_time))

    # Second pass: apply lead time and clamp to prevent overlap
    subtitle_entries = []
    for i, (text, raw_start, raw_end) in enumerate(chunks):
        display_start = max(0, raw_start - SUBTITLE_LEAD_TIME)

        # Clamp end time so it doesn't extend past the next entry's display start
        if i + 1 < len(chunks):
            next_display_start = max(0, chunks[i + 1][1] - SUBTITLE_LEAD_TIME)
            display_end = min(raw_end, next_display_start)
        else:
            display_end = raw_end

        # Safety: end must be after start
        if display_end <= display_start:
            display_end = display_start + 0.1

        start_ts = convert_to_srt_timestamp(display_start)
        end_ts = convert_to_srt_timestamp(display_end)
        subtitle_entries.append(f"{i + 1}\n{start_ts} --> {end_ts}\n{text}\n")

    # Write SRT file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(subtitle_entries))

    return len(subtitle_entries)


def generate_subtitles_for_video(video_folder: str, force: bool = False) -> Tuple[bool, str]:
    """
    Generate subtitles for a single video folder by transcribing final_video.mp4.

    Returns (success, message)
    """
    video_name = os.path.basename(video_folder)

    # Check if subtitles already exist
    subtitle_path = os.path.join(video_folder, 'subtitles.srt')
    if os.path.exists(subtitle_path) and not force:
        return True, f"{video_name}: Skipped (subtitles.srt exists)"

    # Check required files
    video_path = os.path.join(video_folder, 'final_video.mp4')
    if not os.path.exists(video_path):
        return False, f"{video_name}: Missing final_video.mp4 (compile first)"

    json_path = os.path.join(video_folder, 'script.json')
    md_path = os.path.join(video_folder, 'script.md')
    if not os.path.exists(json_path) and not os.path.exists(md_path):
        return False, f"{video_name}: Missing script.json or script.md"

    print(f"\n{'='*60}")
    print(f"Generating subtitles: {video_name}")
    print('='*60)

    try:
        # Step 1: Try stored ElevenLabs timestamps (exact timing, no transcription)
        print("\n[1/3] Looking for stored word timestamps...")
        aligned_words = build_words_from_stored_timestamps(video_folder)

        if aligned_words:
            print(f"      Using ElevenLabs timestamps: {len(aligned_words)} words "
                  f"(no transcription needed)")
            print("\n[2/3] Skipping transcription (timestamps stored at TTS time)")
        else:
            # Step 2 (fallback): transcribe final video with ElevenLabs Scribe
            print("      Not available — falling back to Scribe transcription")
            full_narration = get_full_narration(video_folder)
            script_words = full_narration.split()
            print(f"      {len(script_words)} words in script")

            print("\n[2/3] Transcribing final_video.mp4 with ElevenLabs Scribe...")
            stt_words = transcribe_video_with_scribe(video_path)
            print(f"      Scribe detected {len(stt_words)} words")

            # Align script text to transcription timestamps
            aligned_words = align_script_to_transcription(full_narration, stt_words)
            if not aligned_words:
                return False, f"{video_name}: Alignment failed (no words)"

            # Report alignment quality
            match_count = sum(
                1 for a, t in zip(
                    [_normalize(w['word']) for w in aligned_words],
                    [_normalize(w['word']) for w in stt_words[:len(aligned_words)]]
                )
                if a == t
            )
            match_pct = match_count / len(aligned_words) * 100 if aligned_words else 0
            print(f"      Alignment: {len(aligned_words)} words placed, ~{match_pct:.0f}% direct matches")

        # Step 3: Generate SRT
        print("\n[3/3] Generating subtitles.srt...")
        num_subtitles = generate_srt(aligned_words, subtitle_path)
        print(f"      Created {num_subtitles} subtitle entries")

        return True, f"{video_name}: Generated {num_subtitles} subtitles"

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"{video_name}: Error - {e}"


def find_video_folders(lecture_folder: str, specific_video: Optional[int] = None) -> List[str]:
    """Find all Video-N folders in a lecture folder"""
    video_folders = []

    for item in sorted(os.listdir(lecture_folder)):
        if item.startswith('Video-'):
            video_num = int(item.split('-')[1])

            if specific_video is not None and video_num != specific_video:
                continue

            video_path = os.path.join(lecture_folder, item)
            if os.path.isdir(video_path):
                video_folders.append(video_path)

    return video_folders


def main():
    parser = argparse.ArgumentParser(
        description='Generate subtitles for Ludium Video videos from stored TTS timestamps (Scribe fallback)'
    )
    parser.add_argument('lecture_folder', help='Path to lecture folder (e.g., pipeline/LECTURE)')
    parser.add_argument('--video', type=int, help='Generate for specific video number only')
    parser.add_argument('--force', action='store_true', help='Regenerate even if subtitles.srt exists')

    args = parser.parse_args()

    # Resolve path
    lecture_folder = args.lecture_folder
    if not os.path.isabs(lecture_folder):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        lecture_folder = os.path.join(base_dir, lecture_folder)

    if not os.path.exists(lecture_folder):
        print(f"Error: Lecture folder not found: {lecture_folder}")
        sys.exit(1)

    # Find video folders
    video_folders = find_video_folders(lecture_folder, args.video)

    if not video_folders:
        print(f"Error: No Video-N folders found in {lecture_folder}")
        sys.exit(1)

    print("="*60)
    print("AUREA DICTA SUBTITLE GENERATION")
    print("="*60)
    print(f"Lecture: {os.path.basename(lecture_folder)}")
    print(f"Videos: {len(video_folders)}")
    print(f"Engine: stored ElevenLabs timestamps (Scribe fallback)")
    print(f"Force: {args.force}")
    print("="*60)

    # Process each video
    results = []
    for video_folder in video_folders:
        success, message = generate_subtitles_for_video(video_folder, args.force)
        results.append((success, message))

    # Print summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    success_count = 0
    skip_count = 0
    fail_count = 0

    for success, message in results:
        if success:
            if "Skipped" in message:
                skip_count += 1
                print(f"  - {message}")
            else:
                success_count += 1
                print(f"  + {message}")
        else:
            fail_count += 1
            print(f"  x {message}")

    print()
    print(f"Generated: {success_count}")
    print(f"Skipped: {skip_count}")
    print(f"Failed: {fail_count}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
