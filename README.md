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

On macOS, **Save As** uses the application's file browser. Choose a folder,
enter the file name, and press **Save**; `.csv` is
added when no extension is given. Scenario files are replaced only after a complete
write, so an unsuccessful save preserves an existing file and reports the error.

The main simulation workflows are available from the `Simulation` menu:

- `Manual` for direct interaction with the scenario;
- `Automatic` for replaying activity and sensor data;
- `LLM Smart Meter` for extracting and reusing appliance consumption cycles.

## Learned Activity Recognition Across Houses

Activity recognition now uses the trained model exclusively. The old appliance-name,
meal-time and entry/exit rules have been removed from `activity.py`; it manages
model predictions, activity intervals and polling.

Open **Scenario > Learned activity recognition…** to assign shared object
categories, record observations with human activity labels, and train a Random
Forest on a source house. The same model can recognize activities in other
houses through category-based measurements; there is no manual device-to-activity
transfer rule. A category alone never starts an activity, and learned recognition
requires an activated trained model.

The window has three tabs: **Categories**, **Record sessions**, and **Train &
evaluate**. Training uses at least three independent source simulations; target
sessions are reserved for evaluation. **Add synthetic demo sessions** offers a
scripted trial whose results do not measure accuracy on real people. Model vote
confidence is uncalibrated and differs from measured test accuracy.

Try the [source oven/TV scenario](examples/learned_har/source_oven_tv.csv) and
[target microwave/console scenario](examples/learned_har/target_microwave_console.csv).
Follow the [Italian click-by-click guide](docs/learned_har_it.md) for a demo,
recording your own sessions, and preserving data. Shared categories, sessions,
models and reports live under `har_data/`; scenario CSVs preserve house identity,
custom categories and the selected model ID. Copy both when moving the project.

## Editor And Simulation Controls

Personal preferences are available from the `Settings` menu. They control the
manual simulation's initial time, whether `saved.csv` or a selected scenario
CSV is loaded at application startup, whether Manual mode is activated
automatically, and whether PIR fields of view are shown. These choices are stored in
the local, Git-ignored `settings.json` file. Visible PIR fields are clipped by
walls and closed doors to match the simulator's detection behavior, and their
transparency can be adjusted from the same menu: 0% is opaque and 100% is
fully invisible. The overlays use actual alpha transparency, including on
macOS, and resize with canvas zoom. The window state and canvas
zoom position are also restored between application launches. Room outlines and
recognized room types can be shown or hidden with **Settings > Show room types**.
Room labels also show live power in watts, calculated by summing current Smart
Meter readings. A meter contributes to the room containing its associated device;
unlinked meters use their own map position. Each meter counts once, including on
shared room boundaries. Devices without a Smart Meter are excluded, and rooms
without meters are marked **No smart meters**. **Scenario > Recognize rooms**
shows the same live totals in the room list.

To add a device, choose **Scenario > Add devices** and click its map position.
In the dialog, choose a **Category** to filter **Device type**: for example,
**Device_Cooking** offers Oven, Microwave, and Toaster. **All devices** shows the
full list. Devices with multiple shared categories appear in each relevant
group; selecting a group preserves all of the device's categories.

Microwave, Toaster, TV, and PlayStation have illustrative nominal-load profiles
in `consumption_profiles.py`. The toaster switches off after its three-minute
cycle; Microwave, TV, and PlayStation remain on until switched off manually.
These defaults are simulated examples, not measured appliance traces.

To add a light, select **Device_Lighting** or **All devices**, select **Normal Light** or
**Smart Light**, and enter its wattage (10 W by default). Both draw their configured
power while on and 0 W while off, and stay on until switched off. Normal lights
need a separate **Smart Meter** sensor to contribute to room power. Smart lights
include a built-in Smart Meter automatically, with readings available in room
totals, device graphs, and logs. This meter follows the light when moved, copied,
or saved and reloaded; it needs no separate placement. An additional external
meter on a smart light does not count its power twice. Existing **Lighting**
devices remain supported as normal lights. These values are instantaneous power
(W), not accumulated energy (kWh).

Choose **Manual** or **Automatic** in the light's **Light control** field,
independently of whether it is a normal or smart light. In simulation, manual
lights start with a clickable **Switch: ON/OFF** square beside them; click the
switch to activate or deactivate the light. In **Select** mode, drag the switch
to position it independently. Moving a light leaves its switches in place.
Double-click a light to open its live information window, including its state,
power, position, room, metering, associated sensors, and switches. Use **Add switch**
and **Remove selected switch** there to manage multiple switches for the same
light. You can also right-click a light to add a switch, or select a switch and
use Delete/⌘Delete to remove it. Undo restores switch moves, additions, and deletions.
Switch positions and an explicitly empty switch list survive save/load. Older
scenarios retain their default switch. Copying a light creates independent copies
of its switches; a copied switch remains associated with its original light.
Automatic lights follow room presence: they turn on when the character enters
their room and off when the character leaves. Before a character is placed,
active PIR sensors in the room can also activate them. In an open area without
recognized rooms, nearby presence within the PIR range and clear of walls or
closed doors is used instead. Right-click an existing light to change its control
mode. Saved scenarios preserve this choice; older lights default to Manual.

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
from that menu and through the standard Ctrl+C/X/V/D shortcuts, or ⌘C/X/V/D on
macOS. The tool palette includes **Undo** (back arrow), **Redo** (forward arrow),
and **Delete** (trash) buttons. Ctrl+Z, or **⌘Z on Mac**, restores a move or resize, removes the most
recently added wall, or restores a deleted point, sensor, device, wall, or door.
Restoring a point also restores its connected walls and doors; restoring a device
restores its sensor links. **Redo**, **⌘⇧Z on Mac**, or Ctrl+Shift+Z / Ctrl+Y
reapplies the most recently undone edit. Multiple Undo/Redo steps are supported;
a new edit clears the Redo history. Paste and Duplicate can also be undone and
redone. Use **⌘Delete (⌘Canc)** on a MacBook to delete the
selected object; plain Delete/Backspace also works on Mac. These shortcuts apply
when the map has keyboard focus, so text fields retain their normal editing keys.
In Manual mode,
left-click moves the character and performs simulation interactions after Start
is pressed. Selected objects offer type-specific details and actions; press Delete
to remove one. Sensor
and device actions can also open associated graphs and CSV/log data. Scenario Add
commands are temporary placement tools, and starting Manual mode cancels any
unfinished placement cleanly. The icon tool palette beside the canvas provides the
selection, Manual, and construction tools; hover over an icon for its description.
The wall and door tools work directly on the canvas: click two endpoints to create
a segment. Each endpoint reuses a nearby point or silently creates an anonymous
point when needed. Standalone points are also created without a naming dialog.
Moving an attached point moves every connected wall and door endpoint with it.
Construction tools remain active after creating a point, sensor, device, wall, or
door so that several objects can be placed in succession. For walls and doors,
right-click or Escape cancels only the unfinished segment and starts a fresh one.
Choose Select, Manual, or another construction tool to leave placement mode.
The persistent **Grid mode (snap all objects)** setting aligns points, sensors,
devices, walls, and doors to the 25-unit grid during placement, movement, resize,
and paste operations. **Show grid** controls only the visible grid lines, so the
grid can be hidden without disabling snapping (or shown while snapping is off).

## Tests And Checks

Run the automated test suite with:

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
```

The suite includes headless unit and integration tests for scenario round trips,
timer and simulation lifecycle, activity recognition, geometry and editing,
temperature replay, appliance consumption and prediction, Smart Meter profiles,
hardware adapters, cloud imports, interaction logging, and replay/export flows.
Fixtures use temporary files and mocked external services; no AWS credentials,
physical sensors, local `sensor_map.json`, or private datasets are needed.

Run the suite with branch coverage:

```bash
python -m coverage run -m unittest discover -s tests -v
python -m coverage report
```

See [TESTING.md](TESTING.md) for focused runs, real Tk smoke tests, coverage
reports, and the automated Windows/Linux CI checks.

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
