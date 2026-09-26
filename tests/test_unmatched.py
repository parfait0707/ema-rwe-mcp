"""Unrecognised Japanese queries are logged, while a dictionary is configured, so it can be extended."""

import json
from pathlib import Path

import pytest

from ema_rwe.service import Service

EXAMPLE = Path(__file__).resolve().parents[1] / "data" / "terminology.example.json"


@pytest.fixture(autouse=True)
def example_dictionary(monkeypatch):
    monkeypatch.setenv("EMA_TERMINOLOGY_PATH", str(EXAMPLE))


async def test_nothing_is_logged_without_a_dictionary(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("EMA_TERMINOLOGY_PATH", str(tmp_path))  # an empty dictionary folder
    service = Service(settings)
    plan = await service.plan_study_search("線維筋痛症の研究")
    assert plan["status"] == "needs_client_translation" and plan["unmatched_logged"] is False
    assert not service.unmatched_log_path.exists()


async def test_unmatched_japanese_queries_are_logged_with_counts(settings, csv_file):
    service = Service(settings)
    log = service.unmatched_log_path
    assert log == settings.db_path.parent / "terminology_unmatched.json"
    first = await service.plan_study_search("線維筋痛症の研究")
    assert first["status"] == "needs_client_translation" and first["unmatched_logged"] is True
    service.search_studies("線維筋痛症の研究", darwin_only=False)
    await service.compare_protocols("q", ["線維筋痛症の研究", "fibromyalgia"])
    saved = json.loads(log.read_text(encoding="utf-8"))
    assert set(saved) == {"線維筋痛症の研究"}
    entry = saved["線維筋痛症の研究"]
    assert entry["count"] == 3
    assert entry["tools"] == ["plan_study_search", "search_studies", "compare_protocols"]
    assert entry["first_seen"] <= entry["last_seen"]
    summary = service.catalogue_status()["unmatched_terms"]
    assert summary["distinct"] == 1 and summary["top"][0]["text"] == "線維筋痛症の研究"
    assert summary["top"][0]["count"] == 3


async def test_recognised_or_english_queries_are_not_logged(settings):
    service = Service(settings)
    known = await service.plan_study_search("肝障害をアウトカムとした研究")
    assert known["status"] == "planned" and known["unmatched_logged"] is False
    english = await service.plan_study_search("liver injury")
    assert english["unmatched_logged"] is False
    service.search_studies("", darwin_only=False)
    assert not service.unmatched_log_path.exists()
    assert service.catalogue_status()["unmatched_terms"]["distinct"] == 0


async def test_corrupt_log_is_replaced_not_fatal(settings):
    service = Service(settings)
    service.unmatched_log_path.parent.mkdir(parents=True, exist_ok=True)
    service.unmatched_log_path.write_text("{not json", encoding="utf-8")
    result = await service.plan_study_search("原因不明の症候群")
    assert result["unmatched_logged"] is True
    assert (
        json.loads(service.unmatched_log_path.read_text(encoding="utf-8"))["原因不明の症候群"]["count"] == 1
    )
