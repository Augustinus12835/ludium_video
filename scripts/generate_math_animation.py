#!/usr/bin/env python3
"""
Math Animation Generator

Renders Manim-animated video clips for frames selected for animation —
step-by-step animated walkthroughs synced to narration.

Qualifying frames must have:
- math_verification.json with requires_verification: true
- Non-empty math_steps (mathematical or procedural)
- verification_status is not "error"

Uses full-screen whiteboard layout for animations.

Frame sources (frames/frame_N_manim.py) are authored by a Claude Code
subagent: render each frame's prompt with
`render_step_prompt.py manim --video-dir DIR --frame N` and save the code
before running this script — it only re-renders the authored sources.

Usage:
    python scripts/generate_math_animation.py pipeline/LECTURE/Video-N
    python scripts/generate_math_animation.py pipeline/LECTURE/Video-N --frame 2
    python scripts/generate_math_animation.py pipeline/LECTURE/Video-N --force
"""

import os
import sys
import json
import re
import argparse
import subprocess
import tempfile
import shutil
import difflib
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dotenv import load_dotenv

# Add parent to path for utils
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.utils.script_parser import load_script

# Load environment variables
project_root = Path(__file__).parent.parent
load_dotenv(project_root / '.env')


def get_audio_duration_ffprobe(audio_path: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        audio_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


def get_video_info(video_path: str) -> Dict:
    """Get video resolution and duration using ffprobe."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,r_frame_rate',
        '-show_entries', 'format=duration',
        '-of', 'json',
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    info = {}
    if data.get('streams'):
        info['width'] = data['streams'][0].get('width')
        info['height'] = data['streams'][0].get('height')
        info['fps'] = data['streams'][0].get('r_frame_rate')
    if data.get('format'):
        info['duration'] = float(data['format'].get('duration', 0))
    return info


def load_math_verification(video_folder: str) -> Optional[Dict]:
    """Load math_verification.json if it exists."""
    path = os.path.join(video_folder, "math_verification.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# Which modes need each optional section of the system prompt.
# Sections are delimited in the template by <!-- BEGIN SECTION: name --> /
# <!-- END SECTION: name --> markers. Unmarked content is shared by all modes.
PROMPT_SECTION_MODES = {
    "code": {"technical"},        # Code Block layout — only technical frames route here
}


def load_system_prompt(video_folder: str = "", mode: str = "math") -> str:
    """Load the Manim system prompt template, filtered to the pipeline mode.

    mode: "math" | "technical". Sections irrelevant to the mode (e.g. the Code
    Block layout for a pure-math frame) are stripped so each per-frame codegen
    prompt doesn't carry instructions it cannot use. video_folder is unused but
    kept for signature compatibility with callers.
    """
    prompt_path = Path(__file__).parent.parent / "templates" / "manim_system_prompt.md"
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt = f.read()

    for section, allowed_modes in PROMPT_SECTION_MODES.items():
        if mode not in allowed_modes:
            prompt = re.sub(
                rf"<!-- BEGIN SECTION: {section} -->.*?<!-- END SECTION: {section} -->\n?",
                "",
                prompt,
                flags=re.DOTALL,
            )

    # Strip any remaining section markers (kept sections)
    prompt = re.sub(r"<!-- (?:BEGIN|END) SECTION: \w+ -->\n?", "", prompt)
    prompt = re.sub(r"\n{4,}", "\n\n\n", prompt)

    return prompt


def get_qualifying_frames(video_folder: str, specific_frame: Optional[int] = None) -> List[int]:
    """
    Identify frames that qualify for Manim animation.

    A frame qualifies if it has:
    - requires_verification: true with non-empty math_steps, OR
    - frame_type: "visual" (no steps needed — Claude works from visual description)
    - verification_status is not "error"
    """
    math_data = load_math_verification(video_folder)
    if not math_data:
        return []

    qualifying = []
    for frame_key, frame_data in math_data.get("frames", {}).items():
        frame_num = int(frame_key)

        if specific_frame is not None and frame_num != specific_frame:
            continue

        # Visual frames always qualify (no steps needed)
        if frame_data.get("frame_type") == "visual":
            qualifying.append(frame_num)
            continue

        if not frame_data.get("requires_verification"):
            continue
        if frame_data.get("verification_status") == "error":
            continue
        # Code frames qualify on non-empty code_steps; math frames on math_steps.
        if frame_data.get("frame_type") == "code":
            if not frame_data.get("code_steps"):
                continue
        elif not frame_data.get("math_steps"):
            continue

        qualifying.append(frame_num)

    return sorted(qualifying)


def select_frames_for_animation(
    video_folder: str,
    math_data: dict,
    script_data,
    all_frames: bool = False
) -> Tuple[List[int], Dict[int, Dict]]:
    """
    Select which frames to animate.

    When all_frames=True (math/technical courses), animates every frame from the
    script, pulling math_steps and natural_narration from verification data when
    available.

    Otherwise, a frame is animated only if it has verified math steps.

    Returns (selected_frame_numbers, frame_info_map).
    """
    audio_dir = os.path.join(video_folder, 'audio')
    frame_info_map = {}

    if all_frames:
        # Math/technical courses: iterate over ALL script frames
        for frame in script_data.frames:
            frame_num = frame.number
            audio_path = os.path.join(audio_dir, f"frame_{frame_num}.mp3")
            if not os.path.exists(audio_path):
                continue
            duration = get_audio_duration_ffprobe(audio_path)

            # Get verification data if available
            v_data = math_data.get("frames", {}).get(str(frame_num), {})
            step_count = len(v_data.get("math_steps", []))
            frame_type = v_data.get("frame_type", "math")
            frame_info_map[frame_num] = {
                'duration': duration,
                'math_steps': step_count,
                'frame_type': frame_type,
                'narration': v_data.get("natural_narration", frame.narration),
            }
    else:
        for frame_key, frame_data in math_data.get("frames", {}).items():
            frame_num = int(frame_key)
            frame_type = frame_data.get("frame_type", "math")

            # Visual frames always qualify
            if frame_type != "visual":
                if not frame_data.get("requires_verification"):
                    continue
                if frame_data.get("verification_status") == "error":
                    continue
                if not frame_data.get("math_steps"):
                    continue

            # Measure audio duration
            audio_path = os.path.join(audio_dir, f"frame_{frame_num}.mp3")
            if not os.path.exists(audio_path):
                continue
            duration = get_audio_duration_ffprobe(audio_path)

            step_count = len(frame_data.get("math_steps", []))
            frame_info_map[frame_num] = {
                'duration': duration,
                'math_steps': step_count,
                'frame_type': frame_type,
                'narration': frame_data.get("natural_narration", ""),
            }

    # Select all qualifying frames
    candidates = list(frame_info_map.keys())

    return sorted(candidates), frame_info_map


def load_stored_word_timestamps(audio_dir: str, frame_num: int) -> Optional[List[Dict]]:
    """
    Load word timestamps saved by generate_tts_elevenlabs.py at synthesis time
    (audio/frame_N_timestamps.json). These are exact — they come from the TTS
    engine itself — so no Scribe re-transcription is needed.

    Returns list of {'word','start','end'} or None (caller falls back to Scribe).
    """
    path = os.path.join(audio_dir, f"frame_{frame_num}_timestamps.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None
    words = data.get('words') or []
    return words if words else None


def transcribe_with_scribe(audio_path: str) -> List[Dict]:
    """
    Transcribe audio with ElevenLabs Scribe to get word-level timestamps.

    Returns list of word dicts: [{'word': str, 'start': float, 'end': float}, ...]
    """
    from scripts.utils.stt import transcribe

    # Scribe word timestamps are already in seconds
    return transcribe(audio_path)['words']


def _normalize(word: str) -> str:
    """Normalize a word for comparison: lowercase, strip punctuation."""
    return word.lower().strip(".,;:!?\"'()-\u2013\u2014")


def align_words_to_script(script_text: str, stt_words: List[Dict]) -> List[Dict]:
    """
    Align known script text (ground truth) to transcribed word timestamps
    using sequence matching (difflib).

    Handles insertions/deletions gracefully \u2014 only mismatched regions
    use interpolation, while correctly-matched words keep exact timestamps.

    Returns list of {word, start, end} with corrected text.
    """
    script_words = script_text.replace('\n', ' ').split()

    if not stt_words or not script_words:
        return []

    script_normalized = [_normalize(w) for w in script_words]
    stt_normalized = [_normalize(w['word']) for w in stt_words]

    matcher = difflib.SequenceMatcher(None, script_normalized, stt_normalized, autojunk=False)
    opcodes = matcher.get_opcodes()

    aligned = []
    for tag, s_start, s_end, a_start, a_end in opcodes:
        s_count = s_end - s_start
        a_count = a_end - a_start

        if tag == 'equal':
            for i in range(s_count):
                aligned.append({
                    'word': script_words[s_start + i],
                    'start': round(stt_words[a_start + i]['start'], 2),
                    'end': round(stt_words[a_start + i]['end'], 2)
                })

        elif tag == 'replace':
            time_start = stt_words[a_start]['start']
            time_end = stt_words[a_end - 1]['end']
            time_per_word = (time_end - time_start) / s_count if s_count > 0 else 0

            t = time_start
            for i in range(s_count):
                aligned.append({
                    'word': script_words[s_start + i],
                    'start': round(t, 2),
                    'end': round(t + time_per_word, 2)
                })
                t += time_per_word

        elif tag == 'delete':
            # Script words the transcriber didn't detect — interpolate timestamps
            if aligned:
                time_start = aligned[-1]['end']
            elif a_end < len(stt_words):
                time_start = max(0, stt_words[a_end]['start'] - s_count * 0.3)
            else:
                time_start = 0.0

            if a_end < len(stt_words):
                time_end = stt_words[a_end]['start']
            elif aligned:
                time_end = time_start + s_count * 0.3
            else:
                time_end = s_count * 0.3

            time_per_word = (time_end - time_start) / s_count if s_count > 0 else 0
            t = time_start
            for i in range(s_count):
                aligned.append({
                    'word': script_words[s_start + i],
                    'start': round(t, 2),
                    'end': round(t + time_per_word, 2)
                })
                t += time_per_word

        elif tag == 'insert':
            # Transcribed words not in script — skip
            pass

    return aligned


def format_word_transcript(aligned_words: List[Dict]) -> str:
    """
    Format aligned word timestamps into a compact transcript for Claude.

    Groups words into ~5-word chunks with the start time of the first word,
    keeping the prompt concise while giving Claude precise timing.
    """
    if not aligned_words:
        return "(no transcript available)"

    lines = []
    chunk_size = 5
    for i in range(0, len(aligned_words), chunk_size):
        chunk = aligned_words[i:i + chunk_size]
        time = chunk[0]['start']
        words = ' '.join(w['word'] for w in chunk)
        lines.append(f"[{time:6.2f}s] {words}")

    return '\n'.join(lines)


def build_claude_prompt(
    narration: str,
    math_steps: List[Dict],
    visual_desc: str,
    total_duration: float,
    frame_number: int,
    word_transcript: str,
    prior_context: str = "",
    code_steps: List[Dict] = None,
    color_plan: Dict = None,
) -> str:
    """Build the user prompt for Claude to generate Manim code.

    For math frames: uses math_steps with LaTeX expressions.
    For code frames: uses code_steps + the Code Block layout (full block on
        screen at frame entry, optional per-line highlight when narration
        discusses a specific line).
    For visual frames: works directly from narration + visual description.

    color_plan: the video-level semantic color plan from math_verification.json
        (math/technical) — injected as a mandatory VIDEO COLOR PLAN block so
        recurring quantities keep one color across every frame of the video.
    """
    prior_context_section = ""
    if prior_context:
        prior_context_section = (
            f"\nPRIOR FRAMES CONTEXT (what the viewer has already seen -- do NOT re-animate these):\n"
            f"{prior_context}\n"
        )

    color_plan_section = ""
    if color_plan:
        plan_lines = []
        for name, spec in color_plan.items():
            tex_forms = ", ".join(f"`{t}`" for t in spec.get("tex", []))
            words = ", ".join(f'"{w}"' for w in spec.get("note_words", []))
            plan_lines.append(
                f"- {name} → {spec.get('color', '?')} — tex forms: {tex_forms or '(none)'};"
                f" note words: {words or '(none)'}")
        color_plan_section = (
            "\nVIDEO COLOR PLAN (video-wide semantic color assignments — MANDATORY; see the "
            "system prompt's \"Semantic color linking\" rules):\n"
            + "\n".join(plan_lines) + "\n"
            "Apply on this frame: every listed tex form appearing in a step goes in that "
            "step's `t2c=` with its plan color; the same quantity drawn on a graph/diagram "
            "gets `.set_color(<plan color>)`; every listed note word appearing in a step's "
            "label goes in `label_t2c=` with the same color. One color = one quantity, never "
            "reassigned. If more than ~3 plan quantities appear on this frame, color the 3 "
            "most central and leave the rest default white/yellow.\n"
        )

    if code_steps:
        # Code frame — Code Block layout. Full block on screen at frame entry,
        # syntax-highlighted, indented, NO fading. Optional per-line highlight
        # when narration discusses a specific line (matched via highlight_when
        # against the word-timestamp transcript).
        lines_text = ""
        for step in code_steps:
            expr = step.get("expression", "")
            op = step.get("operation", "")
            hw = step.get("highlight_when", "")
            line = f"line {step.get('step', '?')}: `{expr}`"
            if op:
                line += f"  — {op}"
            if hw:
                line += f"  [highlight when narrator says: \"{hw}\"]"
            lines_text += line + "\n"

        return f"""Generate a Manim Scene class `MathAnimation` for this CODE BLOCK frame.

FRAME NUMBER: {frame_number}
TOTAL DURATION: {total_duration:.1f} seconds (animation must fill this exactly)
{prior_context_section}
NARRATION (what the speaker says during this animation):
{narration}

CODE LINES (in display order — preserve indentation exactly):
{lines_text}{color_plan_section}
WORD-LEVEL TRANSCRIPT (ground-truth text with precise timestamps):
{word_transcript}

LAYOUT — Code Block (mandatory for code frames):

1. Render the code as a SINGLE Manim `Code()` mobject covering the safe area.
   Example:
       code_string = (
           "for i in range(len(nums)):\\n"
           "    total += nums[i]\\n"
       )
       code = Code(
           code_string=code_string,
           language="python",
           formatter_style="monokai",
           background="window",
           paragraph_config={{"font": "Monospace", "font_size": 32}},
       )
       code.move_to(ORIGIN)
       self.play(FadeIn(code), run_time=1.0)
2. **No fading, no dimming, no scrolling.** All lines stay at full opacity from
   frame entry to frame end. The block sits still while narration plays over it.
3. **Optional typewriter reveal** for the "instructor is typing" feel: lines fade in
   one-at-a-time as the narrator first references each. Use the highlight_when
   phrase in each step to find the timestamp in the word transcript, and reveal
   the line at that moment. Once a line is on screen it stays. If most lines have
   no `highlight_when`, show the whole block at frame entry instead.
4. **Per-line highlight** when narration discusses a specific line: wrap a soft
   `SurroundingRectangle(line, color=YELLOW, buff=0.1, stroke_width=2)` for
   ~2 seconds, then `FadeOut` the rectangle. Other lines are untouched. To get
   a single line's mobject from a `Code()`, use `code.code_lines[i]` (zero-indexed
   matching the step number minus 1).
5. **Caption above the block (optional)**: a short Tex() title (≤ 4 words) above
   the code, describing the function's purpose. Skip if narration is self-evident.
6. Scale the Code mobject if needed to fit the safe zone (x ∈ [−6.5, 6.5],
   y ∈ [−3.7, 3.7]). For ~10 lines at font_size=32, the natural size fits.
   For longer blocks, drop font_size to 26 or 24 before scaling.

REQUIREMENTS:
1. Class must be named `MathAnimation` extending `Scene`.
2. Total animation duration must be {total_duration:.1f}s (sum of run_time + wait calls).
3. Code lines must appear in the order given. Preserve indentation literally.
4. NO `add_step` / scrolling whiteboard pattern. That's for math, not code.
5. NO step-by-step build of lines unless you're using the typewriter reveal anchored
   to highlight_when timestamps. Default to full block at frame entry.
6. Use color scheme: dark bg (#000000), accent YELLOW (#FACC15) for line highlights,
   BLUE / GREEN / ORANGE / RED_C for callouts as in other technical frames.
7. Return ONLY the Python code, no explanation.

Return the complete Python code starting with `from manim import *`."""

    elif not math_steps:
        # Visual frame — Claude works directly from narration + visual description
        return f"""Generate a Manim Scene class `MathAnimation` for this visual frame.

FRAME NUMBER: {frame_number}
TOTAL DURATION: {total_duration:.1f} seconds (animation must fill this exactly)
{prior_context_section}
NARRATION (what the speaker says during this animation):
{narration}

VISUAL DESCRIPTION (what to animate — design your own layout):
{visual_desc}
{color_plan_section}
WORD-LEVEL TRANSCRIPT (ground-truth text with precise timestamps):
{word_transcript}

REQUIREMENTS:
1. The Scene class must be named `MathAnimation`
2. Total animation duration must be {total_duration:.1f}s (sum of all run_time + wait calls)
3. **Full canvas** — use the entire screen, no rigid split panels unless the content calls for it
4. **Use `Tex()` for all text labels**, `MathTex()` for math expressions. NOT `Text()`.
   - Titles: `Tex(r"Title Here").scale(1.0)`
   - Labels: `Tex(r"Label").scale(0.7)` (minimum)
   - Multi-line: `Tex(r"Line one \\\\ Line two").scale(0.7)`
   - Math: `MathTex(r"\\frac{{a}}{{b}}").scale(0.75)` (minimum)
5. **Large readable text** — Tex scale ≥0.7 for labels, 1.0 for titles
6. **Progressive reveal** synced to narration — read the transcript, reveal each element when the narrator introduces it
7. Start content within 1-2 seconds — NEVER have dead time with just a title card
8. Use the color scheme: dark bg (#000000), BLUE (#3B82F6) for primary elements, ORANGE (#F97316) for highlights, GREEN (#22C55E) for results, YELLOW (#FACC15) for labels, RED (#EF4444) for warnings
9. Add `add_background_rectangle(color="#000000", opacity=0.85, buff=0.08)` on labels that overlap other elements
10. Use `make_box()` helper for labeled boxes (see system prompt — it uses `Tex()`)
11. Return ONLY the Python code, no explanation

Return the complete Python code starting with `from manim import *`."""

    else:
        # Math frame — original prompt
        steps_text = ""
        for step in math_steps:
            steps_text += (
                f"step {step['step']}: "
                f"`{step['expression']}` — {step['operation']}"
            )
            if step.get('note'):
                steps_text += f" ({step['note']})"
            steps_text += "\n"

        return f"""Generate a Manim Scene class `MathAnimation` for this math frame.

FRAME NUMBER: {frame_number}
TOTAL DURATION: {total_duration:.1f} seconds (animation must fill this exactly)
{prior_context_section}
NARRATION (what the speaker says during this animation):
{narration}

VISUAL DESCRIPTION (script author's suggestion — use as a starting point):
{visual_desc}

The visual description above is the script author's suggestion for how to present this content. Use it as a starting point — you may follow it closely, adapt it, or design your own approach if a different layout better serves the content. Use the `make_step_column()` factory and building blocks from the system prompt.

STEPS (in order — YOU decide when each appears based on the transcript):
{steps_text}

These steps may be mathematical expressions (LaTeX), procedural labels, timeline entries, or graph descriptions. Use the appropriate Manim objects:
- LaTeX expressions → MathTex()
- Procedural labels → Tex() (e.g., Tex(r"Step 1: Setup", color=YELLOW).scale(0.5))
- Timelines/flows → Tex() labels with connecting arrows
- Graphs → Axes + plot
{color_plan_section}
WORD-LEVEL TRANSCRIPT (ground-truth text with precise timestamps):
{word_transcript}

REQUIREMENTS:
1. The Scene class must be named `MathAnimation`
2. Total animation duration must be {total_duration:.1f}s (sum of all run_time + wait calls)
3. **Plan your animation**: Before writing code, think about what layout and visual approach will best serve this content. Consider the narration flow, the math steps, and what would be clearest for the viewer. Use `make_step_column()` for scrolling step regions.
4. Read the word-level transcript carefully. Each step's animation should BEGIN when the narrator starts introducing that concept — find the words in the transcript that correspond to each step
5. NEVER have dead time with just a title card — start showing content within the first 1-2 seconds
6. Use the color scheme from the system prompt (dark bg, blue math, orange highlights, green answer)
7. The final step/answer should remain visible until the end
8. Use the `make_step_column()` factory from the system prompt for sequential derivation steps. For procedural steps, you can still use `add_step()` with Tex() labels instead of MathTex.
9. **Graph drawing**: If the VISUAL DESCRIPTION mentions specific functions, graphs, curves, holes, asymptotes, or any plotted shapes, you MUST draw them on axes. Plot the actual functions described — don't skip them in favor of pure algebra. The graph is the visual payoff; the algebraic steps support it.
10. Return ONLY the Python code, no explanation

Return the complete Python code starting with `from manim import *`."""


def render_manim_scene(
    scene_code: str,
    output_path: str,
    total_duration: float,
    label: str = ""
) -> Tuple[bool, str]:
    """
    Write Manim code to a temp file, render it, and copy output.

    label: optional tag included in progress prints (renders may run in
    parallel, so output lines need to identify their frame).

    Returns (success, message).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy any referenced images/SVGs into the temp dir so Manim can find them.
        # Scan for ImageMobject("...") and SVGMobject("...") paths.
        import re as _re
        for pattern in [r'ImageMobject\(["\'](.+?)["\']\)', r'SVGMobject\(["\'](.+?)["\']\)']:
            for match in _re.finditer(pattern, scene_code):
                file_path = match.group(1)
                if os.path.isabs(file_path) and os.path.exists(file_path):
                    local_name = os.path.basename(file_path)
                    shutil.copy2(file_path, os.path.join(tmpdir, local_name))
                    scene_code = scene_code.replace(file_path, local_name)


        # Manim's default tex template loads only amsmath/amssymb under OT1 font
        # encoding. Code/technical frames routinely reach for text symbols it lacks
        # — \textquotedbl (double quotes in code strings), \textbackslash,
        # \texttrademark, \textdegree — each of which fails "latex error converting
        # to dvi". T1 fontenc (cm-super provides the fonts) makes \textquotedbl et al.
        # available, and textcomp defines the rest. Inject both once for every render.
        if "textcomp" not in scene_code:
            scene_code = scene_code.replace(
                "from manim import *",
                'from manim import *\n'
                'config.tex_template.add_to_preamble('
                'r"\\usepackage[T1]{fontenc}" + "\\n" + r"\\usepackage{textcomp}")',
                1,
            )

        scene_file = os.path.join(tmpdir, "scene.py")
        with open(scene_file, 'w', encoding='utf-8') as f:
            f.write(scene_code)

        # Render with Manim
        cmd = [
            sys.executable, '-m', 'manim', 'render',
            '-r', '3840,2160',
            '--fps', '30',
            '--format', 'mp4',
            '-o', 'output.mp4',
            '--media_dir', os.path.join(tmpdir, 'media'),
            scene_file,
            'MathAnimation'
        ]

        # Render ceiling. Dense/long frames at 4K (3840x2160) can exceed the old
        # 900s default; raise via MANIM_RENDER_TIMEOUT (seconds) when needed.
        render_timeout = int(os.environ.get("MANIM_RENDER_TIMEOUT", "1800"))
        tag = f" [{label}]" if label else ""
        print(f"      Rendering Manim scene{tag}... (timeout {render_timeout}s)")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=tmpdir,
            timeout=render_timeout
        )

        if result.returncode != 0:
            return False, f"Manim render failed:\n{result.stderr[-2000:]}"

        # Find the output file
        rendered = None
        for root, dirs, files in os.walk(os.path.join(tmpdir, 'media')):
            for f in files:
                if f.endswith('.mp4'):
                    rendered = os.path.join(root, f)
                    break
            if rendered:
                break

        if not rendered or not os.path.exists(rendered):
            return False, "Manim produced no output file"

        # Validate output
        info = get_video_info(rendered)
        if not info.get('duration'):
            return False, "Could not read duration of rendered video"

        duration_diff = abs(info['duration'] - total_duration)
        if duration_diff > 2.0:
            print(f"      Warning: Duration mismatch: {info['duration']:.1f}s vs expected {total_duration:.1f}s (diff: {duration_diff:.1f}s)")

        width = info.get('width', 0)
        height = info.get('height', 0)
        if width != 1920 or height != 1080:
            print(f"      Warning: Resolution {width}x{height}, expected 1920x1080")

        # Copy to output
        shutil.copy2(rendered, output_path)
        return True, f"Rendered {info['duration']:.1f}s video ({width}x{height})"


def prepare_frame_code(
    video_folder: str,
    frame_num: int,
    math_data: Dict,
    script_data,
    force: bool = False,
) -> Tuple[bool, str, Optional[Dict]]:
    """
    Phase 1 for a single frame: load the subagent-authored frame_N_manim.py
    and return a render job. Frame sources are authored via
    `render_step_prompt.py manim --video-dir DIR --frame N` — a missing
    source is an error.

    Returns (success, message, render_job_or_None). render_job is None when
    the frame is skipped (mp4 exists) or on failure.
    """
    frames_dir = os.path.join(video_folder, 'frames')
    audio_dir = os.path.join(video_folder, 'audio')
    output_path = os.path.join(frames_dir, f"frame_{frame_num}.mp4")

    # Skip if already exists (unless force)
    if os.path.exists(output_path) and not force:
        return True, f"Frame {frame_num}: Skipped (frame_{frame_num}.mp4 exists)", None

    # Get frame data
    frame_info = math_data.get("frames", {}).get(str(frame_num), {})
    frame_type = frame_info.get("frame_type", "math")
    math_steps = frame_info.get("math_steps", [])
    code_steps = frame_info.get("code_steps", [])

    # Get audio duration
    audio_path = os.path.join(audio_dir, f"frame_{frame_num}.mp3")
    if not os.path.exists(audio_path):
        return False, f"Frame {frame_num}: Missing audio file", None

    total_duration = get_audio_duration_ffprobe(audio_path)
    if frame_type == "visual":
        print(f"\n    Frame {frame_num}: visual frame, {total_duration:.1f}s audio")
    elif frame_type == "code":
        print(f"\n    Frame {frame_num}: code frame ({len(code_steps)} lines), {total_duration:.1f}s audio")
    else:
        print(f"\n    Frame {frame_num}: {len(math_steps)} math steps, {total_duration:.1f}s audio")

    code_path = os.path.join(frames_dir, f"frame_{frame_num}_manim.py")
    if not os.path.exists(code_path):
        return False, (
            f"Frame {frame_num}: frames/frame_{frame_num}_manim.py missing — author it "
            f"via a Claude Code subagent first: python scripts/render_step_prompt.py "
            f"manim --video-dir {video_folder} --frame {frame_num}"
        ), None

    print(f"    [1/2] Using frame_{frame_num}_manim.py")
    with open(code_path, 'r', encoding='utf-8') as f:
        scene_code = f.read()

    job = {
        'frame_num': frame_num,
        'scene_code': scene_code,
        'output_path': output_path,
        'total_duration': total_duration,
    }
    return True, f"Frame {frame_num}: code ready", job


def check_color_links(video_folder: str, quiet: bool = False) -> List[str]:
    """Deterministic missed-color-link lint (WARNING-level, never blocks).

    Two checks over the generated frame_N_manim.py sources (math/technical):
      1. Color-plan coverage — every math_verification.json `color_plan` quantity
         whose tex form appears in a frame's math/code steps must have its plan
         color somewhere in that frame's code.
      2. Generic missed link — a frame that draws a graph (Axes/NumberPlane/plot)
         over ≥3 math steps with zero non-empty t2c/label_t2c/glyph_colors maps
         is flagged as a likely missed graph↔text link.

    Returns the warning strings (also printed unless quiet). Fix by adding the
    missing t2c/label_t2c/.set_color links and re-rendering the frame.
    """
    warnings: List[str] = []
    math_data = load_math_verification(str(video_folder)) or {}
    plan = math_data.get("color_plan") or {}
    frames_dir = Path(video_folder) / "frames"

    def norm(s: str) -> str:
        return re.sub(r"[\s\\{}()]", "", s or "")

    for py_path in sorted(frames_dir.glob("frame_*_manim.py"),
                          key=lambda p: int(re.search(r"frame_(\d+)_", p.name).group(1))):
        n = re.search(r"frame_(\d+)_", py_path.name).group(1)
        entry = math_data.get("frames", {}).get(n, {})
        steps = entry.get("math_steps") or entry.get("code_steps") or []
        if not steps:
            continue
        code = py_path.read_text(encoding="utf-8")
        # Every scene copies the standard color-constant block, so a bare name
        # match always hits. Only count USES: drop definition lines (X = "#...")
        # and comments before searching for the color name.
        code_uses = "\n".join(
            line for line in code.splitlines()
            if not re.match(r"\s*[A-Z_]+\s*=\s*[\"']#", line)
            and not line.lstrip().startswith("#"))
        steps_text = norm(" ".join(
            f"{s.get('expression', '')} {s.get('operation', '')} {s.get('note', '')}"
            for s in steps))

        plan_hit = False
        for name, spec in plan.items():
            forms = [norm(t) for t in spec.get("tex", []) if norm(t)]
            present = [t for t in forms if t in steps_text]
            if not present:
                continue
            plan_hit = True
            color = spec.get("color", "")
            if color and color not in code_uses:
                warnings.append(
                    f"frame {n}: plan quantity '{name}' ({color}) appears in the steps "
                    f"(e.g. {spec.get('tex', ['?'])[0]}) but {color} never appears in the "
                    f"code — likely missed color link")

        # Generic check only when the plan gave this frame nothing to check
        if not plan_hit and len(steps) >= 3 and entry.get("frame_type", "math") == "math":
            has_graph = re.search(r"\b(Axes|NumberPlane)\s*\(|\.plot\s*\(", code)
            has_link = re.search(
                r"(?:\blabel_t2c|\bt2c|\bglyph_colors)\s*=\s*\{\s*[\"'r\d]", code)
            if has_graph and not has_link:
                warnings.append(
                    f"frame {n}: draws a graph over {len(steps)} steps with no "
                    f"t2c/label_t2c/glyph_colors anywhere — likely missed graph↔text link")

    if warnings and not quiet:
        print(f"\n  Color-link check ({Path(video_folder).name}) — "
              f"{len(warnings)} warning(s), non-blocking:")
        for w in warnings:
            print(f"    ⚠ {w}")
    elif not quiet:
        print(f"\n  Color-link check: OK")
    return warnings


def render_frame_job(job: Dict) -> Tuple[int, bool, str]:
    """Phase 2: render one prepared frame. Returns (frame_num, success, message)."""
    frame_num = job['frame_num']
    success, message = render_manim_scene(
        job['scene_code'], job['output_path'], job['total_duration'],
        label=f"frame {frame_num}",
    )

    if success:
        return frame_num, True, f"Frame {frame_num}: {message}"
    else:
        # Clean up failed output
        if os.path.exists(job['output_path']):
            os.remove(job['output_path'])
        return frame_num, False, f"Frame {frame_num}: {message}"


def main():
    parser = argparse.ArgumentParser(
        description='Generate Manim math animations for qualifying frames'
    )
    parser.add_argument('video_folder', help='Path to Video-N folder')
    parser.add_argument('--frame', type=int, help='Process specific frame number only')
    parser.add_argument('--force', action='store_true',
                        help='Re-render even if .mp4 exists (from the existing '
                             'frame_N_manim.py — edit the source to change a frame)')
    parser.add_argument('--math', action='store_true',
                        help='Force math mode (animate ALL frames)')
    parser.add_argument('--technical', action='store_true',
                        help='Force technical mode (animate ALL frames, supports conceptual)')

    args = parser.parse_args()

    # Resolve path
    video_folder = args.video_folder
    if not os.path.isabs(video_folder):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        video_folder = os.path.join(base_dir, video_folder)

    if not os.path.exists(video_folder):
        print(f"Error: Video folder not found: {video_folder}")
        sys.exit(1)

    # Detect math/technical course
    MATH_PREFIXES = ("Calculus_", "Linear_Algebra_", "Statistics_",
                     "Probability_", "Differential_Equations_")
    lecture_name = Path(video_folder).parent.name
    is_math_course = args.math or any(lecture_name.startswith(p) for p in MATH_PREFIXES)
    is_technical = args.technical and not is_math_course

    print("=" * 70)
    print("MANIM MATH ANIMATION GENERATOR")
    print("=" * 70)
    print(f"Video: {video_folder}")
    print(f"Frame: {args.frame or 'all qualifying'}")
    print(f"Force: {args.force}")
    if is_math_course:
        print(f"Mode: All-Manim (math course)")
    elif is_technical:
        print(f"Mode: All-Manim (technical course)")

    # Load math verification data
    math_data = load_math_verification(video_folder)
    if not math_data:
        print("\nNo math_verification.json found. Nothing to animate.")
        sys.exit(0)

    # Load script data
    script_data = load_script(Path(video_folder))

    # Select frames for animation
    if args.frame is not None:
        # Manual override — validate the specific frame qualifies
        qualifying = get_qualifying_frames(video_folder, args.frame)
        if not qualifying:
            print(f"\nFrame {args.frame} does not qualify (needs verification + math_steps).")
            sys.exit(0)
        frame_info_map = {}
    else:
        # Smart selection based on audio duration
        qualifying, frame_info_map = select_frames_for_animation(
            video_folder, math_data, script_data,
            all_frames=is_math_course or is_technical
        )

        # Print selection reasoning
        if frame_info_map:
            print(f"\n  Frame selection:")
            for frame_num in sorted(frame_info_map.keys()):
                info = frame_info_map[frame_num]
                tag = "ANIMATE" if frame_num in qualifying else "skip"
                ftype = info.get('frame_type', 'math')
                if ftype == "visual":
                    print(f"    Frame {frame_num}: {info['duration']:.0f}s, visual → {tag}")
                else:
                    print(f"    Frame {frame_num}: {info['duration']:.0f}s, "
                          f"{info['math_steps']} steps → {tag}")
            print(f"\n  Animating {len(qualifying)} of {len(frame_info_map)} frames")

    if not qualifying:
        print("\nNo frames selected for animation.")
        # Write marker even when 0 frames animated (selection decided none needed)
        marker = os.path.join(video_folder, 'frames', '.animate_done')
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        Path(marker).touch()
        sys.exit(0)

    print(f"\nSelected frames: {qualifying}")

    # PHASE 1: load the subagent-authored frame_N_manim.py sources
    # (`render_step_prompt.py manim` renders each frame's authoring prompt).
    print(f"\n{'─' * 70}")
    print("PHASE 1: Frame sources")
    print(f"{'─' * 70}")

    results = []
    render_jobs = []
    for frame_num in qualifying:
        try:
            success, message, job = prepare_frame_code(
                video_folder, frame_num, math_data, script_data, args.force,
            )
            if job is not None:
                render_jobs.append(job)
            else:
                results.append((frame_num, success, message))
        except Exception as e:
            print(f"\n    Frame {frame_num}: EXCEPTION - {e}")
            import traceback
            traceback.print_exc()
            results.append((frame_num, False, f"Frame {frame_num}: Exception - {e}"))

    # PHASE 2: render all prepared frames (parallelizable — renders are
    # independent subprocesses; MANIM_RENDER_JOBS controls concurrency).
    if render_jobs:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_jobs = max(1, int(os.environ.get("MANIM_RENDER_JOBS", "2")))
        print(f"\n{'─' * 70}")
        print(f"PHASE 2: Rendering {len(render_jobs)} frame(s) "
              f"({max_jobs} parallel job(s); set MANIM_RENDER_JOBS to change)")
        print(f"{'─' * 70}")

        with ThreadPoolExecutor(max_workers=max_jobs) as executor:
            futures = {executor.submit(render_frame_job, job): job for job in render_jobs}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    frame_num, success, message = future.result()
                except Exception as e:
                    frame_num = job['frame_num']
                    success, message = False, f"Frame {frame_num}: Exception - {e}"
                status = "OK" if success else "FAILED"
                print(f"    Frame {frame_num}: render {status}")
                results.append((frame_num, success, message))

    results.sort(key=lambda r: r[0])

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    success_count = 0
    fail_count = 0
    skip_count = 0

    for frame_num, success, message in results:
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

    print(f"\nAnimated: {success_count}")
    print(f"Skipped: {skip_count}")
    print(f"Failed: {fail_count}")

    # Semantic color-link lint (math/technical only; warning-level, non-blocking)
    if is_math_course or is_technical:
        try:
            check_color_links(video_folder)
        except Exception as e:
            print(f"\n  Color-link check errored (non-blocking): {e}")

    # Write completion marker (even with skips — the selection was made)
    if fail_count == 0:
        marker = os.path.join(video_folder, 'frames', '.animate_done')
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        Path(marker).touch()

    if fail_count > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
