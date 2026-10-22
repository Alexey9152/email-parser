"""Progress counters used by the map UI."""

from __future__ import annotations

from email_parser.progress import ProgressTracker


def test_website_progress_reports_processed_and_remaining():
    progress = ProgressTracker()
    progress.update(
        stage="websites",
        sites_total=10,
        sites_done=4,
        venues_total=12,
        emails_found=2,
    )
    snapshot = progress.snapshot()
    assert snapshot["stage_label"] == "Проверка сайтов"
    assert snapshot["total"] == 10
    assert snapshot["processed"] == 4
    assert snapshot["remaining"] == 6
    assert snapshot["unit"] == "сайтов"
    assert snapshot["percent"] == 40.0
    assert snapshot["emails_found"] == 2


def test_search_and_facebook_progress_use_their_own_counters():
    progress = ProgressTracker()
    progress.update(stage="search", searches_total=8, searches_done=8)
    assert progress.snapshot()["percent"] == 100.0

    progress.update(stage="facebook", fb_total=4, fb_done=1)
    snapshot = progress.snapshot()
    assert snapshot["processed"] == 1
    assert snapshot["remaining"] == 3
    assert snapshot["percent"] == 25.0


def test_done_stage_is_always_hundred_percent():
    progress = ProgressTracker()
    progress.update(stage="done", venues_total=20, emails_found=11)
    snapshot = progress.snapshot()
    assert snapshot["percent"] == 100.0
    assert snapshot["stage_label"] == "Готово"
