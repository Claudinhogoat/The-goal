"""Risk management for arbitrage trading."""

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from .detector import ArbOpportunity

logger = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    """Risk limit configuration."""

    max_single_trade_usd: float = 2000
    max_position_per_market_usd: float = 10000
    max_total_exposure_usd: float = 50000
    max_daily_loss_usd: float = 1000
    max_drawdown_pct: float = 0.10  # 10%
    max_trades_per_minute: int = 10
    max_trades_per_hour: int = 100
    pause_on_consecutive_losses: int = 3
    pause_duration_minutes: int = 30

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RiskLimits":
        """Create from dictionary."""
        return cls(
            max_single_trade_usd=data.get("max_single_trade_usd", 2000),
            max_position_per_market_usd=data.get("max_position_per_market_usd", 10000),
            max_total_exposure_usd=data.get("max_total_exposure_usd", 50000),
            max_daily_loss_usd=data.get("max_daily_loss_usd", 1000),
            max_drawdown_pct=data.get("max_drawdown_pct", 0.10),
            max_trades_per_minute=data.get("max_trades_per_minute", 10),
            max_trades_per_hour=data.get("max_trades_per_hour", 100),
            pause_on_consecutive_losses=data.get("pause_on_consecutive_losses", 3),
            pause_duration_minutes=data.get("pause_duration_minutes", 30),
        )


class RiskAction(Enum):
    """Risk check result actions."""

    ALLOW = "allow"
    BLOCK = "block"
    REDUCE_SIZE = "reduce_size"


@dataclass
class TradeRecord:
    """Record of a trade for risk tracking."""

    mapping_id: str
    platform: str  # 'polymarket' or 'betfair'
    side: str  # 'buy', 'sell', 'back', 'lay'
    size_usd: float
    price: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RiskCheckResult:
    """Result of a risk check."""

    action: RiskAction
    reason: str
    adjusted_size: float  # Original or reduced size

    @property
    def is_allowed(self) -> bool:
        """Check if trade is allowed."""
        return self.action != RiskAction.BLOCK


class RiskManager:
    """Manages risk limits and position tracking."""

    def __init__(self, limits: RiskLimits, initial_equity: float):
        """
        Initialize risk manager.

        Args:
            limits: Risk limit configuration
            initial_equity: Starting equity in USD
        """
        self.limits = limits
        self.initial_equity = initial_equity
        self.current_equity = initial_equity
        self.peak_equity = initial_equity

        # Position tracking
        self._positions: dict[str, dict[str, float]] = {}  # mapping_id -> {platform: size}
        self._total_exposure: float = 0.0

        # Trade history for rate limiting
        self._recent_trades: deque[datetime] = deque(maxlen=1000)

        # P&L tracking
        self._daily_pnl: float = 0.0
        self._daily_reset_date: datetime = datetime.now(timezone.utc).date()
        self._consecutive_losses: int = 0

        # State
        self._emergency_stopped: bool = False
        self._emergency_reason: str = ""
        self._paused_until: datetime | None = None

    def check_trade(
        self, opportunity: ArbOpportunity, size_usd: float
    ) -> RiskCheckResult:
        """
        Check if a trade should be allowed.

        Returns RiskCheckResult with action, reason, and potentially adjusted size.
        """
        # Reset daily stats if needed
        self._check_daily_reset()

        # Emergency stop check
        if self._emergency_stopped:
            return RiskCheckResult(
                action=RiskAction.BLOCK,
                reason=f"Emergency stopped: {self._emergency_reason}",
                adjusted_size=0,
            )

        # Pause check
        if self._paused_until:
            if datetime.now(timezone.utc) < self._paused_until:
                return RiskCheckResult(
                    action=RiskAction.BLOCK,
                    reason=f"Trading paused until {self._paused_until}",
                    adjusted_size=0,
                )
            else:
                self._paused_until = None

        # Single trade size limit
        if size_usd > self.limits.max_single_trade_usd:
            size_usd = self.limits.max_single_trade_usd
            logger.info(f"Reduced trade size to max single trade: ${size_usd}")

        # Market position limit
        current_market_exposure = self.get_market_exposure(opportunity.mapping_id)
        remaining_market_capacity = (
            self.limits.max_position_per_market_usd - current_market_exposure
        )

        if remaining_market_capacity <= 0:
            return RiskCheckResult(
                action=RiskAction.BLOCK,
                reason=f"Market position limit reached: ${current_market_exposure:.2f}",
                adjusted_size=0,
            )

        if size_usd > remaining_market_capacity:
            size_usd = remaining_market_capacity
            logger.info(f"Reduced trade size for market limit: ${size_usd}")

        # Total exposure limit
        remaining_total_capacity = (
            self.limits.max_total_exposure_usd - self._total_exposure
        )

        if remaining_total_capacity <= 0:
            return RiskCheckResult(
                action=RiskAction.BLOCK,
                reason=f"Total exposure limit reached: ${self._total_exposure:.2f}",
                adjusted_size=0,
            )

        if size_usd > remaining_total_capacity:
            size_usd = remaining_total_capacity
            logger.info(f"Reduced trade size for total exposure limit: ${size_usd}")

        # Daily loss limit
        if self._daily_pnl < -self.limits.max_daily_loss_usd:
            return RiskCheckResult(
                action=RiskAction.BLOCK,
                reason=f"Daily loss limit reached: ${self._daily_pnl:.2f}",
                adjusted_size=0,
            )

        # Drawdown limit
        drawdown = (self.peak_equity - self.current_equity) / self.peak_equity
        if drawdown > self.limits.max_drawdown_pct:
            return RiskCheckResult(
                action=RiskAction.BLOCK,
                reason=f"Drawdown limit reached: {drawdown:.1%}",
                adjusted_size=0,
            )

        # Rate limits
        now = datetime.now(timezone.utc)
        minute_ago = now - timedelta(minutes=1)
        hour_ago = now - timedelta(hours=1)

        trades_last_minute = sum(1 for t in self._recent_trades if t > minute_ago)
        trades_last_hour = sum(1 for t in self._recent_trades if t > hour_ago)

        if trades_last_minute >= self.limits.max_trades_per_minute:
            return RiskCheckResult(
                action=RiskAction.BLOCK,
                reason=f"Rate limit: {trades_last_minute} trades in last minute",
                adjusted_size=0,
            )

        if trades_last_hour >= self.limits.max_trades_per_hour:
            return RiskCheckResult(
                action=RiskAction.BLOCK,
                reason=f"Rate limit: {trades_last_hour} trades in last hour",
                adjusted_size=0,
            )

        # All checks passed
        if size_usd < opportunity.max_size_usd:
            return RiskCheckResult(
                action=RiskAction.REDUCE_SIZE,
                reason="Size reduced due to risk limits",
                adjusted_size=size_usd,
            )

        return RiskCheckResult(
            action=RiskAction.ALLOW,
            reason="All risk checks passed",
            adjusted_size=size_usd,
        )

    def record_trade(
        self,
        mapping_id: str,
        platform: str,
        side: str,
        size: float,
        price: float,
    ) -> None:
        """Record a trade for risk tracking."""
        # Update position tracking
        if mapping_id not in self._positions:
            self._positions[mapping_id] = {}

        current = self._positions[mapping_id].get(platform, 0)

        # Buy/back increases position, sell/lay decreases
        if side in ("buy", "back"):
            self._positions[mapping_id][platform] = current + size
            self._total_exposure += size
        else:
            self._positions[mapping_id][platform] = current - size
            self._total_exposure -= size

        # Record timestamp for rate limiting
        self._recent_trades.append(datetime.now(timezone.utc))

        logger.debug(
            f"Recorded trade: {mapping_id} {platform} {side} ${size:.2f} @ {price}"
        )

    def record_pnl(self, amount: float) -> None:
        """Record realized P&L."""
        self._check_daily_reset()

        self._daily_pnl += amount
        self.current_equity += amount

        # Update peak equity
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity

        # Track consecutive losses
        if amount < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.limits.pause_on_consecutive_losses:
                self._pause_trading()
        else:
            self._consecutive_losses = 0

        logger.info(
            f"P&L: ${amount:.2f}, Daily: ${self._daily_pnl:.2f}, "
            f"Equity: ${self.current_equity:.2f}"
        )

    def emergency_stop(self, reason: str) -> None:
        """Trigger emergency stop."""
        self._emergency_stopped = True
        self._emergency_reason = reason
        logger.critical(f"EMERGENCY STOP: {reason}")

    def resume_trading(self) -> None:
        """Resume trading after emergency stop or pause."""
        self._emergency_stopped = False
        self._emergency_reason = ""
        self._paused_until = None
        logger.info("Trading resumed")

    def get_total_exposure(self) -> float:
        """Get total exposure across all markets."""
        return max(0, self._total_exposure)

    def get_market_exposure(self, mapping_id: str) -> float:
        """Get exposure for a specific market."""
        if mapping_id not in self._positions:
            return 0.0
        return sum(abs(v) for v in self._positions[mapping_id].values())

    def get_position(self, mapping_id: str, platform: str) -> float:
        """Get position size for a market/platform combination."""
        if mapping_id not in self._positions:
            return 0.0
        return self._positions[mapping_id].get(platform, 0.0)

    def get_daily_pnl(self) -> float:
        """Get daily P&L."""
        self._check_daily_reset()
        return self._daily_pnl

    def get_drawdown(self) -> float:
        """Get current drawdown as a percentage."""
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity

    def get_status(self) -> dict[str, Any]:
        """Get risk manager status summary."""
        return {
            "emergency_stopped": self._emergency_stopped,
            "emergency_reason": self._emergency_reason,
            "paused_until": self._paused_until.isoformat() if self._paused_until else None,
            "current_equity": self.current_equity,
            "peak_equity": self.peak_equity,
            "drawdown_pct": self.get_drawdown() * 100,
            "daily_pnl": self._daily_pnl,
            "total_exposure": self._total_exposure,
            "consecutive_losses": self._consecutive_losses,
            "positions": {k: dict(v) for k, v in self._positions.items()},
        }

    def _check_daily_reset(self) -> None:
        """Reset daily stats if it's a new day."""
        today = datetime.now(timezone.utc).date()
        if today != self._daily_reset_date:
            logger.info(f"New trading day: resetting daily stats")
            self._daily_pnl = 0.0
            self._daily_reset_date = today

    def _pause_trading(self) -> None:
        """Pause trading for configured duration."""
        self._paused_until = datetime.now(timezone.utc) + timedelta(
            minutes=self.limits.pause_duration_minutes
        )
        logger.warning(
            f"Trading paused until {self._paused_until} "
            f"after {self._consecutive_losses} consecutive losses"
        )
