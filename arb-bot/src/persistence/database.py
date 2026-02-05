"""SQLite database persistence layer."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from .models import AuditLog, NakedPositionRecord, Position, Trade


class Database:
    """SQLite database for persisting trades, positions, and logs."""

    def __init__(self, path: str):
        """Initialize database connection."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get database connection with context management."""
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Trades table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    mapping_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    poly_size REAL NOT NULL,
                    poly_price REAL NOT NULL,
                    betfair_size REAL NOT NULL,
                    betfair_price REAL NOT NULL,
                    status TEXT NOT NULL,
                    gross_edge_bps INTEGER NOT NULL,
                    net_edge_bps INTEGER NOT NULL,
                    poly_order_id TEXT,
                    betfair_order_id TEXT,
                    pnl REAL,
                    fees_paid REAL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Positions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id TEXT PRIMARY KEY,
                    mapping_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    side TEXT NOT NULL,
                    size REAL NOT NULL,
                    avg_price REAL NOT NULL,
                    unrealized_pnl REAL DEFAULT 0,
                    realized_pnl REAL DEFAULT 0,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT
                )
            """)

            # Naked positions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS naked_positions (
                    id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    mapping_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    side TEXT NOT NULL,
                    size REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    reason TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 5,
                    status TEXT DEFAULT 'pending',
                    resolution TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                )
            """)

            # Audit log table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    details TEXT NOT NULL,
                    severity TEXT DEFAULT 'INFO',
                    timestamp TEXT NOT NULL
                )
            """)

            # Create indexes for common queries
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_mapping_id ON trades(mapping_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades(created_at)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_positions_mapping_id ON positions(mapping_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_positions_open ON positions(closed_at)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_naked_positions_status ON naked_positions(status)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)"
            )

            conn.commit()

    def record_trade(self, trade: Trade) -> None:
        """Record a trade to the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            data = trade.to_dict()
            cursor.execute(
                """
                INSERT INTO trades (
                    id, mapping_id, direction, poly_size, poly_price,
                    betfair_size, betfair_price, status, gross_edge_bps,
                    net_edge_bps, poly_order_id, betfair_order_id, pnl,
                    fees_paid, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    data["id"],
                    data["mapping_id"],
                    data["direction"],
                    data["poly_size"],
                    data["poly_price"],
                    data["betfair_size"],
                    data["betfair_price"],
                    data["status"],
                    data["gross_edge_bps"],
                    data["net_edge_bps"],
                    data["poly_order_id"],
                    data["betfair_order_id"],
                    data["pnl"],
                    data["fees_paid"],
                    data["error"],
                    data["created_at"],
                ),
            )
            conn.commit()

    def get_trades(
        self,
        mapping_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[Trade]:
        """Get trades with optional filters."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM trades WHERE 1=1"
            params: list[Any] = []

            if mapping_id:
                query += " AND mapping_id = ?"
                params.append(mapping_id)

            if since:
                query += " AND created_at >= ?"
                params.append(since.isoformat())

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            return [Trade.from_dict(dict(row)) for row in rows]

    def record_position(self, position: Position) -> None:
        """Record or update a position."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            data = position.to_dict()
            cursor.execute(
                """
                INSERT OR REPLACE INTO positions (
                    id, mapping_id, platform, side, size, avg_price,
                    unrealized_pnl, realized_pnl, opened_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    data["id"],
                    data["mapping_id"],
                    data["platform"],
                    data["side"],
                    data["size"],
                    data["avg_price"],
                    data["unrealized_pnl"],
                    data["realized_pnl"],
                    data["opened_at"],
                    data["closed_at"],
                ),
            )
            conn.commit()

    def get_open_positions(self, mapping_id: str | None = None) -> list[Position]:
        """Get all open positions."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM positions WHERE closed_at IS NULL"
            params: list[Any] = []

            if mapping_id:
                query += " AND mapping_id = ?"
                params.append(mapping_id)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            return [Position.from_dict(dict(row)) for row in rows]

    def close_position(self, position_id: str, realized_pnl: float) -> None:
        """Close a position."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE positions
                SET closed_at = ?, realized_pnl = ?
                WHERE id = ?
            """,
                (datetime.utcnow().isoformat(), realized_pnl, position_id),
            )
            conn.commit()

    def record_naked_position(self, naked: NakedPositionRecord) -> None:
        """Record a naked position."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            data = naked.to_dict()
            cursor.execute(
                """
                INSERT OR REPLACE INTO naked_positions (
                    id, position_id, mapping_id, platform, side, size,
                    entry_price, reason, retry_count, max_retries,
                    status, resolution, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    data["id"],
                    data["position_id"],
                    data["mapping_id"],
                    data["platform"],
                    data["side"],
                    data["size"],
                    data["entry_price"],
                    data["reason"],
                    data["retry_count"],
                    data["max_retries"],
                    data["status"],
                    data["resolution"],
                    data["created_at"],
                    data["resolved_at"],
                ),
            )
            conn.commit()

    def get_pending_naked_positions(self) -> list[NakedPositionRecord]:
        """Get all pending naked positions."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM naked_positions WHERE status IN ('pending', 'retrying')"
            )
            rows = cursor.fetchall()

            return [NakedPositionRecord.from_dict(dict(row)) for row in rows]

    def update_naked_position(
        self,
        naked_id: str,
        retry_count: int | None = None,
        status: str | None = None,
        resolution: str | None = None,
        resolved_at: datetime | None = None,
    ) -> None:
        """Update a naked position."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            updates = []
            params: list[Any] = []

            if retry_count is not None:
                updates.append("retry_count = ?")
                params.append(retry_count)

            if status is not None:
                updates.append("status = ?")
                params.append(status)

            if resolution is not None:
                updates.append("resolution = ?")
                params.append(resolution)

            if resolved_at is not None:
                updates.append("resolved_at = ?")
                params.append(resolved_at.isoformat())

            if updates:
                query = f"UPDATE naked_positions SET {', '.join(updates)} WHERE id = ?"
                params.append(naked_id)
                cursor.execute(query, params)
                conn.commit()

    def log_event(self, event_type: str, details: dict[str, Any], severity: str = "INFO") -> None:
        """Log an audit event."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            log = AuditLog(event_type=event_type, details=details, severity=severity)
            data = log.to_dict()
            cursor.execute(
                """
                INSERT INTO audit_log (id, event_type, details, severity, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    data["id"],
                    data["event_type"],
                    json.dumps(data["details"]),
                    data["severity"],
                    data["timestamp"],
                ),
            )
            conn.commit()

    def get_audit_logs(
        self,
        event_type: str | None = None,
        severity: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditLog]:
        """Get audit logs with optional filters."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM audit_log WHERE 1=1"
            params: list[Any] = []

            if event_type:
                query += " AND event_type = ?"
                params.append(event_type)

            if severity:
                query += " AND severity = ?"
                params.append(severity)

            if since:
                query += " AND timestamp >= ?"
                params.append(since.isoformat())

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            logs = []
            for row in rows:
                data = dict(row)
                data["details"] = json.loads(data["details"])
                logs.append(AuditLog.from_dict(data))

            return logs

    def get_daily_pnl(self, date: datetime | None = None) -> float:
        """Get total P&L for a given day."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if date is None:
                date = datetime.utcnow()

            date_str = date.strftime("%Y-%m-%d")
            cursor.execute(
                """
                SELECT COALESCE(SUM(pnl), 0) as total_pnl
                FROM trades
                WHERE created_at LIKE ? AND pnl IS NOT NULL
            """,
                (f"{date_str}%",),
            )
            row = cursor.fetchone()
            return float(row["total_pnl"]) if row else 0.0

    def get_trade_count(self, since: datetime) -> int:
        """Get number of trades since a given time."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as count FROM trades WHERE created_at >= ?",
                (since.isoformat(),),
            )
            row = cursor.fetchone()
            return int(row["count"]) if row else 0
