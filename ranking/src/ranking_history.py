"""日本時間の日付ごとに、各配信者が到達した1〜3位を長期保存します。

通知の回数とは別の記録です。同じ日に何度取得しても日数は増えず、
1位・2位・3位を自由に組み合わせても同じ日を重複して数えません。
各順位にいた時の最大勢いも残すので、後から「150以上」などで数え直せます。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

JST = timezone(timedelta(hours=9))
VERSION = 1
# 管理画面でも正確に扱える整数の上限です。普段の勢いはこれより十分小さい値です。
MAX_MOMENTUM = 9007199254740991


def new_history() -> dict[str, Any]:
    return {"version": VERSION, "time_zone": "Asia/Tokyo", "first_observed_at": None,
            "last_observed_at": None, "profiles": {}, "days": {}}


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("順位記録の日時が不正です")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("順位記録の日時に時差がありません")
    return result


def validate_history(history: Any) -> None:
    """壊れた記録を空データで上書きしないよう、読み込み時に検証します。"""
    if (not isinstance(history, dict) or history.get("version") != VERSION
            or history.get("time_zone") != "Asia/Tokyo"
            or not isinstance(history.get("profiles"), dict)
            or not isinstance(history.get("days"), dict)):
        raise ValueError("日別順位記録の形式が不正です")
    for key in ("first_observed_at", "last_observed_at"):
        if history.get(key) is not None:
            _timestamp(history[key])
    for user_id, profile in history["profiles"].items():
        if (not isinstance(user_id, str) or not re.fullmatch(r"[0-9]+", user_id)
                or not isinstance(profile, dict) or not isinstance(profile.get("name"), str)):
            raise ValueError("日別順位記録の配信者情報が不正です")
        _timestamp(profile.get("observed_at"))
    for day, record in history["days"].items():
        if not isinstance(day, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
            raise ValueError("日別順位記録の日付が不正です")
        date.fromisoformat(day)
        if (not isinstance(record, dict) or not isinstance(record.get("users"), dict)
                or type(record.get("observations")) is not int or record["observations"] < 1):
            raise ValueError("日別順位記録の観測情報が不正です")
        _timestamp(record.get("last_observed_at"))
        for user_id, mask in record["users"].items():
            if user_id not in history["profiles"] or type(mask) is not int or not 1 <= mask <= 7:
                raise ValueError("日別順位記録の順位が不正です")
        # 旧データに勢いの項目がなくても、そのまま順位の日数を表示できます。
        if "momentum_complete" in record and type(record["momentum_complete"]) is not bool:
            raise ValueError("勢い記録の観測情報が不正です")
        momentum = record.get("momentum", {})
        if not isinstance(momentum, dict):
            raise ValueError("日別順位記録の勢いが不正です")
        for user_id, by_rank in momentum.items():
            if user_id not in record["users"] or not isinstance(by_rank, dict):
                raise ValueError("日別順位記録の勢いが不正です")
            for rank, value in by_rank.items():
                if (rank not in ("1", "2", "3") or type(value) is not int
                        or not 0 <= value <= MAX_MOMENTUM
                        or not record["users"][user_id] & (1 << (int(rank) - 1))):
                    raise ValueError("日別順位記録の順位別勢いが不正です")


def load_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return new_history()
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
        validate_history(history)
        return history
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("日別順位記録を読み取れません。既存の記録は初期化しません。") from exc


def observe(history: dict[str, Any], streams: Iterable[Any], observed_at: datetime) -> int:
    if observed_at.tzinfo is None:
        raise ValueError("観測日時に時差がありません")
    streams = list(streams)
    if streams and not any(type(stream.rank) is int and stream.rank in (1, 2, 3) for stream in streams):
        raise ValueError("上位1〜3位の順位を読み取れません。日別記録は更新しません。")
    if any(type(stream.momentum) is not int or not 0 <= stream.momentum <= MAX_MOMENTUM
           for stream in streams if type(stream.rank) is int and stream.rank in (1, 2, 3)):
        raise ValueError("上位1〜3位の勢いを読み取れません。日別記録は更新しません。")
    timestamp = observed_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    day = observed_at.astimezone(JST).date().isoformat()
    record = history["days"].setdefault(day, {"observations": 0, "last_observed_at": timestamp, "users": {}})
    momentum = record.setdefault("momentum", {})
    # 旧記録に後から低い勢いを足しても、旧時間帯の勢いまで分かったことにはしません。
    # 初めから勢いを保存した日だけ完全な記録とし、旧ログを全件復元した場合に回復できます。
    missing_previous = any(
        str(rank) not in momentum.get(user_id, {})
        for user_id, mask in record["users"].items()
        for rank in (1, 2, 3) if mask & (1 << (rank - 1))
    )
    record["momentum_complete"] = record.get("momentum_complete", True) and not missing_previous
    # 同じ観測時刻の再実行でも、観測件数を重複させません。
    if record["observations"] == 0 or record["last_observed_at"] != timestamp:
        record["observations"] += 1
    record["last_observed_at"] = max(record["last_observed_at"], timestamp)
    added = 0
    for stream in streams:
        # 順位不明を画面上の並び順から推測すると誤集計するため、記録しません。
        if type(stream.rank) is not int or stream.rank not in (1, 2, 3):
            continue
        user_id = str(stream.user_id)
        if not re.fullmatch(r"[0-9]+", user_id):
            continue
        # 1位=1、2位=2、3位=4の印を足し合わせず結合します。
        # 例：同じ日に1位と3位に入ると5。何度入っても5のままです。
        previous = record["users"].get(user_id, 0)
        updated = previous | (1 << (stream.rank - 1))
        record["users"][user_id] = updated
        added += updated != previous
        # 順位ごとに分けるのが大事です。1位で100・3位で200だった人は、
        # 「1位だけ・150以上」では数えず、「3位・150以上」ならその日を数えます。
        # 同じ順位では最大値だけあれば、どの下限で数えても結果は変わりません。
        by_rank = momentum.setdefault(user_id, {})
        rank = str(stream.rank)
        by_rank[rank] = max(by_rank.get(rank, 0), stream.momentum)
        profile = history["profiles"].get(user_id)
        if profile is None or profile["observed_at"] <= timestamp:
            history["profiles"][user_id] = {"name": stream.broadcaster_name, "observed_at": timestamp}
    history["first_observed_at"] = min(history.get("first_observed_at") or timestamp, timestamp)
    history["last_observed_at"] = max(history.get("last_observed_at") or timestamp, timestamp)
    return added


def save_history(path: Path, history: dict[str, Any]) -> None:
    validate_history(history)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    # 長期間保存しても大きくなりすぎないよう、余分な空白は付けません。
    temporary.write_text(json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)
