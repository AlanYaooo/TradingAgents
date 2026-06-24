from datetime import datetime

from .alpha_vantage_common import _filter_csv_by_date_range, _make_api_request
from .errors import NoMarketDataError


def get_stock(
    symbol: str,
    start_date: str,
    end_date: str
) -> str:
    """
    Returns raw daily OHLCV values, adjusted close values, and historical split/dividend events
    filtered to the specified date range.

    Args:
        symbol: The name of the equity. For example: symbol=IBM
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        CSV string containing the daily adjusted time series data filtered to the date range.
    """
    # Parse dates to determine the range
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    today = datetime.now()

    # Choose outputsize based on whether the requested range is within the latest 100 days
    # Compact returns latest 100 data points, so check if start_date is recent enough
    days_from_today_to_start = (today - start_dt).days
    outputsize = "compact" if days_from_today_to_start < 100 else "full"

    params = {
        "symbol": symbol,
        "outputsize": outputsize,
        "datatype": "csv",
    }

    response = _make_api_request("TIME_SERIES_DAILY_ADJUSTED", params)

    result = _filter_csv_by_date_range(response, start_date, end_date)
    # An empty or header-only result means Alpha Vantage has no coverage for this
    # symbol (e.g. a non-US ticker). Raise a typed no-data error so the router
    # falls through to the next configured vendor instead of returning an empty
    # table that the agent would treat as valid (the "fallback illusion").
    rows = [ln for ln in (result or "").strip().splitlines() if ln.strip()]
    if len(rows) <= 1:
        raise NoMarketDataError(
            symbol, symbol, f"Alpha Vantage returned no rows for {start_date}..{end_date}"
        )
    return result
