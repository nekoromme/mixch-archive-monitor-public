"""通知設定を切り替えても、確認した活動日で20日解除を判定する。"""
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archive_metadata as metadata
import mixcha_watcher as watcher


NOW = datetime.datetime(2026, 10, 17, 12, 7, tzinfo=datetime.timezone.utc)
TODAY = NOW.astimezone(metadata.JST).date()
OLD_CREATED = int((NOW - datetime.timedelta(days=40)).timestamp())
OLD_DATE = metadata.ArchiveSnapshot("10", OLD_CREATED, 3600, 1).archive_date


class ArchiveInactivityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.watchlist = self.root / "watchlist.json"
        self.state = self.root / "state.json"
        self.activity = self.root / "activity_state.json"
        for name, path in (("WATCHLIST_FILE", self.watchlist), ("STATE_FILE", self.state),
                           ("ACTIVITY_STATE_FILE", self.activity)):
            manager = patch.object(watcher, name, str(path))
            manager.start()
            self.addCleanup(manager.stop)

    def reset_history(self, observed_date=None):
        self.watchlist.write_text('[{"id":"1","name":"streamer"}]')
        self.state.write_text('{"1":"60:00"}')
        history = {"archive_identity_version": 1, "latest_archive_id": "10",
                   "latest_archive_created": OLD_CREATED, "latest_archive_date": OLD_DATE,
                   "last_notified_date": OLD_DATE, "seen_archive_ids": ["10"]}
        if observed_date is not None:
            history["latest_observed_archive_date"] = observed_date
        self.activity.write_text(json.dumps({"1": history}))

    def run_monitor(self, current, public_only):
        # 実際の設定ファイルを読み、通知だけを模擬する。本番へは送信しない。
        (self.root / "monitor_settings.json").write_text(json.dumps({
            "enabled": True, "archive_public_only": public_only}))
        with (
            patch.object(watcher, "get_today_jst", return_value=TODAY.isoformat()),
            patch.object(watcher, "log_metric"),
            patch.object(watcher, "get_discord_webhook_url", return_value="test-only"),
            patch.object(watcher, "is_last_daily_inactive_notification_run", return_value=True),
            patch.object(watcher.ArchiveClient, "fetch_latest", return_value=current),
            patch.object(watcher, "send_embeds_to_discord", return_value=[]) as notify,
        ):
            watcher.main()
        return notify

    def test_recent_archive_keeps_target_for_every_visibility_and_notification_mode(self):
        for public_only in (True, False):
            for visibility in (0, 1, 2, 3):
                with self.subTest(public_only=public_only, visibility=visibility):
                    self.reset_history()
                    current = metadata.ArchiveSnapshot("20", int(NOW.timestamp()), 3600, visibility)
                    notify = self.run_monitor(current, public_only)
                    self.assertEqual(len(json.loads(self.watchlist.read_text())), 1)
                    self.assertEqual(notify.call_count, int(not public_only or visibility == 1))
                    if notify.called:
                        self.assertIn("更新通知", notify.call_args.args[0])

    def test_restricted_activity_survives_setting_switch_and_archive_removal(self):
        for public_only in (True, False):
            for current in (metadata.ArchiveSnapshot(),
                            metadata.ArchiveSnapshot("10", OLD_CREATED, 3600, 1)):
                with self.subTest(public_only=public_only, current=current.archive_id):
                    self.reset_history()
                    recent = metadata.ArchiveSnapshot("20", int(NOW.timestamp()), 3600, 3)
                    self.run_monitor(recent, public_only=True).assert_not_called()
                    # 新しい限定公開が消えたあと、空一覧／古い公開動画へ戻る。
                    # 「すべて通知」へ切り替えても、確認済みの活動日は失わない。
                    self.run_monitor(current, public_only).assert_not_called()
                    self.assertEqual(len(json.loads(self.watchlist.read_text())), 1)
                    activity = json.loads(self.activity.read_text())["1"]
                    self.assertEqual(activity["latest_observed_archive_date"], TODAY.isoformat())
                    self.assertEqual(activity["last_notified_date"], OLD_DATE)

    def test_observed_archive_age_keeps_19_days_and_removes_at_20_in_both_modes(self):
        for public_only in (True, False):
            for days in (19, 20):
                with self.subTest(public_only=public_only, days=days):
                    observed = (TODAY - datetime.timedelta(days=days)).isoformat()
                    self.reset_history(observed)
                    notify = self.run_monitor(metadata.ArchiveSnapshot(), public_only)
                    self.assertEqual(len(json.loads(self.watchlist.read_text())), int(days < 20))
                    self.assertEqual(notify.call_count, int(days >= 20))
                    if notify.called:
                        self.assertIn("20日以上アーカイブ更新なし", notify.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
