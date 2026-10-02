# -*- coding: utf-8 -*-
"""Replay notifications from two historical state commits.

This tool reads both revisions from this repository, sends only the resulting
Discord notification, and does not modify the current state.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from mixcha_watcher import build_update_lines, send_embeds_to_discord


COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")


def load_json_from_git(data_dir: Path, commit_sha: str, path: str) -> Any:
    """Read one JSON file from a validated commit without changing the checkout."""

    if COMMIT_SHA_PATTERN.fullmatch(commit_sha) is None:
        raise ValueError(f"不正なコミットSHAです: {path}")

    result = subprocess.run(
        ["git", "-C", str(data_dir), "show", f"{commit_sha}:{path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def collect_changed_reports(
    base_state: Dict[str, str],
    head_state: Dict[str, str],
    watchlist: List[Dict[str, Any]],
    base_activity=None,
    head_activity=None,
) -> List[Dict[str, str]]:
    """通知済み動画番号で再送対象を選び、旧履歴だけ再生時間で比較する。"""

    base_activity, head_activity = base_activity or {}, head_activity or {}
    changed_ids = set()
    for user_id in set(base_state) | set(head_state) | set(head_activity):
        before, after = base_activity.get(user_id, {}), head_activity.get(user_id, {})
        if after.get("archive_identity_version") == 1:
            # 同じ再生時間の新動画も拾う。番号を記録しただけの移行は再送しない。
            notified_id = after.get("last_notified_archive_id")
            if notified_id and notified_id != before.get("last_notified_archive_id"):
                changed_ids.add(user_id)
        elif head_state.get(user_id) not in (None, "NO_VIDEO") and base_state.get(user_id) != head_state.get(user_id):
            changed_ids.add(user_id)

    reports = []
    for user in watchlist:
        user_id = str(user["id"])
        if user_id not in changed_ids:
            continue
        reports.append(
            {
                "id": user_id,
                "name": user["name"],
                "url": f"https://mixch.tv/u/{user_id}/live_archives",
            }
        )

    found_ids = {report["id"] for report in reports}
    missing_ids = changed_ids - found_ids
    if missing_ids:
        raise ValueError(
            f"watchlist.jsonで名前を解決できない変更対象があります: {len(missing_ids)}件"
        )

    return reports


def main() -> None:
    data_dir = Path(os.getenv("MIXCH_DATA_DIR", "."))
    base_sha = os.getenv("REPLAY_BASE", "").strip()
    head_sha = os.getenv("REPLAY_HEAD", "").strip()
    label = os.getenv("REPLAY_LABEL", "").strip()

    if not base_sha or not head_sha:
        raise ValueError("REPLAY_BASEとREPLAY_HEADの両方が必要です")

    base_state = load_json_from_git(data_dir, base_sha, "state.json")
    head_state = load_json_from_git(data_dir, head_sha, "state.json")
    base_activity = load_json_from_git(data_dir, base_sha, "activity_state.json")
    head_activity = load_json_from_git(data_dir, head_sha, "activity_state.json")
    watchlist = load_json_from_git(data_dir, head_sha, "watchlist.json")
    reports = collect_changed_reports(base_state, head_state, watchlist, base_activity, head_activity)

    if not reports:
        logging.info("再送対象は0件です")
        return

    display_label = label or f"{base_sha[:7]}..{head_sha[:7]}"
    send_embeds_to_discord(
        f"🕰️ Mixcha 取りこぼし通知（{display_label}）",
        build_update_lines(reports),
    )
    logging.info("取りこぼし通知を送信しました: %s件", len(reports))


if __name__ == "__main__":
    main()
