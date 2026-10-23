"""Tests for the local per-recipient sending UI and its API."""

from __future__ import annotations

from io import BytesIO

from email_parser.config import Settings
from email_parser.exporter import save_results_xlsx
from email_parser.mailer import save_sent_log
from email_parser.mailer_state import load_state, sent_log_path
from email_parser.models import (
    SOURCE_FB_PAGE,
    STATUS_FB,
    ContactResult,
)
from email_parser.webui import create_app


def _contact() -> ContactResult:
    return ContactResult(
        title="Coastal Salt",
        website="https://coastalsaltoc.com",
        facebook_url="https://www.facebook.com/CoastalSaltOC",
        emails=["info@coastalsaltoc.com", "manager@coastalsaltoc.com"],
        email_sources={
            "info@coastalsaltoc.com": SOURCE_FB_PAGE,
            "manager@coastalsaltoc.com": SOURCE_FB_PAGE,
        },
        status=STATUS_FB,
        address="1 Ocean Ave",
    )


def _app(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        mail_from="alex@example.com",
        mail_from_name="Alex Los",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_user="alex@example.com",
        smtp_ssl=True,
        smtp_starttls=False,
        mail_daily_limit=5,
    )
    report_dir = tmp_path / "ui-test"
    report_dir.mkdir()
    save_results_xlsx(report_dir / "final_contacts.xlsx", [_contact()])
    return settings, create_app(settings)


def _base_config(report: str = "ui-test/final_contacts.xlsx") -> dict:
    return {
        "report": report,
        "letter": "Hello **Manager** at {restaurant},\n\n- **Server**\n- Cashier\n\n"
        "LinkedIn: linkedin.com/in/aleksey-los/",
        "subject": "Work & Travel application — {restaurant}",
        "from_email": "alex@example.com",
        "from_name": "Alex Los",
        "reply_to": "",
        "bcc": "",
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "smtp_user": "alex@example.com",
        "smtp_password": "",
        "smtp_ssl": True,
        "smtp_starttls": False,
        "remember_password": True,
        "daily_limit": 5,
        "resume": "",
        "attach_resume": False,
    }


def test_send_page_loads(tmp_path):
    _settings, app = _app(tmp_path)
    response = app.test_client().get("/send")
    assert response.status_code == 200
    assert "Подтвердите отправку" in response.get_data(as_text=True)
    assert "linkedin.com/in/aleksey-los/" in response.get_data(as_text=True)


def test_settings_are_saved_without_exposing_password(tmp_path):
    settings, app = _app(tmp_path)
    client = app.test_client()
    config = _base_config()
    config["letter"] = "Saved **letter**"
    config["remember_password"] = True
    config["smtp_password"] = "super-secret"

    response = client.post("/api/send/settings", json=config)
    assert response.status_code == 200
    saved = response.get_json()["settings"]
    assert saved["letter"].strip() == "Saved **letter**"
    assert saved["has_saved_password"] is True
    assert "smtp_password" not in saved

    state = load_state(settings)
    assert state["letter"].strip() == "Saved **letter**"
    assert state["smtp_password"] == "super-secret"
    assert (tmp_path / "application_email.txt").read_text(encoding="utf-8") == (
        "Saved **letter**\n"
    )

    again = client.get("/api/send/settings").get_json()["settings"]
    assert again["letter"].strip() == "Saved **letter**"
    assert again["has_saved_password"] is True
    assert "smtp_password" not in again


def test_password_is_not_persisted_without_explicit_consent(tmp_path):
    settings, app = _app(tmp_path)
    client = app.test_client()
    config = _base_config()
    config["remember_password"] = False
    config["smtp_password"] = "secret-without-consent"

    response = client.post("/api/send/settings", json=config)
    assert response.status_code == 200
    assert response.get_json()["settings"]["has_saved_password"] is False
    assert load_state(settings)["smtp_password"] == ""


def test_resume_upload_is_saved_and_attached_in_preview(tmp_path):
    _settings, app = _app(tmp_path)
    client = app.test_client()
    client.post("/api/send/settings", json=_base_config())

    response = client.post(
        "/api/send/upload-resume",
        data={"file": (BytesIO(b"%PDF-1.4 fake resume"), "my-resume.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["resume"].endswith("resumes/my-resume.pdf")
    assert body["settings"]["resume_exists"] is True

    preview = client.post(
        "/api/send/preview",
        json={
            **_base_config(),
            "email": "manager@coastalsaltoc.com",
            "resume": body["resume"],
            "attach_resume": True,
        },
    )
    assert preview.status_code == 200
    assert preview.get_json()["preview"]["attachments"] == ["my-resume.pdf"]


def test_recipient_list_and_preview_render_rich_text(tmp_path):
    _settings, app = _app(tmp_path)
    client = app.test_client()
    client.post("/api/send/settings", json=_base_config())

    listing = client.get("/api/send/recipients").get_json()
    assert listing["ok"] is True
    assert listing["stats"]["total"] == 1
    assert listing["stats"]["selected"] == 1
    assert listing["recipients"][0]["email"] == "manager@coastalsaltoc.com"
    assert listing["recipients"][0]["source_label"] == "Facebook (страница)"
    assert listing["recipients"][0]["sent"] is False

    response = client.post(
        "/api/send/preview",
        json={**_base_config(), "email": "manager@coastalsaltoc.com"},
    )
    assert response.status_code == 200
    preview = response.get_json()["preview"]
    assert "<strong>Manager</strong>" in preview["html"]
    assert "<ul" in preview["html"]
    assert "https://www.linkedin.com/in/aleksey-los/" in preview["html"]
    assert "**" not in preview["plain"]
    assert preview["to"] == "manager@coastalsaltoc.com"
    assert preview["attachments"] == []


def test_send_requires_confirmation_and_calls_one_recipient(tmp_path, monkeypatch):
    _settings, app = _app(tmp_path)
    client = app.test_client()
    client.post("/api/send/settings", json=_base_config())

    calls = []

    def fake_send_one(row, **kwargs):
        calls.append((row, kwargs))
        entry = {
            "restaurant": row.title,
            "subject": "Work & Travel application — Coastal Salt",
            "sent_at": "2026-10-23T18:00:00",
            "status": "sent",
        }
        save_sent_log(sent_log_path(_settings), {row.primary_email: entry})
        return {
            "email": row.primary_email,
            "entry": entry,
            "sent_today": 1,
        }

    monkeypatch.setattr("email_parser.webui.send_one", fake_send_one)
    payload = {**_base_config(), "email": "manager@coastalsaltoc.com"}

    refused = client.post("/api/send/send", json=payload)
    assert refused.status_code == 400
    assert calls == []

    accepted = client.post("/api/send/send", json={**payload, "confirm": True})
    assert accepted.status_code == 200, accepted.get_json()
    assert len(calls) == 1
    row, kwargs = calls[0]
    assert row.primary_email == "manager@coastalsaltoc.com"
    assert kwargs["log_path"] == sent_log_path(_settings)
    assert accepted.get_json()["recipient"]["sent"] is True


def test_send_rejects_unknown_recipient(tmp_path, monkeypatch):
    _settings, app = _app(tmp_path)
    client = app.test_client()
    client.post("/api/send/settings", json=_base_config())
    monkeypatch.setattr(
        "email_parser.webui.send_one",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("must not send unknown recipient")
        ),
    )
    response = client.post(
        "/api/send/send",
        json={
            **_base_config(),
            "email": "attacker@example.com",
            "confirm": True,
        },
    )
    assert response.status_code == 400
    assert "не найден" in response.get_json()["error"]


def test_invalid_from_email_is_rejected_on_save(tmp_path):
    _settings, app = _app(tmp_path)
    client = app.test_client()
    response = client.post(
        "/api/send/settings",
        json={**_base_config(), "from_email": "not-an-email"},
    )
    assert response.status_code == 400
