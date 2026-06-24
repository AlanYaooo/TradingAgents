"""China A-share market support: detection, symbol normalization, proxy bypass,
retry, and a trading-day calendar.

This is the shared foundation for the akshare vendor. It exists because the rest
of the data stack silently assumes US/Yahoo conventions (see the v0.3.0 audit):
there was no notion of "this ticker is a mainland A-share", no Chinese symbol
normalization, and Chinese data hosts (eastmoney/sina) are reached *directly*,
not through the user's outbound VPN proxy.

Nothing here makes network calls at import except installing a NO_PROXY rule.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

logger = logging.getLogger(__name__)

# --- Proxy bypass for Chinese data sources ---------------------------------
# A China VPN proxy (e.g. Clash on 127.0.0.1:7890) is the WRONG route for China
# data hosts: it is for reaching *foreign* sites, and routing eastmoney/sina
# through it is both unnecessary and unreliable (observed: intermittent
# "RemoteDisconnected" from the proxy on eastmoney, while a direct connection
# returns 200). We add the China data hosts to NO_PROXY so `requests` (which
# akshare uses) connects to them DIRECTLY, while Yahoo/Anthropic still use the
# proxy. Set TRADINGAGENTS_CN_NO_PROXY=0 to disable if you genuinely reach
# Chinese data only via a proxy.
_CN_DATA_HOSTS = (
    "eastmoney.com,sina.com.cn,sinajs.cn,sse.com.cn,szse.cn,bse.cn,"
    "cninfo.com.cn,csindex.com.cn,akfamily.xyz,gtimg.cn,qq.com,10jqka.com.cn"
)


def install_cn_no_proxy() -> None:
    """Add Chinese data hosts to NO_PROXY so requests reaches them directly."""
    if os.environ.get("TRADINGAGENTS_CN_NO_PROXY", "1").strip().lower() in ("0", "false", "no"):
        return
    for var in ("NO_PROXY", "no_proxy"):
        existing = os.environ.get(var, "")
        hosts = [h for h in existing.split(",") if h.strip()]
        for h in _CN_DATA_HOSTS.split(","):
            if h not in hosts:
                hosts.append(h)
        os.environ[var] = ",".join(hosts)


install_cn_no_proxy()


# --- Market detection & symbol normalization -------------------------------
# Yahoo uses 600519.SS / 000001.SZ. Mainland users and other vendors also write
# 600519.SH, SH600519, sh.600519, or the bare 600519. We accept all and resolve
# to (6-digit code, exchange) for akshare, and to canonical Yahoo for the parts
# that still use Yahoo (benchmark index, identity).

_SH_SUFFIXES = (".SS", ".SH")     # Yahoo Shanghai is .SS; many sources use .SH
_SZ_SUFFIXES = (".SZ",)
_BJ_SUFFIXES = (".BJ",)


def _strip_prefix(s: str) -> tuple[str, str | None]:
    """Handle SH/SZ/BJ prefixes: 'SH600519', 'sh.600519' -> ('600519','SH')."""
    u = s.upper().replace(".", "")
    for ex in ("SH", "SZ", "BJ"):
        if u.startswith(ex) and u[len(ex):].isdigit():
            return u[len(ex):], ex
    return s, None


def infer_exchange(code: str) -> str:
    """Infer the exchange from a bare 6-digit A-share code.

    SH: 600/601/603/605 (main), 688 (STAR). SZ: 000/001/002/003 (main),
    300/301 (ChiNext). BJ: 8xx / 4xx / 920 (Beijing Stock Exchange).
    """
    if code.startswith(("60", "688", "689", "900", "5")):
        return "SH"
    if code.startswith(("00", "30", "200", "159", "12")):
        return "SZ"
    if code.startswith(("8", "4", "920", "92")):
        return "BJ"
    # Fallback: 6 -> SH, else SZ.
    return "SH" if code.startswith("6") else "SZ"


def parse_cn(symbol: str) -> tuple[str, str] | None:
    """Return (6-digit code, exchange in {SH,SZ,BJ}) for an A-share, else None."""
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    s = symbol.strip()
    up = s.upper()

    for suf in _SH_SUFFIXES:
        if up.endswith(suf) and up[: -len(suf)].isdigit():
            return up[: -len(suf)], "SH"
    for suf in _SZ_SUFFIXES:
        if up.endswith(suf) and up[: -len(suf)].isdigit():
            return up[: -len(suf)], "SZ"
    for suf in _BJ_SUFFIXES:
        if up.endswith(suf) and up[: -len(suf)].isdigit():
            return up[: -len(suf)], "BJ"

    # Prefixed form: SH600519 / sh.600519
    code, ex = _strip_prefix(s)
    if ex and len(code) == 6 and code.isdigit():
        return code, ex

    # Bare 6-digit code -> infer exchange.
    if len(s) == 6 and s.isdigit():
        return s, infer_exchange(s)

    return None


def is_ashare(symbol: str) -> bool:
    """True if ``symbol`` is a mainland China A-share (SH/SZ/BJ)."""
    return parse_cn(symbol) is not None


# --- Run-level active market -----------------------------------------------
# Symbol-less calls (global news, macro) can't detect the market from their
# args, so the router records the market from the first symbol-bearing call of a
# run (the market analyst runs first) and these calls inherit it.
_ACTIVE_MARKET: str | None = None


def set_active_market(market: str | None) -> None:
    global _ACTIVE_MARKET
    _ACTIVE_MARKET = market


def get_active_market() -> str | None:
    return _ACTIVE_MARKET


# Run-level analysis date (YYYY-mm-dd). Symbol-less, date-less tool calls
# (prediction markets especially) read this to filter out events after the
# analysis date — look-ahead safety for backtests.
_ANALYSIS_DATE: str | None = None


def set_analysis_date(date_str: str | None) -> None:
    global _ANALYSIS_DATE
    _ANALYSIS_DATE = date_str


def get_analysis_date() -> str | None:
    return _ANALYSIS_DATE


def to_ak_symbol(symbol: str) -> str:
    """Bare 6-digit code akshare's stock_zh_a_hist / news / comment expect."""
    parsed = parse_cn(symbol)
    return parsed[0] if parsed else symbol


def to_em_symbol(symbol: str) -> str:
    """Eastmoney report-style symbol (e.g. 'SH600519') for *_by_report_em."""
    parsed = parse_cn(symbol)
    if not parsed:
        return symbol
    code, ex = parsed
    return f"{ex}{code}"


def to_yahoo_symbol(symbol: str) -> str:
    """Canonical Yahoo A-share symbol (600519.SS / 000001.SZ / 8xxxxx.BJ).

    Used by the parts that still go through Yahoo (alpha benchmark, identity).
    """
    parsed = parse_cn(symbol)
    if not parsed:
        return symbol
    code, ex = parsed
    return f"{code}.{'SS' if ex == 'SH' else ex}"


# --- Retry wrapper for the flaky Chinese endpoints --------------------------
# A shared thread pool gives every akshare call a hard wall-clock cap. Some
# akshare endpoints don't set a request timeout, so a *stalled* connection (no
# response, not a refusal) would otherwise hang the whole run forever. Running
# the call in a worker thread and waiting with a timeout bounds it. Only the
# data layer uses with_retry — the LLM (which legitimately streams for a long
# time) never goes through here.
_EXEC = ThreadPoolExecutor(max_workers=6, thread_name_prefix="akshare")


def with_retry(fn, *, attempts: int = 4, base_delay: float = 1.0,
               per_call_timeout: float = 30.0, label: str = ""):
    """Call ``fn`` with retries and a per-attempt wall-clock cap.

    Chinese data endpoints intermittently drop the connection
    ("RemoteDisconnected") or stall without responding; retries fix the former
    and the timeout fixes the latter. A timed-out attempt's thread is left to
    finish on its own (a blocking socket can't be force-killed) rather than
    blocking shutdown.
    """
    last = None
    for i in range(attempts):
        fut = _EXEC.submit(fn)
        try:
            return fut.result(timeout=per_call_timeout)
        except FuturesTimeout:
            fut.cancel()
            last = TimeoutError(f"akshare call {label!r} exceeded {per_call_timeout:.0f}s")
            logger.debug("akshare call %s timed out (try %d/%d)", label, i + 1, attempts)
        except Exception as exc:  # noqa: BLE001 — retry any transient transport error
            last = exc
            logger.debug("akshare call %s failed (try %d/%d): %s", label, i + 1, attempts, exc)
        if i < attempts - 1:
            time.sleep(base_delay * (i + 1))
    raise last


# --- Trading-day calendar (for holiday-aware staleness) ---------------------
_TRADE_DAYS: set | None = None


def get_trade_days() -> set:
    """Set of A-share trading dates (datetime.date). Cached for the process.

    Returns an empty set if the calendar cannot be fetched, so callers fall
    back to calendar-day logic rather than crashing.
    """
    global _TRADE_DAYS
    if _TRADE_DAYS is not None:
        return _TRADE_DAYS
    try:
        import akshare as ak
        import pandas as pd
        df = with_retry(lambda: ak.tool_trade_date_hist_sina(), label="trade_cal")
        days = pd.to_datetime(df["trade_date"]).dt.date
        _TRADE_DAYS = set(days)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load A-share trade calendar: %s", exc)
        _TRADE_DAYS = set()
    return _TRADE_DAYS


def trading_days_between(latest, requested) -> int | None:
    """Number of A-share trading days in (latest, requested]. None if no calendar.

    Used to judge staleness by *trading* days, so a long holiday (Spring
    Festival / Golden Week) doesn't make fresh data look stale.
    """
    import pandas as pd
    cal = get_trade_days()
    if not cal:
        return None
    lo = pd.to_datetime(latest).date()
    hi = pd.to_datetime(requested).date()
    if hi <= lo:
        return 0
    return sum(1 for d in cal if lo < d <= hi)
