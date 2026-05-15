"""Generate infographics for each Newsletter.infographic_specs entry.

Uses the same `GEMINI_API_KEY` as the writing step. Native image generation
("Nano Banana") via the Gemini image model returns inline PNG bytes — no
polling or task IDs needed.

Saves images to:
    <run_dir>/images/hero.png            -- placement="hero"
    <run_dir>/images/section_<i>.png     -- placement="section", section_index=i

CLI:
    python tools/generate_infographic.py --run-dir <ts>
                                         [--model gemini-3.1-flash-image-preview]
                                         [--size 1K]
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_env, require_env, resolve_run_dir, setup_logging  # noqa: E402
from schema import InfographicSpec, Newsletter  # noqa: E402

log = setup_logging("infographic")

# `gemini-2.5-flash-image` is the GA Nano Banana model and is available on the
# Gemini API free tier. The preview models (`gemini-3.1-flash-image-preview`,
# `gemini-3-pro-image-preview`) are higher quality but currently return
# `RESOURCE_EXHAUSTED, limit: 0` on free-tier keys.
DEFAULT_MODEL = "gemini-2.5-flash-image"
FALLBACK_MODEL = "gemini-2.5-flash-image"

EDITORIAL_PREAMBLE = (
    "Create a stylized editorial illustration suitable for a modern tech newsletter. "
    "Flat vector style, soft gradients, minimal symbolism, clean composition, "
    "calm professional color palette (indigo, teal, off-white). "
    "Do not include real text, brand names, logos, or photorealistic people. "
    "Subject: "
)


def _output_filename(spec: InfographicSpec) -> str:
    if spec.placement == "hero":
        return "hero.png"
    return f"section_{spec.section_index}.png"


def _save_first_image(parts, out_path: Path) -> bool:
    """Find the first inline image part and save it as PNG."""
    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            data = inline.data
            try:
                img = Image.open(io.BytesIO(data))
                img.save(out_path, format="PNG")
                return True
            except Exception as exc:
                log.warning("Failed to decode image data: %s", exc)
    return False


def _generate_one(
    client: genai.Client,
    spec: InfographicSpec,
    out_path: Path,
    model: str,
    image_size: str,
) -> bool:
    prompt = EDITORIAL_PREAMBLE + spec.prompt

    config = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(
            aspect_ratio=spec.aspect_ratio,
            image_size=image_size,
        ),
    )

    log.info("Generating %s (aspect=%s, model=%s)", out_path.name, spec.aspect_ratio, model)
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
    except Exception as exc:
        msg = str(exc).lower()
        # "limit: 0" means the model isn't on the free tier — no amount of
        # waiting helps; surface that to the caller for model fallback.
        if "limit: 0" in msg:
            log.warning("Model %s not available on this tier (limit: 0).", model)
            return False
        if "429" in msg or "quota" in msg or "rate" in msg or "resource_exhausted" in msg:
            log.warning("Rate-limited on %s; sleeping 30s and retrying once.", model)
            time.sleep(30)
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
            except Exception as exc2:
                log.warning("Still failing after backoff: %s", exc2)
                return False
        else:
            raise

    candidates = getattr(response, "candidates", None) or []
    parts = candidates[0].content.parts if candidates and candidates[0].content else []
    if _save_first_image(parts, out_path):
        log.info("  -> saved %s (%d bytes)", out_path, out_path.stat().st_size)
        return True

    # Some safety blocks return only text. Surface it for debugging.
    text_bits: list[str] = []
    for part in parts:
        t = getattr(part, "text", None)
        if t:
            text_bits.append(t)
    log.warning("  -> no image returned. Text feedback: %s",
                " | ".join(text_bits)[:300] or "(none)")
    return False


def generate(run_dir: Path, model: str, image_size: str) -> dict:
    nl_path = run_dir / "newsletter.json"
    if not nl_path.exists():
        log.error("Missing %s — run write_newsletter.py first.", nl_path)
        sys.exit(2)
    newsletter = Newsletter.model_validate_json(nl_path.read_text(encoding="utf-8"))

    api_key = require_env("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)

    images_dir = run_dir / "images"
    images_dir.mkdir(exist_ok=True)

    successes = 0
    failures: list[str] = []
    for spec in newsletter.infographic_specs:
        out_path = images_dir / _output_filename(spec)
        ok = _generate_one(client, spec, out_path, model, image_size)
        if not ok and model != FALLBACK_MODEL:
            log.info("Retrying %s on fallback model %s", out_path.name, FALLBACK_MODEL)
            ok = _generate_one(client, spec, out_path, FALLBACK_MODEL, image_size)
        if ok:
            successes += 1
        else:
            failures.append(out_path.name)

    log.info("Done. %d/%d images generated.",
             successes, len(newsletter.infographic_specs))
    if failures:
        log.warning("Failed: %s. Renderer will use placeholders for those.",
                    ", ".join(failures))
    return {"successes": successes, "failures": failures}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate infographics via Gemini.")
    parser.add_argument("--run-dir", required=True, help="Existing run dir or timestamp")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Gemini image model (default {DEFAULT_MODEL})")
    parser.add_argument("--size", default="1K",
                        help="Image size: 512, 1K, 2K, 4K (default 1K)")
    args = parser.parse_args()

    load_env()
    run_dir = resolve_run_dir(args.run_dir)
    generate(run_dir, args.model, args.size)


if __name__ == "__main__":
    main()
