"""Polymarket API client for WebSocket and REST operations."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


@dataclass
class PolymarketBook:
    """Order book snapshot for a Polymarket token."""

    token_id: str
    bids: list[tuple[float, float]]  # (price, size) sorted descending by price
    asks: list[tuple[float, float]]  # (price, size) sorted ascending by price
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def best_bid(self) -> float | None:
        """Get best bid price."""
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        """Get best ask price."""
        return self.asks[0][0] if self.asks else None

    @property
    def best_bid_size(self) -> float | None:
        """Get best bid size."""
        return self.bids[0][1] if self.bids else None

    @property
    def best_ask_size(self) -> float | None:
        """Get best ask size."""
        return self.asks[0][1] if self.asks else None

    @property
    def mid_price(self) -> float | None:
        """Get mid price."""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread(self) -> float | None:
        """Get bid-ask spread."""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    def get_liquidity_at_price(self, side: str, price: float) -> float:
        """Get total liquidity at or better than a given price."""
        if side == "bid":
            return sum(size for p, size in self.bids if p >= price)
        else:
            return sum(size for p, size in self.asks if p <= price)


@dataclass
class OrderResult:
    """Result of an order placement."""

    order_id: str
    status: str  # 'filled', 'partial', 'cancelled', 'failed', 'timeout'
    requested_size: float
    filled_size: float
    avg_price: float
    fees: float
    error: str | None = None


class PolymarketClient:
    """Client for interacting with Polymarket API."""

    def __init__(
        self,
        api_key: str = "",
        private_key: str = "",
        ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        rest_url: str = "https://clob.polymarket.com",
    ):
        """Initialize Polymarket client."""
        self.api_key = api_key
        self.private_key = private_key
        self.ws_url = ws_url
        self.rest_url = rest_url

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._subscribed_tokens: set[str] = set()
        self._order_books: dict[str, PolymarketBook] = {}
        self._callbacks: list[Callable[[str, PolymarketBook], None]] = []
        self._running = False
        self._reconnect_delay = 1
        self._max_reconnect_delay = 60
        self._last_message_time: datetime | None = None
        self._session: aiohttp.ClientSession | None = None

    @property
    def last_message_time(self) -> datetime | None:
        """Get time of last received message."""
        return self._last_message_time

    def get_order_book(self, token_id: str) -> PolymarketBook | None:
        """Get cached order book for a token."""
        return self._order_books.get(token_id)

    def on_book_update(self, callback: Callable[[str, PolymarketBook], None]) -> None:
        """Register callback for order book updates."""
        self._callbacks.append(callback)

    async def connect(self) -> None:
        """Connect to WebSocket and start receiving messages."""
        self._running = True
        while self._running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1  # Reset on successful connect
                    logger.info("Connected to Polymarket WebSocket")

                    # Resubscribe to tokens after reconnect
                    for token_id in self._subscribed_tokens:
                        await self._send_subscribe(token_id)

                    await self._receive_loop()

            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

            if self._running:
                logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None

    async def subscribe(self, token_id: str) -> None:
        """Subscribe to order book updates for a token."""
        self._subscribed_tokens.add(token_id)
        if self._ws:
            await self._send_subscribe(token_id)

    async def unsubscribe(self, token_id: str) -> None:
        """Unsubscribe from order book updates for a token."""
        self._subscribed_tokens.discard(token_id)
        if self._ws:
            await self._send_unsubscribe(token_id)

    async def _send_subscribe(self, token_id: str) -> None:
        """Send subscription message."""
        if self._ws:
            msg = {
                "type": "subscribe",
                "channel": "book",
                "assets_ids": [token_id],
            }
            await self._ws.send(json.dumps(msg))
            logger.debug(f"Subscribed to token: {token_id}")

    async def _send_unsubscribe(self, token_id: str) -> None:
        """Send unsubscription message."""
        if self._ws:
            msg = {
                "type": "unsubscribe",
                "channel": "book",
                "assets_ids": [token_id],
            }
            await self._ws.send(json.dumps(msg))
            logger.debug(f"Unsubscribed from token: {token_id}")

    async def _receive_loop(self) -> None:
        """Main loop for receiving WebSocket messages."""
        while self._running and self._ws:
            try:
                message = await self._ws.recv()
                self._last_message_time = datetime.now(timezone.utc)
                await self._handle_message(message)
            except ConnectionClosed:
                raise
            except Exception as e:
                logger.error(f"Error handling message: {e}")

    async def _handle_message(self, message: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get("type", data.get("event_type", ""))

            if msg_type == "book":
                await self._handle_book_update(data)
            elif msg_type == "error":
                logger.error(f"Polymarket error: {data.get('message')}")
            elif msg_type in ("subscribed", "unsubscribed"):
                logger.debug(f"Subscription update: {msg_type}")
            else:
                logger.debug(f"Unknown message type: {msg_type}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")

    async def _handle_book_update(self, data: dict[str, Any]) -> None:
        """Handle order book update message."""
        token_id = data.get("asset_id", "")
        if not token_id:
            return

        # Parse bids and asks
        bids = []
        asks = []

        for bid in data.get("bids", []):
            price = float(bid.get("price", 0))
            size = float(bid.get("size", 0))
            if price > 0 and size > 0:
                bids.append((price, size))

        for ask in data.get("asks", []):
            price = float(ask.get("price", 0))
            size = float(ask.get("size", 0))
            if price > 0 and size > 0:
                asks.append((price, size))

        # Sort bids descending, asks ascending
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])

        book = PolymarketBook(
            token_id=token_id,
            bids=bids,
            asks=asks,
            timestamp=datetime.now(timezone.utc),
        )

        self._order_books[token_id] = book

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(token_id, book)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure HTTP session exists."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_market(self, market_id: str) -> dict[str, Any] | None:
        """Get market info from REST API."""
        session = await self._ensure_session()
        url = f"{self.rest_url}/markets/{market_id}"

        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.error(f"Failed to get market: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"Error fetching market: {e}")
            return None

    async def get_orderbook(self, token_id: str) -> PolymarketBook | None:
        """Get order book snapshot from REST API."""
        session = await self._ensure_session()
        url = f"{self.rest_url}/book"
        params = {"token_id": token_id}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_rest_book(token_id, data)
                else:
                    logger.error(f"Failed to get orderbook: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"Error fetching orderbook: {e}")
            return None

    def _parse_rest_book(self, token_id: str, data: dict[str, Any]) -> PolymarketBook:
        """Parse REST API order book response."""
        bids = []
        asks = []

        for bid in data.get("bids", []):
            price = float(bid.get("price", 0))
            size = float(bid.get("size", 0))
            if price > 0 and size > 0:
                bids.append((price, size))

        for ask in data.get("asks", []):
            price = float(ask.get("price", 0))
            size = float(ask.get("size", 0))
            if price > 0 and size > 0:
                asks.append((price, size))

        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])

        return PolymarketBook(
            token_id=token_id,
            bids=bids,
            asks=asks,
            timestamp=datetime.now(timezone.utc),
        )

    async def place_order(
        self,
        token_id: str,
        side: str,  # 'buy' or 'sell'
        price: float,
        size: float,
        timeout: float = 5.0,
    ) -> OrderResult:
        """
        Place a limit order on Polymarket.

        Note: In production, this requires signing with private key using py_clob_client.
        This is a simplified implementation for the bot structure.
        """
        if not self.api_key or not self.private_key:
            return OrderResult(
                order_id="",
                status="failed",
                requested_size=size,
                filled_size=0,
                avg_price=0,
                fees=0,
                error="API key and private key required for trading",
            )

        session = await self._ensure_session()
        url = f"{self.rest_url}/order"

        # In production, you would use py_clob_client to sign the order
        # This is a placeholder for the order structure
        order_data = {
            "tokenID": token_id,
            "side": side.upper(),
            "price": str(price),
            "size": str(size),
            "type": "GTC",  # Good till cancelled
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with asyncio.timeout(timeout):
                async with session.post(
                    url, json=order_data, headers=headers
                ) as resp:
                    if resp.status in (200, 201):
                        result = await resp.json()
                        return OrderResult(
                            order_id=result.get("orderID", ""),
                            status="filled" if result.get("matched") else "pending",
                            requested_size=size,
                            filled_size=float(result.get("matchedSize", 0)),
                            avg_price=float(result.get("avgPrice", price)),
                            fees=float(result.get("fee", 0)),
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

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order."""
        if not self.api_key:
            logger.error("API key required for cancellation")
            return False

        session = await self._ensure_session()
        url = f"{self.rest_url}/order/{order_id}"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            async with session.delete(url, headers=headers) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get current positions."""
        if not self.api_key:
            return []

        session = await self._ensure_session()
        url = f"{self.rest_url}/positions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.error(f"Failed to get positions: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []
