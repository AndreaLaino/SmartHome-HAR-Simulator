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

Personal preferences are available from the `Settings` menu. They control the
manual simulation's initial time, whether `saved.csv` or a selected scenario
CSV is loaded at application startup, whether Manual mode is activated
automatically, and whether PIR fields of view are shown. These choices are stored in
the local, Git-ignored `settings.json` file. Visible PIR fields are clipped by
walls and closed doors to match the simulator's detection behavior, and their
transparency can be adjusted from the same menu. The window state and canvas
zoom position are also restored between application launches. Room outlines and
recognized room types can be shown or hidden with **Settings > Show room types**.
Room labels also show live power in watts, calculated by summing current Smart
Meter readings. A meter contributes to the room containing its associated device;
unlinked meters use their own map position. Each meter counts once, including on
shared room boundaries. Devices without a Smart Meter are excluded, and rooms
without meters are marked **No smart meters**. **Scenario > Recognize rooms**
shows the same live totals in the room list.

To add a light, choose **Scenario > Add devices**, select **Normal Light** or
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

### Weighted properties and activity recognition

Devices and sensors have one or more weighted properties (0–1). Oven and
Microwave share `cooking`; TV and PlayStation share `relaxing`. Computer supports
both `working` and `relaxing`, with a stronger default work weight. Other additions
include Vacuum Cleaner, Kettle, Toaster, Hair Dryer, Bed, Sofa, Dining Table,
Shower, Toilet and Sink. Furniture and water fixtures consume 0 W; new electrical
devices use their configured constant wattage until switched off.

Set property weights in the Add dialog, or right-click a device/sensor and choose
**Properties / recognition weights…**. Properties use `name=weight`, separated
by commas; `cooking device` and `cooking` are equivalent. A separate object weight
controls its overall contribution. Names identify objects and sensor associations.
An observed object supplies activity evidence only after matching an explicit
reference-object association. Copies keep independent property dictionaries.
Old scenarios receive the default reference catalogue automatically.

Use **Scenario > Activity recognition…** to see live scores, supporting sensors,
missing conditions, and inferred activities. Edit evidence weights, activity
weights and thresholds. A custom object capability also needs a reference
association before it can supply a rule's evidence role. Scores are
weighted rule support, **not calibrated probabilities**. Repeated sensors with
the same property do not inflate its support. Rules and object weights are saved
in the scenario CSV along with properties.

In Manual simulation, associate a **Smart Meter** with an electrical appliance
to observe its use; active nearby presence sensors also support active appliances.
A **Weight** sensor can observe a Bed or Dining Table. A **Water Flow** sensor
can observe a Shower or Sink; in the local simulator, clicking within 10 map units
activates it and moving away deactivates it. Bathroom use can be supported by a
Weight/contact sensor associated with a Toilet. Sensor associations can be edited
in the properties window.

Cooking followed by relaxing can infer **eating** without requiring an eating
sensor. **Eating while relaxing** groups independently recognized eating and
relaxing; both base activities keep their own confidence and uninterrupted log
intervals. The composite requires both activities to pass their evidence and
confidence gates, and cannot replace ongoing cooking or working. Oven → TV and
Microwave → PlayStation therefore use the same recognition mechanism. The cooking
evidence decays over a configurable window (60 simulated minutes by default);
when the inferred meal expires, relaxing can continue by itself. Reversed or
simultaneous use does not establish the cooking-then-relaxing sequence.
**Sleeping** requires continuous bedroom presence and bed weight, every light OFF,
and zero light power for 10 simulated minutes by default. Missing lighting or room
evidence does not count as confirmation; unrelated loads such as a fridge are
excluded from the light-power check. Laundry/dishwashing may continue while the
person is outside, while personal activities are suppressed.

### Reference-device substitution during recognition

The known default association is **Oven → cooking**. Microwave has no direct
cooking association: after a sensor detects its use, the recognizer compares its
properties with the reference oven's relevant properties. A sufficient match lets
it fill the oven's cooking role. The same mechanism transfers TV's relaxing role
to PlayStation and can transfer roles between sensors. Removing every reference
association for a role prevents that role from being recognized, even when a
target property has the same name as the activity.

Edit the shared associations in **Scenario > Shared recognition library… >
Reference rules**. Select a known source object, an evidence role, frozen source
properties, relevant property weights, required properties and minimum similarity.
Use **Activity recognition… > Reference device rules…** for a house-specific
override. This opens a simplified editor: choose a known source device and a
destination in the current house. Property compatibility is checked immediately;
the activity dropdown offers only compatible existing activities and supports
multiple selections. Source choices remain available when either device changes
and after saving: only activities are filtered by compatibility. Saving clears
the selected pair so another association can be added. Sources include
stored references, the shared catalogue (or the built-in catalogue when no library
exists), and actual known devices from registered houses. Browsing these sources
does not activate their associations in the house. **Use selected reference roles** adds the chosen
references to this house without changing destination properties. They remain
reusable by other compatible devices; existing associations are preserved.
Save the scenario CSV to keep these overrides. Compatibility is not a runtime
recognition result: sensors and all activity context gates still apply.

The creation form occupies roughly 75% of the window. The right-hand **Current
connections** list shows each device reference, its activity/role and compatible
destinations. **Remove selected** removes the selected reference rules from this
house; all devices using those rules are affected. Save the scenario to keep the
removals. Other houses and the shared catalogue are unchanged.

The shared Oven reference includes a physical food-preparation association
(`heats_food`, `food_preparation`) alongside its existing activity-labelled
association, so the physical microwave example can use that same catalogue source.

The simplified editor's **Advanced reference rules…** button opens the original
editor. New associations are entered there using a new rule ID; associations can
also be deleted. Setting a reference house attaches existing known associations to
matching source-house types. Other associations retain their built-in catalogue
references until edited or deleted.

Similarity is directional weighted reference-property coverage. Extra target
properties do not reduce a match. Required properties prevent a generic shared
capability, such as heating, from making a hair dryer substitute for an oven.
Similarity, association weight, reference strength and device/sensor weights all
contribute to evidence. Runtime explanations retain the source object, target,
binding ID, property match and observing sensor, including earlier cooking evidence
used in a later meal inference. Double-click an activity in the recognition window
to inspect the full explanation.

For a demonstration with independent capability names, open either scenario in
`examples/reference_transfer/`. Both contain only the known oven cooking association;
the microwave has `heats_food`, `food_preparation` and `defrosts` properties, with
no `cooking` property. Start Manual simulation and switch the appliance ON. Delete
the oven association in the house's reference-rule editor to verify recognition
stops. See [the example guide](examples/reference_transfer/README.md).

Example scenarios are in `examples/semantic_activities/`: open either cooking and
relaxing house, start Manual simulation, switch the cooking appliance ON then OFF,
and switch the entertainment device ON. The bedroom/bathroom example supports
bed-weight and fixture-flow checks. Room types can be corrected through
**Scenario > Recognize rooms** when needed.

### Shared model and transfer between houses

Use **Scenario > Shared recognition library…** to manage the installation-wide
`recognition_library/` directory. Its versioned property profiles and activity
rules are separate from house layouts and device names. A reference house is a
baseline for comparison; the shared model is reusable by every connected house.

The main window provides house registration, a list of registered houses, reference
selection, shared-model connection and **Reference device rules…**. **Advanced…**
opens the complete existing editor: shared profiles/rules, reference rules,
transfer reports and labelled evaluation. Choosing a reference selects source
objects for existing shared associations; it does not publish that house's local rules.

1. Open your baseline scenario, connect the shared model, and **Set current as
   reference**. In **Advanced…**, publish its effective rules if you want to use your local rules globally.
   To choose an already registered house, select its row and **Use selected house as reference**.
2. Open another scenario and **Register as a new house**. Connect the shared model
   and use each object's properties editor to choose a reusable profile. Factory
   properties inherit the matching profile automatically; custom properties stay
   local. Local rule overrides are preserved unless you uncheck that option.
3. In **Advanced… > Transfer report**, compare property and sensor coverage. For example,
   Oven → TV and Microwave → PlayStation support the same activity sequence.
   Coverage reports possible recognition, not measured accuracy.
4. Run Manual simulation and use **Advanced… > Accuracy / ground truth** to record the activities
   actually occurring, or explicitly label a frame as having no activity. Evaluate
   the shared model on held-out houses. Reference-house runs are excluded; unlabelled
   frames preserve sequence context without being scored.

Live **confidence** is uncalibrated rule support multiplied by evidence coverage,
with a 0.85 factor for inferred activities. A minimum confidence setting can make
the recognizer abstain. **Accuracy** is exact activity-set agreement with human
labels; precision, recall, F1 and sample/house counts are also reported. Accuracy
is unavailable until labelled held-out examples are evaluated. Keep test houses
separate from model tuning to assess transfer honestly.

**Simulation > Activity Log** and the Activity tab in **Generate log** refresh
automatically and show running intervals as **In progress**. CSV exports include
these intervals with an empty `end` field; viewing or exporting never closes them.
Eating, relaxing and their composite are separate log entries when recognized.

Save scenario CSV files to preserve house identity, profile bindings and a frozen
shared rule snapshot. **Disconnect** freezes the effective model locally, including
when the library is unavailable. Shared edits invalidate previous reports until
re-evaluation. See [the library directory guide](recognition_library/README.md)
for the data layout and moving an experiment between installations.

### Learned recognition and controlled source/target experiments

Open **Scenario > Source / target experiment…** to compare reference-transfer
rules, direct-property rules, a Random Forest using shared semantic roles, and a
Random Forest using fixed source sensor identities. Start with the scripted
Oven/TV versus Microwave/PlayStation demo, or use independently labelled sessions
from registered source and target houses. Training, calibration and source tests
use separate complete source sessions; target houses remain held out.

The tool exports comparison tables, per-activity/per-house metrics, abstention
coverage, predictions, causal features, frozen observations and portable JSON
models. Saved experiments can be replayed without training or live-library reads.
Synthetic results are labelled explicitly and do not establish real-world accuracy.

```sh
python -m har_experiment --demo --sessions 20 --seed 42
python -m har_experiment --library
python -m har_experiment --replay PATH_TO_SAVED_EXPERIMENT
```

See [the experiment guide](docs/source_target_experiment.md) for human annotation,
source/session separation, feature definitions, confidence and output files.

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
