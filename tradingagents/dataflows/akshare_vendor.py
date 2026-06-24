"""akshare data vendor — China A-share coverage (prices, fundamentals,
statements, news, macro, sentiment).

Registered in interface.py as the ``akshare`` vendor and auto-preferred for
A-share tickers. Every function matches the signature of its yfinance/AV
counterpart so it drops into ``VENDOR_METHODS`` unchanged, returns a formatted
string, and raises ``NoMarketDataError`` on empty results so the router can
fall back to the next vendor.

akshare is imported lazily (it is heavy) and every network call is wrapped in
``with_retry`` because the Chinese endpoints intermittently drop connections.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime

import pandas as pd

from .cn_market import (
    parse_cn,
    to_ak_symbol,
    to_em_symbol,
    trading_days_between,
    with_retry,
)
from .errors import NoMarketDataError

logger = logging.getLogger(__name__)

# A-share OHLCV more than this many *trading* days stale (vs the requested date)
# is rejected — holiday-aware, unlike the calendar-day guard used for US data.
MAX_OHLCV_STALE_TRADING_DAYS = 6


def _ak():
    """Lazy akshare import (heavy module; keep framework startup fast)."""
    import akshare as ak
    return ak


def _yyyymmdd(date_str: str) -> str:
    return pd.to_datetime(date_str).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# OHLCV
# ---------------------------------------------------------------------------
def _fetch_hist(code: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
    """Raw daily OHLCV from akshare, normalized to Date/Open/High/Low/Close/Volume."""
    ak = _ak()
    df = with_retry(
        lambda: ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=_yyyymmdd(start), end_date=_yyyymmdd(end), adjust=adjust,
        ),
        label=f"hist:{code}",
    )
    if df is None or df.empty:
        return pd.DataFrame()
    out = pd.DataFrame()
    out["Date"] = pd.to_datetime(df["日期"])
    out["Open"] = pd.to_numeric(df["开盘"], errors="coerce")
    out["High"] = pd.to_numeric(df["最高"], errors="coerce")
    out["Low"] = pd.to_numeric(df["最低"], errors="coerce")
    out["Close"] = pd.to_numeric(df["收盘"], errors="coerce")
    # akshare 成交量 is in 手 (lots of 100 shares); convert to shares for
    # parity with yfinance Volume semantics.
    out["Volume"] = pd.to_numeric(df["成交量"], errors="coerce") * 100
    out = out.dropna(subset=["Close"]).reset_index(drop=True)
    return out


def load_ohlcv_ak(symbol: str, curr_date: str) -> pd.DataFrame:
    """A-share OHLCV up to ``curr_date`` as a Date/Open/High/Low/Close/Volume frame.

    Used by the market-aware branch of ``stockstats_utils.load_ohlcv`` so the
    technical-indicator path and the verified market snapshot work for A-shares.
    Raises NoMarketDataError on empty or stale data (holiday-aware staleness).
    """
    code, _ex = parse_cn(symbol)
    curr_dt = pd.to_datetime(curr_date)
    start = (curr_dt - pd.DateOffset(years=2)).strftime("%Y-%m-%d")
    end = curr_dt.strftime("%Y-%m-%d")  # akshare end is inclusive

    # Cache the fetched series. Unlike the yfinance path, akshare is otherwise
    # uncached, so the market analyst (8 indicators + verified snapshot) would
    # refetch the SAME series ~9x — slow and 9x more chances to hit a flaky
    # endpoint. Cache keyed by code + window so those 9 calls share one fetch.
    from .config import get_config

    cache_dir = get_config().get("data_cache_dir")
    data = None
    cache_file = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{code}-akshare-{start}-{end}.csv")
        if os.path.exists(cache_file):
            try:
                cached = pd.read_csv(cache_file, parse_dates=["Date"])
                if not cached.empty and "Close" in cached.columns:
                    data = cached
            except Exception:  # noqa: BLE001 — poisoned cache -> refetch
                data = None
    if data is None:
        data = _fetch_hist(code, start, end)
        if data.empty:
            raise NoMarketDataError(symbol, code, "akshare returned no rows")
        if cache_file:
            try:
                data.to_csv(cache_file, index=False)
            except Exception:  # noqa: BLE001
                pass

    data = data[data["Date"] <= curr_dt].reset_index(drop=True)
    if data.empty:
        raise NoMarketDataError(symbol, code, f"no rows on or before {curr_date}")

    # Holiday-aware staleness: count *trading* days, not calendar days, so a
    # week-long Spring Festival / Golden Week break doesn't look stale.
    latest = data["Date"].max()
    td = trading_days_between(latest, curr_dt)
    stale = td if td is not None else (curr_dt - latest).days
    threshold = MAX_OHLCV_STALE_TRADING_DAYS if td is not None else 12
    if stale > threshold:
        raise NoMarketDataError(
            symbol, code,
            f"latest row {latest.date()} is {stale} "
            f"{'trading' if td is not None else 'calendar'} days before {curr_date} (stale)",
        )
    return data


def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """core_stock_apis: formatted OHLCV table for an A-share (mirrors yfinance)."""
    code, _ex = parse_cn(symbol)
    data = _fetch_hist(code, start_date, end_date)
    if data.empty:
        raise NoMarketDataError(symbol, code, f"no rows between {start_date} and {end_date}")
    end_dt = pd.to_datetime(end_date)
    data = data[data["Date"] <= end_dt]
    if data.empty:
        raise NoMarketDataError(symbol, code, f"no rows on or before {end_date}")

    name = _safe_name(code)
    out = data.copy()
    out["Date"] = out["Date"].dt.strftime("%Y-%m-%d")
    for col in ("Open", "High", "Low", "Close"):
        out[col] = out[col].round(2)
    out["Volume"] = out["Volume"].astype("int64")
    csv = out.to_csv(index=False)
    label = f"{symbol} ({name})" if name else symbol
    header = (
        f"# A-share OHLCV (akshare, 前复权) for {label} from {start_date} to {end_date}\n"
        f"# Total records: {len(out)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + csv


# ---------------------------------------------------------------------------
# Identity / name helpers
# ---------------------------------------------------------------------------
def _safe_name(code: str) -> str | None:
    """Best-effort company short name from 千股千评 (cached-ish, fail-open)."""
    try:
        row = _comment_row(code)
        if row is not None:
            return str(row.get("名称", "")).strip() or None
    except Exception:  # noqa: BLE001
        pass
    return None


_COMMENT_DF: pd.DataFrame | None = None


def _comment_df() -> pd.DataFrame:
    """千股千评 for all A-shares (institutional score, attention, valuation)."""
    global _COMMENT_DF
    if _COMMENT_DF is None:
        ak = _ak()
        _COMMENT_DF = with_retry(lambda: ak.stock_comment_em(), label="comment")
    return _COMMENT_DF


def _comment_row(code: str):
    df = _comment_df()
    hit = df[df["代码"].astype(str).str.zfill(6) == code]
    return hit.iloc[0] if not hit.empty else None


# ---------------------------------------------------------------------------
# Fundamentals overview
# ---------------------------------------------------------------------------
# Curated key metrics (substring match against akshare's 指标 column).
_ABSTRACT_KEYS = [
    "营业总收入", "归母净利润", "扣非净利润", "营业成本", "净利润",
    "每股收益", "每股净资产", "净资产收益率", "销售毛利率", "销售净利率",
    "资产负债率", "经营现金流", "每股经营现金流",
]


def _date_cols_upto(cols, curr_date, limit=5) -> list[str]:
    """Pick the most recent <= curr_date columns that look like YYYYMMDD."""
    cutoff = pd.to_datetime(curr_date)
    dated = []
    for c in cols:
        cs = str(c)
        if re.fullmatch(r"\d{8}", cs):
            try:
                d = pd.to_datetime(cs, format="%Y%m%d")
            except Exception:  # noqa: BLE001
                continue
            if d <= cutoff:
                dated.append((d, cs))
    dated.sort(reverse=True)
    return [cs for _d, cs in dated[:limit]]


def _fmt_yi(x) -> str:
    """Format a raw RMB amount in 亿 (1e8); pass small ratios through."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "N/A"
    if pd.isna(v):
        return "N/A"
    if abs(v) >= 1e8:
        return f"{v / 1e8:,.2f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:,.2f}万"
    return f"{v:,.4f}".rstrip("0").rstrip(".")


def get_fundamentals(ticker: str, curr_date: str = None) -> str:
    """fundamental_data: A-share overview — valuation + key financial summary."""
    code, ex = parse_cn(ticker)
    ak = _ak()
    lines = [f"# A股基本面 (akshare) — {ticker}"]

    # --- valuation / market snapshot from 千股千评 ---
    try:
        row = _comment_row(code)
        if row is not None:
            lines += [
                "",
                f"## 估值与市场快照 (千股千评, {row.get('交易日', '')})",
                f"- 名称: {row.get('名称', 'N/A')}",
                f"- 最新价: {row.get('最新价', 'N/A')}　涨跌幅: {row.get('涨跌幅', 'N/A')}%",
                f"- 市盈率(PE): {row.get('市盈率', 'N/A')}　换手率: {row.get('换手率', 'N/A')}%",
                f"- 综合得分: {row.get('综合得分', 'N/A')}　机构参与度: {row.get('机构参与度', 'N/A')}",
                f"- 关注指数: {row.get('关注指数', 'N/A')}　目前排名: {row.get('目前排名', 'N/A')}",
            ]
    except Exception as exc:  # noqa: BLE001
        logger.debug("comment overview failed for %s: %s", code, exc)

    # --- company info (best-effort; this endpoint is flaky) ---
    try:
        info = with_retry(lambda: ak.stock_individual_info_em(symbol=code), attempts=2, label="info")
        if info is not None and not info.empty:
            kv = dict(zip(info["item"], info["value"], strict=False))
            lines += [
                "",
                "## 公司信息",
                f"- 行业: {kv.get('行业', 'N/A')}　上市时间: {kv.get('上市时间', 'N/A')}",
                f"- 总市值: {_fmt_yi(kv.get('总市值'))}　流通市值: {_fmt_yi(kv.get('流通市值'))}",
                f"- 总股本: {_fmt_yi(kv.get('总股本'))}　流通股: {_fmt_yi(kv.get('流通股'))}",
            ]
    except Exception as exc:  # noqa: BLE001
        logger.debug("individual_info failed for %s: %s", code, exc)

    # --- key financial summary (财务摘要) ---
    try:
        abs = with_retry(lambda: ak.stock_financial_abstract(symbol=code), label="abstract")
        periods = _date_cols_upto(abs.columns, curr_date or datetime.now().strftime("%Y-%m-%d"), limit=4)
        if periods:
            lines += ["", f"## 关键财务指标 (财务摘要, 最近{len(periods)}期)", ""]
            header = "| 指标 | " + " | ".join(periods) + " |"
            sep = "|---|" + "---|" * len(periods)
            lines += [header, sep]
            seen = set()
            for _, r in abs.iterrows():
                metric = str(r.get("指标", ""))
                if not any(k in metric for k in _ABSTRACT_KEYS) or metric in seen:
                    continue
                seen.add(metric)
                cells = [_fmt_yi(r.get(p)) for p in periods]
                lines.append(f"| {metric} | " + " | ".join(cells) + " |")
    except Exception as exc:  # noqa: BLE001
        logger.debug("financial_abstract failed for %s: %s", code, exc)

    if len(lines) <= 1:
        raise NoMarketDataError(ticker, code, "akshare returned no fundamentals")
    lines += ["", "> 数据来源: 东方财富/同花顺 via akshare。A股口径，单位多为人民币。"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Financial statements (balance / income / cashflow)
# ---------------------------------------------------------------------------
_BALANCE_FIELDS = [
    ("TOTAL_ASSETS", "资产总计"), ("TOTAL_CURRENT_ASSETS", "流动资产合计"),
    ("MONETARYFUNDS", "货币资金"), ("ACCOUNTS_RECE", "应收账款"), ("INVENTORY", "存货"),
    ("FIXED_ASSET", "固定资产"), ("GOODWILL", "商誉"),
    ("TOTAL_LIABILITIES", "负债合计"), ("TOTAL_CURRENT_LIAB", "流动负债合计"),
    ("SHORT_LOAN", "短期借款"), ("LONG_LOAN", "长期借款"),
    ("TOTAL_PARENT_EQUITY", "归母所有者权益"), ("TOTAL_EQUITY", "所有者权益合计"),
]
_INCOME_FIELDS = [
    ("TOTAL_OPERATE_INCOME", "营业总收入"), ("OPERATE_INCOME", "营业收入"),
    ("TOTAL_OPERATE_COST", "营业总成本"), ("OPERATE_COST", "营业成本"),
    ("SALE_EXPENSE", "销售费用"), ("MANAGE_EXPENSE", "管理费用"), ("FINANCE_EXPENSE", "财务费用"),
    ("OPERATE_PROFIT", "营业利润"), ("TOTAL_PROFIT", "利润总额"),
    ("NETPROFIT", "净利润"), ("PARENT_NETPROFIT", "归母净利润"),
    ("DEDUCT_PARENT_NETPROFIT", "扣非归母净利润"), ("BASIC_EPS", "基本每股收益"),
]
_CASHFLOW_FIELDS = [
    ("NETCASH_OPERATE", "经营活动现金流净额"), ("NETCASH_INVEST", "投资活动现金流净额"),
    ("NETCASH_FINANCE", "筹资活动现金流净额"),
    ("TOTAL_OPERATE_INFLOW", "经营活动现金流入"), ("ASSIGN_DIVIDEND_PORFIT", "分配股利/利润付现"),
    ("END_CCE", "期末现金及等价物"),
]


def _statement(ticker, freq, curr_date, fetch_name, fields, title):
    code, ex = parse_cn(ticker)
    ak = _ak()
    em = to_em_symbol(ticker)  # e.g. SH600519
    df = with_retry(lambda: getattr(ak, fetch_name)(symbol=em), label=fetch_name)
    if df is None or df.empty:
        raise NoMarketDataError(ticker, code, f"akshare {fetch_name} returned no rows")

    df = df.copy()
    df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"], errors="coerce")
    df = df.dropna(subset=["REPORT_DATE"])
    if curr_date:
        df = df[df["REPORT_DATE"] <= pd.to_datetime(curr_date)]  # look-ahead safe
    if (freq or "").lower().startswith("annual"):
        df = df[df["REPORT_DATE"].dt.month == 12]
    df = df.sort_values("REPORT_DATE", ascending=False).head(4)
    if df.empty:
        raise NoMarketDataError(ticker, code, "no statement periods on or before curr_date")

    periods = [d.strftime("%Y-%m-%d") for d in df["REPORT_DATE"]]
    lines = [f"## {title} — {ticker} (最近{len(periods)}期, akshare/东财)", ""]
    lines.append("| 项目 | " + " | ".join(periods) + " |")
    lines.append("|---|" + "---|" * len(periods))
    for col, label in fields:
        if col not in df.columns:
            continue
        cells = [_fmt_yi(v) for v in df[col].tolist()]
        if all(c == "N/A" for c in cells):
            continue
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def get_balance_sheet(ticker, freq="quarterly", curr_date=None) -> str:
    return _statement(ticker, freq, curr_date, "stock_balance_sheet_by_report_em",
                      _BALANCE_FIELDS, "资产负债表")


def get_income_statement(ticker, freq="quarterly", curr_date=None) -> str:
    return _statement(ticker, freq, curr_date, "stock_profit_sheet_by_report_em",
                      _INCOME_FIELDS, "利润表")


def get_cashflow(ticker, freq="quarterly", curr_date=None) -> str:
    return _statement(ticker, freq, curr_date, "stock_cash_flow_sheet_by_report_em",
                      _CASHFLOW_FIELDS, "现金流量表")


# ---------------------------------------------------------------------------
# News (+ folded-in A-share sentiment so the social analyst sees it)
# ---------------------------------------------------------------------------
def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """news_data: recent Chinese news for an A-share + a 千股千评 sentiment line."""
    code, _ex = parse_cn(ticker)
    ak = _ak()
    df = with_retry(lambda: ak.stock_news_em(symbol=code), label=f"news:{code}")
    lines = [f"# A股新闻与情绪 (akshare) — {ticker}  [{start_date} ~ {end_date}]"]

    # Fold in 千股千评 sentiment so the sentiment analyst (which calls get_news)
    # gets a real China retail/institutional signal instead of empty US feeds.
    try:
        row = _comment_row(code)
        if row is not None:
            lines += [
                "",
                "## 市场情绪 (东财千股千评)",
                f"- 综合得分: {row.get('综合得分', 'N/A')} (越高越积极)　"
                f"机构参与度: {row.get('机构参与度', 'N/A')}",
                f"- 关注指数: {row.get('关注指数', 'N/A')}　市场排名: {row.get('目前排名', 'N/A')}　"
                f"主力成本: {row.get('主力成本', 'N/A')}",
            ]
    except Exception as exc:  # noqa: BLE001
        logger.debug("comment sentiment failed for %s: %s", code, exc)

    if df is not None and not df.empty:
        df = df.copy()
        df["发布时间"] = pd.to_datetime(df["发布时间"], errors="coerce")
        s, e = pd.to_datetime(start_date), pd.to_datetime(end_date) + pd.Timedelta(days=1)
        win = df[(df["发布时间"] >= s) & (df["发布时间"] < e)].sort_values("发布时间", ascending=False)
        # stock_news_em returns only the latest ~100 (real-time). If the window
        # excludes all, fall back to the most recent items ON OR BEFORE end_date
        # (never future — look-ahead safe).
        past = df[df["发布时间"] < e].sort_values("发布时间", ascending=False)
        use = win if not win.empty else past.head(10)
        lines += ["", f"## 个股新闻 ({'窗口内' if not win.empty else '最近'}{len(use)}条)", ""]
        for _, r in use.head(20).iterrows():
            t = r["发布时间"]
            ts = t.strftime("%Y-%m-%d %H:%M") if pd.notna(t) else ""
            title = str(r.get("新闻标题", "")).strip()
            src = str(r.get("文章来源", "")).strip()
            body = str(r.get("新闻内容", "")).strip().replace("\n", " ")
            lines.append(f"- [{ts}] {title} （{src}）")
            if body:
                lines.append(f"    {body[:160]}")

    if len(lines) <= 1:
        raise NoMarketDataError(ticker, code, "akshare returned no news/sentiment")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Global / macro news (Chinese)
# ---------------------------------------------------------------------------
def get_global_news(curr_date: str, look_back_days: int = None, limit: int = None) -> str:
    """news_data global: Chinese global finance flash news (东财全球财经快讯)."""
    ak = _ak()
    look_back_days = look_back_days or 7
    limit = limit or 15
    df = with_retry(lambda: ak.stock_info_global_em(), label="global_news")
    if df is None or df.empty:
        raise NoMarketDataError("global", "global", "akshare returned no global news")
    df = df.copy()
    df["发布时间"] = pd.to_datetime(df["发布时间"], errors="coerce")
    df = df.sort_values("发布时间", ascending=False)
    cutoff = pd.to_datetime(curr_date) + pd.Timedelta(days=1)  # end of curr_date
    lo = pd.to_datetime(curr_date) - pd.Timedelta(days=look_back_days)
    win = df[(df["发布时间"] < cutoff) & (df["发布时间"] >= lo)]
    note = ""
    if not win.empty:
        use = win  # look-ahead-safe: strictly on/before curr_date
    else:
        # stock_info_global_em is a REAL-TIME feed: it only holds the latest
        # ~200 flashes. When curr_date precedes them (live run dated "today" but
        # the feed clock is a few hours into the next day, or the feed has
        # scrolled past curr_date), serve the latest as a live snapshot with an
        # explicit note rather than aborting. For a true backtest this is the
        # only data the feed can give; the note makes the as-of time clear.
        use = df
        latest = df["发布时间"].max()
        note = f"（注：实时快讯，最新条目时间 {latest:%Y-%m-%d %H:%M}，按实时快照处理）"
    if use.empty:
        raise NoMarketDataError("global", "global", "akshare returned no global news")
    lines = [f"# 全球财经快讯 (akshare/东财) — 截至 {curr_date} {note}", ""]
    for _, r in use.head(limit).iterrows():
        t = r["发布时间"]
        ts = t.strftime("%Y-%m-%d %H:%M") if pd.notna(t) else ""
        title = str(r.get("标题", "")).strip()
        summ = str(r.get("摘要", "")).strip().replace("\n", " ")
        lines.append(f"- [{ts}] {title}")
        if summ:
            lines.append(f"    {summ[:200]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Macro (China)
# ---------------------------------------------------------------------------
_MACRO_FUNCS = {
    "lpr": ("macro_china_lpr", "中国 LPR 贷款市场报价利率"),
    "loan_prime_rate": ("macro_china_lpr", "中国 LPR 贷款市场报价利率"),
    "利率": ("macro_china_lpr", "中国 LPR 贷款市场报价利率"),
    "fed_funds_rate": ("macro_china_lpr", "中国 LPR (替代美联储利率)"),
    "cpi": ("macro_china_cpi_monthly", "中国 CPI 月率"),
    "inflation": ("macro_china_cpi_monthly", "中国 CPI 月率"),
    "通胀": ("macro_china_cpi_monthly", "中国 CPI 月率"),
    "ppi": ("macro_china_ppi", "中国 PPI 年率"),
    "pmi": ("macro_china_pmi_yearly", "中国官方制造业 PMI"),
    "制造业": ("macro_china_pmi_yearly", "中国官方制造业 PMI"),
    "m2": ("macro_china_money_supply", "中国货币供应量 M0/M1/M2"),
    "money_supply": ("macro_china_money_supply", "中国货币供应量 M0/M1/M2"),
    "货币": ("macro_china_money_supply", "中国货币供应量 M0/M1/M2"),
    "social_financing": ("macro_china_shrzgm", "中国社会融资规模增量"),
    "社融": ("macro_china_shrzgm", "中国社会融资规模增量"),
    "real_gdp": ("macro_china_gdp_yearly", "中国 GDP 年率"),
    "gdp": ("macro_china_gdp_yearly", "中国 GDP 年率"),
}

_DATE_COL_CANDIDATES = ("日期", "TRADE_DATE", "月份", "date", "统计时间", "时间")


def _render_macro(title: str, df: pd.DataFrame, curr_date: str, n: int = 10) -> str:
    if df is None or df.empty:
        return f"## {title}\n(无数据)"
    df = df.copy()
    date_col = next((c for c in _DATE_COL_CANDIDATES if c in df.columns), None)
    if date_col is not None:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        if curr_date:
            df = df[df[date_col] <= pd.to_datetime(curr_date)]  # exclude forecasts (look-ahead)
        df = df.sort_values(date_col).tail(n)
        df[date_col] = df[date_col].dt.strftime("%Y-%m-%d")
    else:
        df = df.tail(n)
    show_cols = list(df.columns)[:6]
    lines = [f"## {title}", "", "| " + " | ".join(map(str, show_cols)) + " |",
             "|" + "---|" * len(show_cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in show_cols) + " |")
    return "\n".join(lines)


def get_macro_indicators(indicator: str, curr_date: str, look_back_days: int = None) -> str:
    """macro_data: Chinese macro series (LPR, CPI, PMI, M2, 社融, GDP)."""
    ak = _ak()
    key = (indicator or "").strip().lower()
    n = max(6, min((look_back_days or 365) // 30 + 4, 24))

    targets = []
    if key in _MACRO_FUNCS:
        targets = [_MACRO_FUNCS[key]]
    else:
        for alias, spec in _MACRO_FUNCS.items():
            if alias in key:
                targets = [spec]
                break
    if not targets:
        # Unknown alias -> a China macro overview (rates + inflation + activity).
        targets = [
            ("macro_china_lpr", "中国 LPR"),
            ("macro_china_cpi_monthly", "中国 CPI 月率"),
            ("macro_china_pmi_yearly", "中国官方制造业 PMI"),
        ]

    blocks = [f"# 中国宏观指标 (akshare) — 截至 {curr_date}  (请求: {indicator})"]
    ok = False
    for fn_name, title in targets:
        try:
            fn = getattr(ak, fn_name, None)
            if fn is None:
                continue
            df = with_retry(fn, attempts=3, label=fn_name)
            blocks.append(_render_macro(title, df, curr_date, n))
            ok = True
        except Exception as exc:  # noqa: BLE001
            logger.debug("macro %s failed: %s", fn_name, exc)
            blocks.append(f"## {title}\n(获取失败: {exc})")
    if not ok:
        raise NoMarketDataError("china_macro", "china_macro", f"no macro data for {indicator}")
    return "\n\n".join(blocks)
