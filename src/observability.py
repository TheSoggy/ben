"""Prometheus metrics for the Ben game API.

Exposes lock-wait, lock-hold, request latency and a workers-busy gauge
through the standard `prometheus_client` collectors. The `/metrics`
endpoint in `gameapi.py` serves them in the Prometheus text exposition
format.

Phase 5C of `docs/plans/ben-observability.md` in the BridgeArena repo.
The single global `model_lock_play` mutex serialises every TF inference
and is the suspected source of /play tail latency under burst load.
Splitting wait time vs. hold time tells us whether the bottleneck is
contention (wait dominates) or genuinely slow inference (hold dominates).

Bucket boundaries match the BridgeArena-side plan and bracket the
operating range. DO NOT change without an observability-plan update —
Prometheus invalidates historical comparisons when bucket lists change.
"""

from contextlib import contextmanager
from threading import Lock
from typing import Iterator

from prometheus_client import Counter, Gauge, Histogram

# --- Bucket boundaries (seconds) -------------------------------------------

# Lock acquisition is sub-second under healthy load; we want fine-grained
# resolution down to 1 ms to detect contention before it shows up in the
# request histogram.
_LOCK_BUCKETS_SECONDS = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

# Request latency: same shape as the BridgeArena-side histogram, in seconds
# (Prometheus convention). Mirrors `@ben_request_buckets` in
# `lib/bridgearena_web/telemetry.ex` (which is in milliseconds — same
# physical boundaries, different unit).
_REQUEST_BUCKETS_SECONDS = (
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)

# --- Collectors -------------------------------------------------------------

BEN_LOCK_WAIT = Histogram(
    "ben_lock_wait_seconds",
    "Time spent waiting to acquire a model lock.",
    labelnames=("lock",),
    buckets=_LOCK_BUCKETS_SECONDS,
)

BEN_LOCK_HOLD = Histogram(
    "ben_lock_hold_seconds",
    "Time spent holding a model lock.",
    labelnames=("lock",),
    buckets=_LOCK_BUCKETS_SECONDS,
)

BEN_REQUEST = Histogram(
    "ben_request_seconds",
    "Flask request handler latency, end to end.",
    labelnames=("endpoint", "outcome"),
    buckets=_REQUEST_BUCKETS_SECONDS,
)

BEN_WORKERS_BUSY = Gauge(
    "ben_workers_busy",
    "Workers currently inside a model-locked critical section.",
    labelnames=("lock",),
)

BEN_LOCK_ACQUISITIONS = Counter(
    "ben_lock_acquisitions_total",
    "Total successful acquisitions of a model lock.",
    labelnames=("lock",),
)


@contextmanager
def lock_timed(lock: Lock, name: str) -> Iterator[None]:
    """Context manager that records wait/hold time around a lock acquire.

    `name` becomes the value of the ``lock`` label on every emitted metric.
    Use the same string ("play", "bid") across all wrapping sites so the
    series stay clean.

    Side effects per call:

    * `ben_lock_wait_seconds{lock=name}` observes seconds spent in
      ``lock.acquire()``.
    * `ben_workers_busy{lock=name}` increments while the body runs and
      decrements on exit (always — even on exceptions).
    * `ben_lock_acquisitions_total{lock=name}` increments once after
      a successful acquire.
    * `ben_lock_hold_seconds{lock=name}` observes seconds the body held
      the lock.
    """
    # Prefer time.monotonic() for elapsed-time measurements: monotonic is
    # not subject to wall-clock adjustments, so we never see negative deltas.
    from time import monotonic

    wait_start = monotonic()
    lock.acquire()
    BEN_LOCK_WAIT.labels(lock=name).observe(monotonic() - wait_start)
    BEN_LOCK_ACQUISITIONS.labels(lock=name).inc()
    BEN_WORKERS_BUSY.labels(lock=name).inc()

    hold_start = monotonic()
    try:
        yield
    finally:
        BEN_LOCK_HOLD.labels(lock=name).observe(monotonic() - hold_start)
        BEN_WORKERS_BUSY.labels(lock=name).dec()
        lock.release()


@contextmanager
def request_timed(endpoint: str) -> Iterator["RequestObservation"]:
    """Context manager that records Flask request latency.

    Yields a small object whose ``outcome`` attribute should be set to one
    of ``"ok"``, ``"client_error"``, ``"server_error"`` (or any other short
    label) before the block exits. Default is ``"ok"`` — set it to a
    failure label when an exception is caught or a non-2xx response is
    returned.

    Use the actual route path as ``endpoint`` ("/play", "/bid", ...) so the
    series line up with the BridgeArena-side `:path` tag.
    """
    from time import monotonic

    obs = RequestObservation()
    start = monotonic()
    try:
        yield obs
    finally:
        BEN_REQUEST.labels(endpoint=endpoint, outcome=obs.outcome).observe(
            monotonic() - start
        )


class RequestObservation:
    """Mutable holder for the request-outcome label.

    The handler updates ``outcome`` in place before the surrounding
    `request_timed` context exits.
    """

    __slots__ = ("outcome",)

    def __init__(self) -> None:
        self.outcome = "ok"
