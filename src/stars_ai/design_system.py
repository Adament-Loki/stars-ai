from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .player_tech import PlayerRaceTech, player_race_tech_from_file
from .standard_mod import (
    ModDatabase,
    ComponentSpec,
    parse_mod_file,
    legal_available_components_for_slot,
    availability_snapshot,
)
from .design_legality import ComponentRef, ValidationResult
from .standard_mod import validate_design_against_mod


@dataclass(frozen=True)
class PlayerDesignSystem:
    race_tech: PlayerRaceTech
    database: ModDatabase

    @classmethod
    def from_files(cls, m_file: str | Path, mod_file: str | Path) -> "PlayerDesignSystem":
        return cls(
            race_tech=player_race_tech_from_file(m_file),
            database=parse_mod_file(mod_file),
        )

    def available_components(self) -> list[dict]:
        return availability_snapshot(self.database, self.race_tech.tech)

    def legal_components(self, hull_id: int, slot_index: int) -> list[ComponentSpec]:
        return legal_available_components_for_slot(
            hull_id, slot_index, self.database, self.race_tech.tech
        )

    def validate(self, hull_id: int, components: list[ComponentRef]) -> ValidationResult:
        return validate_design_against_mod(
            hull_id, components, self.database, self.race_tech.tech
        )
