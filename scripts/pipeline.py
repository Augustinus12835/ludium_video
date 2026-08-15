#!/usr/bin/env python3
"""
Ludium Video - Resumable Video Production Pipeline

A file-based, resumable pipeline with interactive review checkpoints.
State is detected from output files, not tracked in JSON.

LLM-authored steps — clean, segment, script, and verify_math — are written
by Claude Code subagents: render each step's prompt with
scripts/render_step_prompt.py and save the subagent's output, then resume
(see .claude/skills/run-pipeline/SKILL.md). This orchestrator runs the
deterministic/non-LLM steps (TTS, Manim renders, compile, subtitles).

Usage:
    python pipeline.py run YOUR_LECTURE
    python pipeline.py video YOUR_LECTURE/Video-3
    python pipeline.py status YOUR_LECTURE
"""

import argparse
import os
import subprocess
import sys
import json
import re
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)
from dataclasses import dataclass
from typing import Optional, List, Tuple
from enum import Enum
from urllib.parse import urlparse, parse_qs


# =============================================================================
# CONFIGURATION
# =============================================================================

PIPELINE_ROOT = Path("pipeline")
SCRIPTS_DIR = Path(__file__).parent

# Steps that require user review
REVIEW_CHECKPOINTS = {
    "segment": "Review segments.json and Video-N/content.txt files",
    "script": "Review script.md",
    "verify_math": "Review math_verification.json for accuracy",
    "tts": "Review generated audio in audio/ (check for errors, silence, pronunciation)",
}

# Week-level steps (run once per lecture)
WEEK_STEPS = ["transcribe", "clean", "segment"]

# Video-level steps (run per video). The former "brief" step was merged into
# "script" — script generation now plans pedagogy directly from content.txt +
# segments.json metadata (video_brief.md/visual_specs.json are legacy artifacts).
VIDEO_STEPS = ["script", "verify_math", "tts", "animate", "compile", "subtitle"]


# =============================================================================
# YOUTUBE SUPPORT
# =============================================================================

def is_youtube_url(url: str) -> bool:
    """Check if a string is a YouTube URL."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        return parsed.netloc in ('www.youtube.com', 'youtube.com', 'youtu.be', 'm.youtube.com')
    except Exception:
        return False


def extract_youtube_video_id(url: str) -> Optional[str]:
    """Extract video ID from YouTube URL."""
    try:
        parsed = urlparse(url)
        if parsed.netloc == 'youtu.be':
            return parsed.path[1:]
        if parsed.netloc in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
            if parsed.path == '/watch':
                return parse_qs(parsed.query).get('v', [None])[0]
            if parsed.path.startswith('/embed/'):
                return parsed.path.split('/')[2]
            if parsed.path.startswith('/v/'):
                return parsed.path.split('/')[2]
        return None
    except Exception:
        return None


def transcribe_youtube_audio(video_id: str, output_dir: Path) -> Tuple[bool, str]:
    """
    Download YouTube audio with yt-dlp and transcribe with ElevenLabs Scribe.

    Fallback when YouTube captions are disabled/unavailable.

    Returns:
        (success, error_message)
    """
    if not os.getenv("ELEVENLABS_API_KEY"):
        return False, "ELEVENLABS_API_KEY not set in .env (needed for audio transcription fallback)"

    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / "source_audio.m4a"

    # Step 1: Download audio with yt-dlp
    if not audio_path.exists():
        print(f"  Downloading audio with yt-dlp...")
        url = f"https://www.youtube.com/watch?v={video_id}"
        result = subprocess.run(
            ["yt-dlp", "-f", "bestaudio[ext=m4a]/bestaudio", "-o", str(audio_path), url],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            return False, f"yt-dlp failed: {result.stderr[-500:]}"
        file_mb = audio_path.stat().st_size / (1024 * 1024)
        print(f"  Downloaded: {audio_path.name} ({file_mb:.1f} MB)")
    else:
        file_mb = audio_path.stat().st_size / (1024 * 1024)
        print(f"  Using existing audio: {audio_path.name} ({file_mb:.1f} MB)")

    # Step 2: Transcribe with ElevenLabs Scribe (blocking upload + transcription)
    sys.path.insert(0, str(SCRIPTS_DIR.parent))
    from scripts.utils.stt import transcribe

    start = time.time()
    try:
        scribe = transcribe(str(audio_path))
    except Exception as e:
        return False, f"Scribe transcription failed: {e}"
    print(f"  Transcription complete ({time.time() - start:.0f}s)")

    # Step 3: Convert to transcript.json format (segments/duration in ms,
    # matching the YouTube-caption path)
    full_text = scribe["text"]
    words = scribe["words"]  # Scribe timestamps are in seconds

    # Build ~10-second chunks from words
    segments = []
    chunk_words = []
    chunk_start = 0
    for w in words:
        if not chunk_words:
            chunk_start = int(w["start"] * 1000)
        chunk_words.append(w["word"])
        if w["end"] * 1000 - chunk_start >= 10000:  # 10s chunks
            segments.append({
                "text": " ".join(chunk_words),
                "start": chunk_start,
                "end": int(w["end"] * 1000),
            })
            chunk_words = []
    if chunk_words:
        segments.append({
            "text": " ".join(chunk_words),
            "start": chunk_start,
            "end": int(words[-1]["end"] * 1000),
        })

    # Audio length via ffprobe (Scribe doesn't report file duration)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True
    )
    try:
        total_duration = float(probe.stdout.strip()) * 1000  # ms
    except ValueError:
        total_duration = words[-1]["end"] * 1000 if words else 0

    transcript_path = output_dir / "transcript.json"
    with open(transcript_path, 'w', encoding='utf-8') as f:
        json.dump({
            'text': full_text,
            'language': 'en',
            'duration': int(total_duration),
            'segments': segments,
            'source': 'elevenlabs_scribe',
            'video_id': video_id
        }, f, indent=2, ensure_ascii=False)

    print(f"  Saved transcript: {transcript_path}")
    print(f"  Duration: {total_duration/60000:.1f} minutes")
    print(f"  Segments: {len(segments)}")

    # Clean up audio file (large, not needed after transcription)
    audio_path.unlink()
    print(f"  Cleaned up {audio_path.name}")

    return True, ""


def fetch_youtube_transcript(video_id: str, output_dir: Path) -> Tuple[bool, str]:
    """
    Fetch YouTube transcript and save to transcript.json.

    Returns:
        (success, error_message)
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
    except ImportError:
        return False, "youtube-transcript-api not installed. Run: pip install youtube-transcript-api"

    try:
        # Create API instance (new API style)
        ytt_api = YouTubeTranscriptApi()

        # Try to get transcript (prefer manual captions over auto-generated)
        transcript_list = ytt_api.list(video_id)

        transcript = None
        language = 'en'

        # Try manual transcripts first
        try:
            transcript = transcript_list.find_manually_created_transcript(['en', 'en-US', 'en-GB'])
            print(f"  Found manual English transcript")
        except Exception:
            pass

        # Fall back to auto-generated
        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(['en', 'en-US', 'en-GB'])
                print(f"  Found auto-generated English transcript")
            except Exception:
                pass

        # If still no English, try any available and translate
        if transcript is None:
            try:
                for t in transcript_list:
                    transcript = t
                    language = t.language_code
                    print(f"  Found transcript in {t.language} - will use as-is")
                    break
            except Exception:
                pass

        if transcript is None:
            return False, "No transcript available for this video"

        # Fetch the actual transcript data
        transcript_data = transcript.fetch()

        # Convert to our format - transcript_data is now a FetchedTranscript object
        # which is iterable and contains Snippet objects
        snippets = list(transcript_data)
        full_text = ' '.join([snippet.text for snippet in snippets])
        total_duration = snippets[-1].start + snippets[-1].duration if snippets else 0

        # Build segments in transcript.json format (start/end in ms)
        segments = []
        for snippet in snippets:
            segments.append({
                'text': snippet.text,
                'start': int(snippet.start * 1000),  # Convert to milliseconds
                'end': int((snippet.start + snippet.duration) * 1000),
            })

        # Save transcript.json
        output_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = output_dir / "transcript.json"

        with open(transcript_path, 'w', encoding='utf-8') as f:
            json.dump({
                'text': full_text,
                'language': language,
                'duration': int(total_duration * 1000),
                'segments': segments,
                'source': 'youtube',
                'video_id': video_id
            }, f, indent=2, ensure_ascii=False)

        print(f"  Saved transcript: {transcript_path}")
        print(f"  Duration: {total_duration/60:.1f} minutes")
        print(f"  Segments: {len(segments)}")

        return True, ""

    except (TranscriptsDisabled, NoTranscriptFound) as e:
        print(f"  No YouTube captions available ({type(e).__name__})")
        print(f"  Falling back to audio download + Scribe transcription...")
        return transcribe_youtube_audio(video_id, output_dir)
    except Exception as e:
        return False, f"Error fetching transcript: {str(e)}"


def get_youtube_video_title(video_id: str) -> Optional[str]:
    """Get video title from YouTube (for auto-generating lecture ID)."""
    try:
        import urllib.request
        import json as json_module

        # Use YouTube oEmbed API (no API key required)
        url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json_module.loads(response.read().decode())
            return data.get('title', '')
    except Exception:
        return None


def sanitize_lecture_id(title: str) -> str:
    """Convert a video title to a valid lecture ID."""
    # Remove special characters, keep alphanumeric and spaces
    sanitized = re.sub(r'[^\w\s-]', '', title)
    # Replace spaces with underscores
    sanitized = re.sub(r'\s+', '_', sanitized.strip())
    # Limit length
    if len(sanitized) > 50:
        sanitized = sanitized[:50]
    return sanitized or "YouTube_Video"


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class StepStatus(Enum):
    COMPLETE = "complete"
    PENDING = "pending"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class WeekState:
    """State of week-level pipeline steps."""
    lecture_dir: Path
    transcript_exists: bool = False
    transcript_cleaned_exists: bool = False
    plan_exists: bool = False
    video_count: int = 0
    videos_with_content: int = 0

    @property
    def transcribe_status(self) -> StepStatus:
        return StepStatus.COMPLETE if self.transcript_exists else StepStatus.PENDING

    @property
    def clean_status(self) -> StepStatus:
        return StepStatus.COMPLETE if self.transcript_cleaned_exists else StepStatus.PENDING

    @property
    def segment_status(self) -> StepStatus:
        if not self.plan_exists:
            return StepStatus.PENDING
        if self.videos_with_content < self.video_count:
            return StepStatus.PARTIAL
        return StepStatus.COMPLETE


@dataclass
class VideoState:
    """State of a single video's pipeline steps."""
    video_dir: Path
    video_num: int
    video_title: str = ""

    # File existence
    content_exists: bool = False
    script_exists: bool = False
    math_verification_exists: bool = False
    video_exists: bool = False
    subtitle_exists: bool = False

    # Flags
    requires_math: bool = False

    # Counts
    expected_frames: int = 0
    audio_found: int = 0

    @property
    def script_status(self) -> StepStatus:
        return StepStatus.COMPLETE if self.script_exists else StepStatus.PENDING

    @property
    def verify_math_status(self) -> StepStatus:
        # If math_verification.json exists, it's complete (regardless of requires_math flag)
        if self.math_verification_exists:
            return StepStatus.COMPLETE
        if not self.requires_math:
            return StepStatus.SKIPPED
        return StepStatus.PENDING

    @property
    def tts_status(self) -> StepStatus:
        if self.expected_frames == 0:
            return StepStatus.PENDING
        if self.audio_found >= self.expected_frames:
            return StepStatus.COMPLETE
        if self.audio_found > 0:
            return StepStatus.PARTIAL
        return StepStatus.PENDING

    @property
    def animate_status(self) -> StepStatus:
        # Animation is optional — skip if no math verification
        if not self.math_verification_exists:
            return StepStatus.SKIPPED
        # Check for .animate_done marker (written by generate_math_animation.py)
        animate_marker = self.video_dir / "frames" / ".animate_done"
        if animate_marker.exists():
            return StepStatus.COMPLETE
        return StepStatus.PENDING

    @property
    def compile_status(self) -> StepStatus:
        return StepStatus.COMPLETE if self.video_exists else StepStatus.PENDING

    @property
    def subtitle_status(self) -> StepStatus:
        return StepStatus.COMPLETE if self.subtitle_exists else StepStatus.PENDING

    def next_step(self) -> Optional[str]:
        """Determine the next step to run. Returns None if complete."""
        if not self.content_exists:
            return None  # Cannot proceed without content.txt

        if not self.script_exists:
            return "script"

        if self.verify_math_status == StepStatus.PENDING:
            return "verify_math"

        if self.tts_status in (StepStatus.PENDING, StepStatus.PARTIAL):
            return "tts"

        if self.animate_status == StepStatus.PENDING:
            return "animate"

        if not self.video_exists:
            return "compile"

        if not self.subtitle_exists:
            return "subtitle"

        return None  # Complete


# =============================================================================
# STATE DETECTION FUNCTIONS
# =============================================================================

def detect_week_state(lecture_dir: Path) -> WeekState:
    """Detect week-level pipeline state from files."""
    state = WeekState(lecture_dir=lecture_dir)

    # Check transcript files
    transcript_json = lecture_dir / "transcript.json"
    state.transcript_exists = transcript_json.exists() and transcript_json.stat().st_size > 100

    # Check for cleaned content (clean_transcript.py outputs content_cleaned.txt)
    content_cleaned = lecture_dir / "content_cleaned.txt"
    state.transcript_cleaned_exists = content_cleaned.exists()

    # Check segmentation files (segment_concepts.py outputs segments.json)
    # Also check for plan.md which may be created for human review
    segments_json = lecture_dir / "segments.json"
    plan_md = lecture_dir / "plan.md"
    state.plan_exists = segments_json.exists() or plan_md.exists()

    if plan_md.exists():
        # Count expected videos from plan.md
        state.video_count = count_videos_in_plan(plan_md)
    elif segments_json.exists():
        # Count videos from segments.json
        state.video_count = count_videos_in_segments(segments_json)

    # Count videos with content.txt
    video_dirs = sorted(lecture_dir.glob("Video-*"))
    state.videos_with_content = sum(
        1 for vd in video_dirs if (vd / "content.txt").exists()
    )

    if state.video_count == 0:
        state.video_count = len(video_dirs)

    return state


def detect_video_state(video_dir: Path, math: bool = False, technical: bool = False) -> VideoState:
    """Detect video-level pipeline state from files."""
    # Extract video number from directory name
    video_num = int(video_dir.name.replace("Video-", ""))
    state = VideoState(video_dir=video_dir, video_num=video_num)

    # Check basic file existence
    state.content_exists = (video_dir / "content.txt").exists()
    # Check for script.json (preferred) or script.md (legacy)
    state.script_exists = (video_dir / "script.json").exists() or (video_dir / "script.md").exists()
    state.math_verification_exists = (video_dir / "math_verification.json").exists()

    # Check final video (with size validation)
    final_video = video_dir / "final_video.mp4"
    state.video_exists = final_video.exists() and final_video.stat().st_size > 1_000_000

    # Extract video title (script.json preferred; legacy video_brief.md fallback)
    state.video_title = extract_video_title(video_dir)

    # Check requires_math (script.json metadata > legacy visual_specs.json > course name)
    lecture_name = video_dir.parent.name
    state.requires_math = check_requires_math(video_dir, lecture_name)

    # --math or --technical flag forces math verification (and thus animation)
    if math or technical:
        state.requires_math = True

    # Count frames expected (from the script)
    if state.script_exists:
        state.expected_frames = count_frames_in_script(video_dir / "script.md")

    # Count audio files
    audio_dir = video_dir / "audio"
    if audio_dir.exists():
        state.audio_found = len(list(audio_dir.glob("frame_*.mp3")))

    # Check subtitles
    state.subtitle_exists = (video_dir / "subtitles.srt").exists()

    return state


def count_videos_in_plan(plan_path: Path) -> int:
    """Count number of videos defined in plan.md."""
    content = plan_path.read_text()
    # Match patterns like "## Video 1:" or "### Video-1" or "Video 1 -"
    matches = re.findall(r'(?:^|\n)#*\s*Video[- ]?(\d+)', content, re.IGNORECASE)
    return len(set(matches)) if matches else 0


def count_videos_in_segments(segments_path: Path) -> int:
    """Count number of videos defined in segments.json."""
    try:
        segments = json.loads(segments_path.read_text())
        # segments.json has a "videos" array
        return len(segments.get("videos", []))
    except (json.JSONDecodeError, KeyError):
        return 0


# Lecture name prefixes that always require math verification
MATH_COURSE_PREFIXES = (
    "Calculus_",
    "Linear_Algebra_",
    "Statistics_",
    "Probability_",
    "Differential_Equations_",
    "Trigonometry_",
)


def is_math_course(lecture_name: str) -> bool:
    """Detect math courses by lecture folder name."""
    return any(lecture_name.startswith(prefix) for prefix in MATH_COURSE_PREFIXES)


def require_pipeline_mode(lecture_name: str, math: bool, technical: bool) -> None:
    """Exit unless a supported mode is selected (--math/--technical or math prefix)."""
    if math or technical or is_math_course(lecture_name):
        return
    print(f"{Colors.RED}Error: cannot determine pipeline mode for '{lecture_name}'.{Colors.RESET}")
    print("This pipeline supports math and technical modes only.")
    print("Pass --math (pure math) or --technical (math + diagrams + code).")
    print(f"{Colors.DIM}Folders starting with {', '.join(MATH_COURSE_PREFIXES)} "
          f"auto-select math mode.{Colors.RESET}")
    sys.exit(1)


def check_requires_math(video_dir: Path, lecture_name: str = "") -> bool:
    """Check if video requires math verification.

    Priority: course name detection > script.json metadata (script generation
    declares it) > legacy visual_specs.json flag > default False.
    """
    # Course-level override: math courses always require math
    if lecture_name and is_math_course(lecture_name):
        return True

    json_path = video_dir / "script.json"
    if json_path.exists():
        try:
            meta_flag = json.loads(json_path.read_text()).get("metadata", {}).get("requires_math")
            if isinstance(meta_flag, bool):
                return meta_flag
        except (json.JSONDecodeError, KeyError):
            pass

    specs_path = video_dir / "visual_specs.json"
    if specs_path.exists():
        try:
            specs = json.loads(specs_path.read_text())
            return specs.get("requires_math", False)
        except (json.JSONDecodeError, KeyError):
            pass

    return False


def count_frames_in_script(script_path: Path) -> int:
    """
    Count frames defined in script file.

    Supports both script.json and script.md formats.
    """
    video_dir = script_path.parent

    # Try JSON first
    json_path = video_dir / "script.json"
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return len(data.get("frames", []))
        except (json.JSONDecodeError, IOError):
            pass

    # Fall back to MD parsing
    if script_path.exists():
        content = script_path.read_text()
        # Match "## Frame N" headers
        matches = re.findall(r'^## Frame (\d+)', content, re.MULTILINE)
        return len(matches)

    return 0


def extract_video_title(video_dir: Path) -> str:
    """Extract video title — script.json first, legacy video_brief.md fallback."""
    json_path = video_dir / "script.json"
    if json_path.exists():
        try:
            title = json.loads(json_path.read_text()).get("title", "")
            if title:
                return title
        except (json.JSONDecodeError, KeyError):
            pass

    brief_path = video_dir / "video_brief.md"
    if brief_path.exists():
        content = brief_path.read_text()
        # Look for "**Title:**" line or similar
        match = re.search(r'\*\*Title:\*\*\s*(.+)$', content, re.MULTILINE)
        if match:
            return match.group(1).strip()
        # Fallback: look for first heading
        match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        if match:
            return match.group(1).strip()
    return ""


# =============================================================================
# OUTPUT FORMATTING
# =============================================================================

# ANSI color codes
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    RED = "\033[31m"


def print_header(title: str, subtitle: str = ""):
    """Print formatted header."""
    width = 64
    print()
    print(f"{Colors.CYAN}{'─' * width}{Colors.RESET}")
    print(f"{Colors.BOLD}  {title}{Colors.RESET}")
    if subtitle:
        print(f"{Colors.DIM}  {subtitle}{Colors.RESET}")
    print(f"{Colors.CYAN}{'─' * width}{Colors.RESET}")
    print()


def status_symbol(status: StepStatus) -> str:
    """Get symbol for status."""
    symbols = {
        StepStatus.COMPLETE: f"{Colors.GREEN}✓{Colors.RESET}",
        StepStatus.PENDING: f"{Colors.DIM}○{Colors.RESET}",
        StepStatus.PARTIAL: f"{Colors.YELLOW}◐{Colors.RESET}",
        StepStatus.SKIPPED: f"{Colors.DIM}–{Colors.RESET}",
        StepStatus.ERROR: f"{Colors.RED}✗{Colors.RESET}",
    }
    return symbols.get(status, "?")


def print_week_status(state: WeekState):
    """Print week-level status."""
    print(f"  {status_symbol(state.transcribe_status)} transcript.json")
    print(f"  {status_symbol(state.clean_status)} content_cleaned.txt")

    seg_status = state.segment_status
    if seg_status == StepStatus.PARTIAL:
        print(f"  {status_symbol(seg_status)} segments.json ({state.videos_with_content}/{state.video_count} videos)")
    else:
        print(f"  {status_symbol(seg_status)} segments.json" +
              (f" ({state.video_count} videos)" if state.plan_exists else ""))


def print_video_status(state: VideoState, verbose: bool = True):
    """Print video-level status."""
    print(f"  {status_symbol(state.script_status)} script.md" +
          (f" ({state.expected_frames} frames)" if state.script_exists else ""))

    # Math verification status
    if state.verify_math_status == StepStatus.SKIPPED:
        print(f"  {status_symbol(state.verify_math_status)} math_verification.json (not a math video)")
    else:
        print(f"  {status_symbol(state.verify_math_status)} math_verification.json")

    # Audio with count
    if state.expected_frames > 0:
        print(f"  {status_symbol(state.tts_status)} audio/ ({state.audio_found}/{state.expected_frames})")
    else:
        print(f"  {status_symbol(state.tts_status)} audio/")

    # Animation status
    if state.animate_status == StepStatus.SKIPPED:
        print(f"  {status_symbol(state.animate_status)} animate (no math)")
    else:
        # Count animated .mp4 frames
        frames_dir = state.video_dir / "frames"
        mp4_count = len(list(frames_dir.glob("frame_*.mp4"))) if frames_dir.exists() else 0
        if mp4_count > 0:
            print(f"  {status_symbol(state.animate_status)} animate ({mp4_count} animated frames)")
        else:
            print(f"  {status_symbol(state.animate_status)} animate")

    print(f"  {status_symbol(state.compile_status)} final_video.mp4")
    print(f"  {status_symbol(state.subtitle_status)} subtitles.srt")


def print_step_header(step_num: int, total_steps: int, step_name: str, description: str):
    """Print step header."""
    print()
    print(f"{Colors.BOLD}[{step_num}/{total_steps}] {step_name.upper()}{Colors.RESET}")
    print(f"{Colors.DIM}    {description}{Colors.RESET}")
    print()


# =============================================================================
# CHECKPOINT HANDLING
# =============================================================================

def prompt_checkpoint(step: str, video_dir: Path = None) -> bool:
    """
    Prompt user at a review checkpoint.
    Returns True to continue, False to quit.
    """
    description = REVIEW_CHECKPOINTS.get(step, "Review output")

    print()
    print(f"{Colors.CYAN}{'─' * 64}{Colors.RESET}")
    print(f"{Colors.BOLD}  REVIEW CHECKPOINT: {step.upper()}{Colors.RESET}")
    print(f"{Colors.CYAN}{'─' * 64}{Colors.RESET}")
    print()
    print(f"  {description}")
    print()

    # Show relevant files to review
    if video_dir:
        if step == "script":
            print(f"  File: {video_dir}/script.md")
    else:
        # Week-level checkpoint
        print(f"  Files to review in pipeline directory")

    print()
    print(f"  {Colors.DIM}Make any manual edits now. Pipeline will detect changes.{Colors.RESET}")
    print()
    print(f"  [{Colors.GREEN}c{Colors.RESET}] Continue to next step")
    print(f"  [{Colors.YELLOW}q{Colors.RESET}] Quit (resume later with same command)")
    print()

    while True:
        try:
            choice = input(f"  Choice [c/q]: ").strip().lower()
            if choice in ("c", "continue", ""):
                return True
            elif choice in ("q", "quit"):
                return False
            else:
                print(f"  {Colors.RED}Invalid choice. Enter 'c' to continue or 'q' to quit.{Colors.RESET}")
        except (KeyboardInterrupt, EOFError):
            print()
            return False


# =============================================================================
# STEP EXECUTION
# =============================================================================

def run_script(script_name: str, args: List[str], verbose: bool = True) -> bool:
    """
    Run a pipeline script and return success status.
    """
    script_path = SCRIPTS_DIR / script_name

    if not script_path.exists():
        print(f"{Colors.RED}  Error: Script not found: {script_path}{Colors.RESET}")
        return False

    cmd = [sys.executable, str(script_path)] + args

    if verbose:
        print(f"{Colors.DIM}  Running: {script_name} {' '.join(args)}{Colors.RESET}")

    try:
        result = subprocess.run(cmd)
        return result.returncode == 0
    except Exception as e:
        print(f"{Colors.RED}  Error running script: {e}{Colors.RESET}")
        return False


def subagent_step_error(step: str, render_cmd: str) -> bool:
    """Hard-fail an LLM-authored step: it must be written by a Claude Code subagent."""
    print(f"{Colors.RED}  Error: The {step} step is authored by a Claude Code "
          f"subagent.{Colors.RESET}")
    print(f"{Colors.RED}  Render its prompt: venv/bin/python scripts/render_step_prompt.py "
          f"{render_cmd} — see .claude/skills/run-pipeline/SKILL.md.{Colors.RESET}")
    return False


def run_week_step(step: str, lecture_dir: Path, source_video: Path = None,
                  youtube_video_id: str = None) -> bool:
    """Run a week-level pipeline step."""
    if step == "transcribe":
        # Check if this is a YouTube source
        if youtube_video_id:
            print(f"  Fetching YouTube transcript...")
            success, error = fetch_youtube_transcript(youtube_video_id, lecture_dir)
            if not success:
                print(f"{Colors.RED}  Error: {error}{Colors.RESET}")
                return False
            return True

        # Otherwise, use local video file
        if source_video is None:
            # Try to find source video
            source_video = find_source_video(lecture_dir)
        if source_video is None:
            print(f"{Colors.RED}  Error: Source video not found{Colors.RESET}")
            return False
        return run_script("transcribe_lecture.py", [str(source_video)])

    elif step == "clean":
        return subagent_step_error(
            "clean", f"clean --transcript {lecture_dir}/transcript.json")

    elif step == "segment":
        return subagent_step_error(
            "segment",
            f"segment --content {lecture_dir}/content_cleaned.txt "
            f"(then segment_concepts.py {lecture_dir} --apply RESPONSE.json)")

    return False


def run_video_step(step: str, video_dir: Path, lecture_dir: Path,
                   technical: bool = False, math: bool = False) -> bool:
    """Run a video-level pipeline step."""
    video_num = video_dir.name.replace("Video-", "")

    if step == "script":
        if math:
            mode = "math"
        elif technical:
            mode = "technical"
        else:
            mode = "math"  # math course auto-detected from the folder prefix
        return subagent_step_error(
            "script", f"script --video-dir {video_dir} --mode {mode}")

    elif step == "verify_math":
        # Check if math verification is needed
        requires_math = check_requires_math(video_dir, lecture_dir.name)
        # --math/--technical force verification; otherwise need requires_math flag
        if not requires_math and not math and not technical:
            print(f"  {Colors.DIM}No math verification needed, skipping...{Colors.RESET}")
            return True
        return subagent_step_error(
            "verify_math",
            f"verify_math --video-dir {video_dir} --frame N (per frame, "
            f"then color_plan --video-dir {video_dir})")

    elif step == "tts":
        script_path = video_dir / "script.md"
        if not script_path.exists():
            print(f"{Colors.RED}  Error: script.md not found{Colors.RESET}")
            return False

        # Pre-TTS guard: halt if TTS-unfriendly tokens (raw numerals, Greek letters, math
        # symbols, hex/opaque strings, unspaced differentials, bare initialisms, code tokens)
        # survived into spoken narration. Surfaced like a SymPy failure so /run-pipeline triages
        # (convert the source vs. bypass a false alarm). Bypass with SKIP_NARRATION_CHECK=1.
        if not os.getenv("SKIP_NARRATION_CHECK"):
            sys.path.insert(0, str(SCRIPTS_DIR.parent))
            from scripts.utils.narration_check import scan_video_narration, format_report
            offenders = scan_video_narration(video_dir)
            if offenders:
                print(f"\n{Colors.RED}{format_report(offenders, video_dir)}{Colors.RESET}")
                return False

        # No --voice-id: generate_tts_elevenlabs.py reads ELEVENLABS_VOICE_ID from .env
        return run_script("generate_tts_elevenlabs.py", [str(script_path)])

    elif step == "animate":
        # Skip if no math verification data
        mv_path = video_dir / "math_verification.json"
        if not mv_path.exists():
            print(f"  {Colors.DIM}No math_verification.json, skipping animation...{Colors.RESET}")
            return True
        # Create frames/ dir so the renders have an output target
        (video_dir / "frames").mkdir(parents=True, exist_ok=True)
        args = [str(video_dir)]
        if math:
            args.append("--math")
        elif technical:
            args.append("--technical")
        return run_script("generate_math_animation.py", args)

    elif step == "compile":
        return run_script("compile_video.py", [str(video_dir)])

    elif step == "subtitle":
        return run_script("generate_subtitles.py", [str(lecture_dir), "--video", video_num])

    return False


def find_source_video(lecture_dir: Path) -> Optional[Path]:
    """Find the source video file for a lecture."""
    inputs_dir = Path("inputs")
    lecture_name = lecture_dir.name

    # Try exact match
    for ext in [".mp4", ".mov", ".mkv"]:
        video_path = inputs_dir / f"{lecture_name}{ext}"
        if video_path.exists():
            return video_path

    # Try partial match
    for video_path in inputs_dir.glob("*.*"):
        if video_path.suffix.lower() in [".mp4", ".mov", ".mkv"]:
            if lecture_name in video_path.stem:
                return video_path

    return None


# =============================================================================
# MAIN PIPELINE RUNNERS
# =============================================================================

def run_full_pipeline(lecture_id: str, review_mode: bool = True,
                      from_step: str = None, only_video: int = None,
                      technical: bool = False, math: bool = False,
                      to_step: str = None):
    """
    Run the full pipeline for a lecture.

    lecture_id can be:
    - A lecture name (e.g., "MY_LECTURE") - looks for inputs/MY_LECTURE.mp4
    - A YouTube URL (e.g., "https://www.youtube.com/watch?v=xxx") - fetches transcript

    1. Run week-level steps (transcribe, clean, segment)
    2. Checkpoint: Review segmentation
    3. For each video, run video pipeline with checkpoints
    """
    youtube_video_id = None

    # Check if lecture_id is a YouTube URL
    if is_youtube_url(lecture_id):
        youtube_video_id = extract_youtube_video_id(lecture_id)
        if not youtube_video_id:
            print(f"{Colors.RED}Error: Could not extract video ID from YouTube URL{Colors.RESET}")
            sys.exit(1)

        # Get video title to create lecture ID
        print(f"Fetching video info from YouTube...")
        video_title = get_youtube_video_title(youtube_video_id)
        if video_title:
            lecture_id = sanitize_lecture_id(video_title)
            print(f"  Title: {video_title}")
            print(f"  Lecture ID: {lecture_id}")
        else:
            lecture_id = f"YouTube_{youtube_video_id}"
            print(f"  Could not fetch title, using: {lecture_id}")

    # Strip "pipeline/" prefix if user passed the full path
    prefix = str(PIPELINE_ROOT) + "/"
    if lecture_id.startswith(prefix):
        lecture_id = lecture_id[len(prefix):]
    lecture_dir = PIPELINE_ROOT / lecture_id

    # A mode is required: --math, --technical, or a math course folder prefix
    require_pipeline_mode(lecture_id, math, technical)

    # Ensure pipeline directory exists
    lecture_dir.mkdir(parents=True, exist_ok=True)

    # Save YouTube source info for reference
    if youtube_video_id:
        source_info = lecture_dir / "source.json"
        if not source_info.exists():
            with open(source_info, 'w') as f:
                json.dump({
                    'type': 'youtube',
                    'video_id': youtube_video_id,
                    'url': f'https://www.youtube.com/watch?v={youtube_video_id}'
                }, f, indent=2)

    print_header(
        "AUREA DICTA - Video Production Pipeline",
        f"Lecture: {lecture_id}" + (f" (YouTube: {youtube_video_id})" if youtube_video_id else "")
    )

    # --- PHASE 1: Week-Level Steps ---

    week_state = detect_week_state(lecture_dir)

    print(f"{Colors.BOLD}Week-Level Status:{Colors.RESET}")
    print_week_status(week_state)
    print()

    # Determine starting step
    week_start_idx = 0
    if from_step and from_step in WEEK_STEPS:
        week_start_idx = WEEK_STEPS.index(from_step)
    elif from_step and from_step in VIDEO_STEPS:
        # Skip all week steps when --from targets a video step
        week_start_idx = len(WEEK_STEPS)
    else:
        # Auto-detect from state
        if week_state.transcribe_status != StepStatus.COMPLETE:
            if week_state.clean_status == StepStatus.COMPLETE:
                # content_cleaned.txt exists (e.g., book or slide-deck source) — skip transcribe+clean
                week_start_idx = 2
            else:
                week_start_idx = 0
        elif week_state.clean_status != StepStatus.COMPLETE:
            week_start_idx = 1
        elif week_state.segment_status != StepStatus.COMPLETE:
            week_start_idx = 2
        else:
            week_start_idx = len(WEEK_STEPS)  # Skip week steps

    # Run week-level steps
    for i in range(week_start_idx, len(WEEK_STEPS)):
        step = WEEK_STEPS[i]

        # --to: stop after the named step (inclusive). Honor it for week steps.
        if to_step and to_step in WEEK_STEPS and i > WEEK_STEPS.index(to_step):
            break

        # Re-check state (may have changed)
        week_state = detect_week_state(lecture_dir)

        step_status = getattr(week_state, f"{step}_status", StepStatus.PENDING)
        if step_status == StepStatus.COMPLETE:
            print(f"  {Colors.DIM}Skipping {step} (already complete){Colors.RESET}")
            continue

        print_step_header(i + 1, len(WEEK_STEPS), step,
                         f"{'Transcribe lecture' if step == 'transcribe' else 'Clean transcript' if step == 'clean' else 'Segment into videos'}")

        success = run_week_step(step, lecture_dir, youtube_video_id=youtube_video_id)

        if not success:
            print(f"\n{Colors.RED}  ✗ {step} failed{Colors.RESET}")
            print(f"\n  Fix the issue and run:")
            print(f"  python scripts/pipeline.py run {lecture_id} --from {step}")
            return

        print(f"\n  {Colors.GREEN}✓ {step} completed{Colors.RESET}")

    # --- CHECKPOINT: Review Segmentation (only if we ran segment step) ---

    # Only prompt if we actually ran week steps (not if all were already complete)
    if week_start_idx < len(WEEK_STEPS) and review_mode:
        week_state = detect_week_state(lecture_dir)
        if week_state.segment_status == StepStatus.COMPLETE:
            if not prompt_checkpoint("segment"):
                print(f"\n{Colors.YELLOW}Pipeline paused.{Colors.RESET}")
                print(f"Resume with: python scripts/pipeline.py run {lecture_id}")
                return

    # --to a week step → don't descend into video steps.
    if to_step and to_step in WEEK_STEPS:
        print(f"\n{Colors.YELLOW}Stopped after '{to_step}' (--to).{Colors.RESET}")
        return

    # --- PHASE 2: Video-Level Steps ---

    video_dirs = sorted(lecture_dir.glob("Video-*"))

    if not video_dirs:
        print(f"{Colors.RED}No Video-N directories found{Colors.RESET}")
        return

    # Filter to specific video if requested
    if only_video is not None:
        video_dirs = [vd for vd in video_dirs if vd.name == f"Video-{only_video}"]
        if not video_dirs:
            print(f"{Colors.RED}Video-{only_video} not found{Colors.RESET}")
            return

    total_videos = len(video_dirs)

    for video_idx, video_dir in enumerate(video_dirs):
        video_state = detect_video_state(video_dir, math=math, technical=technical)

        print_header(
            f"Video {video_state.video_num}/{total_videos}: {video_state.video_title or video_dir.name}",
            str(video_dir)
        )

        print(f"{Colors.BOLD}Status:{Colors.RESET}")
        print_video_status(video_state)
        print()

        # Check if video is complete
        next_step = video_state.next_step()
        if next_step is None:
            if video_state.video_exists:
                print(f"  {Colors.GREEN}✓ Video complete!{Colors.RESET}")
                continue
            else:
                print(f"  {Colors.RED}Cannot proceed - content.txt missing{Colors.RESET}")
                continue

        # Determine starting step for this video
        video_start_idx = VIDEO_STEPS.index(next_step)

        if from_step and from_step in VIDEO_STEPS:
            video_start_idx = VIDEO_STEPS.index(from_step)

        print(f"  Starting from: {Colors.CYAN}{VIDEO_STEPS[video_start_idx]}{Colors.RESET}")
        print()

        # Run video steps
        for step_idx in range(video_start_idx, len(VIDEO_STEPS)):
            step = VIDEO_STEPS[step_idx]

            # --to: stop after the named step (inclusive).
            if to_step and to_step in VIDEO_STEPS and step_idx > VIDEO_STEPS.index(to_step):
                print(f"  {Colors.YELLOW}Stopped after '{to_step}' (--to).{Colors.RESET}")
                break

            # Re-detect state
            video_state = detect_video_state(video_dir, math=math, technical=technical)

            # Check if step is needed
            if step == "script" and video_state.script_exists:
                continue
            if step == "verify_math" and video_state.verify_math_status in (StepStatus.COMPLETE, StepStatus.SKIPPED):
                continue
            if step == "tts" and video_state.tts_status == StepStatus.COMPLETE:
                continue
            if step == "animate" and video_state.animate_status in (StepStatus.COMPLETE, StepStatus.SKIPPED):
                continue
            if step == "compile" and video_state.compile_status == StepStatus.COMPLETE:
                continue
            if step == "subtitle" and video_state.subtitle_status == StepStatus.COMPLETE:
                continue

            # Run step
            print_step_header(
                step_idx + 1, len(VIDEO_STEPS), step,
                {"script": "Generate narration script",
                 "verify_math": "Verify math calculations",
                 "tts": "Generate TTS audio",
                 "animate": "Generate math animations",
                 "compile": "Compile final video",
                 "subtitle": "Generate subtitles"}[step]
            )

            success = run_video_step(step, video_dir, lecture_dir, technical=technical, math=math)

            if not success:
                print(f"\n{Colors.RED}  ✗ {step} failed{Colors.RESET}")
                print(f"\n  Fix the issue and run:")
                print(f"  python scripts/pipeline.py video {lecture_id}/{video_dir.name} --from {step}")
                return

            print(f"\n  {Colors.GREEN}✓ {step} completed{Colors.RESET}")

            # Checkpoint after review steps
            if step in REVIEW_CHECKPOINTS and review_mode:
                if not prompt_checkpoint(step, video_dir):
                    print(f"\n{Colors.YELLOW}Pipeline paused.{Colors.RESET}")
                    print(f"Resume with: python scripts/pipeline.py video {lecture_id}/{video_dir.name}")
                    return

        # Video complete
        video_state = detect_video_state(video_dir, math=math, technical=technical)
        if video_state.video_exists:
            final_video = video_dir / "final_video.mp4"
            size_mb = final_video.stat().st_size / (1024 * 1024)
            print()
            print(f"  {Colors.GREEN}✓ Video complete: {final_video} ({size_mb:.1f} MB){Colors.RESET}")

    # --- All Done ---
    print()
    print_header("PIPELINE COMPLETE", f"All videos generated in {lecture_dir}")

    # Summary
    for video_dir in video_dirs:
        final_video = video_dir / "final_video.mp4"
        if final_video.exists():
            size_mb = final_video.stat().st_size / (1024 * 1024)
            print(f"  {Colors.GREEN}✓{Colors.RESET} {video_dir.name}/final_video.mp4 ({size_mb:.1f} MB)")
        else:
            print(f"  {Colors.RED}✗{Colors.RESET} {video_dir.name}/final_video.mp4 (not created)")


def run_single_video(video_path: str, review_mode: bool = True, from_step: str = None,
                     technical: bool = False, math: bool = False,
                     to_step: str = None):
    """
    Run pipeline for a single video (skips week-level steps).
    Assumes content.txt already exists.
    """
    video_dir = PIPELINE_ROOT / video_path

    if not video_dir.exists():
        # Try as relative path
        video_dir = Path(video_path)

    if not video_dir.exists():
        print(f"{Colors.RED}Error: Video directory not found: {video_path}{Colors.RESET}")
        sys.exit(1)

    if not video_dir.name.startswith("Video-"):
        print(f"{Colors.RED}Error: Expected Video-N directory{Colors.RESET}")
        sys.exit(1)

    lecture_dir = video_dir.parent

    # A mode is required: --math, --technical, or a math course folder prefix
    require_pipeline_mode(lecture_dir.name, math, technical)

    video_state = detect_video_state(video_dir, math=math, technical=technical)

    print_header(
        f"AUREA DICTA - Single Video Pipeline",
        f"Video: {video_dir}"
    )

    print(f"{Colors.BOLD}Status:{Colors.RESET}")
    print_video_status(video_state)
    print()

    # Check prerequisites
    if not video_state.content_exists:
        print(f"{Colors.RED}Error: content.txt not found{Colors.RESET}")
        print("Run week-level segmentation first:")
        print(f"  python scripts/pipeline.py run {lecture_dir.name}")
        sys.exit(1)

    # Determine starting step
    next_step = video_state.next_step()

    # Only return early if complete AND not forcing from a specific step
    if next_step is None and from_step is None:
        if video_state.video_exists:
            print(f"{Colors.GREEN}✓ Video already complete!{Colors.RESET}")
            return

    start_idx = VIDEO_STEPS.index(from_step) if from_step else VIDEO_STEPS.index(next_step)

    print(f"Starting from: {Colors.CYAN}{VIDEO_STEPS[start_idx]}{Colors.RESET}")
    print()

    # Run steps
    for step_idx in range(start_idx, len(VIDEO_STEPS)):
        step = VIDEO_STEPS[step_idx]

        # --to: stop after the named step (inclusive). Used by manual codegen mode
        # (--to tts) to hand off before the animate step's codegen API calls.
        if to_step and to_step in VIDEO_STEPS and step_idx > VIDEO_STEPS.index(to_step):
            print(f"{Colors.YELLOW}Stopped after '{to_step}' (--to).{Colors.RESET}")
            break

        # Re-detect state
        video_state = detect_video_state(video_dir, math=math, technical=technical)

        # Check if step needed (skip if complete, unless forced with --from)
        skip_check = from_step is None or step_idx > VIDEO_STEPS.index(from_step)
        if skip_check:
            if step == "script" and video_state.script_exists:
                continue
            if step == "verify_math" and video_state.verify_math_status in (StepStatus.COMPLETE, StepStatus.SKIPPED):
                continue
            if step == "tts" and video_state.tts_status == StepStatus.COMPLETE:
                continue
            if step == "animate" and video_state.animate_status in (StepStatus.COMPLETE, StepStatus.SKIPPED):
                continue
            if step == "compile" and video_state.compile_status == StepStatus.COMPLETE:
                continue
            if step == "subtitle" and video_state.subtitle_status == StepStatus.COMPLETE:
                continue

        print_step_header(
            step_idx + 1, len(VIDEO_STEPS), step,
            {"script": "Generate narration script",
             "verify_math": "Verify math calculations",
             "tts": "Generate TTS audio",
             "animate": "Generate math animations",
             "compile": "Compile final video",
             "subtitle": "Generate subtitles"}[step]
        )

        success = run_video_step(step, video_dir, lecture_dir, technical=technical, math=math)

        if not success:
            print(f"\n{Colors.RED}  ✗ {step} failed{Colors.RESET}")
            print(f"\n  Fix and run:")
            print(f"  python scripts/pipeline.py video {video_path} --from {step}")
            return

        print(f"\n  {Colors.GREEN}✓ {step} completed{Colors.RESET}")

        # Checkpoint
        if step in REVIEW_CHECKPOINTS and review_mode:
            if not prompt_checkpoint(step, video_dir):
                print(f"\n{Colors.YELLOW}Pipeline paused.{Colors.RESET}")
                print(f"Resume: python scripts/pipeline.py video {video_path}")
                return

    # Complete
    video_state = detect_video_state(video_dir, math=math, technical=technical)
    if video_state.video_exists:
        final_video = video_dir / "final_video.mp4"
        size_mb = final_video.stat().st_size / (1024 * 1024)
        print()
        print_header("VIDEO COMPLETE", f"{final_video} ({size_mb:.1f} MB)")


def show_status(path: str, video_num: int = None):
    """Show pipeline status."""
    target_path = PIPELINE_ROOT / path

    if not target_path.exists():
        target_path = Path(path)

    if not target_path.exists():
        print(f"{Colors.RED}Error: Path not found: {path}{Colors.RESET}")
        sys.exit(1)

    # Determine if this is a lecture or video directory
    if target_path.name.startswith("Video-"):
        # Single video status
        video_state = detect_video_state(target_path)
        print_header(f"Status: {target_path.name}", str(target_path))
        print_video_status(video_state, verbose=True)

        next_step = video_state.next_step()
        if next_step:
            print()
            print(f"  Next step: {Colors.CYAN}{next_step}{Colors.RESET}")
            print(f"  Run: python scripts/pipeline.py video {path}")
        elif video_state.video_exists:
            print()
            print(f"  {Colors.GREEN}✓ Video complete{Colors.RESET}")
    else:
        # Lecture status
        lecture_dir = target_path

        print_header(f"Status: {lecture_dir.name}", str(lecture_dir))

        week_state = detect_week_state(lecture_dir)
        print(f"{Colors.BOLD}Week-Level:{Colors.RESET}")
        print_week_status(week_state)
        print()

        video_dirs = sorted(lecture_dir.glob("Video-*"))

        if video_num is not None:
            video_dirs = [vd for vd in video_dirs if vd.name == f"Video-{video_num}"]

        if video_dirs:
            print(f"{Colors.BOLD}Videos:{Colors.RESET}")
            for video_dir in video_dirs:
                video_state = detect_video_state(video_dir)

                # Summary line
                if video_state.video_exists:
                    status = f"{Colors.GREEN}✓ Complete{Colors.RESET}"
                elif video_state.next_step():
                    status = f"{Colors.YELLOW}◐ {video_state.next_step()}{Colors.RESET}"
                else:
                    status = f"{Colors.RED}✗ Missing content{Colors.RESET}"

                # Check for animated frames
                frames_dir = video_dir / "frames"
                mp4_count = len(list(frames_dir.glob("frame_*.mp4"))) if frames_dir.exists() else 0
                anim_tag = f" {Colors.CYAN}[{mp4_count} animated]{Colors.RESET}" if mp4_count > 0 else ""

                title = video_state.video_title[:40] if video_state.video_title else ""
                print(f"  {video_dir.name}: {status}{anim_tag} {Colors.DIM}{title}{Colors.RESET}")


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Ludium Video - Resumable Video Production Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s run YOUR_LECTURE              Run full pipeline
  %(prog)s run YOUR_LECTURE --video 3    Run only Video-3
  %(prog)s video YOUR_LECTURE/Video-3    Run single video
  %(prog)s status YOUR_LECTURE           Show status

The pipeline automatically detects state from files and resumes
from where it left off. To force a step to rerun, delete its
output files and run the pipeline again.

LLM-authored steps (clean, segment, script, verify_math) are
written by Claude Code subagents via
scripts/render_step_prompt.py — this orchestrator hard-fails on
them with the exact command to run (see the /run-pipeline skill).

Review Checkpoints:
  - After segmentation: Review segments.json and content.txt
  - After script: Review script.md
  - After verify_math: Review math_verification.json
  - After tts: Review audio/
        """
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # run command
    run_parser = subparsers.add_parser("run", help="Run full pipeline for a lecture")
    run_parser.add_argument("lecture_id", help="Lecture ID (e.g., YOUR_LECTURE)")
    run_parser.add_argument("--video", type=int, help="Only process specific video number")
    run_parser.add_argument("--from", dest="from_step", help="Force start from step")
    run_parser.add_argument("--to", dest="to_step", default=None,
                            help="Stop after this step (inclusive). e.g. --to tts hands off "
                                 "before the animate codegen for manual frame authoring.")
    run_parser.add_argument("--no-review", action="store_true", help="Skip review checkpoints")
    run_parser.add_argument("--technical", action="store_true",
                            help="Force technical mode (all-Manim pipeline with conceptual visuals)")
    run_parser.add_argument("--math", action="store_true",
                            help="Force math mode (all-Manim pipeline)")

    # video command
    video_parser = subparsers.add_parser("video", help="Run pipeline for single video")
    video_parser.add_argument("path", help="Video path (e.g., YOUR_LECTURE/Video-3)")
    video_parser.add_argument("--from", dest="from_step", help="Force start from step")
    video_parser.add_argument("--to", dest="to_step", default=None,
                              help="Stop after this step (inclusive). e.g. --to tts hands off "
                                   "before the animate codegen for manual frame authoring.")
    video_parser.add_argument("--no-review", action="store_true", help="Skip review checkpoints")
    video_parser.add_argument("--technical", action="store_true",
                              help="Force technical mode (all-Manim pipeline with conceptual visuals)")
    video_parser.add_argument("--math", action="store_true",
                              help="Force math mode (all-Manim pipeline)")

    # status command
    status_parser = subparsers.add_parser("status", help="Show pipeline status")
    status_parser.add_argument("path", help="Lecture or video path")
    status_parser.add_argument("video_num", nargs="?", type=int, help="Video number")

    args = parser.parse_args()

    # Legacy: the brief step was merged into script
    if getattr(args, "from_step", None) == "brief":
        print(f"{Colors.YELLOW}Note: the brief step was merged into script — starting from script.{Colors.RESET}")
        args.from_step = "script"

    # Validate --to (must be a known week or video step)
    to_step = getattr(args, "to_step", None)
    if to_step and to_step not in WEEK_STEPS and to_step not in VIDEO_STEPS:
        print(f"{Colors.RED}Error: unknown --to step '{to_step}'. "
              f"Valid: {', '.join(WEEK_STEPS + VIDEO_STEPS)}{Colors.RESET}")
        sys.exit(1)

    if args.command == "run":
        run_full_pipeline(
            args.lecture_id,
            review_mode=not args.no_review,
            from_step=args.from_step,
            only_video=args.video,
            technical=args.technical,
            math=args.math,
            to_step=to_step,
        )

    elif args.command == "video":
        run_single_video(
            args.path,
            review_mode=not args.no_review,
            from_step=args.from_step,
            technical=args.technical,
            math=args.math,
            to_step=to_step,
        )

    elif args.command == "status":
        show_status(args.path, args.video_num)


if __name__ == "__main__":
    main()
