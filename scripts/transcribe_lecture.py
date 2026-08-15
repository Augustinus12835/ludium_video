#!/usr/bin/env python3
"""
Source Transcription Script for Ludium Video
Uses ElevenLabs Scribe for long lectures (fast cloud transcription)
Uses Whisper locally for short videos (subtitle timestamp alignment)
"""

import os
import sys
import json
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment variables
project_root = Path(__file__).parent.parent
load_dotenv(project_root / '.env')

ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY')


def get_video_duration(video_path: str) -> float:
    """
    Get video duration in seconds using ffprobe

    Args:
        video_path: Path to video file

    Returns:
        Duration in seconds
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0


def extract_audio(video_path: str, output_path: str = None) -> str:
    """
    Extract audio from video file using FFmpeg

    Args:
        video_path: Path to video file
        output_path: Optional output path for audio file

    Returns:
        Path to extracted audio file
    """
    if output_path is None:
        output_path = tempfile.mktemp(suffix=".wav")

    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-vn",  # No video
        "-acodec", "pcm_s16le",  # PCM format
        "-ar", "16000",  # 16kHz sample rate
        "-ac", "1",  # Mono
        "-y",  # Overwrite
        output_path
    ]

    print(f"Extracting audio from {video_path}...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise Exception(f"FFmpeg error: {result.stderr}")

    print(f"Audio extracted to {output_path}")
    return output_path


def transcribe_with_scribe(file_path: str) -> dict:
    """
    Transcribe video/audio using ElevenLabs Scribe (fast cloud transcription)
    Scribe handles MP4 directly - no need to extract audio

    Args:
        file_path: Path to video or audio file

    Returns:
        Dictionary with transcription data in Whisper-compatible format
    """
    from scripts.utils.stt import transcribe

    scribe = transcribe(file_path)

    print("Transcription complete!")

    # Convert to Whisper-compatible format
    result = {
        "text": scribe["text"],
        "language": "en",
        "segments": []
    }

    # Scribe returns words, we need to group them into segments
    # Note: We only store segment-level timestamps, not word-level (saves ~90% file size)
    if scribe["words"]:
        current_segment = {
            "id": 0,
            "start": scribe["words"][0]["start"],
            "end": scribe["words"][0]["end"],
            "text": ""
        }

        segment_duration = 120.0  # Group into ~2 minute segments (reduces JSON size)
        segment_start = current_segment["start"]

        for word in scribe["words"]:
            word_start = word["start"]
            word_end = word["end"]

            # Start new segment if we've exceeded duration
            if word_start - segment_start > segment_duration:
                # Save current segment
                current_segment["text"] = current_segment["text"].strip()
                result["segments"].append(current_segment)

                # Start new segment
                current_segment = {
                    "id": len(result["segments"]),
                    "start": word_start,
                    "end": word_end,
                    "text": ""
                }
                segment_start = word_start

            # Add word to current segment
            current_segment["text"] += " " + word["word"]
            current_segment["end"] = word_end

        # Don't forget the last segment
        if current_segment["text"]:
            current_segment["text"] = current_segment["text"].strip()
            result["segments"].append(current_segment)

    return result


def transcribe_with_whisper(audio_path: str, model_name: str = "small") -> dict:
    """
    Transcribe audio using local Whisper model

    Args:
        audio_path: Path to audio file
        model_name: Whisper model name (tiny, base, small, medium, large)

    Returns:
        Dictionary with transcription data
    """
    try:
        import whisper
    except ImportError:
        print("Error: openai-whisper not installed. Run: pip install openai-whisper")
        sys.exit(1)

    print(f"Loading Whisper model: {model_name}")
    print("(This may take a while on first run as the model downloads...)")
    model = whisper.load_model(model_name)

    print("Transcribing audio locally...")
    print("(This may take a long time for long lectures)")

    result = model.transcribe(
        audio_path,
        word_timestamps=True,
        language="en",
        verbose=True
    )

    return result


def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS format"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def save_transcript(transcript: dict, output_dir: str) -> tuple:
    """
    Save transcript in multiple formats

    Args:
        transcript: Transcription result
        output_dir: Directory to save files

    Returns:
        Tuple of (json_path, txt_path)
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Prepare JSON output
    json_data = {
        "text": transcript["text"],
        "language": transcript.get("language", "en"),
        "duration": transcript["segments"][-1]["end"] if transcript["segments"] else 0,
        "segments": []
    }

    for segment in transcript["segments"]:
        seg_data = {
            "id": segment["id"],
            "start": segment["start"],
            "end": segment["end"],
            "start_formatted": format_timestamp(segment["start"]),
            "end_formatted": format_timestamp(segment["end"]),
            "text": segment["text"].strip()
        }
        # Note: Word-level timestamps omitted to reduce file size
        # Use transcript_timestamped.txt for segment-level timestamps
        json_data["segments"].append(seg_data)

    # Save JSON
    json_path = output_path / "transcript.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    print(f"Saved: {json_path}")

    # Save plain text
    txt_path = output_path / "transcript.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(transcript["text"])
    print(f"Saved: {txt_path}")

    # Save timestamped text
    timestamped_path = output_path / "transcript_timestamped.txt"
    with open(timestamped_path, "w", encoding="utf-8") as f:
        for segment in transcript["segments"]:
            timestamp = format_timestamp(segment["start"])
            f.write(f"[{timestamp}] {segment['text'].strip()}\n")
    print(f"Saved: {timestamped_path}")

    return str(json_path), str(txt_path)


def main():
    """Main transcription function"""
    if len(sys.argv) < 2:
        print("Usage: python transcribe_lecture.py <video_path> [OPTIONS]")
        print()
        print("Arguments:")
        print("  video_path       Path to lecture video file")
        print()
        print("Options:")
        print("  --scribe         Use ElevenLabs Scribe (default for long lectures)")
        print("  --whisper        Use local Whisper model")
        print("  --model MODEL    Whisper model (tiny/base/small/medium/large)")
        print()
        print("Examples:")
        print("  python transcribe_lecture.py inputs/YOUR_LECTURE.mp4")
        print("  python transcribe_lecture.py inputs/lecture.mp4 --whisper --model small")
        sys.exit(1)

    video_path = sys.argv[1]

    # Parse options ("--assemblyai" kept as a legacy alias for --scribe)
    use_scribe = ("--scribe" in sys.argv or "--assemblyai" in sys.argv
                  or "--whisper" not in sys.argv)
    use_whisper = "--whisper" in sys.argv
    model_name = "small"

    if "--model" in sys.argv:
        idx = sys.argv.index("--model")
        if idx + 1 < len(sys.argv):
            model_name = sys.argv[idx + 1]

    # Default to Scribe if API key is available
    if not use_whisper and not ELEVENLABS_API_KEY:
        print("Warning: ELEVENLABS_API_KEY not found, falling back to Whisper")
        use_scribe = False
        use_whisper = True

    # Validate video file
    if not os.path.exists(video_path):
        print(f"Error: Video file not found: {video_path}")
        sys.exit(1)

    # Determine output directory
    video_name = Path(video_path).stem
    output_dir = Path("pipeline") / video_name

    # Get video duration for time estimate
    video_duration = get_video_duration(video_path)
    video_duration_str = format_timestamp(video_duration) if video_duration > 0 else "unknown"

    # Estimate transcription time based on benchmarks:
    # - Scribe: ~12x real-time (3hr video = ~15 min)
    # - Whisper small: ~1x real-time (3hr video = ~3 hr)
    # - Whisper large: ~0.3x real-time (3hr video = ~10 hr)
    if use_scribe:
        est_time = video_duration / 12  # ~12x real-time
    else:
        whisper_speeds = {"tiny": 10, "base": 5, "small": 1, "medium": 0.5, "large": 0.3}
        speed = whisper_speeds.get(model_name, 1)
        est_time = video_duration / speed

    est_time_str = format_timestamp(est_time) if video_duration > 0 else "unknown"

    print("=" * 60)
    print("Ludium Video - Source Transcription")
    print("=" * 60)
    print(f"Video: {video_path}")
    print(f"Duration: {video_duration_str}")
    print(f"Engine: {'ElevenLabs Scribe (cloud)' if use_scribe else f'Whisper (local, {model_name})'}")
    print(f"Estimated time: ~{est_time_str}")
    print(f"Output: {output_dir}")
    print("=" * 60)

    start_time = datetime.now()

    try:
        # Transcribe
        if use_scribe:
            # Scribe handles MP4 directly - no audio extraction needed
            transcript = transcribe_with_scribe(video_path)
        else:
            # Whisper needs audio extraction
            audio_path = extract_audio(video_path)
            transcript = transcribe_with_whisper(audio_path, model_name)
            # Clean up temporary audio file
            if audio_path.startswith(tempfile.gettempdir()):
                os.remove(audio_path)

        # Save results
        json_path, txt_path = save_transcript(transcript, output_dir)

        # Report
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        print()
        print("=" * 60)
        print("Transcription Complete")
        print("=" * 60)
        print(f"Duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
        print(f"Segments: {len(transcript['segments'])}")
        print(f"Words: ~{len(transcript['text'].split())}")
        print()
        print("Output files:")
        print(f"  - {json_path}")
        print(f"  - {txt_path}")
        print()
        print("Next step: Run segment_concepts.py to segment into videos")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
