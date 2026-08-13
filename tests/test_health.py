"""Tests for src/health.py — the solver-exercising /health check.

The health check must prove the double-dummy solver path works, not just
that gunicorn answers: on 2026-08-05 a `DDSSolver.solve` signature drift
400'd /play, /lead and /claim for hours while `/` stayed 200. These tests
pin the contract: a real solve through the same wrapper the bot paths
use, a deterministic expected answer, and every failure class reported
as unhealthy rather than raised.

Run with:
    pytest engines/ben/tests/test_health.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add src/ to import path so the module-under-test can be imported without
# an editable install. Mirrors the layout the gunicorn process uses.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import health  # noqa: E402
from health import EXPECTED_TRICKS, HEALTH_PBN, run_health_check  # noqa: E402
from ddsolver.ddssolver import DDSSolver  # noqa: E402


class RecordingSolver:
    """Stands in for DDSSolver; records the call and returns a canned result."""

    def __init__(self, result=None, exc=None):
        self.result = {0: [EXPECTED_TRICKS]} if result is None else result
        self.exc = exc
        self.calls = []

    def solve(self, strain_i, leader_i, current_trick, hands_pbn, solutions, purpose=""):
        self.calls.append(
            {
                "strain_i": strain_i,
                "leader_i": leader_i,
                "current_trick": current_trick,
                "hands_pbn": hands_pbn,
                "solutions": solutions,
                "purpose": purpose,
            }
        )
        if self.exc is not None:
            raise self.exc
        return self.result


@pytest.mark.unit
@pytest.mark.parametrize("leader", [0, 1, 2, 3])
def test_real_solver_reports_healthy_from_every_seat(leader) -> None:
    """A live DDS solve answers the fixture correctly from all four seats.

    Production always solves with the default leader; parametrizing here
    proves the fixture's claimed symmetry rather than trusting it.
    """
    solver = DDSSolver(max_threads=1)

    report = run_health_check(solver, leader=leader)

    assert report["error"] is None
    assert report["ok"] is True
    assert report["elapsed_ms"] >= 0


@pytest.mark.unit
def test_solve_called_through_the_bot_path_contract() -> None:
    """The check exercises the exact call shape the bot paths use.

    `purpose=` is the keyword whose drift caused the 2026-08-05 outage —
    passing it here means a recurrence turns /health red instead of
    hiding until a game hits the DD path.
    """
    solver = RecordingSolver()

    run_health_check(solver)

    [call] = solver.calls
    assert call["strain_i"] == 0  # notrump
    assert call["leader_i"] == 0  # production default
    assert call["current_trick"] == []
    assert call["hands_pbn"] == [HEALTH_PBN]
    assert call["solutions"] == 3
    assert call["purpose"] == "health"


@pytest.mark.unit
def test_raising_solver_reports_unhealthy_instead_of_raising() -> None:
    solver = RecordingSolver(exc=RuntimeError("boom"))

    report = run_health_check(solver)

    assert report["ok"] is False
    assert "RuntimeError" in report["error"]
    assert "boom" in report["error"]


@pytest.mark.unit
def test_wrong_trick_count_reports_unhealthy() -> None:
    """A solver that answers, but wrongly, is broken — not healthy."""
    solver = RecordingSolver(result={0: [EXPECTED_TRICKS - 1]})

    report = run_health_check(solver)

    assert report["ok"] is False
    assert "expected" in report["error"]


@pytest.mark.unit
def test_empty_result_reports_unhealthy() -> None:
    """{} is DDSolver's I-solved-nothing shape (e.g. all PBNs rejected)."""
    solver = RecordingSolver(result={})

    report = run_health_check(solver)

    assert report["ok"] is False
    assert report["error"] is not None


class RecordingBotFactory:
    """Stands in for `BBABotBid.get_dll()["EPBot"]`."""

    def __init__(self, exc=None, bot=None, version="8739"):
        self.exc = exc
        self.bot = _StubBot(version) if bot is None else bot
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.bot


class _StubBot:
    def __init__(self, version):
        self._version = version

    def version(self):
        if isinstance(self._version, Exception):
            raise self._version
        return self._version


@pytest.mark.unit
def test_bot_factory_is_exercised_when_supplied() -> None:
    """The bidding half of the engine is checked, not just the solver.

    The 2026-08-13 outage broke every EPBot construction while the solver
    stayed perfect; a check that only solves reports that as healthy.
    """
    solver = RecordingSolver()
    factory = RecordingBotFactory()

    report = run_health_check(solver, bot_factory=factory)

    assert factory.calls == 1
    assert report["ok"] is True
    assert report["error"] is None


@pytest.mark.unit
def test_failing_bot_construction_reports_unhealthy() -> None:
    solver = RecordingSolver()
    factory = RecordingBotFactory(exc=OverflowError("Arithmetic operation resulted in an overflow"))

    report = run_health_check(solver, bot_factory=factory)

    assert report["ok"] is False
    assert "OverflowError" in report["error"]


@pytest.mark.unit
def test_bot_factory_sys_exit_is_caught_not_propagated() -> None:
    """`BBABotBid.get_dll` calls sys.exit(1) when the DLL will not load.

    SystemExit is not an Exception, so an unguarded check would kill the
    gunicorn worker that was only asking whether it was healthy.
    """
    solver = RecordingSolver()
    factory = RecordingBotFactory(exc=SystemExit(1))

    report = run_health_check(solver, bot_factory=factory)

    assert report["ok"] is False
    assert "SystemExit" in report["error"]


@pytest.mark.unit
def test_bot_without_version_reports_unhealthy() -> None:
    """Constructing is not enough — the object has to answer."""
    solver = RecordingSolver()
    factory = RecordingBotFactory(version=None)

    report = run_health_check(solver, bot_factory=factory)

    assert report["ok"] is False
    assert "version" in report["error"]


@pytest.mark.unit
def test_absent_bot_factory_skips_the_bidding_check() -> None:
    """Ben without the EPBot DLL must not be unhealthy forever."""
    solver = RecordingSolver()

    report = run_health_check(solver, bot_factory=None)

    assert report["ok"] is True


@pytest.mark.unit
def test_broken_solver_short_circuits_before_building_a_bot() -> None:
    solver = RecordingSolver(exc=RuntimeError("boom"))
    factory = RecordingBotFactory()

    report = run_health_check(solver, bot_factory=factory)

    assert report["ok"] is False
    assert factory.calls == 0


@pytest.mark.unit
def test_negative_tick_count_names_the_host_reboot_remedy(monkeypatch) -> None:
    """The error has to say *reboot the host*, not *restart the container*.

    CLOCK_MONOTONIC is not namespaced per container, so a restart changes
    nothing — the single most expensive fact to establish during the
    2026-08-13 outage.
    """
    monkeypatch.setattr(health, "_tick_count_is_negative", lambda: True)
    solver = RecordingSolver()
    factory = RecordingBotFactory(exc=OverflowError("overflow"))

    report = run_health_check(solver, bot_factory=factory)

    assert "rebooting the host" in report["error"]
    assert "OverflowError" in report["error"]


@pytest.mark.unit
def test_positive_tick_count_leaves_the_error_unadorned(monkeypatch) -> None:
    """An unrelated EPBot fault must not be misattributed to the clock."""
    monkeypatch.setattr(health, "_tick_count_is_negative", lambda: False)
    solver = RecordingSolver()
    factory = RecordingBotFactory(exc=RuntimeError("something else"))

    report = run_health_check(solver, bot_factory=factory)

    assert "rebooting the host" not in report["error"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("uptime_seconds", "expected"),
    [
        (60.0, False),  # fresh boot
        (2**31 / 1000 - 1, False),  # last second before the flip
        (2**31 / 1000, True),  # 24.855 days — the 2026-08-13 crossing
        (2**32 / 1000 - 1, True),  # last second before wrapping positive
        (2**32 / 1000, False),  # 49.71 days — wrapped, healthy again
        (10_739_825.534, True),  # the reading taken live during the outage
    ],
)
def test_tick_count_sign_tracks_host_uptime(monkeypatch, uptime_seconds, expected) -> None:
    """`time.monotonic` reads the same clock .NET's TickCount truncates."""
    monkeypatch.setattr(health.time, "monotonic", lambda: uptime_seconds)

    assert health._tick_count_is_negative() is expected
