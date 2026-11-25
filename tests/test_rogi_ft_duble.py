import unittest
from unittest.mock import patch
import Main
import cornerflick_source
from RogiFTDuble import MIN_LEG_ODDS, MAX_LEG_ODDS, MAX_DOUBLES_PER_DAY, LEGS_PER_DOUBLE, FT_LINES, _placeholder_team, _recent_five_corner_profile, _candidate_ft_corner_stats, _is_top_six_league, is_blocked_under_league, is_blocked_youth_or_reserve_team, daily_quota_count, used_leagues_for_quota, fixture_bucket, night_session_day
from Main import superbet_event_is_prematch


class StrategyRulesTests(unittest.TestCase):
    def test_rules_are_ft_over_only_and_minimum_odds(self):
        self.assertEqual(MIN_LEG_ODDS, 1.65)
        self.assertEqual(MAX_LEG_ODDS, 2.60)
        self.assertEqual(LEGS_PER_DOUBLE, 2)
        self.assertEqual(MAX_DOUBLES_PER_DAY, 2)
        self.assertEqual(FT_LINES, (9.5, 8.5))

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

    @patch("RogiFTDuble.source.ft_matchday_candidate_profile")
    def test_team_history_is_required_and_used_for_candidate_score(self, profile):
        item = {
            "event_id": 321, "league": "Liga", "home": "Gospodarze",
            "away": "Goscie", "start_ts": 1795550000,
            "line": 9.5, "odds": 1.85,
        }
        profile.return_value = (142.5, "próba FT 10+10, Over 8.5 70%")
        result = _candidate_ft_corner_stats(item)
        self.assertEqual(result["event_id"], 321)
        self.assertEqual(result["quality_score"], 142.5)
        self.assertIn("Over 8.5", result["stats_detail"])
        profile.assert_called_once_with("Liga", "Gospodarze", "Goscie", 1795550000)

    @patch("RogiFTDuble.source.ft_matchday_candidate_profile")
    def test_candidate_without_reliable_corner_history_is_rejected(self, profile):
        item = {
            "event_id": 322, "league": "Liga", "home": "Gospodarze",
            "away": "Goscie", "start_ts": 1795550000,
            "line": 9.5, "odds": 1.85,
        }
        profile.return_value = (None, "brak wystarczających danych")
        self.assertIsNone(_candidate_ft_corner_stats(item))

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

    def test_daily_cap_is_two_doubles(self):
        self.assertEqual(MAX_DOUBLES_PER_DAY * LEGS_PER_DOUBLE, 4)

    def test_future_fixtures_keep_their_own_day_or_night_quota(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Warsaw")
        tomorrow_day = int(datetime(2026, 11, 24, 18, 0, tzinfo=tz).timestamp())
        tomorrow_night = int(datetime(2026, 11, 25, 2, 0, tzinfo=tz).timestamp())
        self.assertEqual(fixture_bucket(tomorrow_day), ("day", "2026-11-24"))
        self.assertEqual(fixture_bucket(tomorrow_night), ("night", "2026-11-24"))

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

    def test_quota_reset_unlocks_old_leagues_but_keeps_pre_reset_history(self):
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE ft_doubles(coupon_id TEXT, sent_at TEXT, bucket TEXT, quota_day TEXT)")
        db.execute("CREATE TABLE ft_double_legs(coupon_id TEXT, league TEXT)")
        db.execute("CREATE TABLE ft_daily_quota_resets(day TEXT PRIMARY KEY, reset_at TEXT)")
        db.execute("INSERT INTO ft_doubles VALUES('old','2026-11-24T08:00:00+01:00','day','2026-11-24')")
        db.execute("INSERT INTO ft_double_legs VALUES('old','Premier League')")
        db.execute("INSERT INTO ft_daily_quota_resets VALUES('2026-11-24','2026-11-24T09:00:00+01:00')")
        self.assertEqual(used_leagues_for_quota(db,'day','2026-11-24','2026-11-24'), set())
        db.execute("INSERT INTO ft_doubles VALUES('new','2026-11-24T09:05:00+01:00','day','2026-11-24')")
        db.execute("INSERT INTO ft_double_legs VALUES('new','La Liga')")
        self.assertEqual(used_leagues_for_quota(db,'day','2026-11-24','2026-11-24'), {'laliga'})
        self.assertEqual(db.execute("SELECT COUNT(*) FROM ft_doubles").fetchone()[0], 2)
        db.close()

    def test_graphic_uses_the_exact_user_supplied_bull_asset(self):
        from PIL import Image
        from rogift_graphic import _bull_artwork, render_coupon_image
        bull = _bull_artwork()
        self.assertGreater(bull.width, 100)
        self.assertGreater(bull.height, 60)
        legs = [
            {"home":"Arsenal","away":"Borussia Dortmund","league":"Liga Mistrzów",
             "start_ts":1795550000,"line":9.5,"odds":1.72},
            {"home":"Como","away":"AEK Ateny","league":"Liga Konferencji",
             "start_ts":1795553600,"line":9.5,"odds":1.70},
        ]
        output = render_coupon_image(legs, 1.72*1.70, 50)
        with Image.open(output) as image:
            self.assertEqual(image.size, (1080, 1420))

    def test_quota_reset_preserves_old_history_and_unlocks_new_doubles(self):
        import sqlite3
        from datetime import datetime
        from zoneinfo import ZoneInfo

        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE ft_doubles(coupon_id TEXT, day TEXT, sent_at TEXT, bucket TEXT, quota_day TEXT)')
        db.execute('CREATE TABLE ft_double_legs(event_id INTEGER, coupon_id TEXT)')
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

    @patch("team_league_resolver._team_record")
    def test_team_league_lookup_requires_both_clubs_to_agree(self, lookup):
        lookup.side_effect = [
            {"country": "Brazil", "league": "Serie A"},
            {"country": "Brazil", "league": "Serie A"},
        ]
        from team_league_resolver import resolve_match_league
        self.assertEqual(resolve_match_league("Coritiba", "Santos"), "Brazil - Serie A")

    @patch("team_league_resolver._team_record")
    def test_team_league_lookup_rejects_disagreement(self, lookup):
        lookup.side_effect = [
            {"country": "Brazil", "league": "Serie A"},
            {"country": "Brazil", "league": "Serie B"},
        ]
        from team_league_resolver import resolve_match_league
        self.assertIsNone(resolve_match_league("Coritiba", "Santos"))

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


    @patch("Main.superbet_api")
    def test_ft_corner_market_accepts_line_in_selection_and_missing_optional_status(self, api):
        api.return_value = {"events": [{
            "id": 456,
            "fixture": {"event_name": "Team One·Team Two", "offer_state_status": {"1": 1}},
            "inplay_stats_metadata": {"status": "NOT_STARTED"},
            "markets": [{
                "name": "Liczba rzutów rożnych",
                "status": 1,
                "odds": [{
                    "name": "Powyżej 9.5",
                    "price": 1.82,
                    "display": True,
                    "metadata": {"offer_state_id": 1},
                }],
            }],
        }]}
        result = Main.superbet_ft_corners_over_prices(456, lines=(9.5,), min_odds=1.60)
        self.assertEqual(result, {9.5: 1.82})


class CornerflickSourceTests(unittest.TestCase):
    def test_extracts_only_real_finished_full_time_corner_counts(self):
        payload = {
            "meta": {"url": "https://cornerflick.com/example"},
            "match": {"status": "finished", "kickoff_at": "2026-11-22T16:30:00+00:00"},
            "teams": {"home": {"name": "Liverpool"}, "away": {"name": "Manchester United"}},
            "statistics": [
                {"label": "Shots", "home": 28, "away": 9},
                {"label": "Corners", "home": 15, "away": 5},
            ],
        }
        row = cornerflick_source._extract_corner_row(payload)
        self.assertEqual(row["date"], "2026-11-22")
        self.assertEqual(row["home"], "Liverpool")
        self.assertEqual(row["away"], "Manchester United")
        self.assertEqual(row["home_corners"], 15)
        self.assertEqual(row["away_corners"], 5)

    def test_missing_corner_stat_is_not_treated_as_zero(self):
        payload = {
            "match": {"status": "finished", "kickoff_at": "2026-11-22T16:30:00+00:00"},
            "teams": {"home": {"name": "A"}, "away": {"name": "B"}},
            "statistics": [{"label": "Shots", "home": 4, "away": 3}],
        }
        self.assertIsNone(cornerflick_source._extract_corner_row(payload))

    def test_unfinished_match_is_rejected(self):
        payload = {
            "match": {"status": "live", "kickoff_at": "2026-11-22T16:30:00+00:00"},
            "teams": {"home": {"name": "A"}, "away": {"name": "B"}},
            "statistics": [{"label": "Corners", "home": 4, "away": 3}],
        }
        self.assertIsNone(cornerflick_source._extract_corner_row(payload))

    def test_competition_match_ignores_country_suffix(self):
        self.assertGreaterEqual(
            cornerflick_source._competition_score("Anglia - Premier League", "Premier League - England"),
            0.95,
        )

    def test_competition_match_rejects_wrong_country_and_similar_division(self):
        self.assertEqual(
            cornerflick_source._competition_score("Walia - Premier League", "Premier League - England"),
            0.0,
        )
        self.assertLess(
            cornerflick_source._competition_score("Japonia - J2 League", "J1 League - Japan"),
            0.85,
        )

if __name__ == "__main__":
    unittest.main()
