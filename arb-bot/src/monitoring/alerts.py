"""Alerting system for notifications."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """Alert severity levels."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class Alert:
    """Alert message."""

    level: AlertLevel
    message: str
    data: dict[str, Any] | None = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)

    def format_message(self) -> str:
        """Format alert for sending."""
        prefix = {
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.CRITICAL: "🚨",
        }[self.level]

        msg = f"{prefix} [{self.level.value}] {self.message}"

        if self.data:
            details = "\n".join(f"  {k}: {v}" for k, v in self.data.items())
            msg += f"\n{details}"

        return msg


class Alerter:
    """Manages alert sending across channels."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize alerter.

        Config structure:
        {
            "telegram": {
                "enabled": true,
                "bot_token": "...",
                "chat_id": "..."
            }
        }
        """
        self.config = config
        self._telegram_enabled = config.get("telegram", {}).get("enabled", False)
        self._telegram_token = config.get("telegram", {}).get("bot_token", "")
        self._telegram_chat_id = config.get("telegram", {}).get("chat_id", "")

        self._alert_history: list[Alert] = []
        self._max_history = 1000

    def send(
        self,
        level: str | AlertLevel,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """
        Send an alert.

        Args:
            level: Alert level (INFO, WARNING, CRITICAL)
            message: Alert message
            data: Additional data to include
        """
        if isinstance(level, str):
            level = AlertLevel[level.upper()]

        alert = Alert(level=level, message=message, data=data)
        self._alert_history.append(alert)

        # Trim history if needed
        if len(self._alert_history) > self._max_history:
            self._alert_history = self._alert_history[-self._max_history:]

        # Log the alert
        log_method = {
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.CRITICAL: logger.critical,
        }[level]
        log_method(f"ALERT: {message}")

        # Send based on level
        if level == AlertLevel.INFO:
            # INFO: Log only
            pass
        elif level == AlertLevel.WARNING:
            # WARNING: Telegram
            if self._telegram_enabled:
                asyncio.create_task(self._send_telegram(alert.format_message()))
        elif level == AlertLevel.CRITICAL:
            # CRITICAL: Telegram + all channels
            if self._telegram_enabled:
                asyncio.create_task(self._send_telegram(alert.format_message()))

    async def send_async(
        self,
        level: str | AlertLevel,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Send an alert asynchronously."""
        if isinstance(level, str):
            level = AlertLevel[level.upper()]

        alert = Alert(level=level, message=message, data=data)
        self._alert_history.append(alert)

        # Log the alert
        log_method = {
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.CRITICAL: logger.critical,
        }[level]
        log_method(f"ALERT: {message}")

        # Send based on level
        if level in (AlertLevel.WARNING, AlertLevel.CRITICAL):
            if self._telegram_enabled:
                await self._send_telegram(alert.format_message())

    async def _send_telegram(self, message: str) -> bool:
        """Send message via Telegram bot."""
        if not self._telegram_token or not self._telegram_chat_id:
            logger.warning("Telegram not configured")
            return False

        try:
            import aiohttp

            url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
            data = {
                "chat_id": self._telegram_chat_id,
                "text": message,
                "parse_mode": "HTML",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data) as resp:
                    if resp.status == 200:
                        logger.debug("Telegram alert sent successfully")
                        return True
                    else:
                        error = await resp.text()
                        logger.error(f"Telegram send failed: {error}")
                        return False

        except ImportError:
            logger.error("aiohttp not installed for Telegram alerts")
            return False
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    def get_recent_alerts(
        self, count: int = 10, level: AlertLevel | None = None
    ) -> list[Alert]:
        """Get recent alerts, optionally filtered by level."""
        alerts = self._alert_history
        if level:
            alerts = [a for a in alerts if a.level == level]
        return alerts[-count:]
