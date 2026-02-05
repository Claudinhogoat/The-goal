"""Utility functions and helpers."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_config(filepath: str) -> dict[str, Any]:
    """Load JSON configuration from file."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {filepath}")

    with open(path, "r") as f:
        return json.load(f)


def save_config(filepath: str, config: dict[str, Any]) -> None:
    """Save configuration to JSON file."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def generate_id() -> str:
    """Generate a unique ID."""
    return str(uuid.uuid4())


def timestamp_now() -> datetime:
    """Get current UTC timestamp."""
    return datetime.now(timezone.utc)


def odds_to_probability(decimal_odds: float) -> float:
    """Convert decimal odds to implied probability."""
    if decimal_odds <= 0:
        return 0.0
    return 1.0 / decimal_odds


def probability_to_odds(probability: float) -> float:
    """Convert probability to decimal odds."""
    if probability <= 0:
        return float("inf")
    return 1.0 / probability


def calculate_edge_bps(price1: float, price2: float) -> int:
    """Calculate edge in basis points between two prices."""
    if price1 <= 0 or price2 <= 0:
        return 0
    edge = abs(price1 - price2) / max(price1, price2)
    return int(edge * 10000)


def usd_to_gbp(usd: float, fx_rate: float = 1.27) -> float:
    """Convert USD to GBP."""
    return usd / fx_rate


def gbp_to_usd(gbp: float, fx_rate: float = 1.27) -> float:
    """Convert GBP to USD."""
    return gbp * fx_rate


def format_bps(bps: int) -> str:
    """Format basis points for display."""
    return f"{bps / 100:.2f}%"


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max."""
    return max(min_val, min(max_val, value))
