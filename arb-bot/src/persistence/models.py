"""Database models and data classes."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..utils.helpers import generate_id, timestamp_now


@dataclass
class Trade:
    """Record of an executed arbitrage trade."""

    mapping_id: str
    direction: str  # 'buy_poly_lay_betfair' or 'sell_poly_back_betfair'
    poly_size: float
    poly_price: float
    betfair_size: float
    betfair_price: float
    status: str  # 'success', 'partial', 'failed'
    gross_edge_bps: int
    net_edge_bps: int
    poly_order_id: str | None = None
    betfair_order_id: str | None = None
    pnl: float | None = None
    fees_paid: float = 0.0
    error: str | None = None
    id: str = field(default_factory=generate_id)
    created_at: datetime = field(default_factory=timestamp_now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database storage."""
        return {
            "id": self.id,
            "mapping_id": self.mapping_id,
            "direction": self.direction,
            "poly_size": self.poly_size,
            "poly_price": self.poly_price,
            "betfair_size": self.betfair_size,
            "betfair_price": self.betfair_price,
            "status": self.status,
            "gross_edge_bps": self.gross_edge_bps,
            "net_edge_bps": self.net_edge_bps,
            "poly_order_id": self.poly_order_id,
            "betfair_order_id": self.betfair_order_id,
            "pnl": self.pnl,
            "fees_paid": self.fees_paid,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trade":
        """Create from dictionary."""
        data = data.copy()
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        return cls(**data)


@dataclass
class Position:
    """Open position on a platform."""

    mapping_id: str
    platform: str  # 'polymarket' or 'betfair'
    side: str  # 'long' or 'short' for poly, 'back' or 'lay' for betfair
    size: float
    avg_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    id: str = field(default_factory=generate_id)
    opened_at: datetime = field(default_factory=timestamp_now)
    closed_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        """Check if position is still open."""
        return self.closed_at is None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database storage."""
        return {
            "id": self.id,
            "mapping_id": self.mapping_id,
            "platform": self.platform,
            "side": self.side,
            "size": self.size,
            "avg_price": self.avg_price,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Position":
        """Create from dictionary."""
        data = data.copy()
        if isinstance(data.get("opened_at"), str):
            data["opened_at"] = datetime.fromisoformat(data["opened_at"])
        if isinstance(data.get("closed_at"), str):
            data["closed_at"] = datetime.fromisoformat(data["closed_at"])
        return cls(**data)


@dataclass
class NakedPositionRecord:
    """Record of an unhedged position that needs resolution."""

    position_id: str
    mapping_id: str
    platform: str
    side: str
    size: float
    entry_price: float
    reason: str  # Why hedge failed
    retry_count: int = 0
    max_retries: int = 5
    status: str = "pending"  # 'pending', 'retrying', 'closed', 'failed'
    resolution: str | None = None
    id: str = field(default_factory=generate_id)
    created_at: datetime = field(default_factory=timestamp_now)
    resolved_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database storage."""
        return {
            "id": self.id,
            "position_id": self.position_id,
            "mapping_id": self.mapping_id,
            "platform": self.platform,
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "reason": self.reason,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "status": self.status,
            "resolution": self.resolution,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NakedPositionRecord":
        """Create from dictionary."""
        data = data.copy()
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("resolved_at"), str):
            data["resolved_at"] = datetime.fromisoformat(data["resolved_at"])
        return cls(**data)


@dataclass
class AuditLog:
    """Audit log entry for tracking system events."""

    event_type: str
    details: dict[str, Any]
    severity: str = "INFO"  # 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
    id: str = field(default_factory=generate_id)
    timestamp: datetime = field(default_factory=timestamp_now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database storage."""
        return {
            "id": self.id,
            "event_type": self.event_type,
            "details": self.details,
            "severity": self.severity,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditLog":
        """Create from dictionary."""
        data = data.copy()
        if isinstance(data.get("timestamp"), str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)
