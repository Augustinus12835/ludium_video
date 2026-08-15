#!/usr/bin/env python3
"""
Video Compilation Script for Ludium Video
Compiles final video from frames and audio.

Usage:
    python3 compile_video.py pipeline/YOUR_LECTURE/Video-1

Output:
    - final_video.mp4 (complete video)
    - compilation_report.txt (verification report)

Note: Subtitles are generated separately using generate_subtitles.py
"""

import os
import sys
import json
import subprocess
from datetime import datetime
from typing import List, Dict, Tuple
from pathlib import Path

# Add parent to path for utils
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.utils.script_parser import load_script


class VideoCompilationError(Exception):
    """Base exception for compilation errors"""
    pass


class FrameMismatchError(VideoCompilationError):
    """Frame count doesn't match"""
    pass


class TimingError(VideoCompilationError):
    """Duration mismatch"""
    pass


class FFmpegError(VideoCompilationError):
    """FFmpeg execution failed"""
    pass


class FrameData:
    """Data structure for a single frame"""
    def __init__(self, number: int, start_time: float, end_time: float,
                 words: int, narration: str):
        self.number = number
        self.start_time = start_time  # Original script timing
        self.end_time = end_time      # Original script timing
        self.duration = end_time - start_time  # Script estimate
        self.words = words
        self.narration = narration  # Actual script text (ground truth)
        self.image_path = None
        self.audio_path = None
        self.actual_audio_duration = None  # Measured from audio file
        self.actual_start_time = None  # Actual video timestamp (calculated)
        self.actual_end_time = None    # Actual video timestamp (calculated)
        self.is_animated = False  # True if .mp4 (Manim animation) exists for this frame


def parse_time_to_seconds(time_str: str) -> float:
    """
    Convert MM:SS time format to seconds

    Examples:
        "0:15" -> 15.0
        "1:30" -> 90.0
        "4:00" -> 240.0
    """
    parts = time_str.split(':')
    if len(parts) != 2:
        raise ValueError(f"Invalid time format: {time_str}")

    minutes = int(parts[0])
    seconds = int(parts[1])
    return minutes * 60 + seconds


def parse_script_from_dir(video_folder: str) -> List[FrameData]:
    """
    Parse script file (JSON or MD) to extract frame timing and narration.

    Uses the shared script_parser utility for consistent parsing.
    Returns list of FrameData objects.
    """
    script_data = load_script(Path(video_folder))

    frames = []
    for frame in script_data.frames:
        frame_data = FrameData(
            number=frame.number,
            start_time=frame.start_seconds,
            end_time=frame.end_seconds,
            words=frame.word_count,
            narration=frame.narration  # Already clean - no visual annotations in JSON
        )
        frames.append(frame_data)

    return frames


def get_audio_duration_ffprobe(audio_path: str) -> float:
    """
    Sample-accurate DECODED audio duration in seconds.

    MP3 container metadata overstates the decoded length by ~30-40ms per file
    (encoder delay/padding that ffmpeg trims on decode). Segment -t values
    built from container durations made each PNG segment's video run ~34ms
    past its audio, which the concat filter padded with silence — accumulating
    ~0.5s of A/V timeline drift over a 16-frame video and breaking the
    decoded-duration offset stacking in generate_subtitles.py.
    """
    probe = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'a:0',
         '-show_entries', 'stream=channels,sample_rate', '-of', 'csv=p=0',
         audio_path],
        capture_output=True, text=True)
    try:
        channels, sample_rate = (int(x) for x in probe.stdout.strip().split(','))
        decoded = subprocess.run(
            ['ffmpeg', '-v', 'error', '-i', audio_path, '-f', 's16le', '-'],
            capture_output=True)
        if decoded.stdout:
            return len(decoded.stdout) / (2 * channels * sample_rate)
    except (ValueError, ZeroDivisionError):
        pass
    # Fallback: container metadata (decode failed — better than crashing)
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        audio_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


def calculate_actual_frame_times(frames: List[FrameData]) -> None:
    """
    Calculate actual frame start/end times based on measured audio durations

    This replaces script estimates with actual audio lengths.
    Frames and audio will start/end simultaneously.
    """
    current_time = 0.0

    for frame in frames:
        frame.actual_start_time = current_time
        frame.actual_end_time = current_time + frame.actual_audio_duration
        current_time = frame.actual_end_time

        print(f"      Frame {frame.number}: {frame.actual_start_time:.2f}s - {frame.actual_end_time:.2f}s "
              f"(audio: {frame.actual_audio_duration:.2f}s, script: {frame.duration:.2f}s)")


def validate_input_files(video_folder: str, frames: List[FrameData]) -> Tuple[int, int, int]:
    """
    Validate that all required input files exist and measure actual audio durations

    Returns: (num_frames, num_images, num_audio)
    """
    frames_dir = os.path.join(video_folder, 'frames')
    audio_dir = os.path.join(video_folder, 'audio')

    # Check directories exist
    if not os.path.exists(frames_dir):
        raise FrameMismatchError(f"Frames directory not found: {frames_dir}")
    if not os.path.exists(audio_dir):
        raise FrameMismatchError(f"Audio directory not found: {audio_dir}")

    # Validate each frame has a visual (.mp4 preferred, .png fallback) and audio
    for frame in frames:
        mp4_path = os.path.join(frames_dir, f"frame_{frame.number}.mp4")
        png_path = os.path.join(frames_dir, f"frame_{frame.number}.png")

        if os.path.exists(mp4_path):
            frame.image_path = mp4_path
            frame.is_animated = True
        elif os.path.exists(png_path):
            frame.image_path = png_path
            frame.is_animated = False
        else:
            raise FrameMismatchError(f"Missing visual: frame_{frame.number}.png or .mp4")

        # Check audio and get actual duration
        audio_name = f"frame_{frame.number}.mp3"
        audio_path = os.path.join(audio_dir, audio_name)
        if not os.path.exists(audio_path):
            raise FrameMismatchError(f"Missing audio: {audio_name}")
        frame.audio_path = audio_path

        # Get actual audio duration
        frame.actual_audio_duration = get_audio_duration_ffprobe(audio_path)

    # Calculate actual frame times based on real audio durations
    calculate_actual_frame_times(frames)

    num_frames = len(frames)
    num_visuals = len([f for f in os.listdir(frames_dir)
                       if f.startswith('frame_') and (f.endswith('.png') or f.endswith('.mp4'))])
    num_audio = len([f for f in os.listdir(audio_dir) if f.endswith('.mp3')])

    animated_count = sum(1 for f in frames if f.is_animated)
    if animated_count:
        print(f"      {animated_count} animated + {len(frames) - animated_count} static frames")

    return num_frames, num_visuals, num_audio


def build_ffmpeg_command(video_folder: str, frames: List[FrameData],
                        subtitle_path: str) -> List[str]:
    """
    Build FFmpeg command for video compilation with transitions

    Uses complex filter graph with:
    - Crossfade transitions (0.5s)
    - Actual audio durations (no estimates)
    - Separate subtitle file (NOT burned-in, allows students to toggle)

    Audio and frames start/end simultaneously - no artificial delays.
    """
    cmd = ['ffmpeg', '-y']

    # Add audio inputs
    for frame in frames:
        cmd.extend(['-i', frame.audio_path])

    # Add visual inputs (static PNGs looped, animated .mp4s used directly)
    for frame in frames:
        if frame.is_animated:
            # Clamp animated mp4 to audio duration to prevent cumulative drift
            # (Manim clips are often slightly longer than the audio)
            cmd.extend(['-t', str(frame.actual_audio_duration), '-i', frame.image_path])
        else:
            # Static PNG frame: feed a SINGLE frame; zoompan in the
            # filter graph expands it to the full duration with a slow Ken Burns
            # push. (No -loop here — zoompan's d=N owns the frame count.)
            cmd.extend(['-i', frame.image_path])

    # Build complex filter graph
    filter_parts = []

    # Process each visual: scale, set frame rate, add fade transitions
    num_frames = len(frames)
    fade_duration = 0.5  # 0.5 second crossfade

    for i, frame in enumerate(frames):
        input_idx = num_frames + i  # Visuals start after audio files

        # Calculate fade timings based on actual audio duration
        fade_out_start = frame.actual_audio_duration - fade_duration

        # scale preserving aspect ratio, pad to 1920x1080 with black bars, normalize SAR
        scale_pad = (
            "scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )

        if frame.is_animated:
            # Animated clip: already 30fps from Manim, just scale/pad + fades
            filter_str = (
                f"[{input_idx}:v]{scale_pad},"
                f"fade=t=in:st=0:d={fade_duration},"
                f"fade=t=out:st={fade_out_start}:d={fade_duration}[v{i}]"
            )
        else:
            # Static image: slow Ken Burns push (zoompan), upscaled 2x
            # for sub-pixel-smooth motion. Alternate zoom magnitude per frame so the
            # series breathes. Centered framing never reveals edges. fps locked to 30.
            n_out = max(2, int(round(frame.actual_audio_duration * 30)))
            zmax = 1.13 if (i % 2 == 0) else 1.08
            zstep = (zmax - 1.0) / (n_out - 1)
            ken_burns = (
                "scale=3840:2160:force_original_aspect_ratio=increase:flags=lanczos,"
                "crop=3840:2160,setsar=1,"
                f"zoompan=z='min(zoom+{zstep:.6f},{zmax})':d={n_out}:"
                "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                "s=1920x1080:fps=30,setsar=1"
            )
            filter_str = (
                f"[{input_idx}:v]{ken_burns},"
                f"fade=t=in:st=0:d={fade_duration},"
                f"fade=t=out:st={fade_out_start}:d={fade_duration}[v{i}]"
            )
        filter_parts.append(filter_str)

    # Reset audio timestamps (MP3 encoder delay can shift PTS)
    for i in range(num_frames):
        filter_parts.append(f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]")

    # Paired concat: each segment's video+audio are treated as a unit.
    # Quantization errors (video snaps to 1/30s boundaries) reset at each
    # segment boundary instead of accumulating across all frames.
    paired_concat = ''.join([f"[v{i}][a{i}]" for i in range(num_frames)])
    paired_concat += f"concat=n={num_frames}:v=1:a=1[video][audio]"
    filter_parts.append(paired_concat)

    # NOTE: Subtitles are NOT burned-in
    # Separate subtitles.srt file is generated for students to toggle on/off

    # Join all filter parts
    filter_complex = ';'.join(filter_parts)

    cmd.extend(['-filter_complex', filter_complex])

    # Map outputs (no subtitle burning)
    cmd.extend(['-map', '[video]', '-map', '[audio]'])

    # Video encoding settings
    cmd.extend([
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-crf', '23',
        '-pix_fmt', 'yuv420p',
        '-r', '30'
    ])

    # Audio encoding settings
    cmd.extend([
        '-c:a', 'aac',
        '-b:a', '192k',
        '-ar', '48000'
    ])

    # Output file
    output_path = os.path.join(video_folder, 'final_video.mp4')
    cmd.append(output_path)

    return cmd


def execute_ffmpeg(cmd: List[str]) -> Tuple[bool, str]:
    """
    Execute FFmpeg command with progress monitoring

    Returns: (success, output_message)
    """
    print("\n" + "="*70)
    print("EXECUTING FFMPEG COMPILATION")
    print("="*70)

    try:
        # Run FFmpeg
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )

        # Monitor progress from stderr (FFmpeg outputs to stderr)
        stderr_output = []
        for line in process.stderr:
            stderr_output.append(line)
            # Show progress lines
            if 'time=' in line:
                print(f"\r{line.strip()}", end='', flush=True)

        process.wait()

        if process.returncode != 0:
            error_msg = ''.join(stderr_output[-50:])  # Last 50 lines
            raise FFmpegError(f"FFmpeg failed with code {process.returncode}\n{error_msg}")

        print("\n✓ FFmpeg compilation successful")
        return True, "Success"

    except Exception as e:
        return False, str(e)


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe"""
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        video_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


def get_video_info(video_path: str) -> Dict:
    """Get detailed video information using ffprobe"""
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,codec_name,r_frame_rate',
        '-of', 'json',
        video_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)

    return data['streams'][0] if data.get('streams') else {}


def verify_compilation(video_folder: str, frames: List[FrameData]) -> Dict:
    """
    Verify the compiled video meets requirements

    Returns verification results dictionary
    """
    video_path = os.path.join(video_folder, 'final_video.mp4')
    results = {}

    # Check video exists
    if not os.path.exists(video_path):
        results['exists'] = False
        return results
    results['exists'] = True

    # Check file size
    file_size = os.path.getsize(video_path)
    results['file_size_mb'] = file_size / (1024 * 1024)

    # Check duration
    expected_duration = frames[-1].end_time
    actual_duration = get_video_duration(video_path)
    results['expected_duration'] = expected_duration
    results['actual_duration'] = actual_duration
    results['duration_diff'] = abs(actual_duration - expected_duration)
    results['duration_ok'] = results['duration_diff'] <= 2.0

    # Check video properties
    video_info = get_video_info(video_path)
    results['width'] = video_info.get('width', 0)
    results['height'] = video_info.get('height', 0)
    results['codec'] = video_info.get('codec_name', 'unknown')
    results['resolution_ok'] = (results['width'] == 1920 and results['height'] == 1080)

    return results


def generate_report(video_folder: str, frames: List[FrameData],
                   verification: Dict, compilation_time: float) -> str:
    """
    Generate comprehensive compilation report
    """
    report_lines = [
        "Video Compilation Report",
        "=" * 70,
        f"Video: {video_folder}/final_video.mp4",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Compilation Time: {compilation_time:.1f} seconds",
        "",
        "INPUT VERIFICATION",
        "-" * 70,
        f"Script parsed: {len(frames)} frames",
        f"Images found: {len(frames)} PNG files (1920x1080)",
        f"Audio found: {len(frames)} MP3 files",
        f"Total expected duration: {frames[-1].end_time:.0f} seconds",
        "",
        "VIDEO COMPILATION",
        "-" * 70,
        f"Frame transitions: Crossfade (0.5s)",
        f"Audio timing: Synchronized with frames",
        f"Video codec: H.264 (libx264, CRF 23)",
        f"Audio codec: AAC (192 kbps)",
        f"Resolution: 1920x1080 @ 30fps",
        "",
        "OUTPUT VERIFICATION",
        "-" * 70,
    ]

    if verification.get('exists'):
        status_symbol = "OK" if verification.get('duration_ok') else "WARNING"
        report_lines.extend([
            f"{status_symbol} Video duration: {verification['actual_duration']:.0f}s "
            f"(target: {verification['expected_duration']:.0f}s, "
            f"diff: {verification['duration_diff']:.1f}s)",
            f"File size: {verification['file_size_mb']:.1f} MB",
        ])

        if verification.get('resolution_ok'):
            report_lines.append(f"Resolution: {verification['width']}x{verification['height']}")
        else:
            report_lines.append(f"WARNING Resolution: {verification['width']}x{verification['height']} "
                              f"(expected 1920x1080)")

        report_lines.append(f"Codec: {verification['codec']}")
    else:
        report_lines.append("ERROR: Video file not created")

    report_lines.extend([
        "",
        "FILES CREATED",
        "-" * 70,
        f"final_video.mp4 ({verification.get('file_size_mb', 0):.1f} MB)",
        f"compilation_report.txt",
        "",
        "NOTE: Subtitles generated separately using generate_subtitles.py",
        "",
    ])

    # Overall status
    if verification.get('exists') and verification.get('duration_ok') and verification.get('resolution_ok'):
        report_lines.extend([
            "STATUS: COMPILATION SUCCESSFUL",
            "",
            "Next step: Generate subtitles, then review"
        ])
    else:
        report_lines.extend([
            "STATUS: COMPILATION COMPLETED WITH WARNINGS",
            "",
            "Please review the warnings above and verify video quality"
        ])

    return '\n'.join(report_lines)


def compile_video(video_folder: str) -> str:
    """
    Main compilation function

    Workflow:
    1. Parse script to get frame information
    2. Validate input files and measure audio durations
    3. Build and execute FFmpeg command
    4. Verify output and generate report

    Note: Subtitles are generated separately using generate_subtitles.py

    Returns status message
    """
    start_time = datetime.now()

    print("=" * 70)
    print("AUREA DICTA VIDEO COMPILATION")
    print("=" * 70)
    print(f"Video folder: {video_folder}")
    print("Frames/audio sync: Simultaneous (no delay)")
    print("Note: Subtitles generated separately (generate_subtitles.py)")
    print()

    try:
        # Step 1: Parse script first to get frame count
        print("[1/5] Parsing script...")
        # Check for script.json or script.md
        json_path = os.path.join(video_folder, 'script.json')
        md_path = os.path.join(video_folder, 'script.md')
        if not os.path.exists(json_path) and not os.path.exists(md_path):
            raise VideoCompilationError(f"Script not found: {json_path} or {md_path}")

        frames = parse_script_from_dir(video_folder)
        print(f"      Parsed {len(frames)} frames")
        print(f"      Script duration: {frames[-1].end_time:.0f} seconds")

        # Step 2: Validate input files and measure audio durations
        print("\n[2/5] Validating input files and calculating actual frame times...")
        num_frames, num_visuals, num_audio = validate_input_files(video_folder, frames)
        print(f"\n      Found {num_visuals} frame visuals (png/mp4)")
        print(f"      Found {num_audio} audio files")

        # Show actual vs script duration
        total_audio_duration = sum(f.actual_audio_duration for f in frames)
        total_script_duration = frames[-1].end_time
        print(f"      Total audio duration: {total_audio_duration:.1f}s (script estimate: {total_script_duration:.0f}s)")

        if num_frames != num_audio:
            raise FrameMismatchError(
                f"Mismatch: {num_frames} script frames, "
                f"{num_visuals} visuals, {num_audio} audio files"
            )

        # Step 3: Build FFmpeg command
        print("\n[3/5] Building FFmpeg command...")
        subtitle_path = os.path.join(video_folder, 'subtitles.srt')
        ffmpeg_cmd = build_ffmpeg_command(video_folder, frames, subtitle_path)
        print(f"      Filter graph created")
        print(f"      {len(frames)} frames with 0.5s crossfade transitions")
        print(f"      Using actual audio durations (no estimates)")
        print(f"      Frames and audio synchronized (no delay)")

        # Step 4: Execute compilation
        print("\n[4/5] Compiling video...")
        success, message = execute_ffmpeg(ffmpeg_cmd)
        if not success:
            raise FFmpegError(message)

        # Step 5: Verify output and generate report
        print("\n[5/5] Verifying output and generating report...")
        verification = verify_compilation(video_folder, frames)

        if verification.get('duration_ok'):
            print(f"      Duration verified: {verification['actual_duration']:.0f}s")
        else:
            print(f"      Duration off by {verification['duration_diff']:.1f}s")

        if verification.get('resolution_ok'):
            print(f"      Resolution verified: 1920x1080")
        else:
            print(f"      Resolution: {verification['width']}x{verification['height']}")

        print(f"      File size: {verification['file_size_mb']:.1f} MB")

        end_time = datetime.now()
        compilation_time = (end_time - start_time).total_seconds()

        report = generate_report(
            video_folder, frames,
            verification, compilation_time
        )

        report_path = os.path.join(video_folder, 'compilation_report.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)

        print(f"      Report saved to: compilation_report.txt")

        # Print summary
        print("\n" + "=" * 70)
        print("COMPILATION COMPLETE")
        print("=" * 70)
        print(f"Output: {video_folder}/final_video.mp4")
        print(f"Duration: {verification['actual_duration']:.0f}s (target: {verification['expected_duration']:.0f}s)")
        print(f"File size: {verification['file_size_mb']:.1f} MB")
        print(f"Compilation time: {compilation_time:.1f}s")
        print("\nReady for review!")

        return "SUCCESS"

    except FrameMismatchError as e:
        print(f"\nERROR: Frame mismatch - {e}")
        return f"ERROR: {e}"
    except TimingError as e:
        print(f"\nERROR: Timing issue - {e}")
        return f"ERROR: {e}"
    except FFmpegError as e:
        print(f"\nERROR: FFmpeg failed - {e}")
        return f"ERROR: {e}"
    except Exception as e:
        print(f"\nERROR: Unexpected error - {e}")
        import traceback
        traceback.print_exc()
        return f"ERROR: {e}"


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("Usage: python3 compile_video.py Week-N/Video-M")
        print("\nExample: python3 compile_video.py Week-1/Video-1")
        sys.exit(1)

    video_folder = sys.argv[1]

    # Convert to absolute path if needed
    if not os.path.isabs(video_folder):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        video_folder = os.path.join(base_dir, video_folder)

    if not os.path.exists(video_folder):
        print(f"Error: Video folder not found: {video_folder}")
        sys.exit(1)

    result = compile_video(video_folder)

    if result == "SUCCESS":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
