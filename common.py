import logging

logger = logging.getLogger("common")

def update_sensor_states(name, state, sensor_states, timestamp):
    if name not in sensor_states:
        sensor_states[name] = {'time': [], 'state': []}
    sensor_states[name]['time'].append(timestamp)
    sensor_states[name]['state'].append(state)
    logger.debug(f"Sensor state updated: {name} -> {state}")
