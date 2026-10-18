# Testing the simulator

Run commands from the repository root using Python 3.10 or later with Tkinter
installed. On Windows, the existing environment can be used directly as
`.venv\Scripts\python.exe` if `python` points to the Windows Store launcher.

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
```

The default suite runs without opening windows. Hardware, HTTP, AWS, and LLM
requests are mocked at their boundaries. Data-dependent tests create their own
small CSV, JSON, and pickle fixtures in temporary directories. They do not need
credentials, recorded household data, or user preferences. Real Tk smoke tests
are opt-in and are skipped by the default command.

## Coverage

```bash
python -m coverage run -m unittest discover -s tests -v
python -m coverage report
python -m coverage html
```

Open `htmlcov/index.html` to inspect uncovered lines and branches. The report
includes application code and UI code; headless tests do not execute every
dialog or widget construction path. Coverage is a diagnostic, not a guarantee
that real devices, cloud services, or graphical behavior work in every setting.

## Focused runs

```bash
python -m unittest discover -s tests -p test_simulation_runtime.py -v
python -m unittest discover -s tests -p test_scenario_persistence.py -v
python -m unittest discover -s tests -p "test_sensor_*.py" -v
python -m unittest discover -s tests -p test_automatic_replay.py -v
python -m unittest discover -s tests -p test_cloud_import.py -v
```

| Area | Regression and integration tests |
| --- | --- |
| Application lifecycle | `test_application_lifecycle.py`, `test_preferences.py`, `test_confirmations.py` |
| Clock, sensor ticks, fast-forward, session isolation | `test_simulation_runtime.py`, `test_core_simulator.py` |
| Saved home through appliance cycle, HAR history, and log replay | `test_simulator_workflow.py` |
| Scenario persistence, malformed inputs, save sessions | `test_scenario_persistence.py`, `test_scenario_io.py` |
| Activity and presence recognition | `test_activity.py` |
| Geometry, zoom, rooms, editing and selection | `test_geometry.py`, `test_room_recognition.py`, `test_device_inspector.py`, `test_segment_creation.py`, `test_room_ui.py`, `test_tool_palette.py` |
| Temperature, consumption, prediction, profiles, hardware | `test_temperature.py`, `test_sensor_regressions.py`, `test_sensor_profile_loading.py`, `test_hardware_integrations.py` |
| Graph data, interaction logging, replay and export | `test_graph_data.py`, `test_session_logging.py`, `test_automatic_replay.py` |
| Cloud import and optional LLM processing | `test_cloud_import.py`, `test_llm_smartmeter.py`, `test_cycle_pipeline.py` |
| Actual Tk widgets | `test_tk_smoke.py` (opt-in) |

## Real Tk smoke tests

These tests build actual widgets in a withdrawn window and exercise the
application with temporary storage and hardware access disabled. Run them on a
desktop or a virtual display.

PowerShell:

```powershell
$env:SIMULATOR_GUI_TESTS = "1"
python -m unittest discover -s tests -p test_tk_smoke.py -v
Remove-Item Env:SIMULATOR_GUI_TESTS
```

Linux:

```bash
SIMULATOR_GUI_TESTS=1 xvfb-run -a python -m unittest discover -s tests -p test_tk_smoke.py -v
```

CI runs the headless suite on Windows and Linux with Python 3.10 and 3.12,
uploads coverage XML reports, and runs the Tk smoke tests under Xvfb on Linux.
Physical sensor wiring, actual AWS permissions, and live LLM responses require
separate checks in the deployment environment.
