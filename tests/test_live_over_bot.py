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

    def test_ht_accepts_over_05_and_15_with_minimum_odds_160(self):
        self.assertEqual(bot.exact_live_over_markets(event("1. połowa - liczba goli", 0.5, 1.60), "HT", 0)[0]["line"], 0.5)
        self.assertEqual(bot.exact_live_over_markets(event("1. połowa - liczba goli", 1.5, 1.60), "HT", 0)[0]["line"], 1.5)
        self.assertEqual(bot.exact_live_over_markets(event("1. połowa - liczba goli", 1.5, 1.59), "HT", 0), [])

    def test_ft_accepts_over_05_and_15_with_minimum_odds_170(self):
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 0.5, 1.70), "FT", 0)[0]["line"], 0.5)
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 1.5, 1.70), "FT", 0)[0]["line"], 1.5)
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 1.5, 1.69), "FT", 0), [])

    def test_ft_rejects_half_market_and_ht_rejects_full_match_market(self):
        self.assertEqual(bot.exact_live_over_markets(event("1. połowa - liczba goli", 0.5), "FT", 0), [])
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 0.5), "HT", 0), [])

    def test_over_lines_only_available_until_settlement(self):
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 0.5, 2.20), "FT", 1), [])
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 1.5, 2.20), "FT", 1)[0]["line"], 1.5)
        self.assertEqual(bot.exact_live_over_markets(event("Total Goals", 1.5, 2.20), "FT", 2), [])
        self.assertEqual((bot.HT_MIN_ODDS, bot.FT_MIN_ODDS), (1.60, 1.70))

    def test_superbet_url_finds_nested_direct_event_url_only(self):
        direct = "https://superbet.pl/zaklady-bukmacherskie/pilka-nozna/testowy-mecz"
        self.assertEqual(bot.superbet_url({"fixture": {"links": {"event_url": direct}}}), direct)
        self.assertIsNone(bot.superbet_url({"event_id": "123", "fixture": {"event_name": "A · B"}}))

    def test_sportscore_stats_are_parsed_and_summed(self):
        payload = {"stats": [
            {"name": "Total Shots", "home": "7", "away": "4"},
            {"name": "Shots on Target", "home": "3", "away": "1"},
            {"name": "Dangerous Attacks", "home": "14", "away": "12"},
            {"name": "Corner Kicks", "home": "2", "away": "1"},
        ]}
        self.assertEqual(bot.extract_sportscore_stats(payload), {
            "shots": 11, "shots_on_target": 4, "dangerous_attacks": 26, "corners": 3
        })

    def test_live_over_requires_real_stats_and_sufficient_tempo(self):
        strong = {"shots": 10, "shots_on_target": 3, "dangerous_attacks": 25, "corners": 2}
        weak = {"shots": 10, "shots_on_target": 1, "dangerous_attacks": 25, "corners": 2}
        self.assertTrue(bot.stats_confirm_over(strong, 30, 0.5))
        self.assertFalse(bot.stats_confirm_over(weak, 30, 0.5))
        self.assertFalse(bot.stats_confirm_over(None, 30, 0.5))
        not_strong_enough_for_15 = {"shots": 7, "shots_on_target": 2, "dangerous_attacks": 25, "corners": 2}
        self.assertFalse(bot.stats_confirm_over(not_strong_enough_for_15, 30, 1.5))

    def test_sportscore_slug_is_extracted_from_live_match_url(self):
        fixture = {"url": "/football/match/finland-u17-vs-scotland-u17/3glrw7hne0o1qdy/"}
        self.assertEqual(bot.sportscore_fixture_slug(fixture), "finland-u17-vs-scotland-u17")

    def test_sportscore_team_match_is_normalized(self):
        fixtures = [{"home": {"name": "Manchester United FC"},
                     "away": {"name": "Chelsea"}}]
        self.assertIsNotNone(bot.match_sportscore_fixture("Manchester United", "Chelsea FC", fixtures))
        self.assertIsNone(bot.match_sportscore_fixture("Liverpool", "Arsenal", fixtures))

    def test_phase_windows_and_daily_cap(self):
        self.assertEqual((bot.HT_MIN_MINUTE, bot.HT_MAX_MINUTE), (1, 35))
        self.assertEqual((bot.FT_MIN_MINUTE, bot.FT_MAX_MINUTE), (1, 75))
        self.assertEqual(bot.MAX_PICKS_PER_DAY, 10)


    def test_superbet_live_stats_clock_and_score_fields(self):
        sample = {"inplay_stats": {"minutes": "27", "home_team_score": "1", "away_team_score": "0"}}
        self.assertEqual(bot.event_minute(sample), 27)
        self.assertEqual(bot.event_score(sample), (1, 0))

    def test_accepts_live_market_status_two(self):
        data = event("Liczba goli", 0.5)
        data["markets"][0]["status"] = 2
        data["markets"][0]["odds"][0]["status"] = 2
        data["markets"][0]["odds"][0]["metadata"]["offer_state_id"] = 2
        self.assertEqual(bot.exact_live_over_markets(data, "FT", 0)[0]["line"], 0.5)

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
