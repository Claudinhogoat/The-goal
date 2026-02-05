"""Market mapping between Polymarket and Betfair."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MarketMapping:
    """Mapping between a Polymarket market and Betfair market."""

    id: str
    name: str
    poly_market_id: str
    poly_yes_token: str
    poly_no_token: str
    betfair_market_id: str
    betfair_yes_selection_id: int
    betfair_no_selection_id: int
    min_edge_bps: int = 300  # 3% minimum edge
    max_position_usd: float = 10000
    enabled: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "poly_market_id": self.poly_market_id,
            "poly_yes_token": self.poly_yes_token,
            "poly_no_token": self.poly_no_token,
            "betfair_market_id": self.betfair_market_id,
            "betfair_yes_selection_id": self.betfair_yes_selection_id,
            "betfair_no_selection_id": self.betfair_no_selection_id,
            "min_edge_bps": self.min_edge_bps,
            "max_position_usd": self.max_position_usd,
            "enabled": self.enabled,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarketMapping":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            poly_market_id=data["poly_market_id"],
            poly_yes_token=data["poly_yes_token"],
            poly_no_token=data.get("poly_no_token", ""),
            betfair_market_id=data["betfair_market_id"],
            betfair_yes_selection_id=data["betfair_yes_selection_id"],
            betfair_no_selection_id=data.get("betfair_no_selection_id", 0),
            min_edge_bps=data.get("min_edge_bps", 300),
            max_position_usd=data.get("max_position_usd", 10000),
            enabled=data.get("enabled", True),
            notes=data.get("notes", ""),
        )


class MarketMapper:
    """Manages market mappings between Polymarket and Betfair."""

    def __init__(self):
        """Initialize mapper."""
        self._mappings: dict[str, MarketMapping] = {}
        self._by_poly_token: dict[str, MarketMapping] = {}
        self._by_betfair_market: dict[str, MarketMapping] = {}
        self._by_betfair_selection: dict[tuple[str, int], MarketMapping] = {}

    def load_mappings(self, filepath: str) -> int:
        """
        Load mappings from JSON file.

        Returns number of mappings loaded.
        """
        path = Path(filepath)
        if not path.exists():
            logger.warning(f"Mappings file not found: {filepath}")
            return 0

        try:
            with open(path, "r") as f:
                data = json.load(f)

            mappings_data = data.get("mappings", [])
            count = 0

            for item in mappings_data:
                try:
                    mapping = MarketMapping.from_dict(item)
                    self.add_mapping(mapping)
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to load mapping: {e}")

            logger.info(f"Loaded {count} market mappings from {filepath}")
            return count

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse mappings file: {e}")
            return 0
        except Exception as e:
            logger.error(f"Error loading mappings: {e}")
            return 0

    def save_mappings(self, filepath: str) -> bool:
        """Save mappings to JSON file."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            data = {
                "mappings": [m.to_dict() for m in self._mappings.values()]
            }
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Failed to save mappings: {e}")
            return False

    def add_mapping(self, mapping: MarketMapping) -> None:
        """Add a market mapping."""
        self._mappings[mapping.id] = mapping

        # Index by Polymarket tokens
        self._by_poly_token[mapping.poly_yes_token] = mapping
        if mapping.poly_no_token:
            self._by_poly_token[mapping.poly_no_token] = mapping

        # Index by Betfair market
        self._by_betfair_market[mapping.betfair_market_id] = mapping

        # Index by Betfair selection
        self._by_betfair_selection[
            (mapping.betfair_market_id, mapping.betfair_yes_selection_id)
        ] = mapping
        if mapping.betfair_no_selection_id:
            self._by_betfair_selection[
                (mapping.betfair_market_id, mapping.betfair_no_selection_id)
            ] = mapping

    def remove_mapping(self, mapping_id: str) -> bool:
        """Remove a market mapping."""
        mapping = self._mappings.pop(mapping_id, None)
        if mapping is None:
            return False

        # Remove from indexes
        self._by_poly_token.pop(mapping.poly_yes_token, None)
        if mapping.poly_no_token:
            self._by_poly_token.pop(mapping.poly_no_token, None)

        self._by_betfair_market.pop(mapping.betfair_market_id, None)

        self._by_betfair_selection.pop(
            (mapping.betfair_market_id, mapping.betfair_yes_selection_id), None
        )
        if mapping.betfair_no_selection_id:
            self._by_betfair_selection.pop(
                (mapping.betfair_market_id, mapping.betfair_no_selection_id), None
            )

        return True

    def get_by_id(self, mapping_id: str) -> MarketMapping | None:
        """Get mapping by ID."""
        return self._mappings.get(mapping_id)

    def get_by_poly_token(self, token_id: str) -> MarketMapping | None:
        """Get mapping by Polymarket token ID."""
        return self._by_poly_token.get(token_id)

    def get_by_betfair_market(self, market_id: str) -> MarketMapping | None:
        """Get mapping by Betfair market ID."""
        return self._by_betfair_market.get(market_id)

    def get_by_betfair_selection(
        self, market_id: str, selection_id: int
    ) -> MarketMapping | None:
        """Get mapping by Betfair market and selection ID."""
        return self._by_betfair_selection.get((market_id, selection_id))

    def get_all(self) -> list[MarketMapping]:
        """Get all mappings."""
        return list(self._mappings.values())

    def get_all_enabled(self) -> list[MarketMapping]:
        """Get all enabled mappings."""
        return [m for m in self._mappings.values() if m.enabled]

    def get_poly_tokens(self) -> set[str]:
        """Get all Polymarket token IDs that need subscription."""
        tokens = set()
        for mapping in self.get_all_enabled():
            tokens.add(mapping.poly_yes_token)
            if mapping.poly_no_token:
                tokens.add(mapping.poly_no_token)
        return tokens

    def get_betfair_markets(self) -> set[str]:
        """Get all Betfair market IDs that need subscription."""
        return {m.betfair_market_id for m in self.get_all_enabled()}

    def is_yes_token(self, mapping: MarketMapping, token_id: str) -> bool:
        """Check if token is the YES token for a mapping."""
        return token_id == mapping.poly_yes_token

    def is_no_token(self, mapping: MarketMapping, token_id: str) -> bool:
        """Check if token is the NO token for a mapping."""
        return token_id == mapping.poly_no_token

    def is_yes_selection(
        self, mapping: MarketMapping, selection_id: int
    ) -> bool:
        """Check if selection is the YES selection for a mapping."""
        return selection_id == mapping.betfair_yes_selection_id

    def is_no_selection(
        self, mapping: MarketMapping, selection_id: int
    ) -> bool:
        """Check if selection is the NO selection for a mapping."""
        return selection_id == mapping.betfair_no_selection_id

    def __len__(self) -> int:
        """Return number of mappings."""
        return len(self._mappings)

    def __iter__(self):
        """Iterate over mappings."""
        return iter(self._mappings.values())
