"""Tests for the claim checks' choice of hidden-card splits.

Claimer.claimcheck runs whenever declarer could take more than 10 tricks
less the trick number, so at trick one on any slam. It used to build every
way of splitting the hidden cards as a Python list before keeping 50 of them:
at trick one that is 25 cards split 12 against 13, C(25, 12) = 5,200,300
tuples, about 800 MB and two seconds. On BridgeArena's bench it took a
one-worker Ben from 1,000 MiB past its 1,792 MiB cap, and the kernel killed it
mid-answer (2026-11-07). These tests pin that the splits are drawn without
building the list, and that what is drawn is still distinct and the right size.

Run with:
    pytest engines/ben/tests/test_claim_splits.py
"""

from __future__ import annotations

import sys
import tracemalloc
from itertools import combinations
from math import comb
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import deck52  # noqa: E402
from claim import Claimer, _distinct_splits  # noqa: E402


def _hand52(pbn_hand):
    hand = np.zeros(52, dtype=np.int32)
    for suit_i, holding in enumerate(pbn_hand.split(".")):
        for rank in holding:
            hand[deck52.encode_card("SHDC"[suit_i] + rank)] = 1
    return hand


class _StubSolver:
    """Every card makes every trick on every split, and the calls are kept."""

    def __init__(self):
        self.calls = []

    def solve(self, strain_i, leader_i, current_trick, hands_pbn, mode, purpose=""):
        self.calls.append(hands_pbn)
        return {0: [12] * len(hands_pbn)}


def test_claimcheck_at_trick_one_of_a_slam_does_not_build_every_split():
    # S3 set 37: West declares 6NT and plays to the lead of the five of hearts.
    west = _hand52("KQ84.A4.A876.KJ7")
    dummy = _hand52("AT93.KQT9.Q2.AT5")
    h5 = deck52.encode_card("H5")
    solver = _StubSolver()
    tracemalloc.start()
    Claimer(False, solver).claimcheck(
        strain_i=0, player_i=3, hands52=[west, dummy], tricks52=[], claim_cards=[0],
        shown_out_suits=[[], [], [], []], missing_cards=[6, 5, 5, 9], current_trick=[h5],
        n_samples=50, tricks=12,
    )
    peak_mb = tracemalloc.get_traced_memory()[1] / 2**20
    tracemalloc.stop()
    assert peak_mb < 20, f"claimcheck peaked at {peak_mb:.0f} MB"
    samples = solver.calls[0]
    assert len(samples) == 50
    assert len(set(samples)) == 50


def test_distinct_splits_draws_distinct_splits_of_the_given_size():
    cards = list(range(25))
    splits = _distinct_splits(cards, 12, 50)
    assert len(splits) == 50
    assert len(set(splits)) == 50
    assert all(len(s) == 12 and set(s) <= set(cards) for s in splits)


def test_distinct_splits_returns_every_split_when_there_are_fewer_than_asked():
    cards = [3, 7, 11, 20, 31]
    splits = _distinct_splits(cards, 2, 50)
    assert sorted(splits) == sorted(combinations(cards, 2))
    assert len(splits) == comb(5, 2)


def test_distinct_splits_with_few_splits_still_samples_without_repeats():
    cards = list(range(8))
    splits = _distinct_splits(cards, 4, 50)  # 70 splits, 50 asked
    assert len(splits) == 50
    assert len(set(splits)) == 50
