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
) -> List[Dict[str, str]]:
    """Return changed watched users in watchlist order."""

    changed_ids = {
        user_id
        for user_id in set(base_state) | set(head_state)
        if base_state.get(user_id) != head_state.get(user_id)
    }

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
    watchlist = load_json_from_git(data_dir, head_sha, "watchlist.json")
    reports = collect_changed_reports(base_state, head_state, watchlist)

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
