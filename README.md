# Newsletter Demo

A small WAT-framework (Workflows · Agents · Tools) pipeline that turns a topic
into a polished, email-ready HTML newsletter:

1. Research the topic on the open web (free, no API key).
2. Draft a structured newsletter with Google Gemini, validated by Pydantic.
3. Optionally generate hero / section infographics with Gemini's image model.
4. Render an email-safe HTML (Jinja2 + premailer).
5. Optionally send it via Gmail SMTP.

Everything runs locally as plain Python; the only mandatory paid surface is
Gemini, and even that fits in the free text-tier per newsletter.

---

## Project layout

```
newsletter_demo/
├── .env / .env.example         # Secrets (never commit .env)
├── requirements.txt
├── workflows/
│   └── generate_newsletter.md  # SOP the agent (you / Cursor) follows
├── templates/
│   ├── newsletter.html.j2      # Email-safe Jinja2 template
│   └── newsletter.css          # Inlined by premailer at render time
├── tools/
│   ├── _common.py              # Shared helpers (env, run-dir, retry, logging)
│   ├── schema.py               # Pydantic Newsletter contract
│   ├── research_topic.py       # DDGS + trafilatura
│   ├── write_newsletter.py     # Gemini text -> validated JSON
│   ├── generate_infographic.py # Gemini image (same API key)
│   ├── render_html.py          # Newsletter JSON -> HTML
│   ├── send_email.py           # SMTP + inline CID images
│   └── run_newsletter.py       # End-to-end orchestrator
└── .tmp/runs/<timestamp>/      # All artifacts of each run (gitignored)
```

The agent layer reads `workflows/generate_newsletter.md` and calls the
deterministic Python tools above. Each tool can also be invoked on its own.

---

## Prerequisites

- **Python 3.10+** (developed on 3.14).
- A **Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey).
- (Optional) A **Gmail account** with 2-Step Verification + an
  [App Password](https://myaccount.google.com/apppasswords) if you want to send
  the newsletter by email.

---

## Setup

1. Clone / open the project.
2. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and fill in the values:

   ```env
   GEMINI_API_KEY=<your AI Studio key>

   # Only required to actually send mail
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your.address@gmail.com
   SMTP_PASSWORD=<16-char Gmail App Password>

   NEWSLETTER_FROM_EMAIL=your.address@gmail.com   # must match SMTP_USER
   NEWSLETTER_TO_EMAIL=recipient@example.com
   ```

   `.env` is gitignored. Never commit it.

---

## Quick start

End-to-end run, rendered but not emailed:

```powershell
python tools\run_newsletter.py --topic "AI agent architectures 2026" --no-send
```

When it finishes, the orchestrator prints the run directory, e.g.:

```
.tmp\runs\20260513_174918\
```

Open `newsletter.html` from that folder in a browser to preview.

To actually send the email:

```powershell
python tools\run_newsletter.py --topic "AI agent architectures 2026"
```

The recipient is taken from `NEWSLETTER_TO_EMAIL`, override with `--to`.

---

## Orchestrator (`run_newsletter.py`)

```
python tools\run_newsletter.py --topic "<topic>"
                              [--no-send]                # render but don't send
                              [--dry-run]                # also skip image generation
                              [--reuse-research <ts>]    # reuse a previous run dir's research
                              [--placeholders]           # show an SVG placeholder when an image is missing
                              [--to <email>]             # override NEWSLETTER_TO_EMAIL
                              [--max-urls 8]             # research breadth
                              [--region us-en]           # DDGS region
                              [--news]                   # use DDGS news backend (recency-first)
                              [--timelimit d|w|m|y]      # constrain search freshness
                              [--text-model gemini-2.5-flash]
                              [--image-model gemini-2.5-flash-image]
                              [--image-size 1K]
```

### Picking the right recency mode

The research stage defaults to a general web search with no time filter, which
is fine for evergreen topics ("Intro to vector databases") but **wrong for
live events** ("EPL 2025-26 title race"). Use these flags to keep the
newsletter from echoing stale reporting:

| Topic shape | Recommended flags |
| --- | --- |
| Static / evergreen | *(none)* |
| Recent developments (months matter) | `--timelimit m` |
| Live event / current standings / season race | `--news --timelimit w` |
| Breaking / yesterday's news | `--news --timelimit d` |

Behind the scenes:

- `--news` switches DDGS to its news backend, which returns publication dates
  and biases recency. Per-source `date` and `source` (publisher) are written
  to `sources.json` and surfaced in `research.md`.
- `--timelimit` is a hard cutoff (`d`=24h, `w`=week, `m`=month, `y`=year).
  The news backend ignores `y`.
- The drafting prompt is automatically injected with `TODAY'S DATE` and the
  newest source's publication date, and is told to **prefer the freshest
  dated claims when sources conflict**.

### Common recipes

```powershell
# Cheapest, fastest preview (no image calls, no email)
python tools\run_newsletter.py --topic "<topic>" --dry-run

# Reuse research from an earlier run, iterate on the draft only
python tools\run_newsletter.py --topic "<topic>" --no-send `
    --reuse-research 20260513_174918

# Force placeholder SVG into the image slots (for template design)
python tools\run_newsletter.py --topic "<topic>" --no-send --placeholders

# Live sports / standings / anything that changes weekly
python tools\run_newsletter.py --topic "EPL 2025-26 title race" `
    --news --timelimit w --no-send
```

---

## Running each step independently

Every tool is independently runnable against a `--run-dir`. The run-dir is
either a full path or just the timestamp folder name under `.tmp/runs/`.

```powershell
# 1. Research only — creates a fresh .tmp/runs/<ts>/ and prints its path
python tools\research_topic.py --topic "<topic>" --max-urls 6

# 1b. Research a live event with recency filters
python tools\research_topic.py --topic "<topic>" --news --timelimit w

# 2. Draft the newsletter against existing research
python tools\write_newsletter.py --run-dir 20260513_174918

# 3. Generate infographics (requires Gemini billing — see "Known limits")
python tools\generate_infographic.py --run-dir 20260513_174918

# 4. Render to HTML (fast, free; safe to repeat after editing newsletter.json)
python tools\render_html.py --run-dir 20260513_174918
python tools\render_html.py --run-dir 20260513_174918 --placeholders

# 5. Send the email
python tools\send_email.py --run-dir 20260513_174918
python tools\send_email.py --run-dir 20260513_174918 --to friend@example.com
```

---

## What ends up in a run directory

```
.tmp\runs\<ts>\
├── research.md            # Combined main-text from extracted pages (with Published: dates when known)
├── sources.json           # [{title, url, fetched_chars, date?, source?}] — sorted newest first
├── newsletter.json        # Validated Pydantic Newsletter (the contract)
├── newsletter.raw.json    # Latest raw model output (for debugging)
├── images/                # hero.png, section_<i>.png — created if image API succeeds
├── newsletter.html        # Browser preview (base64 inline images)
├── newsletter_email.html  # Email-ready HTML (CID image references)
├── images_manifest.json   # cid -> file path, consumed by send_email.py
└── send.log               # Written after a successful SMTP send
```

You can hand-edit `newsletter.json` and re-run only `render_html.py` to update
the preview without touching the model.

---

## Known limits (current, May 2026)

- **Gemini image free tier: not available.** On a brand-new key without
  billing, both `gemini-3.1-flash-image-preview` and `gemini-2.5-flash-image`
  return `RESOURCE_EXHAUSTED, limit: 0`. `generate_infographic.py` detects this
  cleanly and exits with 0/N images; the renderer then **omits the image area
  entirely** unless you pass `--placeholders`. Enable billing on the Google
  Cloud project that owns `GEMINI_API_KEY` to turn real images on — no code
  change required.
- **Gemini text JSON occasionally invalid.** `write_newsletter.py` retries
  once with the parse / validation error fed back to the model. The raw output
  is always saved to `newsletter.raw.json` for debugging.
- **DDGS extraction quality is uneven.** SPA / paywalled pages may yield empty
  text and are silently skipped; the workflow aborts cleanly if fewer than 3
  sources extract successfully — re-phrase the topic and try again.
- **Gmail SMTP** requires 2-Step Verification + an App Password. The `FROM`
  address must equal `SMTP_USER`.
- **SynthID watermark.** All Gemini-generated images carry an invisible
  watermark by Google policy — harmless for newsletter use.

---

## How to customize

- **Visual design**: edit `templates/newsletter.html.j2` and
  `templates/newsletter.css`. Then just re-run `render_html.py` against the
  existing run dir — no API calls.
- **Editorial voice / structure**: edit `SYSTEM_PROMPT` in
  `tools/write_newsletter.py`. Keep `SCHEMA_HINT` in sync with `tools/schema.py`
  if you change the model.
- **Different image backend**: swap the body of `_generate_one` in
  `tools/generate_infographic.py` to call Hugging Face Inference, Replicate,
  Together, or any other provider. The render layer only needs PNGs at
  `images/hero.png` and `images/section_<i>.png`.
- **Different recipient list**: pass `--to` explicitly, or extend
  `send_email.py` to loop over a `recipients.txt` file.

---

## Troubleshooting

- **`Quota exceeded ... limit: 0`** — Gemini image model is not on the free
  tier. Enable billing or skip images with the default render mode.
- **`Schema validation failed`** — see `newsletter.raw.json` in the run dir for
  the model's last attempt. The orchestrator already retried once. Either
  loosen `tools/schema.py` or refine `SYSTEM_PROMPT`.
- **`SMTP auth failed`** — Gmail rejected the login. Make sure 2-Step
  Verification is on and that `SMTP_PASSWORD` is the **16-character App
  Password**, not your regular account password.
- **Empty research / fewer than 3 sources** — broaden the topic phrasing, or
  increase `--max-urls`, or try a different `--region`.
- **Newsletter describes the *previous* season / quarter / cycle** — the search
  picked up older, high-PR articles. Re-run with `--news --timelimit w` (or
  `d`) and inspect `sources.json`: each entry should have a recent `date` and
  the bulk should be from the last 1-2 weeks. The drafting prompt already
  asks the model to prefer the freshest dated claims, so once recent sources
  dominate the input the output follows.

---

## License / notice

This is a personal demo; treat all generated content as drafts. Verify facts
against the cited `sources.json` URLs before publishing.
