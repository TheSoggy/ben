"""Solver-exercising health check backing the /health endpoint.

The container health check used to be `curl -f /`, which only proves
gunicorn is answering: on 2026-08-05 a `DDSSolver.solve` signature drift
400'd /play, /lead and /claim for hours while `/` stayed 200 and the
accessory reported `Up (healthy)`. This module runs a real double-dummy
solve through the same `DDSSolver.solve(..., purpose=)` call the bot
paths use, so a broken solver path turns every probe consumer red.

Kept import-light (no Flask, no TensorFlow) so tests can exercise it
against a real solver without booting gameapi.
"""

from __future__ import annotations

import itertools
import time

# Three-card notrump endgame: every hand holds only its own suit's AKQ,
# so whichever seat is on lead cashes exactly three tricks — every legal
# card DDS returns must score 3. The answer is the same from all four
# seats, which lets the leader rotate for cache-busting without changing
# the expected result.
HEALTH_PBN = "N:AKQ... .AKQ.. ..AKQ. ...AKQ"
EXPECTED_TRICKS = 3

# DDSolver maps strain_i 0 -> DDS trump 4 (notrump).
_NT_STRAIN = 0

# Rotates the leader N -> E -> S -> W across calls so consecutive probes
# are distinct solves rather than transposition-table replays. Per-process
# counter: each gunicorn worker rotates independently, which is fine —
# the point is variation, not global coverage.
_leader = itertools.count()


def run_health_check(solver):
    """Run one minimal double-dummy solve; report rather than raise.

    Returns ``{"ok": bool, "leader": int, "elapsed_ms": float,
    "error": str | None}``. Every failure class must land in ``error`` —
    this feeds container health checks, where an escaped exception is a
    500 with a traceback instead of a clean unhealthy signal.
    """
    leader = next(_leader) % 4
    started = time.perf_counter()

    try:
        # solutions=3: all legal cards with scores — the same call shape
        # (and the same purpose= keyword) every bot DD call-site uses.
        results = solver.solve(_NT_STRAIN, leader, [], [HEALTH_PBN], 3, purpose="health")
    except Exception as exc:  # noqa: BLE001 — any escape means unhealthy
        return _report(leader, started, f"solve raised {type(exc).__name__}: {exc}")

    if not results:
        return _report(leader, started, "solve returned no cards")

    scores = [score for values in results.values() for score in values]
    if any(score != EXPECTED_TRICKS for score in scores):
        return _report(
            leader,
            started,
            f"expected every card to score {EXPECTED_TRICKS} tricks, got {sorted(set(scores))}",
        )

    return _report(leader, started, None)


def _report(leader, started, error):
    return {
        "ok": error is None,
        "leader": leader,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "error": error,
    }
