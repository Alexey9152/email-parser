"""Thread-safe run progress shared by the pipeline and the web UI."""

from __future__ import annotations

import threading
from typing import Any

STAGE_LABELS = {
    "idle": "Ожидание",
    "maps": "Сбор заведений в Google Maps",
    "websites": "Проверка сайтов",
    "search": "Поиск в Google / Facebook",
    "js": "Рендер JS-сайтов",
    "facebook": "Открываем страницы Facebook",
    "guess": "Подбор адресов по MX",
    "export": "Сохраняем Excel / CSV",
    "done": "Готово",
    "error": "Ошибка",
}

_STAGE_UNITS = {
    "maps": "запросов",
    "websites": "сайтов",
    "search": "запросов",
    "js": "сайтов",
    "facebook": "страниц",
    "guess": "заведений",
    "export": "файлов",
}


class ProgressTracker:
    """Small mutable counter set; ``snapshot`` is safe to call from Flask."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "stage": "idle",
            "message": "",
            "venues_total": 0,
            "emails_found": 0,
            "sites_total": 0,
            "sites_done": 0,
            "searches_total": 0,
            "searches_done": 0,
            "js_total": 0,
            "js_done": 0,
            "fb_total": 0,
            "fb_done": 0,
            "guess_total": 0,
            "guess_done": 0,
            "errors": 0,
        }

    def update(self, **values: Any) -> None:
        with self._lock:
            self._state.update(values)

    def increment(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._state[key] = int(self._state.get(key) or 0) + amount

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)

        stage = str(state.get("stage") or "idle")
        total, done, unit = self._stage_counters(state, stage)
        remaining = max(0, total - done)
        if stage in {"done", "export"}:
            percent = 100.0
        elif total > 0:
            percent = max(0.0, min(100.0, done * 100.0 / total))
        else:
            percent = 0.0

        state.update(
            {
                "stage_label": STAGE_LABELS.get(stage, stage),
                "total": total,
                "processed": min(done, total) if total else done,
                "remaining": remaining,
                "unit": unit,
                "percent": round(percent, 1),
            }
        )
        return state

    @staticmethod
    def _stage_counters(state: dict[str, Any], stage: str) -> tuple[int, int, str]:
        if stage == "websites":
            return (
                int(state.get("sites_total") or 0),
                int(state.get("sites_done") or 0),
                _STAGE_UNITS["websites"],
            )
        if stage == "search":
            return (
                int(state.get("searches_total") or 0),
                int(state.get("searches_done") or 0),
                _STAGE_UNITS["search"],
            )
        if stage == "facebook":
            return (
                int(state.get("fb_total") or 0),
                int(state.get("fb_done") or 0),
                _STAGE_UNITS["facebook"],
            )
        if stage == "js":
            return (
                int(state.get("js_total") or 0),
                int(state.get("js_done") or 0),
                _STAGE_UNITS["js"],
            )
        if stage == "guess":
            return (
                int(state.get("guess_total") or 0),
                int(state.get("guess_done") or 0),
                _STAGE_UNITS["guess"],
            )
        if stage == "maps":
            return (
                int(state.get("venues_total") or 0),
                int(state.get("venues_total") or 0),
                _STAGE_UNITS["maps"],
            )
        return (0, 0, "")
