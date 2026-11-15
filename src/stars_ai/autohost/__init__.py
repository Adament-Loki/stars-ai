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
    DEFAULT_HUMAN_TURN_TIMEOUT_SECONDS,
    HistorySyncError,
    HumanTurnTimeoutError,
    LiveGameValidationError,
    SeedValidationError,
    TurnExecution,
    ValidatedLiveGame,
    ValidatedSeedGame,
    WindowsAutoHostConfig,
    normalize_human_turn_timeout,
)

__all__ = [
    "DEFAULT_HUMAN_TURN_TIMEOUT_SECONDS",
    "ExternalCommandOrderBridge",
    "HistorySyncError",
    "HostedTurnLifecycle",
    "HumanTurnTimeoutError",
    "IntegratedNativeOrderBridge",
    "LiveGameValidationError",
    "NativeOrderBridge",
    "NoopOrderBridge",
    "load_windows_autohost_config",
    "normalize_human_turn_timeout",
    "SeedValidationError",
    "TurnExecution",
    "TurnArchiver",
    "TurnLifecyclePhase",
    "ValidatedLiveGame",
    "ValidatedSeedGame",
    "WindowsAutoHostConfig",
]
