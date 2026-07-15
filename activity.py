import re
from datetime import timedelta

from log import log_activity_start, log_activity_end, log_end_of_simulation
from house_state import HouseState
from utils import find_closest_sensor_within_fov

FOV_ANGLE = 60  # degrees for PIR field-of-view checks
RADIUS_STANDARD = 150   # px distance for "closest sensor within FOV"
MEAL_MIN_DURATION = 10  # elapsed timer seconds, equivalent to 10 simulated minutes
SLEEP_MIN_DURATION = 10  # elapsed timer seconds, equivalent to 10 simulated minutes
EXIT_MOTION_WINDOW = 10  # elapsed timer seconds, equivalent to 10 simulated minutes
ENTRY_MOTION_WINDOW = 5
EXIT_CONFIRMATION_DELAY = 5
ENTRY_ACTIVITY_DURATION = 3


def _activity_state(house_state: HouseState) -> dict:
    state = house_state.activity_state()
    state.setdefault("activity_sessions", {})
    state.setdefault("current_activities", {})
    state.setdefault("exit_triggered", False)
    state.setdefault("exit_time", None)
    state.setdefault("exit_activated", False)
    state.setdefault("returning_triggered", False)
    state.setdefault("returning_time", None)
    state.setdefault("returning_activity_until", None)
    state.setdefault("returning_pir_positions", {})
    state.setdefault("avatar_presence", None)
    state.setdefault(
        "meal_detection_start",
        {
            "breakfast": None,
            "lunch": None,
            "dinner": None,
        },
    )
    state.setdefault("meal_active", None)
    state.setdefault("sleep_weight_start", {})
    state.setdefault("exit_last_edge_idx", -1)
    state.setdefault("returning_last_edge_idx", -1)
    state.setdefault("last_pir_motion_time", None)
    state.setdefault("pir_event_positions", {})
    return state


def _activity_log_state(house_state: HouseState) -> dict:
    state = house_state.activity_log_state()
    state.setdefault("activity_log", [])
    state.setdefault("active_activities", {})
    return state


def process_activities(
    activity_label,
    timer_app_instance,
    sensor_states_store,
    house_state: HouseState,
    runtime_sources: dict,
):
    state = _activity_state(house_state)
    log_state = _activity_log_state(house_state)

    p_points = runtime_sources["points"]
    d_devices = runtime_sources["devices"]
    s_sensors = runtime_sources["sensors"]
    walls = runtime_sources["walls"]
    d_doors = runtime_sources["doors"]

    if not timer_app_instance.is_running:
        return

    now = timer_app_instance.get_simulated_time()
    detected = set()

    avatar_presence, presence_activity = _presence_from_avatar(
        house_state,
        p_points,
        walls,
        timer_app_instance,
        state,
    )

    if avatar_presence is not None:
        returning = "returning home" if presence_activity == "returning home" else None
        leaving = "Leaving home" if presence_activity == "Leaving home" else None
    elif state.get("exit_activated", False):
        returning = detect_entering_home(
            sensor_states_store,
            s_sensors,
            timer_app_instance,
            activity_label,
            state,
        )
        leaving = None if returning else detect_exiting_home(
            sensor_states_store,
            s_sensors,
            timer_app_instance,
            state,
        )
    else:
        leaving = detect_exiting_home(
            sensor_states_store,
            s_sensors,
            timer_app_instance,
            state,
        )
        returning = detect_entering_home(
            sensor_states_store,
            s_sensors,
            timer_app_instance,
            activity_label,
            state,
        )
    if returning:
        detected.add(returning)
    elif leaving:
        detected.add(leaving)

    detectors = [
        lambda: detect_laundry(sensor_states_store, d_devices),
        lambda: detect_dishwasher(sensor_states_store, d_devices),
    ]
    if avatar_presence != "outside":
        detectors.extend(
            [
                lambda: detect_sleeping(
                    sensor_states_store,
                    s_sensors,
                    p_points,
                    timer_app_instance,
                    state,
                ),
                lambda: detect_cooking(
                    sensor_states_store,
                    d_devices,
                    s_sensors,
                    walls,
                    d_doors,
                ),
                lambda: detect_meal(
                    sensor_states_store,
                    s_sensors,
                    d_devices,
                    p_points,
                    timer_app_instance,
                    state,
                    walls,
                    d_doors,
                ),
                lambda: detect_office(sensor_states_store, d_devices),
            ]
        )

    for detect in detectors:
        act = detect()
        if act:
            detected.add(act)

    update_activity_state(now, detected, activity_label, state, log_state)


def monitor_activities(canvas, activity_label, timer_app_instance, sensor_states_store, house_state: HouseState, runtime_sources: dict):
    process_activities(
        activity_label,
        timer_app_instance,
        sensor_states_store,
        house_state,
        runtime_sources,
    )
    if timer_app_instance.is_running:
        canvas.after(1000, monitor_activities, canvas, activity_label, timer_app_instance, sensor_states_store, house_state, runtime_sources)

def update_activity_state(current_time, detected_activities, activity_label, state: dict, log_state: dict):
    current_activities = state["current_activities"]
    activity_sessions = state["activity_sessions"]

    for act in detected_activities:
        if act not in current_activities:
            current_activities[act] = current_time
            log_activity_start(act, current_time, log_state)

    ended = [act for act in current_activities if act not in detected_activities]
    for act in ended:
        start = current_activities.pop(act)
        activity_sessions.setdefault(act, []).append({"start": start, "end": current_time})
        log_activity_end(act, current_time, log_state)

    # update activity label
    active = list(current_activities.keys())
    if activity_label:
        if active:
            activity_label.config(text="Activity: " + ", ".join(sorted(active)))
        else:
            activity_label.config(text="Activity: None")

def close_current_activity(timer_app_instance, activity_label=None, house_state: HouseState | None = None):
    state = _activity_state(house_state) if house_state is not None else {
        "current_activities": {},
        "activity_sessions": {},
    }
    log_state = _activity_log_state(house_state) if house_state is not None else {
        "activity_log": [],
        "active_activities": {},
    }
    current_activities = state["current_activities"]
    activity_sessions = state["activity_sessions"]

    now = timer_app_instance.get_simulated_time()

    for act, start in list(current_activities.items()):
        activity_sessions.setdefault(act, []).append({"start": start, "end": now})
        log_activity_end(act, now, log_state)
    current_activities.clear()

    if activity_label:
        activity_label.config(text="Activity: None")

    log_end_of_simulation(now, log_state)


def detect_cooking(sensor_states, devices, sensors, walls, doors):
    pir_sensors = [sensor for sensor in sensors if sensor.type.lower() == "pir"]
    for device in devices:
        x, y, device_type, device_state = device.x, device.y, device.type, device.state
        if re.match(r'^oven\d*$', device_type, re.IGNORECASE) and device_state == 1:
            pir = find_closest_sensor_within_fov((x, y), pir_sensors, walls, doors, RADIUS_STANDARD, FOV_ANGLE)
            if pir:
                pir_state = sensor_states.get(pir.name, {}).get('state', [])
                if pir_state and pir_state[-1] == 1:
                    return "cooking"
    return None

def detect_laundry(sensor_states, devices):
    for name, data in sensor_states.items():
        if data.get('type') == "Smart Meter":
            assoc = data.get('associated_device')
            state = data['state'][-1] if data['state'] else 0
            if assoc:
                for d in devices:
                    if d.name == assoc and d.type.lower() == "washing_machine" and state > 0:
                        return "laundry"
    return None

def detect_dishwasher(sensor_states, devices):
    for name, data in sensor_states.items():
        if data.get('type') == "Smart Meter":
            assoc = data.get('associated_device')
            state = data['state'][-1] if data['state'] else 0
            if assoc:
                for d in devices:
                    if d.name == assoc and d.type.lower() == "dishwasher" and state > 0:
                        return "dishwasher"
    return None

def detect_office(sensor_states, devices):
    for name, data in sensor_states.items():
        if data.get('type') == "Smart Meter":
            assoc = data.get('associated_device')
            state = data['state'][-1] if data['state'] else 0
            if assoc:
                for d in devices:
                    if d.name == assoc and d.type.lower() == "computer" and state > 0:
                        return "office"
    return None

def _entrance_state_sequence(sensor_states, sensors):
    for sensor in sensors:
        if sensor.name.lower() == "entrance" and sensor.type.lower() == "switch":
            return sensor_states.get(sensor.name, {}).get("state", [])
    return []


def _home_bounds(points, walls):
    wall_coordinates = [
        coordinate
        for wall in walls
        for coordinate in ((wall.x1, wall.y1), (wall.x2, wall.y2))
    ]
    coordinates = wall_coordinates or [(point.x, point.y) for point in points]
    if not coordinates:
        return None

    xs = [coordinate[0] for coordinate in coordinates]
    ys = [coordinate[1] for coordinate in coordinates]
    return min(xs), min(ys), max(xs), max(ys)


def _presence_from_avatar(
    house_state,
    points,
    walls,
    timer_app_instance,
    state,
):
    avatar_position = house_state.sim_state().get("avatar_position")
    bounds = _home_bounds(points, walls)
    if avatar_position is None or bounds is None:
        return None, None

    x, y = avatar_position
    min_x, min_y, max_x, max_y = bounds
    current_presence = (
        "inside"
        if min_x <= x <= max_x and min_y <= y <= max_y
        else "outside"
    )
    previous_presence = state.get("avatar_presence")
    state["avatar_presence"] = current_presence

    state["exit_triggered"] = False
    state["exit_time"] = None
    state["returning_triggered"] = False
    state["returning_time"] = None

    if current_presence == "outside":
        state["exit_activated"] = True
        state["returning_activity_until"] = None
        return "outside", "Leaving home"

    state["exit_activated"] = False
    if previous_presence == "outside":
        state["returning_activity_until"] = (
            timer_app_instance.elapsed_time + timedelta(seconds=ENTRY_ACTIVITY_DURATION)
        )
    elif (
        state.get("returning_activity_until") is not None
        and timer_app_instance.elapsed_time >= state["returning_activity_until"]
    ):
        state["returning_activity_until"] = None
    returning_active = state.get("returning_activity_until") is not None
    return "inside", "returning home" if returning_active else None


def _latest_door_edge_after(sequence, last_edge_idx):
    latest = None
    for index in range(max(1, last_edge_idx + 1), len(sequence)):
        if sequence[index - 1] != sequence[index]:
            latest = index
    return latest


def _any_pir_active(sensor_states, sensors):
    return any(
        sensor_states.get(sensor.name, {}).get("state", [])
        and sensor_states[sensor.name]["state"][-1] == 1
        for sensor in sensors
        if sensor.type.lower() == "pir"
    )


def _observe_pir_motion(sensor_states, sensors, state, now_elapsed):
    positions = state.setdefault("pir_event_positions", {})
    motion_detected = False

    for sensor in sensors:
        if sensor.type.lower() != "pir":
            continue

        sequence = sensor_states.get(sensor.name, {}).get("state", [])
        start_index = min(int(positions.get(sensor.name, 0)), len(sequence))
        if any(value == 1 for value in sequence[start_index:]):
            motion_detected = True
        positions[sensor.name] = len(sequence)

    if motion_detected or _any_pir_active(sensor_states, sensors):
        state["last_pir_motion_time"] = now_elapsed
        return True
    return False


def _pir_positions(sensor_states, sensors):
    return {
        sensor.name: len(sensor_states.get(sensor.name, {}).get("state", []))
        for sensor in sensors
        if sensor.type.lower() == "pir"
    }


def _pir_motion_after_positions(sensor_states, sensors, positions):
    for sensor in sensors:
        if sensor.type.lower() != "pir":
            continue
        sequence = sensor_states.get(sensor.name, {}).get("state", [])
        start_index = min(int(positions.get(sensor.name, 0)), len(sequence))
        if any(value == 1 for value in sequence[start_index:]):
            return True
    return False


def detect_exiting_home(sensor_states, sensors, timer_app_instance, state: dict):
    exit_triggered = bool(state.get("exit_triggered", False))
    exit_time = state.get("exit_time")
    exit_activated = bool(state.get("exit_activated", False))
    exit_last_edge_idx = int(state.get("exit_last_edge_idx", -1))
    last_pir_motion_time = state.get("last_pir_motion_time")

    now_elapsed = timer_app_instance.elapsed_time

    _observe_pir_motion(sensor_states, sensors, state, now_elapsed)
    last_pir_motion_time = state.get("last_pir_motion_time")

    entrance_state = _entrance_state_sequence(sensor_states, sensors)
    if not exit_activated:
        last_edge_idx = _latest_door_edge_after(entrance_state, exit_last_edge_idx)
        if last_edge_idx is not None:
            exit_last_edge_idx = last_edge_idx
            state["returning_last_edge_idx"] = max(
                int(state.get("returning_last_edge_idx", -1)),
                last_edge_idx,
            )
            state["returning_triggered"] = True
            state["returning_time"] = now_elapsed
            state["returning_pir_positions"] = _pir_positions(sensor_states, sensors)
            recent_motion = (
                last_pir_motion_time is not None and
                (now_elapsed - last_pir_motion_time).total_seconds() <= EXIT_MOTION_WINDOW
            )
            if recent_motion:
                exit_triggered = True
                exit_time = now_elapsed

    if exit_triggered and not exit_activated:
        delta = (now_elapsed - exit_time).total_seconds()
        if delta >= EXIT_CONFIRMATION_DELAY:
            exit_triggered = False
            if not _any_pir_active(sensor_states, sensors):
                exit_activated = True
                state["returning_triggered"] = False
                state["returning_time"] = None
                state["exit_triggered"] = exit_triggered
                state["exit_time"] = exit_time
                state["exit_activated"] = exit_activated
                state["exit_last_edge_idx"] = exit_last_edge_idx
                state["last_pir_motion_time"] = last_pir_motion_time
                return "Leaving home"

    if exit_activated:
        state["exit_triggered"] = exit_triggered
        state["exit_time"] = exit_time
        state["exit_activated"] = exit_activated
        state["exit_last_edge_idx"] = exit_last_edge_idx
        state["last_pir_motion_time"] = last_pir_motion_time
        return "Leaving home"

    state["exit_triggered"] = exit_triggered
    state["exit_time"] = exit_time
    state["exit_activated"] = exit_activated
    state["exit_last_edge_idx"] = exit_last_edge_idx
    state["last_pir_motion_time"] = last_pir_motion_time
    return None

def detect_entering_home(sensor_states, sensors, timer_app_instance, activity_label=None, state: dict | None = None):
    if state is None:
        state = {}

    returning_triggered = bool(state.get("returning_triggered", False))
    returning_time = state.get("returning_time")
    exit_activated = bool(state.get("exit_activated", False))
    exit_triggered = bool(state.get("exit_triggered", False))
    exit_time = state.get("exit_time")
    exit_last_edge_idx = int(state.get("exit_last_edge_idx", -1))
    returning_last_edge_idx = int(state.get("returning_last_edge_idx", -1))
    returning_activity_until = state.get("returning_activity_until")
    returning_pir_positions = state.get("returning_pir_positions", {})
    now_elapsed = timer_app_instance.elapsed_time

    if returning_activity_until is not None and now_elapsed < returning_activity_until:
        return "returning home"
    state["returning_activity_until"] = None

    entrance_state = _entrance_state_sequence(sensor_states, sensors)
    last_edge_idx = _latest_door_edge_after(entrance_state, returning_last_edge_idx)
    if last_edge_idx is not None:
        returning_last_edge_idx = last_edge_idx
        returning_triggered = True
        returning_time = now_elapsed
        returning_pir_positions = _pir_positions(sensor_states, sensors)

    if returning_triggered:
        delta = (now_elapsed - returning_time).total_seconds()
        if delta > ENTRY_MOTION_WINDOW:
            returning_triggered = False
            returning_time = None
        else:
            if _pir_motion_after_positions(
                sensor_states,
                sensors,
                returning_pir_positions,
            ):
                returning_triggered = False
                returning_time = None
                exit_activated = False
                exit_triggered = False
                exit_time = None
                exit_last_edge_idx = max(exit_last_edge_idx, returning_last_edge_idx)
                returning_activity_until = now_elapsed + timedelta(
                    seconds=ENTRY_ACTIVITY_DURATION
                )

                if activity_label:
                    activity_label.config(text="Activity: returning home")
                    def reset_label():
                        current_activities = state.get("current_activities", {})
                        if current_activities:
                            activity_label.config(
                                text="Activity: " + ", ".join(sorted(current_activities.keys()))
                            )
                        else:
                            activity_label.config(text="Activity: None")
                    activity_label.after(2000, reset_label)

                state["returning_triggered"] = returning_triggered
                state["returning_time"] = returning_time
                state["exit_activated"] = exit_activated
                state["exit_triggered"] = exit_triggered
                state["exit_time"] = exit_time
                state["exit_last_edge_idx"] = exit_last_edge_idx
                state["returning_last_edge_idx"] = returning_last_edge_idx
                state["returning_activity_until"] = returning_activity_until
                state["returning_pir_positions"] = returning_pir_positions

                return "returning home"

    state["returning_triggered"] = returning_triggered
    state["returning_time"] = returning_time
    state["exit_activated"] = exit_activated
    state["exit_triggered"] = exit_triggered
    state["exit_time"] = exit_time
    state["exit_last_edge_idx"] = exit_last_edge_idx
    state["returning_last_edge_idx"] = returning_last_edge_idx
    state["returning_activity_until"] = returning_activity_until
    state["returning_pir_positions"] = returning_pir_positions
    return None


def detect_sleeping(sensor_states, sensors, points, timer_app_instance, state: dict):
    sleep_weight_start = state.setdefault("sleep_weight_start", {})

    bed_pattern = re.compile(r'^bed\d*$', re.IGNORECASE)

    # search all 'bed*' points
    beds = [(point.name, point.x, point.y) for point in points if bed_pattern.match(point.name)]

    def dist(ax, ay, bx, by):
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

    any_near_bed_active = False

    for _, lx, ly in beds:
        # search weight sensor near bed
        for s in sensors:
            name, sx, sy, type = s.name, s.x, s.y, s.type
            if type == "Weight" and dist(lx, ly, sx, sy) < 30:
                state_seq = sensor_states.get(name, {}).get('state', [])
                active = bool(state_seq and state_seq[-1] == 1)

                if active:
                    any_near_bed_active = True
                    # start timer for this sensor
                    if name not in sleep_weight_start:
                        sleep_weight_start[name] = timer_app_instance.elapsed_time
                    else:
                        delta = (timer_app_instance.elapsed_time - sleep_weight_start[name]).total_seconds()
                        if delta >= SLEEP_MIN_DURATION:
                            return "sleeping"
                else:
                    # reset timer
                    if name in sleep_weight_start:
                        del sleep_weight_start[name]




def detect_meal(
    sensor_states,
    sensors,
    devices,
    points_source,
    timer_app_instance,
    state: dict,
    walls=None,
    doors=None,
):
    meal_detection_start = state.setdefault(
        "meal_detection_start",
        {"breakfast": None, "lunch": None, "dinner": None},
    )
    meal_active = state.get("meal_active")
    TABLE_RADIUS = 40  # max distance weight - table

    # find table coordinates
    table_coords = None
    table_pattern = re.compile(r'^table\d*$', re.IGNORECASE)
    for point in points_source:
        if table_pattern.match(point.name):
            table_coords = (point.x, point.y)
            break

    # search for Active Weight sensor near the table
    def weight_active_near_table():
        if not table_coords:
            return False
        tx, ty = table_coords
        for s in sensors:
            name, sx, sy, type = s.name, s.x, s.y, s.type
            if type == "Weight":
                dist = ((tx - sx)**2 + (ty - sy)**2) ** 0.5
                if dist <= TABLE_RADIUS:
                    state_sequence = sensor_states.get(name, {}).get("state", [])
                    if state_sequence and state_sequence[-1] == 1:
                        return True
        return False


    time_str = timer_app_instance.get_simulated_time()
    try:
        hour, _ = map(int, time_str.split(":"))
    except:
        hour = 0

    slot = None
    if 7 <= hour < 9:
        slot = "breakfast"
    elif 12 <= hour < 14:
        slot = "lunch"
    elif 20 <= hour < 22:
        slot = "dinner"

    if slot:
        meal_conditions_met = False
        pir_sensors = [sensor for sensor in sensors if sensor.type.lower() == "pir"]
        for d in devices:
            x, y, device_type, device_state = d.x, d.y, d.type, d.state
            if device_type.lower() == "oven" and device_state == 0:
                pir = find_closest_sensor_within_fov(
                    (x, y),
                    pir_sensors,
                    walls or [],
                    doors or [],
                    RADIUS_STANDARD,
                    FOV_ANGLE,
                )
                if pir:
                    pir_state = sensor_states.get(pir.name, {}).get("state", [])
                    meal_conditions_met = bool(
                        pir_state
                        and pir_state[-1] == 1
                        and weight_active_near_table()
                    )
                    if meal_conditions_met:
                        break

        if meal_conditions_met:
            if meal_active == slot:
                return slot
            if meal_detection_start[slot] is None:
                meal_detection_start[slot] = timer_app_instance.elapsed_time
            else:
                delta = (timer_app_instance.elapsed_time - meal_detection_start[slot]).total_seconds()
                if delta >= MEAL_MIN_DURATION:
                    meal_detection_start[slot] = None
                    state["meal_active"] = slot
                    return slot
            return None

        meal_detection_start[slot] = None
        if meal_active == slot:
            state["meal_active"] = None
    else:
        for key in meal_detection_start:
            meal_detection_start[key] = None
        meal_active = None
        state["meal_active"] = meal_active

    return None
