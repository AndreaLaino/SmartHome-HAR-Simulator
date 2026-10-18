"""Persist actual log files and verify they can be parsed without local state."""

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import log
from house_state import HouseState


class ActivityLogPersistenceTests(unittest.TestCase):
    def test_contiguous_sessions_merge_without_merging_a_real_gap(self):
        state = {}
        for start, end in (("12:00", "12:01"), ("12:01", "12:03"), ("12:05", "12:06")):
            log.log_activity_start("cooking", start, state)
            log.log_activity_end("cooking", end, state)
        self.assertEqual(state["activity_log"], [
            {"activity": "cooking", "start": "12:00", "end": "12:03"},
            {"activity": "cooking", "start": "12:05", "end": "12:06"},
        ])

    def test_simulation_end_closes_each_activity_once(self):
        state = {}
        log.log_activity_start("cooking", "12:00", state)
        log.log_activity_start("office", "12:01", state)
        log.log_activity_start("office", "12:02", state)
        log.log_end_of_simulation("12:05", state)
        log.log_end_of_simulation("12:06", state)
        self.assertEqual(state["active_activities"], {})
        self.assertEqual(len(state["activity_log"]), 2)
        self.assertEqual({entry["end"] for entry in state["activity_log"]}, {"12:05"})
        self.assertEqual(state["activity_log"][1]["start"], "12:01")

    def test_activity_csv_quotes_names_and_does_not_mutate_history(self):
        entries = [
            {"activity": "café, study", "start": "12:00", "end": "12:01"},
            {"activity": "café, study", "start": "12:01", "end": "12:02"},
        ]
        state = {"activity_log": entries}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activities.csv"
            log.save_activity_log(path, state)
            with path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(rows, [{"activity": "café, study", "start": "12:00", "end": "12:02"}])
        self.assertEqual(entries[0]["end"], "12:01")


class InteractionLoggingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.state = HouseState()
        session = patch.object(log, "get_or_create_current_save_session", return_value=self.directory)
        session.start()
        self.addCleanup(session.stop)
        self.addCleanup(log.stop_interaction_log_session, self.state)

    def rows(self):
        with (self.directory / "interactions" / "interactions.csv").open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_events_before_start_and_after_stop_are_ignored(self):
        log.log_move(self.state, "2026-07-14 12:00", 1, 2)
        self.assertFalse((self.directory / "interactions").exists())
        log.start_interaction_log_session(self.state)
        log.stop_interaction_log_session(self.state)
        log.log_move(self.state, "2026-07-14 12:01", 1, 2)
        self.assertEqual(self.rows(), [])

    def test_all_event_types_round_trip_in_one_csv(self):
        log.start_interaction_log_session(self.state, "home: one/two")
        time = "2026-07-14 12:00"
        log.log_move(self.state, time, 10, 20)
        log.log_sensor_event(self.state, time, "power, café", "Smart Meter", 10, 20, 0, "reason, quoted")
        log.log_device_event(self.state, time, "oven", "Oven", 15, 20, 1)
        log.log_door_event(self.state, time, "door", 0, 0, 0, 25, 1)
        log.log_llm_cycle_event(self.state, time, "meter", {"cycle_id": 3, "cluster": 2, "duration_minutes": 5})
        rows = self.rows()  # Writes are flushed before shutdown.
        self.assertEqual([row["event_type"] for row in rows], ["move", "sensor", "device", "door", "llm_cycle"])
        self.assertEqual(rows[1]["name"], "power, café")
        self.assertEqual(rows[1]["value"], "0")
        self.assertEqual(rows[1]["extra"], "reason, quoted")
        self.assertEqual(rows[3]["extra"], "(0,0)-(0,25)")
        self.assertEqual(json.loads(rows[4]["extra"])["duration_minutes"], 5)
        self.assertTrue(all(None not in row for row in rows))

    def test_reopening_appends_with_a_single_header(self):
        log.start_interaction_log_session(self.state)
        log.log_move(self.state, "12:00", 1, 2)
        log.stop_interaction_log_session(self.state)
        log.start_interaction_log_session(self.state)
        log.log_move(self.state, "12:01", 3, 4)
        self.assertEqual([row["timestamp_sim"] for row in self.rows()], ["12:00", "12:01"])

    def test_starting_an_open_session_releases_previous_file_handle(self):
        log.start_interaction_log_session(self.state)
        previous = self.state.interaction_log_state()["interaction_file"]
        log.start_interaction_log_session(self.state)
        self.assertTrue(previous.closed)

    def test_empty_existing_file_gets_a_header(self):
        directory = self.directory / "interactions"
        directory.mkdir()
        (directory / "interactions.csv").touch()
        log.start_interaction_log_session(self.state)
        log.log_move(self.state, "12:00", 1, 2)
        self.assertEqual(self.rows()[0]["timestamp_sim"], "12:00")

    def test_stop_is_idempotent_and_closes_the_file(self):
        log.start_interaction_log_session(self.state)
        stream = self.state.interaction_log_state()["interaction_file"]
        log.stop_interaction_log_session(self.state)
        log.stop_interaction_log_session(self.state)
        self.assertTrue(stream.closed)
        self.assertIsNone(self.state.interaction_log_state()["interaction_file"])


if __name__ == "__main__":
    unittest.main()
