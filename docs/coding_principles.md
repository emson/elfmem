# elf0_trader - Coding Principles

## Philosophy: SIMPLE, ELEGANT, FLEXIBLE, ROBUST

We write **functional-style Python** that is easy for both humans and LLMs to understand, modify, and debug.

### Core Principles

1. **Fail Fast** - Let exceptions bubble up; handle only at system boundaries
2. **Pure Functions** - Input → Output, no side effects, no mutation
3. **Composable** - Small functions (≤50 lines) composed into pipelines
4. **No Defensive Code** - Don't catch exceptions you can't meaningfully handle
5. **Type Hints** - Complete type hints on all functions
6. **LLM-Friendly** - Code structure that AI agents can easily understand

---

## Functional Programming Patterns

### Pattern 1: Compose Small Functions into Pipelines

```python
# ✅ GOOD: Composite function from smaller subfunctions
def process_market_data(symbol: str, start: date, end: date) -> DataFrame:
    """Pipeline: fetch → validate → normalise → enrich."""
    raw_data = fetch_ohlcv(symbol, start, end)
    validated = validate_ohlcv(raw_data)
    normalised = normalise_timestamps(validated)
    return add_technical_indicators(normalised)

# ❌ BAD: Monolithic function doing everything
def process_market_data(symbol: str, start: date, end: date) -> DataFrame:
    # 200 lines of mixed concerns...
    pass
```

### Pattern 2: Early Returns, Not Nested Ifs

```python
# ✅ GOOD: Guard clauses with early returns
def validate_symbol(symbol: str) -> str:
    if not symbol:
        raise ValueError("Symbol required")
    if not symbol.replace("-", "").replace("/", "").isalnum():
        raise ValueError(f"Invalid symbol: {symbol}")
    return symbol.upper()

# ❌ BAD: Nested conditionals
def validate_symbol(symbol: str) -> str:
    if symbol:
        if symbol.isalpha():
            return symbol.upper()
        else:
            raise ValueError(f"Invalid symbol: {symbol}")
    else:
        raise ValueError("Symbol required")
```

### Pattern 3: Transform Data, Don't Mutate

```python
# ✅ GOOD: Return new data
def add_returns(df: DataFrame) -> DataFrame:
    return df.assign(returns=df["close"].pct_change())

# ❌ BAD: Mutate in place
def add_returns(df: DataFrame) -> None:
    df["returns"] = df["close"].pct_change()
```

### Pattern 4: Functions Under 50 Lines

If a function exceeds 50 lines, extract subfunctions:

```python
# ✅ GOOD: Main function composes subfunctions
def save_ohlcv(df: DataFrame, symbol: str, source: str) -> Path:
    """Save OHLCV data to filesystem."""
    path = build_storage_path(symbol, source)
    ensure_directory(path.parent)
    write_csv(df, path)
    write_metadata(symbol, source, path.parent)
    return path

# Each subfunction is focused and testable
def build_storage_path(symbol: str, source: str) -> Path: ...
def ensure_directory(path: Path) -> None: ...
def write_csv(df: DataFrame, path: Path) -> None: ...
def write_metadata(symbol: str, source: str, dir: Path) -> None: ...
```

---

## Error Handling

### Errors Bubble Up (LLM Can See Them)

**Business logic has NO try/catch.** Errors must be visible to the LLM for debugging.

```python
# ✅ GOOD: Business logic - errors bubble up
def fetch_yahoo(symbol: str, start: date, end: date) -> DataFrame:
    if not symbol:
        raise ValueError("Symbol required")
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start, end=end)
    if df.empty:
        raise ValueError(f"No data for {symbol}")
    return df

# ✅ GOOD: CLI boundary - catch and format errors here
@app.command()
def fetch(symbol: str, start: str, end: str):
    try:
        result = fetch_yahoo(symbol, parse_date(start), parse_date(end))
        console.print(format_dataframe(result))
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

# ❌ BAD: Hidden errors - LLM can't debug this
def fetch_yahoo(symbol: str, start: date, end: date) -> DataFrame | None:
    try:
        ticker = yf.Ticker(symbol)
        return ticker.history(start=start, end=end)
    except Exception:
        return None  # Error hidden!
```

### Domain-Specific Exceptions

```python
# Define clear exception types
class ValidationError(ValueError):
    """Raised when input validation fails."""

class DataFetchError(Exception):
    """Raised when data fetching fails."""

class StorageError(Exception):
    """Raised when storage operations fail."""
```

---

## LLM-Friendly Code

This codebase will be read and modified by LLMs. Write code that AI agents can easily understand.

### Naming: Be Explicit and Descriptive

```python
# ✅ GOOD: Names reveal intent
def fetch_daily_ohlcv(symbol: str, start_date: date, end_date: date) -> DataFrame:
    """Fetch daily OHLCV bars from Yahoo Finance."""
    ...

def calculate_rolling_volatility(prices: Series, window_days: int = 20) -> Series:
    """Calculate annualised rolling volatility."""
    ...

# ❌ BAD: Cryptic names - LLM must guess intent
def fetch(sym: str, s: date, e: date) -> DataFrame: ...
def calc_vol(p: Series, w: int = 20) -> Series: ...
```

### Docstrings: Purpose, Args, Returns, Raises

```python
def save_ohlcv(
    df: DataFrame,
    symbol: str,
    source: str,
    timeframe: str = "1d",
) -> Path:
    """
    Save OHLCV data to filesystem in CSV format.

    Args:
        df: DataFrame with columns [timestamp, open, high, low, close, volume]
        symbol: Ticker symbol (e.g., "AAPL", "BTC-USDT")
        source: Data source (e.g., "yahoo", "alpaca", "binance")
        timeframe: Bar timeframe (e.g., "1d", "1h", "5m")

    Returns:
        Path to the saved CSV file.

    Raises:
        ValueError: If DataFrame is empty or missing required columns.
    """
    ...
```

### Comments: Explain "Why", Not "What"

```python
# ✅ GOOD: Comments explain non-obvious decisions
def normalise_timestamps(df: DataFrame) -> DataFrame:
    # Yahoo returns timezone-aware timestamps, but we store as UTC naive
    # for consistent comparison across sources
    return df.assign(
        timestamp=df["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
    )

# ❌ BAD: Comment states the obvious
def normalise_timestamps(df: DataFrame) -> DataFrame:
    # Convert timestamps
    return df.assign(timestamp=df["timestamp"].dt.tz_convert("UTC"))
```

### Constants: No Magic Numbers or Strings

```python
# ✅ GOOD: Named constants - LLM understands the meaning
TRADING_DAYS_PER_YEAR = 252
DEFAULT_VOLATILITY_WINDOW = 20
OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

def calculate_annualised_volatility(
    returns: Series,
    window: int = DEFAULT_VOLATILITY_WINDOW
) -> Series:
    daily_vol = returns.rolling(window).std()
    return daily_vol * (TRADING_DAYS_PER_YEAR ** 0.5)

# ❌ BAD: Magic numbers - LLM must guess what 252 and 20 mean
def calculate_annualised_volatility(returns: Series, window: int = 20) -> Series:
    return returns.rolling(window).std() * (252 ** 0.5)
```

### Consistent Patterns: Same Problem = Same Solution

```python
# ✅ GOOD: All fetch functions follow the same pattern
def fetch_yahoo(symbol: str, start: date, end: date, timeframe: str) -> DataFrame:
    """Fetch from Yahoo Finance."""
    validate_symbol(symbol)
    validate_date_range(start, end)
    raw_data = _call_yahoo_api(symbol, start, end, timeframe)
    return normalise_ohlcv(raw_data, source="yahoo")

def fetch_alpaca(symbol: str, start: date, end: date, timeframe: str) -> DataFrame:
    """Fetch from Alpaca."""
    validate_symbol(symbol)
    validate_date_range(start, end)
    raw_data = _call_alpaca_api(symbol, start, end, timeframe)
    return normalise_ohlcv(raw_data, source="alpaca")

# LLM can now predict how fetch_ccxt will look
```

---

## File Structure

### Predictable Locations

```
market/
├── fetch.py      # All fetch_* functions
├── quote.py      # All get_quote_* functions
├── storage.py    # All save_*/load_*/list_* functions
└── __init__.py   # Public API exports

# Function naming matches file:
# market/fetch.py → fetch_yahoo(), fetch_alpaca(), fetch_ccxt()
# market/quote.py → get_quote_yahoo(), get_quote_alpaca()
```

### Module Structure

```python
# market/fetch.py
"""Historical OHLCV data fetching from various sources."""

from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

from .validation import validate_symbol, validate_date_range
from .normalise import normalise_ohlcv

# Constants at top
DEFAULT_TIMEFRAME = "1d"

# Public functions
def fetch_yahoo(symbol: str, start: date, end: date, timeframe: str = DEFAULT_TIMEFRAME) -> pd.DataFrame:
    ...

def fetch_alpaca(symbol: str, start: date, end: date, timeframe: str = DEFAULT_TIMEFRAME) -> pd.DataFrame:
    ...

# Private helpers prefixed with underscore
def _call_yahoo_api(symbol: str, start: date, end: date, timeframe: str) -> pd.DataFrame:
    ...
```

---

## Testing

Focus on **integration tests over many unit tests**. Unit tests for core functions only.

```python
# tests/test_fetch.py
import pytest
from datetime import date
from market.fetch import fetch_yahoo, validate_symbol

# Test pure validation functions
def test_validate_symbol_valid():
    assert validate_symbol("AAPL") == "AAPL"
    assert validate_symbol("aapl") == "AAPL"

def test_validate_symbol_empty_raises():
    with pytest.raises(ValueError, match="Symbol required"):
        validate_symbol("")

# Integration test with real API (mark as slow)
@pytest.mark.slow
def test_fetch_yahoo_real_data():
    df = fetch_yahoo("AAPL", date(2024, 1, 1), date(2024, 1, 31))
    assert not df.empty
    assert "close" in df.columns
```

---

## Implementation Checklist

### Before Starting
- [ ] Read the feature document tasks
- [ ] Check existing patterns in similar files
- [ ] Understand the data flow

### During Implementation
- [ ] Functions ≤50 lines
- [ ] Complete type hints
- [ ] Docstrings with Args/Returns/Raises
- [ ] No try/catch in business logic
- [ ] Constants for magic numbers
- [ ] Consistent patterns with existing code

### Before Committing
- [ ] `uv run python -m cli --help` works
- [ ] Manual test with sample data
- [ ] `git diff` reviewed

---

## Quick Reference

| Instead of... | Do this... |
|---------------|------------|
| Long functions | Compose small functions (≤50 lines) |
| Nested if/else | Early returns with guard clauses |
| Mutating data | Return new transformed data |
| try/catch everywhere | Let errors bubble up; catch at CLI only |
| Procedural steps | Data transformation pipelines |
| Cryptic names | Descriptive names that reveal intent |
| Magic numbers | Named constants |
| Hidden errors | Visible exceptions for LLM debugging |
