"""起動予約を止めた古い実行状態と、GitHub読取りの一時障害を検証する。"""
import unittest
from unittest.mock import Mock, patch

from src.github_actions_recovery import GitHubApiClient, GitHubApiError, latest_successful_run, parse_workflow_runs


class GitHubReadRecoveryTests(unittest.TestCase):
    def test_transient_read_errors_retry_without_breaking_relay(self):
        client = GitHubApiClient('owner/repo', 'test')
        client._request_json_once = Mock(side_effect=[
            GitHubApiError('503', status_code=503),
            GitHubApiError('connection lost'),
            {'ranking_enabled': True},
        ])
        with patch('src.github_actions_recovery.time.sleep') as sleep:
            self.assertEqual({'ranking_enabled': True}, client._request_json('GET', '/example'))
        self.assertEqual([2.0, 5.0], [call.args[0] for call in sleep.call_args_list])

    def test_permission_error_does_not_retry_and_post_uses_existing_dispatch_retry(self):
        for method, error in (('GET', GitHubApiError('401', status_code=401)),
                              ('POST', GitHubApiError('503', status_code=503))):
            client = GitHubApiClient('owner/repo', 'test')
            client._request_json_once = Mock(side_effect=error)
            with patch('src.github_actions_recovery.time.sleep') as sleep:
                with self.assertRaises(GitHubApiError):
                    client._request_json(method, '/example')
            sleep.assert_not_called()
            client._request_json_once.assert_called_once()

    def test_individual_run_read_bypasses_old_list_response(self):
        client = GitHubApiClient('owner/repo', 'test')
        client._request_json = Mock(return_value={
            'id': 99, 'created_at': '2026-10-21T14:25:19Z',
            'status': 'completed', 'conclusion': 'success', 'html_url': '',
        })
        self.assertEqual('completed', client.get_workflow_run(99).status)
        self.assertIn('/actions/runs/99?monitor_check=', client._request_json.call_args.args[1])

    def test_deployment_dry_run_does_not_count_as_successful_production_monitoring(self):
        runs = parse_workflow_runs({'workflow_runs': [{
            'id': 99, 'created_at': '2026-10-21T14:25:19Z', 'status': 'completed',
            'conclusion': 'success', 'event': 'push',
        }]})
        self.assertEqual('push', runs[0].event)
        self.assertIsNone(latest_successful_run(runs))
