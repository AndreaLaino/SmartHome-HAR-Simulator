"""Learned activity recognition, interval logging and simulation polling."""

import logging

from house_state import HouseState
from log import log_activity_start, log_activity_end, log_end_of_simulation


logger = logging.getLogger(__name__)


def _activity_state(house_state: HouseState) -> dict:
    state = house_state.activity_state()
    state.setdefault("activity_sessions", {})
    state.setdefault("current_activities", {})
    state.setdefault("monitor_job", None)
    state.setdefault("monitor_canvas", None)
    state.setdefault("monitor_token", None)
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
    if not timer_app_instance.is_running:
        return

    from har.runtime import process_frame

    state = _activity_state(house_state)
    log_state = _activity_log_state(house_state)
    now = timer_app_instance.get_simulated_time()
    try:
        result = process_frame(
            sensor_states_store,
            runtime_sources,
            state,
            timer_app_instance.elapsed_time.total_seconds() * 60,
            house_state=house_state,
        )
    except (OSError, ValueError) as error:
        logger.warning("Learned activity recognition unavailable: %s", error)
        result = {
            "activities": [],
            "probabilities": {},
            "confidence": None,
            "status": "error",
            "abstained": True,
            "message": f"Recognition unavailable: {error}",
        }
    state["har_prediction"] = result
    detected = result.get("activities", []) if (
        result.get("status") == "partial" or (result.get("status") == "ready" and not result.get("abstained"))
    ) else []
    update_activity_state(now, detected, state, log_state)
    if activity_label is not None:
        activity_label.config(text=_learned_activity_text(result))


def _learned_activity_text(result):
    status = result.get("status")
    if status in {"ready", "partial", "uncertain"} and "activity_confidences" in result:
        confidences = result["activity_confidences"]
        activities = ", ".join(f"{name} ({confidences[name]:.0%})" for name in sorted(result["activities"]))
        text = "Activities (ML): " + (activities or ("None" if status == "ready" else "none confirmed"))
        uncertain = result.get("uncertain_activities", [])
        if uncertain:
            text += "\nUncertain: " + ", ".join(f"{name} ({confidences[name]:.0%})" for name in uncertain)
        return text + "\nConfidence is uncalibrated."
    if status == "ready" and not result.get("abstained"):
        activities = ", ".join(sorted(result.get("activities", []))) or "None"
        text = "Activities (ML): " + activities
        confidence = result.get("confidence")
        if confidence is not None:
            text += f"\nModel confidence: {confidence:.0%} (uncalibrated)"
        return text
    if status == "no_model":
        return "Activity recognition: no trained model · record labelled examples and train a model"
    if status == "uncertain" or (result.get("abstained") and status != "error"):
        return "Activity recognition: uncertain · " + result.get("message", "no confident prediction")
    return "Activity recognition: " + result.get("message", "model unavailable")


def _cancel_activity_monitor(state):
    job = state.get("monitor_job")
    canvas = state.get("monitor_canvas")
    state["monitor_job"] = None
    state["monitor_canvas"] = None
    state["monitor_token"] = None
    if job is not None and canvas is not None:
        canvas.after_cancel(job)


def monitor_activities(canvas, activity_label, timer_app_instance, sensor_states_store, house_state: HouseState, runtime_sources: dict):
    state = _activity_state(house_state)
    _cancel_activity_monitor(state)
    process_activities(
        activity_label,
        timer_app_instance,
        sensor_states_store,
        house_state,
        runtime_sources,
    )
    if timer_app_instance.is_running and canvas is not None:
        token = object()
        state["monitor_token"] = token

        def poll_activities():
            if house_state.activity_state() is not state or state["monitor_token"] is not token:
                return
            state["monitor_job"] = None
            state["monitor_canvas"] = None
            state["monitor_token"] = None
            monitor_activities(canvas, activity_label, timer_app_instance,
                               sensor_states_store, house_state, runtime_sources)

        state["monitor_canvas"] = canvas
        state["monitor_job"] = canvas.after(1000, poll_activities)


def update_activity_state(current_time, detected_activities, state: dict, log_state: dict):
    current_activities = state["current_activities"]
    activity_sessions = state["activity_sessions"]

    for activity in sorted(detected_activities):
        if activity not in current_activities:
            current_activities[activity] = current_time
            log_activity_start(activity, current_time, log_state)

    ended = [activity for activity in current_activities if activity not in detected_activities]
    for activity in ended:
        start = current_activities.pop(activity)
        activity_sessions.setdefault(activity, []).append({"start": start, "end": current_time})
        log_activity_end(activity, current_time, log_state)


def close_current_activity(timer_app_instance, activity_label=None, house_state: HouseState | None = None):
    state = _activity_state(house_state) if house_state is not None else {
        "current_activities": {},
        "activity_sessions": {},
    }
    log_state = _activity_log_state(house_state) if house_state is not None else {
        "activity_log": [],
        "active_activities": {},
    }
    _cancel_activity_monitor(state)
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
