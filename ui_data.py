"""
UI 数据层 —— 名称解析 / 名称搜索 / 实时报价。

关键约束：**本模块绝不 import akshare**。akshare 内部用 py_mini_racer(V8)，在
Streamlit 脚本线程里初始化会 FATAL 崩进程。所以：
  - A股名称全表：交给 cn_list_worker.py 子进程（akshare 官方源 -> 干净全名）。
  - 港股/美股名称全表：直连 eastmoney clist 分页（纯 requests，安全），健壮重试缺失页。
  - 报价：A股 eastmoney 直连（实时），港股/美股/币 yfinance（并行 + 超时）。

名称全表按天缓存到 ~/.tradingagents/{cn,hk,us}_stocks.json。
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")
_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
# eastmoney push2 有多台镜像，单台会间歇性掐连接 —— 轮询 + 重试。
_EM_HOSTS = ("push2.eastmoney.com", "82.push2.eastmoney.com",
             "80.push2.eastmoney.com", "16.push2.eastmoney.com")

CN = "🇨🇳 A股"
US = "🇺🇸 美股"
HK = "🇭🇰 港股"
CRYPTO = "₿ 虚拟币"

_LIST_PATH = {
    CN: os.path.join(_HOME, "cn_stocks.json"),
    HK: os.path.join(_HOME, "hk_stocks.json"),
    US: os.path.join(_HOME, "us_stocks.json"),
}
# eastmoney clist 市场过滤：港股 m:128，美股 m:105/106/107（NASDAQ/NYSE/AMEX）
_FS = {
    HK: "m:128 t:3,m:128 t:4,m:128 t:1,m:128 t:2",
    US: "m:105,m:106,m:107",
}


# --- eastmoney 直连（绕过 Clash 代理）---------------------------------------
def _ensure_no_proxy() -> None:
    cn = "eastmoney.com,push2.eastmoney.com,82.push2.eastmoney.com,80.push2.eastmoney.com,16.push2.eastmoney.com"
    cur = os.environ.get("NO_PROXY", "")
    if "eastmoney" not in cur:
        os.environ["NO_PROXY"] = (cur + "," + cn).strip(",")
        os.environ["no_proxy"] = os.environ["NO_PROXY"]


_ensure_no_proxy()


def _em_get(path: str, params: dict, tries: int = 2) -> dict | None:
    """打 eastmoney push2 接口：多 host 轮询 + 重试（单台会间歇掉连接）。"""
    for attempt in range(tries):
        for i in range(len(_EM_HOSTS)):
            host = _EM_HOSTS[(attempt + i) % len(_EM_HOSTS)]
            try:
                r = requests.get(f"https://{host}{path}", params=params, headers=_H, timeout=12)
                if r.status_code == 200:
                    return r.json()
            except Exception:  # noqa: BLE001
                pass
        if attempt + 1 < tries:
            time.sleep(0.4)
    return None


# --- 代码 ↔ 交易所 / Yahoo ticker -------------------------------------------
def _exchange(code: str) -> str:  # A股：复刻框架 infer_exchange
    if code.startswith(("60", "688", "689", "900", "5")):
        return "SH"
    if code.startswith(("00", "30", "200", "159", "12")):
        return "SZ"
    if code.startswith(("8", "4", "920", "92")):
        return "BJ"
    return "SH" if code.startswith("6") else "SZ"


def _cn_ticker(code: str) -> str:  # 6 位 -> Yahoo（600519.SS / 000001.SZ / 8xx.BJ）
    ex = _exchange(code)
    return f"{code}.{'SS' if ex == 'SH' else ex}"


def _secid(code: str) -> str:  # A股 6 位 -> eastmoney secid（SH=1，SZ/BJ=0）
    return ("1." if _exchange(code) == "SH" else "0.") + code


def _hk_ticker(code: str) -> str:
    """eastmoney 港股 5 位码 -> Yahoo（02513 -> 2513.HK，00700 -> 0700.HK）。"""
    c = code.lstrip("0") or "0"
    if len(c) <= 4:
        c = c.zfill(4)
    return f"{c}.HK"


def _to_ticker(code: str, market: str) -> str:
    if market == CN:
        return _cn_ticker(code)
    if market == HK:
        return _hk_ticker(code)
    return code  # 美股：eastmoney 代码即 Yahoo ticker


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


# --- eastmoney clist 健壮分页（港股/美股全表）-------------------------------
def _clist_page(pn: int, fs: str, pz: int = 100) -> tuple[int, list]:
    params = {"pn": pn, "pz": pz, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f12",
              "fs": fs, "fields": "f12,f14"}
    d = _em_get("/api/qt/clist/get", params, tries=3)
    data = (d or {}).get("data") or {}
    rows = [{"code": q["f12"], "name": str(q.get("f14") or "").strip()}
            for q in (data.get("diff") or []) if q.get("f12")]
    return int(data.get("total") or 0), rows


def _em_clist_all(fs: str, conc: int = 5) -> list[dict]:
    """全市场分页：并发 conc + 多 host，缺失页重试多轮（eastmoney 偶发掉连接）。"""
    total, first = _clist_page(1, fs)
    if not total:
        return first, bool(first)
    pages = max(1, math.ceil(total / 100))
    got = {1: first}
    for _round in range(5):
        todo = [p for p in range(2, pages + 1) if p not in got]
        if not todo:
            break
        with ThreadPoolExecutor(max_workers=conc) as ex:
            for pn, rows in zip(todo, ex.map(lambda p: _clist_page(p, fs)[1], todo), strict=False):
                if rows:
                    got[pn] = rows
        if [p for p in range(2, pages + 1) if p not in got]:
            time.sleep(0.8)
    merged = []
    for pn in sorted(got):
        merged.extend(got[pn])
    complete = (len(got) == pages)  # 1..pages 每页都拿到（空页被丢弃，故页数即完整性）
    return merged, complete


# --- 名称全表（按天缓存）----------------------------------------------------
def _read_list(market: str) -> dict:
    try:
        return json.load(open(_LIST_PATH[market], encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_list(market: str, lst: list) -> None:
    try:
        os.makedirs(_HOME, exist_ok=True)
        json.dump({"day": time.strftime("%Y%m%d"), "list": lst},
                  open(_LIST_PATH[market], "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def _refresh_cn_list(timeout: int = 90) -> None:
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run([sys.executable, os.path.join(here, "cn_list_worker.py"), _LIST_PATH[CN]],
                       cwd=here, timeout=timeout, capture_output=True)
    except Exception:  # noqa: BLE001
        pass


def instrument_list(market: str, force: bool = False) -> list[dict]:
    """市场全表 [{code,name}]，按天缓存。CN 走子进程(akshare)，HK/US 走 eastmoney 分页。"""
    if market not in _LIST_PATH:
        return []
    today = time.strftime("%Y%m%d")
    cached = _read_list(market)
    if not force and cached.get("day") == today and cached.get("list"):
        return cached["list"]
    if market == CN:
        _refresh_cn_list()
    else:
        lst, complete = _em_clist_all(_FS[market])
        if lst and complete:
            _write_list(market, lst)  # 只缓存完整结果
        elif lst:
            return lst  # 残缺：本次可用但不写缓存，下次再拉全（避免一整天用残表）
    fresh = _read_list(market)
    if fresh.get("list"):
        return fresh["list"]
    return cached.get("list", [])  # 刷新失败 -> 旧缓存（即便过期）


def cn_stock_list(force: bool = False) -> list[dict]:  # 兼容旧调用
    return instrument_list(CN, force)


def _list_cached_only(market: str) -> list[dict]:
    """只读缓存、绝不触发拉取（resolve_name 用，避免阻塞）。"""
    return _read_list(market).get("list", [])


def _cn_name_by_code() -> dict:
    return {e["code"]: e["name"] for e in _list_cached_only(CN)}


def _ticker_name_map(market: str) -> dict:
    return {_to_ticker(e["code"], market).upper(): e["name"] for e in _list_cached_only(market)}


# --- 各市场热门精选（搜索兜底 + 行情默认列表）-------------------------------
CURATED = {
    US: [("NVDA", "英伟达 NVIDIA"), ("AAPL", "苹果 Apple"), ("TSLA", "特斯拉 Tesla"),
         ("MSFT", "微软 Microsoft"), ("AMZN", "亚马逊 Amazon"), ("GOOGL", "谷歌 Alphabet"),
         ("META", "Meta"), ("AMD", "AMD"), ("PLTR", "Palantir"), ("SPY", "标普500 ETF")],
    HK: [("0700.HK", "腾讯控股"), ("9988.HK", "阿里巴巴"), ("3690.HK", "美团"),
         ("1810.HK", "小米集团"), ("9618.HK", "京东集团"), ("9888.HK", "百度集团"),
         ("0939.HK", "建设银行"), ("2513.HK", "智谱")],
    CRYPTO: [("BTC-USD", "比特币 Bitcoin"), ("ETH-USD", "以太坊 Ethereum"), ("SOL-USD", "Solana"),
             ("BNB-USD", "BNB"), ("XRP-USD", "瑞波 XRP"), ("DOGE-USD", "狗狗币 Dogecoin"),
             ("ADA-USD", "Cardano"), ("AVAX-USD", "Avalanche")],
}


def default_tickers(market: str) -> list[str]:
    if market == CN:
        return ["600519.SS", "000001.SZ", "300750.SZ", "601318.SS", "000858.SZ", "002594.SZ"]
    return [t for t, _ in CURATED.get(market, [])]


def resolve_name(ticker: str, market: str | None = None) -> str:
    """ticker -> 展示名称（查对应市场缓存表 / 精选；查不到回退 ticker）。只读缓存不拉取。"""
    t = (ticker or "").strip()
    if not t:
        return t
    code = cn_code(t)
    if code and market in (None, CN):
        nm = _cn_name_by_code().get(code)
        if nm:
            return nm
    for mk in ([market] if market in (HK, US) else [HK, US]):
        nm = _ticker_name_map(mk).get(t.upper())
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
    if market in (CN, HK, US):
        lst = instrument_list(market)  # 缺则拉取（首次较慢；调用方用 spinner 包裹）
        ql, qu = q.lower(), q.upper()
        code_hits, name_hits = [], []
        for e in lst:
            code, name = e["code"], e["name"]
            tk = _to_ticker(code, market)
            cu, tu = code.upper(), tk.upper()
            if qu in cu or qu in tu:
                # 代码/ticker 命中：精确匹配优先（忽略港股前导零，如 700/0700/00700 等价），
                # 再按去零后代码长度（短=正股，长多为窝轮/牛熊证）。
                tdig = tu.replace(".HK", "")
                qn = qu.lstrip("0")
                exact = qu in (cu, tu) or (qn != "" and (qn == cu.lstrip("0") or qn == tdig.lstrip("0")))
                code_hits.append((0 if exact else 1, len(cu.lstrip("0")) or len(cu), name, tk))
            elif ql in name.lower():
                # 名称命中：前缀匹配优先，再按名称长度（短名常是正股，如「苹果」> 苹果概念ETF）
                name_hits.append((0 if name.lower().startswith(ql) else 1, len(name), name, tk))
        code_hits.sort()
        name_hits.sort()
        out = (code_hits + name_hits)[:n]
        return [{"name": nm, "ticker": tk, "market": market} for _, _, nm, tk in out]
    # 虚拟币：精选里按名称/代码匹配
    ql = q.lower()
    res = [{"name": nm, "ticker": tk, "market": market}
           for tk, nm in CURATED.get(market, [])
           if ql in nm.lower() or ql in tk.lower()]
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


def _yf_one(t: str):
    import yfinance as yf
    try:
        fi = yf.Ticker(t).fast_info
        last = fi.get("lastPrice") if hasattr(fi, "get") else fi.last_price
        prev = fi.get("previousClose") if hasattr(fi, "get") else fi.previous_close
        if last:
            chg = (last / prev - 1) * 100 if prev else 0.0
            return t, {"price": float(last), "chg": float(chg), "name": None}
    except Exception:  # noqa: BLE001
        pass
    return t, None


def _yf_quotes(tickers: list[str]) -> dict:
    """并行取 yfinance 报价 + 整体超时（避免某只卡死整页）。"""
    if not tickers:
        return {}
    out = {}
    with ThreadPoolExecutor(max_workers=min(8, len(tickers))) as ex:
        futs = [ex.submit(_yf_one, t) for t in tickers]
        try:
            for f in as_completed(futs, timeout=25):
                t, v = f.result()
                if v:
                    out[t] = v
        except Exception:  # noqa: BLE001 (整体超时 -> 用已拿到的)
            pass
    return out


def quotes(items: list[dict]) -> dict:
    """items: [{ticker, market}] -> {ticker: {price, chg, name}}。
    A股走 eastmoney（实时，失败回退 yfinance），其余走 yfinance。"""
    # 尊重显式 market；只有 market 未给时才靠 cn_code 兜底判 A股，
    # 否则 6 位数字的非 A股代码会被误路由到 eastmoney 拿到错/空价。
    cn = [it["ticker"] for it in items
          if it.get("market") == CN
          or (it.get("market") in (None, "") and cn_code(it.get("ticker", "")))]
    other = [it["ticker"] for it in items if it["ticker"] not in cn]
    out = {}
    out.update(_cn_quotes(cn))
    miss = [t for t in cn if t not in out]
    out.update(_yf_quotes(other + miss))
    return out


def kline(ticker: str, period: str = "3mo", interval: str = "1d") -> list[dict]:
    """K 线 OHLCV（yfinance，覆盖 A股/港股/美股/币，支持日内）。
    period/interval 同 yfinance（如 ('1d','5m') 分时、('3mo','1d') 日线）。
    返回 [{dt, date, open, high, low, close, volume}]，按时间升序；dt 为展示标签。"""
    t = (ticker or "").strip()
    if not t:
        return []
    import yfinance as yf
    try:
        h = yf.Ticker(t).history(period=period, interval=interval, auto_adjust=False)
    except Exception:  # noqa: BLE001
        return []
    intraday = interval.endswith(("m", "h"))
    rows = []
    for idx, r in h.iterrows():
        try:
            o, hi, lo, c = float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"])
            if o != o or c != c:  # NaN
                continue
            rows.append({"dt": idx.strftime("%m-%d %H:%M") if intraday else idx.strftime("%Y-%m-%d"),
                         "date": idx.strftime("%Y-%m-%d"), "open": o, "high": hi,
                         "low": lo, "close": c, "volume": float(r.get("Volume", 0) or 0)})
        except Exception:  # noqa: BLE001
            pass
    return rows


if __name__ == "__main__":  # 自测
    sys.stdout.reconfigure(encoding="utf-8")
    print("A股:", len(instrument_list(CN)), "| 港股:", len(instrument_list(HK)),
          "| 美股:", len(instrument_list(US)))
    print("搜 智谱(HK) ->", search("智谱", HK)[:3])
    print("搜 2513(HK) ->", search("2513", HK)[:3])
    print("搜 苹果(US) ->", search("苹果", US)[:3])
    print("名称 2513.HK ->", resolve_name("2513.HK", HK))
    print("名称 600519.SS ->", resolve_name("600519.SS", CN))
    print("报价 智谱/腾讯/茅台:", quotes([{"ticker": "2513.HK", "market": HK},
                                          {"ticker": "0700.HK", "market": HK},
                                          {"ticker": "600519.SS", "market": CN}]))
