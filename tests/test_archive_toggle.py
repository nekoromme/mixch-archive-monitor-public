"""全体停止中は外部取得・通知・データ変更を行わないことを検証。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import mixcha_watcher as watcher

class GlobalMonitoringTests(unittest.TestCase):
    def test_pause_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "monitor_settings.json"
            watchlist = root / "watchlist.json"
            watchlist.write_text('[{"id":"1","name":"test"}]')
            settings.write_text('{"enabled":false}')
            with patch.object(watcher, "WATCHLIST_FILE", str(watchlist)), patch.object(watcher, "log_metric"), patch.object(watcher, "get_discord_webhook_url") as secret, patch.object(watcher, "create_driver") as driver, patch.object(watcher, "save_json") as save:
                watcher.main()
                secret.assert_not_called()
                driver.assert_not_called()
                save.assert_not_called()
                self.assertEqual(json.loads(watchlist.read_text())[0]["id"], "1")
                settings.write_text('{"enabled":true}')
                self.assertTrue(watcher.monitoring_enabled())
                settings.write_text('{"enabled":"false"}')
                with self.assertRaises(ValueError):
                    watcher.monitoring_enabled()
