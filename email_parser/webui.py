"""Local web UI: draw an area on a map, pick categories, get an Excel report."""

from __future__ import annotations

import argparse
import logging
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

from .categories import CATEGORY_GROUPS, FOOD_CATEGORIES
from .config import PROJECT_ROOT, Settings
from .pipeline import run
from .progress import ProgressTracker
from .utils import parse_bbox

log = logging.getLogger(__name__)


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
            if self.state == "done":
                for name in ("final_contacts.xlsx", "final_contacts.csv"):
                    if (self.out_dir / name).exists():
                        files.append(name)
            return {
                "state": self.state,
                "log": self.log[-600:],
                "stats": self.stats,
                "progress": self.progress.snapshot(),
                "error": self.error,
                "files": files,
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
            f"Файл: {job.out_dir / 'final_contacts.xlsx'}"
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


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    state: dict[str, Job | None] = {"job": None}
    lock = threading.Lock()

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            categories=list(FOOD_CATEGORIES),
            groups={name: list(items) for name, items in CATEGORY_GROUPS.items()},
        )

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
            return jsonify({"error": "Для категорий нужен город или выделенная область"}), 400

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

    return app


def main(argv: list[str] | None = None) -> int:
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

    app = create_app()
    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"UI: {url}")
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
