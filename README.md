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
