"""Tests for Claimer.claimcheck's layout sampling.

claimcheck DD-checks a sample of the hidden cards' splits, and every claim
card that fails on one of them is penalised (adjust=-0.05 in
BotCardPlayer). The sample used to come from Python's global `random`,
which nothing seeds per request, so one /play request could return
different cards on repeated calls (2026-10-30: S3 against ST, 6 times in
20). These tests pin that the sample comes from the caller's generator.

Run with:
    pytest engines/ben/tests/test_claimcheck_determinism.py
"""

from __future__ import annotations

import random
import sys
from math import comb
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claim import Claimer  # noqa: E402

pytestmark = pytest.mark.unit

# Declarer (player 3) holds the top four spades, dummy the top four hearts,
# and the defenders' eight cards are the top four diamonds and clubs:
# C(8, 4) = 70 splits, more than the 50 claimcheck samples.
OUR_CARDS = [0, 1, 2, 3]
DUMMY_CARDS = [13, 14, 15, 16]
HIDDEN_CARDS = [26, 27, 28, 29, 39, 40, 41, 42]


class RecordingSolver:
    """Stands in for DDSSolver; records the layouts it is asked to solve."""

    def __init__(self):
        self.layouts = []

    def solve(self, strain_i, leader_i, current_trick, hands_pbn, solutions, purpose=""):
        self.layouts.append(list(hands_pbn))
        return {OUR_CARDS[0]: [4] * len(hands_pbn)}


def _hand(cards):
    hand = np.zeros(52, dtype=np.int32)
    hand[cards] = 1
    return hand


def _claimcheck(rng, n_samples=50):
    played = [c for c in range(52) if c not in OUR_CARDS + DUMMY_CARDS + HIDDEN_CARDS]
    tricks52 = [played[i : i + 4] for i in range(0, len(played), 4)]
    solver = RecordingSolver()
    Claimer(False, solver).claimcheck(
        strain_i=0,
        player_i=3,
        hands52=[_hand(OUR_CARDS), _hand(DUMMY_CARDS)],
        tricks52=tricks52,
        claim_cards=[OUR_CARDS[0]],
        shown_out_suits=[set(), set(), set(), set()],
        missing_cards=[0, 0, 4, 4],
        current_trick=[],
        n_samples=n_samples,
        tricks=4,
        rng=rng,
    )
    [layouts] = solver.layouts
    return layouts


def test_same_generator_seed_checks_the_same_layouts():
    random.seed(1)
    first = _claimcheck(np.random.default_rng(4300000000))
    random.seed(2)
    second = _claimcheck(np.random.default_rng(4300000000))

    assert len(first) == 50
    assert first == second


def test_samples_are_distinct_splits():
    layouts = _claimcheck(np.random.default_rng(7))

    assert len(set(layouts)) == 50


def test_checks_every_split_when_there_are_fewer_than_n_samples():
    layouts = _claimcheck(np.random.default_rng(7), n_samples=100)

    assert len(set(layouts)) == comb(len(HIDDEN_CARDS), len(HIDDEN_CARDS) // 2)
