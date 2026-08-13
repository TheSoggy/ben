"""Solver-exercising health check backing the /health endpoint.

The container health check used to be `curl -f /`, which only proves
gunicorn is answering: on 2026-08-05 a `DDSSolver.solve` signature drift
400'd /play, /lead and /claim for hours while `/` stayed 200 and the
accessory reported `Up (healthy)`. This module runs a real double-dummy
solve through the same `DDSSolver.solve(..., purpose=)` call the bot
paths use, so a broken solver path turns every probe consumer red.

A solver check alone left the other half of the engine uncovered, and on
2026-08-13 that half broke: `Environment.TickCount` — a signed 32-bit
millisecond counter over CLOCK_MONOTONIC — went negative after 124.3 days
of *host* uptime, and EPBot's `Class_Initialize` overflows on it, so every
`EPBot()` construction threw. /bid, /explain, /lead and /play died while
this check, `docker ps` and the Kamal accessory all stayed green for 43
minutes. So the check now also constructs an EPBot, the way every bidding
request does.

Scope: this covers the `DDSSolver.solve` -> `SolveAllBoards` path that
/play, /lead and /claim ride, plus the EPBot construction that /bid,
/explain, /lead and /play ride. /double_dummy (`CalcDDtablePBN`) and
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

# .NET's Environment.TickCount is milliseconds since boot truncated to a
# *signed* 32-bit int, so it reads negative for the 24.85 days either side
# of every 49.71-day wrap.
_TICK_WRAP_MS = 2**32
_TICK_SIGN_FLIP_MS = 2**31


def run_health_check(solver, leader=0, bot_factory=None):
    """Solve one minimal deal and build one bidding bot; report rather than raise.

    `leader` is the seat on lead (0=N .. 3=W); production uses the
    default, tests pass each seat to prove the fixture's symmetry.

    `bot_factory` is a zero-arg callable returning a fresh EPBot —
    gameapi passes `BBABotBid.get_dll()["EPBot"]`. It stays optional
    because the EPBot DLL is optional: an install running Ben without
    BBA would otherwise report unhealthy forever. Passing None skips
    only the bidding half of the check.

    Returns ``{"ok": bool, "elapsed_ms": float, "error": str | None}``.
    Every failure class must land in ``error`` — this feeds container
    health checks, where an escaped exception is a 500 with a traceback
    instead of a clean unhealthy signal.
    """
    started = time.perf_counter()

    solve_error = _check_solve(solver, leader)
    if solve_error is not None:
        return _report(started, solve_error)

    if bot_factory is not None:
        bot_error = _check_bot(bot_factory)
        if bot_error is not None:
            return _report(started, bot_error)

    return _report(started, None)


def _check_solve(solver, leader):
    try:
        # solutions=3: all legal cards with scores — the same call shape
        # (and the same purpose= keyword) every bot DD call-site uses.
        results = solver.solve(_NT_STRAIN, leader, [], [HEALTH_PBN], 3, purpose="health")
    except Exception as exc:  # noqa: BLE001 — any escape means unhealthy
        return f"solve raised {type(exc).__name__}: {exc}"

    if not results:
        return "solve returned no cards"

    scores = [score for values in results.values() for score in values]
    if any(score != EXPECTED_TRICKS for score in scores):
        return (
            f"expected every card to score {EXPECTED_TRICKS} tricks, got {sorted(set(scores))}"
        )

    return None


def _check_bot(bot_factory):
    """Construct one EPBot and ask its version, as every bidding call does.

    SystemExit is caught alongside Exception on purpose: `BBABotBid.get_dll`
    calls `sys.exit(1)` when the DLL will not load, and a health probe must
    never be the thing that kills a gunicorn worker.
    """
    try:
        bot = bot_factory()
        version = bot.version() if bot is not None else None
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — any escape means unhealthy
        return _bot_error(f"EPBot construction raised {type(exc).__name__}: {exc}")

    if bot is None:
        return _bot_error("EPBot factory returned None")

    if version is None:
        return _bot_error("EPBot reported no version")

    return None


def _bot_error(detail):
    """Name the host-uptime cause when the clock says that is what this is.

    Worth the extra sentence: the 2026-08-13 outage cost most of its
    diagnosis time to proving that restarting the container does nothing,
    because CLOCK_MONOTONIC is not namespaced per container and so counts
    host uptime. Only a host reboot resets it.
    """
    if not _tick_count_is_negative():
        return detail

    return (
        f"{detail} — Environment.TickCount is negative (host uptime has passed a "
        "24.85-day boundary), which overflows EPBot's Class_Initialize. Restarting "
        "this container will NOT clear it; only rebooting the host will."
    )


def _tick_count_is_negative():
    """Whether .NET's Environment.TickCount currently reads negative.

    `time.monotonic()` reads the same CLOCK_MONOTONIC .NET does, so this
    needs no .NET round trip and keeps the module import-light.
    """
    return int(time.monotonic() * 1000) % _TICK_WRAP_MS >= _TICK_SIGN_FLIP_MS


def _report(started, error):
    return {
        "ok": error is None,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "error": error,
    }
