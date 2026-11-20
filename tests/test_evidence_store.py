"""Evidence store (spec v1.31): prefetching a source type's protocols, facts derived from analyses,
facts distributed without quotes and their restoration from a local PDF."""

import asyncio

import pytest

from ema_rwe import catalogue
from ema_rwe.domain import RWEError, Study
from ema_rwe.service import Service


def study(study_id: str, types=("claims",), listed=True) -> Study:
    return Study(
        study_id=study_id,
        title=f"Study {study_id}",
        study_type="Non-interventional study",
        data_source_types=list(types),
        protocol_listed=listed,
        source_url=f"https://example.test/study/{study_id}",
    )


@pytest.fixture
def prefetch(settings, monkeypatch):
    service = Service(settings)
    for s in (study("1"), study("2"), study("3", types=("ehr",)), study("4", listed=False)):
        service.repo.upsert(s)
    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(catalogue.asyncio, "sleep", sleep)
    return service, sleeps


def run(service, outcomes, **kwargs):
    calls = []

    async def get_protocol(study_id):
        calls.append(study_id)
        outcome = outcomes[study_id].pop(0)
        if outcome != "ok":
            raise RWEError(outcome, outcome)
        return {}

    service.get_protocol = get_protocol
    return asyncio.run(service.prefetch_protocols("claims", **kwargs)), calls


def test_prefetch_takes_only_the_source_types_studies_with_a_listed_protocol(prefetch):
    service, sleeps = prefetch
    summary, calls = run(service, {"1": ["ok"], "2": ["ok"]}, interval=30)
    assert calls == ["1", "2"] and summary["fetched"] == 2 and summary["studies"] == 2
    assert sleeps == [30]


def test_a_rate_limit_widens_the_gap_to_a_minute_and_retries_the_same_study(prefetch):
    service, sleeps = prefetch
    summary, calls = run(service, {"1": ["EMA_RATE_LIMITED", "ok"], "2": ["ok"]}, interval=30)
    assert calls == ["1", "1", "2"] and summary["fetched"] == 2
    assert sleeps == [60, 60] and summary["gap_seconds"] == 60


def test_a_second_rate_limit_in_a_row_pauses_before_retrying(prefetch):
    service, sleeps = prefetch
    outcomes = {"1": ["EMA_RATE_LIMITED", "EMA_RATE_LIMITED", "ok"], "2": ["ok"]}
    summary, calls = run(service, outcomes, interval=30, pause=900)
    assert calls == ["1", "1", "1", "2"] and summary["pauses"] == 1
    assert sleeps == [60, 900, 60, 60]


def test_repeated_rate_limits_stop_the_run_after_three_pauses(prefetch):
    service, _ = prefetch
    summary, calls = run(service, {"1": ["EMA_RATE_LIMITED"] * 10, "2": ["ok"]}, interval=30)
    assert summary["pauses"] == 3 and summary["stopped"] and "2" not in calls


def test_a_rerun_skips_studies_found_without_a_protocol(prefetch):
    service, _ = prefetch
    summary, _ = run(service, {"1": ["PROTOCOL_NOT_FOUND"], "2": ["EMA_UNAVAILABLE"]})
    assert summary["no_protocol"] == 1 and summary["errors"] == {"EMA_UNAVAILABLE": 1}
    summary, calls = run(service, {"2": ["ok"]})
    assert calls == ["2"] and summary["queued"] == 1
