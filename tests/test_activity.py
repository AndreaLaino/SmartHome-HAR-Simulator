"""Activity intervals and shutdown; recognition tests live in test_learned_har_live."""

import unittest

from activity import close_current_activity, update_activity_state
from house_state import HouseState


class FakeTimer:
    def __init__(self, simulated_time="12:00"):
        self.simulated_time = simulated_time

    def get_simulated_time(self):
        return self.simulated_time


class ActivityLifecycleTests(unittest.TestCase):
    def test_update_starts_and_ends_activity_once(self):
        state = {"current_activities": {}, "activity_sessions": {}}
        log_state = {"activity_log": [], "active_activities": {}}

        update_activity_state("12:00", {"cooking"}, state, log_state)
        update_activity_state("12:01", {"cooking"}, state, log_state)
        update_activity_state("12:02", set(), state, log_state)

        self.assertEqual(
            state["activity_sessions"]["cooking"],
            [{"start": "12:00", "end": "12:02"}],
        )
        self.assertEqual(
            log_state["activity_log"],
            [{"activity": "cooking", "start": "12:00", "end": "12:02"}],
        )

    def test_close_current_activity_closes_all_open_sessions(self):
        timer = FakeTimer("12:30")
        house_state = HouseState()
        house_state.activity_state().update(
            {
                "current_activities": {
                    "cooking": "12:00",
                    "office": "12:10",
                },
                "activity_sessions": {},
            }
        )
        house_state.activity_log_state().update(
            {
                "activity_log": [],
                "active_activities": {
                    "cooking": "12:00",
                    "office": "12:10",
                },
            }
        )

        close_current_activity(timer, house_state=house_state)

        self.assertEqual(house_state.activity_state()["current_activities"], {})
        self.assertEqual(
            house_state.activity_state()["activity_sessions"]["cooking"],
            [{"start": "12:00", "end": "12:30"}],
        )
        self.assertEqual(len(house_state.activity_log_state()["activity_log"]), 2)
        self.assertEqual(house_state.activity_log_state()["active_activities"], {})
