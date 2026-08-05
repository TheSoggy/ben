"""DDSSolver must stay a drop-in replacement for DDSolver.

`gameapi.py` and `gameserver.py` construct `DDSSolver`, but every bot
call-site (botcardplayer, botopeninglead, claim, botbidder, badcontracts)
was written against `DDSolver`. When the two signatures drift, the
mismatch is invisible until a request hits the DD path at runtime — the
`purpose=` keyword drift took out /play, /lead and /claim in production
while /bid and /explain (pure NN paths) stayed green.

Run with:
    pytest engines/ben/tests/test_solver_api_parity.py
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

# Add src/ to import path so the modules-under-test can be imported without
# an editable install. Mirrors the layout the gunicorn process uses.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ddsolver.ddsolver import DDSolver  # noqa: E402  — sys.path tweak above
from ddsolver.ddssolver import DDSSolver  # noqa: E402


def _overridden_public_methods() -> list[str]:
    """Public callables DDSSolver defines that also exist on DDSolver."""
    return sorted(
        name
        for name in vars(DDSSolver)
        if not name.startswith("_")
        and callable(getattr(DDSSolver, name))
        and callable(getattr(DDSolver, name, None))
    )


@pytest.mark.unit
@pytest.mark.parametrize("method_name", _overridden_public_methods())
def test_wrapper_signature_matches_base(method_name: str) -> None:
    """Each overridden method keeps the exact signature it wraps.

    Parametrized per method so a drift report names the offender instead
    of failing on whichever one happens to be checked first.
    """
    base = inspect.signature(getattr(DDSolver, method_name))
    wrapper = inspect.signature(getattr(DDSSolver, method_name))

    assert str(wrapper) == str(base), (
        f"DDSSolver.{method_name}{wrapper} drifted from "
        f"DDSolver.{method_name}{base}"
    )


@pytest.mark.unit
def test_solve_accepts_purpose_keyword() -> None:
    """`purpose=` is passed by every DD call-site; binding must not raise.

    `Signature.bind` raises the same TypeError the live call raises, so
    this reproduces the production failure without loading the DDS
    library.
    """
    inspect.signature(DDSSolver.solve).bind(
        None,  # self
        strain_i=0,
        leader_i=1,
        current_trick=[],
        hands_pbn=["N:AKQ.AKQ.AKQ.AKQJ ... "],
        solutions=3,
        purpose="play",
    )
