import base64
import json
import unittest
from unittest.mock import Mock
from datetime import datetime, timezone, timedelta
from src.relay import run_relay, ensure_relay
from src.github_actions_recovery import WorkflowRun


class RelayTests(unittest.TestCase):
    def client(self, enabled=True, ready=True):
        client = Mock(repository='owner/repo')
        client.list_workflow_runs.return_value = []
        settings = {'ranking_enabled':enabled, 'ranking_ready':ready, 'ranking_scheduler_probe':True}
        client._request_json.side_effect = lambda *args: {'content':base64.b64encode(json.dumps(settings).encode()).decode()}
        return client, settings

    def test_off_does_not_wait_or_dispatch(self):
        client, _ = self.client(enabled=False)
        sleep = Mock()
        run_relay(client, sleep=sleep)
        sleep.assert_not_called()
        client.dispatch_workflow.assert_not_called()

    def test_recording_continues_when_notifications_are_paused(self):
        client, settings = self.client(enabled=False)
        settings['ranking_recording_enabled'] = True
        run_relay(client, sleep=Mock())
        client.dispatch_workflow.assert_any_call('ranking-monitor.yml', inputs={'dry_run':'false','test_webhook':'false'})
        client.dispatch_workflow.assert_any_call('ranking-relay.yml', inputs={'probe':'false'})

    def test_stop_during_wait_ends_chain(self):
        client, settings = self.client()
        def stop(_): settings['ranking_enabled'] = False
        run_relay(client, sleep=stop)
        client.dispatch_workflow.assert_not_called()

    def test_live_waits_five_minutes_and_reserves_next(self):
        client, _ = self.client()
        sleep = Mock()
        run_relay(client, sleep=sleep)
        self.assertEqual(sleep.call_count,5)
        self.assertTrue(all(call.args == (60,) for call in sleep.call_args_list))
        self.assertEqual(client.dispatch_workflow.call_count,2)
        client.dispatch_workflow.assert_any_call('ranking-monitor.yml', inputs={'dry_run':'false','test_webhook':'false'})
        client.dispatch_workflow.assert_any_call('ranking-relay.yml', inputs={'probe':'false'})

    def test_probe_never_notifies_or_reserves_next(self):
        client, _ = self.client(ready=False)
        run_relay(client, probe=True, sleep=Mock())
        client.dispatch_workflow.assert_called_once_with('ranking-monitor.yml', inputs={'dry_run':'true','test_webhook':'false'})

    def test_existing_run_is_not_duplicated(self):
        client, _ = self.client()
        client.list_workflow_runs.return_value = [WorkflowRun(99,datetime.now(timezone.utc),'in_progress','', 'https://example.test')]
        client.get_workflow_run.return_value = client.list_workflow_runs.return_value[0]
        ensure_relay(client, current_run_id='1')
        client.dispatch_workflow.assert_not_called()

    def test_stale_active_list_does_not_break_monitor_or_relay_chain(self):
        # 実際の停止直前ログと同じく、両方の一覧が古い実行中を返すケース。
        client, _ = self.client()
        created = datetime.now(timezone.utc) - timedelta(minutes=5)
        client.list_workflow_runs.return_value = [WorkflowRun(99, created, 'in_progress', '', '')]
        client.get_workflow_run.return_value = WorkflowRun(99, created, 'completed', 'success', '')
        run_relay(client, sleep=Mock(), current_run_id='100')
        self.assertEqual(2, client.dispatch_workflow.call_count)
        client.dispatch_workflow.assert_any_call('ranking-monitor.yml', inputs={'dry_run':'false','test_webhook':'false'})
        client.dispatch_workflow.assert_any_call('ranking-relay.yml', inputs={'probe':'false'})

    def test_deploy_probe_never_receives_production_relay_baton(self):
        # コード更新時の確認だけのジョブは、次回の本番監視を予約しない。
        client, _ = self.client()
        client.list_workflow_runs.return_value = [WorkflowRun(
            99, datetime.now(timezone.utc), 'queued', '', '', event='push'
        )]
        ensure_relay(client, current_run_id='100')
        client.get_workflow_run.assert_not_called()
        client.dispatch_workflow.assert_called_once_with('ranking-relay.yml', inputs={'probe':'false'})

    def test_bad_settings_fail_closed(self):
        client, settings = self.client()
        settings['ranking_enabled'] = 'true'
        with self.assertRaises(ValueError): run_relay(client, sleep=Mock())
        client.dispatch_workflow.assert_not_called()
