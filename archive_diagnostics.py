"""登録中の実ページと履歴移行を検証する。通知・運用データの変更は行わない。"""
import collections
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import mixcha_watcher as watcher


def main():
    protected_files = [Path(name) for name in (
        "watchlist.json", "state.json", "activity_state.json", "monitor_settings.json")]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected_files}
    users = watcher.dedupe_watchlist(watcher.load_watchlist(watcher.WATCHLIST_FILE))
    snapshots, failed = {}, 0
    sources = collections.Counter()
    client = watcher.ArchiveClient(browser_factory=watcher.create_driver, metric=watcher.log_metric)
    try:
        for index, user in enumerate(users, 1):
            try:
                snapshot = client.fetch_latest(user["id"])
                snapshots[str(user["id"])] = snapshot
                sources[snapshot.source] += 1
                print("READ_OK", index, "empty" if snapshot.archive_id is None else "identified", flush=True)
            except Exception as error:
                failed += 1
                print("READ_FAILED", index, type(error).__name__, flush=True)
        # 二度取得しても同じ番号なら、新着判定にならないことを実データで確認。
        repeated, changed_while_reading = 0, 0
        for user in users[:3]:
            user_id = str(user["id"])
            if user_id in snapshots:
                again = client.fetch_latest(user_id)
                repeated += 1
                changed_while_reading += again.archive_id != snapshots[user_id].archive_id
        print("VERIFIED_COUNTS", json.dumps({
            "total": len(users), "identified": sum(s.archive_id is not None for s in snapshots.values()),
            "empty": sum(s.archive_id is None for s in snapshots.values()), "failed": failed,
            "sources": dict(sources), "repeated": repeated,
            "changed_while_reading": changed_while_reading,
        }), flush=True)
    finally:
        client.close()
    if failed:
        raise watcher.ArchiveReadError("Some registered archive pages could not be identified")

    # 本体の保存・移行・再実行も、実際に取得した入力と一時フォルダーで検証。
    # 本番の全体スイッチが停止中でも、その設定を変更する必要はない。
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for path in protected_files[:3]:
            shutil.copyfile(path, root / path.name)
        (root / "monitor_settings.json").write_text('{"enabled":true}', encoding="utf-8")
        with (
            patch.object(watcher, "WATCHLIST_FILE", str(root / "watchlist.json")),
            patch.object(watcher, "STATE_FILE", str(root / "state.json")),
            patch.object(watcher, "ACTIVITY_STATE_FILE", str(root / "activity_state.json")),
            patch.object(watcher, "get_discord_webhook_url", return_value="diagnostic-only"),
            patch.object(watcher, "is_last_daily_inactive_notification_run", return_value=False),
            patch.object(watcher.ArchiveClient, "fetch_latest", side_effect=lambda user_id: snapshots[str(user_id)]),
            patch.object(watcher, "send_embeds_to_discord", return_value=[]) as notify,
        ):
            watcher.main()
            first_candidates = notify.call_count
            notify.reset_mock()
            watcher.main()
            if notify.call_count:
                raise AssertionError("Same snapshots produced duplicate update notifications")
            activity = json.loads((root / "activity_state.json").read_text(encoding="utf-8"))
            identified_history = sum(activity[str(user["id"])].get("latest_archive_id") is not None for user in users)
            print("VERIFIED_MIGRATION", json.dumps({
                "identified_history": identified_history,
                "first_pass_notification_batches": first_candidates,
                "second_pass_notification_batches": notify.call_count,
                "real_notifications_sent": 0,
            }), flush=True)

    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected_files}
    if before != after:
        raise AssertionError("Diagnostic run changed live monitor files")
    print("LIVE_FILES_PRESERVED", flush=True)


if __name__ == "__main__":
    main()
