import unittest

from replay_notifications import collect_changed_reports


class ReplayNotificationTests(unittest.TestCase):
    def test_collects_only_changed_users_in_watchlist_order(self):
        base_state = {"100": "1:00", "200": "2:00", "300": "NO_VIDEO"}
        head_state = {"100": "1:01", "200": "2:00", "300": "3:00"}
        watchlist = [
            {"id": "300", "name": "三番"},
            {"id": "100", "name": "一番"},
            {"id": "200", "name": "二番"},
        ]

        reports = collect_changed_reports(base_state, head_state, watchlist)

        self.assertEqual(["300", "100"], [report["id"] for report in reports])
        self.assertEqual(
            "https://mixch.tv/u/300/live_archives",
            reports[0]["url"],
        )

    def test_missing_watchlist_entry_is_fatal(self):
        with self.assertRaises(ValueError):
            collect_changed_reports(
                {"100": "1:00"},
                {"100": "1:01"},
                [],
            )


if __name__ == "__main__":
    unittest.main()
