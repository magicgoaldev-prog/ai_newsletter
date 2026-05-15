"""Research a topic for free using DDGS metasearch + trafilatura extraction.

Outputs (inside the supplied run-dir):
    research.md   -- combined main-text excerpts, lightly headed by source + date
    sources.json  -- [{title, url, fetched_chars, date?, source?}]

Recency controls
----------------
Use `--news` for time-sensitive topics: it switches to the DDGS news backend
which returns per-article publication dates AND sorts toward recent items.
Use `--timelimit d|w|m|y` to constrain how far back results may reach. Both
flags can be combined: `--news --timelimit w` is the strictest "what happened
this week" mode.

CLI:
    python tools/research_topic.py --topic "..." [--run-dir <ts>]
                                   [--max-urls 8] [--region us-en]
                                   [--timelimit d|w|m|y]
                                   [--news]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ddgs import DDGS
import trafilatura

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_env, resolve_run_dir, retry, setup_logging  # noqa: E402

log = setup_logging("research")

MIN_EXTRACTIONS = 3
MIN_CHARS = 400


@retry(attempts=2, base_delay=2.0)
def _search_text(query: str, max_results: int, region: str, timelimit: str | None) -> list[dict]:
    return DDGS().text(
        query,
        region=region,
        safesearch="moderate",
        timelimit=timelimit,
        max_results=max_results,
    )


@retry(attempts=2, base_delay=2.0)
def _search_news(query: str, max_results: int, region: str, timelimit: str | None) -> list[dict]:
    # `news()` returns per-result `date` (ISO 8601) and `source`, both useful
    # downstream. timelimit accepts only d/w/m on this backend.
    safe_tl = timelimit if timelimit in {"d", "w", "m"} else None
    return DDGS().news(
        query,
        region=region,
        safesearch="moderate",
        timelimit=safe_tl,
        max_results=max_results,
    )


@retry(attempts=2, base_delay=1.0)
def _fetch(url: str) -> str | None:
    """Fetch a URL and return its main text, or None if extraction failed."""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None
    return trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=False,
        favor_recall=True,
    )


def _collect_results(
    topic: str,
    max_urls: int,
    region: str,
    timelimit: str | None,
    news_mode: bool,
) -> list[dict]:
    """Run the chosen search backends and return a de-duplicated, recency-first list."""
    raw: list[dict] = []
    if news_mode:
        log.info("DDGS news() query=%r timelimit=%s", topic, timelimit)
        raw.extend(_search_news(topic, max_urls, region, timelimit))
        # Backfill with general text() when news returns too little.
        if len(raw) < max_urls:
            need = max_urls - len(raw)
            log.info("Backfilling with DDGS text() for %d more results", need)
            raw.extend(_search_text(topic, need, region, timelimit))
    else:
        log.info("DDGS text() query=%r timelimit=%s", topic, timelimit)
        raw.extend(_search_text(topic, max_urls, region, timelimit))

    # Normalize, dedupe by URL, push items with a date to the top so the most
    # recent reporting drives both extraction order and the LLM context.
    seen: set[str] = set()
    norm: list[dict] = []
    for r in raw:
        url = r.get("href") or r.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        norm.append({
            "title": r.get("title") or url,
            "url": url,
            "date": r.get("date"),     # ISO-8601 from news(); None from text()
            "source": r.get("source"), # publisher name from news(); None from text()
        })
    norm.sort(key=lambda x: (x["date"] or ""), reverse=True)
    return norm[:max_urls]


def research(
    topic: str,
    run_dir: Path,
    max_urls: int,
    region: str,
    timelimit: str | None,
    news_mode: bool,
) -> dict:
    log.info("Searching DDGS for topic: %r (news=%s, timelimit=%s)", topic, news_mode, timelimit)
    results = _collect_results(topic, max_urls, region, timelimit, news_mode)
    log.info("Got %d candidate URLs", len(results))

    sources: list[dict] = []
    sections: list[str] = []

    for r in results:
        url = r["url"]
        title = r["title"]
        date = r.get("date")
        publisher = r.get("source")
        log.info("Extracting: %s%s", url, f" (date={date})" if date else "")
        try:
            text = _fetch(url)
        except Exception as exc:
            log.warning("Skip %s — fetch failed: %s", url, exc)
            continue
        if not text or len(text) < MIN_CHARS:
            log.info("Skip %s — too short (%d chars)", url, len(  ) if text else 0)
            continue
        record = {"title": title, "url": url, "fetched_chars": len(text)}
        if date:
            record["date"] = date
        if publisher:
            record["source"] = publisher
        sources.append(record)

        meta_line = "Source: " + url
        if publisher:
            meta_line += f" — {publisher}"
        if date:
            meta_line += f" — Published: {date}"
        sections.append(f"## {title}\n{meta_line}\n\n{text.strip()}\n")

    if len(sources) < MIN_EXTRACTIONS:
        log.error(
            "Only %d sources usable (need >= %d). Try a different topic phrasing.",
            len(sources), MIN_EXTRACTIONS,
        )
        sys.exit(3)

    research_path = run_dir / "research.md"
    header_bits = [f"# Research notes: {topic}"]
    if timelimit or news_mode:
        header_bits.append(
            f"_Backend: {'news+text' if news_mode else 'text'} · timelimit={timelimit or 'none'}_"
        )
    research_path.write_text(
        "\n\n".join(header_bits) + "\n\n" + "\n---\n\n".join(sections),
        encoding="utf-8",
    )
    sources_path = run_dir / "sources.json"
    sources_path.write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")

    log.info("Wrote %s (%d chars)", research_path, research_path.stat().st_size)
    log.info("Wrote %s (%d sources)", sources_path, len(sources))
    return {"sources": sources, "research_path": str(research_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Research a topic into research.md + sources.json")
    parser.add_argument("--topic", required=True, help="Free-text topic")
    parser.add_argument("--run-dir", default=None,
                        help="Existing run dir or timestamp; if omitted a new one is created")
    parser.add_argument("--max-urls", type=int, default=8, help="Max URLs to attempt (default 8)")
    parser.add_argument("--region", default="us-en", help="DDGS region (default us-en)")
    parser.add_argument("--timelimit", default=None, choices=[None, "d", "w", "m", "y"],
                        help="Restrict freshness: d/w/m/y (news backend ignores 'y')")
    parser.add_argument("--news", action="store_true",
                        help="Use DDGS news backend (returns publication dates, recency-first)")
    args = parser.parse_args()

    load_env()
    run_dir = resolve_run_dir(args.run_dir)
    log.info("Run dir: %s", run_dir)

    research(
        args.topic,
        run_dir,
        args.max_urls,
        args.region,
        args.timelimit,
        args.news,
    )
    print(run_dir)


if __name__ == "__main__":
    main()
