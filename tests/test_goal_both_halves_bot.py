import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import GoalBothHalvesBot as bot


def sample_event(market_name="Goal in Both Halves", label="Yes", price=1.22,
                 status="NOT_STARTED"):
    kickoff = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    return {
        "event_id": "abc123",
        "fixture": {
            "sport_id": "5",
            "event_name": "Home FC · Away FC",
            "status": status,
            "start_time": kickoff,
        },
        "markets": [{
            "name": market_name,
            "status": 1,
            "odds": [{
                "name": label, "price": price, "status": 1, "display": True,
                "metadata": {"name": label, "offer_state_id": 1},
            }],
        }],
    }


class GoalBothHalvesTests(unittest.TestCase):
    def test_accepts_exact_market_yes_without_minimum_odds(self):
        event = sample_event(price=1.08)
        result = bot.both_halves_yes_market(event)
        self.assertIsNotNone(result)
        self.assertEqual(result["odds"], 1.08)

    def test_accepts_polish_market_name_and_tak(self):
        result = bot.both_halves_yes_market(
            sample_event("Gol w obu połowach", "Tak", 1.35))
        self.assertEqual(result["odds"], 1.35)

    def test_rejects_no_selection(self):
        self.assertIsNone(bot.both_halves_yes_market(
            sample_event("Goal in Both Halves", "No", 2.8)))

    def test_rejects_unrelated_over_market(self):
        self.assertIsNone(bot.both_halves_yes_market(
            sample_event("Total Goals Over 2.5", "Yes", 1.9)))

    def test_rejects_live_and_finished_events(self):
        self.assertIsNone(bot.both_halves_yes_market(
            sample_event(status="SECOND_HALF")))
        self.assertIsNone(bot.both_halves_yes_market(
            sample_event(status="FINISHED")))

    def test_rejects_suspended_market(self):
        event = sample_event()
        event["markets"][0]["status"] = "SUSPENDED"
        self.assertIsNone(bot.both_halves_yes_market(event))

    def test_event_teams_are_parsed(self):
        self.assertEqual(bot.event_teams(sample_event()), ("Home FC", "Away FC"))


if __name__ == "__main__":
    unittest.main()
