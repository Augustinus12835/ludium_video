#!/usr/bin/env python3
"""
Script Parser Utility for Ludium Video

Provides a unified interface for loading script data from either:
- script.json (new structured format - preferred)
- script.md (legacy markdown format - backward compatible)

Usage:
    from scripts.utils.script_parser import load_script, Frame, ScriptData

    data = load_script(video_dir)
    for frame in data.frames:
        print(frame.narration)
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any


@dataclass
class VisualInfo:
    """Visual information for a frame."""
    type: str  # "title", "conceptual", "data_chart", "text_focused"
    reference: str  # Description or filename reference


@dataclass
class Frame:
    """Represents a single frame with all its data."""
    number: int
    start_seconds: float
    end_seconds: float
    word_count: int
    narration: str
    visual: VisualInfo
    math_context: str = ""

    @property
    def duration(self) -> float:
        """Duration of this frame in seconds."""
        return self.end_seconds - self.start_seconds

    @property
    def timing_str(self) -> str:
        """Formatted timing string like '0:00-0:20'."""
        start_min = int(self.start_seconds // 60)
        start_sec = int(self.start_seconds % 60)
        end_min = int(self.end_seconds // 60)
        end_sec = int(self.end_seconds % 60)
        return f"{start_min}:{start_sec:02d}-{end_min}:{end_sec:02d}"


@dataclass
class ScriptMetadata:
    """Metadata about the script."""
    total_duration: str  # Formatted string like "7:00"
    total_duration_seconds: float
    frame_count: int
    word_count: int
    target_wps: float = 2.5


@dataclass
class ScriptData:
    """Complete script data."""
    title: str
    metadata: ScriptMetadata
    frames: List[Frame]

    def get_frame(self, number: int) -> Optional[Frame]:
        """Get a specific frame by number."""
        for frame in self.frames:
            if frame.number == number:
                return frame
        return None


def parse_time_to_seconds(time_str: str) -> float:
    """
    Convert MM:SS or M:SS time format to seconds.

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
    return float(minutes * 60 + seconds)


def seconds_to_time_str(seconds: float) -> str:
    """Convert seconds to M:SS format."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def load_script_json(json_path: Path) -> ScriptData:
    """
    Load script from JSON format.

    Expected JSON schema:
    {
        "title": "Video Title",
        "metadata": {
            "total_duration": "7:00",
            "frame_count": 11,
            "word_count": 1050,
            "target_wps": 2.5
        },
        "frames": [
            {
                "number": 0,
                "timing": {"start": "0:00", "end": "0:20", "start_seconds": 0, "end_seconds": 20},
                "word_count": 50,
                "narration": "...",
                "visual": {"type": "title", "reference": "Title slide - ..."}
            }
        ]
    }
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Parse metadata
    meta = data.get("metadata", {})
    total_duration_str = meta.get("total_duration", "0:00")
    metadata = ScriptMetadata(
        total_duration=total_duration_str,
        total_duration_seconds=parse_time_to_seconds(total_duration_str),
        frame_count=meta.get("frame_count", 0),
        word_count=meta.get("word_count", 0),
        target_wps=meta.get("target_wps", 2.5)
    )

    # Parse frames
    frames = []
    for frame_data in data.get("frames", []):
        timing = frame_data.get("timing", {})

        # Support both formats: explicit seconds or parsed from strings
        if "start_seconds" in timing:
            start_sec = timing["start_seconds"]
            end_sec = timing["end_seconds"]
        else:
            start_sec = parse_time_to_seconds(timing.get("start", "0:00"))
            end_sec = parse_time_to_seconds(timing.get("end", "0:00"))

        visual_data = frame_data.get("visual", {})
        visual = VisualInfo(
            type=visual_data.get("type", "conceptual"),
            reference=visual_data.get("reference", "")
        )

        frame = Frame(
            number=frame_data.get("number", 0),
            start_seconds=start_sec,
            end_seconds=end_sec,
            word_count=frame_data.get("word_count", 0),
            narration=frame_data.get("narration", ""),
            visual=visual,
            math_context=frame_data.get("math_context", ""),
        )
        frames.append(frame)

    return ScriptData(
        title=data.get("title", "Untitled"),
        metadata=metadata,
        frames=frames
    )


def load_script_md(md_path: Path) -> ScriptData:
    """
    Load script from markdown format (backward compatibility).

    Expected markdown format:
    # Script: Video Title

    **Total Duration:** 7:00
    **Frame Count:** 11
    **Word Count:** 1,050 (target: 1,050 words at 2.5/sec)

    ---

    ## Frame 0 (0:00-0:20) • 50 words

    Narration text here...

    [Visual: Description of visual]

    ---
    """
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract title
    title_match = re.search(r"# Script: (.+)", content)
    title = title_match.group(1).strip() if title_match else "Untitled"

    # Extract metadata
    duration_match = re.search(r"\*\*Total Duration:\*\*\s*(\d+:\d+)", content)
    frame_count_match = re.search(r"\*\*Frame Count:\*\*\s*(\d+)", content)
    word_count_match = re.search(r"\*\*Word Count:\*\*\s*([\d,]+)", content)

    total_duration_str = duration_match.group(1) if duration_match else "0:00"
    frame_count = int(frame_count_match.group(1)) if frame_count_match else 0
    word_count_str = word_count_match.group(1).replace(",", "") if word_count_match else "0"
    word_count = int(word_count_str)

    metadata = ScriptMetadata(
        total_duration=total_duration_str,
        total_duration_seconds=parse_time_to_seconds(total_duration_str),
        frame_count=frame_count,
        word_count=word_count,
        target_wps=2.5
    )

    # Parse frames
    frames = []

    # Pattern: ## Frame N (MM:SS-MM:SS) • NN words
    frame_pattern = r"## Frame (\d+) \((\d+:\d+)-(\d+:\d+)\) • (\d+) words?"
    sections = re.split(r"(?=## Frame \d+)", content)

    for section in sections:
        if not section.strip() or not section.strip().startswith("## Frame"):
            continue

        header_match = re.search(frame_pattern, section)
        if not header_match:
            continue

        frame_num = int(header_match.group(1))
        start_time = parse_time_to_seconds(header_match.group(2))
        end_time = parse_time_to_seconds(header_match.group(3))
        frame_word_count = int(header_match.group(4))

        # Extract visual reference
        visual_match = re.search(r"\[Visual: ([^\]]+)\]", section)
        visual_ref = visual_match.group(1).strip() if visual_match else ""

        # Determine visual type from reference
        visual_type = classify_visual_type(frame_num, visual_ref)

        visual = VisualInfo(type=visual_type, reference=visual_ref)

        # Extract narration (text between header and [Visual:])
        lines = section.split("\n")
        narration_lines = []
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue
            if line_stripped.startswith("## Frame"):
                continue
            if line_stripped.startswith("[Visual:"):
                continue
            if line_stripped.startswith("---"):
                continue
            narration_lines.append(line_stripped)

        narration = " ".join(narration_lines).strip()

        frame = Frame(
            number=frame_num,
            start_seconds=start_time,
            end_seconds=end_time,
            word_count=frame_word_count,
            narration=narration,
            visual=visual
        )
        frames.append(frame)

    # Update metadata frame count if we parsed more frames
    if len(frames) > metadata.frame_count:
        metadata = ScriptMetadata(
            total_duration=metadata.total_duration,
            total_duration_seconds=metadata.total_duration_seconds,
            frame_count=len(frames),
            word_count=metadata.word_count,
            target_wps=metadata.target_wps
        )

    return ScriptData(
        title=title,
        metadata=metadata,
        frames=frames
    )


def classify_visual_type(frame_num: int, visual_ref: str) -> str:
    """
    Classify the visual type from the reference string.

    Returns: "title", "data_chart", "conceptual", or "text_focused"
    """
    visual_ref_lower = visual_ref.lower()

    # Title slide (usually frame 0)
    if frame_num == 0:
        return "title"

    # Data chart (references a PNG file)
    if ".png" in visual_ref_lower:
        # Check if it's a chart reference like "visual_7.png"
        if any(word in visual_ref_lower for word in ["chart", "graph", "data", "visual_"]):
            return "data_chart"

    # Conceptual diagram
    if "conceptual" in visual_ref_lower:
        return "conceptual"

    # Default to conceptual for most visuals
    if visual_ref:
        return "conceptual"

    return "text_focused"


def load_script(video_dir: Path) -> ScriptData:
    """
    Load script data from a video directory.

    Auto-detects format:
    - Prefers script.json if it exists (new format)
    - Falls back to script.md (legacy format)

    Args:
        video_dir: Path to Video-N directory

    Returns:
        ScriptData object with parsed script

    Raises:
        FileNotFoundError: If neither script.json nor script.md exists
    """
    video_dir = Path(video_dir)
    json_path = video_dir / "script.json"
    md_path = video_dir / "script.md"

    if json_path.exists():
        return load_script_json(json_path)
    elif md_path.exists():
        return load_script_md(md_path)
    else:
        raise FileNotFoundError(f"No script file found in {video_dir}")


def _frame_to_dict(frame: Frame) -> Dict[str, Any]:
    """Convert a single Frame to a JSON-serializable dict."""
    d = {
        "number": frame.number,
        "timing": {
            "start": seconds_to_time_str(frame.start_seconds),
            "end": seconds_to_time_str(frame.end_seconds),
            "start_seconds": frame.start_seconds,
            "end_seconds": frame.end_seconds
        },
        "word_count": frame.word_count,
        "narration": frame.narration,
        "visual": {
            "type": frame.visual.type,
            "reference": frame.visual.reference
        }
    }
    if frame.math_context:
        d["math_context"] = frame.math_context
    return d


def script_to_json(data: ScriptData) -> Dict[str, Any]:
    """
    Convert ScriptData to JSON-serializable dict.

    Useful for saving script.json.
    """
    return {
        "title": data.title,
        "metadata": {
            "total_duration": data.metadata.total_duration,
            "frame_count": data.metadata.frame_count,
            "word_count": data.metadata.word_count,
            "target_wps": data.metadata.target_wps
        },
        "frames": [
            _frame_to_dict(frame)
            for frame in data.frames
        ]
    }


def script_to_markdown(data: ScriptData) -> str:
    """
    Convert ScriptData to markdown format.

    Useful for generating human-readable script.md from JSON.
    """
    lines = [
        f"# Script: {data.title}",
        "",
        f"**Total Duration:** {data.metadata.total_duration}",
        f"**Frame Count:** {data.metadata.frame_count}",
        f"**Word Count:** {data.metadata.word_count:,} (target: {data.metadata.word_count:,} words at {data.metadata.target_wps}/sec)",
        "",
        "---",
        ""
    ]

    for frame in data.frames:
        lines.append(f"## Frame {frame.number} ({frame.timing_str}) • {frame.word_count} words")
        lines.append("")
        lines.append(frame.narration)
        lines.append("")
        lines.append(f"[Visual: {frame.visual.reference}]")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def save_script(data: ScriptData, video_dir: Path, write_json: bool = True, write_md: bool = True):
    """
    Save script data to files.

    Args:
        data: ScriptData to save
        video_dir: Path to Video-N directory
        write_json: Write script.json
        write_md: Write script.md
    """
    video_dir = Path(video_dir)

    if write_json:
        json_path = video_dir / "script.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(script_to_json(data), f, indent=2, ensure_ascii=False)

    if write_md:
        md_path = video_dir / "script.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(script_to_markdown(data))
