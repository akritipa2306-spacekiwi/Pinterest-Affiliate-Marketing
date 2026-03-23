#!/usr/bin/env python3
"""
Pinterest Affiliate Marketing — Image Generator
================================================
Reads generated_pins/latest.json (or a specified input file), generates a
styled Pinterest-ready image for each pin using the Google Gemini image
generation API (gemini-2.0-flash-preview-image-generation) via google-genai, and saves
results to generated_pins/latest_with_images.json.

Usage:
  export GOOGLE_API_KEY="your-key-here"

  python3 image_generator.py              # generate images for all pins
  python3 image_generator.py --count 3   # generate images for first 3 pins
  python3 image_generator.py --input generated_pins/2026-03-23.json

Outputs:
  generated_pins/images/YYYY-MM-DD_pin_{n}_{style_slug}.png
  generated_pins/latest_with_images.json
"""

import argparse
import json
import os
import random
import re
import sys
from datetime import datetime, timezone

from google import genai
from google.genai import types

# ── Load .env file if present ────────────────────────────────────────────────
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _val.strip())

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
PINS_DIR      = os.path.join(BASE_DIR, "generated_pins")
IMAGES_DIR    = os.path.join(PINS_DIR, "images")
DEFAULT_INPUT = os.path.join(PINS_DIR, "latest.json")
OUTPUT_FILE   = os.path.join(PINS_DIR, "latest_with_images.json")

GEMINI_MODEL  = "gemini-2.5-flash-image"

# Visual styles: each pin gets one, shuffled, no consecutive repeats.
VISUAL_STYLES = [
    {
        "name":       "modern sleek minimalist interior",
        "slug":       "minimalist",
        "descriptor": (
            "modern sleek minimalist interior, clean lines, neutral palette, "
            "editorial photography feel"
        ),
    },
    {
        "name":       "mid-century inspired",
        "slug":       "midcentury",
        "descriptor": (
            "mid-century inspired, warm wood tones, retro typography, "
            "vintage poster aesthetic"
        ),
    },
    {
        "name":       "cyberpunk / techno",
        "slug":       "cyberpunk",
        "descriptor": (
            "cyberpunk techno aesthetic, neon accents, dark backgrounds, "
            "futuristic product staging"
        ),
    },
    {
        "name":       "boho eclectic",
        "slug":       "boho",
        "descriptor": (
            "boho eclectic, natural textures, plants, woven materials, "
            "warm earthy tones"
        ),
    },
    {
        "name":       "japandi",
        "slug":       "japandi",
        "descriptor": (
            "japandi style, wabi-sabi, muted beiges, zen negative space"
        ),
    },
    {
        "name":       "maximalist cottagecore",
        "slug":       "cottagecore",
        "descriptor": (
            "maximalist cottagecore, floral patterns, cozy clutter, "
            "fairy-light ambiance"
        ),
    },
    {
        "name":       "dark academia",
        "slug":       "dark-academia",
        "descriptor": (
            "dark academia aesthetic, moody lighting, rich browns, "
            "book-lined shelves"
        ),
    },
    {
        "name":       "industrial loft",
        "slug":       "industrial",
        "descriptor": (
            "industrial loft aesthetic, exposed brick and concrete, "
            "Edison bulbs, metal accents"
        ),
    },
]

PROMPT_SUFFIX = (
    "photorealistic interior lifestyle photography, aspirational, "
    "cinematic lighting, high detail, sharp focus, "
    "no collage grids, no multi-panel photo grids, no decorative grid overlays, "
    "no text overlays, no watermarks, no logos, no price tags, "
    "no labels, no annotations, no callouts, no captions"
)


# ════════════════════════════════════════════════════════════════════════════
# STYLE ASSIGNMENT — shuffled, no consecutive repeats
# ════════════════════════════════════════════════════════════════════════════

def assign_styles(count):
    """Return a list of `count` styles, shuffled with no consecutive repeats."""
    styles = VISUAL_STYLES.copy()
    random.shuffle(styles)
    pool     = styles[:]
    assigned = []

    for _ in range(count):
        # Refill pool when exhausted
        if not pool:
            pool = styles[:]
            random.shuffle(pool)

        # Swap first two if next would repeat the last assigned style
        if assigned and pool[0]["slug"] == assigned[-1]["slug"] and len(pool) > 1:
            pool[0], pool[1] = pool[1], pool[0]

        assigned.append(pool.pop(0))

    return assigned


# ════════════════════════════════════════════════════════════════════════════
# IMAGE CONCEPT SANITIZER
# ════════════════════════════════════════════════════════════════════════════

# Triggers that indicate a multi-scene / collage concept
_MULTI_SCENE_RE = re.compile(
    r'\b(collage|photo[\s-]grid|flat\s+lay\s+or\s+grid|product\s+collage'
    r'|\d+[-–]\d+\s+different|\bshowing\s+\d+[-–]\d+\b)\b',
    re.IGNORECASE,
)

# Before/after splits are fine — preserve them (they produce great images)
_BEFORE_AFTER_RE = re.compile(r'\bbefore\b.{0,30}\bafter\b', re.IGNORECASE)

# Phrases to strip from collage concepts to extract the core subject
_STRIP_RES = [
    re.compile(r'(?i)(vertical\s+)?(collage|photo[\s-]grid|flat\s+lay\s+or\s+grid)\s+(showing|of)\s+(\d+[-–]\d+\s+different\s+)?'),
    re.compile(r'(?i)\bshowing\s+\d+[-–]\d+\s+[\w\s]+styles?\b'),
    re.compile(r'(?i),?\s*each\s+styled\s+with\b[^,\.]*'),
    re.compile(r'(?i),?\s*in\s+(real\s+)?apartment\s+settings?\b[^,\.]*'),
    # Remove orphaned "styles" left after stripping multi-scene framing
    re.compile(r'(?i)\bstyles?\s+(?=\w)'),
]


def sanitize_image_concept(concept):
    """
    Rewrite multi-panel / collage image concepts as a single cohesive scene.
    Before/after splits are preserved — they generate strong Pinterest images.
    """
    if _BEFORE_AFTER_RE.search(concept):
        return concept                      # before/after works well, keep it

    if not _MULTI_SCENE_RE.search(concept):
        return concept                      # already a single scene

    cleaned = concept
    for pattern in _STRIP_RES:
        cleaned = pattern.sub('', cleaned)

    cleaned = cleaned.strip().strip(',').strip()

    if len(cleaned) < 20:                   # stripping went too far, use original
        cleaned = concept

    # Ensure it reads as a single scene
    if not re.match(r'^(a |an |styled |single )', cleaned, re.IGNORECASE):
        cleaned = 'A single styled ' + cleaned[0].lower() + cleaned[1:]

    return cleaned


# ════════════════════════════════════════════════════════════════════════════
# GEMINI API HELPER
# ════════════════════════════════════════════════════════════════════════════

def build_prompt(image_concept, style):
    clean_concept = sanitize_image_concept(image_concept)
    return f"{clean_concept}, {style['descriptor']}, {PROMPT_SUFFIX}"


def generate_image_gemini(client, prompt):
    """
    Call the Gemini image generation model and return raw image bytes.
    Raises an exception if no image part is found in the response.
    """
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"]
        ),
    )

    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            return part.inline_data.data  # raw bytes

    raise RuntimeError("Gemini response contained no image data")


# ════════════════════════════════════════════════════════════════════════════
# MAIN GENERATION LOGIC
# ════════════════════════════════════════════════════════════════════════════

def generate_images(count=None, input_file=None):
    """
    Generate images for pins in `input_file`.
    `count` limits how many pins are processed (None = all).
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not set.")
        print("  Add it to .env or: export GOOGLE_API_KEY=your-key-here")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    if input_file is None:
        input_file = DEFAULT_INPUT

    if not os.path.exists(input_file):
        print(f"ERROR: Input file not found: {input_file}")
        print("  Run pin_generator.py first.")
        sys.exit(1)

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_pins = data.get("pins", [])
    if not all_pins:
        print("No pins found in input file.")
        sys.exit(1)

    pins_to_process = all_pins[:count] if count is not None else all_pins
    total = len(pins_to_process)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    os.makedirs(IMAGES_DIR, exist_ok=True)

    print("=" * 56)
    print("  IMAGE GENERATOR — Gemini Flash")
    print("=" * 56)
    print(f"\n  Processing {total} pin(s) from: {os.path.basename(input_file)}")
    print()

    styles  = assign_styles(total)
    updated = []

    for i, (pin, style) in enumerate(zip(pins_to_process, styles), 1):
        image_concept = pin.get("image_concept", "styled home office product photo")
        prompt   = build_prompt(image_concept, style)
        slug     = style["slug"]
        filename = f"{today}_pin_{i}_{slug}.png"
        filepath = os.path.join(IMAGES_DIR, filename)
        rel_path = os.path.join("generated_pins", "images", filename)

        print(f"  Pin {i}/{total} — style: {style['name']}", end=" — ", flush=True)

        pin = dict(pin)  # shallow copy so we don't mutate original
        try:
            image_bytes = generate_image_gemini(client, prompt)
            with open(filepath, "wb") as f:
                f.write(image_bytes)
            pin["generated_image"] = rel_path
            pin["image_style"]     = style["name"]
            print("✓ image saved")
        except Exception as exc:
            print(f"✗ error: {exc}")
            pin["generated_image"] = None
            pin["image_style"]     = style["name"]

        updated.append(pin)

    # Merge updated pins back (unprocessed pins keep their original data)
    merged = updated + list(all_pins[total:])

    output = dict(data)
    output["pins"]                = merged
    output["images_generated_at"] = datetime.now(timezone.utc).isoformat()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    success = sum(1 for p in updated if p.get("generated_image"))
    print(f"\n  {'─' * 54}")
    print(f"  ✓ {success}/{total} images generated successfully")
    print(f"  Saved to:  {OUTPUT_FILE}")
    print(f"  Images in: {IMAGES_DIR}")
    print()


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Pinterest Affiliate — Image Generator (Gemini Flash)"
    )
    parser.add_argument(
        "--count", type=int, default=None,
        help="Number of pins to generate images for (default: all)",
    )
    parser.add_argument(
        "--input", dest="input_file", default=None,
        help="Path to input JSON file (default: generated_pins/latest.json)",
    )
    args = parser.parse_args()
    generate_images(count=args.count, input_file=args.input_file)


if __name__ == "__main__":
    main()
