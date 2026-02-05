"""Arbitrage opportunity detection between Polymarket and Betfair."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..clients.betfair import BetfairMarket
from ..clients.polymarket import PolymarketBook
from .mapper import MarketMapping

logger = logging.getLogger(__name__)


@dataclass
class ArbOpportunity:
    """Detected arbitrage opportunity."""

    mapping_id: str
    direction: str  # 'buy_poly_lay_betfair' or 'sell_poly_back_betfair'
    poly_price: float  # Price on Polymarket (0-1)
    betfair_price: float  # Decimal odds on Betfair
    betfair_implied: float  # Implied probability from Betfair odds
    gross_edge_bps: int  # Edge before fees in basis points
    net_edge_bps: int  # Edge after fees in basis points
    poly_liquidity_usd: float  # Available liquidity on Polymarket
    betfair_liquidity_gbp: float  # Available liquidity on Betfair
    max_size_usd: float  # Maximum trade size
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(seconds=5)
    )

    @property
    def is_expired(self) -> bool:
        """Check if opportunity has expired."""
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def time_remaining_ms(self) -> int:
        """Get time remaining before expiry in milliseconds."""
        delta = self.expires_at - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds() * 1000))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging/storage."""
        return {
            "mapping_id": self.mapping_id,
            "direction": self.direction,
            "poly_price": self.poly_price,
            "betfair_price": self.betfair_price,
            "betfair_implied": self.betfair_implied,
            "gross_edge_bps": self.gross_edge_bps,
            "net_edge_bps": self.net_edge_bps,
            "poly_liquidity_usd": self.poly_liquidity_usd,
            "betfair_liquidity_gbp": self.betfair_liquidity_gbp,
            "max_size_usd": self.max_size_usd,
            "detected_at": self.detected_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


class ArbDetector:
    """Detects arbitrage opportunities between Polymarket and Betfair."""

    def __init__(
        self,
        poly_fee: float = 0.02,  # 2% on winning positions
        betfair_fee: float = 0.05,  # 5% commission on net profit
        fx_rate: float = 1.27,  # GBP to USD
        opportunity_ttl_seconds: int = 5,
    ):
        """
        Initialize detector.

        Args:
            poly_fee: Polymarket fee on winning positions (default 2%)
            betfair_fee: Betfair commission on net profit (default 5%)
            fx_rate: GBP/USD exchange rate
            opportunity_ttl_seconds: How long opportunities are valid
        """
        self.poly_fee = poly_fee
        self.betfair_fee = betfair_fee
        self.fx_rate = fx_rate
        self.opportunity_ttl = timedelta(seconds=opportunity_ttl_seconds)

    def detect(
        self,
        mapping: MarketMapping,
        poly_book: PolymarketBook,
        betfair_market: BetfairMarket,
    ) -> list[ArbOpportunity]:
        """
        Detect arbitrage opportunities for a market mapping.

        Arbitrage conditions:
        1. Buy Poly YES + Lay Betfair: poly_ask < (1 / betfair_lay)
           - Buy YES on Polymarket cheap, lay (sell) on Betfair high
        2. Sell Poly YES + Back Betfair: poly_bid > (1 / betfair_back)
           - Sell YES on Polymarket expensive, back (buy) on Betfair cheap

        Returns list of opportunities that meet minimum edge threshold.
        """
        if not mapping.enabled:
            return []

        if betfair_market.status != "OPEN":
            return []

        opportunities = []

        # Get Betfair runner for YES outcome
        runner = betfair_market.get_runner(mapping.betfair_yes_selection_id)
        if runner is None:
            return []

        # Direction 1: Buy Poly YES + Lay Betfair
        opp = self._check_buy_poly_lay_betfair(mapping, poly_book, runner)
        if opp:
            opportunities.append(opp)

        # Direction 2: Sell Poly YES + Back Betfair
        opp = self._check_sell_poly_back_betfair(mapping, poly_book, runner)
        if opp:
            opportunities.append(opp)

        return opportunities

    def _check_buy_poly_lay_betfair(
        self,
        mapping: MarketMapping,
        poly_book: PolymarketBook,
        betfair_runner: "BetfairRunner",
    ) -> ArbOpportunity | None:
        """
        Check for buy Poly YES + lay Betfair opportunity.

        Profitable when: poly_ask < (1 / betfair_lay_odds)
        We buy YES cheap on Poly and sell (lay) at higher implied prob on Betfair.
        """
        from ..clients.betfair import BetfairRunner

        poly_ask = poly_book.best_ask
        betfair_lay = betfair_runner.best_lay

        if poly_ask is None or betfair_lay is None:
            return None

        # Betfair lay odds imply a probability
        # If you lay at 2.0, you're betting against at 50% implied
        betfair_implied = 1.0 / betfair_lay

        # Gross edge: we pay poly_ask, market implies betfair_implied
        # If betfair_implied > poly_ask, there's an edge
        gross_edge = betfair_implied - poly_ask

        if gross_edge <= 0:
            return None

        gross_edge_bps = int(gross_edge * 10000)

        # Calculate net edge after fees
        # Polymarket: 2% fee on winning side
        # Betfair: 5% on net profit from the lay
        # Simplified: assume we win on poly and lose lay liability
        net_edge = self._calculate_net_edge_buy_lay(poly_ask, betfair_lay)
        net_edge_bps = int(net_edge * 10000)

        if net_edge_bps < mapping.min_edge_bps:
            return None

        # Calculate max size based on liquidity
        poly_liquidity_usd = poly_book.best_ask_size or 0  # Shares available
        betfair_liquidity_gbp = betfair_runner.best_lay_size or 0

        # Convert Betfair liquidity to USD equivalent
        betfair_liquidity_usd = betfair_liquidity_gbp * self.fx_rate

        # Max size is limited by both sides
        max_size_usd = min(
            poly_liquidity_usd * poly_ask,  # Cost to buy poly shares
            betfair_liquidity_usd,
            mapping.max_position_usd,
        )

        if max_size_usd < 10:  # Minimum trade size
            return None

        now = datetime.now(timezone.utc)

        return ArbOpportunity(
            mapping_id=mapping.id,
            direction="buy_poly_lay_betfair",
            poly_price=poly_ask,
            betfair_price=betfair_lay,
            betfair_implied=betfair_implied,
            gross_edge_bps=gross_edge_bps,
            net_edge_bps=net_edge_bps,
            poly_liquidity_usd=poly_liquidity_usd * poly_ask,
            betfair_liquidity_gbp=betfair_liquidity_gbp,
            max_size_usd=max_size_usd,
            detected_at=now,
            expires_at=now + self.opportunity_ttl,
        )

    def _check_sell_poly_back_betfair(
        self,
        mapping: MarketMapping,
        poly_book: PolymarketBook,
        betfair_runner: "BetfairRunner",
    ) -> ArbOpportunity | None:
        """
        Check for sell Poly YES + back Betfair opportunity.

        Profitable when: poly_bid > (1 / betfair_back_odds)
        We sell YES expensive on Poly and buy (back) at lower implied prob on Betfair.
        """
        from ..clients.betfair import BetfairRunner

        poly_bid = poly_book.best_bid
        betfair_back = betfair_runner.best_back

        if poly_bid is None or betfair_back is None:
            return None

        # Betfair back odds imply a probability
        betfair_implied = 1.0 / betfair_back

        # Gross edge: we receive poly_bid, market implies betfair_implied
        # If poly_bid > betfair_implied, there's an edge
        gross_edge = poly_bid - betfair_implied

        if gross_edge <= 0:
            return None

        gross_edge_bps = int(gross_edge * 10000)

        # Calculate net edge after fees
        net_edge = self._calculate_net_edge_sell_back(poly_bid, betfair_back)
        net_edge_bps = int(net_edge * 10000)

        if net_edge_bps < mapping.min_edge_bps:
            return None

        # Calculate max size based on liquidity
        poly_liquidity_usd = poly_book.best_bid_size or 0  # Shares available to sell
        betfair_liquidity_gbp = betfair_runner.best_back_size or 0

        # Convert Betfair liquidity to USD equivalent
        betfair_liquidity_usd = betfair_liquidity_gbp * self.fx_rate

        # Max size is limited by both sides
        max_size_usd = min(
            poly_liquidity_usd * poly_bid,  # Value of poly shares to sell
            betfair_liquidity_usd,
            mapping.max_position_usd,
        )

        if max_size_usd < 10:  # Minimum trade size
            return None

        now = datetime.now(timezone.utc)

        return ArbOpportunity(
            mapping_id=mapping.id,
            direction="sell_poly_back_betfair",
            poly_price=poly_bid,
            betfair_price=betfair_back,
            betfair_implied=betfair_implied,
            gross_edge_bps=gross_edge_bps,
            net_edge_bps=net_edge_bps,
            poly_liquidity_usd=poly_liquidity_usd * poly_bid,
            betfair_liquidity_gbp=betfair_liquidity_gbp,
            max_size_usd=max_size_usd,
            detected_at=now,
            expires_at=now + self.opportunity_ttl,
        )

    def _calculate_net_edge_buy_lay(
        self, poly_price: float, betfair_odds: float
    ) -> float:
        """
        Calculate net edge for buy Poly + lay Betfair strategy.

        Scenario: Buy YES on Poly at poly_price, Lay on Betfair at betfair_odds.

        If event happens (YES wins):
        - Poly: Win (1 - poly_price) per share, minus fee
        - Betfair: Lose (betfair_odds - 1) per unit staked

        If event doesn't happen (NO wins):
        - Poly: Lose poly_price per share
        - Betfair: Win stake (before commission)
        """
        betfair_implied = 1.0 / betfair_odds

        # Expected value calculation
        # Assume perfect hedge sizing

        # On YES win: Poly profit - Betfair loss
        poly_profit_yes = (1 - poly_price) * (1 - self.poly_fee)
        betfair_loss_yes = betfair_odds - 1  # Liability per unit

        # On NO win: Betfair profit - Poly loss
        poly_loss_no = poly_price
        betfair_profit_no = 1 * (1 - self.betfair_fee)  # Win stake minus commission

        # For a true arb, we size positions so profit is equal regardless of outcome
        # Simplified: net edge is approximately the gross edge minus weighted fees
        gross_edge = betfair_implied - poly_price

        # Estimate fee impact (simplified)
        avg_fee_impact = (self.poly_fee * betfair_implied + self.betfair_fee * (1 - betfair_implied)) / 2

        return gross_edge - avg_fee_impact

    def _calculate_net_edge_sell_back(
        self, poly_price: float, betfair_odds: float
    ) -> float:
        """
        Calculate net edge for sell Poly + back Betfair strategy.

        Scenario: Sell YES on Poly at poly_price, Back on Betfair at betfair_odds.

        If event happens (YES wins):
        - Poly: Owe (1 - poly_price) per share
        - Betfair: Win (betfair_odds - 1) per unit (minus commission)

        If event doesn't happen (NO wins):
        - Poly: Keep poly_price per share (minus fee if applicable)
        - Betfair: Lose stake
        """
        betfair_implied = 1.0 / betfair_odds

        # Gross edge
        gross_edge = poly_price - betfair_implied

        # Estimate fee impact (simplified)
        avg_fee_impact = (self.poly_fee * (1 - betfair_implied) + self.betfair_fee * betfair_implied) / 2

        return gross_edge - avg_fee_impact

    def calculate_optimal_sizes(
        self,
        opportunity: ArbOpportunity,
        max_usd: float,
    ) -> tuple[float, float]:
        """
        Calculate optimal position sizes for both legs.

        Returns (poly_size_shares, betfair_size_gbp).
        """
        if opportunity.direction == "buy_poly_lay_betfair":
            # Buy poly shares, lay on betfair
            # Size poly to spend max_usd
            poly_shares = max_usd / opportunity.poly_price

            # Size betfair lay to hedge
            # Liability = (odds - 1) * stake
            # We want liability ≈ poly_shares * (1 - poly_price)
            target_liability = poly_shares * (1 - opportunity.poly_price)
            betfair_stake = target_liability / (opportunity.betfair_price - 1)
            betfair_gbp = betfair_stake / self.fx_rate

            return (poly_shares, betfair_gbp)

        else:  # sell_poly_back_betfair
            # Sell poly shares, back on betfair
            # Size poly to receive max_usd
            poly_shares = max_usd / opportunity.poly_price

            # Size betfair back to hedge
            # Profit = (odds - 1) * stake
            # We want profit ≈ poly_shares * poly_price (what we owe if YES wins)
            target_profit = poly_shares * opportunity.poly_price
            betfair_stake = target_profit / (opportunity.betfair_price - 1)
            betfair_gbp = betfair_stake / self.fx_rate

            return (poly_shares, betfair_gbp)
