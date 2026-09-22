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
from .lifecycle import HostedTurnLifecycle, TurnArchiver, TurnLifecyclePhase
from .configuration import load_windows_autohost_config
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
    "HostedTurnLifecycle",
    "IntegratedNativeOrderBridge",
    "LiveGameValidationError",
    "NativeOrderBridge",
    "NoopOrderBridge",
    "load_windows_autohost_config",
    "SeedValidationError",
    "TurnExecution",
    "TurnArchiver",
    "TurnLifecyclePhase",
    "ValidatedLiveGame",
    "ValidatedSeedGame",
    "WindowsAutoHostConfig",
]
