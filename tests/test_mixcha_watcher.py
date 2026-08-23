import os
import unittest
from pathlib import Path
from unittest.mock import patch

import mixcha_watcher as watcher


class ImmediateMarkerDriver:
    """A fake browser whose newest archive marker is already rendered."""

    def __init__(self, text: str = "151:04"):
        self.text = text
        self.requested_urls = []
        self.executed_scripts = []
        self.page_source_access_count = 0

    def get(self, url: str):
        self.requested_urls.append(url)

    def execute_script(self, script: str, *args):
        self.executed_scripts.append(script)
        if "document.querySelector" in script:
            return self.text
        raise AssertionError(f"高速経路では実行されないはずのスクリプトです: {script}")

    @property
    def page_source(self):
        self.page_source_access_count += 1
        raise AssertionError("高速経路ではページ全体のHTMLを取得しないはずです")


class DelayedMarkerDriver(ImmediateMarkerDriver):
    """A fake browser whose marker appears on the second DOM check."""

    def __init__(self):
        super().__init__()
        self.dom_query_count = 0

    def execute_script(self, script: str, *args):
        self.executed_scripts.append(script)
        if "document.querySelector" in script:
            self.dom_query_count += 1
            return None if self.dom_query_count == 1 else self.text
        raise AssertionError(f"高速経路では実行されないはずのスクリプトです: {script}")


class FallbackDriver:
    """A fake browser that needs the previous scroll-and-HTML fallback."""

    def __init__(self, page_source: str):
        self._page_source = page_source
        self.page_source_access_count = 0
        self.scroll_count = 0

    def get(self, _url: str):
        return None

    def execute_script(self, script: str, *args):
        if "document.querySelector" in script:
            return None
        if script == "return document.body.scrollHeight":
            return 1000
        if script == "window.scrollTo(0, document.body.scrollHeight);":
            self.scroll_count += 1
            return None
        raise AssertionError(f"想定外のスクリプトです: {script}")

    @property
    def page_source(self):
        self.page_source_access_count += 1
        return self._page_source


class MarkerExtractionTests(unittest.TestCase):
    def test_extract_latest_marker(self):
        self.assertEqual("12:34", watcher.extract_latest_marker("配信時間 12:34"))
        self.assertEqual("1:02", watcher.extract_latest_marker("\u00a01:02\u00a0"))
        self.assertIsNone(watcher.extract_latest_marker("時間を解析できません"))
        self.assertIsNone(watcher.extract_latest_marker(None))

    def test_extract_latest_archive_date_supports_japanese_and_english(self):
        reference_date = watcher.datetime.date(2026, 8, 21)

        self.assertEqual(
            "2026-08-21",
            watcher.extract_latest_archive_date("3時間前 28:40", reference_date),
        )
        self.assertEqual(
            "2026-08-21",
            watcher.extract_latest_archive_date("19 hours ago56:49", reference_date),
        )
        self.assertEqual(
            "2026-08-02",
            watcher.extract_latest_archive_date("19 days ago6:58", reference_date),
        )
        self.assertIsNone(
            watcher.extract_latest_archive_date("relative age unavailable", reference_date)
        )

    def test_ready_marker_skips_fixed_wait_scroll_and_full_html(self):
        driver = ImmediateMarkerDriver()

        with (
            patch.object(watcher, "log_metric"),
            patch.object(watcher.time, "sleep") as sleep_mock,
        ):
            marker = watcher.get_latest_marker(
                driver,
                "12345",
                user_name="テスト",
                marker_wait_seconds=5,
            )

        self.assertEqual(("151:04", None), marker)
        sleep_mock.assert_not_called()
        self.assertEqual(0, driver.page_source_access_count)
        self.assertFalse(
            any("window.scrollTo" in script for script in driver.executed_scripts)
        )

    def test_wait_polls_until_marker_is_ready(self):
        driver = DelayedMarkerDriver()

        with (
            patch.object(watcher, "log_metric"),
            patch.object(watcher.time, "sleep") as sleep_mock,
        ):
            marker = watcher.get_latest_marker(
                driver,
                "12345",
                user_name="テスト",
                marker_wait_seconds=5,
            )

        self.assertEqual(("151:04", None), marker)
        self.assertEqual(2, driver.dom_query_count)
        sleep_mock.assert_called_once()
        self.assertLessEqual(
            sleep_mock.call_args.args[0],
            watcher.MARKER_POLL_INTERVAL,
        )
        self.assertFalse(
            any("window.scrollTo" in script for script in driver.executed_scripts)
        )

    def test_unready_marker_keeps_scroll_and_old_html_parser(self):
        driver = FallbackDriver(
            '<html><span class="css-lmrlel e1hhguts0">151:04</span></html>'
        )

        with (
            patch.object(watcher, "log_metric"),
            patch.object(watcher.time, "sleep") as sleep_mock,
        ):
            marker = watcher.get_latest_marker(
                driver,
                "12345",
                user_name="テスト",
                marker_wait_seconds=0,
            )

        self.assertEqual(("151:04", None), marker)
        self.assertEqual(1, driver.scroll_count)
        self.assertEqual(1, driver.page_source_access_count)
        sleep_mock.assert_called_once_with(watcher.SCROLL_WAIT_SECONDS)

    def test_missing_marker_still_returns_no_video(self):
        driver = FallbackDriver("<html><body>アーカイブなし</body></html>")

        with (
            patch.object(watcher, "log_metric"),
            patch.object(watcher.time, "sleep"),
        ):
            marker = watcher.get_latest_marker(
                driver,
                "12345",
                user_name="テスト",
                marker_wait_seconds=0,
            )

        self.assertEqual(("NO_VIDEO", None), marker)


class WorkflowSynchronizationTests(unittest.TestCase):
    def test_final_daily_notification_cron_matches_workflow(self):
        workflow_path = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "schedule.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn(
            f"- cron: '{watcher.LAST_DAILY_INACTIVE_NOTIFICATION_CRON}'",
            workflow,
        )
        self.assertIn("- cron: '7 8,10 * * *'", workflow)
        self.assertNotIn("- cron: '7 8,10,12 * * *'", workflow)

    def test_watchlist_changes_are_committed(self):
        workflow_path = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "schedule.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn(
            "git add -- state.json activity_state.json watchlist.json",
            workflow,
        )

    def test_private_data_is_checked_out_separately(self):
        workflow_path = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "schedule.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn("repository: ${{ github.repository_owner }}/mixch", workflow)
        self.assertIn("MIXCH_DATA_DIR: private-data", workflow)
        self.assertIn("PUBLIC_LOGS: 'true'", workflow)

    def test_missing_discord_secret_fails_before_watcher_runs(self):
        workflow_path = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "schedule.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn("Validate Discord notification secret", workflow)
        self.assertIn("DISCORD_WEBHOOK_URL is not configured", workflow)

    def test_replay_mode_does_not_run_or_commit_normal_monitor_state(self):
        workflow_path = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "schedule.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn("Replay missed notifications", workflow)
        self.assertIn("inputs.replay_base == ''", workflow)
        self.assertIn("inputs.replay_base != ''", workflow)

    def test_public_workflow_has_temporary_cutoff(self):
        workflow_path = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "schedule.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn('2026-09-01', workflow)


class PublicLogPrivacyTests(unittest.TestCase):
    def test_private_metric_keys_cover_streamer_identity_and_errors(self):
        self.assertTrue(
            {
                "user_id",
                "user_name",
                "url",
                "error_message",
                "slowest_user_id",
                "slowest_user_name",
            }.issubset(watcher.PRIVATE_METRIC_KEYS)
        )

    def test_only_final_scheduled_run_sends_inactive_notification(self):
        with patch.dict(
            os.environ,
            {"SCHEDULE_CRON": watcher.LAST_DAILY_INACTIVE_NOTIFICATION_CRON},
            clear=False,
        ):
            self.assertTrue(watcher.is_last_daily_inactive_notification_run())

        with patch.dict(os.environ, {"SCHEDULE_CRON": "7 10 * * *"}, clear=False):
            self.assertFalse(watcher.is_last_daily_inactive_notification_run())


class DiscordDeliveryTests(unittest.TestCase):
    def test_missing_webhook_is_a_fatal_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(watcher.DiscordDeliveryError):
                watcher.get_discord_webhook_url()

    def test_http_failure_is_fatal_without_exposing_webhook(self):
        class RejectedResponse:
            status_code = 401
            ok = False
            text = "rejected"

        secret_url = "https://discord.invalid/api/webhooks/secret-value"
        with (
            patch.dict(
                os.environ,
                {"DISCORD_WEBHOOK_URL": secret_url},
                clear=False,
            ),
            patch.object(watcher.requests, "post", return_value=RejectedResponse()),
            patch.object(watcher, "log_metric"),
        ):
            with self.assertRaises(watcher.DiscordDeliveryError) as raised:
                watcher.send_embeds_to_discord("test", ["message"])

        self.assertNotIn(secret_url, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
