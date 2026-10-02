# -*- coding: utf-8 -*-
import datetime
import json
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from archive_metadata import ArchiveClient, ArchiveReadError, plan_archive_update

# 監視対象と状態は、実行するリポジトリ内の3つのJSONへ保存します。
# MIXCH_DATA_DIRを指定すれば、ローカルテスト時だけ別ディレクトリへ
# 切り替えることもできます。未指定時はリポジトリ直下です。
DATA_DIR = Path(os.getenv("MIXCH_DATA_DIR", "."))
WATCHLIST_FILE = str(DATA_DIR / "watchlist.json")
STATE_FILE = str(DATA_DIR / "state.json")
ACTIVITY_STATE_FILE = str(DATA_DIR / "activity_state.json")
PAGE_LOAD_WAIT = 5
MARKER_POLL_INTERVAL = 0.25
SCROLL_REPEAT = 3
SCROLL_WAIT_SECONDS = 1.5
DESCRIPTION_LIMIT = 3900
INACTIVE_DAYS_THRESHOLD = 20
LAST_DAILY_INACTIVE_NOTIFICATION_CRON = "7 12 * * *"  # UTC12:07 → JST21:07
METRICS_FILE = "run_metrics.jsonl"
PUBLIC_LOGS = os.getenv("PUBLIC_LOGS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
PRIVATE_METRIC_KEYS = {
    "error_message",
    "user_id",
    "user_name",
    "url",
    "slowest_user_id",
    "slowest_user_name",
    "archive_id",
}
JST = datetime.timezone(datetime.timedelta(hours=9))
LATEST_MARKER_SELECTOR = "span.css-lmrlel"
LATEST_MARKER_PATTERN = re.compile(r"(\d+:\d+)$")
LATEST_ARCHIVE_AGE_PATTERN = re.compile(r"(\d+)(秒|分|時間|日)前")
LATEST_ARCHIVE_AGE_EN_PATTERN = re.compile(
    r"(\d+)\s*(seconds?|minutes?|hours?|days?)\s+ago",
    re.IGNORECASE,
)
LATEST_MARKER_TEXT_SCRIPT = r"""
// 自動生成されるクラス名が変わっても、経過時間＋再生時間で見つけます。
const isArchive = text => /(?:\d+(?:秒|分|時間|日)前|\d+\s*(?:seconds?|minutes?|hours?|days?)\s+ago)\s*\d+:\d+$/i.test(text.replace(/\u00a0/g, ' ').trim());
const element = Array.from(document.querySelectorAll('span')).find(node => isArchive(node.textContent))
  || document.querySelector(arguments[0]);
if (!element) {
    return null;
}

// BeautifulSoup の get_text(strip=True) と同じように、
// 子要素を含むテキスト断片の前後空白を除いて連結します。
const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
const parts = [];
let node = walker.nextNode();
while (node) {
    const text = node.nodeValue.trim();
    if (text) {
        parts.push(text);
    }
    node = walker.nextNode();
}
return parts.join("");
"""
CHROME_BINARY_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)
PROCESS_START = time.perf_counter()


class DiscordDeliveryError(RuntimeError):
    """Raised when a Discord notification cannot be delivered safely."""

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def now_utc_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def now_jst_iso():
    return datetime.datetime.now(JST).isoformat()


def log_metric(event: str, **kwargs):
    # 公開リポジトリのActionsログと成果物は第三者から見えるため、
    # 配信者を特定できる値だけを公開実行時に除外します。
    if PUBLIC_LOGS:
        kwargs = {
            key: value
            for key, value in kwargs.items()
            if key not in PRIVATE_METRIC_KEYS
        }

    payload = {
        "event": event,
        "utc": now_utc_iso(),
        "jst": now_jst_iso(),
        "elapsed_from_process_start_sec": round(time.perf_counter() - PROCESS_START, 3),
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
        "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "github_event_name": os.getenv("GITHUB_EVENT_NAME"),
        "schedule_cron": os.getenv("SCHEDULE_CRON"),
    }
    payload.update(kwargs)

    metric_json = json.dumps(payload, ensure_ascii=False)
    logging.info("METRIC %s", metric_json)

    with open(METRICS_FILE, "a", encoding="utf-8") as fp:
        fp.write(metric_json + "\n")


def find_chrome_binary() -> Optional[str]:
    env_binary = os.getenv("CHROME_BINARY")
    if env_binary:
        return env_binary

    for binary in CHROME_BINARY_CANDIDATES:
        path = shutil.which(binary)
        if path:
            return path

    return None


def create_driver() -> webdriver.Chrome:
    driver_start = time.perf_counter()
    log_metric("driver_create_start")

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1280,2000")

    chrome_binary = find_chrome_binary()
    if chrome_binary:
        chrome_options.binary_location = chrome_binary

    webdriver_start = time.perf_counter()
    log_metric("webdriver_start", manager="selenium_manager", chrome_binary=chrome_binary)
    driver = webdriver.Chrome(options=chrome_options)
    log_metric(
        "webdriver_end",
        elapsed_sec=round(time.perf_counter() - webdriver_start, 3),
        manager="selenium_manager",
        chrome_binary=chrome_binary,
    )

    log_metric("driver_create_end", elapsed_sec=round(time.perf_counter() - driver_start, 3))
    return driver


def scroll_to_bottom(driver: webdriver.Chrome, user_id: str = None, user_name: str = None):
    last_height = driver.execute_script("return document.body.scrollHeight")
    for iteration in range(1, SCROLL_REPEAT + 1):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        sleep_sec = SCROLL_WAIT_SECONDS
        time.sleep(sleep_sec)
        new_height = driver.execute_script("return document.body.scrollHeight")
        height_changed = new_height != last_height
        log_metric(
            "scroll_iteration",
            user_id=user_id,
            user_name=user_name,
            iteration=iteration,
            old_height=last_height,
            new_height=new_height,
            height_changed=height_changed,
            sleep_sec=sleep_sec,
        )
        if not height_changed:
            break
        last_height = new_height


def extract_latest_marker(text: Optional[str]) -> Optional[str]:
    """Extract the archive duration marker from already-rendered text."""

    if text is None:
        return None

    normalized_text = str(text).strip().replace("\xa0", " ")
    match = LATEST_MARKER_PATTERN.search(normalized_text)
    return match.group(1) if match else None


def extract_latest_archive_date(
    text: Optional[str],
    reference_date: datetime.date,
) -> Optional[str]:
    """Infer the latest archive date from Mixch's rendered relative age.

    The latest archive card contains text such as ``3時間前 28:40`` or
    ``19 hours ago56:49`` depending on the runner locale. Reusing it avoids
    another page/API request for every watched user. Seconds, minutes and hours
    all belong to the reference date; the site switches to an integer day count
    after 24 hours.
    """

    if text is None:
        return None

    normalized_text = str(text).strip().replace("\xa0", " ")
    japanese_match = LATEST_ARCHIVE_AGE_PATTERN.search(normalized_text)
    if japanese_match is not None:
        amount = int(japanese_match.group(1))
        days_ago = amount if japanese_match.group(2) == "日" else 0
    else:
        english_match = LATEST_ARCHIVE_AGE_EN_PATTERN.search(normalized_text)
        if english_match is None:
            return None
        amount = int(english_match.group(1))
        days_ago = amount if english_match.group(2).lower().startswith("day") else 0

    return (reference_date - datetime.timedelta(days=days_ago)).isoformat()


def read_latest_marker_text_from_dom(driver: webdriver.Chrome) -> Optional[str]:
    """Read only the target DOM node instead of transferring the entire page."""

    text = driver.execute_script(LATEST_MARKER_TEXT_SCRIPT, LATEST_MARKER_SELECTOR)
    if text is None:
        return None
    return str(text).replace("\xa0", " ")


def wait_for_latest_marker(
    driver: webdriver.Chrome,
    timeout_seconds: float = PAGE_LOAD_WAIT,
) -> Dict[str, Any]:
    """Wait until a valid marker is readable, up to the former fixed wait.

    A normal archive page can finish immediately. If the marker never becomes
    readable, the caller keeps the existing scroll-and-parse fallback.
    """

    wait_start = time.perf_counter()
    deadline = wait_start + max(0.0, timeout_seconds)
    poll_count = 0
    last_text: Optional[str] = None
    last_error: Optional[Exception] = None

    while True:
        poll_count += 1
        try:
            text = read_latest_marker_text_from_dom(driver)
            if text is not None:
                last_text = text
            marker = extract_latest_marker(text)
            if marker is not None:
                return {
                    "marker": marker,
                    "text": text,
                    "poll_count": poll_count,
                    "elapsed_sec": round(time.perf_counter() - wait_start, 3),
                    "error": None,
                }
        except Exception as exc:
            # DOM取得だけが失敗しても、後段の従来方式（page_source解析）を残します。
            last_error = exc

        remaining_seconds = deadline - time.perf_counter()
        if remaining_seconds <= 0:
            break
        time.sleep(min(MARKER_POLL_INTERVAL, remaining_seconds))

    return {
        "marker": None,
        "text": last_text,
        "poll_count": poll_count,
        "elapsed_sec": round(time.perf_counter() - wait_start, 3),
        "error": last_error,
    }


def read_latest_marker_text_with_fallback(
    driver: webdriver.Chrome,
) -> tuple[Optional[str], str]:
    """Use the lightweight DOM query first, retaining the previous parser."""

    try:
        text = read_latest_marker_text_from_dom(driver)
        if text is not None:
            return text, "dom_query"
    except Exception as exc:
        log_metric(
            "dom_query_fallback",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    # No marker or a DOM-query failure: preserve the old full-HTML parser.
    soup = BeautifulSoup(driver.page_source, "lxml")
    # 表示用の自動生成クラスには依存せず、カードの意味から探します。
    for span in soup.find_all("span"):
        text = span.get_text(" ", strip=True).replace("\xa0", " ")
        if extract_latest_marker(text) and (
            LATEST_ARCHIVE_AGE_PATTERN.search(text)
            or LATEST_ARCHIVE_AGE_EN_PATTERN.search(text)
        ):
            return text, "semantic_html"
    span = soup.select_one(LATEST_MARKER_SELECTOR)
    if span is not None and extract_latest_marker(span.get_text(strip=True)):
        return span.get_text(strip=True).replace("\xa0", " "), "page_source_fallback"
    # 明示的な空表示だけを「なし」とします。ログイン画面や構造変更は失敗です。
    visible_text = soup.get_text(" ", strip=True)
    empty_messages = ("アーカイブなし", "アーカイブがありません", "アーカイブはありません",
                      "No archives", "No live archives", "Broadcast archive does not exist")
    if any(message in visible_text for message in empty_messages):
        return None, "confirmed_empty"
    raise ArchiveReadError("Archive marker and explicit empty state are both missing")


def get_latest_marker(
    driver: webdriver.Chrome,
    user_id: str,
    user_name: str = None,
    index: int = None,
    total: int = None,
    marker_wait_seconds: float = PAGE_LOAD_WAIT,
    reference_date: Optional[datetime.date] = None,
) -> tuple[str, Optional[str]]:
    if reference_date is None:
        reference_date = datetime.datetime.now(JST).date()

    url = f"https://mixch.tv/u/{user_id}/live_archives"

    page_get_start = time.perf_counter()
    log_metric("page_get_start", user_id=user_id, user_name=user_name, url=url, index=index, total=total)
    driver.get(url)
    log_metric(
        "page_get_end",
        user_id=user_id,
        user_name=user_name,
        elapsed_sec=round(time.perf_counter() - page_get_start, 3),
    )

    log_metric(
        "page_load_wait_start",
        user_id=user_id,
        user_name=user_name,
        wait_sec=marker_wait_seconds,
        max_wait_sec=marker_wait_seconds,
        wait_mode="until_marker_ready",
    )
    wait_result = wait_for_latest_marker(driver, timeout_seconds=marker_wait_seconds)
    marker_ready = wait_result["marker"] is not None
    log_metric(
        "page_load_wait_end",
        user_id=user_id,
        user_name=user_name,
        wait_sec=marker_wait_seconds,
        max_wait_sec=marker_wait_seconds,
        actual_wait_sec=wait_result["elapsed_sec"],
        poll_count=wait_result["poll_count"],
        marker_ready=marker_ready,
        wait_mode="until_marker_ready",
        dom_error_type=(
            type(wait_result["error"]).__name__
            if wait_result["error"] is not None
            else None
        ),
    )

    if marker_ready:
        latest_marker = wait_result["marker"]
        text = wait_result["text"]
        latest_archive_date = extract_latest_archive_date(text, reference_date)
        log_metric(
            "scroll_skipped",
            user_id=user_id,
            user_name=user_name,
            reason="latest_marker_already_ready",
        )
        parse_start = time.perf_counter()
        log_metric("parse_start", user_id=user_id, user_name=user_name)
        log_metric(
            "parse_end",
            user_id=user_id,
            user_name=user_name,
            elapsed_sec=round(time.perf_counter() - parse_start, 3),
            span_found=True,
            text=text,
            latest_marker=latest_marker,
            latest_archive_date=latest_archive_date,
            result="ok",
            extraction_method="dom_wait",
        )
        return latest_marker, latest_archive_date

    scroll_start = time.perf_counter()
    log_metric("scroll_start", user_id=user_id, user_name=user_name)
    scroll_to_bottom(driver, user_id=user_id, user_name=user_name)
    log_metric(
        "scroll_end",
        user_id=user_id,
        user_name=user_name,
        elapsed_sec=round(time.perf_counter() - scroll_start, 3),
    )

    parse_start = time.perf_counter()
    log_metric("parse_start", user_id=user_id, user_name=user_name)
    text, extraction_method = read_latest_marker_text_with_fallback(driver)
    if text is None:
        log_metric(
            "parse_end",
            user_id=user_id,
            user_name=user_name,
            elapsed_sec=round(time.perf_counter() - parse_start, 3),
            span_found=False,
            text=None,
            latest_marker="NO_VIDEO",
            result="no_video",
            extraction_method=extraction_method,
        )
        log_metric("marker_no_video", user_id=user_id, user_name=user_name, result="no_video")
        return "NO_VIDEO", None

    marker = extract_latest_marker(text)
    latest_archive_date = extract_latest_archive_date(text, reference_date)
    if marker is None:
        raise ArchiveReadError("Archive duration could not be parsed")
    latest_marker = marker
    result = "ok"
    log_metric(
        "parse_end",
        user_id=user_id,
        user_name=user_name,
        elapsed_sec=round(time.perf_counter() - parse_start, 3),
        span_found=True,
        text=text,
        latest_marker=latest_marker,
        latest_archive_date=latest_archive_date,
        result=result,
        extraction_method=extraction_method,
    )
    if marker is None:
        log_metric(
            "marker_parse_failed",
            user_id=user_id,
            user_name=user_name,
            text=text,
            result="parse_failed",
        )
    return latest_marker, latest_archive_date


def load_json(path: str, default):
    p = Path(path)
    if p.exists():
        with p.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    return default


def save_json(path: str, data):
    # 途中終了しても元のJSONが半分だけ書き換わらないよう、同じ場所の
    # 一時ファイルへ完成版を書き、最後に置き換えます。
    target = Path(path)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=f".{target.name}.", suffix=".tmp", delete=False) as fp:
            temporary_path = Path(fp.name)
            json.dump(data, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_archive_history(watched_ids=None):
    """欠落・破損した履歴を初回扱いにして全員へ通知することを防ぐ。"""
    for path in (STATE_FILE, ACTIVITY_STATE_FILE):
        if not Path(path).is_file():
            raise ArchiveReadError(f"Required archive history is missing: {Path(path).name}")
    try:
        state = load_json(STATE_FILE, {})
        activity = load_json(ACTIVITY_STATE_FILE, {})
    except (OSError, ValueError) as error:
        raise ArchiveReadError("Archive history could not be read safely") from error
    if not isinstance(state, dict) or not isinstance(activity, dict):
        raise ArchiveReadError("Archive history must contain JSON objects")
    # 解除済み対象の旧履歴には NO_PAGE 等の旧エラー値も残っている。
    # 履歴を消さず、今回比較する対象だけを厳密に検証する。
    for user_id, marker in state.items():
        if watched_ids is not None and user_id not in watched_ids:
            continue
        if not isinstance(marker, str) or (marker != "NO_VIDEO" and not re.fullmatch(r"\d+:[0-5]\d", marker)):
            raise ArchiveReadError("Invalid saved archive duration")
    if not state and activity:
        raise ArchiveReadError("Saved archive durations are empty while activity history exists")
    for user_id, record in activity.items():
        if watched_ids is not None and user_id not in watched_ids:
            continue
        if not isinstance(record, dict):
            raise ArchiveReadError("Invalid saved archive activity")
        for key in ("last_notified_date", "latest_archive_date"):
            value = record.get(key)
            if value is not None:
                try:
                    datetime.date.fromisoformat(value)
                except (ValueError, TypeError):
                    raise ArchiveReadError("Invalid saved archive activity date") from None
        archive_id = record.get("latest_archive_id")
        if archive_id is not None and (not isinstance(archive_id, str) or not archive_id.isascii()
                                       or not archive_id.isdigit() or int(archive_id) <= 0):
            raise ArchiveReadError("Invalid saved archive identity")
        if archive_id is not None and (type(record.get("latest_archive_created")) is not int
                                       or record["latest_archive_created"] <= 0):
            raise ArchiveReadError("Saved archive identity has no valid creation time")
        seen_ids = record.get("seen_archive_ids", [])
        if not isinstance(seen_ids, list) or any(not isinstance(value, str) or not value.isascii()
                                                or not value.isdigit() or int(value) <= 0 for value in seen_ids):
            raise ArchiveReadError("Invalid saved archive identity history")
        if record.get("archive_identity_version") not in (None, 1):
            raise ArchiveReadError("Unsupported saved archive identity format")
        if archive_id is None and record.get("archive_identity_version") == 1 and state.get(user_id) not in (None, "NO_VIDEO"):
            raise ArchiveReadError("Saved archive identity is incomplete")
    return state, activity


def persist_archive_history(state, activity):
    # 動画番号と通知済み番号が判定の正本。そちらを先に確定し、再生時間の
    # 互換用ファイルの書き込みが失敗しても、同じ動画を再通知しない。
    save_json(ACTIVITY_STATE_FILE, activity)
    save_json(STATE_FILE, state)
    log_metric("archive_history_saved", state_count=len(state), activity_count=len(activity))


def group_update_reports(reports):
    """送信上限ごとに配信者をまとめ、成功した組だけ通知済みにできるようにする。"""
    groups, group, length = [], [], 0
    for report in reports:
        line_length = len(build_update_lines([report])[0])
        if line_length > DESCRIPTION_LIMIT:
            raise DiscordDeliveryError("Archive notification entry is too long")
        if group and length + 2 + line_length > DESCRIPTION_LIMIT:
            groups.append(group)
            group, length = [], 0
        length += line_length + (2 if group else 0)
        group.append(report)
    if group:
        groups.append(group)
    return groups


def get_watchlist_entry_line_numbers(path: str, expected_count: int) -> List[Optional[int]]:
    p = Path(path)
    if not p.exists():
        return [None] * expected_count

    line_numbers: List[int] = []
    depth = 0
    in_string = False
    escape = False

    with p.open("r", encoding="utf-8") as fp:
        for line_no, line in enumerate(fp, start=1):
            for ch in line:
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                    continue

                if ch == '"':
                    in_string = True
                elif ch in "[{":
                    depth += 1
                    if ch == "{" and depth == 2:
                        line_numbers.append(line_no)
                elif ch in "]}":
                    depth -= 1

    if len(line_numbers) != expected_count:
        logging.warning(
            f"watchlist.jsonの行番号取得数が監視対象数と一致しません: "
            f"行番号={len(line_numbers)} 監視対象={expected_count}"
        )

    return line_numbers + [None] * max(0, expected_count - len(line_numbers))


def load_watchlist(path: str) -> List[Dict[str, Any]]:
    watchlist = load_json(path, [])
    line_numbers = get_watchlist_entry_line_numbers(path, len(watchlist))
    for user, line_no in zip(watchlist, line_numbers):
        if line_no is not None:
            user["watchlist_line"] = line_no
    return watchlist


def get_today_jst() -> str:
    return datetime.datetime.now(JST).date().isoformat()


def is_last_daily_inactive_notification_run() -> bool:
    """Return True only for the final scheduled run of the JST day.

    GitHub Actions exposes the cron expression that triggered the workflow in
    github.event.schedule. The final run requested for a day is JST 21:07,
    which is UTC 12:07. Manual workflow_dispatch runs do not have this value,
    so they intentionally skip the recurring inactive notification.
    """

    return os.getenv("SCHEDULE_CRON") == LAST_DAILY_INACTIVE_NOTIFICATION_CRON


def dedupe_watchlist(watchlist: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped = []
    for user in watchlist:
        user_id = str(user["id"])
        user_name = user["name"]
        if user_id in seen:
            if PUBLIC_LOGS:
                logging.warning("重複した監視対象を1件スキップしました")
            else:
                logging.warning(
                    "重複したuser_idをスキップしました: %s %s",
                    user_id,
                    user_name,
                )
            continue
        seen.add(user_id)
        deduped.append(
            {
                **user,
                "id": user_id,
                "name": user_name,
                "watchlist_line": user.get("watchlist_line"),
            }
        )
    return deduped


def split_lines_by_limit(lines: List[str], limit: int = DESCRIPTION_LIMIT) -> List[str]:
    chunks: List[str] = []
    current = ""
    for line in lines:
        if not current:
            current = line
            continue

        candidate = f"{current}\n\n{line}"
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = line

    if current:
        chunks.append(current)
    return chunks


def get_discord_webhook_url() -> str:
    """Return the configured webhook or fail without exposing its value."""

    url = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
    if not url:
        raise DiscordDeliveryError("DISCORD_WEBHOOK_URL が設定されていません")
    return url


def send_embeds_to_discord(title: str, lines: List[str], empty_description: str = None) -> List[float]:
    url = get_discord_webhook_url()
    descriptions = split_lines_by_limit(lines) if lines else [empty_description or ""]
    total = len(descriptions)
    send_elapsed_points: List[float] = []

    for idx, description in enumerate(descriptions, start=1):
        embed_title = title
        if total > 1:
            embed_title = f"{title} {idx}/{total}"

        log_metric(
            "discord_send_start",
            title=embed_title,
            description_length=len(description),
            chunk_index=idx,
            chunk_total=total,
            has_webhook_url=bool(url),
        )

        payload = {
            "embeds": [
                {
                    "title": embed_title,
                    "description": description,
                }
            ]
        }
        send_start = time.perf_counter()
        try:
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                data=json.dumps(payload),
                timeout=10,
            )
            elapsed_sec = round(time.perf_counter() - send_start, 3)
            log_metric(
                "discord_send_end",
                title=embed_title,
                elapsed_sec=elapsed_sec,
                status_code=resp.status_code,
                ok=resp.ok,
                response_text_head=resp.text[:200],
            )
            if resp.ok:
                send_elapsed_points.append(round(time.perf_counter() - PROCESS_START, 3))
                logging.info(f"Discord送信成功: {embed_title}")
            else:
                logging.error(f"Discord送信失敗: {resp.status_code} {resp.text}")
                raise DiscordDeliveryError(
                    f"Discord通知がHTTP {resp.status_code}で拒否されました"
                )
        except requests.RequestException as exc:
            elapsed_sec = round(time.perf_counter() - send_start, 3)
            log_metric(
                "discord_send_error",
                title=embed_title,
                elapsed_sec=elapsed_sec,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            logging.error(
                "Discord送信例外: %s error_type=%s",
                embed_title,
                type(exc).__name__,
            )
            # requestsの例外文にはWebhook URLが含まれる場合があります。
            # 公開Actionsログへ秘密値を出さず、ワークフローだけを失敗させます。
            raise DiscordDeliveryError(
                f"Discord通知の通信に失敗しました ({type(exc).__name__})"
            ) from None

    return send_elapsed_points


def build_update_lines(reports: List[Dict[str, Any]]) -> List[str]:
    return [f"・{r['name']}\n<{r['url']}>" for r in reports]


def build_inactive_lines(inactive_reports: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for r in inactive_reports:
        line = (
            f"・{r['name']}（{r['basis_label']}: {r['basis_date']} / "
            f"{r['days_since']}日経過 / watchlist.json:{r['watchlist_line']}行目 / "
            "監視対象から自動解除）"
            f"\n<{r['url']}>"
        )
        lines.append(line)
    return lines


def save_watchlist(path: str, watchlist: List[Dict[str, Any]]):
    # 監視オン・オフ等の設定は残し、読み込み時の補助情報だけを除去します。
    persisted_watchlist = [
        {
            key: value for key, value in user.items() if key != "watchlist_line"
        }
        for user in watchlist
    ]
    save_json(path, persisted_watchlist)


def remove_inactive_users_from_watchlist(
    watchlist: List[Dict[str, Any]],
    inactive_reports: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    inactive_ids = {str(report["id"]) for report in inactive_reports}
    return [user for user in watchlist if str(user["id"]) not in inactive_ids]


def monitoring_enabled():
    # 対象一覧とは独立した全体スイッチ。未導入時だけ従来どおりオン。
    # 壊れた設定を勝手にオン扱いせず、エラーで停止させます。
    path = Path(WATCHLIST_FILE).parent / "monitor_settings.json"
    if not path.exists():
        return True
    settings = json.loads(path.read_text(encoding="utf-8"))
    if type(settings.get("enabled")) is not bool:
        raise ValueError("monitor_settings.json: enabled must be boolean")
    return settings["enabled"]


def main():
    # ブラウザ起動・通知先検証・状態変更より前に全体の停止を確認します。
    if not monitoring_enabled():
        log_metric("monitoring_paused", scope="all")
        logging.info("全体のアーカイブ監視は停止中です。対象一覧と履歴は変更しません。")
        return

    log_metric(
        "python_start",
        watchlist_file=WATCHLIST_FILE,
        state_file=STATE_FILE,
        activity_state_file=ACTIVITY_STATE_FILE,
    )

    # 通知先がない状態で監視済みデータだけを進めると、次回以降も通知を
    # 再試行できません。ページ取得より前に設定不備を検出して終了します。
    get_discord_webhook_url()

    watchlist = load_watchlist(WATCHLIST_FILE)
    original_watchlist_count = len(watchlist)
    log_metric("watchlist_loaded", watchlist_count=original_watchlist_count)

    state, activity_state = load_archive_history({str(user["id"]) for user in watchlist})
    original_state, original_activity = dict(state), dict(activity_state)

    logging.info(f"watchlist総数: {len(watchlist)}")
    watchlist = dedupe_watchlist(watchlist)
    deduped_watchlist_count = len(watchlist)
    duplicate_removed_count = original_watchlist_count - deduped_watchlist_count
    log_metric(
        "watchlist_deduped",
        deduped_watchlist_count=deduped_watchlist_count,
        duplicate_removed_count=duplicate_removed_count,
    )
    logging.info(f"重複除去後の監視対象数: {len(watchlist)}")

    # 旧画面の個別オフ設定は無視し、全体スイッチだけで制御します。
    active_watchlist = watchlist
    log_metric("archive_monitor_selection",
               enabled_count=len(active_watchlist),
               disabled_count=len(watchlist) - len(active_watchlist))

    today_jst = get_today_jst()
    today_dt = datetime.date.fromisoformat(today_jst)
    report_summary = []
    failed_count = 0
    no_video_count = 0
    slowest_user_id = None
    slowest_user_name = None
    max_user_elapsed_sec = 0.0
    discord_send_elapsed_points: List[float] = []
    notified_archive_count = 0
    successful_ids = set()
    pending_decisions = {}
    empty_previous_video_ids = set()
    known_video_count = sum(state.get(str(user["id"]), "NO_VIDEO") != "NO_VIDEO"
                            for user in active_watchlist)
    checked_at = datetime.datetime.now(datetime.timezone.utc)
    # 外部サイトが遅い時も、30分の強制終了前に正常結果の保存まで終える。
    read_deadline = time.monotonic() + 24 * 60

    client = ArchiveClient(browser_factory=create_driver, metric=log_metric)
    try:
        total = len(active_watchlist)
        for index, user in enumerate(active_watchlist, start=1):
            if time.monotonic() >= read_deadline:
                deferred_count = total - index + 1
                failed_count += deferred_count
                log_metric("archive_read_budget_exhausted", deferred_count=deferred_count)
                break
            user_start = time.perf_counter()
            user_id = str(user["id"])
            user_name = user["name"]
            url = f"https://mixch.tv/u/{user_id}/live_archives"
            prev_marker = state.get(user_id)
            latest_marker = prev_marker
            latest_archive_date = None
            state_updated = False
            changed = False

            log_metric(
                "user_start",
                index=index,
                total=total,
                user_id=user_id,
                user_name=user_name,
                url=url,
            )

            try:
                snapshot = client.fetch_latest(user_id)
                decision = plan_archive_update(
                    prev_marker, activity_state.get(user_id, {}), snapshot,
                    checked_at=checked_at,
                )
                latest_marker = snapshot.marker
                latest_archive_date = snapshot.archive_date
                changed = decision.changed
                result = "no_video" if snapshot.archive_id is None else "ok"
                successful_ids.add(user_id)
                if snapshot.archive_id is None:
                    no_video_count += 1
                    if prev_marker not in (None, "NO_VIDEO"):
                        empty_previous_video_ids.add(user_id)

                log_metric(
                    "user_marker_result",
                    index=index,
                    total=total,
                    user_id=user_id,
                    user_name=user_name,
                    prev_marker=prev_marker,
                    latest_marker=latest_marker,
                    latest_archive_date=latest_archive_date,
                    changed=changed,
                    result=result,
                    decision_reason=decision.reason,
                    archive_id=snapshot.archive_id,
                    extraction_method=snapshot.source,
                )

                if changed:
                    report_summary.append(
                        {
                            "id": user_id,
                            "name": user_name,
                            "url": url,
                        }
                    )
                    # 通知に失敗した人の比較基準を先へ進めない。
                    # 送信が成功した組だけ、後でこの変更案を確定する。
                    pending_decisions[user_id] = decision
                else:
                    state[user_id] = decision.marker
                    activity_state[user_id] = decision.activity
                    state_updated = True
            except Exception as exc:
                failed_count += 1
                log_metric(
                    "user_error",
                    user_id=user_id,
                    user_name=user_name,
                    index=index,
                    total=total,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    elapsed_sec=round(time.perf_counter() - user_start, 3),
                )
                if PUBLIC_LOGS:
                    logging.error(
                        "監視対象の処理中に例外が発生しました: "
                        "index=%s error_type=%s",
                        index,
                        type(exc).__name__,
                    )
                else:
                    logging.exception(
                        "ユーザー処理中に例外が発生しました: %s %s",
                        user_id,
                        user_name,
                    )
            finally:
                user_elapsed_sec = round(time.perf_counter() - user_start, 3)
                if user_elapsed_sec > max_user_elapsed_sec:
                    max_user_elapsed_sec = user_elapsed_sec
                    slowest_user_id = user_id
                    slowest_user_name = user_name

                log_metric(
                    "user_end",
                    index=index,
                    total=total,
                    user_id=user_id,
                    user_name=user_name,
                    elapsed_sec=user_elapsed_sec,
                    prev_marker=prev_marker,
                    latest_marker=latest_marker,
                    latest_archive_date=latest_archive_date,
                    changed=changed,
                    state_updated=state_updated,
                )

    finally:
        client.close()

    # サービス側が多数の一覧を空にした時も、動画履歴の初期化や自動解除を
    # しない。正常に読めた別の人の新着は、この後そのまま処理する。
    if len(empty_previous_video_ids) >= 5 and len(empty_previous_video_ids) >= known_video_count * 0.8:
        for user_id in empty_previous_video_ids:
            if user_id in original_activity:
                activity_state[user_id] = original_activity[user_id]
            else:
                activity_state.pop(user_id, None)
            state[user_id] = original_state[user_id]
        successful_ids -= empty_previous_video_ids
        failed_count += len(empty_previous_video_ids)
        log_metric("archive_widespread_empty", affected_count=len(empty_previous_video_ids),
                   known_video_count=known_video_count, state_preserved=True)

    # 全件失敗なら書き込みも通知も行わない。一部失敗なら、その人の履歴を
    # 残し、成功した人の結果だけ保存する。1人の失敗で全員分を止めない。
    if active_watchlist and not successful_ids:
        log_metric("archive_read_failed", failed_count=failed_count,
                   watchlist_count=len(active_watchlist), state_preserved=True)
        raise ArchiveReadError(f"Archive read failed for {failed_count} targets; state preserved")

    logging.info(f"新着通知対象の人数: {len(report_summary)}")
    delivery_error = None
    # 通知不要の正常結果を先に確定する。新着の変更案はまだ含まれていない。
    persist_archive_history(state, activity_state)
    if report_summary:
        groups = group_update_reports(report_summary)
        for index, group in enumerate(groups, 1):
            title = "🆕 Mixcha 更新通知"
            if len(groups) > 1:
                title += f" {index}/{len(groups)}"
            try:
                discord_send_elapsed_points.extend(send_embeds_to_discord(title, build_update_lines(group)))
            except DiscordDeliveryError as error:
                delivery_error = error
                log_metric("archive_notification_pending", pending_count=sum(len(g) for g in groups[index - 1:]))
                break
            for report in group:
                decision = pending_decisions[report["id"]]
                state[report["id"]] = decision.marker
                activity_state[report["id"]] = decision.activity
            notified_archive_count += len(group)
            log_metric("archive_update_notification_delivered", recipient_count=len(group),
                       chunk_index=index, chunk_total=len(groups))
            # 後続の組が失敗しても、送れた組を次回また送らない。
            persist_archive_history(state, activity_state)
    else:
        logging.info("変化がないため、Mixcha 更新通知のDiscord送信をスキップします")
        log_metric("discord_update_notification_skipped", reason="no_changes")

    should_send_inactive_notification = is_last_daily_inactive_notification_run()
    log_metric(
        "inactive_notification_schedule_check",
        should_send=should_send_inactive_notification,
        schedule_cron=os.getenv("SCHEDULE_CRON"),
        last_daily_cron=LAST_DAILY_INACTIVE_NOTIFICATION_CRON,
    )

    inactive_reports = []
    for user in active_watchlist:
        user_id = str(user["id"])
        if user_id not in successful_ids or delivery_error is not None:
            continue
        user_name = user["name"]
        watchlist_line = user.get("watchlist_line", "不明")
        last_notified_date = activity_state[user_id]["last_notified_date"]
        latest_archive_date = activity_state[user_id].get("latest_archive_date")

        if latest_archive_date:
            basis_label = "最新アーカイブ"
            basis_date = latest_archive_date
        else:
            # 公開アーカイブが1件もない場合は参照できる日付がないため、
            # 従来どおり監視開始日（または最終通知日）を安全な代替基準にします。
            basis_label = "公開アーカイブなし・基準日"
            basis_date = last_notified_date

        basis_dt = datetime.date.fromisoformat(basis_date)
        days_since = max(0, (today_dt - basis_dt).days)

        if days_since >= INACTIVE_DAYS_THRESHOLD:
            inactive_reports.append(
                {
                    "id": user_id,
                    "name": user_name,
                    "url": f"https://mixch.tv/u/{user_id}/live_archives",
                    "basis_label": basis_label,
                    "basis_date": basis_date,
                    "days_since": days_since,
                    "watchlist_line": watchlist_line,
                }
            )

    logging.info(f"最新アーカイブ等の基準日から20日以上経過した人数: {len(inactive_reports)}")
    if inactive_reports and should_send_inactive_notification:
        inactive_lines = build_inactive_lines(inactive_reports)
        discord_send_elapsed_points.extend(send_embeds_to_discord("⚠️ 20日以上アーカイブ更新なし", inactive_lines))
        inactive_removed_count = len(inactive_reports)
        watchlist = remove_inactive_users_from_watchlist(watchlist, inactive_reports)
        watchlist_save_start = time.perf_counter()
        log_metric(
            "save_watchlist_start",
            path=WATCHLIST_FILE,
            inactive_removed_count=inactive_removed_count,
            remaining_watchlist_count=len(watchlist),
        )
        save_watchlist(WATCHLIST_FILE, watchlist)
        log_metric(
            "save_watchlist_end",
            elapsed_sec=round(time.perf_counter() - watchlist_save_start, 3),
            path=WATCHLIST_FILE,
            inactive_removed_count=inactive_removed_count,
            remaining_watchlist_count=len(watchlist),
        )
        logging.info(f"20日以上アーカイブ更新なしの配信者を監視対象から解除しました: {inactive_removed_count}人")
    elif inactive_reports:
        logging.info("その日の最終起動ではないため、20日以上アーカイブ更新なしのDiscord送信をスキップします")
        log_metric(
            "discord_inactive_notification_skipped",
            reason="not_last_daily_run",
            inactive_count=len(inactive_reports),
        )

    log_metric(
        "python_end",
        total_elapsed_sec=round(time.perf_counter() - PROCESS_START, 3),
        watchlist_count=original_watchlist_count,
        deduped_watchlist_count=deduped_watchlist_count,
        changed_count=len(report_summary),
        notified_archive_count=notified_archive_count,
        failed_count=failed_count,
        no_video_count=no_video_count,
        discord_send_count=len(discord_send_elapsed_points),
        first_discord_send_elapsed_sec=(discord_send_elapsed_points[0] if discord_send_elapsed_points else None),
        last_discord_send_elapsed_sec=(discord_send_elapsed_points[-1] if discord_send_elapsed_points else None),
        slowest_user_id=slowest_user_id,
        slowest_user_name=slowest_user_name,
        max_user_elapsed_sec=max_user_elapsed_sec,
    )
    if delivery_error is not None:
        raise delivery_error
    if failed_count:
        raise ArchiveReadError(f"Archive read failed for {failed_count} targets; healthy results saved")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log_metric(
            "python_fatal_error",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        logging.exception("watcher全体が例外で終了しました")
        raise
