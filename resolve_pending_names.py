# -*- coding: utf-8 -*-
"""watchlist.json の「名前取得待ち」を実際のプロフィール名へ置き換える。

Google Apps Script からミクチャへ直接アクセスすると HTTP 403 になるため、
GitHub Actions 上の Chrome でプロフィールを開いて名前を取得する。
"""

import argparse
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


WATCHLIST_FILE = "watchlist.json"
PENDING_NAME_PREFIX = "__AUTO_NAME__:"
PROFILE_NAME_LIMIT = 10
PROFILE_LOAD_TIMEOUT_SECONDS = 20

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def load_watchlist(path: str = WATCHLIST_FILE) -> List[Dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, list):
        raise ValueError("watchlist.json が配列ではありません")
    return data


def save_watchlist(watchlist: List[Dict[str, Any]], path: str = WATCHLIST_FILE) -> None:
    with Path(path).open("w", encoding="utf-8") as fp:
        json.dump(watchlist, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def is_pending_name(value: Any) -> bool:
    return str(value or "").startswith(PENDING_NAME_PREFIX)


def pending_entries(watchlist: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [entry for entry in watchlist if is_pending_name(entry.get("name"))]


def extract_profile_name_from_body_text(text: str, user_id: str) -> Optional[str]:
    """表示済みページ本文の「ID : 数字」の直前行を名前として取り出す。"""
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(text or "").replace("\xa0", " ").splitlines()
    ]
    lines = [line for line in lines if line]
    marker = re.compile(rf"^ID\s*[:：]\s*{re.escape(str(user_id))}$")

    for index, line in enumerate(lines):
        if marker.fullmatch(line) and index > 0:
            name = lines[index - 1].strip()
            if name and "MIXCHANNEL" not in name.upper() and "1800万ユーザー" not in name:
                return name
    return None


def truncate_profile_name(name: str, limit: int = PROFILE_NAME_LIMIT) -> str:
    """Pythonの文字単位で先頭を切る。絵文字も途中で壊しにくい。"""
    return "".join(list(str(name or "").strip())[:limit])


def create_driver():
    """GitHub Actions に入っている Chrome をヘッドレスで起動する。"""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,2000")

    for binary in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        path = shutil.which(binary)
        if path:
            options.binary_location = path
            break
    return webdriver.Chrome(options=options)


def fetch_profile_name(driver, user_id: str) -> str:
    """プロフィールを開き、名前が表示されるまで最大20秒待つ。"""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    profile_url = f"https://mixch.tv/u/{user_id}"
    logging.info("プロフィールを取得します: %s", profile_url)
    driver.get(profile_url)

    def read_name(_driver):
        body_text = _driver.find_element(By.TAG_NAME, "body").text
        return extract_profile_name_from_body_text(body_text, user_id) or False

    name = WebDriverWait(driver, PROFILE_LOAD_TIMEOUT_SECONDS).until(read_name)
    shortened = truncate_profile_name(name)
    if not shortened:
        raise ValueError(f"プロフィール名が空です: {user_id}")

    logging.info("プロフィール名を取得しました: id=%s name=%s", user_id, shortened)
    return shortened


def resolve_pending_names(path: str = WATCHLIST_FILE) -> int:
    """取得待ちの全項目を処理し、成功した件数を返す。"""
    watchlist = load_watchlist(path)
    targets = pending_entries(watchlist)
    if not targets:
        logging.info("名前取得待ちの項目はありません")
        return 0

    driver = create_driver()
    resolved_count = 0
    failed_ids: List[str] = []
    try:
        for entry in targets:
            user_id = str(entry.get("id") or "").strip()
            if not user_id.isdigit():
                failed_ids.append(user_id or "(空)")
                logging.error("数字ではないIDをスキップしました: %s", user_id)
                continue
            try:
                entry["name"] = fetch_profile_name(driver, user_id)
                resolved_count += 1
            except Exception:
                failed_ids.append(user_id)
                logging.exception("プロフィール名の取得に失敗しました: %s", user_id)
    finally:
        driver.quit()

    if resolved_count:
        save_watchlist(watchlist, path)
        logging.info("watchlist.json を更新しました: %s件", resolved_count)
    if failed_ids:
        raise RuntimeError("名前取得に失敗したID: " + ", ".join(failed_ids))
    return resolved_count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--watchlist", default=WATCHLIST_FILE)
    args = parser.parse_args()

    if args.check:
        targets = pending_entries(load_watchlist(args.watchlist))
        logging.info("名前取得待ち: %s件", len(targets))
        raise SystemExit(0 if targets else 1)

    started = time.perf_counter()
    count = resolve_pending_names(args.watchlist)
    logging.info(
        "名前取得処理を終了しました: 件数=%s 経過秒=%.3f",
        count,
        time.perf_counter() - started,
    )


if __name__ == "__main__":
    main()
