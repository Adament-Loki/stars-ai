
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
from typing import Any


def _safe_list(value):
    return list(value) if isinstance(value, (list, tuple)) else None


@dataclass
class AgentMemory:
    """
    Persistent per-player strategic state.

    v7.0 principle:
      the current .m# file is an observation DELTA/snapshot, not the empire's
      complete memory. Once a planet has been learned, sparse future M files may
      age that intelligence but must never make the planet "virgin unknown" again.
    """
    schema_version: int = 2
    game_id: int | None = None
    player_id: int | None = None
    start_year: int | None = None
    last_year: int | None = None

    # Legacy fields retained for compatibility with older memory files.
    known_enemy_planets: dict[str, int] = field(default_factory=dict)
    planet_scores: dict[str, float] = field(default_factory=dict)
    strategic_notes: list[str] = field(default_factory=list)
    goal_progress: dict[str, float] = field(default_factory=dict)
    diplomacy: dict[str, dict] = field(default_factory=dict)

    # v7.0 persistent intelligence.
    initial_owned_planet_ids: list[int] = field(default_factory=list)
    planet_intel: dict[str, dict[str, Any]] = field(default_factory=dict)
    colonized_years: dict[str, int] = field(default_factory=dict)
    scan_target_history: dict[str, dict[str, Any]] = field(default_factory=dict)
    scout_routes: dict[str, dict[str, Any]] = field(default_factory=dict)
    kpi_history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None) -> "AgentMemory":
        if not path:
            return cls()
        p = Path(path)
        if not p.exists():
            return cls()
        raw = json.loads(p.read_text(encoding="utf-8"))
        allowed = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in raw.items() if k in allowed}
        return cls(**filtered)

    def save(self, path: str | Path | None) -> None:
        if not path:
            return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(p)

    def _state_game_id(self, state) -> int | None:
        header = (state.native or {}).get("header") or {}
        value = header.get("game_id")
        return int(value) if value is not None else None

    def _reset_for_state(self, state) -> None:
        # Preserve only schema shape, not knowledge from another/restarted game.
        fresh = AgentMemory(
            game_id=self._state_game_id(state),
            player_id=int(state.player_id),
            start_year=int(state.year),
            last_year=None,
            initial_owned_planet_ids=sorted(
                int(p.id) for p in state.planets if p.owner == state.player_id
            ),
        )
        for f in fields(self):
            setattr(self, f.name, getattr(fresh, f.name))

    def ensure_game(self, state) -> bool:
        """
        Returns True when memory had to be reset.

        Reset on:
        - different game id;
        - different player seat;
        - time moving backwards (common when replaying the same game from Turn 0).
        """
        gid = self._state_game_id(state)
        mismatch = (
            self.game_id is not None and gid is not None and int(self.game_id) != int(gid)
        )
        wrong_player = self.player_id is not None and int(self.player_id) != int(state.player_id)
        rewound = self.last_year is not None and int(state.year) < int(self.last_year)

        if mismatch or wrong_player or rewound:
            self._reset_for_state(state)
            return True

        if self.game_id is None:
            self.game_id = gid
        if self.player_id is None:
            self.player_id = int(state.player_id)
        if self.start_year is None:
            self.start_year = int(state.year)
        if not self.initial_owned_planet_ids:
            self.initial_owned_planet_ids = sorted(
                int(p.id) for p in state.planets if p.owner == state.player_id
            )
        return False

    def _capture_planet(self, planet, year: int, player_id: int) -> None:
        key = str(int(planet.id))
        prior = self.planet_intel.get(key, {})
        first_seen = prior.get("first_seen_year")
        if first_seen is None:
            first_seen = int(year)

        native = planet.native or {}
        entry = {
            "planet_id": int(planet.id),
            "name": str(planet.name),
            "x": float(planet.position.x),
            "y": float(planet.position.y),
            "ever_observed": True,
            "first_seen_year": int(first_seen),
            "last_seen_year": int(year),
            "last_known_owner": planet.owner,
            "habitability": planet.habitability,
            "environment": _safe_list(native.get("environment")),
            "original_environment": _safe_list(native.get("original_environment")),
            "mineral_concentrations": _safe_list(native.get("mineral_concentrations")),
            "population": int(planet.population or 0),
            "factories": int(planet.factories or 0),
            "mines": int(planet.mines or 0),
            "defenses": int(planet.defenses or 0),
            "ironium": int(planet.ironium or 0),
            "boranium": int(planet.boranium or 0),
            "germanium": int(planet.germanium or 0),
            "has_starbase": bool(native.get("has_starbase", False)),
            "starbase_hull_id": native.get("starbase_hull_id"),
        }
        self.planet_intel[key] = entry

        if planet.habitability is not None:
            self.planet_scores[key] = float(planet.habitability)

        if planet.owner not in (None, player_id):
            self.known_enemy_planets[key] = int(planet.owner)
        elif key in self.known_enemy_planets and planet.owner in (None, player_id):
            # Current observation supersedes stale enemy ownership.
            self.known_enemy_planets.pop(key, None)

        if (
            planet.owner == player_id
            and int(planet.id) not in set(self.initial_owned_planet_ids)
            and key not in self.colonized_years
        ):
            self.colonized_years[key] = int(year)

    def reconcile_state(self, state) -> dict[str, Any]:
        """
        Merge CURRENT observations into memory, then restore older learned data
        onto map-only/sparse planets for strategic reasoning.
        """
        reset = self.ensure_game(state)
        year = int(state.year)

        current_observed = {int(p.id): bool(p.observed) for p in state.planets}

        # First ingest only what the current M file genuinely exposes.
        for p in state.planets:
            if current_observed[int(p.id)]:
                self._capture_planet(p, year, int(state.player_id))

        restored = 0
        for p in state.planets:
            if current_observed[int(p.id)]:
                p.native["intel_source"] = "current_m"
                p.native["intel_age_years"] = 0
                continue

            intel = self.planet_intel.get(str(int(p.id)))
            if not intel or not intel.get("ever_observed"):
                continue

            # Restore strategic knowledge, but explicitly mark it stale.
            p.observed = True
            p.owner = intel.get("last_known_owner")
            p.habitability = intel.get("habitability")
            p.population = int(intel.get("population", 0) or 0)
            p.factories = int(intel.get("factories", 0) or 0)
            p.mines = int(intel.get("mines", 0) or 0)
            p.defenses = int(intel.get("defenses", 0) or 0)
            p.ironium = int(intel.get("ironium", 0) or 0)
            p.boranium = int(intel.get("boranium", 0) or 0)
            p.germanium = int(intel.get("germanium", 0) or 0)

            if intel.get("environment") is not None:
                p.native["environment"] = list(intel["environment"])
            if intel.get("original_environment") is not None:
                p.native["original_environment"] = list(intel["original_environment"])
            if intel.get("mineral_concentrations") is not None:
                p.native["mineral_concentrations"] = list(intel["mineral_concentrations"])

            last_seen = int(intel.get("last_seen_year", year))
            p.native["observed_turn"] = last_seen - 2400
            p.native["intel_source"] = "persistent_memory"
            p.native["intel_age_years"] = max(0, year - last_seen)
            p.native["intel_from_memory"] = True
            restored += 1

        self.last_year = year

        known = self.ever_observed_count()
        current = sum(1 for v in current_observed.values() if v)
        return {
            "memory_reset": bool(reset),
            "current_m_observed": current,
            "ever_observed": known,
            "restored_from_memory": restored,
            "total_planets": len(state.planets),
        }

    def ever_observed_count(self) -> int:
        return sum(
            1 for x in self.planet_intel.values()
            if bool(x.get("ever_observed"))
        )

    def ever_observed_ids(self) -> set[int]:
        return {
            int(k) for k, x in self.planet_intel.items()
            if bool(x.get("ever_observed"))
        }

    def recent_scan_target_ids(self, year: int, cooldown_years: int = 3) -> set[int]:
        out = set()
        for key, info in self.scan_target_history.items():
            last = info.get("last_assigned_year")
            if last is None:
                continue
            if int(year) - int(last) <= int(cooldown_years):
                # Known worlds are already excluded by p.observed. This protects
                # only unresolved/recent assignments from immediate ping-pong.
                out.add(int(key))
        return out

    def record_scan_orders(self, orders, year: int) -> None:
        for o in orders.orders:
            if o.kind != "move_fleet":
                continue
            if str(o.payload.get("mission", "")) not in ("scan", "recon"):
                continue
            pid = o.payload.get("destination_planet_id")
            if pid is None or o.payload.get("deconflicted_hold"):
                continue
            key = str(int(pid))
            info = dict(self.scan_target_history.get(key, {}))
            info["last_assigned_year"] = int(year)
            info["assignment_count"] = int(info.get("assignment_count", 0)) + 1
            fid=int(o.payload.get("fleet_id", -1))
            info["last_fleet_id"] = fid
            self.scan_target_history[key] = info

            # If final same-turn deconfliction retargeted a route-managed probe,
            # make persistent route state agree with the order actually emitted.
            if o.payload.get("route_managed"):
                rkey=str(fid)
                route=dict(self.scout_routes.get(rkey,{}) or {})
                ids=[int(x) for x in route.get("planet_ids",[])]
                actual=int(pid)
                if ids:
                    ids[0]=actual
                else:
                    ids=[actual]
                # Keep later stops unique after replacing the head.
                dedup=[]; seen=set()
                for x in ids:
                    if x in seen: continue
                    seen.add(x); dedup.append(x)
                route["planet_ids"]=dedup
                route["updated_year"]=int(year)
                self.scout_routes[rkey]=route



    def scout_route(self, fleet_id: int) -> dict[str, Any] | None:
        route=self.scout_routes.get(str(int(fleet_id)))
        return dict(route) if isinstance(route,dict) else None

    def set_scout_route(
        self,
        fleet_id: int,
        planet_ids: list[int],
        year: int,
        *,
        expected_discoveries: int | None = None,
        total_distance: float | None = None,
        sector_index: int | None = None,
        sector_count: int | None = None,
        terminal: bool = True,
        awaiting_refuel: bool = False,
    ) -> None:
        self.scout_routes[str(int(fleet_id))]={
            "fleet_id":int(fleet_id),
            "planet_ids":[int(x) for x in planet_ids],
            "created_year":int(year),
            "updated_year":int(year),
            "expected_discoveries":int(
                expected_discoveries if expected_discoveries is not None else len(planet_ids)
            ),
            "total_distance":round(float(total_distance or 0.0),2),
            "sector_index":sector_index,
            "sector_count":sector_count,
            "terminal":bool(terminal),
            "awaiting_refuel":bool(awaiting_refuel),
        }

    def clear_scout_route(self, fleet_id: int) -> None:
        self.scout_routes.pop(str(int(fleet_id)),None)

    def prune_scout_routes(self, state) -> None:
        """
        Drop dead fleets and planets already observed/reached from persistent
        routes. The remaining list is the probe's forward campaign.
        """
        fleets={
            int(f.id):f for f in state.fleets
            if f.owner==state.player_id and f.role in ("scout","unknown")
        }
        planets={int(p.id):p for p in state.planets}
        for key in list(self.scout_routes):
            fid=int(key)
            fleet=fleets.get(fid)
            if fleet is None:
                self.scout_routes.pop(key,None)
                continue
            route=dict(self.scout_routes[key])
            kept=[]
            for pid in route.get("planet_ids",[]):
                planet=planets.get(int(pid))
                if planet is None:
                    continue
                at_planet=(
                    abs(float(planet.position.x)-float(fleet.position.x))<=0.5
                    and abs(float(planet.position.y)-float(fleet.position.y))<=0.5
                )
                if planet.observed or at_planet:
                    continue
                kept.append(int(pid))
            route["planet_ids"]=kept
            route["updated_year"]=int(state.year)
            route["awaiting_refuel"]=(
                bool(route.get("awaiting_refuel",False))
                if fleet.destination_planet_id is not None else False
            )
            self.scout_routes[key]=route

    def reserved_scout_route_targets(self, exclude_fleet_id: int | None = None) -> set[int]:
        reserved=set()
        for key,route in self.scout_routes.items():
            if exclude_fleet_id is not None and int(key)==int(exclude_fleet_id):
                continue
            for pid in route.get("planet_ids",[]):
                reserved.add(int(pid))
        return reserved

    def discoveries_in_recent_years(self, year: int, window: int = 5) -> int:
        floor = int(year) - max(0, int(window) - 1)
        return sum(
            1 for x in self.planet_intel.values()
            if x.get("first_seen_year") is not None
            and int(x["first_seen_year"]) >= floor
        )

    def new_colonies_count(self) -> int:
        return len(self.colonized_years)

    def scan_order_count(self) -> int:
        return sum(
            int(x.get("assignment_count", 0))
            for x in self.scan_target_history.values()
        )

    def unique_scan_target_count(self) -> int:
        return len(self.scan_target_history)

    def append_kpi(self, status: dict[str, Any], keep: int = 200) -> None:
        self.kpi_history.append(dict(status))
        if len(self.kpi_history) > keep:
            self.kpi_history = self.kpi_history[-keep:]
