# SmartHome HAR Simulator

Smart-home simulator for human activity recognition (HAR), virtual sensors,
appliance consumption profiles, real sensor integration, and Smart Meter
cycle analysis.

Repository: https://github.com/AndreaLaino/SmartHome-HAR-Simulator

The project provides a Tkinter interface for creating and simulating domestic
scenarios. It also includes a clustering pipeline for appliance consumption
cycles and an optional LLM module that turns the generated profiles into
user-readable descriptions.

## Requirements

- Python 3.10 or later
- Tkinter available in the Python installation
- The dependencies listed in `pyproject.toml`
- A desktop environment to run the graphical interface

The AWS modules require valid AWS credentials only when cloud import or
telemetry features are used. Local simulation and the automated tests do not
require AWS credentials.

## Installation

From the project directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

On Linux, install the system Tkinter package if needed, for example
`python3-tk` on Debian-based distributions.

## Run The Simulator

```bash
python main.py
```

The application opens the scenario editor. Existing scenarios can be loaded
from the `File` menu, while the `Scenario` menu can be used to create points,
walls, doors, sensors, and devices from scratch.

The main simulation workflows are available from the `Simulation` menu:

- `Manual` for direct interaction with the scenario;
- `Automatic` for replaying activity and sensor data;
- `LLM Smart Meter` for extracting and reusing appliance consumption cycles.

Personal preferences are available from the `Settings` menu. They control the
manual simulation's initial time, whether `saved.csv` or a selected scenario
CSV is loaded at application startup, whether Manual mode is activated
automatically, and whether PIR fields of view are shown. These choices are stored in
the local, Git-ignored `settings.json` file. Visible PIR fields are clipped by
walls and closed doors to match the simulator's detection behavior, and their
transparency can be adjusted from the same menu. The window state and canvas
zoom position are also restored between application launches. Room outlines and
recognized room types can be shown or hidden with **Settings > Show room types**.

Confirmation dialogs for closing the application, clearing the current home,
deleting each map-object type, and saving scenarios include a persistent
"Don't ask again" checkbox. They can be enabled again individually—or all at
once—from **Settings > Confirmations**.

The canvas uses mode-specific mouse controls. Middle-button drag always pans the
home. In Select mode, left-click selects a point, sensor, device, wall, or door;
dragging moves it, while dragging a wall or door endpoint resizes that segment.
Right-click opens its action menu, while right-dragging draws a marquee that
selects multiple objects; left-dragging one of them moves the entire selection.
Copy, Cut, Paste, and Duplicate are available
from that menu and through the standard Ctrl+C/X/V/D shortcuts. Ctrl+Z restores
the previous position after moving or resizing map objects, and also removes or
restores the most recently added or deleted wall. In Manual mode,
left-click moves the character and performs simulation interactions after Start
is pressed. Selected objects offer type-specific details and actions; press Delete
to remove one. Sensor
and device actions can also open associated graphs and CSV/log data. Scenario Add
commands are temporary placement tools, and starting Manual mode cancels any
unfinished placement cleanly. The icon tool palette beside the canvas provides the
selection, Manual, and construction tools; hover over an icon for its description.
The wall and door tools work point-to-point: click an existing point, preview the
segment, then click its second point. Moving an attached point moves every connected
wall and door endpoint with it. The wall tool remains active after creating a wall;
right-click or Escape cancels only its unfinished segment and starts a fresh one.
Choose Select, Manual, or another construction tool to leave wall placement mode.
The persistent **Grid mode (snap all objects)** setting aligns points, sensors,
devices, walls, and doors to the 25-unit grid during placement, movement, resize,
and paste operations. **Show grid** controls only the visible grid lines, so the
grid can be hidden without disabling snapping (or shown while snapping is off).

## Tests And Checks

Run the automated test suite with:

```bash
python -m unittest discover -s tests -v
```

The tests cover scenario loading, activity recognition, geometry, Smart Meter
profiles, cycle completion, appliance state transitions, and temperature
replay and smoothing.

Python syntax can be checked with:

```bash
python -m compileall -q .
```

## Smart Meter Pipeline

The pipeline is available under `LLM/smartmeter/`. It supports:

- `Best k`, which selects a value of `K` from the validation metrics;
- `Human-friendly`, which applies additional practical constraints to the
  cluster distribution;
- `Custom k`, when the desired number of profiles is known in advance.

The runtime can use complete simulated profiles or, for a Smart Meter bound
to a real device, match an incoming partial cycle and estimate its completion.
The generated catalog is stored in
`LLM/smartmeter/llm_smartmeter_profiles.json`.

The pipeline outputs include cluster summaries, representative cycles,
evaluation tables, plots, and the files used by the simulator. Large raw
datasets and intermediate runtime files are intentionally treated as local
artifacts and should not be committed to a normal Git repository.

## Input Data And Local Configuration

Acquisition data is read from CSV files in `devices/`. Typical inputs include
temperature files such as `dht_t1.csv` and `dht_t2.csv`, and Smart Meter files
whose names identify the appliance. The exact files used for an experiment
must be kept with that experiment or regenerated from the original dataset.

The optional `sensor_map.json` file binds simulated sensors to real DHT or
Smart Meter sources. It contains local addresses and hardware configuration,
so it is ignored by default and must be created separately on another machine.

Simulation sessions, logs, and generated exports are written to `saves/` and
`logs/`; these directories are local runtime data.

## Project Structure

- `main.py`: graphical application entry point;
- `app/`: application context, controllers, hardware adapters, I/O, and UI;
- `sensor.py`: sensor state updates, temperature model, and Smart Meter logic;
- `sim.py`: simulation loop;
- `activity.py`: activity detection;
- `graph.py`: graph generation;
- `read.py`: scenario CSV parsing;
- `LLM/smartmeter/`: cycle extraction, clustering, and profile generation;
- `tests/`: automated regression tests.

## Reproducibility

For each experiment, record the commit, input CSV files, parameter file,
selected clustering mode, value of `K`, and generated output directory. Do not
store credentials, private sensor addresses, or large raw datasets in the
public repository. Git LFS or an external data store should be used when those
artifacts must be shared.
