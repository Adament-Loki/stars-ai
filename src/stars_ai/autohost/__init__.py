"""Composable pieces of the native Stars! autoplay controller.

`windows_autohost` remains the compatibility entry point and owns the turn
lifecycle.  This package contains the small, independently understandable
contracts that lifecycle coordinates.
"""

from .bridges import (
    ExternalCommandOrderBridge,
    IntegratedNativeOrderBridge,
    NativeOrderBridge,
    NoopOrderBridge,
)
from .models import (
    HistorySyncError,
    LiveGameValidationError,
    SeedValidationError,
    TurnExecution,
    ValidatedLiveGame,
    ValidatedSeedGame,
    WindowsAutoHostConfig,
)

__all__ = [
    "ExternalCommandOrderBridge",
    "HistorySyncError",
    "IntegratedNativeOrderBridge",
    "LiveGameValidationError",
    "NativeOrderBridge",
    "NoopOrderBridge",
    "SeedValidationError",
    "TurnExecution",
    "ValidatedLiveGame",
    "ValidatedSeedGame",
    "WindowsAutoHostConfig",
]
