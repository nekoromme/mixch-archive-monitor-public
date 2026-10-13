"""全体公開の新着判定、再公開、限定公開による誤削除を実送信せず確認。"""
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archive_metadata as metadata
import mixcha_watcher as watcher

NOW = datetime.datetime(2026, 10, 13, 13, 0, tzinfo=datetime.timezone.utc)
CREATED = int(NOW.timestamp()) - 3600


def snapshot(visibility=1, archive_id="20"):
    return metadata.ArchiveSnapshot(archive_id, CREATED, 14400, visibility)


class PublicOnlyTests(unittest.TestCase):
    def decide(self, current, activity=None, marker=None, public_only=True):
        return metadata.plan_archive_update(marker, activity or {}, current,
                                            checked_at=NOW, public_only=public_only)

    def test_only_public_is_eligible_and_unknown_is_never_guessed_public(self):
        for visibility in (0, 1, 2, 3, None):
            with self.subTest(visibility=visibility):
                decision = self.decide(snapshot(visibility))
                self.assertEqual(decision.changed, visibility == 1)
                self.assertEqual(decision.activity.get("last_notified_archive_id"),
                                 "20" if visibility == 1 else None)

    def test_filtered_then_public_notifies_once_and_keeps_existing_history(self):
        history = {"archive_identity_version": 1, "latest_archive_id": "10",
                   "latest_archive_created": CREATED - 86400,
                   "seen_archive_ids": ["10"], "last_notified_date": "2026-10-01"}
        skipped = self.decide(snapshot(3), history, "60:00")
        self.assertEqual(skipped.marker, "60:00")
        self.assertEqual(skipped.activity["latest_archive_id"], "10")
        self.assertEqual(skipped.activity["seen_archive_ids"], ["10"])
        self.assertEqual(skipped.activity["last_notified_date"], "2026-10-01")
        opened = self.decide(snapshot(1), skipped.activity, skipped.marker)
        self.assertTrue(opened.changed)
        self.assertFalse(self.decide(snapshot(1), opened.activity, opened.marker).changed)

    def test_first_public_after_initial_restricted_archive_is_not_lost_as_baseline(self):
        skipped = self.decide(snapshot(2))
        self.assertTrue(self.decide(snapshot(1), skipped.activity, skipped.marker).changed)
        self.assertTrue(self.decide(snapshot(2), skipped.activity, skipped.marker,
                                    public_only=False).changed)

    def test_already_notified_public_becoming_restricted_and_public_is_not_new(self):
        first = self.decide(snapshot())
        hidden = self.decide(snapshot(3), first.activity, first.marker)
        self.assertFalse(self.decide(snapshot(), hidden.activity, hidden.marker).changed)

    def test_disabled_filter_preserves_existing_notification_behavior(self):
        self.assertTrue(self.decide(snapshot(3), public_only=False).changed)
        # 旧形式の履歴では、一斉再通知を防ぐ初回移行の扱いも保ちます。
        skipped = self.decide(snapshot(3), {"last_notified_date": "2026-10-01"}, "60:00")
        self.assertFalse(self.decide(snapshot(), skipped.activity, skipped.marker).changed)

    def test_full_monitor_skips_private_and_does_not_delete_recently_active_streamers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {"WATCHLIST_FILE": root / "watchlist.json",
                     "STATE_FILE": root / "state.json",
                     "ACTIVITY_STATE_FILE": root / "activity_state.json"}
            files["WATCHLIST_FILE"].write_text('[{"id":"1","name":"private"},{"id":"2","name":"public"}]')
            files["STATE_FILE"].write_text('{"1":"60:00","2":"60:00"}')
            old = {"archive_identity_version": 1, "latest_archive_id": "10",
                   "latest_archive_created": CREATED - 40 * 86400,
                   "latest_archive_date": "2026-09-01", "last_notified_date": "2026-09-01",
                   "seen_archive_ids": ["10"]}
            files["ACTIVITY_STATE_FILE"].write_text(json.dumps({"1": old, "2": old}))
            settings = root / "monitor_settings.json"
            settings.write_text('{"enabled":true,"archive_public_only":true}')
            with (
                patch.object(watcher, "WATCHLIST_FILE", str(files["WATCHLIST_FILE"])),
                patch.object(watcher, "STATE_FILE", str(files["STATE_FILE"])),
                patch.object(watcher, "ACTIVITY_STATE_FILE", str(files["ACTIVITY_STATE_FILE"])),
                patch.object(watcher, "get_today_jst", return_value="2026-10-13"),
                patch.object(watcher, "log_metric"),
                patch.object(watcher, "get_discord_webhook_url", return_value="test-only"),
                patch.object(watcher, "is_last_daily_inactive_notification_run", return_value=True),
                patch.object(watcher.ArchiveClient, "fetch_latest", side_effect=[snapshot(3), snapshot(1)]),
                patch.object(watcher, "send_embeds_to_discord", return_value=[]) as send,
            ):
                watcher.main()
            send.assert_called_once()
            self.assertIn("public", str(send.call_args))
            self.assertNotIn("private", str(send.call_args))
            self.assertEqual(len(json.loads(files["WATCHLIST_FILE"].read_text())), 2)
            activity = json.loads(files["ACTIVITY_STATE_FILE"].read_text())
            self.assertEqual(activity["1"]["latest_archive_id"], "10")
            self.assertEqual(activity["1"]["archive_read_status"], "filtered")
            self.assertEqual(activity["2"]["last_notified_archive_id"], "20")
            settings.write_text('{"enabled":true,"archive_public_only":"false"}')
            with patch.object(watcher, "WATCHLIST_FILE", str(files["WATCHLIST_FILE"])):
                with self.assertRaises(ValueError):
                    watcher.load_monitoring_settings()
