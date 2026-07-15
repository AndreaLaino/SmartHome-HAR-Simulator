#sim.py
from PIL import Image, ImageTk
from datetime import datetime, timedelta
from door import interaction_with_door, draw_all_doors
from sensor import (
    TemperatureSensorAdapter,
    PIRSensorAdapter,
    SmartMeterSensorAdapter,
    WeightSensorAdapter,
    SwitchSensorAdapter,
    get_replay_temperature,
    is_temperature_sensor_changing,
    prime_llm_cycle_for_sensor,
)
from utils import find_closest_sensor_within_fov, update_devices_consumption, find_closest_sensor_without_intersection, find_switch_sensors_by_doors, calculate_distance, update_sensor_color, update_temperature_sensor_color
from common import update_sensor_states
from log import log_move, log_sensor_event, log_device_event, log_door_event, log_llm_cycle_event
from house_state import HouseState
from canvas_zoom import event_to_logical, to_canvas

MAX_DISTANCE = 230
FOV_ANGLE = 60
SMARTMETER_ACTIVE_THRESHOLD_W = 1.0

PER_SECOND_SENSOR_SAMPLING = True
PER_SECOND_SENSOR_TYPES = {"PIR", "Switch", "Weight"}


def _prepare_house_state(
    house_state: HouseState,
    *,
    devices_list,
    delta_seconds=None,
    current_datetime=None,
) -> HouseState:
    house_state.replace_runtime(
        devices=devices_list,
        delta_seconds=delta_seconds,
        current_datetime=current_datetime,
    )
    return house_state


def _consumption_to_bin_state(consumption: float | None, threshold_w: float = SMARTMETER_ACTIVE_THRESHOLD_W) -> int:
    return 1 if (consumption or 0.0) > threshold_w else 0


def _append_unique_sample(buffer, ts, state_val, consumption_val=None):
    def _to_float(v):
        try:
            return float(round(float(v), 2))
        except Exception:
            return float("nan")

    state_float = _to_float(state_val)
    buffer.setdefault('time', [])
    buffer.setdefault('state', [])
    if 'consumption' in buffer:
        buffer.setdefault('consumption', [])

    if buffer['time'] and buffer['time'][-1] == ts:
        buffer['state'][-1] = state_float
        if 'consumption' in buffer and consumption_val is not None:
            buffer['consumption'][-1] = _to_float(consumption_val)
    else:
        buffer['time'].append(ts)
        buffer['state'].append(state_float)
        if 'consumption' in buffer and consumption_val is not None:
            buffer['consumption'].append(_to_float(consumption_val))


def _sim_state(house_state: HouseState) -> dict:
    state = house_state.sim_state()
    state.setdefault("last_temp_elapsed", None)
    state.setdefault("avatar_image", None)
    state.setdefault("avatar_id", None)
    state.setdefault("avatar_position", None)
    state.setdefault("active_pir_sensors", [])
    return state


def _collect_temperature_updates(
    house_state: HouseState,
    s_sensors,
    d_devices,
    *,
    delta_seconds=None,
    current_datetime=None,
):
    temp_adapter = TemperatureSensorAdapter()
    updates = []

    for sensor in s_sensors:
        if sensor.type != "Temperature":
            continue

        heating_factor = _compute_heating(sensor, d_devices)
        sensor_name, new_state = temp_adapter.update(
            house_state,
            sensor,
            heating_factor=heating_factor,
            delta_seconds=delta_seconds,
            current_datetime=current_datetime,
            active_devices=d_devices,
            render=False,
        )
        reference_state = get_replay_temperature(sensor_name, current_datetime)
        updates.append((sensor, sensor_name, float(new_state), reference_state))

    return updates


def _apply_temperature_state_updates(temperature_updates):
    for sensor, _, new_state, _ in temperature_updates:
        sensor.state = float(new_state)


def _store_temperature_updates(state_store, timestamp, temperature_updates):
    for _, sensor_name, new_state, reference_state in temperature_updates:
        update_sensor_states(sensor_name, new_state, state_store, timestamp)
        buffer = state_store[sensor_name]
        buffer.setdefault('type', 'Temperature')
        real_states = buffer.setdefault('real_state', [])
        while len(real_states) < len(buffer['state']) - 1:
            real_states.append(None)
        real_states.append(None if reference_state is None else float(reference_state))


def _compute_heating(sensor, devices):
    for device in devices:
        if device.type == "Oven" and device.state == 1:
            dist = calculate_distance(sensor.x, sensor.y, device.x, device.y)
            if dist <= 50:
                return 1
    return 0


def _update_smartmeter_sensors(
    house_state: HouseState,
    s_sensors,
    state_store,
    timestamp,
    *,
    devices_list,
    delta_seconds,
    current_datetime,
):
    smartmeter_adapter = SmartMeterSensorAdapter()
    updated_smartmeters = set()
    smartmeter_updates = []

    for sensor in s_sensors:
        if sensor.type != "Smart Meter":
            continue

        sensor_name, new_consumption = smartmeter_adapter.update(
            house_state,
            sensor,
            devices_list=devices_list,
            delta_seconds=delta_seconds,
            current_datetime=current_datetime,
            render=False,
        )
        smartmeter_updates.append((sensor, sensor_name, float(new_consumption)))

    for sensor, sensor_name, new_consumption in smartmeter_updates:
        sensor.consumption = float(new_consumption)
        associated_device = sensor.associated_device

        if sensor_name not in state_store:
            state_store[sensor_name] = {
                'time': [],
                'state': [],
                'consumption': [],
                'type': 'Smart Meter',
                'associated_device': associated_device,
            }
        else:
            state_store[sensor_name].setdefault('type', 'Smart Meter')
            state_store[sensor_name].setdefault('associated_device', associated_device)
            state_store[sensor_name].setdefault('consumption', [])

        bin_state = _consumption_to_bin_state(new_consumption)
        _append_unique_sample(state_store[sensor_name], timestamp, bin_state, round(new_consumption, 2))

        log_sensor_event(
            house_state,
            timestamp,
            sensor_name,
            "Smart Meter",
            int(sensor.x),
            int(sensor.y),
            float(round(new_consumption, 2)),
            f"device:{associated_device}",
        )
        updated_smartmeters.add(sensor_name)

    return updated_smartmeters


def _render_sensor_states(canvas, s_sensors):
    if canvas is None:
        return

    for sensor in s_sensors:
        sensor_type = sensor.type
        if sensor_type == "Temperature":
            update_temperature_sensor_color(
                canvas,
                sensor.name,
                changing=is_temperature_sensor_changing(sensor.name),
            )
        elif sensor_type == "Smart Meter":
            update_sensor_color(
                canvas,
                sensor.name,
                float(sensor.consumption or 0.0),
                SMARTMETER_ACTIVE_THRESHOLD_W,
            )
        else:
            update_sensor_color(canvas, sensor.name, float(sensor.state), float(sensor.min_val))


def _render_device_states(canvas, d_devices):
    if canvas is None:
        return
    for device in d_devices:
        canvas.itemconfig(device.name, fill="red" if int(device.state) == 0 else "green")


def _render_interaction_scene(canvas, d_devices, s_sensors, d_doors):
    _render_device_states(canvas, d_devices)
    _render_sensor_states(canvas, s_sensors)
    draw_all_doors(canvas, d_doors)


def _snapshot_device_smartmeters(
    house_state: HouseState,
    s_sensors,
    d_devices,
    state_store,
    timestamp,
    updated_smartmeters,
):
    for device in d_devices:
        dev_name = device.name
        current_cons = device.current_consumption

        for sensor in s_sensors:
            if sensor.type != "Smart Meter" or sensor.associated_device != dev_name:
                continue

            sensor_name = sensor.name
            if sensor_name in updated_smartmeters:
                continue

            if sensor_name not in state_store:
                state_store[sensor_name] = {
                    'time': [],
                    'state': [],
                    'consumption': [],
                    'type': 'Smart Meter',
                    'associated_device': dev_name,
                }
            else:
                state_store[sensor_name].setdefault('consumption', [])

            bin_state = _consumption_to_bin_state(current_cons)
            _append_unique_sample(state_store[sensor_name], timestamp, bin_state, round(current_cons, 2))
            sensor.consumption = float(current_cons)

            log_sensor_event(
                house_state,
                timestamp,
                sensor_name,
                "Smart Meter",
                int(sensor.x),
                int(sensor.y),
                float(round(current_cons, 2)),
                f"device:{dev_name}",
            )


def _sample_binary_sensors(house_state: HouseState, s_sensors, state_store, timestamp, fast: bool):
    if (not PER_SECOND_SENSOR_SAMPLING) or fast:
        return

    for sensor in s_sensors:
        sensor_type = sensor.type
        if sensor_type not in PER_SECOND_SENSOR_TYPES:
            continue

        sensor_name = sensor.name
        sx = int(sensor.x)
        sy = int(sensor.y)
        current_state = int(round(float(sensor.state)))

        if sensor_name not in state_store:
            state_store[sensor_name] = {'time': [], 'state': [], 'type': sensor_type}
        else:
            state_store[sensor_name].setdefault('type', sensor_type)

        append_unique_binary(state_store[sensor_name], timestamp, current_state, sensor_type)
        log_sensor_event(house_state, timestamp, sensor_name, sensor_type, sx, sy, int(current_state), "per-second-sample")


def append_unique_binary(buffer, ts, state_val, type=None):
    """ Binary state (0/1) with dedup on timestamp:
    - if ts equals and same value -> overwrite (no unnecessary duplicates)
    - if ts equal but value different - > append (preserve edge 0↔1)"""
    s = 1 if int(round(float(state_val))) else 0

    if 'type' not in buffer and type:
        buffer['type'] = type
    buffer.setdefault('time', [])
    buffer.setdefault('state', [])

    if buffer['time'] and buffer['time'][-1] == ts:
        if buffer['state'][-1] == s:
            buffer['state'][-1] = s
        else:
            buffer['time'].append(ts)
            buffer['state'].append(s)
    else:
        buffer['time'].append(ts)
        buffer['state'].append(s)


def _record_binary_sensor_event(house_state: HouseState, state_store, timestamp, sensor, sensor_type: str, sensor_state, reason: str):
    sensor_name = sensor.name
    if sensor_name not in state_store:
        state_store[sensor_name] = {'time': [], 'state': [], 'type': sensor_type}
    else:
        state_store[sensor_name].setdefault('type', sensor_type)

    append_unique_binary(state_store[sensor_name], timestamp, sensor_state, sensor_type)
    log_sensor_event(
        house_state,
        timestamp,
        sensor_name,
        sensor_type,
        int(sensor.x),
        int(sensor.y),
        int(round(float(sensor_state))),
        reason,
    )


def _handle_pir_interaction(
    house_state: HouseState,
    sim_state: dict,
    s_sensors,
    walls,
    d_doors,
    state_store,
    timestamp,
    click_pos,
):
    pir_adapter = PIRSensorAdapter()
    closest_sensor_pir = find_closest_sensor_within_fov(click_pos, s_sensors, walls, d_doors, MAX_DISTANCE, FOV_ANGLE)
    next_active = []

    for sensor in sim_state["active_pir_sensors"]:
        if closest_sensor_pir and sensor.name == closest_sensor_pir.name:
            next_active.append(sensor)
            continue
        _, sensor_state = pir_adapter.update(house_state, sensor, 0, render=False)
        sensor.state = float(sensor_state)
        _record_binary_sensor_event(house_state, state_store, timestamp, sensor, "PIR", sensor_state, "auto-off-prev")

    if closest_sensor_pir:
        _, sensor_state = pir_adapter.update(house_state, closest_sensor_pir, 1, render=False)
        closest_sensor_pir.state = float(sensor_state)
        _record_binary_sensor_event(house_state, state_store, timestamp, closest_sensor_pir, "PIR", sensor_state, "closest_in_fov")
        next_active = [closest_sensor_pir]

    sim_state["active_pir_sensors"] = next_active
    return closest_sensor_pir


def _handle_weight_interaction(house_state: HouseState, s_sensors, state_store, timestamp, click_pos):
    weight_adapter = WeightSensorAdapter()
    click_x, click_y = click_pos

    for sensor in s_sensors:
        if sensor.type != "Weight":
            continue

        distance = calculate_distance(click_x, click_y, sensor.x, sensor.y)
        target_state = 1 if distance < 10 else 0
        reason = "click_nearby" if target_state == 1 else "auto_off"
        _, sensor_state = weight_adapter.update(house_state, sensor, target_state, render=False)
        sensor.state = float(sensor_state)
        _record_binary_sensor_event(house_state, state_store, timestamp, sensor, "Weight", sensor_state, reason)


def _handle_switch_interaction(house_state: HouseState, d_doors, s_sensors, state_store, timestamp):
    switch_adapter = SwitchSensorAdapter()
    switches_by_door = find_switch_sensors_by_doors(d_doors, s_sensors)

    for door, associated_sensors, door_state in switches_by_door:
        door_name = f"{door.x1},{door.y1}-{door.x2},{door.y2}"
        for sensor in associated_sensors:
            _, sensor_state = switch_adapter.update(house_state, sensor, door_state, render=False)
            sensor.state = float(sensor_state)
            _record_binary_sensor_event(
                house_state,
                state_store,
                timestamp,
                sensor,
                "Switch",
                sensor_state,
                f"sync_with_door:{door_name}",
            )


def _handle_device_toggle_at_click(canvas, house_state: HouseState, timer_app_instance, runtime_sources: dict, click_pos, *, render=False):
    click_x, click_y = click_pos
    d_devices = runtime_sources["devices"]

    for device in d_devices:
        if abs(device.x - click_x) <= 5 and abs(device.y - click_y) <= 5:
            toggle_device_state(
                canvas,
                None,
                house_state,
                timer_app_instance,
                runtime_sources,
                click_x,
                click_y,
                render=render,
            )
            return True
    return False


def _prepare_interaction_context(canvas, event, timer_app_instance, house_state: HouseState, runtime_sources: dict):
    sim_state = _sim_state(house_state)
    click_x, click_y = event_to_logical(canvas, event)

    if not timer_app_instance.is_running:
        print("Error: Simulation not started. Press 'Start Simulation' before interact.")
        return None

    if sim_state["avatar_image"] is None:
        sim_state["avatar_image"] = initialize_avatar_image()

    if sim_state["avatar_id"] is not None:
        canvas.delete(sim_state["avatar_id"])
    avatar_x, avatar_y = to_canvas(canvas, click_x, click_y)
    sim_state["avatar_id"] = canvas.create_image(avatar_x, avatar_y, image=sim_state["avatar_image"])
    sim_state["avatar_position"] = (float(click_x), float(click_y))

    simulated_time = timer_app_instance.get_simulated_time()
    current_date = timer_app_instance.current_date
    timestamp = f"{current_date} {simulated_time}"
    log_move(house_state, timestamp, int(click_x), int(click_y))

    s_sensors = runtime_sources["sensors"]
    if not s_sensors:
        print("No sensors exists.")
        return None

    return {
        "sim_state": sim_state,
        "house_state": house_state,
        "timestamp": timestamp,
        "current_datetime": get_simulation_datetime(timer_app_instance),
        "click_pos": (click_x, click_y),
        "sensors": s_sensors,
        "walls": runtime_sources["walls"],
        "devices": runtime_sources["devices"],
        "doors": runtime_sources["doors"],
        "state_store": house_state.sensor_states(),
    }


def _run_interaction_flow(canvas, event, timer_app_instance, runtime_sources: dict, interaction_ctx: dict):
    house_state = interaction_ctx["house_state"]
    sim_state = interaction_ctx["sim_state"]
    s_sensors = interaction_ctx["sensors"]
    walls = interaction_ctx["walls"]
    d_devices = interaction_ctx["devices"]
    d_doors = interaction_ctx["doors"]
    state_store = interaction_ctx["state_store"]
    timestamp = interaction_ctx["timestamp"]
    click_pos = interaction_ctx["click_pos"]

    closest_sensor_pir = _handle_pir_interaction(
        house_state,
        sim_state,
        s_sensors,
        walls,
        d_doors,
        state_store,
        timestamp,
        click_pos,
    )

    if closest_sensor_pir:
        _handle_device_toggle_at_click(
            canvas,
            house_state,
            timer_app_instance,
            runtime_sources,
            click_pos,
            render=False,
        )
    else:
        closest_temperature_sensor = find_closest_sensor_without_intersection(click_pos, s_sensors, walls)
        if closest_temperature_sensor:
            _handle_device_toggle_at_click(
                canvas,
                house_state,
                timer_app_instance,
                runtime_sources,
                click_pos,
                render=False,
            )

    _handle_weight_interaction(house_state, s_sensors, state_store, timestamp, click_pos)
    interaction_with_door(canvas, event, d_doors, render=False)
    _handle_switch_interaction(house_state, d_doors, s_sensors, state_store, timestamp)
    _render_interaction_scene(canvas, d_devices, s_sensors, d_doors)


def initialize_avatar_image():
    image = Image.open("images/omino.png")
    image = image.resize((20, 27))
    return ImageTk.PhotoImage(image)

def start_simulation(canvas, timer_app_instance, activity_label, house_state: HouseState, runtime_sources: dict):
    state = _sim_state(house_state)
    if state["avatar_image"] is None:
        state["avatar_image"] = initialize_avatar_image()
    if not timer_app_instance.is_running:
        print("Simulation started.")
        timer_app_instance.start_stop()
        timer_app_instance.elapsed_time = timedelta()
    else:
        print("Simulation already running.")
        update_sensors(canvas, timer_app_instance, activity_label, house_state, runtime_sources)

def stop_simulation(timer_app_instance):
    if timer_app_instance.is_running:
        print("Simulation stopped.")
        timer_app_instance.start_stop()
        timer_app_instance.elapsed_time = timedelta()
    else:
        print("Simulation already stopped.")

def get_simulation_datetime(timer_app_instance):
    simulated_time = timer_app_instance.get_simulated_time()
    current_date = timer_app_instance.current_date
    return datetime.strptime(f"{current_date} {simulated_time}", "%Y-%m-%d %H:%M")

# Handle user click: move avatar, pick closest PIR in FOV, fallback actions, and logging.
def interaction(canvas, timer_app_instance, event, activity_label, house_state: HouseState, runtime_sources: dict):
    interaction_ctx = _prepare_interaction_context(
        canvas,
        event,
        timer_app_instance,
        house_state,
        runtime_sources,
    )
    if interaction_ctx is None:
        return
    _run_interaction_flow(canvas, event, timer_app_instance, runtime_sources, interaction_ctx)

def toggle_device_state(canvas, event, house_state, timer_app_instance, runtime_sources: dict, x=None, y=None, *, render=True):
    """toggle device state on click and log the event"""
    if (x is None or y is None) and event is None:
        raise ValueError("toggle_device_state requires either click coordinates or an event")
    if x is None or y is None:
        logical_x, logical_y = event_to_logical(canvas, event)
        x = int(logical_x)
        y = int(logical_y)

    s_sensors = runtime_sources["sensors"]
    d_devices = runtime_sources["devices"]

    simulated_time = timer_app_instance.get_simulated_time()
    current_date = timer_app_instance.current_date
    current_timestamp = f"{current_date} {simulated_time}"
    simulation_datetime = get_simulation_datetime(timer_app_instance)
    cycles_store = house_state.active_cycles()
    smartmeter_sensors = [s for s in s_sensors if s.type == "Smart Meter"]

    for i, device in enumerate(d_devices):
        dev_name, dx, dy, type_d, power, dev_state, min_c, max_c = device.name, device.x, device.y, device.type, device.power, device.state, device.min_consumption, device.max_consumption
        current_cons, cons_dir = device.current_consumption, device.consumption_direction
        
        if abs(dx - x) <= 5 and abs(dy - y) <= 5:
            new_state = 0 if dev_state == 1 else 1

            if new_state == 1:
                current_cons = min_c
                cons_dir = 1
                cycle_entry = {
                    "start_time": simulation_datetime,
                    "cycle_type": type_d,
                }
                assoc_sensor = next((s for s in smartmeter_sensors if s.associated_device == dev_name), None)
                if assoc_sensor is not None:
                    llm_cycle = prime_llm_cycle_for_sensor(
                        assoc_sensor.name,
                        type_d,
                        simulation_datetime,
                        associated_device=dev_name,
                        force_new_prediction=True,
                    )
                    if isinstance(llm_cycle, dict):
                        cycle_entry["llm"] = llm_cycle
                        log_llm_cycle_event(
                            house_state,
                            current_timestamp,
                            assoc_sensor.name,
                            llm_cycle,
                        )
                cycles_store[dev_name] = cycle_entry
            else:
                if type_d != "Fridge" and dev_name in cycles_store:
                    del cycles_store[dev_name]
                # Do not change current_cons for Fridge: continue the descent

            # Update device
            device.state = new_state
            device.current_consumption = current_cons
            device.consumption_direction = cons_dir
            
            if render and canvas is not None:
                canvas.itemconfig(dev_name, fill="red" if new_state == 0 else "green")

            # toggle device
            log_device_event(house_state, current_timestamp, dev_name, type_d, int(dx), int(dy), int(new_state), "user_toggle_at_click")  # [LOG]
            break

def update_sensors(canvas, timer_app_instance, activity_label, house_state: HouseState, runtime_sources: dict, *, schedule_next=True, force=False, delta_override=None, fast=False):
    """update sensors based on elapsed time and log events"""
    state = _sim_state(house_state)

    if (not timer_app_instance.is_running) and (not force):
        print("Error: Simulation not started.")
        return

    ui_canvas = None if fast else canvas

    s_sensors = runtime_sources["sensors"]
    d_devices = runtime_sources["devices"]

    current_elapsed = timer_app_instance.elapsed_time
    if state["last_temp_elapsed"] is None:
        state["last_temp_elapsed"] = current_elapsed

    if delta_override is not None:
        delta_seconds = float(delta_override)
        state["last_temp_elapsed"] = current_elapsed
    else:
        delta_seconds = (current_elapsed - state["last_temp_elapsed"]).total_seconds()
        state["last_temp_elapsed"] = current_elapsed

        if delta_seconds <= 0:
            delta_seconds = 1.0

    simulated_time = timer_app_instance.get_simulated_time()
    current_date = timer_app_instance.current_date
    timestamp = f"{current_date} {simulated_time}"
    current_datetime = get_simulation_datetime(timer_app_instance)

    house_state = _prepare_house_state(
        house_state,
        devices_list=d_devices,
        delta_seconds=delta_seconds,
        current_datetime=current_datetime,
    )
    state_store = house_state.sensor_states()
    temperature_updates = _collect_temperature_updates(
        house_state,
        s_sensors,
        d_devices,
        delta_seconds=delta_seconds,
        current_datetime=current_datetime,
    )
    _apply_temperature_state_updates(temperature_updates)
    _store_temperature_updates(state_store, timestamp, temperature_updates)
    # update devices consumption
    update_devices_consumption(
        ui_canvas,
        d_devices,
        delta_seconds,
        timer_app_instance,
        active_cycles_store=house_state.active_cycles(),
    )

    # Smart Meter sensors must read the already-updated device consumption/state
    # for the current tick; otherwise they can remain one step behind.
    updated_smartmeters = _update_smartmeter_sensors(
        house_state,
        s_sensors,
        state_store,
        timestamp,
        devices_list=d_devices,
        delta_seconds=delta_seconds,
        current_datetime=current_datetime,
    )

    _snapshot_device_smartmeters(house_state, s_sensors, d_devices, state_store, timestamp, updated_smartmeters)
    _sample_binary_sensors(house_state, s_sensors, state_store, timestamp, fast)
    _render_device_states(canvas, d_devices)
    _render_sensor_states(canvas, s_sensors)

    # normal scheduling
    if schedule_next and timer_app_instance.is_running and canvas is not None:
        canvas.after(
            1000,
            lambda: update_sensors(canvas, timer_app_instance, activity_label, house_state, runtime_sources),
        )

        
