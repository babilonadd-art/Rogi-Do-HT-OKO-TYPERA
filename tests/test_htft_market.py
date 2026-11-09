import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Main import htft_market_outcomes, daily_htft_alert_allowed


def market_odds():
    labels = ("1/1", "1/X", "1/2", "X/1", "X/X", "X/2", "2/1", "2/X", "2/2")
    return [
        {"price": 5.0 + i, "status": 1, "metadata": {"name": label, "offer_state_id": 1}}
        for i, label in enumerate(labels)
    ]


class HTFTMarketTests(unittest.TestCase):
    def test_polish_superbet_market_label_is_recognized(self):
        event = {"markets": [{"name": "1. połowa/mecz", "status": 1, "odds": market_odds()}]}
        found = htft_market_outcomes(event)
        self.assertEqual({x["selection"] for x in found}, {"1/2", "2/1"})
        self.assertTrue(all(x["fair_probability"] is not None for x in found))

    def test_first_half_1x2_is_not_mistaken_for_htft(self):
        event = {"markets": [{"name": "1.połowa - 1X2", "status": 1, "odds": [
            {"price": 2.1, "status": 1, "metadata": {"name": "1"}},
            {"price": 3.2, "status": 1, "metadata": {"name": "X"}},
            {"price": 3.4, "status": 1, "metadata": {"name": "2"}},
        ]}]}
        self.assertEqual(htft_market_outcomes(event), [])

    def test_superbet_combo_inside_markets_is_recognized(self):
        event = {"markets": [{"name": "SuperBets", "odds": [{
            "price": 18.0, "status": 1,
            "odd_components": [
                {"market_line_name": "1st Half - Result", "odd_name": "1"},
                {"market_line_name": "Full Time", "odd_name": "2"},
            ],
        }]}]}
        found = htft_market_outcomes(event)
        self.assertEqual([x["selection"] for x in found], ["1/2"])

    def test_only_two_distinct_matches_can_be_alerted_per_day(self):
        sent = {101, 202}
        self.assertFalse(daily_htft_alert_allowed(sent, 303))
        self.assertTrue(daily_htft_alert_allowed(sent, 101))

    def test_one_match_is_not_counted_twice(self):
        sent = {101}
        self.assertTrue(daily_htft_alert_allowed(sent, 101))
        self.assertTrue(daily_htft_alert_allowed(sent, 202))

    def test_same_side_results_are_excluded(self):
        event = {"superbets": [{"odds": [{
            "price": 4.0, "status": 1,
            "odd_components": [
                {"market_line_name": "1st Half - Result", "odd_name": "2"},
                {"market_line_name": "Full Time", "odd_name": "2"},
            ],
        }]}]}
        self.assertEqual(htft_market_outcomes(event), [])


if __name__ == "__main__":
    unittest.main()
