import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import control
from src.mixch_monitor import load_state, run
from src.ranking_history import load_history, new_history, observe, save_history
from test_monitor import config_for_state, stream


class RankingHistoryTests(unittest.TestCase):
    def test_one_day_keeps_all_reached_ranks_and_ids_survive_name_changes(self):
        history = new_history()
        first = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)
        observe(history, [stream('111', 10, '古い名前', rank=1), stream('222', 1, rank=3)], first)
        observe(history, [stream('111', 1, '新しい名前', rank=3)], first.replace(hour=16))
        observe(history, [stream('111', 1, '新しい名前', rank=3)], first.replace(hour=16))
        self.assertEqual(history['days']['2026-10-01']['users'], {'111':5, '222':4})
        self.assertEqual(history['days']['2026-10-01']['observations'], 2)
        self.assertEqual(history['profiles']['111']['name'], '新しい名前')
        self.assertEqual(len(history['profiles']), 2)

    def test_japan_midnight_month_and_year_boundaries(self):
        history = new_history()
        for timestamp in ['2025-12-31T14:59:59+00:00', '2025-12-31T15:00:00+00:00',
                          '2026-09-30T14:59:59+00:00', '2026-09-30T15:00:00+00:00']:
            observe(history, [stream('111', 1)], datetime.fromisoformat(timestamp))
        self.assertEqual(sorted(history['days']), ['2025-12-31','2026-01-01','2026-09-30','2026-10-01'])

    def test_record_only_saves_history_without_notifying_or_altering_notifications(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory)/'state.json'
            state = {'version':1, 'notifications':{}, 'night_candidates':{}, 'metadata':{}}
            state_file.write_text(json.dumps(state))
            history_file = Path(directory)/'ranking-days.json'
            config = replace(config_for_state(state_file), history_file=history_file,
                             notifications_enabled=False, discord_webhook_url='')
            with patch('src.mixch_monitor.fetch_ranking', return_value=[stream('14082684', 1, rank=1)]), \
                 patch('src.mixch_monitor._post_discord') as discord, \
                 patch('src.mixch_monitor.find_public_archive_profiles') as archives:
                self.assertEqual(run(config, datetime(2026,10,1,tzinfo=timezone.utc)), 0)
            self.assertEqual(load_state(state_file), state)
            self.assertEqual(load_history(history_file)['days']['2026-10-01']['users'], {'14082684':1})
            discord.assert_not_called()
            archives.assert_not_called()

    def test_dry_run_and_fetch_failure_never_replace_recorded_history(self):
        with tempfile.TemporaryDirectory() as directory:
            history_file = Path(directory)/'ranking-days.json'
            history = new_history()
            observe(history, [stream('111', 1)], datetime(2026,9,1,tzinfo=timezone.utc))
            save_history(history_file, history)
            original = history_file.read_bytes()
            config = replace(config_for_state(Path(directory)/'state.json'), history_file=history_file,
                             notifications_enabled=False)
            with patch('src.mixch_monitor.fetch_ranking', return_value=[stream('222',1)]):
                self.assertEqual(run(replace(config,dry_run=True)),0)
            self.assertEqual(history_file.read_bytes(),original)
            with patch('src.mixch_monitor.fetch_ranking', side_effect=RuntimeError('offline')):
                self.assertEqual(run(config),1)
            self.assertEqual(history_file.read_bytes(),original)
            history_file.write_text('{broken')
            with self.assertRaises(ValueError): load_history(history_file)
            self.assertEqual(history_file.read_text(),'{broken')

    def test_missing_rank_is_not_guessed(self):
        history = new_history()
        with self.assertRaises(ValueError):
            observe(history, [stream('111',200,rank=None)], datetime.now(timezone.utc))
        self.assertEqual(history,new_history())

    def test_recording_work_is_independent_of_both_original_switches(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_file = Path(directory)/'settings.json'
            settings_file.write_text(json.dumps({'enabled':False,'ranking_enabled':False,
                'ranking_ready':True,'ranking_recording_enabled':True}))
            with patch.object(control,'SETTINGS_FILE',settings_file):
                self.assertFalse(control.ranking_enabled())
                self.assertTrue(control.ranking_recording_enabled())
                self.assertTrue(control.ranking_work_enabled())
