"""Models for the Home Simulator."""

from typing import Optional, List
from dataclasses import dataclass, field


@dataclass
class Point:
    """Represents a reference point on the map."""
    name: str
    x: int
    y: int


@dataclass
class Sensor:
    """Represents a sensor in the simulation."""
    name: str
    x: int
    y: int
    type: str  # "PIR", "Temperature", "Switch", "Smart Meter", "Weight"
    min_val: float
    max_val: float
    step: float
    state: float
    direction: Optional[int] = None  # for PIR/directional sensors
    consumption: Optional[float] = None  # for Smart Meter
    associated_device: Optional[str] = None  # device linked to this sensor


@dataclass
class Device:
    """Represents a device in the simulation."""
    name: str
    x: int
    y: int
    type: str  # "Fridge", "Washing_Machine", "Oven", "Coffee_Machine", "Computer", "Dishwasher"
    power: float  # Watt
    state: int  # 0 = OFF, 1 = ON
    min_consumption: float
    max_consumption: float
    current_consumption: float = 0.0
    consumption_direction: int = 1  # 1 = increasing, -1 = decreasing

    def is_on(self) -> bool:
        """Check if device is ON."""
        return self.state == 1

    def toggle(self):
        """Toggle device state."""
        self.state = 1 - self.state


@dataclass
class Door:
    """Represents a door between two points."""
    x1: int
    y1: int
    x2: int
    y2: int
    state: str = "open"  # "open" or "close"

    def is_closed(self) -> bool:
        """Check if door is closed."""
        return self.state == "close"

    def is_open(self) -> bool:
        """Check if door is open."""
        return self.state == "open"


@dataclass
class Wall:
    """Represents a wall between two points."""
    x1: int
    y1: int
    x2: int
    y2: int
    doors: List[Door] = field(default_factory=list)

    def add_door(self, door: Door):
        """Add a door to this wall."""
        self.doors.append(door)


@dataclass
class Room:
    """A closed area inferred from walls and doors."""
    room_id: str
    polygon: List[tuple[float, float]]
    inferred_type: str = "Unknown"
    manual_type: Optional[str] = None
    confidence: float = 0.0
    sensor_names: List[str] = field(default_factory=list)
    device_names: List[str] = field(default_factory=list)
    point_names: List[str] = field(default_factory=list)

    @property
    def room_type(self) -> str:
        return self.manual_type or self.inferred_type

    @property
    def centroid(self) -> tuple[float, float]:
        if not self.polygon:
            return (0.0, 0.0)
        return (
            sum(x for x, _ in self.polygon) / len(self.polygon),
            sum(y for _, y in self.polygon) / len(self.polygon),
        )


@dataclass
class SimulationState:
    """Container for all simulation objects.
    
    This replaces the scattered global variables (sensors, devices, points, etc.)
    """
    sensors: List[Sensor] = field(default_factory=list)
    devices: List[Device] = field(default_factory=list)
    points: List[Point] = field(default_factory=list)
    walls: List[Wall] = field(default_factory=list)
    doors: List[Door] = field(default_factory=list)
    rooms: List[Room] = field(default_factory=list)

    def get_sensor(self, name: str) -> Optional[Sensor]:
        """Get sensor by name."""
        for s in self.sensors:
            if s.name == name:
                return s
        return None

    def get_device(self, name: str) -> Optional[Device]:
        """Get device by name."""
        for d in self.devices:
            if d.name == name:
                return d
        return None

    def get_point(self, name: str) -> Optional[Point]:
        """Get point by name."""
        for p in self.points:
            if p.name == name:
                return p
        return None

    def sensor_names(self) -> List[str]:
        """Get all sensor names."""
        return [s.name for s in self.sensors]

    def device_names(self) -> List[str]:
        """Get all device names."""
        return [d.name for d in self.devices]

    def point_names(self) -> List[str]:
        """Get all point names."""
        return [p.name for p in self.points]
