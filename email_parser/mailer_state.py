"""Local persistence for the per-recipient sending UI.

The parser UI is a single-user local application, so the mailer state lives in
``data/`` next to the reports and the sent log.  E-mail/SMTP passwords are only
written when the user explicitly ticks «remember password» in the UI.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, Settings
from .mailer import DEFAULT_SUBJECT, DEFAULT_TEMPLATE, DEFAULT_TEMPLATE_PATH

STATE_FILENAME = "mailer_settings.json"
LETTER_FILENAME = "application_email.txt"
RESUME_DIRNAME = "resumes"

STR_FIELDS = (
    "from_email",
    "from_name",
    "reply_to",
    "bcc",
    "smtp_host",
    "smtp_user",
    "report",
    "resume",
    "subject",
)
BOOL_FIELDS = ("smtp_ssl", "smtp_starttls", "remember_password", "attach_resume")
INT_FIELDS = ("smtp_port", "daily_limit")
FLOAT_FIELDS = ("delay",)


def state_path(settings: Settings) -> Path:
    return settings.data_dir / STATE_FILENAME


def letter_path(settings: Settings) -> Path:
    return settings.data_dir / LETTER_FILENAME


def sent_log_path(settings: Settings) -> Path:
    return settings.data_dir / "sent_log.json"


def resume_dir(settings: Settings) -> Path:
    return settings.data_dir / RESUME_DIRNAME


def display_path(path: Path | str | None, settings: Settings | None = None) -> str:
    """Return a portable path for the UI (``data/...`` when possible)."""
    if not path:
        return ""
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        if settings is not None:
            try:
                return candidate.resolve().relative_to(
                    settings.data_dir.resolve()
                ).as_posix()
            except (OSError, ValueError):
                pass
        return str(candidate)


def _report_candidates(settings: Settings) -> list[Path]:
    root = settings.data_dir
    if not root.exists():
        return []
    candidates = [
        path
        for path in root.rglob("final_contacts.*")
        if path.is_file() and path.suffix.lower() in {".xlsx", ".json"}
    ]
    return sorted(
        candidates,
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def list_reports(settings: Settings) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for path in _report_candidates(settings):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            label = display_path(path, settings)
            parent = path.parent.name
            reports.append(
                {
                    "path": label,
                    "label": label,
                    "folder": parent,
                    "name": path.name,
                    "modified": modified.strftime("%Y-%m-%d %H:%M"),
                    "size": path.stat().st_size,
                }
            )
        except OSError:
            continue
    return reports


def _default_resume(settings: Settings) -> Path | None:
    for candidate in (
        settings.data_dir / "resume.pdf",
        settings.data_dir / "Alex_Los_Resume.pdf",
    ):
        if candidate.is_file():
            return candidate
    return None


def default_state(settings: Settings) -> dict[str, Any]:
    reports = list_reports(settings)
    resume = _default_resume(settings)
    saved_letter = letter_path(settings)
    if saved_letter.exists():
        letter = saved_letter.read_text(encoding="utf-8")
    elif DEFAULT_TEMPLATE_PATH.exists():
        letter = DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8")
    else:
        letter = DEFAULT_TEMPLATE
    return {
        "from_email": settings.mail_from or settings.smtp_user,
        "from_name": settings.mail_from_name or "Alex Los",
        "reply_to": settings.mail_reply_to,
        "bcc": settings.mail_bcc,
        "smtp_host": settings.smtp_host,
        "smtp_port": int(settings.smtp_port or 587),
        "smtp_user": settings.smtp_user,
        "smtp_ssl": bool(settings.smtp_ssl),
        "smtp_starttls": bool(settings.smtp_starttls),
        "remember_password": False,
        "daily_limit": max(1, int(settings.mail_daily_limit or 50)),
        "delay": max(0.0, float(settings.mail_delay or 0)),
        "report": reports[0]["path"] if reports else "",
        "subject": DEFAULT_SUBJECT,
        "letter": letter,
        "resume": display_path(resume, settings) if resume else "",
        "attach_resume": bool(resume),
        "smtp_password": "",
    }


def load_state(settings: Settings) -> dict[str, Any]:
    """Load persisted settings, falling back to .env/defaults."""
    state = default_state(settings)
    path = state_path(settings)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        if isinstance(payload, dict):
            for key in STR_FIELDS + BOOL_FIELDS + INT_FIELDS + FLOAT_FIELDS:
                if key in payload and payload[key] is not None:
                    state[key] = payload[key]
            if "letter" in payload and isinstance(payload["letter"], str):
                state["letter"] = payload["letter"]
            if isinstance(payload.get("smtp_password"), str):
                state["smtp_password"] = payload["smtp_password"]
            if payload.get("saved_at"):
                state["saved_at"] = str(payload["saved_at"])

    saved_letter = letter_path(settings)
    if saved_letter.exists():
        try:
            state["letter"] = saved_letter.read_text(encoding="utf-8")
        except OSError:
            pass

    # Normalise persisted values; a corrupt JSON file must not break the UI.
    state["smtp_port"] = _bounded_int(state.get("smtp_port"), 1, 65535, 587)
    state["daily_limit"] = _bounded_int(state.get("daily_limit"), 1, 1000, 50)
    try:
        state["delay"] = max(0.0, float(state.get("delay") or 0))
    except (TypeError, ValueError):
        state["delay"] = 0.0
    for key in BOOL_FIELDS:
        state[key] = _as_bool(state.get(key))
    for key in STR_FIELDS:
        state[key] = str(state.get(key) or "").strip()
    if not state["letter"]:
        state["letter"] = DEFAULT_TEMPLATE
    if not state["subject"]:
        state["subject"] = DEFAULT_SUBJECT
    return state


def _bounded_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def save_state(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    """Merge UI values into the local state file and return the saved state."""
    state = load_state(settings)
    for key in STR_FIELDS:
        if key in payload and payload[key] is not None:
            state[key] = str(payload[key]).strip()
    for key in BOOL_FIELDS:
        if key in payload:
            state[key] = _as_bool(payload[key])
    for key in INT_FIELDS:
        if key in payload and payload[key] is not None:
            state[key] = payload[key]
    for key in FLOAT_FIELDS:
        if key in payload and payload[key] is not None:
            state[key] = payload[key]
    if "letter" in payload and payload["letter"] is not None:
        state["letter"] = str(payload["letter"])
    if not state["letter"].strip():
        state["letter"] = DEFAULT_TEMPLATE
    if not state["subject"].strip():
        state["subject"] = DEFAULT_SUBJECT

    state["smtp_port"] = _bounded_int(state.get("smtp_port"), 1, 65535, 587)
    state["daily_limit"] = _bounded_int(state.get("daily_limit"), 1, 1000, 50)
    try:
        state["delay"] = max(0.0, float(state.get("delay") or 0))
    except (TypeError, ValueError):
        state["delay"] = 0.0

    password = payload.get("smtp_password")
    if payload.get("forget_password"):
        state["smtp_password"] = ""
    elif isinstance(password, str) and password:
        if state["remember_password"]:
            state["smtp_password"] = password
        elif not state["remember_password"]:
            state["smtp_password"] = ""
    elif not state["remember_password"]:
        state["smtp_password"] = ""

    state["saved_at"] = datetime.now().isoformat(timespec="seconds")
    settings.ensure_data_dir()
    target = state_path(settings)
    temporary = target.with_name(target.name + ".tmp")
    payload_to_write = {
        key: value
        for key, value in state.items()
        if key not in {"saved_at"} or value
    }
    temporary.write_text(
        json.dumps(payload_to_write, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)

    letter = letter_path(settings)
    letter.write_text(state["letter"].rstrip() + "\n", encoding="utf-8")
    return load_state(settings)


def public_state(settings: Settings, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """JSON-safe settings without the password itself."""
    state = state or load_state(settings)
    data = {
        key: value
        for key, value in state.items()
        if key not in {"smtp_password", "saved_at"}
    }
    data["has_saved_password"] = bool(state.get("smtp_password"))
    data["has_env_password"] = bool(settings.smtp_password)
    data["has_password"] = data["has_saved_password"] or data["has_env_password"]
    data["saved_at"] = state.get("saved_at", "")
    data["reports"] = list_reports(settings)
    data["state_path"] = display_path(state_path(settings), settings)
    data["letter_path"] = display_path(letter_path(settings), settings)
    data["sent_log"] = display_path(sent_log_path(settings), settings)
    data["resume_exists"] = bool(
        data.get("resume") and _resolve_existing(settings, data["resume"])
    )
    return data


def _resolve_existing(settings: Settings, raw: str) -> Path | None:
    if not raw:
        return None
    candidate = Path(raw)
    options = [candidate] if candidate.is_absolute() else [
        PROJECT_ROOT / candidate,
        settings.data_dir / candidate,
    ]
    for option in options:
        try:
            if option.is_file():
                return option.resolve()
        except OSError:
            continue
    return None


def resolve_file(settings: Settings, raw: str, *, suffixes: set[str] | None = None) -> Path:
    """Resolve a report/resume path from the UI and validate its suffix."""
    if not raw or not str(raw).strip():
        raise ValueError("Путь к файлу не указан.")
    candidate = Path(str(raw).strip())
    options = [candidate] if candidate.is_absolute() else [
        PROJECT_ROOT / candidate,
        settings.data_dir / candidate,
    ]
    for option in options:
        try:
            if option.is_file():
                resolved = option.resolve()
                if suffixes and resolved.suffix.lower() not in suffixes:
                    raise ValueError(
                        f"Неподдерживаемый формат файла: {resolved.suffix}"
                    )
                return resolved
        except OSError:
            continue
    raise FileNotFoundError(f"Файл не найден: {raw}")


def settings_from_state(
    state: dict[str, Any],
    base: Settings,
    *,
    password_override: str | None = None,
) -> Settings:
    """Build a Settings object for one preview/send operation."""
    settings = Settings.from_env()
    settings.data_dir = base.data_dir
    settings.smtp_host = str(state.get("smtp_host") or "").strip()
    settings.smtp_port = _bounded_int(state.get("smtp_port"), 1, 65535, 587)
    settings.smtp_user = str(state.get("smtp_user") or "").strip()
    settings.smtp_ssl = _as_bool(state.get("smtp_ssl"))
    settings.smtp_starttls = _as_bool(state.get("smtp_starttls"))
    password = (password_override or "").strip()
    if not password:
        password = str(state.get("smtp_password") or base.smtp_password or "")
    settings.smtp_password = password
    settings.mail_from = str(state.get("from_email") or "").strip()
    settings.mail_from_name = str(state.get("from_name") or "").strip()
    settings.mail_reply_to = str(state.get("reply_to") or "").strip()
    settings.mail_bcc = str(state.get("bcc") or "").strip()
    settings.mail_daily_limit = _bounded_int(state.get("daily_limit"), 1, 1000, 50)
    try:
        settings.mail_delay = max(0.0, float(state.get("delay") or 0))
    except (TypeError, ValueError):
        settings.mail_delay = 0.0
    if not settings.mail_from:
        settings.mail_from = settings.smtp_user
    return settings


def save_uploaded_resume(
    settings: Settings, filename: str, content: bytes
) -> Path:
    """Store an uploaded resume under ``data/resumes`` with a safe name."""
    from werkzeug.utils import secure_filename

    clean = secure_filename(filename or "resume.pdf")
    if not clean:
        clean = "resume.pdf"
    suffix = Path(clean).suffix.lower()
    if suffix not in {".pdf", ".doc", ".docx", ".rtf", ".txt"}:
        raise ValueError("Резюме должно быть PDF, DOC, DOCX, RTF или TXT.")
    target_dir = resume_dir(settings)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / clean
    stem = target.stem
    counter = 2
    while target.exists():
        target = target_dir / f"{stem}-{counter}{suffix}"
        counter += 1
    target.write_bytes(content)
    return target
