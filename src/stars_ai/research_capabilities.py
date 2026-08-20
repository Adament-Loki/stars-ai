from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .design_development import TECH_FIELDS, stock_hulls
from .terraforming import STANDARD_LEVELS, TOTAL_TERRAFORMING_LEVELS


@dataclass(frozen=True)
class ResearchCapability:
    """A named, actionable reason to acquire a precise six-field tech profile."""

    capability_id: str
    name: str
    category: str
    requirements: dict[str, int]
    post_unlock_action: str
    source: str
    executable: bool = True
    tags: tuple[str, ...] = ()

    def remaining(self, tech) -> dict[str, int]:
        return {
            field: max(0, int(level) - int(getattr(tech, field, 0) or 0))
            for field, level in self.requirements.items()
            if int(level) > int(getattr(tech, field, 0) or 0)
        }

    def unlocked(self, tech) -> bool:
        return not self.remaining(tech)

    def to_dict(self) -> dict:
        return asdict(self)


def _requirements(values: Iterable[int]) -> dict[str, int]:
    return {
        field: int(level)
        for field, level in zip(TECH_FIELDS, values)
        if int(level) > 0
    }


def stock_capability_catalog(
    *,
    include_ife: bool = False,
    total_terraforming: bool = False,
    improved_starbases: bool = False,
    prt_id: int | None = None,
) -> list[ResearchCapability]:
    """Build the catalog from bundled authoritative hull/terraforming data.

    Race legality belongs in the capability catalog, not only in the eventual
    ship/base designer.  In particular, Space Dock and Ultra Station are ISB
    (Improved Starbases) LRT hulls and must never become research goals for a
    race without ISB.
    """
    hulls = stock_hulls()
    capabilities: list[ResearchCapability] = []

    hull_specs = [
        (1, "logistics", "Upgrade an existing freighter design and expand population transport."),
        (2, "logistics", "Develop a Large Freighter design for high-volume colonization logistics."),
    ]
    # Super Freighter is Inner Strength PRT-only; never turn it into a generic
    # Construction research goal for another race.
    if prt_id == 4:
        hull_specs.append((3, "logistics", "Develop a Super Freighter design for mature Inner Strength logistics."))
    if improved_starbases:
        hull_specs.extend([
            (33, "expansion", "Develop and queue a Space Dock support-base upgrade."),
            (35, "mature", "Develop and queue an Ultra Station support-base upgrade."),
        ])

    for hull_id, category, action in hull_specs:
        hull = hulls[hull_id]
        capabilities.append(ResearchCapability(
            capability_id=f"hull:{hull_id}",
            name=hull.name,
            category=category,
            requirements=_requirements(hull.requirements),
            post_unlock_action=action,
            source="bundled data_hulls.mod",
            # New design creation remains partially blocked, so the planner will
            # discount (but not invent) this otherwise authoritative capability.
            executable=False,
            tags=("hull", "expansion_enabler") if category in ("logistics", "expansion") else ("hull",),
        ))

    if include_ife:
        capabilities.append(ResearchCapability(
            capability_id="component:fuel_mizer",
            name="Fuel Mizer engine",
            category="expansion",
            requirements={"propulsion": 2},
            post_unlock_action="Upgrade scout and freighter designs to the Fuel Mizer engine.",
            source="bundled IFE doctrine and validated stock requirement",
            executable=False,
            tags=("engine", "expansion_enabler"),
        ))

    if total_terraforming:
        for bio, amount in TOTAL_TERRAFORMING_LEVELS:
            if bio <= 0:
                continue
            capabilities.append(ResearchCapability(
                capability_id=f"terraform:tt:{amount}",
                name=f"Total Terraforming {amount}%",
                category="terraforming",
                requirements={"biotechnology": int(bio)},
                post_unlock_action=f"Queue Max Terraform where the {amount}% limit improves habitability.",
                source="terraforming.TOTAL_TERRAFORMING_LEVELS",
                tags=("terraforming", "expansion_enabler"),
            ))
    else:
        axes = (
            ("gravity", "propulsion"),
            ("temperature", "energy"),
            ("radiation", "weapons"),
        )
        for axis, related_field in axes:
            for related, bio, amount in STANDARD_LEVELS:
                capabilities.append(ResearchCapability(
                    capability_id=f"terraform:{axis}:{amount}",
                    name=f"{axis.title()} Terraforming {amount}%",
                    category="terraforming",
                    requirements={related_field: int(related), "biotechnology": int(bio)},
                    post_unlock_action=f"Queue Max Terraform on {axis}-limited worlds improved by the {amount}% limit.",
                    source="terraforming.STANDARD_LEVELS",
                    tags=("terraforming", "expansion_enabler"),
                ))

    return capabilities
