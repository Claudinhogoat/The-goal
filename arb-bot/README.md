# Polymarket-Betfair Arbitrage Bot

A Python bot that detects price discrepancies between Polymarket (crypto prediction market) and Betfair Exchange (UK betting exchange), and executes trades on both platforms simultaneously.

## Features

- **Real-time Price Monitoring**: WebSocket connections to both Polymarket and Betfair for live order book updates
- **Arbitrage Detection**: Automatically detects profitable arbitrage opportunities between platforms
- **Risk Management**: Comprehensive risk controls including position limits, daily loss limits, and drawdown protection
- **Dry Run Mode**: Test the bot without executing real trades
- **Health Monitoring**: Continuous monitoring of connections, memory usage, and system health
- **Alerting**: Telegram notifications for important events
- **Naked Position Handling**: Automatic handling of unhedged positions when one leg fails
- **Database Persistence**: SQLite storage for trades, positions, and audit logs

## Architecture

```
arb-bot/
├── config/
│   ├── config.json          # Main configuration
│   └── mappings.json        # Market mappings
├── src/
│   ├── clients/             # API clients
│   │   ├── polymarket.py    # Polymarket WebSocket & REST
│   │   └── betfair.py       # Betfair Stream & Betting API
│   ├── core/                # Business logic
│   │   ├── mapper.py        # Market mapping
│   │   ├── detector.py      # Arbitrage detection
│   │   ├── risk.py          # Risk management
│   │   └── executor.py      # Trade execution
│   ├── handlers/            # Event handlers
│   │   └── naked_position.py
│   ├── monitoring/          # System monitoring
│   │   ├── health.py
│   │   ├── alerts.py
│   │   └── reconciliation.py
│   ├── persistence/         # Data storage
│   │   ├── database.py
│   │   └── models.py
│   └── main.py              # Application entry point
├── tests/
├── requirements.txt
└── README.md
```

## Installation

```bash
# Clone the repository
cd arb-bot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Configuration

### config/config.json

```json
{
  "mode": "dry_run",
  "polymarket": {
    "api_key": "your_api_key",
    "private_key": "your_private_key",
    "ws_url": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    "rest_url": "https://clob.polymarket.com"
  },
  "betfair": {
    "app_key": "your_app_key",
    "username": "your_username",
    "password": "your_password",
    "cert_path": "/path/to/cert.pem",
    "key_path": "/path/to/key.pem"
  },
  "risk": {
    "max_single_trade_usd": 2000,
    "max_position_per_market_usd": 10000,
    "max_total_exposure_usd": 50000,
    "max_daily_loss_usd": 1000,
    "max_drawdown_pct": 0.10
  },
  "execution": {
    "order_timeout_seconds": 5,
    "max_slippage_bps": 50,
    "min_edge_bps": 300
  },
  "alerts": {
    "telegram": {
      "enabled": true,
      "bot_token": "your_bot_token",
      "chat_id": "your_chat_id"
    }
  },
  "database": {
    "path": "./data/arb_bot.db"
  },
  "initial_equity": 10000
}
```

### config/mappings.json

```json
{
  "mappings": [
    {
      "id": "election-2024",
      "name": "2024 US Presidential Election",
      "poly_market_id": "0x...",
      "poly_yes_token": "0x...",
      "poly_no_token": "0x...",
      "betfair_market_id": "1.234567890",
      "betfair_yes_selection_id": 12345678,
      "betfair_no_selection_id": 12345679,
      "min_edge_bps": 300,
      "max_position_usd": 10000,
      "enabled": true
    }
  ]
}
```

## Usage

### Dry Run Mode (Recommended for Testing)

```bash
python -m src.main config/config.json config/mappings.json
```

### Live Mode

Change `"mode": "dry_run"` to `"mode": "live"` in config.json, then run:

```bash
python -m src.main config/config.json config/mappings.json
```

## Arbitrage Logic

The bot detects two types of arbitrage opportunities:

### 1. Buy Polymarket + Lay Betfair

When Polymarket YES price (ask) < Betfair implied probability from lay odds:
- Buy YES shares on Polymarket (cheap)
- Lay (sell) on Betfair at higher implied probability

### 2. Sell Polymarket + Back Betfair

When Polymarket YES price (bid) > Betfair implied probability from back odds:
- Sell YES shares on Polymarket (expensive)
- Back (buy) on Betfair at lower implied probability

### Fee Considerations

- **Polymarket**: 2% fee on winning positions
- **Betfair**: 5% commission on net profit
- Minimum edge of 300 bps (3%) required after fees

## Risk Management

- **Single Trade Limit**: Maximum USD per trade
- **Market Position Limit**: Maximum exposure per market
- **Total Exposure Limit**: Maximum total exposure across all markets
- **Daily Loss Limit**: Stop trading after daily loss threshold
- **Drawdown Limit**: Stop trading if drawdown exceeds threshold
- **Rate Limits**: Maximum trades per minute/hour
- **Consecutive Loss Pause**: Pause trading after consecutive losses

## Execution Flow

1. **Risk Check**: Verify trade passes all risk limits
2. **Polymarket First**: Execute less liquid leg first (5s timeout)
3. **Slippage Check**: Abort if slippage > 50 bps
4. **Betfair Second**: Execute hedge leg with adjusted size
5. **Naked Position Handler**: If leg 2 fails, manage unhedged position

## Health Monitoring

The bot continuously monitors:
- WebSocket connection health (alert if no message > 30s)
- Database connectivity
- Memory usage
- CPU usage

## Alerts

Alerts are sent based on severity:
- **INFO**: Log only
- **WARNING**: Telegram notification
- **CRITICAL**: Telegram + all channels

## Database Schema

- **trades**: Executed trades with P&L
- **positions**: Open positions by market/platform
- **naked_positions**: Unhedged positions requiring resolution
- **audit_log**: System events and decisions

## Development

```bash
# Run tests
pytest tests/

# Run with debug logging
LOG_LEVEL=DEBUG python -m src.main
```

## Disclaimer

This software is for educational purposes only. Trading involves significant risk. Use at your own risk. The authors are not responsible for any financial losses incurred.

## License

MIT
