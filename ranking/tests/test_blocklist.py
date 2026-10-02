import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.blocklist import read_blocked_user_ids
from src.mixch_monitor import Config, select_eligible_streams, select_night_candidates
from test_monitor import NOW, new_state, stream


class SharedBlocklistTests(unittest.TestCase):
    def test_saved_delete_is_loaded_again_and_blocks_day_and_night(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ranking-blocklist.json'
            with patch('src.blocklist.BLOCKLIST_FILE',path), patch.dict(os.environ,{},clear=True):
                path.write_text(json.dumps({'version':1,'blocked':{}}))
                self.assertNotIn('111',Config.from_environment().blocked_user_ids)
                path.write_text(json.dumps({'version':1,'blocked':{'111':True}}))
                config = Config.from_environment()
                self.assertIn('111',config.blocked_user_ids)
                renamed = stream('111',999,name='名前変更後')
                self.assertEqual([],select_eligible_streams([renamed],new_state(),150,12,NOW,config.blocked_user_ids))
                self.assertEqual([],select_night_candidates([renamed],new_state(),150,12,NOW,config.blocked_user_ids))

    def test_missing_or_broken_file_never_turns_into_empty_blocklist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ranking-blocklist.json'
            with self.assertRaisesRegex(ValueError,'通知を停止'):
                read_blocked_user_ids(path)
            for value in [None,[],{'version':True,'blocked':{}},{'version':2,'blocked':{}},
                          {'version':1,'blocked':[]},{'version':1,'blocked':{'abc':True}},
                          {'version':1,'blocked':{'111':False}},{'version':1,'blocked':{'111':1}}]:
                path.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'通知を停止'):
                    read_blocked_user_ids(path)
