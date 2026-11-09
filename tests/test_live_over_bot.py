import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import LiveOverBot as bot


def odd(line, price=2.2, name="Powyżej"):
    return {"price": price, "name": name, "status": 1, "display": True,
            "metadata": {"name": name, "special_bet_value": str(line), "offer_state_id": 1}}


def event(market_name, line, price=2.2):
    return {"inplay_stats_metadata": {"status": "SECOND_HALF"},
            "markets": [{"name": market_name, "status": 1, "odds": [odd(line, price)]}]}


class LiveOverBotTests(unittest.TestCase):
    def test_live_status_recognized_across_nested_fields(self):
        self.assertTrue(bot.event_is_still_live({"fixture": {"status": "IN_PLAY"}}))
        self.assertTrue(bot.event_is_still_live({"inplay_stats_metadata": {"status": "SECOND_HALF"}}))

    def test_prematch_and_finished_are_rejected(self):
        self.assertFalse(bot.event_is_still_live({"inplay_stats_metadata": {"status": "NOT_STARTED"}}))
        self.assertFalse(bot.event_is_still_live({"fixture": {"status": "FINISHED"}}))

    def test_ht_selects_next_goal_line_up_to_over_15(self):
        self.assertEqual(bot.exact_live_over_markets(event("1. połowa - liczba goli", 0.5), "HT", 0)[0]["line"], 0.5)
        self.assertEqual(bot.exact_live_over_markets(event("1. połowa - liczba goli", 1.5), "HT", 1)[0]["line"], 1.5)

    def test_ht_does_not_exceed_max_line(self):
        self.assertEqual(bot.exact_live_over_markets(event("1. połowa - liczba goli", 2.5), "HT", 2), [])

    def test_ft_selects_next_goal_line_up_to_over_25(self):
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 2.5), "FT", 2)[0]["line"], 2.5)

    def test_ft_rejects_half_market_and_ht_rejects_full_match_market(self):
        self.assertEqual(bot.exact_live_over_markets(event("1. połowa - liczba goli", 0.5), "FT", 0), [])
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 0.5), "HT", 0), [])

    def test_only_exact_next_goal_line_and_odds_range_are_accepted(self):
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 0.5, 1.7), "FT", 0), [])
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 1.5, 2.2), "FT", 0), [])

    def test_phase_windows_and_line_caps(self):
        self.assertEqual((bot.HT_MAX_MINUTE, bot.FT_MIN_MINUTE, bot.FT_MAX_MINUTE), (35, 46, 75))
        self.assertEqual((bot.HT_MAX_LINE, bot.FT_MAX_LINE), (1.5, 2.5))


if __name__ == "__main__":
    unittest.main()
