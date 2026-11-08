"""Stable, serializer-friendly data contracts shared by every AI subsystem.

These dataclasses intentionally contain no strategy.  Adapters translate JSON
or native Stars! records into ``GameState``; planners create ``OrderSet``;
writers then choose how to serialize those semantic orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Position:
    """A Stars! map coordinate in light years."""
    x: float
    y: float


@dataclass
class Planet:
    """The AI's observed state for one planet, in normalized game units."""
    id: int
    name: str
    position: Position
    owner: int | None = None
    habitability: int | None = None
    population: int = 0
    factories: int = 0
    mines: int = 0
    defenses: int = 0
    resources: int = 0
    ironium: int = 0
    boranium: int = 0
    germanium: int = 0
    observed: bool = True
    native: dict[str, Any] = field(default_factory=dict)


@dataclass
class Fleet:
    """A fleet visible to the current player, including its decoded route head."""
    id: int
    name: str
    owner: int
    position: Position
    destination_planet_id: int | None = None
    role: str = "unknown"
    # Normalized headcount. Native fleet population cargo is converted from
    # kT at 100 colonists per kT by the native adapter.
    cargo_population: int = 0
    # Cargo capacity remains mass in native kT.
    cargo_capacity: int = 0
    combat_power: float = 0.0
    speed: int = 7
    native: dict[str, Any] = field(default_factory=dict)
    destination_warp: int | None = None
    destination_task: int | None = None
    destination_mission: str | None = None


@dataclass
class Tech:
    """The six Stars! research field levels for the current player."""
    energy: int = 0
    weapons: int = 0
    propulsion: int = 0
    construction: int = 0
    electronics: int = 0
    biotechnology: int = 0


@dataclass
class RaceProfile:
    """Race traits and planning preferences that affect legal strategic choices."""
    name: str = "AI Race"
    growth_rate: float = 0.15
    primary_trait: str = "unknown"
    native: dict[str, Any] = field(default_factory=dict)
    research_bias: dict[str, float] = field(default_factory=lambda: {
        "energy": 1.0,
        "weapons": 1.0,
        "propulsion": 1.0,
        "construction": 1.0,
        "electronics": 1.0,
        "biotechnology": 1.0,
    })


@dataclass
class GameState:
    """Complete normalized input required to make one player's turn decision."""
    game_name: str
    year: int
    player_id: int
    race: RaceProfile
    tech: Tech
    planets: list[Planet]
    fleets: list[Fleet]
    messages: list[str] = field(default_factory=list)
    native: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameState":
        """Build a normalized state from the public JSON representation."""
        planets = [
            Planet(
                id=p["id"],
                name=p["name"],
                position=Position(**p["position"]),
                owner=p.get("owner"),
                habitability=p.get("habitability"),
                population=p.get("population", 0),
                factories=p.get("factories", 0),
                mines=p.get("mines", 0),
                defenses=p.get("defenses", 0),
                resources=p.get("resources", 0),
                ironium=p.get("ironium", 0),
                boranium=p.get("boranium", 0),
                germanium=p.get("germanium", 0),
                observed=p.get("observed", True),
                native=p.get("native", {}),
            )
            for p in data.get("planets", [])
        ]
        fleets = [
            Fleet(
                id=f["id"],
                name=f["name"],
                owner=f["owner"],
                position=Position(**f["position"]),
                destination_planet_id=f.get("destination_planet_id"),
                role=f.get("role", "unknown"),
                cargo_population=f.get("cargo_population", 0),
                cargo_capacity=f.get("cargo_capacity", 0),
                combat_power=f.get("combat_power", 0.0),
                speed=f.get("speed", 7),
                native=f.get("native", {}),
                destination_warp=f.get("destination_warp"),
                destination_task=f.get("destination_task"),
                destination_mission=f.get("destination_mission"),
            )
            for f in data.get("fleets", [])
        ]
        return cls(
            game_name=data["game_name"],
            year=data["year"],
            player_id=data["player_id"],
            race=RaceProfile(**data.get("race", {})),
            tech=Tech(**data.get("tech", {})),
            planets=planets,
            fleets=fleets,
            messages=data.get("messages", []),
            native=data.get("native", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready deep representation of this state."""
        return asdict(self)


@dataclass
class Order:
    """One semantic AI request; a writer decides its concrete file encoding."""
    kind: str
    payload: dict[str, Any]
    reason: str
    priority: int = 50


@dataclass
class OrderSet:
    """All semantic requests and human-readable planning notes for one turn."""
    game_name: str
    year: int
    player_id: int
    orders: list[Order] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, kind: str, payload: dict[str, Any], reason: str, priority: int = 50):
        """Append an order with its evidence trail and conflict-resolution priority."""
        self.orders.append(Order(kind, payload, reason, priority))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready representation suitable for traces and adapters."""
        return asdict(self)
