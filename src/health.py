"""Solver-exercising health check backing the /health endpoint.

The container health check used to be `curl -f /`, which only proves
gunicorn is answering: on 2026-08-05 a `DDSSolver.solve` signature drift
400'd /play, /lead and /claim for hours while `/` stayed 200 and the
accessory reported `Up (healthy)`. This module runs a real double-dummy
solve through the same `DDSSolver.solve(..., purpose=)` call the bot
paths use, so a broken solver path turns every probe consumer red.

Scope: this covers the `DDSSolver.solve` -> `SolveAllBoards` path that
/play, /lead and /claim ride. /double_dummy (`CalcDDtablePBN`) and
/solve_board (`SolveBoardPBN`) call the DDS library directly and are
covered by scripts/smoke/ben-endpoints.sh in the parent repo instead.

No cache-busting is needed: DDS mode-1 transposition-table reuse keys on
trump + card distribution (see ddsolver/ddsolver.py), and a TT-warm solve
still traverses the full wrapper -> ctypes -> result-decode path, so every
fault class this check targets (signature drift, library load failure,
wrong answer) surfaces regardless of table state.

Kept import-light (no Flask, no TensorFlow) so tests can exercise it
against a real solver without booting gameapi.
"""

from __future__ import annotations

import time

# Three-card notrump endgame: every hand holds only its own suit's AKQ,
# so whichever seat is on lead cashes exactly three tricks — every legal
# card DDS returns must score 3. The answer is the same from all four
# seats, which is what lets tests prove the fixture from every leader.
HEALTH_PBN = "N:AKQ... .AKQ.. ..AKQ. ...AKQ"
EXPECTED_TRICKS = 3

# DDSolver maps strain_i 0 -> DDS trump 4 (notrump).
_NT_STRAIN = 0


def run_health_check(solver, leader=0):
    """Run one minimal double-dummy solve; report rather than raise.

    `leader` is the seat on lead (0=N .. 3=W); production uses the
    default, tests pass each seat to prove the fixture's symmetry.

    Returns ``{"ok": bool, "elapsed_ms": float, "error": str | None}``.
    Every failure class must land in ``error`` — this feeds container
    health checks, where an escaped exception is a 500 with a traceback
    instead of a clean unhealthy signal.
    """
    started = time.perf_counter()

    try:
        # solutions=3: all legal cards with scores — the same call shape
        # (and the same purpose= keyword) every bot DD call-site uses.
        results = solver.solve(_NT_STRAIN, leader, [], [HEALTH_PBN], 3, purpose="health")
    except Exception as exc:  # noqa: BLE001 — any escape means unhealthy
        return _report(started, f"solve raised {type(exc).__name__}: {exc}")

    if not results:
        return _report(started, "solve returned no cards")

    scores = [score for values in results.values() for score in values]
    if any(score != EXPECTED_TRICKS for score in scores):
        return _report(
            started,
            f"expected every card to score {EXPECTED_TRICKS} tricks, got {sorted(set(scores))}",
        )

    return _report(started, None)


def _report(started, error):
    return {
        "ok": error is None,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "error": error,
    }
