"""Health monitoring for the arbitrage bot."""

import asyncio
import logging
import os
import psutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .alerts import Alerter, AlertLevel

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Health check result."""

    component: str
    healthy: bool
    message: str
    last_check: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: dict[str, Any] = field(default_factory=dict)


class HealthMonitor:
    """Monitors health of bot components."""

    def __init__(
        self,
        alerter: Alerter,
        check_interval_seconds: int = 30,
        stale_threshold_seconds: int = 30,
    ):
        """
        Initialize health monitor.

        Args:
            alerter: Alerter for sending alerts
            check_interval_seconds: How often to run health checks
            stale_threshold_seconds: How long before data is considered stale
        """
        self.alerter = alerter
        self.check_interval = check_interval_seconds
        self.stale_threshold = timedelta(seconds=stale_threshold_seconds)

        self._poly_last_message: datetime | None = None
        self._betfair_last_message: datetime | None = None
        self._db_healthy: bool = True
        self._running: bool = False
        self._health_statuses: dict[str, HealthStatus] = {}

        # Track consecutive failures for alerting
        self._consecutive_failures: dict[str, int] = {}
        self._alert_threshold = 3  # Alert after 3 consecutive failures

    def on_poly_message(self) -> None:
        """Call when Polymarket message received."""
        self._poly_last_message = datetime.now(timezone.utc)

    def on_betfair_message(self) -> None:
        """Call when Betfair message received."""
        self._betfair_last_message = datetime.now(timezone.utc)

    def on_db_operation(self, success: bool) -> None:
        """Call after database operation."""
        self._db_healthy = success

    async def run_checks(self) -> dict[str, HealthStatus]:
        """Run all health checks and return results."""
        now = datetime.now(timezone.utc)
        results = {}

        # Check Polymarket WebSocket
        poly_status = self._check_polymarket(now)
        results["polymarket_ws"] = poly_status
        self._handle_status_change("polymarket_ws", poly_status)

        # Check Betfair Stream
        betfair_status = self._check_betfair(now)
        results["betfair_stream"] = betfair_status
        self._handle_status_change("betfair_stream", betfair_status)

        # Check Database
        db_status = self._check_database()
        results["database"] = db_status
        self._handle_status_change("database", db_status)

        # Check Memory
        memory_status = self._check_memory()
        results["memory"] = memory_status
        self._handle_status_change("memory", memory_status)

        # Check CPU
        cpu_status = self._check_cpu()
        results["cpu"] = cpu_status

        self._health_statuses = results
        return results

    def _check_polymarket(self, now: datetime) -> HealthStatus:
        """Check Polymarket WebSocket health."""
        if self._poly_last_message is None:
            return HealthStatus(
                component="polymarket_ws",
                healthy=False,
                message="No messages received yet",
            )

        age = now - self._poly_last_message
        if age > self.stale_threshold:
            return HealthStatus(
                component="polymarket_ws",
                healthy=False,
                message=f"Last message {age.total_seconds():.0f}s ago (stale)",
                details={"last_message_age_seconds": age.total_seconds()},
            )

        return HealthStatus(
            component="polymarket_ws",
            healthy=True,
            message=f"Last message {age.total_seconds():.1f}s ago",
            details={"last_message_age_seconds": age.total_seconds()},
        )

    def _check_betfair(self, now: datetime) -> HealthStatus:
        """Check Betfair Stream health."""
        if self._betfair_last_message is None:
            return HealthStatus(
                component="betfair_stream",
                healthy=False,
                message="No messages received yet",
            )

        age = now - self._betfair_last_message
        if age > self.stale_threshold:
            return HealthStatus(
                component="betfair_stream",
                healthy=False,
                message=f"Last message {age.total_seconds():.0f}s ago (stale)",
                details={"last_message_age_seconds": age.total_seconds()},
            )

        return HealthStatus(
            component="betfair_stream",
            healthy=True,
            message=f"Last message {age.total_seconds():.1f}s ago",
            details={"last_message_age_seconds": age.total_seconds()},
        )

    def _check_database(self) -> HealthStatus:
        """Check database health."""
        return HealthStatus(
            component="database",
            healthy=self._db_healthy,
            message="OK" if self._db_healthy else "Database error detected",
        )

    def _check_memory(self) -> HealthStatus:
        """Check memory usage."""
        try:
            process = psutil.Process(os.getpid())
            memory_mb = process.memory_info().rss / 1024 / 1024
            memory_pct = psutil.virtual_memory().percent

            # Alert if using > 80% system memory or > 1GB process memory
            healthy = memory_pct < 80 and memory_mb < 1024

            return HealthStatus(
                component="memory",
                healthy=healthy,
                message=f"Process: {memory_mb:.0f}MB, System: {memory_pct:.1f}%",
                details={
                    "process_memory_mb": memory_mb,
                    "system_memory_pct": memory_pct,
                },
            )
        except Exception as e:
            return HealthStatus(
                component="memory",
                healthy=True,  # Assume healthy if we can't check
                message=f"Unable to check: {e}",
            )

    def _check_cpu(self) -> HealthStatus:
        """Check CPU usage."""
        try:
            cpu_pct = psutil.cpu_percent(interval=0.1)
            healthy = cpu_pct < 90

            return HealthStatus(
                component="cpu",
                healthy=healthy,
                message=f"CPU: {cpu_pct:.1f}%",
                details={"cpu_percent": cpu_pct},
            )
        except Exception as e:
            return HealthStatus(
                component="cpu",
                healthy=True,
                message=f"Unable to check: {e}",
            )

    def _handle_status_change(self, component: str, status: HealthStatus) -> None:
        """Handle status change and send alerts if needed."""
        if status.healthy:
            # Reset consecutive failures
            if component in self._consecutive_failures:
                del self._consecutive_failures[component]
            return

        # Increment failure count
        self._consecutive_failures[component] = (
            self._consecutive_failures.get(component, 0) + 1
        )

        failures = self._consecutive_failures[component]

        if failures == self._alert_threshold:
            # First alert
            self.alerter.send(
                AlertLevel.WARNING,
                f"Health check failing: {component}",
                {"message": status.message, "consecutive_failures": failures},
            )
        elif failures == self._alert_threshold * 2:
            # Escalate to critical
            self.alerter.send(
                AlertLevel.CRITICAL,
                f"Health check critical: {component}",
                {"message": status.message, "consecutive_failures": failures},
            )

    async def start(self) -> None:
        """Start the health monitoring loop."""
        self._running = True
        logger.info(f"Health monitor started (interval: {self.check_interval}s)")

        while self._running:
            try:
                await self.run_checks()
            except Exception as e:
                logger.error(f"Health check error: {e}")

            await asyncio.sleep(self.check_interval)

    async def stop(self) -> None:
        """Stop the health monitoring loop."""
        self._running = False
        logger.info("Health monitor stopped")

    def get_status_summary(self) -> dict[str, Any]:
        """Get summary of all health statuses."""
        all_healthy = all(s.healthy for s in self._health_statuses.values())

        return {
            "overall_healthy": all_healthy,
            "components": {
                name: {
                    "healthy": status.healthy,
                    "message": status.message,
                    "last_check": status.last_check.isoformat(),
                }
                for name, status in self._health_statuses.items()
            },
        }

    def is_healthy(self) -> bool:
        """Check if all components are healthy."""
        if not self._health_statuses:
            return True  # Haven't checked yet
        return all(s.healthy for s in self._health_statuses.values())
