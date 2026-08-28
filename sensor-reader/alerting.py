# alerting.py
"""Decides whether an alert is published.

Holds the cooldown the README has always promised: once a level has been
announced, the same level stays quiet until the cooldown elapses. An escalation
(warn -> err) is always announced, and a return to ok rearms the gate.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from common.air_quality import Level
from common.alerts import Alert

log = logging.getLogger(__name__)


class AlertGate:
    def __init__(
        self,
        publish: Callable[[Alert], None],
        *,
        cooldown_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._publish = publish
        self._cooldown = max(0.0, cooldown_seconds)
        self._clock = clock
        self._last_level: Optional[Level] = None
        self._last_sent_at: Optional[float] = None

    def submit(self, alert: Alert) -> bool:
        """Publish the alert unless it is suppressed. Returns whether it was sent."""
        if not alert.level.is_alerting:
            self._reset()
            return False

        if self._suppressed(alert.level):
            log.debug("Alert %s suppressed by cooldown (%.0fs)", alert.level.value, self._cooldown)
            return False

        self._publish(alert)
        self._last_level = alert.level
        self._last_sent_at = self._clock()
        return True

    def _suppressed(self, level: Level) -> bool:
        if self._last_level is None or self._last_sent_at is None:
            return False
        if level is Level.ERR and self._last_level is Level.WARN:
            return False  # escalation always gets through
        return (self._clock() - self._last_sent_at) < self._cooldown

    def _reset(self) -> None:
        self._last_level = None
        self._last_sent_at = None
