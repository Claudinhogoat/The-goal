"""Handler for naked (unhedged) positions."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..clients.betfair import BetfairClient
from ..clients.polymarket import PolymarketClient
from ..core.mapper import MarketMapper
from ..monitoring.alerts import Alerter, AlertLevel
from ..persistence.database import Database
from ..persistence.models import NakedPositionRecord
from ..utils.helpers import generate_id

logger = logging.getLogger(__name__)


@dataclass
class NakedPosition:
    """A naked (unhedged) position that needs resolution."""

    id: str
    mapping_id: str
    platform: str  # 'polymarket' or 'betfair'
    side: str  # 'long' or 'short' for poly, 'back' or 'lay' for betfair
    size: float
    entry_price: float
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    retry_count: int = 0
    max_retries: int = 5
    last_retry: datetime | None = None
    status: str = "pending"  # 'pending', 'retrying', 'hedged', 'closed', 'failed'

    @property
    def age_seconds(self) -> float:
        """Get age of position in seconds."""
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()

    @property
    def can_retry(self) -> bool:
        """Check if position can be retried."""
        return self.retry_count < self.max_retries and self.status in ("pending", "retrying")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "mapping_id": self.mapping_id,
            "platform": self.platform,
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "created_at": self.created_at.isoformat(),
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "last_retry": self.last_retry.isoformat() if self.last_retry else None,
            "status": self.status,
        }


class NakedPositionHandler:
    """
    Manages unhedged positions when leg 2 fails.

    Strategy:
    1. Retry hedge (30s window, max 5 attempts)
    2. Force close at market after 30 minutes
    3. Alert human after 3 failed retries
    """

    def __init__(
        self,
        poly_client: PolymarketClient,
        betfair_client: BetfairClient,
        mapper: MarketMapper,
        alerter: Alerter,
        database: Database | None = None,
        max_hold_minutes: int = 30,
        retry_interval_seconds: int = 30,
    ):
        """
        Initialize handler.

        Args:
            poly_client: Polymarket client
            betfair_client: Betfair client
            mapper: Market mapper
            alerter: Alerter for notifications
            database: Optional database for persistence
            max_hold_minutes: Max time to hold before force close
            retry_interval_seconds: Seconds between retry attempts
        """
        self.poly_client = poly_client
        self.betfair_client = betfair_client
        self.mapper = mapper
        self.alerter = alerter
        self.database = database
        self.max_hold_time = timedelta(minutes=max_hold_minutes)
        self.retry_interval = timedelta(seconds=retry_interval_seconds)

        self._positions: dict[str, NakedPosition] = {}
        self._running = False

    def register(self, position: NakedPosition, reason: str = "") -> None:
        """Register a new naked position."""
        self._positions[position.id] = position

        # Persist to database
        if self.database:
            record = NakedPositionRecord(
                id=generate_id(),
                position_id=position.id,
                mapping_id=position.mapping_id,
                platform=position.platform,
                side=position.side,
                size=position.size,
                entry_price=position.entry_price,
                reason=reason,
            )
            self.database.record_naked_position(record)

        # Alert
        self.alerter.send(
            AlertLevel.WARNING,
            f"Naked position created",
            {
                "id": position.id,
                "mapping_id": position.mapping_id,
                "platform": position.platform,
                "side": position.side,
                "size": position.size,
                "reason": reason,
            },
        )

        logger.warning(
            f"Registered naked position: {position.id}, "
            f"{position.platform} {position.side} {position.size}"
        )

    async def process_all(self) -> None:
        """Process all pending naked positions."""
        for position_id in list(self._positions.keys()):
            position = self._positions.get(position_id)
            if position is None:
                continue

            if position.status in ("hedged", "closed", "failed"):
                continue

            try:
                await self._process_position(position)
            except Exception as e:
                logger.error(f"Error processing naked position {position_id}: {e}")

    async def _process_position(self, position: NakedPosition) -> None:
        """Process a single naked position."""
        now = datetime.now(timezone.utc)

        # Check if max hold time exceeded
        if position.age_seconds > self.max_hold_time.total_seconds():
            logger.warning(f"Position {position.id} exceeded max hold time, force closing")
            await self._force_close(position)
            return

        # Check if retry is due
        if position.last_retry:
            time_since_retry = now - position.last_retry
            if time_since_retry < self.retry_interval:
                return  # Not time to retry yet

        # Attempt to hedge
        if position.can_retry:
            await self._attempt_hedge(position)

    async def _attempt_hedge(self, position: NakedPosition) -> bool:
        """Attempt to hedge a naked position."""
        position.retry_count += 1
        position.last_retry = datetime.now(timezone.utc)
        position.status = "retrying"

        logger.info(
            f"Attempting hedge for {position.id} "
            f"(attempt {position.retry_count}/{position.max_retries})"
        )

        mapping = self.mapper.get_by_id(position.mapping_id)
        if mapping is None:
            logger.error(f"Mapping not found for position {position.id}")
            position.status = "failed"
            return False

        try:
            # Determine hedge direction
            if position.platform == "polymarket":
                # Need to hedge on Betfair
                success = await self._hedge_on_betfair(position, mapping)
            else:
                # Need to hedge on Polymarket
                success = await self._hedge_on_poly(position, mapping)

            if success:
                position.status = "hedged"
                self._update_database(position, "hedged")
                self.alerter.send(
                    AlertLevel.INFO,
                    f"Naked position hedged successfully",
                    {"id": position.id},
                )
                return True
            else:
                # Alert after 3 failed attempts
                if position.retry_count >= 3:
                    self.alerter.send(
                        AlertLevel.WARNING,
                        f"Naked position hedge failing",
                        {
                            "id": position.id,
                            "retry_count": position.retry_count,
                            "max_retries": position.max_retries,
                        },
                    )
                return False

        except Exception as e:
            logger.error(f"Hedge attempt failed: {e}")
            return False

    async def _hedge_on_betfair(self, position: NakedPosition, mapping) -> bool:
        """Hedge a Polymarket position on Betfair."""
        # Polymarket long -> Betfair lay
        # Polymarket short -> Betfair back
        side = "LAY" if position.side == "long" else "BACK"

        # Get current Betfair prices
        betfair_market = self.betfair_client.get_market(mapping.betfair_market_id)
        if betfair_market is None:
            logger.warning("Betfair market not available")
            return False

        runner = betfair_market.get_runner(mapping.betfair_yes_selection_id)
        if runner is None:
            logger.warning("Betfair runner not available")
            return False

        # Get best price
        if side == "LAY":
            price = runner.best_lay
        else:
            price = runner.best_back

        if price is None:
            logger.warning("No Betfair price available")
            return False

        # Calculate size (simplified - in practice need proper conversion)
        # Polymarket size is in shares, need to convert to GBP
        betfair_size = position.size * position.entry_price / 1.27  # Rough USD->GBP

        result = await self.betfair_client.place_order(
            market_id=mapping.betfair_market_id,
            selection_id=mapping.betfair_yes_selection_id,
            side=side,
            price=price,
            size=betfair_size,
            timeout=5.0,
        )

        return result.status == "filled" and result.filled_size > 0

    async def _hedge_on_poly(self, position: NakedPosition, mapping) -> bool:
        """Hedge a Betfair position on Polymarket."""
        # Betfair back -> Polymarket buy
        # Betfair lay -> Polymarket sell
        side = "buy" if position.side == "back" else "sell"

        # Get current Polymarket prices
        poly_book = self.poly_client.get_order_book(mapping.poly_yes_token)
        if poly_book is None:
            logger.warning("Polymarket order book not available")
            return False

        # Get best price
        if side == "buy":
            price = poly_book.best_ask
        else:
            price = poly_book.best_bid

        if price is None:
            logger.warning("No Polymarket price available")
            return False

        # Calculate size
        poly_shares = position.size * 1.27 / price  # Rough GBP->USD->shares

        result = await self.poly_client.place_order(
            token_id=mapping.poly_yes_token,
            side=side,
            price=price,
            size=poly_shares,
            timeout=5.0,
        )

        return result.status == "filled" and result.filled_size > 0

    async def _force_close(self, position: NakedPosition) -> None:
        """Force close a position at market."""
        logger.warning(f"Force closing position {position.id}")

        self.alerter.send(
            AlertLevel.CRITICAL,
            f"Force closing naked position",
            {
                "id": position.id,
                "age_minutes": position.age_seconds / 60,
                "retry_count": position.retry_count,
            },
        )

        mapping = self.mapper.get_by_id(position.mapping_id)
        if mapping is None:
            position.status = "failed"
            self._update_database(position, "failed", "Mapping not found")
            return

        try:
            if position.platform == "polymarket":
                # Close on Polymarket - sell if long, buy if short
                side = "sell" if position.side == "long" else "buy"
                poly_book = self.poly_client.get_order_book(mapping.poly_yes_token)

                if poly_book:
                    price = poly_book.best_bid if side == "sell" else poly_book.best_ask
                    if price:
                        # Take market price (might need to adjust for market orders)
                        await self.poly_client.place_order(
                            token_id=mapping.poly_yes_token,
                            side=side,
                            price=price,
                            size=position.size,
                            timeout=10.0,
                        )
            else:
                # Close on Betfair
                opposite_side = "LAY" if position.side == "back" else "BACK"
                betfair_market = self.betfair_client.get_market(mapping.betfair_market_id)

                if betfair_market:
                    runner = betfair_market.get_runner(mapping.betfair_yes_selection_id)
                    if runner:
                        price = runner.best_lay if opposite_side == "LAY" else runner.best_back
                        if price:
                            await self.betfair_client.place_order(
                                market_id=mapping.betfair_market_id,
                                selection_id=mapping.betfair_yes_selection_id,
                                side=opposite_side,
                                price=price,
                                size=position.size,
                                timeout=10.0,
                            )

            position.status = "closed"
            self._update_database(position, "closed", "Force closed")

        except Exception as e:
            logger.error(f"Force close failed: {e}")
            position.status = "failed"
            self._update_database(position, "failed", str(e))

    async def force_close_all(self) -> None:
        """Force close all naked positions (emergency)."""
        logger.warning("Force closing ALL naked positions")

        self.alerter.send(
            AlertLevel.CRITICAL,
            f"Emergency: Force closing all {len(self._positions)} naked positions",
        )

        for position in list(self._positions.values()):
            if position.status in ("pending", "retrying"):
                await self._force_close(position)

    def _update_database(
        self, position: NakedPosition, status: str, resolution: str | None = None
    ) -> None:
        """Update position status in database."""
        if self.database:
            self.database.update_naked_position(
                naked_id=position.id,
                retry_count=position.retry_count,
                status=status,
                resolution=resolution,
                resolved_at=datetime.now(timezone.utc) if status in ("hedged", "closed", "failed") else None,
            )

    def get_all_positions(self) -> list[NakedPosition]:
        """Get all tracked positions."""
        return list(self._positions.values())

    def get_pending_positions(self) -> list[NakedPosition]:
        """Get positions that still need resolution."""
        return [p for p in self._positions.values() if p.status in ("pending", "retrying")]

    async def start(self) -> None:
        """Start the position processing loop."""
        self._running = True
        logger.info("Naked position handler started")

        while self._running:
            try:
                await self.process_all()
            except Exception as e:
                logger.error(f"Position processing error: {e}")

            await asyncio.sleep(10)  # Check every 10 seconds

    async def stop(self) -> None:
        """Stop the processing loop."""
        self._running = False
        logger.info("Naked position handler stopped")
