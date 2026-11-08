"""Compatibility shim for :mod:`stars_ai.core.persona`."""

from .core.persona import (
    BalancedPersona,
    ExpansionistPersona,
    IndustrialistPersona,
    MilitaristPersona,
    Objective,
    StrategicPersona,
    StrategicPlan,
    TechnologistPersona,
    persona_from_name,
)

__all__ = [
    "BalancedPersona",
    "ExpansionistPersona",
    "IndustrialistPersona",
    "MilitaristPersona",
    "Objective",
    "StrategicPersona",
    "StrategicPlan",
    "TechnologistPersona",
    "persona_from_name",
]
