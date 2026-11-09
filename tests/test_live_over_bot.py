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
        self.assertTrue(bot.event_is_still_live({"fixture": {"status": "STARTED"}}))
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

    def test_relaxed_odds_and_multiple_goal_lines(self):
        self.assertEqual((bot.MIN_ODDS, bot.MAX_ODDS), (1.60, 2.80))
        # Kurs 1.25 jest poniżej ustalonego minimum 1.60 i musi zostać odrzucony.
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 0.5, 1.25), "FT", 0), [])
        # Przy 0:0 bot nie może przeskoczyć na Over 1.5.
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 1.5, 2.2), "FT", 0), [])
        # Po jednym golu właściwą następną linią jest Over 1.5.
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 1.5, 2.2), "FT", 1)[0]["line"], 1.5)
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 0.5, 1.59), "FT", 0), [])
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 0.5, 2.79), "FT", 0)[0]["line"], 0.5)
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 3.5, 2.2), "FT", 0), [])

    def test_phase_windows_and_line_caps(self):
        self.assertEqual((bot.HT_MAX_MINUTE, bot.FT_MIN_MINUTE, bot.FT_MAX_MINUTE), (35, 46, 75))
        self.assertEqual((bot.HT_MAX_LINE, bot.FT_MAX_LINE), (1.5, 2.5))


    def test_superbet_live_stats_clock_and_score_fields(self):
        sample = {"inplay_stats": {"minutes": "27", "home_team_score": "1", "away_team_score": "0"}}
        self.assertEqual(bot.event_minute(sample), 27)
        self.assertEqual(bot.event_score(sample), (1, 0))

    def test_accepts_live_market_status_two(self):
        data = event("Liczba goli", 1.5)
        data["markets"][0]["status"] = 2
        data["markets"][0]["odds"][0]["status"] = 2
        data["markets"][0]["odds"][0]["metadata"]["offer_state_id"] = 2
        self.assertEqual(bot.exact_live_over_markets(data, "FT", 1)[0]["line"], 1.5)

    def test_polish_live_total_goals_market_name_is_detected(self):
        self.assertEqual(bot.market_phase("Liczba goli"), "FT")
        self.assertEqual(bot.market_phase("1. połowa - liczba goli"), "HT")


    def test_second_half_goal_market_is_not_full_time(self):
        for name in ("2. połowa - liczba goli", "Druga połowa - gole", "2nd Half Total Goals", "II połowa - liczba goli"):
            self.assertEqual(bot.market_phase(name), "2H", name)
            self.assertEqual(bot.exact_live_over_markets(event(name, 2.5), "FT", 2), [])

    def test_generic_full_match_goal_market_remains_ft(self):
        self.assertEqual(bot.market_phase("Liczba goli"), "FT")
        self.assertEqual(bot.market_phase("Total Goals"), "FT")


    def test_rejects_ambiguous_goal_market_names(self):
        for name in ("Home Team Total Goals", "Goals 15 min", "Team Goals"):
            self.assertIsNone(bot.market_phase(name), name)
        self.assertEqual(bot.market_phase("Goals in 2nd half"), "2H")

    def test_accepts_explicit_full_time_market_names(self):
        for name in ("Full Time Total Goals", "Cały mecz - liczba goli", "Total Goals"):
            self.assertEqual(bot.market_phase(name), "FT", name)


if __name__ == "__main__":
    unittest.main()
