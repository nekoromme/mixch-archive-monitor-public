"""管理画面と通知で共通の、配信者IDによるブロックリスト。

毎回ファイルを読み直すので、画面で削除した人は次の監視から除外される。
欠落・破損時は空リストへ戻さず停止し、ブロック対象への誤通知を防ぐ。
"""
import json
import re
from pathlib import Path

BLOCKLIST_FILE = Path(__file__).resolve().parents[2] / "ranking-blocklist.json"


def read_blocked_user_ids(path: Path | None = None) -> frozenset[str]:
    try:
        data = json.loads((path or BLOCKLIST_FILE).read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or type(data.get("version")) is not int
                or data["version"] != 1 or not isinstance(data.get("blocked"), dict)
                or any(not re.fullmatch(r"[0-9]{1,30}", user_id) or value is not True
                       for user_id, value in data["blocked"].items())):
            raise ValueError("invalid schema")
        return frozenset(data["blocked"])
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("共通ブロックリストを読み取れません。通知を停止します。") from exc
