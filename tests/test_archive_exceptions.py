"""月末例外の期限、設定切替、二重通知、停止・自動解除の回帰確認。"""
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archive_metadata as metadata
import mixcha_watcher as watcher

UTC = datetime.timezone.utc
BEFORE = datetime.datetime(2026, 10, 31, 14, 59, 59, tzinfo=UTC)
AFTER = datetime.datetime(2026, 10, 31, 15, 0, 0, tzinfo=UTC)


class MonthEndExceptionsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.files = {}
        for key, filename in (("WATCHLIST_FILE", "watchlist.json"), ("STATE_FILE", "state.json"),
                              ("ACTIVITY_STATE_FILE", "activity_state.json")):
            path = self.root / filename
            self.files[key] = path
            p = patch.object(watcher, key, str(path))
            p.start()
            self.addCleanup(p.stop)
        self.reset()

    def reset(self, old=False):
        self.files["WATCHLIST_FILE"].write_text(json.dumps([
            {"id": "1", "name": "exception", "archive_all_until": "2026-10-31", "extra": "keep"},
            {"id": "2", "name": "ordinary"}]))
        self.files["STATE_FILE"].write_text('{}')
        self.files["ACTIVITY_STATE_FILE"].write_text('{}')
        if old:
            created = int((BEFORE - datetime.timedelta(days=40)).timestamp())
            history = {"archive_identity_version": 1, "latest_archive_id": "10",
                       "latest_archive_created": created, "latest_archive_date": "2026-09-21",
                       "last_notified_date": "2026-09-21", "seen_archive_ids": ["10"]}
            self.files["STATE_FILE"].write_text('{"1":"60:00","2":"60:00"}')
            self.files["ACTIVITY_STATE_FILE"].write_text(json.dumps({"1": history, "2": history}))

    def run_monitor(self, now=BEFORE, public_only=True, enabled=True, visibility=3, empty=False,
                    fail_delivery=False, clock=None):
        (self.root / 'monitor_settings.json').write_text(json.dumps({
            'enabled': enabled, 'archive_public_only': public_only}))
        current = metadata.ArchiveSnapshot() if empty else metadata.ArchiveSnapshot(
            '20', int(BEFORE.timestamp()) - 3600, 3600, visibility)
        with (
            patch.object(watcher, 'current_time_utc', side_effect=clock or (lambda: now)),
            patch.object(watcher, 'get_today_jst', return_value=now.astimezone(metadata.JST).date().isoformat()),
            patch.object(watcher, 'log_metric'),
            patch.object(watcher, 'get_discord_webhook_url', return_value='test-only') as secret,
            patch.object(watcher, 'is_last_daily_inactive_notification_run', return_value=True),
            patch.object(watcher.ArchiveClient, 'fetch_latest', return_value=current) as fetch,
            patch.object(watcher, 'send_embeds_to_discord', return_value=[],
                         side_effect=watcher.DiscordDeliveryError('mock') if fail_delivery else None) as notify,
        ):
            if fail_delivery:
                with self.assertRaises(watcher.DiscordDeliveryError):
                    watcher.main()
            else:
                watcher.main()
        return notify, fetch, secret

    def test_jst_month_end_boundary_and_bad_date(self):
        user = {'archive_all_until': '2026-10-31'}
        self.assertTrue(watcher.archive_exception_active(user, BEFORE))
        self.assertFalse(watcher.archive_exception_active(user, AFTER))
        for value in ('2026-10-30', '2026-02-31', '', None, 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                watcher.archive_exception_active({'archive_all_until': value}, BEFORE)

    def test_exception_all_visibilities_global_modes_and_deduplication(self):
        for public_only in (True, False):
            for visibility in (0, 1, 2, 3):
                with self.subTest(public_only=public_only, visibility=visibility):
                    self.reset()
                    notify, _, _ = self.run_monitor(public_only=public_only, visibility=visibility)
                    self.assertEqual(notify.call_count, 1)
                    self.assertIn('exception', str(notify.call_args))
                    self.assertEqual('ordinary' in str(notify.call_args), not public_only or visibility == 1)
                    self.run_monitor(public_only=public_only, visibility=visibility)[0].assert_not_called()

    def test_expiry_restores_global_mode_preserves_registration_and_history(self):
        for public_only in (True, False):
            self.reset()
            notify, _, _ = self.run_monitor(now=AFTER, public_only=public_only)
            self.assertEqual(notify.call_count, int(not public_only))
            rows = json.loads(self.files['WATCHLIST_FILE'].read_text())
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0], {'id': '1', 'name': 'exception', 'extra': 'keep'})

    def test_pause_only_cleans_expired_exception_no_external_calls_or_history_changes(self):
        self.run_monitor(enabled=False)[0].assert_not_called()
        self.assertIn('archive_all_until', self.files['WATCHLIST_FILE'].read_text())
        notify, fetch, secret = self.run_monitor(now=AFTER, enabled=False)
        notify.assert_not_called()
        fetch.assert_not_called()
        secret.assert_not_called()
        self.assertNotIn('archive_all_until', self.files['WATCHLIST_FILE'].read_text())
        self.assertEqual(self.files['STATE_FILE'].read_text(), '{}')

    def test_inactive_exception_stays_until_expiry_then_usual_rule_resumes(self):
        self.reset(old=True)
        self.run_monitor(empty=True)
        rows = json.loads(self.files['WATCHLIST_FILE'].read_text())
        self.assertEqual([row['id'] for row in rows], ['1'])
        self.run_monitor(now=AFTER, empty=True)
        self.assertEqual(json.loads(self.files['WATCHLIST_FILE'].read_text()), [])

    def test_notification_failure_retries_and_return_to_public_never_duplicates(self):
        self.run_monitor(fail_delivery=True)
        self.assertNotIn('20', self.files['STATE_FILE'].read_text())
        self.assertEqual(self.run_monitor()[0].call_count, 1)
        # 同じ動画が翌月に全体公開へ変更されても既通知なら再送しない。
        notify, _, _ = self.run_monitor(now=AFTER, visibility=1)
        self.assertNotIn('exception', str(notify.call_args))

    def test_month_rollover_between_fetch_and_send_suppresses_restricted_notification(self):
        clock = iter([BEFORE, BEFORE, BEFORE, AFTER, AFTER, AFTER, AFTER])
        self.run_monitor(clock=lambda: next(clock, AFTER))[0].assert_not_called()
        self.assertNotIn('20', self.files['STATE_FILE'].read_text())
        self.assertEqual(len(json.loads(self.files['WATCHLIST_FILE'].read_text())), 2)


if __name__ == '__main__':
    unittest.main()
