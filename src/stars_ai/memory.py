
from __future__ import annotations

import json
import math
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
    schema_version: int = 6
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
    fleet_movement_history: dict[str, dict[str, Any]] = field(default_factory=dict)

    # v7.4 command lifecycle ledger. Only commands that survive native writer
    # validation are recorded. Autohost promotes this memory transaction only
    # after Stars! accepts the X file, so a committed expectation represents a
    # command that really entered the host cycle.
    action_expectations: dict[str, dict[str, Any]] = field(default_factory=dict)
    action_outcome_history: list[dict[str, Any]] = field(default_factory=list)

    # Capability-oriented research continuity. This keeps a valuable unlock from
    # being abandoned for a nearly tied challenger and records sprint outcomes.
    research_state: dict[str, Any] = field(default_factory=dict)
    research_history: list[dict[str, Any]] = field(default_factory=list)

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
        # Loading an older file is the schema migration: missing v7.4 fields
        # take their dataclass defaults and the next save advertises v6.
        filtered["schema_version"]=cls().schema_version
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
            # Environmental discoveries are durable. Age remains diagnostic,
            # but an explored world never re-enters the scout work queue.
            p.native["intel_needs_refresh"] = False
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

    def _record_scan_payload(self, payload: dict[str,Any], year: int) -> None:
        pid = payload.get("destination_planet_id")
        if pid is None or payload.get("deconflicted_hold"):
            return
        key = str(int(pid))
        info = dict(self.scan_target_history.get(key, {}))
        info["last_assigned_year"] = int(year)
        info["assignment_count"] = int(info.get("assignment_count", 0)) + 1
        fid=int(payload.get("fleet_id", -1))
        info["last_fleet_id"] = fid
        self.scan_target_history[key] = info

        if payload.get("route_managed"):
            rkey=str(fid)
            route=dict(self.scout_routes.get(rkey,{}) or {})
            raw_waypoints=payload.get("route_waypoints") or []
            waypoints=[
                {
                    "planet_id":int(x["planet_id"]),
                    "warp":int(x["warp"]),
                    "task":int(x.get("task",0)),
                }
                for x in raw_waypoints
                if isinstance(x,dict)
                and x.get("planet_id") is not None
                and x.get("warp") is not None
            ]
            ids=[int(x) for x in payload.get("route_planet_ids",[])]
            if not ids:
                ids=[int(x["planet_id"]) for x in waypoints]
            if not ids:
                ids=[int(pid)]
            dedup=[]; seen=set()
            for x in ids:
                if x in seen: continue
                seen.add(x); dedup.append(x)
            route["planet_ids"]=dedup
            route["waypoints"]=[
                x for x in waypoints if int(x["planet_id"]) in seen
            ]
            route["updated_year"]=int(year)
            route["native_queued"]=True
            self.scout_routes[rkey]=route

    def record_scan_orders(self, orders, year: int) -> None:
        """Record semantic scan orders for non-native adapters."""
        for o in orders.orders:
            if o.kind != "move_fleet":
                continue
            if str(o.payload.get("mission", "")) not in ("scan", "recon"):
                continue
            self._record_scan_payload(o.payload,year)

    def record_emitted_scan_orders(self, emitted, year: int) -> None:
        """Record only scan routes that survived native writer validation."""
        for item in emitted:
            if item.get("kind") != "move_fleet":
                continue
            payload=item.get("payload") or {}
            mission=str(payload.get("mission", ""))
            if mission=="refuel_for_scan":
                ids=[int(x) for x in payload.get("planned_route_after_refuel",[])]
                if ids:
                    self.set_scout_route(
                        int(payload["fleet_id"]),ids,year,
                        expected_discoveries=int(
                            payload.get("planned_discoveries_after_refuel",len(ids))
                        ),
                        awaiting_refuel=True,
                        waypoints=list(payload.get("planned_route_waypoints") or []),
                    )
                continue
            if mission not in ("scan", "recon"):
                continue
            self._record_scan_payload(payload,year)

    @staticmethod
    def _travel_years(origin: tuple[float, float], target, warp: int) -> int:
        distance=math.hypot(
            float(target.position.x)-float(origin[0]),
            float(target.position.y)-float(origin[1]),
        )
        speed=max(1,int(warp)) ** 2
        return max(1,int(math.ceil(distance/float(speed))))

    @staticmethod
    def _ship_slot_totals(state) -> dict[str, int]:
        totals={str(i):0 for i in range(16)}
        for fleet in state.fleets:
            if fleet.owner!=state.player_id:
                continue
            counts=(fleet.native or {}).get("ship_count",[]) or []
            if isinstance(counts,int):
                continue
            for slot,count in enumerate(counts[:16]):
                totals[str(slot)]=totals.get(str(slot),0)+int(count or 0)
        return totals

    def _archive_action_outcome(self, outcome: dict[str, Any], keep: int = 300) -> None:
        self.action_outcome_history.append(dict(outcome))
        if len(self.action_outcome_history)>keep:
            self.action_outcome_history=self.action_outcome_history[-keep:]

    def _supersede_action_group(self, group: str, year: int) -> None:
        for key,expectation in list(self.action_expectations.items()):
            if expectation.get("group")!=group:
                continue
            archived={
                **dict(expectation),
                "status":"SUPERSEDED",
                "resolved_year":int(year),
                "message":(
                    f"SUPERSEDED - {expectation.get('description','prior command')} was "
                    "replaced by a newer emitted command."
                ),
            }
            self._archive_action_outcome(archived)
            self.action_expectations.pop(key,None)

    def _add_action_expectation(self, expectation: dict[str, Any]) -> None:
        key=str(expectation["id"])
        self.action_expectations[key]=dict(expectation)

    def record_emitted_actions(self, emitted, state) -> None:
        """Persist observable outcomes for native commands that were emitted.

        The writer calls this only after it has serialized and structurally
        validated the X transaction. Movement can create several expectations,
        one for every native route leg; other command families create one.
        """
        year=int(state.year)
        planets={int(p.id):p for p in state.planets}
        fleets={
            int(f.id):f for f in state.fleets
            if f.owner==state.player_id
        }
        ship_totals=self._ship_slot_totals(state)
        superseded=set()

        for emitted_index,item in enumerate(emitted):
            kind=str(item.get("kind", ""))
            payload=dict(item.get("payload") or {})

            if kind in ("move_fleet","colony_operation","transport_minerals"):
                fid=int(payload["fleet_id"])
                fleet=fleets.get(fid)
                target_id=payload.get("destination_planet_id")
                if fleet is None or target_id is None:
                    continue
                group=f"fleet:{fid}"
                if group not in superseded:
                    self._supersede_action_group(group,year)
                    superseded.add(group)

                route_specs=[]
                if kind=="move_fleet" and payload.get("route_managed"):
                    route_specs=[
                        dict(x) for x in (payload.get("route_waypoints") or [])
                        if isinstance(x,dict) and x.get("planet_id") is not None
                    ]
                    added=int(payload.get("native_waypoints_added",len(route_specs)) or 0)
                    route_specs=route_specs[:added]
                if not route_specs:
                    route_specs=[{
                        "planet_id":int(target_id),
                        "warp":int(payload.get("warp",fleet.speed or 1)),
                        "task":2 if kind=="colony_operation" else 0,
                    }]

                origin=(float(fleet.position.x),float(fleet.position.y))
                due=year
                for sequence,spec in enumerate(route_specs,1):
                    pid=int(spec["planet_id"])
                    target=planets.get(pid)
                    if target is None:
                        continue
                    warp=int(spec.get("warp",payload.get("warp",fleet.speed or 1)) or 0)
                    due+=self._travel_years(origin,target,warp)
                    mission=str(payload.get("mission", "move")).lower()
                    expectation_kind=(
                        "colonize" if kind=="colony_operation"
                        else "transport" if kind=="transport_minerals"
                        else "scan_arrival" if mission in ("scan","recon")
                        else "fleet_arrival"
                    )
                    target_name=str(target.name)
                    fleet_name=str(fleet.name)
                    intel=self.planet_intel.get(str(pid),{})
                    expectation={
                        "id":f"{year}:{kind}:{fid}:{sequence}:{pid}",
                        "group":group,
                        "kind":expectation_kind,
                        "command_kind":kind,
                        "fleet_id":fid,
                        "fleet_name":fleet_name,
                        "planet_id":pid,
                        "planet_name":target_name,
                        "mission":mission,
                        "issued_year":year,
                        "due_year":due,
                        "sequence":sequence,
                        "route_length":len(route_specs),
                        "warp":warp,
                        "target_x":float(target.position.x),
                        "target_y":float(target.position.y),
                        "baseline_owner":target.owner,
                        "baseline_last_seen_year":intel.get("last_seen_year"),
                        "description":(
                            f"{fleet_name} colonize {target_name}"
                            if expectation_kind=="colonize"
                            else f"{fleet_name} arrive at {target_name}"
                        ),
                    }
                    if expectation_kind=="transport":
                        expectation["unload"]={
                            str(k):str(v) for k,v in (payload.get("unload") or {}).items()
                        }
                    self._add_action_expectation(expectation)
                    origin=(float(target.position.x),float(target.position.y))
                continue

            if kind=="set_planet_queue":
                pid=int(payload["planet_id"])
                planet=planets.get(pid)
                if planet is None:
                    continue
                group=f"production:{pid}"
                self._supersede_action_group(group,year)
                queue=[dict(x) for x in (payload.get("queue") or [])]
                self._add_action_expectation({
                    "id":f"{year}:{kind}:{pid}:{emitted_index}",
                    "group":group,
                    "kind":"production_queue",
                    "command_kind":kind,
                    "planet_id":pid,
                    "planet_name":str(planet.name),
                    "issued_year":year,
                    "due_year":year+1,
                    "queue":queue,
                    "clear_queue":bool(payload.get("clear_queue",False)),
                    "baseline":{
                        "factory":int(planet.factories or 0),
                        "mine":int(planet.mines or 0),
                        "defense":int(planet.defenses or 0),
                        "ship_slots":ship_totals,
                        "starbase_design":(planet.native or {}).get("starbase_design"),
                        "habitability":planet.habitability,
                        "environment":list((planet.native or {}).get("environment") or []),
                    },
                    "description":f"{planet.name} production queue",
                })
                continue

            if kind=="set_planet_research_mode":
                pid=int(payload["planet_id"])
                planet=planets.get(pid)
                if planet is None:
                    continue
                group=f"planet_research_mode:{pid}"
                self._supersede_action_group(group,year)
                self._add_action_expectation({
                    "id":f"{year}:{kind}:{pid}:{emitted_index}",
                    "group":group,
                    "kind":"planet_research_mode",
                    "command_kind":kind,
                    "planet_id":pid,
                    "planet_name":str(planet.name),
                    "leftover_only":bool(payload.get("leftover_only",False)),
                    "issued_year":year,
                    "due_year":year+1,
                    "description":f"{planet.name} leftover-only research mode",
                })
                continue

            if kind=="set_research":
                group="research:empire"
                self._supersede_action_group(group,year)
                tech={
                    field:int(getattr(state.tech,field,0) or 0)
                    for field in (
                        "energy","weapons","propulsion","construction",
                        "electronics","biotechnology"
                    )
                }
                field_name=str(payload.get("current_field",payload.get("field","unknown"))).lower()
                self._add_action_expectation({
                    "id":f"{year}:{kind}:{emitted_index}",
                    "group":group,
                    "kind":"research",
                    "command_kind":kind,
                    "field":field_name,
                    "next_field":str(payload.get("next_field",field_name)).lower(),
                    "allocation_percent":int(payload.get("allocation_percent",15)),
                    "capability_id":payload.get("capability_id"),
                    "baseline_tech":tech,
                    "issued_year":year,
                    "due_year":year+1,
                    "description":f"Empire research switch to {field_name}",
                })
                continue

            if kind=="set_player_relation":
                target=int(payload["player_id"])
                group=f"relation:{target}"
                self._supersede_action_group(group,year)
                self._add_action_expectation({
                    "id":f"{year}:{kind}:{target}:{emitted_index}",
                    "group":group,
                    "kind":"player_relation",
                    "command_kind":kind,
                    "target_player_id":target,
                    "relation":str(payload.get("relation","unknown")).lower(),
                    "issued_year":year,
                    "due_year":year+1,
                    "description":f"Player {target} relation change",
                })

    @staticmethod
    def _fleet_at_planet(fleet, planet) -> bool:
        return bool(
            fleet is not None
            and abs(float(fleet.position.x)-float(planet.position.x))<=0.5
            and abs(float(fleet.position.y)-float(planet.position.y))<=0.5
        )

    @staticmethod
    def _production_queue_for(state, planet_id: int) -> list[dict[str, Any]]:
        raw=(state.native or {}).get("production_by_planet",{}) or {}
        value=raw.get(str(int(planet_id)),raw.get(int(planet_id),[]))
        return [dict(x) for x in (value or []) if isinstance(x,dict)]

    @staticmethod
    def _queue_item_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
        item=str(expected.get("item","")).lower()
        actual_name=str(actual.get("item_name",actual.get("item",""))).lower()
        if item=="ship_design":
            slot=expected.get("design_slot")
            return slot is not None and actual_name==f"designslot#{int(slot)}".lower()
        if item=="starbase_design":
            slot=expected.get("design_slot")
            return slot is not None and actual_name==f"starbasedesignslot#{int(slot)}".lower()
        aliases={
            "factory":"factory",
            "mine":"mine",
            "defense":"defenses",
            "max_terraform":"max terraform (auto build)",
        }
        return actual_name==aliases.get(item,item)

    def _evaluate_one_action(self, expectation: dict[str, Any], state) -> tuple[str,str]:
        year=int(state.year)
        due=int(expectation.get("due_year",year))
        kind=str(expectation.get("kind",""))
        planets={int(p.id):p for p in state.planets}
        fleets={
            int(f.id):f for f in state.fleets
            if f.owner==state.player_id
        }

        if kind in ("fleet_arrival","scan_arrival","transport"):
            fid=int(expectation["fleet_id"])
            pid=int(expectation["planet_id"])
            fleet=fleets.get(fid)
            planet=planets.get(pid)
            fleet_name=str(expectation.get("fleet_name",f"Fleet #{fid+1}"))
            planet_name=str(expectation.get("planet_name",f"Planet #{pid+1}"))
            arrived=planet is not None and self._fleet_at_planet(fleet,planet)
            observed=False
            if kind=="scan_arrival":
                intel=self.planet_intel.get(str(pid),{})
                last_seen=intel.get("last_seen_year")
                baseline=expectation.get("baseline_last_seen_year")
                observed=bool(
                    last_seen is not None
                    and int(last_seen)>int(baseline if baseline is not None else -1)
                    and int(last_seen)>int(expectation.get("issued_year",year))
                )
            completed=observed if kind=="scan_arrival" else arrived
            if completed:
                if kind=="transport" and fleet is not None:
                    cargo=(fleet.native or {}).get("cargo",{}) or {}
                    unload=expectation.get("unload",{}) or {}
                    remaining={
                        name:int(cargo.get(name,0) or 0)
                        for name,value in unload.items()
                        if str(value).lower()=="all" and int(cargo.get(name,0) or 0)>0
                    }
                    if remaining:
                        if year<due:
                            return "PENDING",(
                                f"PENDING - {fleet_name} arrived at {planet_name}; "
                                f"transport unload is due in {due}."
                            )
                        return "WARNING",(
                            f"WARNING - {fleet_name} should have unloaded at {planet_name} "
                            f"this turn but failed to do so (cargo remaining {remaining})."
                        )
                if kind=="scan_arrival":
                    return "COMPLETED",(
                        f"COMPLETED - {fleet_name} explored {planet_name} by year {year}."
                    )
                return "COMPLETED",(
                    f"COMPLETED - {fleet_name} arrived at {planet_name} by year {year}."
                )
            if year<due:
                return "PENDING",(
                    f"PENDING - {fleet_name} should arrive at {planet_name} by year {due}."
                )
            if kind=="scan_arrival" and arrived:
                return "WARNING",(
                    f"WARNING - {fleet_name} should have explored {planet_name} this turn "
                    "but failed to do so (fleet arrived, but no new planet observation "
                    f"was recorded; due year {due})."
                )
            detail=(
                "fleet is absent from the current M file"
                if fleet is None else
                f"current position is ({fleet.position.x:.0f},{fleet.position.y:.0f})"
            )
            return "WARNING",(
                f"WARNING - {fleet_name} should have arrived at {planet_name} this turn "
                f"but failed to do so ({detail}; due year {due})."
            )

        if kind=="colonize":
            fid=int(expectation["fleet_id"])
            pid=int(expectation["planet_id"])
            fleet_name=str(expectation.get("fleet_name",f"Fleet #{fid+1}"))
            planet_name=str(expectation.get("planet_name",f"Planet #{pid+1}"))
            planet=planets.get(pid)
            if planet is not None and planet.owner==state.player_id:
                return "COMPLETED",(
                    f"COMPLETED - {fleet_name} colonized {planet_name} by year {year}."
                )
            if year<due:
                return "PENDING",(
                    f"PENDING - {fleet_name} should colonize {planet_name} by year {due}."
                )
            owner="unowned" if planet is None or planet.owner is None else f"Player {planet.owner}"
            return "WARNING",(
                f"WARNING - {fleet_name} should have colonized {planet_name} this turn "
                f"but failed to do so (current owner: {owner}; due year {due})."
            )

        if kind=="production_queue":
            pid=int(expectation["planet_id"])
            planet=planets.get(pid)
            name=str(expectation.get("planet_name",f"Planet #{pid+1}"))
            queue=self._production_queue_for(state,pid)
            expected=list(expectation.get("queue") or [])
            clear=bool(expectation.get("clear_queue",False))
            applied=clear and not queue
            if not applied and expected:
                applied=any(
                    self._queue_item_matches(item,actual)
                    for item in expected for actual in queue
                )
            baseline=expectation.get("baseline",{}) or {}
            if not applied and planet is not None:
                for item in expected:
                    item_name=str(item.get("item","")).lower()
                    if item_name in ("factory","mine","defense"):
                        attr={"factory":"factories","mine":"mines","defense":"defenses"}[item_name]
                        if int(getattr(planet,attr,0) or 0)>int(baseline.get(item_name,0) or 0):
                            applied=True
                    elif item_name=="ship_design" and item.get("design_slot") is not None:
                        slot=str(int(item["design_slot"]))
                        current=self._ship_slot_totals(state).get(slot,0)
                        before=(baseline.get("ship_slots",{}) or {}).get(slot,0)
                        if int(current)>int(before):
                            applied=True
                    elif item_name=="starbase_design" and item.get("design_slot") is not None:
                        desired=int(item["design_slot"])
                        before=baseline.get("starbase_design")
                        current=(planet.native or {}).get("starbase_design")
                        if current is not None and int(current)==desired and (
                            before is None or int(before)!=desired
                        ):
                            applied=True
                    elif item_name=="max_terraform":
                        before_env=tuple(baseline.get("environment") or ())
                        current_env=tuple((planet.native or {}).get("environment") or ())
                        before_hab=baseline.get("habitability")
                        current_hab=planet.habitability
                        if (
                            len(before_env)>=3 and len(current_env)>=3
                            and current_env[:3]!=before_env[:3]
                        ) or (
                            before_hab is not None and current_hab is not None
                            and int(current_hab)>int(before_hab)
                        ):
                            applied=True
            if applied:
                return "COMPLETED",(
                    f"COMPLETED - {name} production command was applied by year {year}."
                )
            if year<due:
                return "PENDING",(
                    f"PENDING - {name} production command should be visible by year {due}."
                )
            if planet is None or planet.owner!=state.player_id:
                detail="planet is no longer an owned observable production site"
            else:
                detail="no matching queue or observable production progress was found"
            return "WARNING",(
                f"WARNING - {name} production command should have been applied this turn "
                f"but failed verification ({detail}; due year {due})."
            )

        if kind=="research":
            field_name=str(expectation.get("field","unknown"))
            next_expected=str(expectation.get("next_field",field_name))
            percent_expected=int(expectation.get("allocation_percent",15))
            current=str((state.native or {}).get("current_research_field") or "").lower()
            next_actual=str((state.native or {}).get("next_research_field") or "").lower()
            percent_actual=(state.native or {}).get("research_allocation_percent")
            before=int((expectation.get("baseline_tech",{}) or {}).get(field_name,0) or 0)
            now=int(getattr(state.tech,field_name,0) or 0)
            setting_matches=(
                current==field_name
                and (not next_actual or next_actual==next_expected)
                and (percent_actual is None or int(percent_actual)==percent_expected)
            )
            if setting_matches or now>before:
                return "COMPLETED",(
                    f"COMPLETED - Empire research is executing {field_name} -> "
                    f"{next_expected} at {percent_expected}%."
                )
            if year<due:
                return "PENDING",(
                    f"PENDING - Empire research should switch to {field_name} by year {due}."
                )
            if current:
                return "WARNING",(
                    f"WARNING - Empire research should have switched to {field_name} -> "
                    f"{next_expected} at {percent_expected}% this turn but failed to do so "
                    f"(actual: {current} -> {next_actual or '?'} at "
                    f"{percent_actual if percent_actual is not None else '?'}%)."
                )
            return "UNVERIFIED",(
                f"UNVERIFIED - Empire research should be on {field_name}, but the current M file "
                "does not expose a research-field setting and no level increase confirms it yet."
            )

        if kind=="planet_research_mode":
            pid=int(expectation["planet_id"])
            name=str(expectation.get("planet_name",f"Planet #{pid+1}"))
            actual=((state.native or {}).get("planet_research_modes") or {}).get(str(pid))
            if actual is not None and int(actual)==1:
                return "COMPLETED",(
                    f"COMPLETED - {name} is contributing leftover resources only."
                )
            if year<due:
                return "PENDING",(
                    f"PENDING - {name} leftover-only research mode should be visible by year {due}."
                )
            if actual is None:
                return "UNVERIFIED",(
                    f"UNVERIFIED - {name} should be in leftover-only research mode, but the current files "
                    "do not expose a PlanetChange value."
                )
            return "WARNING",(
                f"WARNING - {name} should be in leftover-only research mode this turn but failed "
                f"verification (native value: {actual})."
            )

        if kind=="player_relation":
            target=int(expectation["target_player_id"])
            relation=str(expectation.get("relation","unknown"))
            actual=list((state.native or {}).get(
                "actual_player_relations",
                (state.race.native or {}).get("player_relations",[]),
            ) or [])
            value=int(actual[target-1]) if 0<=target-1<len(actual) else None
            expected_code={"friend":1,"enemy":2}.get(relation)
            if expected_code is not None and value==expected_code:
                return "COMPLETED",(
                    f"COMPLETED - Player {target} relation is now {relation}."
                )
            if year<due:
                return "PENDING",(
                    f"PENDING - Player {target} relation should become {relation} by year {due}."
                )
            return "WARNING",(
                f"WARNING - Player {target} relation should have changed to {relation} this turn "
                f"but failed to do so (native relation code: {value})."
            )

        return "UNVERIFIED",(
            f"UNVERIFIED - No observable completion rule exists for "
            f"{expectation.get('description',kind)}."
        )

    def evaluate_action_outcomes(self, state) -> list[dict[str, Any]]:
        """Evaluate committed command expectations against the current M state."""
        outcomes=[]
        ordered=sorted(
            list(self.action_expectations.items()),
            key=lambda item:(
                int(item[1].get("due_year",state.year)),
                str(item[1].get("group","")),
                int(item[1].get("sequence",0)),
            ),
        )
        for key,expectation in ordered:
            if key not in self.action_expectations:
                continue
            sequence=int(expectation.get("sequence",0) or 0)
            if sequence>1:
                blocked=any(
                    other.get("group")==expectation.get("group")
                    and 0<int(other.get("sequence",0) or 0)<sequence
                    for other in self.action_expectations.values()
                )
                if blocked:
                    status="PENDING"
                    message=(
                        f"PENDING - {expectation.get('description','route leg')} waits on "
                        "an earlier route leg outcome."
                    )
                else:
                    status,message=self._evaluate_one_action(expectation,state)
            else:
                status,message=self._evaluate_one_action(expectation,state)
            outcome={
                **dict(expectation),
                "status":status,
                "checked_year":int(state.year),
                "message":message,
            }
            outcomes.append(outcome)
            if status=="COMPLETED":
                outcome["resolved_year"]=int(state.year)
                self._archive_action_outcome(outcome)
                self.action_expectations.pop(key,None)
            else:
                current=dict(self.action_expectations[key])
                current["last_status"]=status
                current["last_checked_year"]=int(state.year)
                if status=="WARNING":
                    current["overdue_turns"]=max(
                        1,int(state.year)-int(current.get("due_year",state.year))+1
                    )
                self.action_expectations[key]=current
        return outcomes



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
        waypoints: list[dict[str,int]] | None = None,
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
            "waypoints":[
                {
                    "planet_id":int(x["planet_id"]),
                    "warp":int(x["warp"]),
                    "task":int(x.get("task",0)),
                }
                for x in (waypoints or [])
            ],
        }

    def clear_scout_route(self, fleet_id: int) -> None:
        self.scout_routes.pop(str(int(fleet_id)),None)

    def sync_scout_routes_from_native(self, state) -> None:
        """Make queued Stars! waypoint chains authoritative for scout memory."""
        for fleet in state.fleets:
            if fleet.owner!=state.player_id or fleet.role not in ("scout","unknown"):
                continue
            raw=list((fleet.native or {}).get("waypoints") or [])
            if len(raw)<2:
                continue
            waypoints=[]
            for wp in raw[1:]:
                object_type=int(wp.get("position_object_type",0)) & 0x0f
                if object_type!=1 or wp.get("position_object") is None:
                    continue
                waypoints.append({
                    "planet_id":int(wp["position_object"]) & 0x7ff,
                    "warp":int(wp.get("warp",0)),
                    "task":int(wp.get("task",0)),
                })
            if not waypoints:
                continue
            key=str(int(fleet.id))
            route=dict(self.scout_routes.get(key,{}) or {})
            route.update({
                "fleet_id":int(fleet.id),
                "planet_ids":[int(x["planet_id"]) for x in waypoints],
                "waypoints":waypoints,
                "updated_year":int(state.year),
                "native_queued":True,
            })
            self.scout_routes[key]=route

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
                needs_recon=not planet.observed
                if (not needs_recon) or at_planet:
                    continue
                kept.append(int(pid))
            route["planet_ids"]=kept
            kept_set=set(kept)
            route["waypoints"]=[
                x for x in route.get("waypoints",[])
                if int(x.get("planet_id",-1)) in kept_set
            ]
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

    def update_movement_progress(self, state) -> list[dict[str, Any]]:
        """Compare native active-waypoint range across observed turns."""
        planets={int(p.id):p for p in state.planets}
        diagnostics=[]
        current_ids=set()
        for fleet in state.fleets:
            if fleet.owner!=state.player_id or fleet.destination_planet_id is None:
                continue
            target=planets.get(int(fleet.destination_planet_id))
            if target is None:
                continue
            key=str(int(fleet.id))
            current_ids.add(key)
            current_range=math.hypot(
                float(target.position.x)-float(fleet.position.x),
                float(target.position.y)-float(fleet.position.y),
            )
            warp=(
                fleet.destination_warp
                if fleet.destination_warp is not None
                else (fleet.native or {}).get("native_destination_warp")
            )
            task=(
                fleet.destination_task
                if fleet.destination_task is not None
                else (fleet.native or {}).get("native_destination_task")
            )
            prior=self.fleet_movement_history.get(key)
            row={
                "fleet_id":int(fleet.id),
                "destination_planet_id":int(fleet.destination_planet_id),
                "year":int(state.year),
                "current_range":round(current_range,3),
                "commanded_warp":int(warp) if warp is not None else None,
                "native_task":int(task) if task is not None else None,
                "prior_range":None,
                "expected_movement":None,
                "actual_progress":None,
                "suspicious":False,
                "suspicious_slow_turns":0,
                "flag":None,
            }
            repeated=0
            if (
                prior
                and int(prior.get("destination_planet_id",-1))==int(fleet.destination_planet_id)
                and int(prior.get("year",state.year)) < int(state.year)
            ):
                years=int(state.year)-int(prior["year"])
                prior_range=float(prior.get("range",current_range))
                actual=prior_range-current_range
                expected=None
                if warp is not None and int(warp)>0:
                    expected=min(prior_range,float(int(warp)**2*years))
                suspicious=bool(
                    expected is not None
                    and expected>=16.0
                    and prior_range>8.0
                    and actual < expected*0.35
                )
                repeated=(int(prior.get("suspicious_slow_turns",0))+1) if suspicious else 0
                row.update({
                    "prior_year":int(prior["year"]),
                    "prior_range":round(prior_range,3),
                    "expected_movement":round(expected,3) if expected is not None else None,
                    "actual_progress":round(actual,3),
                    "suspicious":suspicious,
                    "suspicious_slow_turns":repeated,
                })
                if repeated>=2:
                    row["flag"]=(
                        f"SUSPICIOUS MOVEMENT: W{warp} fleet advanced only "
                        f"{actual:.1f} ly versus approximately {expected:.1f} ly expected; "
                        f"slow progress observed {repeated} consecutive times."
                    )
            self.fleet_movement_history[key]={
                "fleet_id":int(fleet.id),
                "destination_planet_id":int(fleet.destination_planet_id),
                "year":int(state.year),
                "range":current_range,
                "commanded_warp":int(warp) if warp is not None else None,
                "native_task":int(task) if task is not None else None,
                "suspicious_slow_turns":repeated,
            }
            diagnostics.append(row)

        # Dead/idle fleets are not useful as future comparison baselines.
        for key in list(self.fleet_movement_history):
            if key not in current_ids:
                self.fleet_movement_history.pop(key,None)
        return diagnostics
