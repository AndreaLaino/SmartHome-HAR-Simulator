from __future__ import annotations
from dataclasses import dataclass, field
import tkinter as tk
from typing import Optional
from house_state import HouseState

@dataclass
class AppContext:
    """Shared application context/data."""
    window: tk.Tk
    canvas: Optional[tk.Canvas] = None
    timer_frame: Optional[tk.Frame] = None
    activity_label: Optional[tk.Label] = None
    
    file_menu: Optional[tk.Menu] = None
    scenario_menu: Optional[tk.Menu] = None
    simulation_menu: Optional[tk.Menu] = None

    # State variables
    load_active: bool = False
    current_file: Optional[str] = None
    smartmeter_mode: str = "simulation"

    r_points: list = field(default_factory=list)
    read_walls: list = field(default_factory=list)
    read_sensors: list = field(default_factory=list)
    read_devices: list = field(default_factory=list)
    read_doors: list = field(default_factory=list)
    rooms: list = field(default_factory=list)
    room_overrides: dict[str, str] = field(default_factory=dict)

    # Optional reference if you need to stop a background logger on exit
    smart_logger: Optional[object] = None
    house_state: HouseState = field(default_factory=HouseState)
    automatic_state: dict = field(default_factory=dict)
