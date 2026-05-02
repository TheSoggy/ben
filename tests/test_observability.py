"""Tests for src/observability.py.

Run with:
    pytest engines/ben/tests/test_observability.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from threading import BoundedSemaphore, Lock

import pytest

# Add src/ to import path so the module-under-test can be imported without
# an editable install. Mirrors the layout the gunicorn process uses.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from observability import (  # noqa: E402  — sys.path tweak above is intentional
    BEN_LOCK_ACQUISITIONS,
    BEN_LOCK_HOLD,
    BEN_LOCK_WAIT,
    BEN_REQUEST,
    BEN_WORKERS_BUSY,
    lock_timed,
    request_timed,
)


def _sample_value(metric_collection, suffix: str, labels: dict[str, str]) -> float:
    """Find a sample by name suffix + labels. Returns 0.0 when absent.

    `prometheus_client` only emits samples for label combinations that
    have been observed at least once; the helpers treat "not yet
    observed" as zero so call-sites can baseline before the first
    observation without special-casing.
    """
    metric = next(iter(metric_collection))
    for s in metric.samples:
        if s.name.endswith(suffix) and s.labels == labels:
            return float(s.value)
    return 0.0


def _sample_count(histogram, **labels) -> float:
    return _sample_value(histogram.collect(), "_count", labels)


def _sample_sum(histogram, **labels) -> float:
    return _sample_value(histogram.collect(), "_sum", labels)


def _gauge_value(gauge, **labels) -> float:
    metric = next(iter(gauge.collect()))
    for s in metric.samples:
        if s.labels == labels:
            return float(s.value)
    return 0.0


def _counter_value(counter, **labels) -> float:
    return _sample_value(counter.collect(), "_total", labels)


@pytest.mark.unit
def test_lock_timed_records_wait_and_hold() -> None:
    lock = Lock()

    before_wait_count = _sample_count(BEN_LOCK_WAIT, lock="play")
    before_hold_count = _sample_count(BEN_LOCK_HOLD, lock="play")
    before_acquire_count = _counter_value(BEN_LOCK_ACQUISITIONS, lock="play")
    before_hold_sum = _sample_sum(BEN_LOCK_HOLD, lock="play")

    sleep_for = 0.02
    with lock_timed(lock, "play"):
        time.sleep(sleep_for)

    assert _sample_count(BEN_LOCK_WAIT, lock="play") == before_wait_count + 1
    assert _sample_count(BEN_LOCK_HOLD, lock="play") == before_hold_count + 1
    assert _counter_value(BEN_LOCK_ACQUISITIONS, lock="play") == before_acquire_count + 1

    # Hold sum should advance by at least the time we slept for. We allow
    # a generous lower bound because Histogram observation precision can
    # round down by a microsecond or two on slow CI runners.
    assert _sample_sum(BEN_LOCK_HOLD, lock="play") >= before_hold_sum + sleep_for * 0.95


@pytest.mark.unit
def test_lock_timed_increments_then_decrements_workers_busy() -> None:
    lock = Lock()

    baseline = _gauge_value(BEN_WORKERS_BUSY, lock="play")

    with lock_timed(lock, "play"):
        # Inside the block the gauge should reflect one extra busy worker.
        assert _gauge_value(BEN_WORKERS_BUSY, lock="play") == baseline + 1

    assert _gauge_value(BEN_WORKERS_BUSY, lock="play") == baseline


@pytest.mark.unit
def test_lock_timed_releases_lock_on_exception() -> None:
    """If the wrapped block raises, the lock must still be released."""
    lock = Lock()
    baseline = _gauge_value(BEN_WORKERS_BUSY, lock="bid")

    with pytest.raises(RuntimeError, match="boom"):
        with lock_timed(lock, "bid"):
            raise RuntimeError("boom")

    # Gauge restored.
    assert _gauge_value(BEN_WORKERS_BUSY, lock="bid") == baseline
    # Lock available — `acquire(blocking=False)` returns True only if the
    # mutex is unlocked. If the finally clause forgot to release it, this
    # would return False.
    assert lock.acquire(blocking=False) is True
    lock.release()


@pytest.mark.unit
def test_request_timed_default_outcome_is_ok() -> None:
    before = _sample_count(BEN_REQUEST, endpoint="/play", outcome="ok")

    with request_timed("/play"):
        pass

    assert _sample_count(BEN_REQUEST, endpoint="/play", outcome="ok") == before + 1


@pytest.mark.unit
def test_request_timed_outcome_can_be_overridden() -> None:
    before = _sample_count(BEN_REQUEST, endpoint="/play", outcome="server_error")

    with request_timed("/play") as obs:
        obs.outcome = "server_error"

    assert (
        _sample_count(BEN_REQUEST, endpoint="/play", outcome="server_error")
        == before + 1
    )


@pytest.mark.unit
def test_metrics_exposition_emits_lock_series() -> None:
    """Prometheus text exposition should expose our histograms after at
    least one observation, regardless of whether anything else is
    registered against the default registry.
    """
    from prometheus_client import generate_latest

    lock = Lock()
    with lock_timed(lock, "play"):
        pass

    body = generate_latest().decode("utf-8")

    assert "ben_lock_wait_seconds_bucket" in body
    assert "ben_lock_hold_seconds_bucket" in body
    assert 'lock="play"' in body


@pytest.mark.unit
def test_lock_timed_works_with_bounded_semaphore() -> None:
    """`model_lock_play` is a `BoundedSemaphore(N)` in production once
    TF warmup has populated the trace cache. The `lock_timed` helper
    must work with both `Lock` and `BoundedSemaphore` (same
    `acquire`/`release` API). This guard prevents accidentally
    coupling `lock_timed` to `Lock`-specific behaviour in the future.
    """
    sem = BoundedSemaphore(2)

    baseline_busy = _gauge_value(BEN_WORKERS_BUSY, lock="play")
    baseline_count = _sample_count(BEN_LOCK_HOLD, lock="play")

    with lock_timed(sem, "play"):
        assert _gauge_value(BEN_WORKERS_BUSY, lock="play") == baseline_busy + 1

    assert _gauge_value(BEN_WORKERS_BUSY, lock="play") == baseline_busy
    assert _sample_count(BEN_LOCK_HOLD, lock="play") == baseline_count + 1


@pytest.mark.unit
def test_bounded_semaphore_admits_n_concurrent_holders() -> None:
    """Sanity check on the BoundedSemaphore semantics we rely on:
    N=2 must allow 2 simultaneous acquisitions. If TF or pythonnet ever
    swaps in a different sync primitive that doesn't support this,
    `lock_timed` would still "work" but the throughput win disappears.
    """
    sem = BoundedSemaphore(2)

    assert sem.acquire(blocking=False) is True
    assert sem.acquire(blocking=False) is True
    # Third call must refuse (capacity exhausted).
    assert sem.acquire(blocking=False) is False

    # Release one — capacity should reopen.
    sem.release()
    assert sem.acquire(blocking=False) is True

    # Now exactly 2 are held; release them.
    sem.release()
    sem.release()
