# Workflow: Generate Newsletter

## Objective

Given a topic, produce a polished, illustrated HTML newsletter and (optionally)
deliver it to a recipient by email. Research is automatic; the agent's role is
to orchestrate the deterministic tools below.

## Required inputs

- `--topic "<free-text topic>"` (required)
- Environment (see `.env.example`):
  - `GEMINI_API_KEY` — Google AI Studio key, used for both text and images
  - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` — Gmail SMTP (only if sending)
  - `NEWSLETTER_FROM_EMAIL`, `NEWSLETTER_TO_EMAIL` — must match the SMTP account

## Outputs

Every run creates a fresh directory `.tmp/runs/<timestamp>/` containing:

```
research.md           # Combined research notes (markdown)
sources.json          # [{title, url, fetched_chars}]
newsletter.json       # Validated Pydantic Newsletter
images/
  hero.png
  section_<i>.png
newsletter.html       # Browser preview (base64 inline images)
newsletter_email.html # Email-ready HTML (CID image refs, CSS inlined)
send.log              # SMTP delivery log when sent
```

## Tools (in execution order)

1. `tools/research_topic.py --topic "<t>" --run-dir <dir> [--news] [--timelimit d|w|m|y]`
   - DuckDuckGo metasearch via `ddgs` (no key) → top URLs.
   - `--news` switches to the news backend (returns publication date + publisher; recency-first).
   - `--timelimit` constrains how far back results may reach (`d`=24h, `w`=week, `m`=month, `y`=year; news backend ignores `y`).
   - `trafilatura` extracts main page text.
   - Writes `research.md` (with `Published: <date>` lines when known) + `sources.json` (now includes `date` and `source` for news items, sorted newest first).
2. `tools/write_newsletter.py --run-dir <dir>`
   - Gemini text model, `response_mime_type="application/json"`, validated by `Newsletter` Pydantic model.
   - Prompt is injected with `TODAY'S DATE` and the newest source's publication date, and tells the model to **prefer the freshest dated claims when sources conflict**.
   - Writes `newsletter.json`.
3. `tools/generate_infographic.py --run-dir <dir>`
   - For each `infographic_specs[i]`, calls `gemini-3.1-flash-image-preview`.
   - Saves PNGs to `images/`.
4. `tools/render_html.py --run-dir <dir>`
   - Jinja2 template + `premailer` CSS inlining.
   - Writes `newsletter.html` (base64) and `newsletter_email.html` (CID).
5. `tools/send_email.py --run-dir <dir>` (optional)
   - SMTP `multipart/related` with CID image attachments.

The end-to-end orchestrator is `tools/run_newsletter.py`; it wires the five
tools together with shared run-dir and CLI flags.

## CLI

```
python tools/run_newsletter.py --topic "<topic>"
                              [--dry-run]              # skip image generation and email send
                              [--no-send]              # render but don't send
                              [--reuse-research <ts>]  # skip research, reuse a previous run dir
                              [--news]                 # use DDGS news backend (recency-first)
                              [--timelimit d|w|m|y]    # constrain search freshness
```

## Picking the right recency mode

| Topic shape | Recommended flags |
| --- | --- |
| Static / evergreen ("Intro to vector databases") | *(none)* |
| Recent developments ("AI agents in 2026") | `--timelimit m` |
| Live event / current standings ("EPL 2025-26 title race") | `--news --timelimit w` |
| Yesterday's incident / breaking | `--news --timelimit d` |

Rule of thumb: if the answer can change week-to-week, set `--news --timelimit w`.
If it can change in a season but not a day, set `--timelimit m`.

## Edge cases / known constraints

- **Stale search results on time-sensitive topics** *(observed 2026-05)*: without `--news`/`--timelimit`, DDGS's general index can surface high-PR articles that are months or years old, causing the LLM to confidently describe last season's standings as the current state. Mitigation is multi-layer: (1) `--news` uses the news backend which returns `date` + `source` and biases recency; (2) `--timelimit` hard-caps freshness; (3) `sources.json` carries `date`/`source` per item and is re-sorted newest-first; (4) `write_newsletter.py` injects `TODAY'S DATE` + newest source date into the prompt and instructs the model to prefer the freshest dated claims when sources conflict. **For live events, always pass `--news --timelimit w` (or `d`).**
- **DDGS rate limits / empty results**: retry with a slightly wider query; cap at 10 URLs. SPA/paywalled pages may yield empty extractions — those are silently skipped, but at least 3 successful extractions are required, otherwise the workflow aborts with a clear error.
- **Gemini 429 (quota)**: orchestrator catches once and sleeps 30s before retrying; if it persists, abort.
- **JSON parse / schema validation flakiness** *(observed 2026-05)*: with `response_mime_type="application/json"` Gemini 2.5 Flash still occasionally returns malformed JSON (e.g. unquoted keys mid-document) or oversteps string length bounds (`subtitle`, `cta`). `write_newsletter.py` therefore does a **single retry** that feeds the parse/validation error back to the model and asks for a corrected JSON. Raw outputs are always saved to `newsletter.raw.json` for debugging.
- **Image free tier `limit: 0`** *(observed 2026-05)*: on a brand-new Gemini API key without billing enabled, both `gemini-3.1-flash-image-preview` and `gemini-2.5-flash-image` return `RESOURCE_EXHAUSTED, limit: 0` immediately — the free tier does not include image generation. `generate_infographic.py` detects this and exits cleanly with 0/N images; the renderer falls back to a styled SVG placeholder so the pipeline still produces a complete newsletter. To enable real images, enable billing on the Google Cloud project that owns `GEMINI_API_KEY`.
- **Image generation safety blocks**: if Gemini refuses an image prompt, the tool surfaces the refusal text in logs and continues with placeholders.
- **Gmail SMTP**: requires 2-Step Verification + App Password. `SMTP_USER` and `NEWSLETTER_FROM_EMAIL` must match.
- **SynthID watermark**: all Gemini-generated images carry an invisible watermark; harmless here.

## When something breaks

1. Read the full traceback from the failing tool.
2. Reproduce the failing tool in isolation with `--run-dir <ts>` against the existing run dir.
3. Fix the script. Do **not** rerun previous tools unless their inputs changed.
4. Document the learned constraint in this SOP under "Edge cases".

## Cost notes

- Research: free.
- Gemini text: well within free tier per newsletter.
- Gemini image: ~3-4 images per newsletter; consumes free-tier quota faster than text. Use `--dry-run` while iterating on prompts.
- Email: Gmail SMTP free for personal volumes (~500/day cap).
