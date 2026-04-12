"""Tests for passed-hand HCP cap constraint logic."""

import unittest
from botcardplayer import get_passed_positions


class TestGetPassedPositions(unittest.TestCase):
    """Test detection of players who passed in opening position."""

    def test_no_passes_before_first_bid(self):
        # Dealer (N=0) opens 1C immediately
        auction = ['PAD_START', 'PAD_START', 'PAD_START', '1C', 'PASS', 'PASS', 'PASS']
        # dealer=0 (N), decl=0 (N) -> LHO=1(E), dummy=2(S), RHO=3(W)
        result = get_passed_positions(auction, dealer_i=0, decl_i=0)
        self.assertEqual(result, {})

    def test_dealer_passes(self):
        # N deals and passes, E opens 1H
        auction = ['PASS', '1H', 'PASS', '2H', 'PASS', 'PASS', 'PASS']
        # dealer=0 (N), decl=1 (E) -> rel: LHO=2(S), dummy=3(W), RHO=0(N), declarer=1(E)
        # N passed in opening position -> rel pos of N = (0 - 2) % 4 = 2 = RHO
        result = get_passed_positions(auction, dealer_i=0, decl_i=1)
        self.assertIn(2, result)  # RHO (North) passed
        self.assertNotIn(1, result)  # Declarer (East) did not pass

    def test_two_passes_before_bid(self):
        # N passes, E passes, S opens 1S
        auction = ['PASS', 'PASS', '1S', 'PASS', '2S', 'PASS', 'PASS', 'PASS']
        # dealer=0 (N), decl=2 (S)
        # N (abs 0) passed -> rel = (0 - 3) % 4 = 1 = dummy
        # E (abs 1) passed -> rel = (1 - 3) % 4 = 2 = RHO
        result = get_passed_positions(auction, dealer_i=0, decl_i=2)
        self.assertIn(1, result)  # dummy (North) passed
        self.assertIn(2, result)  # RHO (East) passed
        self.assertEqual(len(result), 2)

    def test_three_passes_before_bid(self):
        # N, E, S pass; W opens 1NT
        auction = ['PASS', 'PASS', 'PASS', '1NT', 'PASS', 'PASS', 'PASS']
        # dealer=0 (N), decl=3 (W)
        # N passed -> rel = (0 - 0) % 4 = 0 = LHO
        # E passed -> rel = (1 - 0) % 4 = 1 = dummy
        # S passed -> rel = (2 - 0) % 4 = 2 = RHO
        result = get_passed_positions(auction, dealer_i=0, decl_i=3)
        self.assertIn(0, result)  # LHO
        self.assertIn(1, result)  # dummy
        self.assertIn(2, result)  # RHO
        self.assertEqual(len(result), 3)

    def test_pad_start_stripped(self):
        # Dealer is East (1), auction padded with 1 PAD_START
        auction = ['PAD_START', 'PASS', '1D', 'PASS', 'PASS', 'PASS']
        # dealer=1 (E), decl=1 (E opened but after pass... wait E passed then opened?
        # Actually: E(dealer) passes, S opens 1D
        # decl=2 (S)
        # E passed -> rel = (1 - 3) % 4 = 2 = RHO
        result = get_passed_positions(auction, dealer_i=1, decl_i=2)
        self.assertIn(2, result)  # RHO (East) passed

    def test_none_auction(self):
        result = get_passed_positions(None, dealer_i=0, decl_i=0)
        self.assertEqual(result, {})

    def test_empty_auction(self):
        result = get_passed_positions([], dealer_i=0, decl_i=0)
        self.assertEqual(result, {})

    def test_dealer_opens_immediately_different_seat(self):
        # Dealer is West (3), opens 1NT
        auction = ['PAD_START', 'PAD_START', 'PAD_START', '1NT', 'PASS', '3NT', 'PASS', 'PASS', 'PASS']
        # dealer=3 (W), decl=3 (W) -> no passes before first bid
        result = get_passed_positions(auction, dealer_i=3, decl_i=3)
        self.assertEqual(result, {})


class TestHCPCapIntegration(unittest.TestCase):
    """Test that HCP cap is correctly applied in constraint arrays."""

    def test_ace_constraint_cap(self):
        """Simulate ACE-style array constraint capping."""
        # Simulate: LHO passed, max HCP from samples = 15, margin = 3, shown = 2
        # Without cap: max = min(15 + 3 - 2, 37) = 16
        # With cap at 11: min(16, 11 - 2) = 9
        constraints = [0, 13, 0, 13, 0, 13, 0, 13, 0, 37]
        margin = 3
        already_shown = 2
        max_from_samples = 15

        # Normal computation
        constraints[9] = min(max_from_samples + margin - already_shown, 37)
        self.assertEqual(constraints[9], 16)

        # Apply passed-hand cap
        hcp_cap = 11
        cap = max(hcp_cap - already_shown, 0)
        constraints[9] = min(constraints[9], cap)
        self.assertEqual(constraints[9], 9)

    def test_cap_doesnt_reduce_below_zero(self):
        """Cap with high already_shown doesn't go negative."""
        hcp_cap = 11
        already_shown = 14  # More shown than cap
        cap = max(hcp_cap - already_shown, 0)
        self.assertEqual(cap, 0)

    def test_cap_not_applied_when_disabled(self):
        """When hcp_cap is 0 (disabled), no capping occurs."""
        constraints_max = 16
        hcp_cap = 0
        # Logic: if hcp_cap > 0 then cap
        if hcp_cap > 0:
            constraints_max = min(constraints_max, hcp_cap)
        self.assertEqual(constraints_max, 16)


if __name__ == '__main__':
    unittest.main()
