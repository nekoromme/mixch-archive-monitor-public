"""共通のオンオフ設定。未設定・破損時に勝手に監視を始めないための入口。"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parents[2] / 'monitor_settings.json'

def read_settings():
    settings = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
    for name in ('ranking_enabled', 'ranking_ready'):
        if type(settings.get(name)) is not bool:
            raise ValueError('monitor_settings.json: ' + name + ' must be boolean')
    if type(settings.get('ranking_recording_enabled', False)) is not bool:
        raise ValueError('monitor_settings.json: ranking_recording_enabled must be boolean')
    return settings

def ranking_enabled(for_recovery=False):
    """従来の通知スイッチ。順位の記録だけをオンにしても通知は再開しません。"""
    return _enabled(read_settings(), False, for_recovery)

def ranking_recording_enabled():
    settings = read_settings()
    return settings.get('ranking_recording_enabled', False) and settings['ranking_ready']

def ranking_work_enabled(for_recovery=False):
    """通知か日別記録のどちらかを利用している間、同じ定期取得を共有します。"""
    return _enabled(read_settings(), True, for_recovery)

def _enabled(settings, include_recording, for_recovery):
    enabled = (settings['ranking_enabled'] or (include_recording and settings.get('ranking_recording_enabled', False))) and settings['ranking_ready']
    if not enabled:
        logging.info('RANKING_PAUSED: ランキング監視は停止中または移行準備中です。')
    # 再開直後は過去の停止時間を故障扱いせず、最初の定期実行を待ちます。
    if enabled and for_recovery and settings.get('ranking_changed_at'):
        changed = datetime.fromisoformat(settings['ranking_changed_at'].replace('Z', '+00:00'))
        if 0 <= (datetime.now(timezone.utc) - changed).total_seconds() < 12 * 60:
            logging.info('RANKING_RESUME_GRACE: 再開後の最初の実行を待っています。')
            return False
    return enabled
