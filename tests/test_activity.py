import unittest
from datetime import timedelta

from activity import (
    close_current_activity,
    detect_cooking,
    detect_dishwasher,
    detect_entering_home,
    detect_exiting_home,
    detect_laundry,
    detect_meal,
    detect_office,
    detect_sleeping,
    process_activities,
    update_activity_state,
)
from house_state import HouseState
from models import Device, Point, Sensor, Wall


class FakeTimer:
    def __init__(self, simulated_time="12:00"):
        self.elapsed_time = timedelta()
        self.is_running = True
        self.simulated_time = simulated_time

    def get_simulated_time(self):
        return self.simulated_time


def make_sensor(name, sensor_type, x=0, y=0, direction=None):
    return Sensor(
        name=name,
        x=x,
        y=y,
        type=sensor_type,
        min_val=0,
        max_val=1,
        step=1,
        state=0,
        direction=direction,
    )


class MealDetectionTests(unittest.TestCase):
    def setUp(self):
        self.timer = FakeTimer("12:00")
        self.pir = make_sensor("kitchen_pir", "PIR", direction=0)
        self.weight = make_sensor("chair", "Weight", x=12, y=0)
        self.oven = Device("oven", 10, 0, "Oven", 2000, 0, 1500, 2000)
        self.points = [Point("table1", 12, 0)]
        self.sensor_states = {
            self.pir.name: {"state": [1]},
            self.weight.name: {"state": [1]},
        }
        self.state = {}

    def detect(self):
        return detect_meal(
            self.sensor_states,
            [self.pir, self.weight],
            [self.oven],
            self.points,
            self.timer,
            self.state,
            [],
            [],
        )

    def test_meal_is_confirmed_without_overwriting_activity_state(self):
        self.assertIsNone(self.detect())
        self.timer.elapsed_time = timedelta(seconds=10)

        self.assertEqual(self.detect(), "lunch")
        self.assertEqual(self.state["meal_active"], "lunch")

    def test_interrupted_meal_does_not_accumulate_duration(self):
        self.assertIsNone(self.detect())
        self.timer.elapsed_time = timedelta(seconds=5)
        self.sensor_states[self.weight.name]["state"].append(0)
        self.assertIsNone(self.detect())

        self.timer.elapsed_time = timedelta(seconds=10)
        self.sensor_states[self.weight.name]["state"].append(1)
        self.assertIsNone(self.detect())
        self.timer.elapsed_time = timedelta(seconds=15)
        self.assertIsNone(self.detect())


class SleepingDetectionTests(unittest.TestCase):
    def setUp(self):
        self.timer = FakeTimer("23:00")
        self.weight = make_sensor("bed_weight", "Weight", x=10, y=10)
        self.points = [Point("bed1", 10, 10)]
        self.sensor_states = {"bed_weight": {"state": [1]}}
        self.state = {}

    def detect(self):
        return detect_sleeping(
            self.sensor_states,
            [self.weight],
            self.points,
            self.timer,
            self.state,
        )

    def test_sleep_is_detected_after_continuous_weight(self):
        self.assertIsNone(self.detect())
        self.timer.elapsed_time = timedelta(seconds=10)
        self.assertEqual(self.detect(), "sleeping")

    def test_sleep_timer_resets_when_weight_becomes_inactive(self):
        self.assertIsNone(self.detect())
        self.timer.elapsed_time = timedelta(seconds=5)
        self.sensor_states["bed_weight"]["state"].append(0)
        self.assertIsNone(self.detect())

        self.timer.elapsed_time = timedelta(seconds=10)
        self.sensor_states["bed_weight"]["state"].append(1)
        self.assertIsNone(self.detect())
        self.timer.elapsed_time = timedelta(seconds=15)
        self.assertIsNone(self.detect())


class ApplianceDetectionTests(unittest.TestCase):
    def test_cooking_requires_oven_and_visible_active_pir(self):
        oven = Device("oven", 10, 0, "Oven", 2000, 1, 1500, 2000)
        pir = make_sensor("kitchen", "PIR", x=0, y=0, direction=0)
        sensor_states = {"kitchen": {"state": [1]}}

        self.assertEqual(
            detect_cooking(sensor_states, [oven], [pir], [], []),
            "cooking",
        )
        sensor_states["kitchen"]["state"].append(0)
        self.assertIsNone(detect_cooking(sensor_states, [oven], [pir], [], []))
        oven.state = 0
        sensor_states["kitchen"]["state"].append(1)
        self.assertIsNone(detect_cooking(sensor_states, [oven], [pir], [], []))

    def test_smart_meter_activities_require_matching_association(self):
        washing_machine = Device("wm", 0, 0, "Washing_Machine", 500, 1, 300, 500)
        dishwasher = Device("dw", 0, 0, "Dishwasher", 1800, 1, 1000, 1600)
        computer = Device("pc", 0, 0, "Computer", 250, 1, 100, 250)
        devices = [washing_machine, dishwasher, computer]

        sensor_states = {
            "sm_wm": {
                "type": "Smart Meter",
                "associated_device": "wm",
                "state": [1],
            },
            "sm_dw": {
                "type": "Smart Meter",
                "associated_device": "dw",
                "state": [1],
            },
            "sm_pc": {
                "type": "Smart Meter",
                "associated_device": "pc",
                "state": [1],
            },
        }

        self.assertEqual(detect_laundry(sensor_states, devices), "laundry")
        self.assertEqual(detect_dishwasher(sensor_states, devices), "dishwasher")
        self.assertEqual(detect_office(sensor_states, devices), "office")

        sensor_states["sm_wm"]["associated_device"] = "missing"
        self.assertIsNone(detect_laundry(sensor_states, devices))


class ActivityLifecycleTests(unittest.TestCase):
    def test_update_starts_and_ends_activity_once(self):
        state = {"current_activities": {}, "activity_sessions": {}}
        log_state = {"activity_log": [], "active_activities": {}}

        update_activity_state("12:00", {"cooking"}, None, state, log_state)
        update_activity_state("12:01", {"cooking"}, None, state, log_state)
        update_activity_state("12:02", set(), None, state, log_state)

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


class PresenceDetectionTests(unittest.TestCase):
    def setUp(self):
        self.timer = FakeTimer()
        self.entrance = make_sensor("entrance", "Switch")
        self.pir = make_sensor("hall", "PIR")
        self.sensors = [self.entrance, self.pir]
        self.sensor_states = {
            "entrance": {"state": [0]},
            "hall": {"state": [1]},
        }
        self.state = {}

    def test_exit_requires_recent_motion_then_no_active_pir(self):
        self.assertIsNone(
            detect_exiting_home(self.sensor_states, self.sensors, self.timer, self.state)
        )

        self.timer.elapsed_time = timedelta(seconds=1)
        self.sensor_states["entrance"]["state"].extend([1, 0])
        self.assertIsNone(
            detect_exiting_home(self.sensor_states, self.sensors, self.timer, self.state)
        )
        self.assertTrue(self.state["exit_triggered"])

        self.timer.elapsed_time = timedelta(seconds=6)
        self.sensor_states["hall"]["state"].append(0)
        self.assertEqual(
            detect_exiting_home(self.sensor_states, self.sensors, self.timer, self.state),
            "Leaving home",
        )
        self.assertTrue(self.state["exit_activated"])

        self.timer.elapsed_time = timedelta(seconds=7)
        self.assertIsNone(
            detect_entering_home(
                self.sensor_states,
                self.sensors,
                self.timer,
                state=self.state,
            )
        )
        self.assertTrue(self.state["exit_activated"])

    def test_exit_keeps_pir_motion_that_ended_between_polls(self):
        self.sensor_states["entrance"]["state"] = [0, 1, 0]
        self.sensor_states["hall"]["state"] = [0, 1, 0]

        self.assertIsNone(
            detect_exiting_home(self.sensor_states, self.sensors, self.timer, self.state)
        )
        self.assertTrue(self.state["exit_triggered"])

        self.timer.elapsed_time = timedelta(seconds=5)
        self.assertEqual(
            detect_exiting_home(self.sensor_states, self.sensors, self.timer, self.state),
            "Leaving home",
        )

    def test_opening_entrance_is_enough_to_start_exit_detection(self):
        self.sensor_states["entrance"]["state"] = [0, 1]
        self.sensor_states["hall"]["state"] = [0, 1, 0]

        self.assertIsNone(
            detect_exiting_home(self.sensor_states, self.sensors, self.timer, self.state)
        )
        self.assertTrue(self.state["exit_triggered"])

        self.timer.elapsed_time = timedelta(seconds=5)
        self.assertEqual(
            detect_exiting_home(self.sensor_states, self.sensors, self.timer, self.state),
            "Leaving home",
        )

    def test_entry_detects_complete_door_cycle_between_polls(self):
        self.state.update(
            {
                "exit_activated": True,
                "exit_last_edge_idx": 2,
                "returning_last_edge_idx": 2,
            }
        )
        self.sensor_states["entrance"]["state"] = [0, 1, 0, 1, 0]
        self.sensor_states["hall"]["state"] = [0]
        self.timer.elapsed_time = timedelta(seconds=7)

        self.assertIsNone(
            detect_entering_home(
                self.sensor_states,
                self.sensors,
                self.timer,
                state=self.state,
            )
        )
        self.sensor_states["hall"]["state"].extend([1, 0])
        self.timer.elapsed_time = timedelta(seconds=8)
        self.assertEqual(
            detect_entering_home(
                self.sensor_states,
                self.sensors,
                self.timer,
                state=self.state,
            ),
            "returning home",
        )
        self.assertFalse(self.state["exit_activated"])
        self.assertEqual(self.state["returning_last_edge_idx"], 4)

        self.timer.elapsed_time = timedelta(seconds=10)
        self.assertEqual(
            detect_entering_home(
                self.sensor_states,
                self.sensors,
                self.timer,
                state=self.state,
            ),
            "returning home",
        )
        self.timer.elapsed_time = timedelta(seconds=12)
        self.assertIsNone(
            detect_entering_home(
                self.sensor_states,
                self.sensors,
                self.timer,
                state=self.state,
            )
        )

    def test_opening_entrance_then_pir_detects_return(self):
        self.state.update(
            {
                "exit_activated": True,
                "exit_last_edge_idx": 1,
                "returning_last_edge_idx": 1,
            }
        )
        self.sensor_states["entrance"]["state"] = [0, 1, 0, 1]
        self.sensor_states["hall"]["state"] = [0]

        self.assertIsNone(
            detect_entering_home(
                self.sensor_states,
                self.sensors,
                self.timer,
                state=self.state,
            )
        )
        self.sensor_states["hall"]["state"].extend([1, 0])
        self.timer.elapsed_time = timedelta(seconds=1)
        self.assertEqual(
            detect_entering_home(
                self.sensor_states,
                self.sensors,
                self.timer,
                state=self.state,
            ),
            "returning home",
        )

    def test_entry_does_not_require_a_previously_detected_exit(self):
        self.sensor_states["entrance"]["state"] = [0, 1]
        self.sensor_states["hall"]["state"] = [0]

        self.assertIsNone(
            detect_entering_home(
                self.sensor_states,
                self.sensors,
                self.timer,
                state=self.state,
            )
        )
        self.sensor_states["hall"]["state"].extend([1, 0])
        self.timer.elapsed_time = timedelta(seconds=1)

        self.assertEqual(
            detect_entering_home(
                self.sensor_states,
                self.sensors,
                self.timer,
                state=self.state,
            ),
            "returning home",
        )

    def test_pir_before_door_does_not_count_as_entry(self):
        self.sensor_states["hall"]["state"] = [0, 1, 0]
        self.sensor_states["entrance"]["state"] = [0, 1]

        self.assertIsNone(
            detect_entering_home(
                self.sensor_states,
                self.sensors,
                self.timer,
                state=self.state,
            )
        )

    def test_process_does_not_report_leaving_and_returning_together(self):
        house_state = HouseState()
        activity_state = house_state.activity_state()
        activity_state.update(
            {
                "exit_activated": True,
                "exit_last_edge_idx": 2,
                "returning_last_edge_idx": 2,
            }
        )
        self.sensor_states["entrance"]["state"] = [0, 1, 0, 1, 0]
        self.sensor_states["hall"]["state"] = [0]
        house_state.values["sensor_states"] = self.sensor_states
        runtime_sources = {
            "points": [],
            "devices": [],
            "sensors": self.sensors,
            "walls": [],
            "doors": [],
        }

        process_activities(
            None,
            self.timer,
            self.sensor_states,
            house_state,
            runtime_sources,
        )
        self.sensor_states["hall"]["state"].extend([1, 0])
        self.timer.elapsed_time = timedelta(seconds=1)
        process_activities(
            None,
            self.timer,
            self.sensor_states,
            house_state,
            runtime_sources,
        )

        self.assertEqual(
            set(house_state.activity_state()["current_activities"]),
            {"returning home"},
        )

    def test_process_transitions_from_leaving_to_returning(self):
        house_state = HouseState()
        sensor_states = {
            "entrance": {"state": [0, 1]},
            "hall": {"state": [0, 1, 0]},
        }
        house_state.values["sensor_states"] = sensor_states
        runtime_sources = {
            "points": [],
            "devices": [],
            "sensors": self.sensors,
            "walls": [],
            "doors": [],
        }

        process_activities(
            None,
            self.timer,
            sensor_states,
            house_state,
            runtime_sources,
        )
        self.timer.elapsed_time = timedelta(seconds=5)
        process_activities(
            None,
            self.timer,
            sensor_states,
            house_state,
            runtime_sources,
        )
        self.assertEqual(
            set(house_state.activity_state()["current_activities"]),
            {"Leaving home"},
        )

        sensor_states["entrance"]["state"].append(0)
        self.timer.elapsed_time = timedelta(seconds=6)
        process_activities(
            None,
            self.timer,
            sensor_states,
            house_state,
            runtime_sources,
        )
        sensor_states["hall"]["state"].extend([1, 0])
        self.timer.elapsed_time = timedelta(seconds=7)
        process_activities(
            None,
            self.timer,
            sensor_states,
            house_state,
            runtime_sources,
        )

        self.assertFalse(house_state.activity_state()["exit_activated"])
        self.assertEqual(
            set(house_state.activity_state()["current_activities"]),
            {"returning home"},
        )

    def test_avatar_inside_home_clears_stale_leaving_state(self):
        house_state = HouseState()
        house_state.activity_state().update(
            {
                "exit_activated": True,
                "current_activities": {"Leaving home": "12:00"},
                "activity_sessions": {},
            }
        )
        house_state.activity_log_state().update(
            {
                "activity_log": [],
                "active_activities": {"Leaving home": "12:00"},
            }
        )
        house_state.sim_state()["avatar_position"] = (50, 50)
        house_state.activity_state()["avatar_presence"] = "outside"
        runtime_sources = {
            "points": [],
            "devices": [],
            "sensors": [],
            "walls": [
                Wall(0, 0, 100, 0),
                Wall(100, 0, 100, 100),
                Wall(100, 100, 0, 100),
                Wall(0, 100, 0, 0),
            ],
            "doors": [],
        }

        process_activities(
            None,
            self.timer,
            {},
            house_state,
            runtime_sources,
        )

        self.assertFalse(house_state.activity_state()["exit_activated"])
        self.assertEqual(
            set(house_state.activity_state()["current_activities"]),
            {"returning home"},
        )

    def test_avatar_outside_home_activates_leaving_state(self):
        house_state = HouseState()
        house_state.sim_state()["avatar_position"] = (150, 50)
        runtime_sources = {
            "points": [],
            "devices": [],
            "sensors": [],
            "walls": [
                Wall(0, 0, 100, 0),
                Wall(100, 0, 100, 100),
                Wall(100, 100, 0, 100),
                Wall(0, 100, 0, 0),
            ],
            "doors": [],
        }

        process_activities(
            None,
            self.timer,
            {},
            house_state,
            runtime_sources,
        )

        self.assertTrue(house_state.activity_state()["exit_activated"])
        self.assertEqual(
            set(house_state.activity_state()["current_activities"]),
            {"Leaving home"},
        )

    def test_personal_activities_are_suppressed_while_avatar_is_outside(self):
        house_state = HouseState()
        house_state.sim_state()["avatar_position"] = (150, 50)
        sensor_states = {
            "sm_pc": {
                "type": "Smart Meter",
                "associated_device": "pc",
                "state": [1],
            },
            "sm_wm": {
                "type": "Smart Meter",
                "associated_device": "wm",
                "state": [1],
            },
        }
        devices = [
            Device("pc", 10, 10, "Computer", 250, 1, 100, 250),
            Device("wm", 10, 10, "Washing_Machine", 500, 1, 300, 500),
        ]
        runtime_sources = {
            "points": [],
            "devices": devices,
            "sensors": [],
            "walls": [
                Wall(0, 0, 100, 0),
                Wall(100, 0, 100, 100),
                Wall(100, 100, 0, 100),
                Wall(0, 100, 0, 0),
            ],
            "doors": [],
        }

        process_activities(
            None,
            self.timer,
            sensor_states,
            house_state,
            runtime_sources,
        )

        self.assertEqual(
            set(house_state.activity_state()["current_activities"]),
            {"Leaving home", "laundry"},
        )


if __name__ == "__main__":
    unittest.main()
