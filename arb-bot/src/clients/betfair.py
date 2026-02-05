"""Betfair Exchange API client for Stream and Betting operations."""

import asyncio
import json
import logging
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class BetfairRunner:
    """Runner (selection) within a Betfair market."""

    selection_id: int
    back_prices: list[tuple[float, float]]  # (odds, size) sorted descending by odds
    lay_prices: list[tuple[float, float]]  # (odds, size) sorted ascending by odds
    status: str = "ACTIVE"
    last_traded_price: float | None = None

    @property
    def best_back(self) -> float | None:
        """Get best back odds (highest available)."""
        return self.back_prices[0][0] if self.back_prices else None

    @property
    def best_lay(self) -> float | None:
        """Get best lay odds (lowest available)."""
        return self.lay_prices[0][0] if self.lay_prices else None

    @property
    def best_back_size(self) -> float | None:
        """Get best back size."""
        return self.back_prices[0][1] if self.back_prices else None

    @property
    def best_lay_size(self) -> float | None:
        """Get best lay size."""
        return self.lay_prices[0][1] if self.lay_prices else None

    def get_back_liquidity_at_odds(self, min_odds: float) -> float:
        """Get total back liquidity at or above given odds."""
        return sum(size for odds, size in self.back_prices if odds >= min_odds)

    def get_lay_liquidity_at_odds(self, max_odds: float) -> float:
        """Get total lay liquidity at or below given odds."""
        return sum(size for odds, size in self.lay_prices if odds <= max_odds)


@dataclass
class BetfairMarket:
    """Betfair market with all runners."""

    market_id: str
    runners: dict[int, BetfairRunner]
    status: str = "OPEN"
    in_play: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def get_runner(self, selection_id: int) -> BetfairRunner | None:
        """Get runner by selection ID."""
        return self.runners.get(selection_id)


@dataclass
class OrderResult:
    """Result of a Betfair order placement."""

    order_id: str
    status: str  # 'filled', 'partial', 'cancelled', 'failed', 'timeout'
    requested_size: float
    filled_size: float
    avg_price: float
    fees: float
    error: str | None = None


class BetfairClient:
    """Client for interacting with Betfair Exchange API."""

    STREAM_HOST = "stream-api.betfair.com"
    STREAM_PORT = 443
    BETTING_API_URL = "https://api.betfair.com/exchange/betting/rest/v1.0"
    IDENTITY_URL = "https://identitysso-cert.betfair.com/api/certlogin"

    def __init__(
        self,
        app_key: str = "",
        username: str = "",
        password: str = "",
        cert_path: str = "",
        key_path: str = "",
    ):
        """Initialize Betfair client."""
        self.app_key = app_key
        self.username = username
        self.password = password
        self.cert_path = cert_path
        self.key_path = key_path

        self._session_token: str | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._subscribed_markets: set[str] = set()
        self._markets: dict[str, BetfairMarket] = {}
        self._callbacks: list[Callable[[str, BetfairMarket], None]] = []
        self._running = False
        self._reconnect_delay = 1
        self._max_reconnect_delay = 60
        self._last_message_time: datetime | None = None
        self._connection_id: int = 0
        self._request_id: int = 0
        self._session: aiohttp.ClientSession | None = None

    @property
    def last_message_time(self) -> datetime | None:
        """Get time of last received message."""
        return self._last_message_time

    @property
    def is_authenticated(self) -> bool:
        """Check if client is authenticated."""
        return self._session_token is not None

    def get_market(self, market_id: str) -> BetfairMarket | None:
        """Get cached market data."""
        return self._markets.get(market_id)

    def on_market_update(self, callback: Callable[[str, BetfairMarket], None]) -> None:
        """Register callback for market updates."""
        self._callbacks.append(callback)

    async def login(self) -> bool:
        """Authenticate with Betfair using certificate login."""
        if not all([self.username, self.password, self.cert_path, self.key_path]):
            logger.error("Missing credentials for Betfair login")
            return False

        try:
            ssl_context = ssl.create_default_context()
            ssl_context.load_cert_chain(self.cert_path, self.key_path)

            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                data = {
                    "username": self.username,
                    "password": self.password,
                }
                headers = {
                    "X-Application": self.app_key,
                    "Content-Type": "application/x-www-form-urlencoded",
                }

                async with session.post(
                    self.IDENTITY_URL, data=data, headers=headers
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if result.get("loginStatus") == "SUCCESS":
                            self._session_token = result.get("sessionToken")
                            logger.info("Successfully logged in to Betfair")
                            return True
                        else:
                            logger.error(
                                f"Login failed: {result.get('loginStatus')}"
                            )
                            return False
                    else:
                        logger.error(f"Login request failed: {resp.status}")
                        return False

        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    async def connect_stream(self) -> None:
        """Connect to Betfair streaming API."""
        self._running = True

        while self._running:
            try:
                ssl_context = ssl.create_default_context()
                self._reader, self._writer = await asyncio.open_connection(
                    self.STREAM_HOST, self.STREAM_PORT, ssl=ssl_context
                )

                self._reconnect_delay = 1
                logger.info("Connected to Betfair Stream API")

                # Send authentication
                await self._authenticate_stream()

                # Resubscribe to markets
                for market_id in self._subscribed_markets:
                    await self._send_market_subscription(market_id)

                await self._receive_loop()

            except Exception as e:
                logger.error(f"Stream connection error: {e}")

            if self._running:
                logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    async def disconnect(self) -> None:
        """Disconnect from stream and close sessions."""
        self._running = False
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None
        if self._session:
            await self._session.close()
            self._session = None

    async def subscribe(self, market_id: str) -> None:
        """Subscribe to market data updates."""
        self._subscribed_markets.add(market_id)
        if self._writer:
            await self._send_market_subscription(market_id)

    async def unsubscribe(self, market_id: str) -> None:
        """Unsubscribe from market data updates."""
        self._subscribed_markets.discard(market_id)
        # Betfair doesn't have explicit unsubscribe, need to resubscribe without the market

    def _next_request_id(self) -> int:
        """Get next request ID."""
        self._request_id += 1
        return self._request_id

    async def _authenticate_stream(self) -> None:
        """Send authentication message to stream."""
        if not self._session_token:
            logger.error("No session token for stream authentication")
            return

        auth_msg = {
            "op": "authentication",
            "id": self._next_request_id(),
            "appKey": self.app_key,
            "session": self._session_token,
        }
        await self._send_message(auth_msg)

    async def _send_market_subscription(self, market_id: str) -> None:
        """Send market subscription message."""
        sub_msg = {
            "op": "marketSubscription",
            "id": self._next_request_id(),
            "marketFilter": {"marketIds": [market_id]},
            "marketDataFilter": {
                "ladderLevels": 3,
                "fields": [
                    "EX_BEST_OFFERS",
                    "EX_TRADED",
                    "EX_MARKET_DEF",
                ],
            },
        }
        await self._send_message(sub_msg)
        logger.debug(f"Subscribed to Betfair market: {market_id}")

    async def _send_message(self, msg: dict[str, Any]) -> None:
        """Send message to stream."""
        if self._writer:
            line = json.dumps(msg) + "\r\n"
            self._writer.write(line.encode())
            await self._writer.drain()

    async def _receive_loop(self) -> None:
        """Main loop for receiving stream messages."""
        while self._running and self._reader:
            try:
                line = await self._reader.readline()
                if not line:
                    break

                self._last_message_time = datetime.now(timezone.utc)
                message = line.decode().strip()
                if message:
                    await self._handle_message(message)

            except Exception as e:
                logger.error(f"Error receiving message: {e}")
                break

    async def _handle_message(self, message: str) -> None:
        """Handle incoming stream message."""
        try:
            data = json.loads(message)
            op = data.get("op")

            if op == "connection":
                self._connection_id = data.get("connectionId")
                logger.debug(f"Connection ID: {self._connection_id}")

            elif op == "status":
                if data.get("statusCode") == "FAILURE":
                    logger.error(f"Stream status failure: {data.get('errorMessage')}")

            elif op == "mcm":  # Market change message
                await self._handle_market_change(data)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")

    async def _handle_market_change(self, data: dict[str, Any]) -> None:
        """Handle market change message."""
        for mc in data.get("mc", []):
            market_id = mc.get("id", "")
            if not market_id:
                continue

            # Get or create market
            market = self._markets.get(market_id)
            if market is None:
                market = BetfairMarket(market_id=market_id, runners={})
                self._markets[market_id] = market

            # Update market status
            if "marketDefinition" in mc:
                market_def = mc["marketDefinition"]
                market.status = market_def.get("status", market.status)
                market.in_play = market_def.get("inPlay", market.in_play)

            # Update runners
            for rc in mc.get("rc", []):
                selection_id = rc.get("id")
                if selection_id is None:
                    continue

                runner = market.runners.get(selection_id)
                if runner is None:
                    runner = BetfairRunner(
                        selection_id=selection_id,
                        back_prices=[],
                        lay_prices=[],
                    )
                    market.runners[selection_id] = runner

                # Parse best available to back (atb)
                if "atb" in rc:
                    runner.back_prices = [
                        (float(price), float(size))
                        for price, size in rc["atb"]
                        if size > 0
                    ]
                    runner.back_prices.sort(key=lambda x: x[0], reverse=True)

                # Parse best available to lay (atl)
                if "atl" in rc:
                    runner.lay_prices = [
                        (float(price), float(size))
                        for price, size in rc["atl"]
                        if size > 0
                    ]
                    runner.lay_prices.sort(key=lambda x: x[0])

                # Last traded price
                if "ltp" in rc:
                    runner.last_traded_price = float(rc["ltp"])

            market.timestamp = datetime.now(timezone.utc)

            # Notify callbacks
            for callback in self._callbacks:
                try:
                    callback(market_id, market)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure HTTP session exists."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            "X-Application": self.app_key,
            "X-Authentication": self._session_token or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def place_order(
        self,
        market_id: str,
        selection_id: int,
        side: str,  # 'BACK' or 'LAY'
        price: float,
        size: float,
        timeout: float = 5.0,
    ) -> OrderResult:
        """Place a limit order on Betfair."""
        if not self._session_token:
            return OrderResult(
                order_id="",
                status="failed",
                requested_size=size,
                filled_size=0,
                avg_price=0,
                fees=0,
                error="Not authenticated",
            )

        session = await self._ensure_session()
        url = f"{self.BETTING_API_URL}/placeOrders/"

        order_data = {
            "marketId": market_id,
            "instructions": [
                {
                    "selectionId": selection_id,
                    "side": side.upper(),
                    "orderType": "LIMIT",
                    "limitOrder": {
                        "size": round(size, 2),
                        "price": round(price, 2),
                        "persistenceType": "LAPSE",
                    },
                }
            ],
        }

        try:
            async with asyncio.timeout(timeout):
                async with session.post(
                    url, json=order_data, headers=self._get_headers()
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()

                        if result.get("status") == "SUCCESS":
                            instruction = result.get("instructionReports", [{}])[0]
                            bet_id = instruction.get("betId", "")
                            status = instruction.get("status", "")

                            if status == "SUCCESS":
                                return OrderResult(
                                    order_id=bet_id,
                                    status="filled",
                                    requested_size=size,
                                    filled_size=instruction.get("sizeMatched", 0),
                                    avg_price=instruction.get("averagePriceMatched", price),
                                    fees=0,  # Fees calculated on settlement
                                )
                            else:
                                return OrderResult(
                                    order_id="",
                                    status="failed",
                                    requested_size=size,
                                    filled_size=0,
                                    avg_price=0,
                                    fees=0,
                                    error=instruction.get("errorCode", "Unknown error"),
                                )
                        else:
                            return OrderResult(
                                order_id="",
                                status="failed",
                                requested_size=size,
                                filled_size=0,
                                avg_price=0,
                                fees=0,
                                error=result.get("errorCode", "API error"),
                            )
                    else:
                        error_text = await resp.text()
                        return OrderResult(
                            order_id="",
                            status="failed",
                            requested_size=size,
                            filled_size=0,
                            avg_price=0,
                            fees=0,
                            error=f"HTTP {resp.status}: {error_text}",
                        )

        except asyncio.TimeoutError:
            return OrderResult(
                order_id="",
                status="timeout",
                requested_size=size,
                filled_size=0,
                avg_price=0,
                fees=0,
                error="Order timed out",
            )
        except Exception as e:
            return OrderResult(
                order_id="",
                status="failed",
                requested_size=size,
                filled_size=0,
                avg_price=0,
                fees=0,
                error=str(e),
            )

    async def cancel_order(self, market_id: str, bet_id: str) -> bool:
        """Cancel an existing order."""
        if not self._session_token:
            logger.error("Not authenticated")
            return False

        session = await self._ensure_session()
        url = f"{self.BETTING_API_URL}/cancelOrders/"

        data = {
            "marketId": market_id,
            "instructions": [{"betId": bet_id}],
        }

        try:
            async with session.post(
                url, json=data, headers=self._get_headers()
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("status") == "SUCCESS"
                return False
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    async def get_positions(self, market_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Get current positions/matched bets."""
        if not self._session_token:
            return []

        session = await self._ensure_session()
        url = f"{self.BETTING_API_URL}/listCurrentOrders/"

        data = {}
        if market_ids:
            data["marketIds"] = market_ids

        try:
            async with session.post(
                url, json=data, headers=self._get_headers()
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("currentOrders", [])
                else:
                    logger.error(f"Failed to get positions: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    async def get_market_book(
        self, market_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Get market book data from REST API."""
        if not self._session_token:
            return []

        session = await self._ensure_session()
        url = f"{self.BETTING_API_URL}/listMarketBook/"

        data = {
            "marketIds": market_ids,
            "priceProjection": {
                "priceData": ["EX_BEST_OFFERS", "EX_TRADED"],
                "exBestOffersOverrides": {"bestPricesDepth": 3},
            },
        }

        try:
            async with session.post(
                url, json=data, headers=self._get_headers()
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.error(f"Failed to get market book: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching market book: {e}")
            return []
