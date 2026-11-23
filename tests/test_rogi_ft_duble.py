import unittest
from unittest.mock import patch
import Main
from RogiFTDuble import MIN_LEG_ODDS, MAX_LEG_ODDS, MAX_DOUBLES_PER_DAY, LEGS_PER_DOUBLE, FT_LINES, _placeholder_team, _recent_five_corner_profile, _is_top_six_league, is_blocked_under_league, is_blocked_youth_or_reserve_team, daily_quota_count, fixture_bucket, night_session_day
from Main import superbet_event_is_prematch


class StrategyRulesTests(unittest.TestCase):
    def test_rules_are_ft_over_only_and_minimum_odds(self):
        self.assertEqual(MIN_LEG_ODDS, 1.60)
        self.assertEqual(MAX_LEG_ODDS, 2.60)
        self.assertEqual(LEGS_PER_DOUBLE, 2)
        self.assertEqual(MAX_DOUBLES_PER_DAY, 4)
        self.assertEqual(FT_LINES, (9.5,))

    def test_recent_five_accepts_two_or_more_real_corner_records(self):
        complete = [
            ("2026-11-20", 6, 10),
            ("2026-11-18", 5, 9),
            ("2026-11-16", 7, 12),
            ("2026-11-14", 4, 11),
            ("2026-11-12", 5, 8),
        ]
        profile = _recent_five_corner_profile(complete)
        self.assertEqual(profile["sample"], 5)
        self.assertEqual(profile["over85_hits"], 4)
        self.assertAlmostEqual(profile["own_average"], 5.4)
        self.assertIsNotNone(_recent_five_corner_profile(complete[:3]))
        self.assertIsNone(_recent_five_corner_profile(complete[:1]))

    def test_top_six_league_detection(self):
        self.assertTrue(_is_top_six_league("Premier League"))
        self.assertTrue(_is_top_six_league("LaLiga"))
        self.assertFalse(_is_top_six_league("Bolivian Primera Division"))

    def test_youth_and_reserve_leagues_and_teams_are_rejected(self):
        for league in ("UEFA - Liga Młodzieżowa", "Premier League U21", "National Reserve League"):
            self.assertTrue(is_blocked_under_league(league), league)
        for team in ("Galatasaray U19", "Tottenham U21", "Arsenal Under 18",
                     "Bayern II", "Manchester City B", "Legia Rezerwy"):
            self.assertTrue(is_blocked_youth_or_reserve_team(team), team)
        for team in ("Manchester United", "Bologna", "Kuwejt"):
            self.assertFalse(is_blocked_youth_or_reserve_team(team), team)

    def test_placeholder_team_names_are_rejected(self):
        for name in ("A", "B", "Team A", "Team B", "Drużyna A", "Drużyna B", "Unknown", "TBD"):
            self.assertTrue(_placeholder_team(name))
        self.assertFalse(_placeholder_team("Kuwejt"))
        self.assertFalse(_placeholder_team("Honduras"))

    def test_daily_cap_is_four_doubles(self):
        self.assertEqual(MAX_DOUBLES_PER_DAY * LEGS_PER_DOUBLE, 8)

    def test_night_and_day_pools_are_separate(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('Europe/Warsaw')
        night_kickoff = int(datetime(2026, 11, 21, 2, 30, tzinfo=tz).timestamp())
        day_kickoff = int(datetime(2026, 11, 21, 14, 0, tzinfo=tz).timestamp())
        self.assertEqual(fixture_bucket(night_kickoff), ('night', '2026-11-20'))
        self.assertEqual(fixture_bucket(day_kickoff), ('day', '2026-11-21'))
        self.assertEqual(night_session_day(datetime(2026, 11, 21, 3, 0, tzinfo=tz)), '2026-11-20')
        self.assertEqual(night_session_day(datetime(2026, 11, 21, 10, 0, tzinfo=tz)), '2026-11-21')

    def test_quota_reset_preserves_old_history_and_unlocks_new_doubles(self):
        import sqlite3
        from datetime import datetime
        from zoneinfo import ZoneInfo

        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE ft_doubles(coupon_id TEXT, day TEXT, sent_at TEXT, bucket TEXT, quota_day TEXT)')
        db.execute('CREATE TABLE ft_daily_quota_resets(day TEXT PRIMARY KEY, reset_at TEXT)')
        day = '2026-11-17'
        db.executemany(
            'INSERT INTO ft_doubles VALUES(?,?,?,?,?)',
            [('old-1', day, '2026-11-17T12:00:00+01:00', 'day', day),
             ('old-2', day, '2026-11-17T13:00:00+01:00', 'day', day)],
        )
        now = datetime(2026, 11, 17, 20, 0, tzinfo=ZoneInfo('Europe/Warsaw'))
        db.execute('INSERT INTO ft_daily_quota_resets VALUES(?,?)', (day, now.isoformat()))
        self.assertEqual(daily_quota_count(db, day), 0)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM ft_doubles').fetchone()[0], 2)
        db.execute('INSERT INTO ft_doubles VALUES(?,?,?,?,?)',
                   ('new-1', day, '2026-11-17T20:01:00+01:00', 'day', day))
        self.assertEqual(daily_quota_count(db, day), 1)
        db.close()
    def test_superbet_football_sport_id(self):
        self.assertTrue(superbet_event_is_prematch({
            "fixture": {"sport_id": 5, "offer_state_status": {"1": 1}},
            "inplay_stats_metadata": {"status": "NOT_STARTED"},
        }))
        self.assertFalse(superbet_event_is_prematch({
            "fixture": {"sport_id": 4, "offer_state_status": {"1": 1}},
            "inplay_stats_metadata": {"status": "NOT_STARTED"},
        }))

    def test_sofascore_extracts_full_time_corner_counts(self):
        payload = {"statistics": {"periods": [{
            "period": "ALL",
            "groups": [{"statistics": [
                {"name": "Corner kicks", "home": "7", "away": "5"},
                {"name": "Shots", "home": "10", "away": "8"},
            ]}],
        }]}}
        self.assertEqual(Main.sofascore_extract_corner_counts(payload), (7, 5))

    def test_sofascore_does_not_use_half_time_corner_counts(self):
        payload = {"statistics": {"periods": [{
            "period": "1ST",
            "groups": [{"statistics": [
                {"name": "Corner kicks", "home": "4", "away": "2"},
            ]}],
        }]}}
        self.assertIsNone(Main.sofascore_extract_corner_counts(payload))

    @patch("Main.superbet_api")
    def test_all_ft_over_lines_are_read_with_one_detail_request(self, api):
        api.return_value = {"events": [{
            "id": 123,
            "fixture": {"event_name": "Coritiba·Santos", "offer_state_status": {"1": 1}},
            "inplay_stats_metadata": {"status": "NOT_STARTED"},
            "markets": [{
                "name": "Liczba rzutów rożnych",
                "status": 1,
                "odds": [
                    {"name": "Powyżej 8.5", "price": 1.55, "status": 1,
                     "display": True, "metadata": {"special_bet_value": "8.5", "offer_state_id": 1}},
                    {"name": "Powyżej 9.5", "price": 1.90, "status": 1,
                     "display": True, "metadata": {"special_bet_value": "9.5", "offer_state_id": 1}},
                    {"name": "Powyżej 10.5", "price": 2.20, "status": 1,
                     "display": True, "metadata": {"special_bet_value": "10.5", "offer_state_id": 1}},
                ],
            }, {
                "name": "Coritiba - liczba rzutów rożnych",
                "status": 1,
                "odds": [{
                    "name": "Powyżej 12.5", "price": 2.80, "status": 1,
                    "display": True, "metadata": {"special_bet_value": "12.5", "offer_state_id": 1},
                }],
            }],
        }]}
        result = Main.superbet_ft_corners_over_prices(123, lines=FT_LINES, min_odds=1.90)
        self.assertEqual(result, {9.5: 1.90})
        api.assert_called_once_with("/v3/pl-PL/events", {"events": "123"})


if __name__ == "__main__":
    unittest.main()
