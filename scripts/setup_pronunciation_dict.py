#!/usr/bin/env python3
"""
One-time setup: create the bundled pronunciation dictionary on ElevenLabs.

The pipeline ships a math-narration pronunciation dictionary
(docs/elevenlabs_pronunciation_dict.pls — hyperbolic functions, Greek-letter
words, math abbreviations) that generate_tts_elevenlabs.py attaches to every
TTS request via ELEVENLABS_PRONUNCIATION_DICT_ID. This script uploads that
file to your ElevenLabs account and writes the resulting dictionary ID into
.env, so the dictionary is part of the default setup rather than an extra.

Add more entries to suit your production needs (proper nouns, domain terms,
recurring acronyms): edit the live dictionary via the ElevenLabs dashboard or
rules API — requests are keyed by dictionary ID only, so the LATEST version is
always used — and keep the .pls file in sync as the human-readable source of
truth.

Usage:
    python scripts/setup_pronunciation_dict.py            # create + write .env
    python scripts/setup_pronunciation_dict.py --no-env   # create + print ID only
    python scripts/setup_pronunciation_dict.py --force    # create even if an ID is already configured
"""

import argparse
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
PLS_PATH = PROJECT_ROOT / "docs" / "elevenlabs_pronunciation_dict.pls"
DICT_NAME = "Ludium Video math narration"
API_URL = "https://api.elevenlabs.io/v1/pronunciation-dictionaries/add-from-file"

load_dotenv(ENV_PATH)


def write_env_id(dict_id: str) -> str:
    """Set ELEVENLABS_PRONUNCIATION_DICT_ID in .env (replace or append)."""
    line = f"ELEVENLABS_PRONUNCIATION_DICT_ID={dict_id}"
    if not ENV_PATH.exists():
        ENV_PATH.write_text(line + "\n", encoding="utf-8")
        return "created .env"
    text = ENV_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"^#?\s*ELEVENLABS_PRONUNCIATION_DICT_ID=.*$", re.MULTILINE)
    if pattern.search(text):
        ENV_PATH.write_text(pattern.sub(line, text, count=1), encoding="utf-8")
        return "updated .env"
    if not text.endswith("\n"):
        text += "\n"
    ENV_PATH.write_text(text + line + "\n", encoding="utf-8")
    return "appended to .env"


def main():
    parser = argparse.ArgumentParser(
        description="Create the bundled pronunciation dictionary on ElevenLabs "
                    "and wire its ID into .env")
    parser.add_argument("--name", default=DICT_NAME,
                        help=f"Dictionary name (default: {DICT_NAME!r})")
    parser.add_argument("--no-env", action="store_true",
                        help="Print the ID instead of writing it to .env")
    parser.add_argument("--force", action="store_true",
                        help="Create a new dictionary even if "
                             "ELEVENLABS_PRONUNCIATION_DICT_ID is already set")
    args = parser.parse_args()

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("Error: ELEVENLABS_API_KEY not found in .env")
        sys.exit(1)

    existing = (os.getenv("ELEVENLABS_PRONUNCIATION_DICT_ID") or "").strip()
    if existing and not args.force:
        print(f"ELEVENLABS_PRONUNCIATION_DICT_ID is already set ({existing}) — nothing to do.")
        print("Pass --force to create a fresh dictionary anyway.")
        return

    if not PLS_PATH.exists():
        print(f"Error: {PLS_PATH} not found")
        sys.exit(1)

    print(f"Uploading {PLS_PATH.name} as {args.name!r} ...")
    with open(PLS_PATH, "rb") as fh:
        resp = requests.post(
            API_URL,
            headers={"xi-api-key": api_key},
            data={"name": args.name},
            files={"file": (PLS_PATH.name, fh, "application/xml")},
            timeout=60,
        )
    if resp.status_code != 200:
        print(f"Error: ElevenLabs returned {resp.status_code}: {resp.text[:500]}")
        sys.exit(1)

    data = resp.json()
    dict_id = data.get("id")
    if not dict_id:
        print(f"Error: no dictionary id in response: {data}")
        sys.exit(1)

    print(f"Created pronunciation dictionary: {dict_id} (version {data.get('version_id', '?')})")
    if args.no_env:
        print(f"Add to .env:  ELEVENLABS_PRONUNCIATION_DICT_ID={dict_id}")
    else:
        action = write_env_id(dict_id)
        print(f"ELEVENLABS_PRONUNCIATION_DICT_ID {action}.")
    print("Every TTS request now uses the dictionary's latest version — add entries "
          "for your own content (proper nouns, domain terms) via the dashboard or rules API.")


if __name__ == "__main__":
    main()
