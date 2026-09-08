"""約5分おきの起動をGitHub内で引き継ぐ処理。

Cloudflareには設定ファイルの編集だけを任せ、監視の起動にはGitHubが
実行ごとに発行する鍵を使います。利用者による鍵の追加設定は不要です。
公開リポジトリの標準実行環境を使用し、1本だけ順番に動かします。
"""
import base64
import logging
import os
import time
from datetime import datetime, timezone

from .github_actions_recovery import (
    GitHubApiClient, dispatch_with_retry, recent_active_run,
)

LOGGER = logging.getLogger(__name__)
RELAY = 'ranking-relay.yml'
MONITOR = 'ranking-monitor.yml'


def read_settings(client):
    """待機中の停止操作も拾うため、保存先の最新版を読みます。"""
    import json
    payload = client._request_json(
        'GET', f'/repos/{client.repository}/contents/monitor_settings.json?ref=main'
    )
    settings = json.loads(base64.b64decode(payload['content']).decode('utf-8'))
    for key in ('ranking_enabled', 'ranking_ready'):
        if type(settings.get(key)) is not bool:
            raise ValueError('監視設定の形式が不正です: ' + key)
    return settings


def allowed(settings, probe):
    if probe:
        # 移行確認は1回だけで、通知も次の予約も行いません。
        return settings['ranking_ready'] is False and settings.get('ranking_scheduler_probe') is True
    return settings['ranking_enabled'] and settings['ranking_ready']


def ensure_relay(client, *, current_run_id=None):
    """再開や自動復旧が重なっても待機処理を増やさないよう確認します。"""
    runs = client.list_workflow_runs(RELAY)
    others = [run for run in runs if str(run.run_id) != str(current_run_id)]
    if recent_active_run(others, datetime.now(timezone.utc), 12):
        LOGGER.info('RANKING_RELAY_EXISTS: 既存の待機処理へ引き継ぎます。')
        return
    dispatch_with_retry(client, RELAY, inputs={'probe':'false'})
    LOGGER.info('RANKING_RELAY_RESERVED: 次の約5分後の監視を予約しました。')


def run_relay(client, *, probe=False, sleep=time.sleep, current_run_id=None):
    if not allowed(read_settings(client), probe):
        LOGGER.info('RANKING_RELAY_PAUSED: 停止中または移行準備中です。')
        return
    LOGGER.info('RANKING_RELAY_WAIT: 約5分待機します。通知なし確認=%s', probe)
    # 1分ごとに設定を読み直し、オフになったら次回を予約せず終了します。
    for _ in range(5):
        sleep(60)
        if not allowed(read_settings(client), probe):
            LOGGER.info('RANKING_RELAY_PAUSED: 待機中に設定が変わったため終了します。')
            return
    try:
        active = recent_active_run(
            client.list_workflow_runs(MONITOR), datetime.now(timezone.utc), 12
        )
        if active:
            LOGGER.info('RANKING_MONITOR_ACTIVE: 既に実行中の監視を待ちます。')
        else:
            dispatch_with_retry(client, MONITOR, inputs={
                'dry_run': str(probe).lower(), 'test_webhook':'false',
            })
            LOGGER.info('RANKING_MONITOR_DISPATCHED: 監視を起動しました。通知なし確認=%s', probe)
    finally:
        # 監視の起動が一時的に失敗しても次の予約を試み、連鎖停止を避けます。
        if not probe and allowed(read_settings(client), False):
            ensure_relay(client, current_run_id=current_run_id)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    client = GitHubApiClient(os.environ['GITHUB_REPOSITORY'], os.environ['GH_TOKEN'], 10)
    run_relay(client, probe=os.getenv('RELAY_PROBE', '').lower() == 'true',
              current_run_id=os.getenv('GITHUB_RUN_ID'))


if __name__ == '__main__':
    main()
