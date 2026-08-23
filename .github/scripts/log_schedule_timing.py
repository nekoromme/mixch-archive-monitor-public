#!/usr/bin/env python3
"""Print schedule timing diagnostics for the Mixcha watcher workflow."""

import datetime as dt
import os

EXPECTED_WEEKDAY_SCHEDULED_RUNS_PER_JST_DAY = 4
EXPECTED_WEEKEND_SCHEDULED_RUNS_PER_JST_DAY = 6
JST = dt.timezone(dt.timedelta(hours=9))


def expand_csv_field(field: str) -> list[int]:
    values: list[int] = []
    for part in field.split(','):
        if '-' in part:
            start, end = [int(value) for value in part.split('-', 1)]
            values.extend(range(start, end + 1))
        else:
            values.append(int(part))
    return values


def latest_scheduled_slot(cron: str, now: dt.datetime) -> dt.datetime | None:
    minute_s, hour_s, *_ = cron.split()
    minutes = expand_csv_field(minute_s)
    hours = expand_csv_field(hour_s)
    candidates: list[dt.datetime] = []

    for day_offset in range(-1, 1):
        day = now.date() + dt.timedelta(days=day_offset)
        for hour in hours:
            for minute in minutes:
                candidates.append(dt.datetime.combine(day, dt.time(hour, minute), dt.timezone.utc))

    return max((candidate for candidate in candidates if candidate <= now), default=None)


def main() -> None:
    cron = os.getenv("SCHEDULE_CRON") or "manual"
    now = dt.datetime.now(dt.timezone.utc)

    print(f"expected_weekday_scheduled_runs_per_jst_day={EXPECTED_WEEKDAY_SCHEDULED_RUNS_PER_JST_DAY}")
    print(f"expected_weekend_scheduled_runs_per_jst_day={EXPECTED_WEEKEND_SCHEDULED_RUNS_PER_JST_DAY}")
    print("schedule_history_note=2026-06-12以前は原則1日2回、2026-07-26までは平日1日17回・土日1日21回、現在は平日1日4回・土日1日6回")

    if cron == "manual":
        print("scheduled_slot=manual_or_unknown")
        return

    scheduled_at = latest_scheduled_slot(cron, now)
    if not scheduled_at:
        print("scheduled_slot=unknown")
        return

    print(f"scheduled_slot_utc={scheduled_at.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"scheduled_slot_jst={scheduled_at.astimezone(JST).strftime('%Y-%m-%dT%H:%M:%S%z')}")
    print(f"schedule_delay_sec={round((now - scheduled_at).total_seconds())}")


if __name__ == "__main__":
    main()

