# SmartHome HAR Simulator: repository guidance

This guide applies to the entire repository across branches. Keep it about
shared architecture, workflows, and working agreements. Put detailed feature
instructions and experiment results in the relevant documentation.

## Communication and scope

- Use the user's language and explain changes in plain, professional terms.
- Verify both the user's assumptions and your own claims against the code or
  test results. Explain disagreements; do not agree without checking.
- Before changing application code, inspect the relevant implementation, then
  describe the proposed behavior, affected areas, and meaningful tradeoffs to
  the user. Clarify consequential assumptions and wait for agreement on that
  concrete plan unless the user has already approved it in the conversation.
- Once the scope is agreed, implement and verify it without asking about each
  individual edit. Return to the user if the required behavior or scope changes.
- Ask for explicit permission before implementing an additional idea, feature,
  refactor, dependency, or other improvement outside the agreed scope, even if
  you believe it would be useful. A suggestion is not approval to implement it.
- Make the smallest coherent change that satisfies the request. Avoid unrelated
  refactoring, new abstractions, dependencies, or UI changes.
- Inspect the working tree before editing. Preserve existing user changes and
  local data; do not reset, revert, commit, or switch branches without a request.
- Read-only investigation and checks can proceed while clarifying the plan.
  Do not repeatedly request permission for work already authorized.

## Safe cleanup

- No imports or search references does **not** prove a file is useless. Check
  standalone execution, CLI entry points, dynamic callbacks, documentation,
  tests, and optional workflows before removing code.
- Include proposed removals when agreeing cleanup scope. Preserve independent
  diagnostics, AWS troubleshooting tools, conversion scripts, and optional
  integrations outside that scope.
- If usage remains uncertain, keep the utility and explain the uncertainty.
  Do not delete unrelated features as collateral cleanup.
- Preserve saved houses, recordings, trained models, reports, preferences, and
  credentials. Use temporary directories for experiments and generated fixtures.

## Project map

This is a Python 3.10+ Tkinter desktop application. Substantial editor and
simulation logic lives in root modules; `app/` contains context, controllers,
UI, persistence, and integrations. Follow the existing structure rather than
reorganizing it as part of an unrelated task.

| Area | Main paths and responsibilities |
| --- | --- |
| Startup and shared state | `main.py` starts Tk and restores preferences; `app/context.py` holds application context; `house_state.py` holds house and simulation state. |
| Main interface | `app/ui/main_ui.py`, `app/ui/tool_palette.py`, `app/ui/theme.py`: menus, canvas, tools, and appearance. |
| Editing and history | `app/ui/device_inspector.py`: selection, dragging, resizing, placement, clipboard, deletion, undo/redo, and object actions. |
| Object models and drawing | `models.py`, `point.py`, `wall.py`, `door.py`, `device.py`: data models and map-object creation/rendering. |
| Coordinates and visibility | `canvas_zoom.py`, `label_layout.py`, `utils.py`: logical/canvas coordinates, zoom, pan, snapping, labels, and shared geometry/PIR visibility. |
| Manual simulation and clock | `app/controllers/simulation.py`, `sim.py`, `timer.py`: session lifecycle, avatar/device/door interaction, sampling, pause, reset, and time advance. |
| Replay and analysis | `automatic.py`: loading recorded time series and interaction logs, replay analysis, graphs, and exports. |
| Sensors and appliance power | `sensor.py`, `consumption_profiles.py`, `common.py`: sensor updates, temperature behavior/replay, meter readings, appliance profiles, and timestamped state series. |
| Power previews | `prediction.py`: future consumption estimates from appliance profiles; this is separate from activity recognition. |
| Lighting | `lighting.py`, `app/ui/light_details.py`: normal/smart lights, manual/automatic controls, independent switches, built-in meters, and inspection. |
| Rooms | `room_recognition.py`, `app/ui/rooms.py`: room polygons/types, overlays, and meter-based room power totals. |
| Scenario persistence | `app/io/scenario.py`, `read.py`: scenario CSV saving/loading, parsing/drawing, imports, and exports. |
| File dialogs | `app/io/safe_dialog.py`, `app/io/save_dialog.py`, `scripts/ui_file_dialog.py`: platform-aware dialogs and the macOS Save workaround. |
| Local configuration | `app/preferences.py`, `app/confirmations.py`: preferences, view restoration, and persistent confirmation choices. |
| Real hardware | `app/hardware/smartmeter.py`, `app/hardware/real_sensors.py`, `app/ui/bindings.py`: Shelly/HTTP, DHT/GPIO, device bindings, acquisition, and logging. |
| Cloud imports | `app/io/aws_import.py`, `app/io/aws_telemetry.py`: optional S3 imports and telemetry normalization; cloud UI is also wired through `app/io/scenario.py`. |
| Charts and logs | `graph.py`, `log.py`, `app/logging_setup.py`: sensor/device graphs, activity intervals, interaction records, and application logs. |
| Output paths | `app/save_paths.py`: project-relative data directories and isolated simulation save sessions. |
| Consumption-cycle pipeline | `LLM/smartmeter/common/cycle_pipeline.py`, `LLM/smartmeter/common/params.py`: extraction, DTW/clustering, K selection, representative cycles, and profile artifacts, invoked through the simulator controller. |
| Profile UI and interpretation | `app/controllers/llm_smartmeter.py`: pipeline orchestration, partial-cycle completion, runtime profile selection, and archives; `LLM/smartmeter/common/llm_interpreter.py`: optional LLM descriptions. |
| Learned HAR | `har/catalogue.py`, `har/features.py`, `har/learning.py`: shared categories, temporal features, supervised models, and evaluation. |
| HAR runtime and UI | `har/runtime.py`, `har/storage.py`, `activity.py`, `app/ui/learned_har.py`, `app/ui/training_panel.py`: observations, recording, persistence, inference, activity intervals, configuration UI, and quick activity recording below the map. |
| HAR examples | `har/demo.py`, `examples/learned_har/`, `docs/learned_har_it.md`: explicit synthetic demo, example houses, and user guide. |
| Standalone utilities | `scripts/convert_item_power_to_smartmeter.py` converts input data; `irrigation_controller.py` is an independent irrigation example with placeholder hardware. |
| Dependencies and validation | `pyproject.toml`, `tests/`, `TESTING.md`, `.github/workflows/tests.yml`: packaging, dependencies, regression tests, coverage, and cross-platform CI. |

Read `README.md` for setup and user workflows and `TESTING.md` for checks.
Clustering consumption profiles, optional LLM descriptions, consumption previews,
and supervised HAR are distinct workflows; do not conflate or remove them.

## Local data and reproducibility

- `houses/` contains user scenarios; `devices/` contains local acquisition data;
  `saves/` and `logs/` contain sessions, exports, and logs. They may be absent in
  a fresh checkout and must not be treated as disposable merely because ignored.
- `har_data/` holds the shared catalogue, recordings, models, and reports. A
  scenario CSV stores the selected model ID, not the complete model. Preserve
  both scenario and HAR data when moving an experiment.
- `settings.json` and `sensor_map.json` hold local preferences and hardware
  bindings. Do not commit credentials, private addresses, or personal datasets.
- Generated appliance outputs and profile catalogues may be used at runtime.
  Check producers and consumers before changing their paths, schemas, or names.
- Record input datasets, parameters, seeds, clustering mode/K, model identity,
  and output location when needed to reproduce an experiment. Do not overwrite
  unrelated runs to try an idea.

## Editor, simulation, and lifecycle

- Store geometry in logical coordinates and use `canvas_zoom.py` conversions
  for pointer events. Zooming must not change saved geometry.
- Keep point attachments, wall/door endpoints, sensor/device links, clipboard
  copies, and undo/redo consistent when editing related objects.
- Preserve distinct editor/construction and Manual interaction bindings.
  Starting Manual mode must safely cancel unfinished placement.
- PIR visibility and its displayed field of view must agree about walls and
  closed doors. Preserve room overrides when updating geometry or room labels.
- Preserve simulated-time conversions across sampling, logs, profiles, replay,
  and HAR. Do not confuse wall-clock time with simulation time.
- Keep Tk calls on the UI thread and expensive work off it. On reset, house
  change, or exit, stop loggers and cancel stale timer/sensor/activity callbacks.
  Delayed cleanup from a previous session must not modify its replacement.

## Measurements, lighting, and persistence

- Preserve acquisition/replay timestamps, sampling assumptions, CSV schemas,
  and measurement units through imports, simulation, graphs, and exports.
- A valid zero reading is an observation. Do not substitute a fallback merely
  because the measured value is falsy; distinguish missing from zero.
- Power is instantaneous watts; energy is accumulated consumption. Room power
  comes from meter readings, not the sum of appliance rated powers. Assign each
  reading to at most one room using the existing linked-device/position rules.
- Light type (normal/smart) and control (manual/automatic) are independent.
  Preserve separately movable switches and explicitly empty switch lists.
- Smart-light built-in meters follow their owning light. An external meter
  must not duplicate the same light's power in room totals.
- Preserve backward-compatible scenario loading and atomic file replacement.
  Keep the macOS Save-dialog workaround; the native dialog previously crashed
  the app. Do not replace it casually during UI cleanup.
- Preserve recordings before reset, opening another house, or exit. Failed
  saves must retain the previous file and recoverable in-memory observations.

## Activity recognition invariants

- Categories describe reusable capabilities and measurements, not activities.
  `Device_Cooking` alone must never imply that a person is cooking.
- Recognize activities from observed sensor/device values through the trained
  model. Do not introduce appliance-name rules or manual device-to-activity
  transfer mappings. With no active model, report that state clearly.
- Supervised labels describe what the person actually did. Keep labels,
  predictions, house/device identities, and future observations out of features.
- Support multiple categories per object and consistent measurement units.
  Reuse the model's frozen category/feature schema across houses.
- Train only on source-house training sessions. Split by independent simulation,
  not individual frames; recordings from one simulation must stay together.
- Keep target-house data out of training, schema selection, and tuning. Use it
  to evaluate transfer. Detect duplicated source traces and session leakage.
- Distinguish measured test accuracy from prediction confidence. Forest vote
  confidence is uncalibrated; synthetic demo results do not measure performance
  on real people. Preserve uncertainty and abstention behavior.

## Validation and handoff

- Use the existing environment and dependencies from `pyproject.toml`. Setup
  and application launch commands, when needed, are:

  ```bash
  python -m pip install -e ".[test]"
  python main.py
  ```

- Run focused tests for changed behavior, then relevant integration checks.
  For broad changes, run the headless suite from the repository root:

  ```bash
  python -m unittest discover -s tests -q
  ```

- Choose focused tests by subsystem using the coverage table in `TESTING.md`:
  editor/history/geometry, scenarios/dialogs, simulation/replay, lights/rooms,
  sensors/hardware, cloud, profiles/LLM, or HAR. Do not validate unrelated areas
  solely with HAR tests.
- For widget or dialog changes, also run real Tk smoke tests on a desktop
  (Linux requires a display or Xvfb):

  ```bash
  SIMULATOR_GUI_TESTS=1 python -m unittest tests.test_tk_smoke -q
  ```

- For HAR UI changes, include `tests.test_learned_har_ui` in that GUI run.
  Shell syntax differs on Windows; see `TESTING.md`. Match platform checks to
  the change and consult the CI workflow for its current matrix.
- Tests must mock hardware, AWS, HTTP, and LLM boundaries and use temporary data.
  Do not contact live services merely to validate local changes.
- Preserve local operation without credentials or physical sensors. Exercise
  real integrations only when the user has authorized that specific check.
- Add regression tests for substantive bug fixes or changed behavior. Do not
  weaken assertions or remove tests merely to obtain a passing result.
- Check `git diff --check` and review the final diff for unrelated changes.
  Documentation-only changes need content/link checks, not application tests.
- Report what changed, which checks actually ran, and material limitations.
  Passing tests do not justify claiming there are no bugs.
