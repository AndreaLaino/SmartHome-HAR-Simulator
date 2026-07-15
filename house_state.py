from __future__ import annotations


class HouseState:
    """Single simulation state container used by sensor adapters.

    values: canonical store for both persistent and runtime state.
    context: backward-compatible alias of values["runtime"].
    """

    def __init__(self, initial_values: dict | None = None, initial_context: dict | None = None):
        self.values: dict = {}
        if initial_values is not None:
            self.values.update(initial_values)
        runtime = self.values.get("runtime")
        if not isinstance(runtime, dict):
            runtime = {}
            self.values["runtime"] = runtime
        if initial_context is not None:
            runtime.update(initial_context)

        # Backward compatibility only: context is an alias of values["runtime"].
        self.context: dict = runtime

    def runtime(self) -> dict:
        """Return mutable runtime store from values."""
        runtime = self.values.get("runtime")
        if isinstance(runtime, dict):
            return runtime
        self.values["runtime"] = {}
        self.context = self.values["runtime"]
        return self.values["runtime"]

    def _store(self, key: str, default: dict | None = None) -> dict:
        store = self.values.get(key)
        if isinstance(store, dict):
            return store
        self.values[key] = {} if default is None else dict(default)
        return self.values[key]

    def sensor_states(self) -> dict:
        """Return mutable sensor_states store from values."""
        return self._store("sensor_states")

    def active_cycles(self) -> dict:
        """Return mutable active_cycles store from values."""
        return self._store("active_cycles")

    def sim_state(self) -> dict:
        """Return mutable simulation runtime state."""
        return self._store("sim_state")

    def activity_state(self) -> dict:
        """Return mutable activity-detection state."""
        return self._store("activity_state")

    def activity_log_state(self) -> dict:
        """Return mutable activity-log state."""
        return self._store("activity_log_state")

    def interaction_log_state(self) -> dict:
        """Return mutable interaction-log state."""
        return self._store("interaction_log_state")

    def automatic_state(self) -> dict:
        """Return mutable automatic-mode UI state."""
        return self._store("automatic_state")

    def set_context(self, **kwargs):
        """Backward-compatible runtime update helper."""
        self.runtime().update(kwargs)

    def set_runtime(self, **kwargs):
        """Update runtime values in-place."""
        self.runtime().update(kwargs)

    def replace_runtime(self, **kwargs):
        """Replace runtime store atomically with the provided values."""
        self.values["runtime"] = dict(kwargs)
        self.context = self.values["runtime"]

    def runtime_view(self, **overrides) -> dict:
        """Return a read-only snapshot-like runtime dict with optional overrides."""
        runtime = dict(self.runtime())
        runtime.update(overrides)
        return runtime

    def clear_runtime_state(self) -> None:
        """Clear all per-run simulation data while keeping this object usable."""
        self.values.clear()
        self.values["runtime"] = {}
        self.context = self.values["runtime"]
