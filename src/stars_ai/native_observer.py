
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any
import json

from .native.player_state import PlayerState


@dataclass
class ObserverPlayer:
    player_id: int
    name: str
    prt: str
    planets: int
    population: int
    factories: int
    mines: int
    defenses: int
    fleets: int
    ships: int
    fleet_mass: int
    tech_sum: int
    starbases: int
    score: float = 0.0


@dataclass
class ObserverTurn:
    turn: int
    year: int
    players: list[ObserverPlayer]
    planet_owners: dict[str, int | None]
    fleet_signature: dict[str, dict[str, int]]
    events: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _ship_count(f: Any) -> int:
    vals=getattr(f,"ship_count",[]) or []
    if isinstance(vals,dict):
        return sum(int(v or 0) for v in vals.values())
    return sum(int(v or 0) for v in vals)


def _player_name(p: Any, pid:int) -> str:
    return (
        getattr(p,"name_singular",None)
        or getattr(p,"name_plural",None)
        or f"Player {pid}"
    )


def _player_prt(p: Any) -> str:
    race=getattr(p,"race",None)
    return getattr(race,"prt_name","Unknown") if race else "Unknown"


def _player_tech_sum(p: Any) -> int:
    race=getattr(p,"race",None)
    tech=getattr(race,"tech",None) if race else None
    if tech is None:
        return 0
    return sum(int(getattr(tech,n,0) or 0) for n in
               ("energy","weapons","propulsion","construction","electronics","biotechnology"))


def read_observer_turn(hst_path: str|Path, xy_path: str|Path, turn:int) -> ObserverTurn:
    """
    Read the host file as the omniscient observer. PlayerState's native parser
    is intentionally tolerant of blocks it does not yet decode, while planets,
    fleets, player/race records and designs are sufficient for macro reporting.
    """
    state=PlayerState.from_files(hst_path,xy_path)
    pmap={p.player_number:p for p in state.players}
    planets=list(state.best_planets().values())
    fleets=list(state.best_fleets().values())

    ids=set(pmap)
    ids.update(p.owner for p in planets if p.owner is not None)
    ids.update(f.owner for f in fleets if f.owner is not None)

    players=[]
    for pid in sorted(ids):
        pobj=pmap.get(pid)
        owned=[p for p in planets if p.owner==pid]
        ownf=[f for f in fleets if f.owner==pid]
        pop=sum(int(getattr(p,"population",0) or 0) for p in owned)
        factories=sum(int(getattr(p,"factories",0) or 0) for p in owned)
        mines=sum(int(getattr(p,"mines",0) or 0) for p in owned)
        defenses=sum(int(getattr(p,"defenses",0) or 0) for p in owned)
        ships=sum(_ship_count(f) for f in ownf)
        mass=sum(int(getattr(f,"mass",0) or 0) for f in ownf)
        tech=_player_tech_sum(pobj) if pobj else 0
        starbases=sum(1 for p in owned if bool(getattr(p,"has_starbase",False)))

        # Macro score is deliberately transparent rather than claiming to equal
        # Stars!' built-in score.
        score=(
            len(owned)*100.0
            + pop/10000.0
            + factories*1.5
            + mines*0.4
            + ships*8.0
            + mass/100.0
            + tech*35.0
            + starbases*40.0
        )
        players.append(ObserverPlayer(
            pid,_player_name(pobj,pid),_player_prt(pobj),
            len(owned),pop,factories,mines,defenses,len(ownf),ships,mass,tech,starbases,score
        ))

    owners={str(p.planet_id):p.owner for p in planets}
    fsig={
        f"{f.owner}:{f.fleet_id}":{
            "owner":int(f.owner),
            "fleet_id":int(f.fleet_id),
            "ships":_ship_count(f),
            "mass":int(getattr(f,"mass",0) or 0),
            "x":int(getattr(f,"x",0) or 0),
            "y":int(getattr(f,"y",0) or 0),
        }
        for f in fleets
    }
    return ObserverTurn(
        turn=turn,
        year=int(state.header.get("year",2400+turn)),
        players=players,
        planet_owners=owners,
        fleet_signature=fsig,
    )


def _pmap(obs: ObserverTurn) -> dict[int,ObserverPlayer]:
    return {p.player_id:p for p in obs.players}


def _fleet_loss_evidence(
    previous: ObserverTurn,
    current: ObserverTurn,
    player_id: int,
) -> list[dict[str, int | str]]:
    """Describe individual fleet changes behind a player-level loss total.

    A native observer snapshot is omniscient but does not yet decode the
    battle report.  This evidence is deliberately factual: a fleet either
    disappeared between snapshots or its visible ship count decreased.  It
    does not guess whether the cause was battle, scrapping, or another action.
    """
    old_fleets={
        key:value
        for key,value in previous.fleet_signature.items()
        if int(value.get("owner",-1))==player_id
    }
    new_fleets={
        key:value
        for key,value in current.fleet_signature.items()
        if int(value.get("owner",-1))==player_id
    }
    evidence=[]
    for key,old_fleet in old_fleets.items():
        old_ships=int(old_fleet.get("ships",0) or 0)
        old_mass=int(old_fleet.get("mass",0) or 0)
        new_fleet=new_fleets.get(key)
        if new_fleet is None:
            evidence.append({
                "fleet_id":int(old_fleet.get("fleet_id",-1)),
                "status":"disappeared",
                "ships_before":old_ships,
                "ships_lost":old_ships,
                "mass_before":old_mass,
                "mass_lost":old_mass,
                "last_x":int(old_fleet.get("x",0) or 0),
                "last_y":int(old_fleet.get("y",0) or 0),
            })
            continue

        new_ships=int(new_fleet.get("ships",0) or 0)
        new_mass=int(new_fleet.get("mass",0) or 0)
        if new_ships < old_ships or new_mass < old_mass:
            evidence.append({
                "fleet_id":int(old_fleet.get("fleet_id",-1)),
                "status":"reduced",
                "ships_before":old_ships,
                "ships_after":new_ships,
                "ships_lost":max(0,old_ships-new_ships),
                "mass_before":old_mass,
                "mass_after":new_mass,
                "mass_lost":max(0,old_mass-new_mass),
                "last_x":int(old_fleet.get("x",0) or 0),
                "last_y":int(old_fleet.get("y",0) or 0),
            })
    return sorted(evidence,key=lambda item:(-int(item["ships_lost"]),int(item["fleet_id"])))


def _fleet_loss_text(
    *,
    player_id: int,
    previous: ObserverTurn,
    current: ObserverTurn,
    old: ObserverPlayer,
    new: ObserverPlayer,
    ship_loss: int,
    mass_loss: int,
    evidence: list[dict[str, int | str]],
) -> str:
    details=[]
    for item in evidence:
        fleet_id=int(item["fleet_id"])
        location=f"last seen at ({item['last_x']}, {item['last_y']})"
        if item["status"]=="disappeared":
            details.append(
                f"fleet ID {fleet_id} disappeared "
                f"({item['ships_before']} ships; {location})"
            )
        else:
            details.append(
                f"fleet ID {fleet_id} was reduced "
                f"({item['ships_before']}->{item['ships_after']} ships; {location})"
            )

    totals=(
        f"ships {old.ships}->{new.ships}; fleets {old.fleets}->{new.fleets}"
    )
    if old.fleet_mass or new.fleet_mass:
        totals+=f"; fleet mass {old.fleet_mass}->{new.fleet_mass}"
    summary=(
        f"P{player_id} suffered a major net fleet loss during Turn {current.turn} "
        f"/ Year {current.year} ({totals}; net loss {ship_loss} ships"
    )
    if mass_loss:
        summary+=f", {mass_loss} fleet mass"
    summary+=")."
    if details:
        return f"{summary} Observed fleet change: {'; '.join(details)}. Cause is not decoded."
    return f"{summary} No individual fleet disappearance was decoded; cause is not decoded."


def derive_turn_events(previous: ObserverTurn|None, current: ObserverTurn) -> list[dict]:
    if previous is None:
        return []
    events=[]
    prevp=_pmap(previous); curp=_pmap(current)

    # Planet ownership changes are our strongest unambiguous evidence of conflict.
    for planet_id, new_owner in current.planet_owners.items():
        old_owner=previous.planet_owners.get(planet_id)
        if old_owner != new_owner:
            if old_owner is None and new_owner is not None:
                events.append({
                    "type":"colonization","planet_id":int(planet_id),
                    "player":new_owner,
                    "severity":"notable",
                    "text":f"P{new_owner} colonized planet #{int(planet_id)+1}."
                })
            elif old_owner is not None and new_owner is None:
                events.append({
                    "type":"planet_lost","planet_id":int(planet_id),
                    "from":old_owner,
                    "severity":"major",
                    "text":f"P{old_owner} lost control of planet #{int(planet_id)+1}."
                })
            elif old_owner is not None and new_owner is not None:
                events.append({
                    "type":"capture","planet_id":int(planet_id),
                    "from":old_owner,"to":new_owner,
                    "severity":"major",
                    "text":f"P{new_owner} captured planet #{int(planet_id)+1} from P{old_owner}."
                })

    # Large abrupt ship/mass losses are useful battle indicators even when the
    # exact Stars! battle block is not decoded yet.
    for pid,p in curp.items():
        old=prevp.get(pid)
        if not old:
            continue
        ship_loss=old.ships-p.ships
        mass_loss=old.fleet_mass-p.fleet_mass
        if ship_loss >= max(3,int(old.ships*0.20)) or mass_loss >= max(500,int(old.fleet_mass*0.25)):
            evidence=_fleet_loss_evidence(previous,current,pid)
            events.append({
                "type":"major_fleet_loss",
                "player":pid,
                "turn":current.turn,
                "year":current.year,
                "previous_turn":previous.turn,
                "previous_year":previous.year,
                "ships_before":old.ships,
                "ships_after":p.ships,
                "fleets_before":old.fleets,
                "fleets_after":p.fleets,
                "ships_lost_net":max(0,ship_loss),
                "mass_lost_net":max(0,mass_loss),
                "affected_fleets":evidence,
                "severity":"major",
                "text":_fleet_loss_text(
                    player_id=pid,previous=previous,current=current,old=old,new=p,
                    ship_loss=max(0,ship_loss),mass_loss=max(0,mass_loss),evidence=evidence,
                ),
            })
    return events


def save_observer_turn(path: str|Path, obs: ObserverTurn) -> None:
    Path(path).write_text(json.dumps(obs.to_dict(),indent=2),encoding="utf-8")


def load_observer_turn(path: str|Path) -> ObserverTurn:
    d=json.loads(Path(path).read_text(encoding="utf-8"))
    return ObserverTurn(
        turn=d["turn"],year=d["year"],
        players=[ObserverPlayer(**p) for p in d["players"]],
        planet_owners=d["planet_owners"],
        fleet_signature=d["fleet_signature"],
        events=d.get("events",[]),
    )


def _observer_controller(player: ObserverPlayer, personas: dict[str,str]) -> str:
    """Use the configured AI persona when present; otherwise retain neutral ownership."""
    persona=personas.get(str(player.player_id))
    return f"AI/{persona}" if persona else "external/human"


def build_turn_status_report(
    current: ObserverTurn,
    previous: ObserverTurn|None=None,
    *,
    personas: dict[str,str]|None=None,
) -> str:
    """Render one concise, durable observer section for the running report."""
    personas=personas or {}
    previous_players=_pmap(previous) if previous else {}
    lines=[
        f"## Turn {current.turn:03d} - Year {current.year}",
        "",
        "| Player | Controller | Planets | Population | Ships | Fleets | Fleet mass | Starbases |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for player in sorted(current.players,key=lambda p:p.player_id):
        old=previous_players.get(player.player_id)
        planets_delta=(f" ({player.planets-old.planets:+d})" if old else "")
        population_delta=(f" ({player.population-old.population:+,})" if old else "")
        ships_delta=(f" ({player.ships-old.ships:+d})" if old else "")
        lines.append(
            f"| P{player.player_id} {player.name} ({player.prt}) | "
            f"{_observer_controller(player,personas)} | "
            f"{player.planets:,}{planets_delta} | {player.population:,}{population_delta} | "
            f"{player.ships:,}{ships_delta} | {player.fleets:,} | "
            f"{player.fleet_mass:,} | {player.starbases:,} |"
        )

    major_events=[e for e in current.events if e.get("severity")=="major" or e.get("type") in {
        "capture","planet_lost","major_fleet_loss",
    }]
    other_events=[e for e in current.events if e not in major_events]
    lines.extend(["", "**Major events**"])
    if major_events:
        lines.extend(f"- {event.get('text','Unspecified major observer event.')}" for event in major_events)
    else:
        lines.append("- No planet capture, loss, or major net fleet-loss event detected this turn.")
    if other_events:
        lines.extend(["", "**Other notable events**"])
        lines.extend(f"- {event.get('text','Unspecified observer event.')}" for event in other_events)
    lines.append("")
    return "\n".join(lines)


def build_running_game_report(
    history: list[ObserverTurn],
    *,
    personas: dict[str,str]|None=None,
    major_report_turns: list[int]|tuple[int,...]|set[int]|None=None,
) -> str:
    """Build one chronological game report from immutable observer snapshots.

    Every completed turn gets its own player-status section.  Configured
    checkpoint turns additionally receive the existing deeper observer report,
    so a reader can follow the whole game without opening separate files.
    """
    personas=personas or {}
    if not history:
        return "# Stars! AI Running Game Report\n\nNo observer snapshots are available.\n"

    ordered=sorted(history,key=lambda obs:obs.turn)
    visible=[obs for obs in ordered if obs.turn > 0]
    if not visible:
        return "# Stars! AI Running Game Report\n\nNo completed turns are available yet.\n"
    major_turns={int(turn) for turn in (major_report_turns or [])}
    lines=["# Stars! AI Running Game Report", ""]
    prior_major: ObserverTurn|None=None
    for current in visible:
        all_index=ordered.index(current)
        previous=ordered[all_index-1] if all_index else None
        lines.append(build_turn_status_report(current,previous,personas=personas).rstrip())
        if current.turn in major_turns:
            lines.extend([
                "",
                f"### Major report - Turn {current.turn:03d}",
                "",
                "```text",
                build_human_report(
                    current,
                    ordered[:all_index+1],
                    personas=personas,
                    checkpoint_from=prior_major,
                ).rstrip(),
                "```",
            ])
            prior_major=current
        lines.append("")
    return "\n".join(lines).rstrip()+"\n"


def build_human_report(
    current: ObserverTurn,
    history: list[ObserverTurn],
    *,
    personas: dict[str,str]|None=None,
    checkpoint_from: ObserverTurn|None=None,
) -> str:
    personas=personas or {}
    ps=sorted(current.players,key=lambda p:p.score,reverse=True)
    if not ps:
        return "No observer player data could be decoded.\n"

    leader=ps[0]
    second=ps[1] if len(ps)>1 else None
    gap=(leader.score-second.score) if second else 0
    gap_pct=(gap/second.score*100.0) if second and second.score else 0.0

    recent_events=[]
    start_turn=(checkpoint_from.turn+1) if checkpoint_from else max(1,current.turn-9)
    for h in history:
        if h.turn >= start_turn and h.turn <= current.turn:
            recent_events.extend(h.events)

    captures=[e for e in recent_events if e.get("type")=="capture"]
    fleet_losses=[e for e in recent_events if e.get("type")=="major_fleet_loss"]
    fighting=bool(captures or fleet_losses)

    lines=[]
    lines.append(f"Stars! AI Observer Report - Turn {current.turn} / Year {current.year}")
    lines.append("="*66)
    lines.append("")

    # Executive assessment
    if gap_pct >= 30:
        lead_phrase=f"P{leader.player_id} {leader.name} has a clear lead"
    elif gap_pct >= 12:
        lead_phrase=f"P{leader.player_id} {leader.name} is leading"
    else:
        lead_phrase=f"The game remains competitive; P{leader.player_id} {leader.name} currently has a narrow lead"
    lines.append("EXECUTIVE SUMMARY")
    lines.append("-----------------")
    lines.append(f"{lead_phrase}. Observer score gap over second place: {gap_pct:.1f}%.")
    if fighting:
        if captures:
            lines.append(f"Yes - fighting/conquest has begun. {len(captures)} enemy planet capture(s) occurred in the reporting window.")
        else:
            lines.append("Likely fighting has begun: major fleet losses are visible, although no enemy planet changed hands in this window.")
    else:
        lines.append("No clear evidence of player-vs-player fighting yet; expansion and buildup still dominate.")
    lines.append("")

    lines.append("CURRENT STANDINGS")
    lines.append("-----------------")
    for rank,p in enumerate(ps,1):
        persona=personas.get(str(p.player_id),"")
        tag=f" / {persona}" if persona else ""
        lines.append(
            f"{rank}. P{p.player_id} {p.name} [{p.prt}{tag}] - "
            f"{p.planets} planets, pop {p.population:,}, {p.factories} factories, "
            f"{p.ships} ships / {p.fleet_mass:,} fleet mass, tech total {p.tech_sum}, "
            f"{p.starbases} starbases."
        )
    lines.append("")

    # Trend since last checkpoint
    if checkpoint_from:
        prev=_pmap(checkpoint_from)
        lines.append(f"CHANGE SINCE TURN {checkpoint_from.turn}")
        lines.append("-"*29)
        for p in ps:
            old=prev.get(p.player_id)
            if not old:
                continue
            lines.append(
                f"P{p.player_id}: planets {p.planets-old.planets:+d}, "
                f"population {p.population-old.population:+,}, "
                f"factories {p.factories-old.factories:+d}, "
                f"ships {p.ships-old.ships:+d}, "
                f"fleet mass {p.fleet_mass-old.fleet_mass:+,}, "
                f"tech total {p.tech_sum-old.tech_sum:+d}."
            )
        lines.append("")

    lines.append("WAR & CONTACT")
    lines.append("-------------")
    if captures:
        for e in captures[-12:]:
            lines.append(f"- {e['text']}")
    if fleet_losses:
        for e in fleet_losses[-12:]:
            lines.append(f"- {e['text']}")
    if not captures and not fleet_losses:
        lines.append("- No captures or major net fleet-loss events detected in this reporting window.")
    lines.append("")

    # Strength category leaders
    def best(attr):
        return max(ps,key=lambda p:getattr(p,attr))
    lines.append("WHO IS STRONGEST?")
    lines.append("-----------------")
    lines.append(f"- Territory: P{best('planets').player_id} with {best('planets').planets} planets.")
    lines.append(f"- Population: P{best('population').player_id} with {best('population').population:,}.")
    lines.append(f"- Industry: P{best('factories').player_id} with {best('factories').factories} factories.")
    lines.append(f"- Fleet by mass: P{best('fleet_mass').player_id} with {best('fleet_mass').fleet_mass:,}.")
    lines.append(f"- Technology: P{best('tech_sum').player_id} with tech total {best('tech_sum').tech_sum}.")
    lines.append("")

    # Winners / losers
    bottom=ps[-1]
    lines.append("MOMENTUM / RISK")
    lines.append("---------------")
    lines.append(f"- Current leader: P{leader.player_id} {leader.name}.")
    lines.append(f"- Current last place: P{bottom.player_id} {bottom.name}.")
    for p in ps:
        # Flag obvious weakness relative to leader.
        if leader.score and p.score < leader.score*0.60:
            lines.append(f"- P{p.player_id} is materially behind the leader (<60% of leader observer score).")
        if p.planets == 0:
            lines.append(f"- P{p.player_id} has no planets and is effectively eliminated.")
    lines.append("")

    # PRT-specific observer commentary based on what can actually be seen today.
    for p in ps:
        if p.prt=="SS":
            lines.append("SUPER STEALTH WATCH")
            lines.append("-------------------")
            lines.append(
                f"P{p.player_id}: {p.fleets} fleets / {p.ships} ships. "
                "The observer can see the real force, but SS theft/interception totals "
                "will remain incomplete until Robber Baron/packet native events are decoded."
            )
            lines.append("")
        elif p.prt=="SD":
            lines.append("SPACE DEMOLITION WATCH")
            lines.append("----------------------")
            lines.append(
                f"P{p.player_id}: strategic performance is included in standings. "
                "Exact layered-field/detonation reporting will improve when mine-object changes "
                "are decoded into the observer event stream."
            )
            lines.append("")

    lines.append("INTERPRETATION")
    lines.append("--------------")
    lines.append(
        "Observer score is a transparent diagnostic composite, not the Stars! built-in score. "
        "Planet captures are strong evidence of war; abrupt fleet losses are treated as probable combat "
        "but can also reflect scrapping/merging until battle blocks are fully decoded."
    )
    lines.append("")
    return "\n".join(lines)
