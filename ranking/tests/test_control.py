import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src import control, mixch_monitor, github_actions_recovery

class ControlTests(unittest.TestCase):
    def test_switches_are_independent_and_paused_means_no_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            with patch.object(control, 'SETTINGS_FILE', path):
                for archive in [True, False]:
                    path.write_text(json.dumps({'enabled':archive, 'ranking_enabled':False,'ranking_ready':True}))
                    with patch.dict(os.environ, {'DRY_RUN':'false'}), patch.object(mixch_monitor, 'run') as run, patch.object(github_actions_recovery, 'run_watchdog') as watchdog:
                        self.assertEqual(mixch_monitor.main(),0)
                        self.assertEqual(github_actions_recovery._watchdog_command(None),0)
                        run.assert_not_called()
                        watchdog.assert_not_called()
                path.write_text('{"enabled":false,"ranking_enabled":true,"ranking_ready":true}')
                self.assertTrue(control.ranking_enabled())
                path.write_text('{"ranking_enabled":true,"ranking_ready":false}')
                self.assertFalse(control.ranking_enabled())
                path.write_text('{"ranking_enabled":"false","ranking_ready":true}')
                with self.assertRaises(ValueError): control.ranking_enabled()

    def test_resume_grace_only_delays_recovery_not_monitoring(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'ranking_enabled':True,'ranking_ready':True,'ranking_changed_at':datetime.now(timezone.utc).isoformat()}))
            with patch.object(control,'SETTINGS_FILE',path):
                self.assertTrue(control.ranking_enabled())
                self.assertFalse(control.ranking_enabled(for_recovery=True))
