__all__ = ["build_home_ui", "open_bind_ip_ui", "open_bind_gpio_sensors_ui"]


def __getattr__(name):
    """Load public UI helpers lazily so UI submodules can share theme code."""
    if name == "build_home_ui":
        from .main_ui import build_home_ui

        return build_home_ui
    if name in {"open_bind_ip_ui", "open_bind_gpio_sensors_ui"}:
        from .bindings import open_bind_gpio_sensors_ui, open_bind_ip_ui

        return {
            "open_bind_ip_ui": open_bind_ip_ui,
            "open_bind_gpio_sensors_ui": open_bind_gpio_sensors_ui,
        }[name]
    raise AttributeError(name)
