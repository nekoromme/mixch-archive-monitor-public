"""ミクチャの最新アーカイブを、表示時間ではなく動画番号で識別する。

一覧ページ自身が使う読み取り専用の通信先から、先頭の1件だけを取得する。
「もっと見る」は押さない。空の正常応答と通信・解析の失敗を区別し、
失敗した読み取りから監視履歴を作り直さない。
"""

import datetime
import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests

JST = datetime.timezone(datetime.timedelta(hours=9))
API_BASE = "https://mixch.tv/api-web/users"
REQUEST_TIMEOUT = (20, 20)
READ_ATTEMPTS = 3
SEEN_ID_LIMIT = 64
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://mixch.tv/",
    "Cache-Control": "no-cache",
}


class ArchiveReadError(RuntimeError):
    """動画なしと混同してはいけない、取得または解析の失敗。"""


@dataclass(frozen=True)
class ArchiveSnapshot:
    # archive_id が None なのは、正常な一覧応答が空だった場合だけ。
    archive_id: Optional[str] = None
    created: Optional[int] = None
    duration: Optional[int] = None
    visibility: Optional[int] = None
    source: str = "api"

    @property
    def marker(self):
        if self.archive_id is None:
            return "NO_VIDEO"
        return f"{self.duration // 60}:{self.duration % 60:02d}"

    @property
    def archive_date(self):
        if self.created is None:
            return None
        # 「19時間前」を今日扱いする従来の推定は、日付をまたぐとずれる。
        # 元データの秒単位の時刻を日本時間に直してから日付を取り出す。
        return datetime.datetime.fromtimestamp(self.created, JST).date().isoformat()


def _positive_id(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ArchiveReadError(f"Invalid {label}")
    value = str(value)
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        raise ArchiveReadError(f"Invalid {label}")
    return str(int(value))


def parse_archive_payload(payload, user_id, *, source="api", now=None):
    """実際に観測した archives / has_next 形式を厳密に検証する。"""
    user_id = _positive_id(user_id, "user_id")
    if not isinstance(payload, dict) or not isinstance(payload.get("archives"), list):
        raise ArchiveReadError("Archive response is not an archive list")
    if type(payload.get("has_next")) is not bool:
        raise ArchiveReadError("Archive response has no valid has_next flag")
    archives = payload["archives"]
    if not archives:
        if payload["has_next"]:
            raise ArchiveReadError("Empty archive response claims another page")
        return ArchiveSnapshot(source=source)

    current_timestamp = (now or datetime.datetime.now(datetime.timezone.utc)).timestamp()
    snapshots = []
    for archive in archives:
        if not isinstance(archive, dict):
            raise ArchiveReadError("Invalid archive record")
        if _positive_id(archive.get("user_id"), "owner") != user_id:
            # 別の人の応答や前の画面のデータを、今の人の動画として保存しない。
            raise ArchiveReadError("Archive response belongs to another user")
        archive_id = _positive_id(archive.get("live_session_id"), "live_session_id")
        created, duration = archive.get("created"), archive.get("duration")
        if type(created) is not int or created <= 0 or created > current_timestamp + 300:
            raise ArchiveReadError("Invalid archive creation time")
        if type(duration) is not int or duration < 0:
            raise ArchiveReadError("Invalid archive duration")
        visibility = archive.get("visibility")
        if type(visibility) is not int or visibility not in (0, 1, 2, 3):
            raise ArchiveReadError("Invalid archive visibility")
        snapshots.append(ArchiveSnapshot(archive_id, created, duration, visibility, source))

    # 要求は1件だけだが、配信元が複数返した場合も並び順の変化を新着にしない。
    return max(snapshots, key=lambda item: (item.created, int(item.archive_id)))


BROWSER_API_SCRIPT = """
// 通常の通信が失敗した時だけ、同じ公式の読み取りをブラウザーから試す。
// ログインや動画の再生、アーカイブの公開範囲の変更は行わない。
const url = arguments[0];
const done = arguments[arguments.length - 1];
const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), 15000);
fetch(url, {credentials: 'omit', cache: 'no-store', signal: controller.signal})
  .then(async response => ({status: response.status, payload: await response.json()}))
  .then(done)
  .catch(error => done({error: error.name}))
  .finally(() => clearTimeout(timer));
"""


class ArchiveClient:
    """通信を再利用し、一時失敗を再試行してからブラウザーへ切り替える。"""
    def __init__(self, browser_factory=None, metric: Optional[Callable] = None, session=None):
        self.session = session if session is not None else requests.Session()
        self.session.headers.update(REQUEST_HEADERS)
        self.browser_factory = browser_factory
        self.browser = None
        self.metric = metric or (lambda *args, **kwargs: None)

    def fetch_latest(self, user_id):
        user_id = _positive_id(user_id, "user_id")
        url = f"{API_BASE}/{user_id}/live_archives"
        for attempt in range(1, READ_ATTEMPTS + 1):
            try:
                response = self.session.get(url, params={"limit": 1}, timeout=REQUEST_TIMEOUT)
                self.metric("archive_api_response", user_id=user_id, attempt=attempt,
                            status_code=response.status_code)
                if response.status_code != 200:
                    raise ArchiveReadError(f"Archive API returned HTTP {response.status_code}")
                snapshot = parse_archive_payload(response.json(), user_id)
                self.metric("archive_source_success", user_id=user_id, source="api",
                            result="empty" if snapshot.archive_id is None else "ok")
                return snapshot
            except (requests.RequestException, ValueError, ArchiveReadError) as error:
                # 応答本文や例外全文はログに出さず、失敗段階と理由の種類を残す。
                self.metric("archive_api_retry", user_id=user_id, attempt=attempt,
                            error_type=type(error).__name__)
                if attempt < READ_ATTEMPTS:
                    time.sleep(0.5 * attempt)

        if self.browser_factory is None:
            raise ArchiveReadError("Archive API failed after retries")
        try:
            if self.browser is None:
                self.browser = self.browser_factory()
                self.browser.set_page_load_timeout(30)
                self.browser.set_script_timeout(25)
            self.browser.get(f"https://mixch.tv/u/{user_id}/live_archives")
            result = self.browser.execute_async_script(BROWSER_API_SCRIPT, f"{url}?limit=1")
            if not isinstance(result, dict) or result.get("status") != 200:
                raise ArchiveReadError("Browser archive request failed")
            snapshot = parse_archive_payload(result.get("payload"), user_id, source="browser_api")
            self.metric("archive_source_success", user_id=user_id, source="browser_api",
                        result="empty" if snapshot.archive_id is None else "ok")
            return snapshot
        except Exception as error:
            self.metric("archive_browser_failed", user_id=user_id, error_type=type(error).__name__)
            raise ArchiveReadError("Both archive read routes failed; history preserved") from None

    def close(self):
        self.session.close()
        if self.browser is not None:
            try:
                self.browser.quit()
            except Exception as error:
                self.metric("archive_browser_cleanup_failed", error_type=type(error).__name__)


@dataclass(frozen=True)
class ArchiveDecision:
    marker: str
    activity: dict
    changed: bool
    reason: str


def plan_archive_update(previous_marker, previous_activity, snapshot, *, checked_at):
    """履歴の変更案を作る。通知が必要な案は、送信成功後にだけ確定する。"""
    activity = dict(previous_activity)
    today = checked_at.astimezone(JST).date().isoformat()
    activity.setdefault("last_notified_date", today)
    activity["archive_last_checked_at"] = checked_at.isoformat()
    activity["archive_read_status"] = "empty" if snapshot.archive_id is None else "ok"
    previous_id = activity.get("latest_archive_id")
    initialized = activity.get("archive_identity_version") == 1

    if snapshot.archive_id is None:
        if previous_id is not None or previous_marker in (None, "NO_VIDEO"):
            activity["archive_identity_version"] = 1
        # 空になっても最後に確認できた動画番号・日付・再生時間は消さない。
        # 次回その同じ動画が戻ってきても、新着通知にはならない。
        return ArchiveDecision(previous_marker or "NO_VIDEO", activity, False, "confirmed_empty")

    seen_ids = list(activity.get("seen_archive_ids", []))
    previous_created = activity.get("latest_archive_created")
    if previous_id == snapshot.archive_id:
        changed, reason = False, "same_archive_id"
    elif snapshot.archive_id in seen_ids:
        changed, reason = False, "already_seen_archive"
    elif previous_id is not None and previous_created is not None and (
        snapshot.created < previous_created
        or (snapshot.created == previous_created and int(snapshot.archive_id) < int(previous_id))
    ):
        changed, reason = False, "older_archive_after_removal"
    elif previous_id is None and not initialized and previous_marker is not None:
        # 再生時間しか保存していない旧履歴からの移行で、一斉再通知しない。
        # 初回は動画番号を記録し、次に別の番号が現れた時から新着を判定する。
        changed, reason = False, "legacy_identity_baseline"
    else:
        changed, reason = True, "new_archive_id"

    if reason in ("already_seen_archive", "older_archive_after_removal"):
        # 最新動画の削除で古い動画が先頭へ戻っても、比較基準を巻き戻さない。
        return ArchiveDecision(previous_marker or snapshot.marker, activity, False, reason)

    activity.update({
        "archive_identity_version": 1,
        "latest_archive_id": snapshot.archive_id,
        "latest_archive_created": snapshot.created,
        "latest_archive_date": snapshot.archive_date,
        "latest_archive_visibility": snapshot.visibility,
        "seen_archive_ids": list(dict.fromkeys([snapshot.archive_id, *seen_ids]))[:SEEN_ID_LIMIT],
    })
    if changed:
        activity["last_notified_date"] = today
        activity["last_notified_archive_id"] = snapshot.archive_id
    return ArchiveDecision(snapshot.marker, activity, changed, reason)
