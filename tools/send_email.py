"""Send the rendered newsletter via SMTP (Gmail-friendly).

Reads:
    <run_dir>/newsletter_email.html
    <run_dir>/images_manifest.json   (cid -> abs path)
    <run_dir>/newsletter.json        (for subject line)

Requires env (see .env.example):
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
    NEWSLETTER_FROM_EMAIL, NEWSLETTER_TO_EMAIL

Uses MIME multipart/related: the HTML body references inline images via
`cid:hero@newsletter` etc., and matching attachments are added with the same
Content-ID so email clients render them inline.

CLI:
    python tools/send_email.py --run-dir <ts> [--to alice@example.com]
                              [--subject "Custom subject"]
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_env, require_env, resolve_run_dir, setup_logging  # noqa: E402
from schema import Newsletter  # noqa: E402

log = setup_logging("send")


def _build_message(
    subject: str,
    sender: str,
    recipient: str,
    html_body: str,
    manifest: dict[str, str],
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    # Plain-text fallback for clients that ignore HTML.
    msg.set_content("This newsletter is best viewed as HTML in a modern email client.")

    # The CIDs in the HTML use a bare token (e.g. "hero@newsletter"); we need to
    # generate fresh Message-IDs and rewrite the HTML to match, because the
    # email client compares the trimmed <cid> with the bare 'src=cid:...' value.
    rewritten = html_body
    cid_map: dict[str, str] = {}
    for old_cid in manifest.keys():
        new_cid = make_msgid(domain="newsletter")  # like <abc...@newsletter>
        bare = new_cid[1:-1]                       # strip <>
        cid_map[old_cid] = bare
        rewritten = rewritten.replace(f"cid:{old_cid}", f"cid:{bare}")

    msg.add_alternative(rewritten, subtype="html")

    # Attach each image as a related inline part on the html alternative.
    html_part = msg.get_payload()[-1]
    for old_cid, file_path in manifest.items():
        path = Path(file_path)
        if not path.exists():
            log.warning("Manifest references missing file: %s — skipping.", path)
            continue
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "image/png").split("/", 1)
        new_cid = cid_map[old_cid]
        html_part.add_related(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            cid=f"<{new_cid}>",
            filename=path.name,
            disposition="inline",
        )
    return msg


def send(run_dir: Path, to_override: str | None, subject_override: str | None) -> None:
    email_html = run_dir / "newsletter_email.html"
    manifest_path = run_dir / "images_manifest.json"
    nl_path = run_dir / "newsletter.json"
    if not email_html.exists():
        log.error("Missing %s — run render_html.py first.", email_html)
        sys.exit(2)
    if not nl_path.exists():
        log.error("Missing %s — run write_newsletter.py first.", nl_path)
        sys.exit(2)

    newsletter = Newsletter.model_validate_json(nl_path.read_text(encoding="utf-8"))
    manifest: dict[str, str] = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists() else {}
    )

    host = require_env("SMTP_HOST")
    port = int(require_env("SMTP_PORT"))
    user = require_env("SMTP_USER")
    # Strip spaces — Gmail displays App Passwords with spaces but accepts them stripped.
    password = require_env("SMTP_PASSWORD").replace(" ", "")
    sender = os.environ.get("NEWSLETTER_FROM_EMAIL", user).strip() or user
    recipient = (to_override or os.environ.get("NEWSLETTER_TO_EMAIL", "")).strip()
    if not recipient:
        log.error("No recipient. Set NEWSLETTER_TO_EMAIL in .env or pass --to.")
        sys.exit(2)

    subject = subject_override or newsletter.title

    log.info("Building message: %r -> %r (%d inline images)",
             sender, recipient, len(manifest))
    msg = _build_message(
        subject=subject,
        sender=sender,
        recipient=recipient,
        html_body=email_html.read_text(encoding="utf-8"),
        manifest=manifest,
    )

    log.info("Connecting to %s:%d", host, port)
    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        log.error("SMTP auth failed: %s", exc)
        log.error("For Gmail, ensure 2-Step Verification is on and you used an App Password.")
        sys.exit(5)

    log.info("Sent newsletter to %s", recipient)
    (run_dir / "send.log").write_text(
        f"sent_to={recipient}\nfrom={sender}\nsubject={subject}\nimages={len(manifest)}\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Send the rendered newsletter via SMTP.")
    parser.add_argument("--run-dir", required=True, help="Existing run dir or timestamp")
    parser.add_argument("--to", default=None, help="Override NEWSLETTER_TO_EMAIL")
    parser.add_argument("--subject", default=None, help="Override subject (default = title)")
    args = parser.parse_args()

    load_env()
    run_dir = resolve_run_dir(args.run_dir)
    send(run_dir, args.to, args.subject)


if __name__ == "__main__":
    main()
