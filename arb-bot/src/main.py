"""Main application for Polymarket-Betfair arbitrage bot."""

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .clients.betfair import BetfairClient, BetfairMarket
from .clients.polymarket import PolymarketClient, PolymarketBook
from .core.detector import ArbDetector, ArbOpportunity
from .core.executor import Executor
from .core.mapper import MarketMapper
from .core.risk import RiskLimits, RiskManager
from .handlers.naked_position import NakedPosition, NakedPositionHandler
from .monitoring.alerts import Alerter
from .monitoring.health import HealthMonitor
from .persistence.database import Database
from .utils.helpers import load_config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class ArbBot:
    """Main arbitrage bot application."""

    def __init__(self, config_path: str, mappings_path: str):
        """
        Initialize the bot.

        Args:
            config_path: Path to config.json
            mappings_path: Path to mappings.json
        """
        self.config_path = config_path
        self.mappings_path = mappings_path

        # Load configuration
        self.config = load_config(config_path)
        self.dry_run = self.config.get("mode", "dry_run") == "dry_run"

        # Initialize components
        self._init_components()

        # State
        self._running = False
        self._tasks: list[asyncio.Task] = []

    def _init_components(self) -> None:
        """Initialize all bot components."""
        # Database
        db_path = self.config.get("database", {}).get("path", "./data/arb_bot.db")
        self.database = Database(db_path)
        self.database.init_schema()

        # Alerter
        alert_config = self.config.get("alerts", {})
        self.alerter = Alerter(alert_config)

        # Clients
        poly_config = self.config.get("polymarket", {})
        self.poly_client = PolymarketClient(
            api_key=poly_config.get("api_key", ""),
            private_key=poly_config.get("private_key", ""),
            ws_url=poly_config.get("ws_url", "wss://ws-subscriptions-clob.polymarket.com/ws/market"),
            rest_url=poly_config.get("rest_url", "https://clob.polymarket.com"),
        )

        betfair_config = self.config.get("betfair", {})
        self.betfair_client = BetfairClient(
            app_key=betfair_config.get("app_key", ""),
            username=betfair_config.get("username", ""),
            password=betfair_config.get("password", ""),
            cert_path=betfair_config.get("cert_path", ""),
            key_path=betfair_config.get("key_path", ""),
        )

        # Market Mapper
        self.mapper = MarketMapper()
        self.mapper.load_mappings(self.mappings_path)

        # Risk Manager
        risk_config = self.config.get("risk", {})
        risk_limits = RiskLimits.from_dict(risk_config)
        initial_equity = self.config.get("initial_equity", 10000)
        self.risk_manager = RiskManager(risk_limits, initial_equity)

        # Arbitrage Detector
        execution_config = self.config.get("execution", {})
        fx_config = self.config.get("fx", {})
        self.detector = ArbDetector(
            poly_fee=0.02,
            betfair_fee=0.05,
            fx_rate=fx_config.get("gbp_usd", 1.27),
        )

        # Executor
        self.executor = Executor(
            poly_client=self.poly_client,
            betfair_client=self.betfair_client,
            risk_manager=self.risk_manager,
            mapper=self.mapper,
            detector=self.detector,
            timeout=execution_config.get("order_timeout_seconds", 5),
            max_slippage_bps=execution_config.get("max_slippage_bps", 50),
            dry_run=self.dry_run,
        )

        # Naked Position Handler
        self.naked_handler = NakedPositionHandler(
            poly_client=self.poly_client,
            betfair_client=self.betfair_client,
            mapper=self.mapper,
            alerter=self.alerter,
            database=self.database,
        )

        # Register naked position callback
        self.executor.on_naked_position(self._on_naked_position)

        # Health Monitor
        self.health_monitor = HealthMonitor(
            alerter=self.alerter,
            check_interval_seconds=30,
        )

        # Register health callbacks
        self.poly_client.on_book_update(self._on_poly_update)
        self.betfair_client.on_market_update(self._on_betfair_update)

        logger.info(f"Bot initialized in {'DRY RUN' if self.dry_run else 'LIVE'} mode")
        logger.info(f"Loaded {len(self.mapper)} market mappings")

    def _on_poly_update(self, token_id: str, book: PolymarketBook) -> None:
        """Handle Polymarket order book update."""
        self.health_monitor.on_poly_message()
        asyncio.create_task(self._check_arbitrage_poly(token_id, book))

    def _on_betfair_update(self, market_id: str, market: BetfairMarket) -> None:
        """Handle Betfair market update."""
        self.health_monitor.on_betfair_message()
        asyncio.create_task(self._check_arbitrage_betfair(market_id, market))

    def _on_naked_position(self, position: NakedPosition, reason: str) -> None:
        """Handle naked position creation."""
        self.naked_handler.register(position, reason)

    async def _check_arbitrage_poly(self, token_id: str, poly_book: PolymarketBook) -> None:
        """Check for arbitrage when Polymarket updates."""
        mapping = self.mapper.get_by_poly_token(token_id)
        if mapping is None or not mapping.enabled:
            return

        betfair_market = self.betfair_client.get_market(mapping.betfair_market_id)
        if betfair_market is None:
            return

        await self._process_opportunities(mapping, poly_book, betfair_market)

    async def _check_arbitrage_betfair(self, market_id: str, betfair_market: BetfairMarket) -> None:
        """Check for arbitrage when Betfair updates."""
        mapping = self.mapper.get_by_betfair_market(market_id)
        if mapping is None or not mapping.enabled:
            return

        poly_book = self.poly_client.get_order_book(mapping.poly_yes_token)
        if poly_book is None:
            return

        await self._process_opportunities(mapping, poly_book, betfair_market)

    async def _process_opportunities(
        self,
        mapping,
        poly_book: PolymarketBook,
        betfair_market: BetfairMarket,
    ) -> None:
        """Process detected arbitrage opportunities."""
        opportunities = self.detector.detect(mapping, poly_book, betfair_market)

        for opp in opportunities:
            logger.info(
                f"Opportunity detected: {opp.direction}, "
                f"edge={opp.net_edge_bps}bps, max_size=${opp.max_size_usd:.2f}"
            )

            # Log to database
            self.database.log_event(
                "opportunity_detected",
                opp.to_dict(),
            )

            # Execute (or simulate in dry run)
            result = await self.executor.execute(opp, opp.max_size_usd)

            # Log result
            self.database.log_event(
                "execution_result",
                result.to_dict(),
            )

            if result.is_success:
                logger.info(f"Execution successful: {result.execution_id}")
            else:
                logger.warning(f"Execution failed: {result.status} - {result.reason}")

    async def start(self) -> None:
        """Start the bot."""
        self._running = True

        logger.info("Starting Polymarket-Betfair Arbitrage Bot...")
        self.alerter.send("INFO", "Bot starting", {"mode": "dry_run" if self.dry_run else "live"})

        # Authenticate Betfair if credentials provided
        if self.betfair_client.app_key:
            logged_in = await self.betfair_client.login()
            if not logged_in:
                logger.error("Betfair login failed")
                if not self.dry_run:
                    return

        # Subscribe to markets
        for mapping in self.mapper.get_all_enabled():
            await self.poly_client.subscribe(mapping.poly_yes_token)
            if mapping.poly_no_token:
                await self.poly_client.subscribe(mapping.poly_no_token)
            await self.betfair_client.subscribe(mapping.betfair_market_id)

        # Start background tasks
        self._tasks = [
            asyncio.create_task(self.poly_client.connect()),
            asyncio.create_task(self.betfair_client.connect_stream()),
            asyncio.create_task(self.health_monitor.start()),
            asyncio.create_task(self.naked_handler.start()),
        ]

        logger.info("Bot started successfully")

        # Wait for shutdown
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("Tasks cancelled")

    async def stop(self) -> None:
        """Stop the bot gracefully."""
        logger.info("Stopping bot...")
        self._running = False

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        # Wait for tasks to complete
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # Disconnect clients
        await self.poly_client.disconnect()
        await self.betfair_client.disconnect()

        # Stop monitors
        await self.health_monitor.stop()
        await self.naked_handler.stop()

        self.alerter.send("INFO", "Bot stopped")
        logger.info("Bot stopped")

    def get_status(self) -> dict[str, Any]:
        """Get bot status summary."""
        return {
            "running": self._running,
            "mode": "dry_run" if self.dry_run else "live",
            "mappings_loaded": len(self.mapper),
            "mappings_enabled": len(self.mapper.get_all_enabled()),
            "risk": self.risk_manager.get_status(),
            "health": self.health_monitor.get_status_summary(),
            "naked_positions": len(self.naked_handler.get_pending_positions()),
        }


def setup_signal_handlers(bot: ArbBot, loop: asyncio.AbstractEventLoop) -> None:
    """Setup signal handlers for graceful shutdown."""

    def handle_signal(sig):
        logger.info(f"Received signal {sig}, shutting down...")
        asyncio.create_task(bot.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))


async def main() -> None:
    """Main entry point."""
    # Default paths
    config_path = "config/config.json"
    mappings_path = "config/mappings.json"

    # Check for command line arguments
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
    if len(sys.argv) > 2:
        mappings_path = sys.argv[2]

    # Verify files exist
    if not Path(config_path).exists():
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)

    if not Path(mappings_path).exists():
        logger.warning(f"Mappings file not found: {mappings_path}")

    # Create and start bot
    bot = ArbBot(config_path, mappings_path)

    # Setup signal handlers
    loop = asyncio.get_event_loop()
    setup_signal_handlers(bot, loop)

    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
