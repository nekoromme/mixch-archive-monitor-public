"""画面変更・通信異常で「動画なし」を保存しないための回帰テスト。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import mixcha_watcher as watcher


class ArchiveReadHealthTests(unittest.TestCase):
    def test_changed_generated_classes_still_parse(self):
        for css in ('css-lmrlel e1938c410', 'completely-new-class'):
            driver = MagicMock()
            driver.execute_script.return_value = None
            driver.page_source = (
                f'<span class="{css}">1 hours ago&nbsp;100:09</span>'
                f'<span class="{css}">12 hours ago&nbsp;120:36</span>'
            )
            text, method = watcher.read_latest_marker_text_with_fallback(driver)
            self.assertEqual(watcher.extract_latest_marker(text), '100:09')
            self.assertEqual(method, 'semantic_html')

    def test_unknown_page_is_error_not_empty(self):
        for html in ('<html>Login</html>', '<html>Loading...</html>', '<html></html>'):
            driver = MagicMock()
            driver.execute_script.return_value = None
            driver.page_source = html
            with self.assertRaises(watcher.ArchiveReadError):
                watcher.read_latest_marker_text_with_fallback(driver)

    def test_failed_read_preserves_files_and_skips_notifications_and_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            watchlist = root / 'watchlist.json'
            state = root / 'state.json'
            activity = root / 'activity_state.json'
            watchlist.write_text('[{"id":"1","name":"test"}]')
            state.write_text('{"1":"100:09"}')
            activity.write_text('{"1":{"last_notified_date":"2026-08-01","latest_archive_date":"2026-08-01"}}')
            before = [p.read_text() for p in (watchlist, state, activity)]
            with (
                patch.object(watcher, 'WATCHLIST_FILE', str(watchlist)),
                patch.object(watcher, 'STATE_FILE', str(state)),
                patch.object(watcher, 'ACTIVITY_STATE_FILE', str(activity)),
                patch.object(watcher, 'get_discord_webhook_url', return_value='unused'),
                patch.object(watcher, 'create_driver', return_value=MagicMock()),
                patch.object(watcher, 'get_latest_marker', side_effect=watcher.ArchiveReadError('unreadable')),
                patch.object(watcher, 'log_metric'),
                patch.object(watcher, 'send_embeds_to_discord') as notify,
                patch.object(watcher, 'save_json') as save,
            ):
                with self.assertRaises(watcher.ArchiveReadError):
                    watcher.main()
                notify.assert_not_called()
                save.assert_not_called()
            self.assertEqual(before, [p.read_text() for p in (watchlist, state, activity)])
