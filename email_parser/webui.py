"""Local web UI: draw an area on a map, pick categories, get an Excel report.

The same Flask application also serves the per-recipient Work & Travel mailer:
``/send`` lets the user edit the letter, configure SMTP and confirm every
single letter before it leaves the computer.
"""

from __future__ import annotations

import logging
import re
import smtplib
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_from_directory

from .categories import CATEGORY_GROUPS, FOOD_CATEGORIES
from .config import Settings
from .exporter import STATUS_LABELS, load_contacts
from .mailer import (
    DEFAULT_SUBJECT,
    DEFAULT_TEMPLATE,
    build_message,
    load_sent_log,
    open_smtp,
    select_recipients,
    send_one,
    sent_today,
)
from .mailer_state import (
    BOOL_FIELDS,
    FLOAT_FIELDS,
    INT_FIELDS,
    STR_FIELDS,
    display_path,
    list_reports,
    load_state,
    public_state,
    resolve_file,
    save_state,
    save_uploaded_resume,
    sent_log_path,
    settings_from_state,
)
from .models import SOURCE_GUESS, SOURCE_LABELS, STATUS_GUESS, ContactResult
from .pipeline import run
from .progress import ProgressTracker
from .utils import parse_bbox

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
REPORT_SUFFIXES = {".xlsx", ".json"}
RESUME_SUFFIXES = {".pdf", ".doc", ".docx", ".rtf", ".txt"}


class Job:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.state = "idle"  # idle | running | done | error
        self.log: list[str] = []
        self.stats: dict = {}
        self.error = ""
        self.started = time.time()
        self.progress = ProgressTracker()
        self.lock = threading.Lock()

    def add(self, line: str) -> None:
        with self.lock:
            self.log.append(line)
            if len(self.log) > 3000:
                del self.log[:1000]

    def snapshot(self) -> dict:
        with self.lock:
            files = []
            report_path = ""
            if self.state == "done":
                for name in ("final_contacts.xlsx", "final_contacts.csv"):
                    if (self.out_dir / name).exists():
                        files.append(name)
                xlsx = self.out_dir / "final_contacts.xlsx"
                if xlsx.exists():
                    report_path = display_path(xlsx)
            return {
                "state": self.state,
                "log": self.log[-600:],
                "stats": self.stats,
                "progress": self.progress.snapshot(),
                "error": self.error,
                "files": files,
                "report_path": report_path,
                "elapsed": round(time.time() - self.started, 1),
            }


class _ListHandler(logging.Handler):
    def __init__(self, job: Job):
        super().__init__()
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - logging glue
        try:
            self.job.add(self.format(record))
        except Exception:
            pass


def _run_job(job: Job, settings: Settings, options: dict) -> None:
    handler = _ListHandler(job)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    package_logger = logging.getLogger("email_parser")
    package_logger.addHandler(handler)
    previous_level = package_logger.level
    package_logger.setLevel(logging.INFO)
    try:
        job.state = "running"
        results, stats = run(
            settings,
            query=options["query"],
            limit=options["limit"],
            provider=options.get("provider") or None,
            out_dir=job.out_dir,
            use_search=options["use_search"],
            search_limit=options["search_limit"],
            js_fallback=options["js"],
            facebook_browser=options["facebook_browser"],
            facebook_limit=options["facebook_limit"],
            guess=options["guess"],
            categories=options["categories"],
            area=options["area"],
            require_website=options["require_website"],
            progress=job.progress,
        )
        job.stats = stats.as_dict()
        job.state = "done"
        job.progress.update(
            stage="done",
            message=f"Готово: {stats.with_email} e-mail из {stats.filtered} заведений",
            emails_found=stats.with_email,
        )
        job.add(
            f"Готово: {stats.with_email} e-mail из {stats.filtered} заведений. "
            f"Отчёт: {job.out_dir / 'final_contacts.xlsx'}"
        )
    except Exception as exc:  # noqa: BLE001 - shown in the UI
        log.exception("UI run failed")
        job.error = str(exc)
        job.state = "error"
        job.progress.update(stage="error", message=str(exc))
        job.add(f"ERROR: {exc}")
    finally:
        package_logger.removeHandler(handler)
        package_logger.setLevel(previous_level)


# --------------------------------------------------------------- mailer glue
def _payload_config(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("config")
    if isinstance(nested, dict):
        merged = dict(nested)
        # Flat keys are also accepted for convenience and older clients.
        for key, value in payload.items():
            if key != "config":
                merged.setdefault(key, value)
        return merged
    return payload


def _merge_mail_state(
    state: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Overlay unsaved form values onto persisted state (without writing)."""
    merged = dict(state)
    for key in STR_FIELDS:
        if key in config and config[key] is not None:
            merged[key] = str(config[key]).strip()
    for key in BOOL_FIELDS:
        if key in config:
            value = config[key]
            if isinstance(value, str):
                merged[key] = value.strip().lower() not in {"", "0", "false", "no", "off"}
            else:
                merged[key] = bool(value)
    for key in INT_FIELDS:
        if key in config and config[key] is not None:
            try:
                merged[key] = int(config[key])
            except (TypeError, ValueError):
                pass
    for key in FLOAT_FIELDS:
        if key in config and config[key] is not None:
            try:
                merged[key] = float(config[key])
            except (TypeError, ValueError):
                pass
    if "letter" in config and config["letter"] is not None:
        merged["letter"] = str(config["letter"])
    if not str(merged.get("letter") or "").strip():
        merged["letter"] = DEFAULT_TEMPLATE
    if not str(merged.get("subject") or "").strip():
        merged["subject"] = DEFAULT_SUBJECT
    merged["smtp_port"] = max(1, min(65535, int(merged.get("smtp_port") or 587)))
    merged["daily_limit"] = max(1, min(1000, int(merged.get("daily_limit") or 50)))
    password = config.get("smtp_password") if "smtp_password" in config else None
    return merged, (str(password) if password is not None else None)


def _mail_context(
    payload: dict[str, Any], settings: Settings
) -> tuple[dict[str, Any], Settings, dict[str, Any]]:
    config = _payload_config(payload or {})
    state, password = _merge_mail_state(load_state(settings), config)
    mail_settings = settings_from_state(state, settings, password_override=password)
    return state, mail_settings, config


def _resolve_report(settings: Settings, state: dict[str, Any], raw: str) -> Path:
    value = (raw or state.get("report") or "").strip()
    if not value:
        raise FileNotFoundError(
            "Сначала выберите Excel-отчёт. Его создаёт поиск заведений."
        )
    return resolve_file(settings, value, suffixes=REPORT_SUFFIXES)


def _resume_for_state(settings: Settings, state: dict[str, Any]) -> Path | None:
    if not state.get("attach_resume"):
        return None
    raw = str(state.get("resume") or "").strip()
    if not raw:
        raise FileNotFoundError(
            "Резюме не выбрано. Загрузите PDF/DOCX или снимите галочку «Прикрепить резюме»."
        )
    return resolve_file(settings, raw, suffixes=RESUME_SUFFIXES)


def _sent_display(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    value = str(entry.get("sent_at", ""))
    return value[:16].replace("T", " ") if value else ""


def _recipient_payload(row: ContactResult, sent: dict[str, dict]) -> dict[str, Any]:
    entry = sent.get(row.primary_email.lower())
    source = row.primary_email_source
    return {
        "title": row.title,
        "email": row.primary_email,
        "status": row.status,
        "status_label": STATUS_LABELS.get(row.status, row.status),
        "source": source,
        "source_label": SOURCE_LABELS.get(source, source),
        "source_url": row.primary_email_source_url,
        "website": row.website,
        "facebook": row.facebook_url,
        "maps": row.google_maps_url,
        "address": row.address,
        "phone": row.phone,
        "category": row.category,
        "chain": row.chain,
        "note": row.note,
        "sent": bool(entry),
        "sent_at": _sent_display(entry),
        "eligible": not bool(entry),
    }


def _find_recipient(results: list[ContactResult], email: str) -> ContactResult:
    target = (email or "").strip().lower()
    if not target:
        raise ValueError("Не выбран получатель.")
    for row in results:
        if row.primary_email.strip().lower() == target:
            if row.primary_email_source == SOURCE_GUESS or row.status == STATUS_GUESS:
                raise ValueError(
                    "Этот адрес помечен как непроверенная догадка и не может "
                    "использоваться для отправки."
                )
            return row
    raise ValueError("Получатель не найден в выбранном отчёте.")


def _report_payload(
    settings: Settings, state: dict[str, Any], report_path: Path
) -> dict[str, Any]:
    results = load_contacts(report_path)
    sent = load_sent_log(sent_log_path(settings))
    recipients, skipped = select_recipients(
        results,
        include_guessed=False,
        include_sent=True,
        sent=sent,
    )
    today = sent_today(sent)
    limit = max(1, int(state.get("daily_limit") or 50))
    rows = [_recipient_payload(row, sent) for row in recipients]
    return {
        "report": display_path(report_path, settings),
        "report_label": display_path(report_path, settings),
        "total": len(results),
        "with_email": sum(1 for row in results if row.primary_email),
        "eligible": sum(1 for item in rows if item["eligible"]),
        "chains": sum(1 for item in rows if item["chain"]),
        "sent_today": today,
        "daily_limit": limit,
        "daily_remaining": max(0, limit - today),
        "stats": {
            "total": len(results),
            "with_email": sum(1 for row in results if row.primary_email),
            "selected": len(rows),
            "without_email": skipped["without_email"],
            "guessed": skipped["guessed"],
            "duplicate": skipped["duplicate"],
            "already_sent": sum(1 for item in rows if item["sent"]),
            "chains": sum(1 for item in rows if item["chain"]),
        },
        "recipients": rows,
    }


def _smtp_failure(exc: BaseException) -> tuple[str, int]:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "SMTP отклонил логин или пароль. Для Gmail/Yandex/Mail.ru нужен "
            "пароль приложения, а не обычный пароль от почты.",
            400,
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return f"SMTP отклонил адрес получателя: {exc}", 400
    if isinstance(exc, (smtplib.SMTPException, OSError, TimeoutError)):
        return f"Ошибка SMTP: {exc}", 502
    return str(exc), 400


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
    state: dict[str, Job | None] = {"job": None}
    lock = threading.Lock()
    send_lock = threading.Lock()

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            categories=list(FOOD_CATEGORIES),
            groups={name: list(items) for name, items in CATEGORY_GROUPS.items()},
        )

    @app.get("/send")
    def send_page():
        return render_template("send.html")

    @app.post("/api/run")
    def start_run():
        payload = request.get_json(silent=True) or {}
        location = (payload.get("location") or "").strip()
        categories = [str(c).strip() for c in (payload.get("categories") or []) if str(c).strip()]
        try:
            area = parse_bbox(payload.get("bbox") or "")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if not categories and not location:
            return jsonify({"error": "Укажите город или выделите область на карте"}), 400
        if categories and not location and area is None:
            return jsonify({"error": "Для категорий нужен город или область на карте"}), 400

        with lock:
            current = state.get("job")
            if current is not None and current.state == "running":
                return jsonify({"error": "Поиск уже идёт, дождитесь завершения"}), 409
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            out_dir = settings.ensure_data_dir() / f"ui-{stamp}"
            out_dir.mkdir(parents=True, exist_ok=True)
            job = Job(out_dir)
            state["job"] = job

        options = {
            "query": location,
            "limit": max(1, int(payload.get("limit") or 100)),
            "provider": payload.get("provider") or "",
            "categories": categories,
            "area": area,
            "use_search": bool(payload.get("use_search", True)),
            "search_limit": max(0, int(payload.get("search_limit") or 0)),
            "js": bool(payload.get("js", False)),
            "facebook_browser": bool(payload.get("facebook_browser", True)),
            "facebook_limit": max(0, int(payload.get("facebook_limit") or 60)),
            "guess": bool(payload.get("guess", False)),
            "require_website": bool(payload.get("only_with_website", False)),
        }

        run_settings = Settings.from_env()
        run_settings.data_dir = settings.data_dir
        thread = threading.Thread(
            target=_run_job, args=(job, run_settings, options), daemon=True
        )
        thread.start()
        return jsonify({"ok": True, "out_dir": str(out_dir)})

    @app.get("/api/status")
    def status():
        job = state.get("job")
        if job is None:
            return jsonify(
                {
                    "state": "idle",
                    "log": [],
                    "stats": {},
                    "files": [],
                    "report_path": "",
                    "progress": ProgressTracker().snapshot(),
                }
            )
        return jsonify(job.snapshot())

    @app.get("/api/download/<name>")
    def download(name: str):
        job = state.get("job")
        if job is None or name not in {"final_contacts.xlsx", "final_contacts.csv", "final_contacts.json"}:
            return jsonify({"error": "not found"}), 404
        return send_from_directory(job.out_dir, name, as_attachment=True)

    # ---------------------------------------------------------- sending UI
    @app.get("/api/send/settings")
    def send_settings():
        data = public_state(settings)
        data["default_letter"] = DEFAULT_TEMPLATE
        data["default_subject"] = DEFAULT_SUBJECT
        data["reports"] = list_reports(settings)
        return jsonify({"ok": True, "settings": data})

    @app.post("/api/send/settings")
    def save_send_settings():
        payload = request.get_json(silent=True) or {}
        config = _payload_config(payload)
        from_email = str(config.get("from_email") or "").strip()
        if from_email and not EMAIL_RE.match(from_email):
            return jsonify({"error": "Проверьте e-mail отправителя."}), 400
        try:
            saved = save_state(settings, config)
        except OSError as exc:
            return jsonify({"error": f"Не удалось сохранить настройки: {exc}"}), 500
        data = public_state(settings, saved)
        data["default_letter"] = DEFAULT_TEMPLATE
        data["default_subject"] = DEFAULT_SUBJECT
        return jsonify({"ok": True, "settings": data})

    @app.get("/api/send/reports")
    def send_reports():
        return jsonify({"ok": True, "reports": list_reports(settings)})

    @app.post("/api/send/upload-resume")
    def upload_resume():
        file = request.files.get("file")
        if file is None or not file.filename:
            return jsonify({"error": "Файл резюме не выбран."}), 400
        content = file.read()
        if not content:
            return jsonify({"error": "Файл резюме пустой."}), 400
        try:
            target = save_uploaded_resume(settings, file.filename, content)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except OSError as exc:
            return jsonify({"error": f"Не удалось сохранить резюме: {exc}"}), 500
        saved = save_state(
            settings,
            {
                "resume": display_path(target, settings),
                "attach_resume": True,
            },
        )
        data = public_state(settings, saved)
        data["default_letter"] = DEFAULT_TEMPLATE
        data["default_subject"] = DEFAULT_SUBJECT
        return jsonify(
            {
                "ok": True,
                "settings": data,
                "resume": display_path(target, settings),
                "name": target.name,
            }
        )

    @app.get("/api/send/recipients")
    def send_recipients():
        state_data = load_state(settings)
        raw = request.args.get("report") or state_data.get("report") or ""
        try:
            report_path = _resolve_report(settings, state_data, raw)
            data = _report_payload(settings, state_data, report_path)
        except (OSError, ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, **data})

    @app.post("/api/send/preview")
    def send_preview():
        payload = request.get_json(silent=True) or {}
        email = str(payload.get("email") or "")
        state_data, mail_settings, _config = _mail_context(payload, settings)
        try:
            report_path = _resolve_report(settings, state_data, "")
            results = load_contacts(report_path)
            row = _find_recipient(results, email)
            resume = _resume_for_state(settings, state_data)
        except (OSError, ValueError, RuntimeError, FileNotFoundError) as exc:
            return jsonify({"error": str(exc)}), 400

        message = build_message(
            row,
            settings=mail_settings,
            template=state_data["letter"],
            subject_template=state_data["subject"],
            resume_path=resume,
        )
        plain_body = message.get_body(preferencelist=("plain",))
        html_body = message.get_body(preferencelist=("html",))
        sent = load_sent_log(sent_log_path(settings))
        recipient = _recipient_payload(row, sent)
        return jsonify(
            {
                "ok": True,
                "recipient": recipient,
                "preview": {
                    "from": str(message.get("From", "")),
                    "to": str(message.get("To", "")),
                    "subject": str(message.get("Subject", "")),
                    "plain": plain_body.get_content() if plain_body else "",
                    "html": html_body.get_content() if html_body else "",
                    "attachments": [
                        part.get_filename()
                        for part in message.iter_attachments()
                        if part.get_filename()
                    ],
                    "source_url": row.primary_email_source_url,
                },
            }
        )

    @app.post("/api/send/test-smtp")
    def test_smtp():
        payload = request.get_json(silent=True) or {}
        _state_data, mail_settings, _config = _mail_context(payload, settings)
        if not mail_settings.smtp_host.strip():
            return jsonify({"error": "Укажите SMTP-сервер."}), 400
        try:
            with send_lock:
                server = open_smtp(mail_settings)
                try:
                    server.noop()
                finally:
                    try:
                        server.quit()
                    except Exception:
                        pass
        except (smtplib.SMTPException, OSError, RuntimeError, TimeoutError) as exc:
            message, status = _smtp_failure(exc)
            return jsonify({"error": message}), status
        return jsonify({"ok": True, "message": "SMTP-подключение и вход выполнены."})

    @app.post("/api/send/send")
    def send_confirmed():
        payload = request.get_json(silent=True) or {}
        confirmed = payload.get("confirm") is True or str(
            payload.get("confirm", "")
        ).strip().lower() == "true"
        if not confirmed:
            return (
                jsonify(
                    {
                        "error": "Отправка не подтверждена. Каждое письмо требует "
                        "отдельного подтверждения."
                    }
                ),
                400,
            )

        email = str(payload.get("email") or "").strip()
        state_data, mail_settings, config = _mail_context(payload, settings)
        from_email = mail_settings.mail_from or mail_settings.smtp_user
        if not from_email or not EMAIL_RE.match(from_email):
            return jsonify({"error": "Укажите корректный e-mail отправителя."}), 400
        if not mail_settings.smtp_host.strip():
            return jsonify({"error": "Укажите SMTP-сервер и порт."}), 400

        with send_lock:
            try:
                report_path = _resolve_report(settings, state_data, "")
                results = load_contacts(report_path)
                row = _find_recipient(results, email)
                resume = _resume_for_state(settings, state_data)
                result = send_one(
                    row,
                    settings=mail_settings,
                    template=state_data["letter"],
                    subject_template=state_data["subject"],
                    resume_path=resume,
                    log_path=sent_log_path(settings),
                    daily_limit=int(state_data.get("daily_limit") or 50),
                )
            except (FileNotFoundError, ValueError, RuntimeError) as exc:
                return jsonify({"error": str(exc)}), 400
            except (smtplib.SMTPException, OSError, TimeoutError) as exc:
                message, status = _smtp_failure(exc)
                return jsonify({"error": message}), status

            try:
                save_state(settings, config)
            except OSError as exc:  # The letter left successfully; just report it.
                log.warning("Could not save mailer state after send: %s", exc)

            sent = load_sent_log(sent_log_path(settings))
            return jsonify(
                {
                    "ok": True,
                    "message": f"Письмо отправлено на {row.primary_email}.",
                    "sent_today": result["sent_today"],
                    "recipient": _recipient_payload(row, sent),
                    "entry": result["entry"],
                }
            )

    return app


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Map-based UI for email-parser")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("ddgs").setLevel(logging.WARNING)
    logging.getLogger("primp").setLevel(logging.WARNING)

    app = create_app()
    url = f"http://{args.host}:{args.port}/"
    send_url = f"http://{args.host}:{args.port}/send"
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"UI: {url}")
    print(f"Письма: {send_url}")
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
