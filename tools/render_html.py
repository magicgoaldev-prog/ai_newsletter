"""Render the validated Newsletter JSON to email-safe HTML.

Produces two files inside <run_dir>:
    newsletter.html         -- browser preview (base64 images)
    newsletter_email.html   -- CID-referenced images, suitable for SMTP send
    images_manifest.json    -- {cid -> path} pairs consumed by send_email.py

Images are looked up under <run_dir>/images/ by these names:
    hero.png            -- placement="hero"
    section_<i>.png     -- placement="section" with section_index=i

By default, missing images are **omitted** from the rendered HTML so the
newsletter is text-only and clean. Pass `--placeholders` to fall back to a
soft SVG placeholder where an image is missing (useful for template iteration).

CLI:
    python tools/render_html.py --run-dir <ts> [--placeholders]
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from urllib.parse import quote

import markdown as md_lib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from premailer import transform

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import PROJECT_ROOT, load_env, resolve_run_dir, setup_logging  # noqa: E402
from schema import Newsletter  # noqa: E402

log = setup_logging("render")

TEMPLATES_DIR = PROJECT_ROOT / "templates"

PLACEHOLDER_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 450'>"
    "<defs><linearGradient id='g' x1='0' x2='1'>"
    "<stop offset='0' stop-color='#4f46e5'/><stop offset='1' stop-color='#06b6d4'/>"
    "</linearGradient></defs>"
    "<rect width='800' height='450' fill='url(#g)'/>"
    "<text x='400' y='240' fill='white' font-size='32' "
    "font-family='Helvetica' text-anchor='middle' font-weight='700'>"
    "Infographic placeholder</text></svg>"
)


def _placeholder_data_uri() -> str:
    return "data:image/svg+xml;utf8," + quote(PLACEHOLDER_SVG)


def _image_data_uri(path: Path) -> str:
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _section_html(body_markdown: str) -> str:
    return md_lib.markdown(
        body_markdown,
        extensions=["extra", "sane_lists"],
        output_format="html5",
    )


def _resolve_image(run_dir: Path, name: str) -> Path | None:
    candidate = run_dir / "images" / name
    return candidate if candidate.exists() else None


def render(run_dir: Path, use_placeholders: bool = False) -> dict:
    nl_path = run_dir / "newsletter.json"
    if not nl_path.exists():
        log.error("Missing %s — run write_newsletter.py first.", nl_path)
        sys.exit(2)

    newsletter = Newsletter.model_validate_json(nl_path.read_text(encoding="utf-8"))

    sections_view = [
        {
            "heading": s.heading,
            "body_html": _section_html(s.body_markdown),
            "key_takeaway": s.key_takeaway,
        }
        for s in newsletter.sections
    ]

    hero_path = _resolve_image(run_dir, "hero.png")
    section_paths: dict[int, Path] = {}
    for spec in newsletter.infographic_specs:
        if spec.placement == "section" and spec.section_index is not None:
            p = _resolve_image(run_dir, f"section_{spec.section_index}.png")
            if p:
                section_paths[spec.section_index] = p

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("newsletter.html.j2")
    css = (TEMPLATES_DIR / "newsletter.css").read_text(encoding="utf-8")

    nl_view = {
        "topic": newsletter.topic,
        "title": newsletter.title,
        "subtitle": newsletter.subtitle,
        "hero_blurb": newsletter.hero_blurb,
        "sections": sections_view,
        "cta": newsletter.cta,
        "sources": [str(u) for u in newsletter.sources],
    }

    # --- 1) Preview HTML ---
    # If a real PNG is on disk, inline it as base64. If it's missing and the
    # user explicitly asked for placeholders, drop in the SVG; otherwise leave
    # the slot empty so the template hides the entire image area.
    if hero_path:
        preview_hero = _image_data_uri(hero_path)
    elif use_placeholders:
        preview_hero = _placeholder_data_uri()
    else:
        preview_hero = None

    preview_sections: dict[int, str] = {}
    for spec in newsletter.infographic_specs:
        if spec.placement != "section" or spec.section_index is None:
            continue
        idx = spec.section_index
        if idx in section_paths:
            preview_sections[idx] = _image_data_uri(section_paths[idx])
        elif use_placeholders:
            preview_sections[idx] = _placeholder_data_uri()
        # else: leave it out so template skips the <img>

    preview_html = template.render(
        newsletter=nl_view,
        css=css,
        hero_image=preview_hero,
        section_images=preview_sections,
    )
    preview_html = transform(preview_html, remove_classes=False)
    (run_dir / "newsletter.html").write_text(preview_html, encoding="utf-8")
    log.info("Wrote %s", run_dir / "newsletter.html")

    # --- 2) Email HTML: CID references ---
    manifest: dict[str, str] = {}  # cid -> abs path
    if hero_path:
        cid = "hero@newsletter"
        manifest[cid] = str(hero_path)
        email_hero: str | None = f"cid:{cid}"
    elif use_placeholders:
        email_hero = _placeholder_data_uri()
    else:
        email_hero = None

    email_sections: dict[int, str] = {}
    for i, p in section_paths.items():
        cid = f"section{i}@newsletter"
        manifest[cid] = str(p)
        email_sections[i] = f"cid:{cid}"
    if use_placeholders:
        for spec in newsletter.infographic_specs:
            if spec.placement == "section" and spec.section_index is not None:
                if spec.section_index not in email_sections:
                    email_sections[spec.section_index] = _placeholder_data_uri()

    email_html = template.render(
        newsletter=nl_view,
        css=css,
        hero_image=email_hero,
        section_images=email_sections,
    )
    email_html = transform(email_html, remove_classes=False)
    (run_dir / "newsletter_email.html").write_text(email_html, encoding="utf-8")
    log.info("Wrote %s", run_dir / "newsletter_email.html")

    (run_dir / "images_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8",
    )
    log.info("Wrote %s (%d image refs)", run_dir / "images_manifest.json", len(manifest))
    return {"preview": str(run_dir / "newsletter.html"),
            "email": str(run_dir / "newsletter_email.html")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the newsletter to HTML.")
    parser.add_argument("--run-dir", required=True, help="Existing run dir or timestamp")
    parser.add_argument(
        "--placeholders", action="store_true",
        help="Show a soft SVG placeholder when a real PNG is missing (default: hide the image area).",
    )
    args = parser.parse_args()

    load_env()
    run_dir = resolve_run_dir(args.run_dir)
    render(run_dir, use_placeholders=args.placeholders)


if __name__ == "__main__":
    main()
