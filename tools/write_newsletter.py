"""Draft a structured Newsletter JSON with Gemini, validated by Pydantic.

Reads:  <run_dir>/research.md, <run_dir>/sources.json
Writes: <run_dir>/newsletter.json

CLI:
    python tools/write_newsletter.py --run-dir <ts> [--topic "..."]
                                    [--model gemini-2.5-flash]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_env, require_env, resolve_run_dir, retry, setup_logging  # noqa: E402
from schema import Newsletter  # noqa: E402

# Re-export to satisfy unused-import lints if any tooling cares.
__all__ = ["draft"]

log = setup_logging("write")

DEFAULT_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """You are the editor of a sharp, modern weekly newsletter.

Your job: turn the supplied research notes into a publishable newsletter draft.

Recency rules (critical):
- TODAY'S DATE is provided below. Treat it as ground truth for "now".
- The research notes may include sources with mixed publication dates (look for
  "Published: <ISO date>" lines). When facts conflict on time-sensitive topics
  (standings, leaders, prices, scores, election polls, product versions, etc.),
  PREFER THE MOST RECENT dated source and explicitly ignore stale claims.
- Never present last season's standings, last quarter's results, last year's
  roster, or last cycle's leader as the current state. If the freshest sources
  disagree with older ones, the freshest wins.
- If the freshest sources do not actually cover the topic, say "as of <date>,
  the latest reporting indicates..." instead of fabricating a current state.
- Mention concrete dates in the body wherever it sharpens credibility
  (e.g., "as of November 2026" or "in matchday 13").

Editorial guidelines:
- Tone: confident, plainspoken, lightly opinionated. Avoid hype words.
- Structure: 3-5 sections, each 120-220 words of body_markdown. Use short paragraphs and the occasional bulleted list (markdown).
- Each section may include a one-sentence key_takeaway (<= 240 chars).
- The hero_blurb (40-600 chars) is the lead paragraph shown under the title.
- Produce 2-3 infographic_specs total: exactly one with placement="hero" (aspect_ratio "16:9"), and 1-2 with placement="section" (aspect_ratio "1:1" or "4:3", with section_index referencing the section it accompanies).
- infographic_specs[].prompt must describe a STYLIZED EDITORIAL ILLUSTRATION (flat vector, soft gradients, minimal symbolism). Do NOT request a real chart, photograph, or data visualization. 1-3 sentences each.
- sources: include at least 3 URLs you actually drew from, copied from the provided sources list. Prefer the most recent ones.

Output MUST be a single JSON object matching the schema. No prose before or after. No markdown fences."""


SCHEMA_HINT = """Schema (TypeScript-style, for reference):

type AspectRatio = "16:9" | "1:1" | "4:3" | "3:2" | "2:3"

interface InfographicSpec {
  placement: "hero" | "section"
  section_index: number | null      // 0-based index into sections; null for hero
  prompt: string                    // >= 20 chars, editorial illustration
  aspect_ratio: AspectRatio
}

interface Section {
  heading: string
  body_markdown: string             // >= 80 chars
  key_takeaway: string | null
}

interface Newsletter {
  topic: string
  title: string                     // 4-160 chars
  subtitle: string                  // 4-300 chars
  hero_blurb: string                // 40-800 chars
  sections: Section[]               // 3-5 items
  infographic_specs: InfographicSpec[]   // 2-3 items, exactly one with placement="hero"
  cta: string                       // call-to-action line, 4-300 chars
  sources: string[]                 // absolute URLs
}"""


def _strip_code_fence(s: str) -> str:
    """Remove ```json ... ``` fences if Gemini ignored instructions."""
    s = s.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    return m.group(1) if m else s


@retry(attempts=2, base_delay=2.0)
def _generate(model: str, prompt: str, api_key: str) -> str:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.7,
        ),
    )
    if not response.text:
        raise RuntimeError("Empty response from Gemini")
    return response.text


def _try_parse(raw: str) -> tuple[Newsletter | None, str]:
    """Try to parse + validate. Returns (newsletter or None, error message)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}"
    try:
        return Newsletter.model_validate(data), ""
    except ValidationError as exc:
        return None, f"Schema validation error: {exc}"


def _draft_with_retry(prompt: str, model: str, api_key: str, run_dir: Path) -> Newsletter:
    """Generate, then retry once with the error message fed back."""
    raw = _strip_code_fence(_generate(model, prompt, api_key))
    (run_dir / "newsletter.raw.json").write_text(raw, encoding="utf-8")

    newsletter, err = _try_parse(raw)
    if newsletter:
        return newsletter

    log.warning("First-pass output rejected. %s", err.splitlines()[0])
    log.info("Retrying with feedback to the model...")
    fix_prompt = (
        f"{prompt}\n\n"
        "Your previous output was rejected:\n"
        f"{err}\n\n"
        "Return ONLY a corrected JSON object. No prose, no markdown fences. "
        "Ensure every string field respects its length bounds."
    )
    raw2 = _strip_code_fence(_generate(model, fix_prompt, api_key))
    (run_dir / "newsletter.raw.json").write_text(raw2, encoding="utf-8")

    newsletter, err = _try_parse(raw2)
    if newsletter:
        return newsletter

    log.error("Output still rejected after retry. %s", err)
    sys.exit(4)


def draft(run_dir: Path, topic: str | None, model: str) -> Newsletter:
    research_path = run_dir / "research.md"
    sources_path = run_dir / "sources.json"
    if not research_path.exists() or not sources_path.exists():
        log.error("Missing research artefacts in %s — run research_topic.py first.", run_dir)
        sys.exit(2)

    research = research_path.read_text(encoding="utf-8")
    sources = json.loads(sources_path.read_text(encoding="utf-8"))

    # Surface per-source publication date + publisher so the model can weight
    # recency. Newest first; undated sources sink to the bottom.
    def _src_line(s: dict) -> str:
        parts = [f"- {s['title']} -- {s['url']}"]
        meta = []
        if s.get("date"):
            meta.append(f"published {s['date']}")
        if s.get("source"):
            meta.append(s["source"])
        if meta:
            parts.append(f"  ({'; '.join(meta)})")
        return "\n".join(parts)

    sources_sorted = sorted(sources, key=lambda s: (s.get("date") or ""), reverse=True)
    sources_block = "\n".join(_src_line(s) for s in sources_sorted)

    inferred_topic = topic
    if not inferred_topic:
        first_line = research.splitlines()[0]
        inferred_topic = first_line.replace("# Research notes:", "").strip() or "Untitled"

    today_iso = _dt.date.today().isoformat()
    latest_date = next((s["date"] for s in sources_sorted if s.get("date")), None)
    freshness_line = f"TODAY'S DATE: {today_iso}"
    if latest_date:
        freshness_line += f"\nNEWEST DATED SOURCE: {latest_date}"

    prompt = (
        f"{SYSTEM_PROMPT}\n\n{SCHEMA_HINT}\n\n"
        f"{freshness_line}\n\n"
        f"Topic: {inferred_topic}\n\n"
        f"Available sources (use only URLs from this list in sources[]; newest first):\n{sources_block}\n\n"
        f"Research notes follow. Use them as your only factual basis. "
        f"Pay attention to the 'Published: <date>' line under each source header "
        f"and prefer the freshest claims when they conflict.\n"
        f"---\n{research}\n---\n\n"
        "Now produce the JSON."
    )

    api_key = require_env("GEMINI_API_KEY")
    log.info("Drafting newsletter with %s (research=%d chars)", model, len(research))

    newsletter = _draft_with_retry(prompt, model, api_key, run_dir)

    out = run_dir / "newsletter.json"
    out.write_text(
        newsletter.model_dump_json(indent=2, exclude_none=False),
        encoding="utf-8",
    )
    log.info("Wrote %s (%d sections, %d infographics)",
             out, len(newsletter.sections), len(newsletter.infographic_specs))
    return newsletter


def main() -> None:
    parser = argparse.ArgumentParser(description="Draft a Newsletter JSON from research notes")
    parser.add_argument("--run-dir", required=True, help="Existing run dir or timestamp")
    parser.add_argument("--topic", default=None, help="Override topic (default: read from research.md)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Gemini text model (default {DEFAULT_MODEL})")
    args = parser.parse_args()

    load_env()
    run_dir = resolve_run_dir(args.run_dir)
    draft(run_dir, args.topic, args.model)


if __name__ == "__main__":
    main()
