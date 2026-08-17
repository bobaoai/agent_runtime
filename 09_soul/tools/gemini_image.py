#!/usr/bin/env python3
"""
Gemini image generation and upscaling CLI.

Generate images from text prompts, edit images with text+image input,
or upscale existing images to higher resolution.

Usage:
    # Text-to-image (1K default)
    python tools/gemini_image.py -p "A cat on a cloud" -o cat.jpg

    # Image editing (text + image input)
    python tools/gemini_image.py -p "Remove watermark" -i photo.jpg -o clean.jpg

    # Upscale to 4K
    python tools/gemini_image.py --upscale -i small.jpg -o big.jpg
"""

from typing import Optional, List
from pathlib import Path
import argparse
import mimetypes
import os
import subprocess
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load .env from workspace root (two levels up from tools/)
_SCRIPT_DIR = Path(__file__).parent
_WORKSPACE_ROOT = _SCRIPT_DIR.parent
_ENV_PATH = _WORKSPACE_ROOT / ".env"
load_dotenv(_ENV_PATH)


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------

def _get_api_key_from_1password(
    vault: str = "dev",
    item: str = "dev-api-keys",
    field: str = "gemini_api_key",
) -> Optional[str]:
    """Read API key from 1Password CLI."""
    try:
        result = subprocess.run(
            ["op", "read", f"op://{vault}/{item}/{field}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _get_api_key() -> str:
    """Get API key: env vars -> 1Password CLI. Exit on failure."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if api_key:
        return api_key

    api_key = _get_api_key_from_1password()
    if api_key:
        return api_key

    print("Error: Gemini API key not found. Set GEMINI_API_KEY env var "
          "or configure 1Password CLI.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------

def _get_generation_model() -> str:
    return os.environ.get(
        "GEMINI_IMAGE_GENERATION_MODEL", "gemini-3.1-flash-image-preview"
    )


def _get_upscale_model() -> str:
    return os.environ.get(
        "GEMINI_IMAGE_UPSCALE_MODEL", "gemini-3-pro-image-preview"
    )


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _save_binary(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
    print(f"Saved: {path}")


def _convert_to_jpeg(source: str, target: str) -> bool:
    """Convert image to JPEG using macOS sips."""
    result = subprocess.run(
        ["sips", "-s", "format", "jpeg", source, "--out", target],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"JPEG conversion failed: {result.stderr}", file=sys.stderr)
        return False
    return True


def _save_image_part(inline_data, output_path: str) -> bool:
    """Save an inline_data part to output_path as JPEG."""
    if not (inline_data and inline_data.data):
        return False

    ext = mimetypes.guess_extension(inline_data.mime_type or "image/png") or ".png"

    if ext in {".jpg", ".jpeg"}:
        _save_binary(output_path, inline_data.data)
        return True

    # Save as original format, then convert to JPEG
    temp_path = f"{output_path}{ext}"
    _save_binary(temp_path, inline_data.data)
    success = _convert_to_jpeg(temp_path, output_path)
    if success:
        Path(temp_path).unlink(missing_ok=True)
    return success


# ---------------------------------------------------------------------------
# Core functions (importable)
# ---------------------------------------------------------------------------

def generate(
    prompt: str,
    image_paths: Optional[List[str]] = None,
    output_prefix: str = "output",
    image_size: str = "1K",
    aspect_ratio: Optional[str] = None,
) -> Optional[str]:
    """Generate image from text prompt, optionally with input image(s).

    Args:
        prompt: Text prompt for generation.
        image_paths: Optional list of input image paths.
        output_prefix: Output file path prefix (index + .jpg appended).
        image_size: Target size — "1K", "2K", or "4K".
        aspect_ratio: Optional aspect ratio — "1:1", "4:3", "16:9", etc.

    Returns:
        Path to the first generated image, or None on failure.
    """
    client = genai.Client(api_key=_get_api_key())
    model = _get_generation_model()
    print(f"Model: {model} | Size: {image_size}", file=sys.stderr)

    # Build content parts
    parts = [types.Part.from_text(text=prompt)]

    if image_paths:
        for p in image_paths:
            if not Path(p).exists():
                print(f"Error: file not found: {p}", file=sys.stderr)
                sys.exit(1)
            data = Path(p).expanduser().read_bytes()
            mime, _ = mimetypes.guess_type(p)
            parts.append(types.Part.from_bytes(data=data, mime_type=mime or "image/png"))

    contents = [types.Content(role="user", parts=parts)]

    # Image config
    img_cfg = {}
    if image_size:
        img_cfg["image_size"] = image_size
    if aspect_ratio:
        img_cfg["aspect_ratio"] = aspect_ratio

    config = types.GenerateContentConfig(
        response_modalities=["IMAGE", "TEXT"],
        image_config=types.ImageConfig(**img_cfg),
    )

    first_saved = None
    file_index = 0

    for chunk in client.models.generate_content_stream(
        model=model, contents=contents, config=config,
    ):
        candidate = chunk.candidates[0] if chunk.candidates else None
        content = candidate.content if candidate else None
        parts_out = content.parts if content and content.parts else []

        for part in parts_out:
            if part.text:
                print(part.text, end="", flush=True)
                continue

            out_path = f"{output_prefix}_{file_index}.jpg"
            if _save_image_part(part.inline_data, out_path):
                if first_saved is None:
                    first_saved = out_path
                file_index += 1

    return first_saved


def upscale(
    image_path: str,
    output_path: str,
    aspect_ratio: str = "16:9",
) -> Optional[str]:
    """Upscale an image to 4K resolution.

    Args:
        image_path: Path to the input image.
        output_path: Path for the output image (JPEG).
        aspect_ratio: Aspect ratio for the output (default "16:9").

    Returns:
        output_path on success, None on failure.
    """
    if not Path(image_path).exists():
        print(f"Error: file not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=_get_api_key())
    model = _get_upscale_model()
    print(f"Upscale model: {model} | {image_path} -> {output_path}",
          file=sys.stderr)

    image_bytes = Path(image_path).expanduser().read_bytes()
    mime, _ = mimetypes.guess_type(image_path)

    prompt = (
        "Upscale this image to 4K resolution. Maintain all details, text, "
        "and structure exactly. Do not add or remove elements. "
        "Just increase the resolution and sharpness."
    )

    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt),
                types.Part.from_bytes(data=image_bytes, mime_type=mime or "image/jpeg"),
            ],
        )
    ]

    config = types.GenerateContentConfig(
        response_modalities=["IMAGE", "TEXT"],
        image_config=types.ImageConfig(
            aspect_ratio=aspect_ratio,
            image_size="4K",
        ),
    )

    try:
        for chunk in client.models.generate_content_stream(
            model=model, contents=contents, config=config,
        ):
            candidate = chunk.candidates[0] if chunk.candidates else None
            content = candidate.content if candidate else None
            parts_out = content.parts if content and content.parts else []

            for part in parts_out:
                if _save_image_part(part.inline_data, output_path):
                    return output_path

    except Exception as e:
        print(f"Upscale error: {e}", file=sys.stderr)
        sys.exit(1)

    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate or upscale images using Gemini",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  # Text-to-image (1K)
  %(prog)s -p "A serene mountain lake" -o lake.jpg

  # Edit image with prompt
  %(prog)s -p "Remove the watermark" -i photo.jpg -o clean.jpg

  # Multiple input images
  %(prog)s -p "Merge styles" -i style.jpg -i content.jpg -o merged.jpg

  # Generate at 4K with aspect ratio
  %(prog)s -p "Wide banner" -o banner.jpg --size 4K --aspect-ratio 16:9

  # Upscale existing image (default 16:9)
  %(prog)s --upscale -i small.jpg -o big.jpg

  # Upscale with custom aspect ratio
  %(prog)s --upscale -i avatar.jpg -o avatar_4k.jpg --aspect-ratio 1:1
""",
    )

    parser.add_argument(
        "--prompt", "-p",
        help="Text prompt (required for generate mode)",
    )
    parser.add_argument(
        "--input", "-i",
        action="append",
        help="Input image path (repeatable for multiple images)",
    )
    parser.add_argument(
        "--output", "-o",
        default="output",
        help="Output file path/prefix (default: output)",
    )
    parser.add_argument(
        "--size", "-s",
        default="1K",
        choices=["1K", "2K", "4K"],
        help="Image size for generate mode (default: 1K)",
    )
    parser.add_argument(
        "--aspect-ratio", "-a",
        choices=["1:1", "4:3", "16:9", "9:16", "3:4"],
        help="Aspect ratio (default: not set for generate, 16:9 for upscale)",
    )
    parser.add_argument(
        "--upscale",
        action="store_true",
        help="Upscale mode: enlarge input image to 4K",
    )

    args = parser.parse_args()

    if args.upscale:
        # Upscale mode
        if not args.input or len(args.input) != 1:
            parser.error("--upscale requires exactly one --input image")
        upscale(
            image_path=args.input[0],
            output_path=args.output,
            aspect_ratio=args.aspect_ratio or "16:9",
        )
    else:
        # Generate mode
        if not args.prompt:
            parser.error("--prompt is required in generate mode")
        generate(
            prompt=args.prompt,
            image_paths=args.input,
            output_prefix=args.output,
            image_size=args.size,
            aspect_ratio=args.aspect_ratio,
        )


if __name__ == "__main__":
    main()
