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
