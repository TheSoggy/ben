"""Tests for binary.convert_hands, the vectorised hand conversion in sample.py.

It replaced a per-hand Python loop, `parse_hand_f(n_to)(handxxto52str(hand,
n_from))`, on the sampling path of every /bid, /lead and /play (ported from
upstream v0.9.0.0). The port is only safe if it returns exactly what the loop
did, so Ben's decisions stay identical; these tests pin that.

Run with:
    pytest engines/ben/tests/test_convert_hands.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import binary  # noqa: E402
import deck52  # noqa: E402


def _random_hands(n_cards, count, rng):
    hands = []
    for _ in range(count):
        hand52 = np.zeros(52, dtype=int)
        hand52[rng.permutation(52)[:13]] = 1
        hands.append(binary.parse_hand_f(n_cards)(deck52.deal_to_str(hand52))[0])
    return np.array(hands)


@pytest.mark.parametrize("n_from,n_to", [(32, 24), (24, 32), (32, 32)])
def test_matches_the_per_hand_loop(n_from, n_to):
    hands = _random_hands(n_from, 2000, np.random.default_rng(1))
    loop = np.array([binary.parse_hand_f(n_to)(deck52.handxxto52str(h, n_from))[0] for h in hands])
    assert np.array_equal(binary.convert_hands(hands, n_from, n_to), loop)


def test_keeps_leading_dimensions():
    # sample.py passes (samples, players, cards); each hand converts on its own
    hands = _random_hands(32, 12, np.random.default_rng(2)).reshape(4, 3, 32)
    converted = binary.convert_hands(hands, 32, 24)
    assert converted.shape == (4, 3, 24)
    assert np.array_equal(converted[2, 1], binary.convert_hands(hands[2, 1], 32, 24))
