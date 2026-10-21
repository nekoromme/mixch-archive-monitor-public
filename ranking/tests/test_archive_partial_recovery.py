"""実際の18766676の403を再現し、通常通知と再試行・重複防止を確認する。"""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from src import control
from src.github_actions_recovery import WatchdogConfig, run_watchdog, WorkflowRun
from src.mixch_monitor import (
    MonitorError, NotificationError, accumulate_night_candidates,
    find_public_archive_profiles, load_state, run,
)
from src.relay import ensure_relay
from test_monitor import NOW, config_for_state, new_state, stream
from test_github_actions_recovery import FakeClient


class PartialArchiveRecoveryTests(unittest.TestCase):
    def test_one_403_does_not_block_digest_or_daytime_and_retries_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            state = new_state()
            accumulate_night_candidates(state, [stream('111', 200), stream('18766676', 180)], NOW)
            path.write_text(json.dumps(state))
            config = replace(config_for_state(path), notify_on_error=True)
            calls = []
            failing = True

            def archive_check(user_id, _timeout):
                calls.append(user_id)
                if user_id == '18766676' and failing:
                    raise MonitorError('MixChannelアーカイブ一覧 (user_id=18766676, page=1)がHTTP 403を返しました')
                return True

            with (
                patch('src.mixch_monitor.fetch_ranking', return_value=[stream('333', 300)]),
                patch('src.mixch_monitor.has_public_archive', side_effect=archive_check),
                patch('src.mixch_monitor._post_discord') as post,
                patch('src.mixch_monitor.maybe_send_error_notification') as global_error,
            ):
                self.assertEqual(0, run(config, NOW))
                saved = load_state(path)
                self.assertEqual({'18766676'}, set(saved['night_candidates']))
                self.assertNotIn('18766676', saved['notifications'])
                self.assertIn('403', saved['night_candidates']['18766676']['archive_check_last_error'])
                self.assertEqual({'111', '333'}, set(saved['notifications']))
                self.assertEqual(2, post.call_count)  # 公開確認済みの夜間分と通常通知。

                self.assertEqual(0, run(config, NOW + timedelta(minutes=5)))
                self.assertEqual(2, len(calls))  # まだ再試行せず、送信済みも再送しない。
                self.assertEqual(2, post.call_count)

                failing = False
                self.assertEqual(0, run(config, NOW + timedelta(minutes=15)))
                self.assertEqual(1, calls.count('111'))
                self.assertEqual(2, calls.count('18766676'))
                self.assertEqual(3, post.call_count)
                self.assertIn('/18766676', post.call_args.args[1]['embeds'][0]['description'])
                self.assertEqual({}, load_state(path)['night_candidates'])
                global_error.assert_not_called()

    def test_all_failed_candidates_stay_pending_and_long_failure_warns_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            state = new_state()
            accumulate_night_candidates(state, [stream('18766676', 180)], NOW)
            path.write_text(json.dumps(state))
            config = replace(config_for_state(path), notify_on_error=True)
            with (
                patch('src.mixch_monitor.fetch_ranking', return_value=[]),
                patch('src.mixch_monitor.has_public_archive', side_effect=MonitorError('HTTP 403')),
                patch('src.mixch_monitor._post_discord') as post,
            ):
                for minutes in (0, 15, 30, 45):
                    self.assertEqual(0, run(config, NOW + timedelta(minutes=minutes)))
                post.assert_not_called()
                self.assertEqual(0, run(config, NOW + timedelta(minutes=60)))
                self.assertEqual(1, post.call_count)
                self.assertIn('通常のランキング監視は継続中', post.call_args.args[1]['content'])
                self.assertEqual(0, run(config, NOW + timedelta(minutes=75)))
                self.assertEqual(1, post.call_count)
                saved = load_state(path)
                self.assertIn('18766676', saved['night_candidates'])
                self.assertNotIn('last_error_notified_at', saved['metadata'])
                self.assertIn('last_archive_warning_at', saved['metadata'])

                # 6時間抑制の境界を越えた場合だけ、未解決を再通知する。
                self.assertEqual(0, run(config, NOW + timedelta(hours=7)))
                self.assertEqual(2, post.call_count)

    def test_dry_run_keeps_failed_candidate_and_file_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            state = new_state()
            accumulate_night_candidates(state, [stream('18766676', 180)], NOW)
            path.write_text(json.dumps(state))
            before = path.read_bytes()
            with (
                patch('src.mixch_monitor.fetch_ranking', return_value=[]),
                patch('src.mixch_monitor.has_public_archive', side_effect=MonitorError('HTTP 403')),
                patch('src.mixch_monitor._post_discord') as post,
            ):
                self.assertEqual(0, run(replace(config_for_state(path), dry_run=True), NOW))
                self.assertEqual(before, path.read_bytes())
                post.assert_not_called()

    def test_no_public_archive_is_processed_but_failure_is_not(self):
        failed = {}
        candidates = {user_id: {'max_momentum': 180, 'broadcaster_name': '名前'}
                      for user_id in ('111', '18766676')}
        def check(user_id, _timeout):
            if user_id == '18766676':
                raise MonitorError('HTTP 403')
            return False
        with patch('src.mixch_monitor.has_public_archive', side_effect=check):
            self.assertEqual([], find_public_archive_profiles(candidates, 30, failed_checks=failed))
        self.assertEqual({'18766676': 'HTTP 403'}, failed)

    def test_real_ranking_failure_still_fails_and_alerts(self):
        with tempfile.TemporaryDirectory() as directory:
            config = replace(config_for_state(Path(directory) / 'state.json'), notify_on_error=True)
            with (
                patch('src.mixch_monitor.fetch_ranking', side_effect=MonitorError('全取得経路が失敗')),
                patch('src.mixch_monitor._post_discord') as post,
            ):
                self.assertEqual(1, run(config, NOW))
                self.assertIn('監視処理を完了できませんでした', post.call_args.args[1]['embeds'][0]['title'])


class SchedulerDelayTests(unittest.TestCase):
    def test_live_defaults_allow_60_minutes_but_restart_afterwards(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'GITHUB_REPOSITORY': 'owner/repo', 'GH_TOKEN': 'test',
            'WATCHDOG_STATE_FILE': str(Path(directory) / 'state.json'),
        }, clear=True):
            config = WatchdogConfig.from_environment(notifications_enabled=False)
            self.assertEqual(60, config.stale_minutes)
            self.assertEqual(60, config.relay_active_max_minutes)
            notify = Mock()
            client = FakeClient(monitor_runs=[WorkflowRun(1, NOW - timedelta(minutes=45), 'completed', 'success', '')])
            self.assertEqual(0, run_watchdog(config, now=NOW, client=client, notify=notify))
            self.assertEqual([], client.dispatched)
            notify.assert_not_called()
            client.monitor_runs[0] = WorkflowRun(1, NOW - timedelta(minutes=61), 'completed', 'success', '')
            self.assertEqual(0, run_watchdog(config, now=NOW, client=client, notify=notify))
            self.assertEqual(2, len(client.dispatched))
            notify.assert_called_once()

    def test_45_minute_queued_relay_is_not_duplicated(self):
        client = Mock()
        from datetime import datetime, timezone
        client.list_workflow_runs.return_value = [WorkflowRun(
            99, datetime.now(timezone.utc) - timedelta(minutes=45), 'queued', '', ''
        )]
        client.get_workflow_run.return_value = client.list_workflow_runs.return_value[0]
        ensure_relay(client, current_run_id='1')
        client.dispatch_workflow.assert_not_called()

    def test_resume_waits_60_minutes_for_scheduler(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            with patch.object(control, 'SETTINGS_FILE', path):
                for minutes, expected in ((45, False), (61, True)):
                    path.write_text(json.dumps({
                        'ranking_enabled': True, 'ranking_ready': True,
                        'ranking_changed_at': (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(),
                    }))
                    self.assertTrue(control.ranking_enabled())
                    self.assertEqual(expected, control.ranking_enabled(for_recovery=True))
