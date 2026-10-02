"""同じ再生時間・全件誤更新・失敗と復旧の回帰確認。実送信はしない。"""
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

import archive_metadata as metadata
import mixcha_watcher as watcher

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
CREATED = int(datetime.datetime(2026, 10, 1, 12, 0, tzinfo=UTC).timestamp())


def snapshot(archive_id="20", created=CREATED, duration=14400):
    return metadata.ArchiveSnapshot(archive_id, created, duration, 1)


def record(archive_id="10", created=CREATED - 86400):
    return {
        "last_notified_date": "2026-09-30",
        "latest_archive_date": "2026-09-30",
        "archive_identity_version": 1,
        "latest_archive_id": archive_id,
        "latest_archive_created": created,
        "seen_archive_ids": [archive_id],
        "last_notified_archive_id": archive_id,
    }


def payload(user_id=1, archive_id=20, created=CREATED, duration=14400):
    return {"archives": [{"user_id": user_id, "live_session_id": archive_id,
                          "created": created, "duration": duration, "visibility": 1}],
            "has_next": False}


class ArchivePayloadTests(unittest.TestCase):
    def test_observed_site_response_has_stable_id_and_exact_time(self):
        fixture = Path(__file__).parent / "fixtures" / "archive-api-observed.json"
        result = metadata.parse_archive_payload(json.loads(fixture.read_text()), "18896271", now=NOW)
        self.assertEqual(result.archive_id, "22880743")
        self.assertEqual(result.marker, "122:17")
        self.assertEqual(result.archive_date, "2026-10-01")

    def test_real_empty_response_is_distinct_from_bad_or_wrong_user_response(self):
        empty = metadata.parse_archive_payload({"archives": [], "has_next": False}, "1", now=NOW)
        self.assertIsNone(empty.archive_id)
        for data in ({}, [], {"archives": []}, {"archives": [], "has_next": True},
                     {"archives": "Loading", "has_next": False}, payload(user_id=2),
                     payload(created=True), payload(duration=-1), payload(archive_id=True),
                     payload(created=int(NOW.timestamp()) + 3600)):
            with self.subTest(data=data), self.assertRaises(metadata.ArchiveReadError):
                metadata.parse_archive_payload(data, "1", now=NOW)

    def test_order_does_not_change_latest_identity(self):
        data = payload()
        data["archives"].insert(0, payload(archive_id=10, created=CREATED - 1)["archives"][0])
        self.assertEqual(metadata.parse_archive_payload(data, "1", now=NOW).archive_id, "20")

    def test_exact_japanese_date_crosses_midnight(self):
        created = int(datetime.datetime(2026, 10, 1, 14, 30, tzinfo=UTC).timestamp())
        self.assertEqual(snapshot(created=created).archive_date, "2026-10-01")

    def test_temporary_api_failure_is_retried_without_browser(self):
        session = MagicMock()
        good = MagicMock(status_code=200)
        good.json.return_value = payload()
        session.get.side_effect = [requests.Timeout("unreachable"), MagicMock(status_code=503), good]
        browser = MagicMock()
        with patch.object(metadata.time, "sleep"):
            client = metadata.ArchiveClient(browser_factory=browser, session=session)
            self.assertEqual(client.fetch_latest("1").archive_id, "20")
        self.assertEqual(session.get.call_count, 3)
        browser.assert_not_called()
        self.assertEqual(session.get.call_args.kwargs["params"], {"limit": 1})

    def test_browser_fallback_also_requires_a_valid_identity(self):
        session = MagicMock()
        session.get.return_value = MagicMock(status_code=403)
        driver = MagicMock()
        driver.execute_async_script.return_value = {"status": 200, "payload": payload()}
        with patch.object(metadata.time, "sleep"):
            client = metadata.ArchiveClient(browser_factory=lambda: driver, session=session)
            result = client.fetch_latest("1")
            self.assertEqual((result.archive_id, result.source), ("20", "browser_api"))
            driver.execute_async_script.return_value = {"status": 200, "payload": {}}
            with self.assertRaises(metadata.ArchiveReadError):
                client.fetch_latest("1")
        client.close()
        driver.quit.assert_called_once()


class ArchiveIdentityTests(unittest.TestCase):
    def decide(self, current, previous=None, marker="240:00"):
        return metadata.plan_archive_update(marker, previous if previous is not None else record(),
                                            current, checked_at=NOW)

    def test_different_video_with_identical_four_hour_duration_is_new(self):
        decision = self.decide(snapshot())
        self.assertTrue(decision.changed)
        self.assertEqual(decision.marker, "240:00")
        self.assertEqual(decision.activity["latest_archive_id"], "20")

    def test_same_video_duration_or_visibility_edit_is_not_new(self):
        decision = self.decide(snapshot("10", CREATED - 86400, 6000))
        self.assertFalse(decision.changed)
        self.assertEqual(decision.marker, "100:00")

    def test_empty_and_recovery_do_not_notify_or_erase_identity(self):
        empty = self.decide(metadata.ArchiveSnapshot())
        self.assertFalse(empty.changed)
        self.assertEqual(empty.activity["latest_archive_id"], "10")
        self.assertEqual(empty.marker, "240:00")
        recovered = self.decide(snapshot("10", CREATED - 86400), empty.activity, empty.marker)
        self.assertFalse(recovered.changed)

    def test_latest_deleted_then_restored_does_not_notify(self):
        older = self.decide(snapshot("9", CREATED - 2 * 86400))
        self.assertFalse(older.changed)
        self.assertEqual(older.activity["latest_archive_id"], "10")
        restored = self.decide(snapshot("10", CREATED - 86400), older.activity)
        self.assertFalse(restored.changed)

    def test_same_timestamp_still_distinguishes_two_video_ids(self):
        self.assertTrue(self.decide(snapshot("11", CREATED - 86400)).changed)
        self.assertFalse(self.decide(snapshot("9", CREATED - 86400)).changed)

    def test_legacy_migration_is_quiet_and_does_not_reset_notification_date(self):
        legacy = {"last_notified_date": "2026-09-23", "latest_archive_date": "2026-09-23"}
        for marker in ("240:00", "NO_VIDEO"):
            decision = self.decide(snapshot(), legacy, marker)
            self.assertFalse(decision.changed)
            self.assertEqual(decision.activity["last_notified_date"], "2026-09-23")
            self.assertNotIn("last_notified_archive_id", decision.activity)
            self.assertFalse(self.decide(snapshot(), decision.activity).changed)
            self.assertTrue(self.decide(snapshot("21", CREATED + 1), decision.activity).changed)

    def test_legacy_empty_before_recovery_is_also_quiet(self):
        empty = self.decide(metadata.ArchiveSnapshot(), {"last_notified_date": "2026-09-23"})
        self.assertNotIn("archive_identity_version", empty.activity)
        self.assertFalse(self.decide(snapshot(), empty.activity).changed)

    def test_first_video_after_confirmed_empty_is_new(self):
        empty = self.decide(metadata.ArchiveSnapshot(), {}, "NO_VIDEO")
        self.assertTrue(self.decide(snapshot(), empty.activity, "NO_VIDEO").changed)

    def test_first_added_user_can_notify(self):
        self.assertTrue(self.decide(snapshot(), {}, None).changed)


class MonitorPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.watchlist = self.root / "watchlist.json"
        self.state = self.root / "state.json"
        self.activity = self.root / "activity_state.json"
        self.watchlist.write_text('[{"id":"1","name":"one"},{"id":"2","name":"two"}]')
        self.state.write_text(json.dumps({"1": "240:00", "2": "240:00"}))
        self.activity.write_text(json.dumps({"1": record(), "2": record()}))
        for name, path in (("WATCHLIST_FILE", self.watchlist), ("STATE_FILE", self.state),
                           ("ACTIVITY_STATE_FILE", self.activity)):
            manager = patch.object(watcher, name, str(path))
            manager.start()
            self.addCleanup(manager.stop)
        for manager in (patch.object(watcher, "log_metric"),
                        patch.object(watcher, "get_discord_webhook_url", return_value="unused"),
                        patch.object(watcher, "is_last_daily_inactive_notification_run", return_value=True)):
            manager.start()
            self.addCleanup(manager.stop)

    def read_activity(self):
        return json.loads(self.activity.read_text())

    def test_partial_read_failure_preserves_failed_user_and_saves_healthy_notification(self):
        before = self.read_activity()["2"]
        with (patch.object(watcher.ArchiveClient, "fetch_latest", side_effect=[snapshot(), watcher.ArchiveReadError("offline")]),
              patch.object(watcher, "send_embeds_to_discord", return_value=[1.0]) as notify):
            with self.assertRaises(watcher.ArchiveReadError):
                watcher.main()
        self.assertEqual(self.read_activity()["1"]["latest_archive_id"], "20")
        self.assertEqual(self.read_activity()["2"], before)
        self.assertEqual(len(json.loads(self.watchlist.read_text())), 2)
        self.assertEqual(notify.call_count, 1)

    def test_delivery_failure_does_not_mark_new_archive_as_notified(self):
        with (patch.object(watcher.ArchiveClient, "fetch_latest", return_value=snapshot()),
              patch.object(watcher, "send_embeds_to_discord", side_effect=watcher.DiscordDeliveryError("offline"))):
            with self.assertRaises(watcher.DiscordDeliveryError):
                watcher.main()
        self.assertEqual([r["latest_archive_id"] for r in self.read_activity().values()], ["10", "10"])
        with (patch.object(watcher.ArchiveClient, "fetch_latest", return_value=snapshot()),
              patch.object(watcher, "send_embeds_to_discord", return_value=[1.0]) as notify):
            watcher.main()
            watcher.main()
        self.assertEqual(notify.call_count, 1)

    def test_successful_notification_chunk_is_not_sent_again_after_later_failure(self):
        with (patch.object(watcher, "DESCRIPTION_LIMIT", 75),
              patch.object(watcher.ArchiveClient, "fetch_latest", return_value=snapshot()),
              patch.object(watcher, "send_embeds_to_discord", side_effect=[[1.0], watcher.DiscordDeliveryError("offline")])):
            with self.assertRaises(watcher.DiscordDeliveryError):
                watcher.main()
        self.assertEqual(self.read_activity()["1"]["latest_archive_id"], "20")
        self.assertEqual(self.read_activity()["2"]["latest_archive_id"], "10")
        with (patch.object(watcher.ArchiveClient, "fetch_latest", return_value=snapshot()),
              patch.object(watcher, "send_embeds_to_discord", return_value=[1.0]) as notify):
            watcher.main()
        self.assertEqual(notify.call_count, 1)
        self.assertIn("two", notify.call_args.args[1][0])
        self.assertNotIn("one", notify.call_args.args[1][0])

    def test_mass_empty_response_preserves_all_files_and_sends_nothing(self):
        self.watchlist.write_text(json.dumps([{"id": str(i), "name": "test"} for i in range(1, 7)]))
        self.state.write_text(json.dumps({str(i): "240:00" for i in range(1, 7)}))
        self.activity.write_text(json.dumps({str(i): record() for i in range(1, 7)}))
        before = [p.read_text() for p in (self.state, self.activity, self.watchlist)]
        with (patch.object(watcher.ArchiveClient, "fetch_latest", return_value=metadata.ArchiveSnapshot()),
              patch.object(watcher, "send_embeds_to_discord") as notify):
            with self.assertRaises(watcher.ArchiveReadError):
                watcher.main()
        notify.assert_not_called()
        self.assertEqual(before, [p.read_text() for p in (self.state, self.activity, self.watchlist)])

    def test_missing_or_invalid_history_fails_before_fetch(self):
        for value in (None, "{}broken", "[]", '{"1":null}', '{}'):
            if value is None:
                self.state.unlink()
            else:
                self.state.write_text(value)
            with (patch.object(watcher.ArchiveClient, "fetch_latest") as fetch,
                  patch.object(watcher, "send_embeds_to_discord") as notify):
                with self.assertRaises(watcher.ArchiveReadError):
                    watcher.main()
                fetch.assert_not_called()
                notify.assert_not_called()

    def test_atomic_json_failure_preserves_original_file(self):
        before = self.state.read_text()
        def interrupt(data, fp, **kwargs):
            fp.write('{"half":')
            raise OSError("interrupted")
        with patch.object(watcher.json, "dump", side_effect=interrupt), self.assertRaises(OSError):
            watcher.save_json(str(self.state), {"new": "value"})
        self.assertEqual(before, self.state.read_text())
        self.assertEqual(list(self.root.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
