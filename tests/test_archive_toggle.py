"""停止した対象が取得・通知・自動削除から除外され、再開できることを確認。"""
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import mixcha_watcher as watcher


class ArchiveToggleTests(unittest.TestCase):
    def test_pause_resume_and_inactive_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            watchlist = root / "watchlist.json"
            state = root / "state.json"
            activity = root / "activity_state.json"
            original = [
                {"id": "1", "name": "paused", "archive_enabled": False},
                {"id": "2", "name": "legacy"},
            ]
            watchlist.write_text(json.dumps(original))
            state.write_text(json.dumps({"1": "12:34"}))
            activity.write_text(json.dumps({
                "1": {"last_notified_date": "2020-01-01"},
                "2": {"last_notified_date": "2020-01-01"},
            }))
            driver = Mock()
            with (
                patch.object(watcher, "WATCHLIST_FILE", str(watchlist)),
                patch.object(watcher, "STATE_FILE", str(state)),
                patch.object(watcher, "ACTIVITY_STATE_FILE", str(activity)),
                patch.object(watcher, "get_discord_webhook_url", return_value="unused"),
                patch.object(watcher, "log_metric"),
                patch.object(watcher, "create_driver", return_value=driver) as create,
                patch.object(watcher, "get_latest_marker", return_value=("NO_VIDEO", None)) as fetch,
                patch.object(watcher, "send_embeds_to_discord", return_value=[]) as notify,
                patch.object(watcher, "is_last_daily_inactive_notification_run", return_value=True),
            ):
                watcher.main()
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(fetch.call_args.args[1], "2")
                # 未更新のオン対象だけ削除され、オフ対象の設定と履歴が残る。
                self.assertEqual(json.loads(watchlist.read_text()), [original[0]])
                self.assertEqual(json.loads(state.read_text())["1"], "12:34")
                self.assertNotIn("paused", str(notify.call_args_list))

                create.reset_mock()
                fetch.reset_mock()
                notify.reset_mock()
                watcher.main()
                create.assert_not_called()
                fetch.assert_not_called()
                notify.assert_not_called()

                # オンに戻すと既存の履歴との比較で監視を再開する。
                original[0]["archive_enabled"] = True
                watchlist.write_text(json.dumps([original[0]]))
                fetch.return_value = ("12:35", datetime.date.today().isoformat())
                watcher.main()
                self.assertEqual(fetch.call_args.args[1], "1")
                self.assertEqual(json.loads(state.read_text())["1"], "12:35")
                self.assertTrue(notify.called)


if __name__ == "__main__":
    unittest.main()
