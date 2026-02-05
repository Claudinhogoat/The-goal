"""Trade execution for arbitrage opportunities."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..clients.betfair import BetfairClient
from ..clients.betfair import OrderResult as BetfairOrderResult
from ..clients.polymarket import PolymarketClient
from ..clients.polymarket import OrderResult as PolyOrderResult
from ..utils.helpers import generate_id
from .detector import ArbDetector, ArbOpportunity
from .mapper import MarketMapper
from .risk import RiskAction, RiskManager

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    """Unified order result across platforms."""

    order_id: str
    status: str  # 'filled', 'partial', 'cancelled', 'failed', 'timeout'
    requested_size: float
    filled_size: float
    avg_price: float
    fees: float
    error: str | None = None

    @classmethod
    def from_poly(cls, result: PolyOrderResult) -> "OrderResult":
        """Create from Polymarket result."""
        return cls(
            order_id=result.order_id,
            status=result.status,
            requested_size=result.requested_size,
            filled_size=result.filled_size,
            avg_price=result.avg_price,
            fees=result.fees,
            error=result.error,
        )

    @classmethod
    def from_betfair(cls, result: BetfairOrderResult) -> "OrderResult":
        """Create from Betfair result."""
        return cls(
            order_id=result.order_id,
            status=result.status,
            requested_size=result.requested_size,
            filled_size=result.filled_size,
            avg_price=result.avg_price,
            fees=result.fees,
            error=result.error,
        )


@dataclass
class ExecutionResult:
    """Result of executing an arbitrage opportunity."""

    execution_id: str
    status: str  # 'success', 'leg1_failed', 'leg2_failed', 'slippage_abort', 'timeout', 'blocked', 'dry_run'
    opportunity: ArbOpportunity
    requested_size_usd: float
    reason: str | None = None
    poly_result: OrderResult | None = None
    betfair_result: OrderResult | None = None
    naked_position: bool = False
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_success(self) -> bool:
        """Check if execution was successful."""
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging/storage."""
        return {
            "execution_id": self.execution_id,
            "status": self.status,
            "opportunity": self.opportunity.to_dict(),
            "requested_size_usd": self.requested_size_usd,
            "reason": self.reason,
            "poly_result": {
                "order_id": self.poly_result.order_id,
                "status": self.poly_result.status,
                "filled_size": self.poly_result.filled_size,
                "avg_price": self.poly_result.avg_price,
            } if self.poly_result else None,
            "betfair_result": {
                "order_id": self.betfair_result.order_id,
                "status": self.betfair_result.status,
                "filled_size": self.betfair_result.filled_size,
                "avg_price": self.betfair_result.avg_price,
            } if self.betfair_result else None,
            "naked_position": self.naked_position,
            "executed_at": self.executed_at.isoformat(),
        }


class Executor:
    """Executes arbitrage trades across platforms."""

    def __init__(
        self,
        poly_client: PolymarketClient,
        betfair_client: BetfairClient,
        risk_manager: RiskManager,
        mapper: MarketMapper,
        detector: ArbDetector,
        timeout: float = 5.0,
        max_slippage_bps: int = 50,
        dry_run: bool = True,
    ):
        """
        Initialize executor.

        Args:
            poly_client: Polymarket API client
            betfair_client: Betfair API client
            risk_manager: Risk manager instance
            mapper: Market mapper
            detector: Arbitrage detector for size calculations
            timeout: Order timeout in seconds
            max_slippage_bps: Maximum acceptable slippage in basis points
            dry_run: If True, log but don't execute trades
        """
        self.poly_client = poly_client
        self.betfair_client = betfair_client
        self.risk_manager = risk_manager
        self.mapper = mapper
        self.detector = detector
        self.timeout = timeout
        self.max_slippage_bps = max_slippage_bps
        self.dry_run = dry_run

        self._pending_executions: dict[str, ExecutionResult] = {}
        self._on_naked_position: list[callable] = []

    def on_naked_position(self, callback: callable) -> None:
        """Register callback for naked position events."""
        self._on_naked_position.append(callback)

    async def execute(
        self, opportunity: ArbOpportunity, size_usd: float
    ) -> ExecutionResult:
        """
        Execute an arbitrage opportunity.

        CRITICAL: Execute less liquid leg (Polymarket) FIRST.

        Flow:
        1. Risk check -> abort if blocked
        2. Execute Polymarket order (5s timeout)
        3. If failed -> abort, log
        4. Check slippage -> if > 50bps -> abort, creates naked position
        5. Execute Betfair order (size adjusted to Poly fill)
        6. If failed -> trigger naked position handler
        7. Success -> record trade
        """
        execution_id = generate_id()

        # Check if opportunity expired
        if opportunity.is_expired:
            return ExecutionResult(
                execution_id=execution_id,
                status="timeout",
                opportunity=opportunity,
                requested_size_usd=size_usd,
                reason="Opportunity expired before execution",
            )

        # Risk check
        risk_result = self.risk_manager.check_trade(opportunity, size_usd)
        if not risk_result.is_allowed:
            return ExecutionResult(
                execution_id=execution_id,
                status="blocked",
                opportunity=opportunity,
                requested_size_usd=size_usd,
                reason=risk_result.reason,
            )

        # Adjust size if needed
        size_usd = risk_result.adjusted_size

        # Get mapping for market info
        mapping = self.mapper.get_by_id(opportunity.mapping_id)
        if not mapping:
            return ExecutionResult(
                execution_id=execution_id,
                status="leg1_failed",
                opportunity=opportunity,
                requested_size_usd=size_usd,
                reason="Mapping not found",
            )

        # Calculate optimal sizes
        poly_shares, betfair_gbp = self.detector.calculate_optimal_sizes(
            opportunity, size_usd
        )

        # Dry run mode - log and return
        if self.dry_run:
            logger.info(
                f"[DRY RUN] Would execute {opportunity.direction}: "
                f"Poly: {poly_shares:.2f} shares @ {opportunity.poly_price:.4f}, "
                f"Betfair: £{betfair_gbp:.2f} @ {opportunity.betfair_price:.2f}, "
                f"Edge: {opportunity.net_edge_bps}bps"
            )
            return ExecutionResult(
                execution_id=execution_id,
                status="dry_run",
                opportunity=opportunity,
                requested_size_usd=size_usd,
                reason="Dry run mode - no orders placed",
            )

        # STEP 1: Execute Polymarket order (less liquid leg first)
        poly_result = await self._execute_poly_leg(
            mapping, opportunity, poly_shares
        )

        if poly_result.status in ("failed", "timeout"):
            logger.warning(
                f"Poly leg failed: {poly_result.error or poly_result.status}"
            )
            return ExecutionResult(
                execution_id=execution_id,
                status="leg1_failed",
                opportunity=opportunity,
                requested_size_usd=size_usd,
                reason=f"Polymarket order failed: {poly_result.error}",
                poly_result=poly_result,
            )

        # Check if anything filled
        if poly_result.filled_size == 0:
            logger.warning("Poly leg got zero fill")
            return ExecutionResult(
                execution_id=execution_id,
                status="leg1_failed",
                opportunity=opportunity,
                requested_size_usd=size_usd,
                reason="Polymarket order got zero fill",
                poly_result=poly_result,
            )

        # STEP 2: Check slippage on Poly fill
        slippage_bps = self._calculate_slippage_bps(
            opportunity.poly_price, poly_result.avg_price
        )

        if slippage_bps > self.max_slippage_bps:
            logger.warning(
                f"Slippage too high: {slippage_bps}bps > {self.max_slippage_bps}bps"
            )
            # We have a naked position now!
            await self._handle_naked_position(
                execution_id, mapping, opportunity, poly_result, "slippage_abort"
            )
            return ExecutionResult(
                execution_id=execution_id,
                status="slippage_abort",
                opportunity=opportunity,
                requested_size_usd=size_usd,
                reason=f"Slippage {slippage_bps}bps exceeds max {self.max_slippage_bps}bps",
                poly_result=poly_result,
                naked_position=True,
            )

        # STEP 3: Execute Betfair order (adjusted to Poly fill)
        # Recalculate Betfair size based on actual Poly fill
        actual_poly_value = poly_result.filled_size * poly_result.avg_price
        _, adjusted_betfair_gbp = self.detector.calculate_optimal_sizes(
            opportunity, actual_poly_value
        )

        betfair_result = await self._execute_betfair_leg(
            mapping, opportunity, adjusted_betfair_gbp
        )

        if betfair_result.status in ("failed", "timeout"):
            logger.warning(
                f"Betfair leg failed: {betfair_result.error or betfair_result.status}"
            )
            # We have a naked position now!
            await self._handle_naked_position(
                execution_id, mapping, opportunity, poly_result, "betfair_failed"
            )
            return ExecutionResult(
                execution_id=execution_id,
                status="leg2_failed",
                opportunity=opportunity,
                requested_size_usd=size_usd,
                reason=f"Betfair order failed: {betfair_result.error}",
                poly_result=poly_result,
                betfair_result=betfair_result,
                naked_position=True,
            )

        # STEP 4: Success! Record the trade
        poly_side = "buy" if "buy_poly" in opportunity.direction else "sell"
        betfair_side = "lay" if "lay_betfair" in opportunity.direction else "back"

        self.risk_manager.record_trade(
            mapping_id=mapping.id,
            platform="polymarket",
            side=poly_side,
            size=poly_result.filled_size * poly_result.avg_price,
            price=poly_result.avg_price,
        )

        self.risk_manager.record_trade(
            mapping_id=mapping.id,
            platform="betfair",
            side=betfair_side,
            size=betfair_result.filled_size,
            price=betfair_result.avg_price,
        )

        logger.info(
            f"Execution success: {opportunity.direction}, "
            f"Poly: {poly_result.filled_size:.2f} @ {poly_result.avg_price:.4f}, "
            f"Betfair: £{betfair_result.filled_size:.2f} @ {betfair_result.avg_price:.2f}, "
            f"Edge: {opportunity.net_edge_bps}bps"
        )

        return ExecutionResult(
            execution_id=execution_id,
            status="success",
            opportunity=opportunity,
            requested_size_usd=size_usd,
            poly_result=poly_result,
            betfair_result=betfair_result,
        )

    async def _execute_poly_leg(
        self,
        mapping,
        opportunity: ArbOpportunity,
        shares: float,
    ) -> OrderResult:
        """Execute the Polymarket leg of the trade."""
        if "buy_poly" in opportunity.direction:
            side = "buy"
            token_id = mapping.poly_yes_token
            price = opportunity.poly_price
        else:
            side = "sell"
            token_id = mapping.poly_yes_token
            price = opportunity.poly_price

        result = await self.poly_client.place_order(
            token_id=token_id,
            side=side,
            price=price,
            size=shares,
            timeout=self.timeout,
        )

        return OrderResult.from_poly(result)

    async def _execute_betfair_leg(
        self,
        mapping,
        opportunity: ArbOpportunity,
        size_gbp: float,
    ) -> OrderResult:
        """Execute the Betfair leg of the trade."""
        if "lay_betfair" in opportunity.direction:
            side = "LAY"
        else:
            side = "BACK"

        result = await self.betfair_client.place_order(
            market_id=mapping.betfair_market_id,
            selection_id=mapping.betfair_yes_selection_id,
            side=side,
            price=opportunity.betfair_price,
            size=size_gbp,
            timeout=self.timeout,
        )

        return OrderResult.from_betfair(result)

    def _calculate_slippage_bps(
        self, expected_price: float, actual_price: float
    ) -> int:
        """Calculate slippage in basis points."""
        if expected_price == 0:
            return 0
        slippage = abs(actual_price - expected_price) / expected_price
        return int(slippage * 10000)

    async def _handle_naked_position(
        self,
        execution_id: str,
        mapping,
        opportunity: ArbOpportunity,
        poly_result: OrderResult,
        reason: str,
    ) -> None:
        """Handle creation of a naked position."""
        from ..handlers.naked_position import NakedPosition

        position = NakedPosition(
            id=generate_id(),
            mapping_id=mapping.id,
            platform="polymarket",
            side="long" if "buy_poly" in opportunity.direction else "short",
            size=poly_result.filled_size,
            entry_price=poly_result.avg_price,
            created_at=datetime.now(timezone.utc),
        )

        logger.warning(
            f"Naked position created: {position.id}, "
            f"mapping={mapping.id}, side={position.side}, "
            f"size={position.size}, reason={reason}"
        )

        # Notify callbacks
        for callback in self._on_naked_position:
            try:
                callback(position, reason)
            except Exception as e:
                logger.error(f"Naked position callback error: {e}")
