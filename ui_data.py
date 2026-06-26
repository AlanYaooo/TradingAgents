"""
UI 数据层 —— 名称解析 / 名称搜索 / 实时报价。

关键约束：**本模块绝不 import akshare**。akshare 内部用 py_mini_racer(V8)，在
Streamlit 脚本线程里初始化会 FATAL 崩进程。所以：
  - A股：直连 eastmoney 的 push2 接口（纯 requests，无 JS 解密，安全）。
  - 美股/港股/虚拟币：yfinance（纯 requests/pandas，安全）。

A股代码↔名称全表会缓存到 ~/.tradingagents/cn_stocks.json（按天刷新）。
"""
from __future__ import annotations

import json
import os
import sys
import time

import requests

_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")
_CN_LIST = os.path.join(_HOME, "cn_stocks.json")
_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
# eastmoney push2 有多台镜像，单台会间歇性掐连接 —— 轮询 + 重试。
_EM_HOSTS = ("push2.eastmoney.com", "82.push2.eastmoney.com",
             "80.push2.eastmoney.com", "16.push2.eastmoney.com")

CN = "🇨🇳 A股"
US = "🇺🇸 美股"
HK = "🇭🇰 港股"
CRYPTO = "₿ 虚拟币"


# --- eastmoney 直连（绕过 Clash 代理）---------------------------------------
def _ensure_no_proxy() -> None:
    cn = "eastmoney.com,push2.eastmoney.com,80.push2.eastmoney.com,push2his.eastmoney.com"
    cur = os.environ.get("NO_PROXY", "")
    if "eastmoney" not in cur:
        os.environ["NO_PROXY"] = (cur + "," + cn).strip(",")
        os.environ["no_proxy"] = os.environ["NO_PROXY"]


_ensure_no_proxy()


def _em_get(path: str, params: dict, tries: int = 2) -> dict | None:
    """打 eastmoney push2 接口：多 host 轮询 + 重试（单台会间歇掉连接）。"""
    last = None
    for attempt in range(tries):
        for host in _EM_HOSTS:
            try:
                r = requests.get(f"https://{host}{path}", params=params, headers=_H, timeout=12)
                if r.status_code == 200:
                    return r.json()
            except Exception as e:  # noqa: BLE001
                last = e
        if attempt + 1 < tries:
            time.sleep(0.4)
    return None


# --- A股代码 ↔ 交易所后缀（复刻框架 infer_exchange，避免 import 框架）---------
def _exchange(code: str) -> str:
    if code.startswith(("60", "688", "689", "900", "5")):
        return "SH"
    if code.startswith(("00", "30", "200", "159", "12")):
        return "SZ"
    if code.startswith(("8", "4", "920", "92")):
        return "BJ"
    return "SH" if code.startswith("6") else "SZ"


def _ticker_of(code: str) -> str:
    """6 位代码 -> Yahoo 风格 ticker（框架可识别）。"""
    ex = _exchange(code)
    return f"{code}.{'SS' if ex == 'SH' else ex}"


def _secid(code: str) -> str:
    """6 位代码 -> eastmoney secid（SH=1，SZ/BJ=0）。"""
    return ("1." if _exchange(code) == "SH" else "0.") + code


def cn_code(ticker: str) -> str | None:
    """A股 ticker（600519.SS / SH600519 / 600519）-> 6 位代码，非 A股返回 None。"""
    t = (ticker or "").upper().strip()
    for suf in (".SS", ".SH", ".SZ", ".BJ"):
        if t.endswith(suf) and t[: -len(suf)].isdigit():
            return t[: -len(suf)]
    for pre in ("SH", "SZ", "BJ"):
        u = t.replace(".", "")
        if u.startswith(pre) and u[len(pre):].isdigit() and len(u[len(pre):]) == 6:
            return u[len(pre):]
    if t.isdigit() and len(t) == 6:
        return t
    return None


# --- A股全表（akshare 子进程拉取，按天缓存）---------------------------------
# 直接并发抓 eastmoney clist 会触发反爬封禁；改由 cn_list_worker.py 子进程用
# akshare 取官方源全表（也隔离了 py_mini_racer），稳。
def _read_cn_cache() -> dict:
    try:
        return json.load(open(_CN_LIST, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _refresh_cn_list(timeout: int = 90) -> None:
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run([sys.executable, os.path.join(here, "cn_list_worker.py"), _CN_LIST],
                       cwd=here, timeout=timeout, capture_output=True)
    except Exception:  # noqa: BLE001
        pass


def cn_stock_list(force: bool = False) -> list[dict]:
    today = time.strftime("%Y%m%d")
    cached = _read_cn_cache()
    if not force and cached.get("day") == today and cached.get("list"):
        return cached["list"]
    _refresh_cn_list()
    fresh = _read_cn_cache()
    if fresh.get("list"):
        return fresh["list"]
    return cached.get("list", [])  # 刷新失败 -> 用旧缓存（即便过期）


def _cn_name_map() -> dict:
    return {e["code"]: e["name"] for e in cn_stock_list()}


# --- 各市场热门精选（搜索兜底 + 行情默认列表）-------------------------------
CURATED = {
    US: [("NVDA", "英伟达 NVIDIA"), ("AAPL", "苹果 Apple"), ("TSLA", "特斯拉 Tesla"),
         ("MSFT", "微软 Microsoft"), ("AMZN", "亚马逊 Amazon"), ("GOOGL", "谷歌 Alphabet"),
         ("META", "Meta"), ("AMD", "AMD"), ("PLTR", "Palantir"), ("SPY", "标普500 ETF")],
    HK: [("0700.HK", "腾讯控股"), ("9988.HK", "阿里巴巴"), ("3690.HK", "美团"),
         ("1810.HK", "小米集团"), ("9618.HK", "京东集团"), ("9888.HK", "百度集团"),
         ("0939.HK", "建设银行"), ("1299.HK", "友邦保险")],
    CRYPTO: [("BTC-USD", "比特币 Bitcoin"), ("ETH-USD", "以太坊 Ethereum"), ("SOL-USD", "Solana"),
             ("BNB-USD", "BNB"), ("XRP-USD", "瑞波 XRP"), ("DOGE-USD", "狗狗币 Dogecoin"),
             ("ADA-USD", "Cardano"), ("AVAX-USD", "Avalanche")],
}


def default_tickers(market: str) -> list[str]:
    if market == CN:
        return ["600519.SS", "000001.SZ", "300750.SZ", "601318.SS", "000858.SZ", "002594.SZ"]
    return [t for t, _ in CURATED.get(market, [])]


def resolve_name(ticker: str, market: str | None = None) -> str:
    """ticker -> 展示名称（A股查全表，其余查精选；查不到回退 ticker）。"""
    t = (ticker or "").strip()
    if not t:
        return t
    code = cn_code(t)
    if code and (market in (None, CN)):
        nm = _cn_name_map().get(code)
        if nm:
            return nm
    for mk, items in CURATED.items():
        if market in (None, mk):
            for tk, nm in items:
                if tk.upper() == t.upper():
                    return nm
    return t


def search(query: str, market: str, n: int = 8) -> list[dict]:
    """名称/代码搜索 -> [{name, ticker, market}]（最匹配的前 n 个）。"""
    q = (query or "").strip()
    if not q:
        return []
    if market == CN:
        ql = q.lower()
        lst = cn_stock_list()
        code_hits = [e for e in lst if e["code"].startswith(q)]
        seen = {id(e) for e in code_hits}
        name_hits = [e for e in lst if ql in e["name"].lower() and id(e) not in seen]
        out = (code_hits + name_hits)[:n]
        return [{"name": e["name"], "ticker": _ticker_of(e["code"]), "market": CN} for e in out]
    # 美股/港股/币：精选里按名称或代码匹配；US 允许直接输代码
    ql = q.lower()
    res = [{"name": nm, "ticker": tk, "market": market}
           for tk, nm in CURATED.get(market, [])
           if ql in nm.lower() or ql in tk.lower()]
    if not res and market == US and q.replace(".", "").isalnum():
        res = [{"name": q.upper(), "ticker": q.upper(), "market": US}]
    return res[:n]


# --- 实时报价 ---------------------------------------------------------------
def _cn_quotes(tickers: list[str]) -> dict:
    pairs = [(t, cn_code(t)) for t in tickers]
    pairs = [(t, c) for t, c in pairs if c]
    if not pairs:
        return {}
    secids = ",".join(_secid(c) for _, c in pairs)
    params = {"secids": secids, "fields": "f12,f14,f2,f3", "fltt": 2, "invt": 2}
    d = _em_get("/api/qt/ulist.np/get", params, tries=2)
    diff = ((d or {}).get("data") or {}).get("diff") or []
    by_code = {q.get("f12"): q for q in diff}
    out = {}
    for t, c in pairs:
        q = by_code.get(c)
        if q and isinstance(q.get("f2"), (int, float)):
            out[t] = {"price": float(q["f2"]), "chg": float(q.get("f3") or 0), "name": q.get("f14")}
    return out


def _yf_quotes(tickers: list[str]) -> dict:
    if not tickers:
        return {}
    import yfinance as yf
    out = {}
    for t in tickers:
        try:
            fi = yf.Ticker(t).fast_info
            last = fi.get("lastPrice") if hasattr(fi, "get") else fi.last_price
            prev = fi.get("previousClose") if hasattr(fi, "get") else fi.previous_close
            if last:
                chg = (last / prev - 1) * 100 if prev else 0.0
                out[t] = {"price": float(last), "chg": float(chg), "name": None}
        except Exception:  # noqa: BLE001
            pass
    return out


def quotes(items: list[dict]) -> dict:
    """items: [{ticker, market}] -> {ticker: {price, chg, name}}。
    A股走 eastmoney（实时），其余走 yfinance。"""
    cn = [it["ticker"] for it in items if it.get("market") == CN or cn_code(it.get("ticker", ""))]
    other = [it["ticker"] for it in items if it["ticker"] not in cn]
    out = {}
    out.update(_cn_quotes(cn))
    # eastmoney 偶发失败的 A股 -> 回退 yfinance（延迟行情）
    miss = [t for t in cn if t not in out]
    out.update(_yf_quotes(other + miss))
    return out


if __name__ == "__main__":  # 自测
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("A股全表:", len(cn_stock_list()), "只")
    print("搜 茅台 ->", search("茅台", CN))
    print("搜 比亚迪 ->", search("比亚迪", CN)[:2])
    print("名称 600519.SS ->", resolve_name("600519.SS", CN))
    print("名称 NVDA ->", resolve_name("NVDA", US))
    print("A股报价:", _cn_quotes(["600519.SS", "000001.SZ"]))
    print("yf报价:", _yf_quotes(["AAPL", "BTC-USD"]))
