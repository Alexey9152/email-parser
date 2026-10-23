"""Personalized Work & Travel application mailer.

The input is the Excel report produced by the parser (``final_contacts.xlsx``).
By default the command only builds a preview and writes it to
``data/email_preview.txt``; pass ``--send`` to actually talk to SMTP.

The letter is a plain ``{placeholder}`` template, so the user can edit
``templates/application_email.txt`` without touching code.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import logging
import mimetypes
import re
import smtplib
import sys
import time
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from .config import PROJECT_ROOT, Settings
from .exporter import STATUS_VALUES, load_contacts
from .models import (
    SOURCE_FB_PAGE,
    SOURCE_FB_SNIPPET,
    SOURCE_GUESS,
    SOURCE_LABELS,
    SOURCE_OSM,
    SOURCE_SEARCH,
    SOURCE_SITE,
    SOURCE_SITE_JS,
    STATUS_GUESS,
    ContactResult,
)
from .utils import clean_whitespace, unique

log = logging.getLogger("email_parser.mailer")

DEFAULT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "application_email.txt"
DEFAULT_SUBJECT = "Work & Travel application — {restaurant}"
DEFAULT_LOG_PATH = PROJECT_ROOT / "data" / "sent_log.json"
DEFAULT_PREVIEW_PATH = PROJECT_ROOT / "data" / "email_preview.txt"
DEFAULT_RESUME_PATH = PROJECT_ROOT / "data" / "resume.pdf"

DEFAULT_TEMPLATE = """Dear Manager,

My name is Alex Los, and I am a Belarusian university student. I am writing to express my interest in joining the team at {restaurant} as a server or in another available seasonal role for the upcoming Summer season through the J-1 Work and Travel program. For me, this program is a unique opportunity to experience life in the United States, gain international practice, and engage in cultural exchange.

{found_note}

I am interested in a variety of positions, including:

Server / Busser
Cashier / Food Service Worker
Line Cook / Kitchen Support

I have 2 years of hands-on experience working as a server in a high-volume restaurant in Belarus (Vasilki), where my background includes active customer service, operating POS systems, handling cash, preparing beverages, and assisting with food preparation.

I have an Upper-Intermediate level of English and can interact with guests comfortably and efficiently. I am hardworking, responsible, friendly, adaptable, and willing to learn new skills quickly.

Regarding paperwork: I am fully authorized to work in the U.S. for the summer under the J-1 Work and Travel program. All required visa sponsorship, legal documentation, and insurance are already fully arranged.

My availability & work schedule:

Dates: June 28, 2027 – September 1, 2027
Working Hours: Ready to work 40–60 hours per week (fully available for overtime, flexible shifts, and weekend work).

{resume_note}

Thank you for your time and consideration.

Best regards,

Alex Los

Linkedin: linkedin.com/in/aleksey-los/
Phone / WhatsApp: +375296263124
"""


# --------------------------------------------------------------- recipients
def _resolve(path: str | Path | None, default: Path) -> Path:
    if path is None:
        return default
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def describe_source(row: ContactResult) -> tuple[str, str]:
    """Human phrase + verification link for the primary address."""
    source = row.primary_email_source
    if source in {SOURCE_FB_PAGE, SOURCE_FB_SNIPPET}:
        return "on your Facebook page", row.facebook_url
    if source in {SOURCE_SITE, SOURCE_SITE_JS}:
        return "on your website", row.website
    if source == SOURCE_SEARCH:
        return (
            f"in a public online listing for {row.title}",
            row.google_maps_url or row.website or row.facebook_url,
        )
    if source == SOURCE_OSM:
        return (
            "in the public OpenStreetMap listing for your business",
            row.google_maps_url or row.website or row.facebook_url,
        )
    if source == SOURCE_GUESS:
        return "as a likely address for your business", row.website
    return "online", row.website or row.facebook_url or row.google_maps_url


def found_note(row: ContactResult) -> str:
    phrase, link = describe_source(row)
    link_text = f" ({link})" if link else ""
    return (
        f"I found this e-mail address ({row.primary_email}) {phrase}{link_text}, "
        "so I hope it reaches the right person."
    )


def template_values(row: ContactResult) -> dict[str, str]:
    phrase, link = describe_source(row)
    link_text = f" ({link})" if link else ""
    return {
        "restaurant": row.title,
        "venue": row.title,
        "email": row.primary_email,
        "source": phrase,
        "source_link": link,
        "source_link_text": link_text,
        "found_note": found_note(row),
        "website": row.website,
        "facebook": row.facebook_url,
        "maps": row.google_maps_url,
        "address": row.address,
        "phone": row.phone,
        "category": row.category,
    }


def render_text(template: str, values: dict[str, str]) -> str:
    """Simple, safe placeholder substitution (no ``str.format`` surprises)."""
    result = template or ""
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result


def load_template(path: Path | None = None) -> str:
    candidate = path or DEFAULT_TEMPLATE_PATH
    if candidate and candidate.exists():
        return candidate.read_text(encoding="utf-8")
    if path is not None:
        raise FileNotFoundError(f"template not found: {path}")
    return DEFAULT_TEMPLATE


def normalize_statuses(raw: str) -> set[str]:
    statuses: set[str] = set()
    for item in (raw or "").split(","):
        value = item.strip()
        if not value:
            continue
        statuses.add(STATUS_VALUES.get(value, value))
    return statuses


SOURCE_FILTERS = {
    "facebook": {SOURCE_FB_PAGE, SOURCE_FB_SNIPPET},
    "fb": {SOURCE_FB_PAGE, SOURCE_FB_SNIPPET},
    "website": {SOURCE_SITE, SOURCE_SITE_JS},
    "site": {SOURCE_SITE, SOURCE_SITE_JS},
    "search": {SOURCE_SEARCH},
    "web": {SOURCE_SEARCH},
    "osm": {SOURCE_OSM},
    "openstreetmap": {SOURCE_OSM},
    "guess": {SOURCE_GUESS},
}


def normalize_sources(raw: str) -> set[str]:
    sources: set[str] = set()
    for item in (raw or "").split(","):
        key = item.strip().lower()
        if not key:
            continue
        sources.update(SOURCE_FILTERS.get(key, {key}))
    return sources


def select_recipients(
    results: list[ContactResult],
    *,
    include_guessed: bool = False,
    include_sent: bool = False,
    sent: dict[str, dict] | None = None,
    limit: int = 0,
    statuses: set[str] | None = None,
    sources: set[str] | None = None,
) -> tuple[list[ContactResult], dict[str, int]]:
    sent = sent or {}
    selected: list[ContactResult] = []
    seen: set[str] = set()
    stats = {
        "without_email": 0,
        "guessed": 0,
        "duplicate": 0,
        "already_sent": 0,
        "filtered": 0,
    }

    for row in results:
        email = row.primary_email.strip().lower()
        if not email:
            stats["without_email"] += 1
            continue
        source = row.primary_email_source
        if (source == SOURCE_GUESS or row.status == STATUS_GUESS) and not include_guessed:
            stats["guessed"] += 1
            continue
        if statuses and row.status not in statuses:
            stats["filtered"] += 1
            continue
        if sources and source not in sources:
            stats["filtered"] += 1
            continue
        if email in seen:
            stats["duplicate"] += 1
            continue
        if not include_sent and email in sent:
            stats["already_sent"] += 1
            continue
        seen.add(email)
        selected.append(row)
        if limit and len(selected) >= limit:
            break
    return selected, stats


# ------------------------------------------------------------------- message
def _linkify_html(text: str) -> str:
    pattern = re.compile(r"(https?://[^\s<]+)")

    def replace(match: re.Match[str]) -> str:
        url = html_lib.unescape(match.group(1)).rstrip(".,;:!?")
        return f'<a href="{html_lib.escape(url)}">{html_lib.escape(url)}</a>'

    return pattern.sub(replace, text)


def body_to_html(body: str) -> str:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body or "") if part.strip()]
    chunks = []
    for paragraph in paragraphs:
        escaped = html_lib.escape(paragraph).replace("\n", "<br>")
        chunks.append(f"<p>{_linkify_html(escaped)}</p>")
    return (
        '<html><body style="font-family: Arial, sans-serif; font-size: 14px; '
        'line-height: 1.5; color: #222;">'
        + "".join(chunks)
        + "</body></html>"
    )


def build_message(
    row: ContactResult,
    *,
    settings: Settings,
    template: str,
    subject_template: str = DEFAULT_SUBJECT,
    resume_path: Path | None = None,
) -> EmailMessage:
    values = template_values(row)
    has_resume = bool(
        resume_path and resume_path.exists() and resume_path.is_file()
    )
    values["resume_note"] = (
        "I have attached my Resume to this email for your review. "
        "Please let me know if you need any additional information."
        if has_resume
        else "Please let me know if you need any additional information."
    )
    body = render_text(template, values)
    subject = render_text(subject_template, values)
    body = body.replace("\r\n", "\n").strip() + "\n"

    from_email = (settings.mail_from or settings.smtp_user or "").strip()
    from_name = clean_whitespace(settings.mail_from_name)
    message = EmailMessage()
    message["From"] = formataddr((from_name, from_email)) if from_name else from_email
    message["To"] = row.primary_email
    message["Subject"] = subject
    if settings.mail_reply_to:
        message["Reply-To"] = settings.mail_reply_to
    if settings.mail_bcc:
        message["Bcc"] = settings.mail_bcc
    domain = from_email.rpartition("@")[2] if "@" in from_email else ""
    message["Message-ID"] = make_msgid(domain=domain or None)
    message["X-Mailer"] = "email-parser Work & Travel mailer"

    message.set_content(body)
    message.add_alternative(body_to_html(body), subtype="html")

    if has_resume:
        mime_type = mimetypes.guess_type(resume_path.name)[0] or "application/octet-stream"
        maintype, _, subtype = mime_type.partition("/")
        message.add_attachment(
            resume_path.read_bytes(),
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=resume_path.name,
        )
    return message


# --------------------------------------------------------------- sent log
def load_sent_log(path: str | Path) -> dict[str, dict]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(payload, dict):
        sent = payload.get("sent")
        if isinstance(sent, dict):
            return {str(key).lower(): value for key, value in sent.items()}
        return {str(key).lower(): value for key, value in payload.items()}
    if isinstance(payload, list):
        return {
            str(item.get("email", "")).lower(): item
            for item in payload
            if isinstance(item, dict) and item.get("email")
        }
    return {}


def save_sent_log(path: str | Path, sent: dict[str, dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            {"version": 1, "sent": sent},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def sent_today(sent: dict[str, dict]) -> int:
    today = date.today().isoformat()
    return sum(
        1
        for entry in sent.values()
        if str(entry.get("sent_at", "")).startswith(today)
        and entry.get("status", "sent") == "sent"
    )


# --------------------------------------------------------------------- SMTP
def open_smtp(settings: Settings) -> smtplib.SMTP:
    host = settings.smtp_host.strip()
    if not host:
        raise RuntimeError("SMTP_HOST is not configured (see .env.example)")
    port = int(settings.smtp_port or (465 if settings.smtp_ssl else 587))
    if settings.smtp_ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=45)
    else:
        server = smtplib.SMTP(host, port, timeout=45)
        server.ehlo()
        if settings.smtp_starttls:
            server.starttls()
            server.ehlo()
    if settings.smtp_user:
        server.login(settings.smtp_user, settings.smtp_password)
    return server


def send_all(
    recipients: list[ContactResult],
    *,
    settings: Settings,
    template: str,
    subject_template: str,
    resume_path: Path | None,
    log_path: Path,
    delay: float,
    daily_limit: int,
) -> tuple[int, int]:
    sent_log = load_sent_log(log_path)
    today = sent_today(sent_log)
    if today >= daily_limit:
        print(
            f"Дневной лимит уже достигнут: {today}/{daily_limit}. "
            "Письма не отправлялись."
        )
        return 0, 0

    server = open_smtp(settings)
    sent = failed = 0
    try:
        for index, row in enumerate(recipients, start=1):
            if today >= daily_limit:
                print(f"Дневной лимит {daily_limit} достигнут — останавливаюсь.")
                break
            message = build_message(
                row,
                settings=settings,
                template=template,
                subject_template=subject_template,
                resume_path=resume_path,
            )
            try:
                server.send_message(message)
            except smtplib.SMTPAuthenticationError:
                raise
            except (smtplib.SMTPException, OSError) as exc:
                failed += 1
                print(f"[{index}/{len(recipients)}] FAILED {row.primary_email}: {exc}")
            else:
                sent += 1
                today += 1
                sent_log[row.primary_email.lower()] = {
                    "restaurant": row.title,
                    "subject": str(message["Subject"]),
                    "source": row.primary_email_source,
                    "source_url": row.primary_email_source_url,
                    "sent_at": datetime.now().isoformat(timespec="seconds"),
                    "message_id": str(message.get("Message-ID", "")),
                    "status": "sent",
                }
                save_sent_log(log_path, sent_log)
                print(
                    f"[{index}/{len(recipients)}] SENT {row.primary_email} "
                    f"({row.title})"
                )
            if index < len(recipients) and delay > 0:
                time.sleep(delay)
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return sent, failed


# ------------------------------------------------------------------ preview
def build_preview(
    recipients: list[ContactResult],
    *,
    settings: Settings,
    template: str,
    subject_template: str,
    resume_path: Path | None,
) -> str:
    chunks: list[str] = []
    for index, row in enumerate(recipients, start=1):
        message = build_message(
            row,
            settings=settings,
            template=template,
            subject_template=subject_template,
            resume_path=resume_path,
        )
        body = message.get_body(preferencelist=("plain",))
        content = body.get_content() if body else ""
        attachment = (
            resume_path.name
            if resume_path and resume_path.exists()
            else "нет (для реальной отправки укажите --resume)"
        )
        chunks.append(
            "\n".join(
                [
                    f"=== {index}. {row.title} <{row.primary_email}> ===",
                    f"Откуда адрес: {SOURCE_LABELS.get(row.primary_email_source, row.primary_email_source)}"
                    + (f" — {row.primary_email_source_url}" if row.primary_email_source_url else ""),
                    f"Тема: {message['Subject']}",
                    f"Вложение: {attachment}",
                    "-" * 72,
                    content,
                ]
            )
        )
    return "\n\n".join(chunks)


# ---------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="email-parser send",
        description=(
            "Send personalized Work & Travel application e-mails from the Excel report. "
            "Without --send only a preview is written."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py send --input data/final_contacts.xlsx\n"
            "  python main.py send --input data/final_contacts.xlsx --limit 5\n"
            "  python main.py send --input data/final_contacts.xlsx "
            "--resume data/Alex_Los_Resume.pdf --send\n"
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/final_contacts.xlsx"),
        help="Excel report (or JSON) with contacts (default: data/final_contacts.xlsx)",
    )
    parser.add_argument("--template", type=Path, default=None, help="letter template")
    parser.add_argument("--resume", type=Path, default=None, help="resume file to attach")
    parser.add_argument("--subject", default=DEFAULT_SUBJECT, help="subject template")
    parser.add_argument(
        "--send",
        action="store_true",
        help="actually send; without this flag nothing leaves the computer",
    )
    parser.add_argument("--no-attach", action="store_true", help="do not attach the resume")
    parser.add_argument("--limit", type=int, default=0, help="max recipients in this run")
    parser.add_argument("--delay", type=float, default=None, help="seconds between letters")
    parser.add_argument("--daily-limit", type=int, default=None, help="max letters per day")
    parser.add_argument(
        "--include-guessed",
        action="store_true",
        help="also write to Guessed (unverified) addresses (not recommended)",
    )
    parser.add_argument(
        "--include-sent",
        action="store_true",
        help="ignore data/sent_log.json and send again",
    )
    parser.add_argument("--status", default="", help="comma-separated status filter")
    parser.add_argument(
        "--source",
        default="",
        help="comma-separated source filter: facebook, website, search, osm, guess",
    )
    parser.add_argument("--log", type=Path, default=None, help="sent log path")
    parser.add_argument("--preview-file", type=Path, default=None, help="preview output path")
    parser.add_argument("--preview-count", type=int, default=3, help="letters printed in console")
    parser.add_argument("--from-email", default="", help="From address (default: MAIL_FROM)")
    parser.add_argument("--from-name", default="", help="From name")
    parser.add_argument("--reply-to", default="", help="Reply-To address")
    parser.add_argument("--bcc", default="", help="BCC address (e.g. yourself)")
    parser.add_argument("--smtp-host", default="", help="SMTP host override")
    parser.add_argument("--smtp-port", type=int, default=0, help="SMTP port override")
    parser.add_argument("--smtp-user", default="", help="SMTP login override")
    parser.add_argument(
        "--smtp-password",
        default="",
        help="SMTP password override (prefer SMTP_PASSWORD in .env)",
    )
    parser.add_argument("--smtp-ssl", action="store_true", help="use implicit TLS (port 465)")
    parser.add_argument("--smtp-no-tls", action="store_true", help="disable STARTTLS")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    return parser


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> None:
    if args.smtp_host:
        settings.smtp_host = args.smtp_host
    if args.smtp_port:
        settings.smtp_port = args.smtp_port
    if args.smtp_user:
        settings.smtp_user = args.smtp_user
    if args.smtp_password:
        settings.smtp_password = args.smtp_password
    if args.smtp_ssl:
        settings.smtp_ssl = True
        settings.smtp_starttls = False
    if args.smtp_no_tls:
        settings.smtp_starttls = False
    if args.from_email:
        settings.mail_from = args.from_email
    if args.from_name:
        settings.mail_from_name = args.from_name
    if args.reply_to:
        settings.mail_reply_to = args.reply_to
    if args.bcc:
        settings.mail_bcc = args.bcc
    if not settings.mail_from:
        settings.mail_from = settings.smtp_user
    if args.delay is not None:
        settings.mail_delay = args.delay
    if args.daily_limit is not None:
        settings.mail_daily_limit = args.daily_limit


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose > 1 else logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    settings = Settings.from_env()
    _apply_overrides(settings, args)

    input_path = _resolve(args.input, PROJECT_ROOT / "data" / "final_contacts.xlsx")
    template_path = (
        _resolve(args.template, DEFAULT_TEMPLATE_PATH)
        if args.template
        else DEFAULT_TEMPLATE_PATH
    )
    resume_path = (
        _resolve(args.resume, DEFAULT_RESUME_PATH)
        if args.resume
        else (DEFAULT_RESUME_PATH if DEFAULT_RESUME_PATH.exists() else None)
    )
    log_path = _resolve(args.log, DEFAULT_LOG_PATH)
    preview_path = _resolve(args.preview_file, DEFAULT_PREVIEW_PATH)

    try:
        results = load_contacts(input_path)
        template = load_template(template_path if args.template else None)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Ошибка: {exc}")
        return 1

    sent_log = load_sent_log(log_path)
    recipients, skipped = select_recipients(
        results,
        include_guessed=args.include_guessed,
        include_sent=args.include_sent,
        sent=sent_log,
        limit=max(0, args.limit),
        statuses=normalize_statuses(args.status),
        sources=normalize_sources(args.source),
    )

    print(f"Отчёт: {input_path}")
    print(f"Строк в отчёте: {len(results)}")
    print(f"Получателей: {len(recipients)}")
    print(
        "Пропущено: "
        f"без e-mail {skipped['without_email']}, "
        f"догадки {skipped['guessed']}, "
        f"дубли {skipped['duplicate']}, "
        f"уже отправлены {skipped['already_sent']}, "
        f"по фильтру {skipped['filtered']}"
    )
    if not recipients:
        print("Нечего отправлять.")
        return 1

    if not args.send:
        preview = build_preview(
            recipients,
            settings=settings,
            template=template,
            subject_template=args.subject,
            resume_path=None if args.no_attach else resume_path,
        )
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_text(preview, encoding="utf-8")
        print("\nРежим: ПРЕВЬЮ — ничего не отправлено.")
        print(f"Полный текст: {preview_path}")
        count = max(1, args.preview_count)
        blocks = [block for block in preview.split("\n\n=== ") if block.strip()]
        shown = []
        for index, block in enumerate(blocks[:count]):
            shown.append(block if index == 0 else "=== " + block)
        print("\n" + "\n\n".join(shown))
        print(
            "\nДля реальной отправки добавьте --send и укажите --resume "
            "(и SMTP-настройки в .env)."
        )
        return 0

    if not settings.smtp_host:
        print("Ошибка: не настроен SMTP_HOST (см. .env.example).")
        return 1
    if not settings.mail_from:
        print("Ошибка: не указан адрес отправителя (MAIL_FROM / --from-email).")
        return 1
    if not args.no_attach and (resume_path is None or not resume_path.exists()):
        print(
            "Ошибка: не найден файл резюме. Укажите --resume путь_к_резюме.pdf "
            "или --no-attach."
        )
        return 1

    print(
        f"\nОтправка {len(recipients)} пись(ма/ем) с задержкой "
        f"{settings.mail_delay:g} c, дневной лимит {settings.mail_daily_limit}."
    )
    try:
        sent, failed = send_all(
            recipients,
            settings=settings,
            template=template,
            subject_template=args.subject,
            resume_path=None if args.no_attach else resume_path,
            log_path=log_path,
            delay=max(0.0, settings.mail_delay),
            daily_limit=max(1, settings.mail_daily_limit),
        )
    except (smtplib.SMTPException, OSError, RuntimeError) as exc:
        print(f"Ошибка SMTP: {exc}")
        return 1

    print(f"\nОтправлено: {sent}; ошибок: {failed}; журнал: {log_path}")
    return 0 if sent else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
