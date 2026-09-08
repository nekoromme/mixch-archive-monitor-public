"""共通のオンオフ設定。未設定・破損時に勝手に監視を始めないための入口。"""
import json
import logging
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parents[2] / 'monitor_settings.json'

def ranking_enabled():
    settings = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
    for name in ('ranking_enabled', 'ranking_ready'):
        if type(settings.get(name)) is not bool:
            raise ValueError('monitor_settings.json: ' + name + ' must be boolean')
    enabled = settings['ranking_enabled'] and settings['ranking_ready']
    if not enabled:
        logging.info('RANKING_PAUSED: ランキング監視は停止中または移行準備中です。')
    return enabled
