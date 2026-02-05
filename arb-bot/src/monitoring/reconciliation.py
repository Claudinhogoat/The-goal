"""Position reconciliation between local tracking and exchange APIs."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..clients.betfair import BetfairClient
from ..clients.polymarket import PolymarketClient
from .alerts import Alerter, AlertLevel

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationResult:
    """Result of a reconciliation check."""

    platform: str
    market_id: str
    local_position: float
    exchange_position: float
    difference: float
    is_matched: bool
    timestamp: datetime

    @property
    def difference_pct(self) -> float:
        """Get difference as percentage of local position."""
        if self.local_position == 0:
            return 0 if self.exchange_position == 0 else 100
        return abs(self.difference / self.local_position) * 100


class Reconciler:
    """Reconciles positions between local tracking and exchanges."""

    def __init__(
        self,
        poly_client: PolymarketClient,
        betfair_client: BetfairClient,
        alerter: Alerter,
        tolerance_pct: float = 1.0,  # Allow 1% difference
    ):
        """
        Initialize reconciler.

        Args:
            poly_client: Polymarket client
            betfair_client: Betfair client
            alerter: Alerter for notifications
            tolerance_pct: Acceptable difference percentage
        """
        self.poly_client = poly_client
        self.betfair_client = betfair_client
        self.alerter = alerter
        self.tolerance_pct = tolerance_pct

        self._last_reconciliation: datetime | None = None
        self._discrepancies: list[ReconciliationResult] = []

    async def reconcile_all(
        self, local_positions: dict[str, dict[str, float]]
    ) -> list[ReconciliationResult]:
        """
        Reconcile all positions.

        Args:
            local_positions: Dict of {mapping_id: {platform: size}}

        Returns:
            List of reconciliation results
        """
        results = []

        # Get exchange positions
        poly_positions = await self._get_poly_positions()
        betfair_positions = await self._get_betfair_positions()

        for mapping_id, platforms in local_positions.items():
            # Check Polymarket
            if "polymarket" in platforms:
                local_size = platforms["polymarket"]
                exchange_size = poly_positions.get(mapping_id, 0)

                result = self._compare_positions(
                    platform="polymarket",
                    market_id=mapping_id,
                    local=local_size,
                    exchange=exchange_size,
                )
                results.append(result)

                if not result.is_matched:
                    self._handle_discrepancy(result)

            # Check Betfair
            if "betfair" in platforms:
                local_size = platforms["betfair"]
                exchange_size = betfair_positions.get(mapping_id, 0)

                result = self._compare_positions(
                    platform="betfair",
                    market_id=mapping_id,
                    local=local_size,
                    exchange=exchange_size,
                )
                results.append(result)

                if not result.is_matched:
                    self._handle_discrepancy(result)

        self._last_reconciliation = datetime.now(timezone.utc)
        return results

    async def _get_poly_positions(self) -> dict[str, float]:
        """Get positions from Polymarket."""
        try:
            positions = await self.poly_client.get_positions()
            # Convert to {token_id: size} format
            result = {}
            for pos in positions:
                token_id = pos.get("tokenId", "")
                size = float(pos.get("size", 0))
                if token_id and size != 0:
                    result[token_id] = size
            return result
        except Exception as e:
            logger.error(f"Failed to get Polymarket positions: {e}")
            return {}

    async def _get_betfair_positions(self) -> dict[str, float]:
        """Get positions from Betfair."""
        try:
            orders = await self.betfair_client.get_positions()
            # Aggregate by market
            result: dict[str, float] = {}
            for order in orders:
                market_id = order.get("marketId", "")
                size = float(order.get("sizeMatched", 0))
                side = order.get("side", "")

                if not market_id:
                    continue

                # Back = positive, Lay = negative
                if side == "LAY":
                    size = -size

                result[market_id] = result.get(market_id, 0) + size

            return result
        except Exception as e:
            logger.error(f"Failed to get Betfair positions: {e}")
            return {}

    def _compare_positions(
        self,
        platform: str,
        market_id: str,
        local: float,
        exchange: float,
    ) -> ReconciliationResult:
        """Compare local and exchange positions."""
        difference = exchange - local

        # Check if within tolerance
        if local == 0 and exchange == 0:
            is_matched = True
        elif local == 0:
            is_matched = abs(exchange) < 0.01  # Small exchange-only position OK
        else:
            diff_pct = abs(difference / local) * 100
            is_matched = diff_pct <= self.tolerance_pct

        return ReconciliationResult(
            platform=platform,
            market_id=market_id,
            local_position=local,
            exchange_position=exchange,
            difference=difference,
            is_matched=is_matched,
            timestamp=datetime.now(timezone.utc),
        )

    def _handle_discrepancy(self, result: ReconciliationResult) -> None:
        """Handle a position discrepancy."""
        self._discrepancies.append(result)

        # Alert based on severity
        if result.difference_pct > 10:
            level = AlertLevel.CRITICAL
        elif result.difference_pct > 5:
            level = AlertLevel.WARNING
        else:
            level = AlertLevel.INFO

        self.alerter.send(
            level,
            f"Position discrepancy on {result.platform}",
            {
                "market_id": result.market_id,
                "local": result.local_position,
                "exchange": result.exchange_position,
                "difference": result.difference,
                "difference_pct": f"{result.difference_pct:.1f}%",
            },
        )

    def get_discrepancies(self) -> list[ReconciliationResult]:
        """Get list of recent discrepancies."""
        return self._discrepancies.copy()

    def clear_discrepancies(self) -> None:
        """Clear discrepancy history."""
        self._discrepancies.clear()
