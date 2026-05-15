"""End-to-end newsletter orchestrator.

Steps (in order):
    1. research_topic   -- DDGS + trafilatura  -> research.md + sources.json
    2. write_newsletter -- Gemini text + Pydantic -> newsletter.json
    3. generate_infographic -- Gemini image (skipped on --dry-run) -> images/
    4. render_html      -- Jinja2 + premailer -> newsletter.html + newsletter_email.html
    5. send_email       -- SMTP (skipped on --no-send or --dry-run) -> send.log

CLI:
    python tools/run_newsletter.py --topic "<t>"
                                  [--dry-run]              # skip images and send
                                  [--no-send]              # render but don't send
                                  [--reuse-research <ts>]  # skip step 1
                                  [--to alice@example.com]
                                  [--text-model gemini-2.5-flash]
                                  [--image-model gemini-2.5-flash-image]
                                  [--image-size 1K]
                                  [--news] [--timelimit d|w|m|y]    # recency knobs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_env, new_run_dir, resolve_run_dir, setup_logging  # noqa: E402

log = setup_logging("run")


def _step(name: str, fn, *args, **kwargs):
    log.info("=" * 60)
    log.info("STEP: %s", name)
    log.info("=" * 60)
    return fn(*args, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full newsletter pipeline.")
    parser.add_argument("--topic", required=True, help="Free-text topic")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip image generation and email send")
    parser.add_argument("--no-send", action="store_true",
                        help="Render fully but skip email send")
    parser.add_argument("--reuse-research", default=None,
                        help="Existing run dir / timestamp; skips DDGS+extraction")
    parser.add_argument("--to", default=None, help="Override NEWSLETTER_TO_EMAIL")
    parser.add_argument("--max-urls", type=int, default=8, help="Max URLs for research")
    parser.add_argument("--region", default="us-en", help="DDGS region")
    parser.add_argument("--text-model", default="gemini-2.5-flash")
    parser.add_argument("--image-model", default="gemini-2.5-flash-image")
    parser.add_argument("--image-size", default="1K")
    parser.add_argument(
        "--placeholders", action="store_true",
        help="Show SVG placeholders when an image is missing (default: hide the slot).",
    )
    parser.add_argument(
        "--news", action="store_true",
        help="Use DDGS news backend (recency-first, returns publication dates).",
    )
    parser.add_argument(
        "--timelimit", default=None, choices=[None, "d", "w", "m", "y"],
        help="Constrain search freshness: d/w/m/y. News backend ignores 'y'.",
    )
    args = parser.parse_args()

    load_env()

    # Local imports keep startup snappy and decouple tools (each can run alone).
    from research_topic import research
    from write_newsletter import draft
    from generate_infographic import generate as gen_images
    from render_html import render
    from send_email import send

    # Step 1: research (or reuse)
    if args.reuse_research:
        run_dir = resolve_run_dir(args.reuse_research)
        log.info("Reusing research from %s", run_dir)
        if not (run_dir / "research.md").exists():
            log.error("No research.md found in %s", run_dir)
            sys.exit(2)
    else:
        run_dir = new_run_dir()
        log.info("Run dir: %s", run_dir)
        _step(
            "research_topic",
            research,
            args.topic,
            run_dir,
            args.max_urls,
            args.region,
            args.timelimit,
            args.news,
        )

    # Step 2: write
    _step("write_newsletter", draft, run_dir, args.topic, args.text_model)

    # Step 3: infographics (skipped on --dry-run)
    if args.dry_run:
        log.info("--dry-run: skipping image generation; placeholder SVG will render.")
    else:
        _step("generate_infographic", gen_images, run_dir, args.image_model, args.image_size)

    # Step 4: render
    _step("render_html", render, run_dir, args.placeholders)

    # Step 5: send (skipped on --dry-run / --no-send)
    if args.dry_run or args.no_send:
        log.info("Send skipped (%s). Preview: %s",
                 "--dry-run" if args.dry_run else "--no-send",
                 run_dir / "newsletter.html")
    else:
        _step("send_email", send, run_dir, args.to, None)

    log.info("Done. Run dir: %s", run_dir)
    print(run_dir)


if __name__ == "__main__":
    main()
