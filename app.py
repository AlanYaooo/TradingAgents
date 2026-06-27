"""
千机智能体 (TradingAgents) 可视化界面 (Streamlit)
=================================================================
多智能体交易分析的可视化前端：选市场/标的 → 配 LLM/API key →
逐 agent 实时看分析、辩论、协作与每步输出 → 最终决策报告。
支持深色 / 浅色主题切换。

运行：  streamlit run app.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from urllib.parse import quote

import streamlit as st

import ui_data  # 名称解析 / 名称搜索 / 实时报价（不依赖 akshare，避免 py_mini_racer 崩）

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:  # 没装 plotly 时 K 线退化为折线
    go = None
    make_subplots = None

APP_NAME = "千机智能体"
_HERE = os.path.dirname(os.path.abspath(__file__))

st.set_page_config(
    page_title=f"{APP_NAME} · 你的 AI 股市助手",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    from tradingagents.default_config import DEFAULT_CONFIG  # noqa: F401
    from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: F401
    from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV
except Exception as exc:  # noqa: BLE001
    st.error(f"框架导入失败：{exc}\n\n请在仓库目录用 venv 运行： `.\\.venv\\Scripts\\streamlit run app.py`")
    st.stop()

try:
    import markdown as _md  # md -> html for the downloadable report
except ImportError:
    _md = None


# ===========================================================================
# 主题
# ===========================================================================
# 设计系统：BI/数据密集型 dashboard，深色 slate 为主，单一品牌强调色，克制装饰。
# 配色收敛、对比达 WCAG AA；红涨绿跌（面向中国市场）。
THEMES = {
    "dark": dict(
        bg="#0A0E17", panel="#111722", panel2="#19212F", soft="#0E141F",
        border="#232C3B", border_soft="#1A222F",
        text="#E9EEF7", muted="#9CACC4", faint="#62718A",
        brand="#7C6CFF", brand2="#5B8DEF", cyan="#2DD4BF",
        green="#27C281", red="#F6526B", amber="#E8973A",
        sidebar="#0C121C",
        app_bg="radial-gradient(900px 480px at 88% -10%, rgba(124,108,255,.10), transparent 60%), #0A0E17",
        hero_grad="linear-gradient(120deg,#141b2a,#10151f)",
        dec_grad="linear-gradient(120deg,#141b2a,#10151f)",
        h1_grad="linear-gradient(90deg,#fff,#cdd6ff)",
        shadow="0 8px 24px -16px rgba(0,0,0,.55)",
    ),
    "light": dict(
        bg="#F4F6FB", panel="#FFFFFF", panel2="#F0F3F9", soft="#F8FAFD",
        border="#E3E8F0", border_soft="#EDF0F6",
        text="#141B2D", muted="#566075", faint="#6A7589",  # AA 对比
        brand="#6A5BFF", brand2="#3F7DF0", cyan="#0E9FB4",
        green="#15A66E", red="#E03A56", amber="#C2790C",
        sidebar="#F0F3F9",
        app_bg="radial-gradient(900px 480px at 88% -10%, rgba(124,108,255,.06), transparent 60%), #F4F6FB",
        hero_grad="linear-gradient(120deg,#f3f6fd,#fbfcff)",
        dec_grad="linear-gradient(120deg,#f3f6fd,#fbfcff)",
        h1_grad="linear-gradient(90deg,#1a2240,#3a4fa6)",
        shadow="0 6px 20px -14px rgba(40,55,95,.28)",
    ),
}

STATIC_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');
html, body, .stApp, [class*="css"]{ font-family:'Inter','Segoe UI',sans-serif !important; }
code, pre, [data-testid="stMetricValue"]{ font-family:'JetBrains Mono',monospace !important; }
.stApp{ background:var(--app-bg); color:var(--text); }
.stApp, .stMarkdown, [data-testid="stMarkdownContainer"], p, li, span, label, td, th{ color:var(--text); }
[data-testid="stHeader"]{ background:transparent; }
/* hide the Deploy/menu actions but KEEP stToolbar — the sidebar-expand
   button lives inside it, so display:none on stToolbar breaks reopening. */
#MainMenu, footer, [data-testid="stToolbarActions"], [data-testid="stAppDeployButton"], [data-testid="stDecoration"]{ display:none !important; }
[data-testid="stExpandSidebarButton"]{ display:flex !important; visibility:visible !important; }
[data-testid="stExpandSidebarButton"] button{ color:var(--text) !important; }
.block-container{ padding-top:1.5rem; padding-bottom:3rem; max-width:1300px; }

section[data-testid="stSidebar"]{ background:var(--sidebar); border-right:1px solid var(--border-soft); }
section[data-testid="stSidebar"] .block-container{ padding-top:1.1rem; }
section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3{
  font-size:.78rem !important; letter-spacing:.10em; text-transform:uppercase; color:var(--faint) !important; font-weight:700; margin:.2rem 0 .4rem; }

[data-testid="stTextInput"] input, [data-testid="stDateInput"] input, .stSelectbox div[data-baseweb="select"]>div{
  background:var(--panel2) !important; border:1px solid var(--border) !important; border-radius:10px !important; color:var(--text) !important; }
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"]>div{ background:var(--panel2) !important; border-color:var(--border) !important; }
[data-baseweb="input"] input{ background:transparent !important; color:var(--text) !important; }
[data-baseweb="popover"] [role="listbox"], [data-baseweb="menu"], [data-baseweb="calendar"]{ background:var(--panel) !important; color:var(--text) !important; }
/* 用后代选择器(.stButton button)而非直接子(>)：带 help= 的按钮会被包进 stTooltipHoverTarget，
   不是 .stButton 直接子元素，否则主题色匹配不到、浅色下变黑（如主题切换/自选项按钮） */
.stButton button{ border-radius:10px; font-weight:600; border:1px solid var(--border); background:var(--panel2) !important; color:var(--text) !important; transition:.15s; }
.stButton button:hover{ border-color:var(--brand) !important; }
.stButton button[kind="primary"]{ background:linear-gradient(120deg,var(--brand),var(--brand2)) !important; border:none; color:#fff !important;
  box-shadow:0 8px 24px -8px rgba(124,108,255,.6); font-weight:700; letter-spacing:.02em; }
.stButton>button[kind="primary"]:hover{ filter:brightness(1.08); }
div[data-testid="stVerticalBlockBorderWrapper"]{ background:var(--panel) !important; border-color:var(--border) !important; border-radius:14px !important; box-shadow:var(--shadow); }
[data-testid="stExpander"]{ border:1px solid var(--border) !important; border-radius:12px !important; background:var(--soft) !important; }
[data-testid="stExpander"] summary{ color:var(--text) !important; }

/* 纤细应用头栏（取代大 hero，更像专业终端/产品） */
.apphead{ display:flex; align-items:center; gap:12px; border:1px solid var(--border); border-radius:14px;
  padding:12px 18px; margin-bottom:14px; background:var(--panel); box-shadow:var(--shadow); }
.apphead .logo{ width:38px; height:38px; border-radius:11px; display:flex; align-items:center; justify-content:center; flex:0 0 auto;
  background:linear-gradient(135deg,var(--brand),var(--brand2)); box-shadow:0 6px 16px -9px rgba(124,108,255,.8); }
.apphead .bn{ font-size:1.16rem; font-weight:800; color:var(--text); letter-spacing:.01em; line-height:1.15; }
.apphead .bn .en{ font-size:.66rem; color:var(--faint); font-weight:700; letter-spacing:.16em; margin-left:7px; vertical-align:middle; }
.apphead .bt{ color:var(--muted); font-size:.79rem; margin-top:2px; }

.card{ background:var(--panel); border:1px solid var(--border); border-radius:16px; padding:16px 20px; margin-bottom:14px; box-shadow:var(--shadow); }
.card.soft{ background:var(--soft); }
.sec-title{ font-size:.82rem; font-weight:700; letter-spacing:.10em; text-transform:uppercase; color:var(--faint); margin:18px 2px 8px; }

.stepper{ display:flex; align-items:flex-start; gap:0; margin:6px 0 4px; }
.step{ flex:1; text-align:center; position:relative; }
.step .dot{ width:40px; height:40px; border-radius:50%; margin:0 auto 8px; display:flex; align-items:center; justify-content:center; font-size:1.08rem; font-weight:700; border:1.5px solid var(--border); background:var(--panel2); color:var(--faint); transition:.2s; position:relative; z-index:1; }
.step .lbl{ font-size:.78rem; color:var(--muted); font-weight:600; }
.step .sub{ font-size:.68rem; color:var(--faint); }
.step::before{ content:""; position:absolute; top:19px; left:-50%; width:100%; height:2px; background:var(--border); z-index:0; }
.step:first-child::before{ display:none; }
.step.done .dot{ background:linear-gradient(135deg,#1d8f4e,var(--green)); border-color:transparent; color:#fff; box-shadow:0 6px 16px -6px rgba(34,197,94,.6); }
.step.done .lbl{ color:var(--green); }
.step.active .dot{ background:linear-gradient(135deg,var(--brand),var(--brand2)); border-color:transparent; color:#fff; box-shadow:0 0 0 5px rgba(124,108,255,.18); animation:pulse 1.6s infinite; }
.step.active .lbl{ color:var(--text); }
@keyframes pulse{ 0%,100%{box-shadow:0 0 0 5px rgba(124,108,255,.18);} 50%{box-shadow:0 0 0 9px rgba(124,108,255,.04);} }

.pill{ display:inline-flex; align-items:center; gap:5px; padding:3px 10px; border-radius:999px; font-size:.72rem; font-weight:700; }
.pill.run{ background:rgba(246,167,35,.16); color:var(--amber); border:1px solid rgba(246,167,35,.32); animation:softpulse 1.5s ease-in-out infinite; }
@keyframes softpulse{ 0%,100%{opacity:1;} 50%{opacity:.55;} }
.pill.done{ background:rgba(34,197,94,.16); color:var(--green); border:1px solid rgba(34,197,94,.32); }
.pill.wait{ background:rgba(127,142,170,.14); color:var(--faint); border:1px solid var(--border-soft); }

.ahead{ display:flex; align-items:center; justify-content:space-between; }
.ahead .who{ display:flex; align-items:center; gap:10px; font-weight:700; font-size:.98rem; color:var(--text); }
.ahead .ic{ width:34px; height:34px; border-radius:10px; display:flex; align-items:center; justify-content:center; font-size:1.05rem; background:rgba(124,108,255,.12); border:1px solid var(--border); }

.btitle{ font-weight:700; font-size:.9rem; margin:2px 0 6px; display:flex; gap:7px; align-items:center; color:var(--text); }

.decision{ border:1px solid var(--border); border-radius:18px; padding:20px 26px; background:var(--dec-grad); display:flex; align-items:center; gap:22px; flex-wrap:wrap; box-shadow:var(--shadow); }
.dbadge{ font-size:2.0rem; font-weight:800; letter-spacing:.02em; padding:10px 26px; border-radius:14px; }
.dbadge.buy{ color:var(--green); background:rgba(34,197,94,.13); border:1px solid rgba(34,197,94,.4); }
.dbadge.sell{ color:var(--red); background:rgba(240,80,110,.13); border:1px solid rgba(240,80,110,.4); }
.dbadge.hold{ color:var(--amber); background:rgba(246,167,35,.13); border:1px solid rgba(246,167,35,.4); }

.kpi{ display:flex; gap:12px; flex-wrap:wrap; }
.chip{ background:var(--panel); border:1px solid var(--border); border-radius:13px; padding:11px 18px; min-width:118px; box-shadow:var(--shadow); }
.chip .k{ font-size:.7rem; color:var(--faint); text-transform:uppercase; letter-spacing:.07em; font-weight:600; }
.chip .v{ font-size:1.25rem; font-weight:800; color:var(--text); margin-top:2px; font-family:'JetBrains Mono',monospace; }

hr{ border-color:var(--border-soft); }
::-webkit-scrollbar{ width:9px; height:9px; } ::-webkit-scrollbar-thumb{ background:var(--border); border-radius:6px; } ::-webkit-scrollbar-track{ background:transparent; }

/* 分段控件 / pills（市场/导航/分析师）：未选中段默认用 config 的深色 secondaryBackgroundColor，
   浅色下会变黑框 —— 用主题变量覆盖（按 kind 是否以 Active 结尾区分选中态，同时覆盖 segmented 与 pills）。
   并收紧左右内边距，让 4 个市场短标签能放进一行。 */
[data-testid="stButtonGroup"] button:not([kind$="Active"]){
  background:var(--panel2) !important; color:var(--text) !important; border:1px solid var(--border) !important; }
[data-testid="stButtonGroup"] button:not([kind$="Active"]):hover{ border-color:var(--brand) !important; }
[data-testid="stButtonGroup"] button[kind$="Active"]{
  background:rgba(124,108,255,.15) !important; color:var(--brand) !important; border:1px solid var(--brand) !important; }
[data-testid="stButtonGroup"] button{ padding-left:10px !important; padding-right:10px !important; }

/* 侧栏分区：标题间加分隔线 + 更大间距，层级更清晰 */
section[data-testid="stSidebar"] h3{ margin-top:1.3rem !important; padding-top:.75rem; border-top:1px solid var(--border-soft); }
/* 流程页：决策卡更克制、徽章规整 */
.decision{ background:var(--soft); border-radius:14px; }
.dbadge{ font-size:1.65rem; padding:9px 24px; }
/* 阶段卡标题图标统一尺寸 */
.ahead .ic{ width:32px; height:32px; font-size:1rem; background:rgba(124,108,255,.12); }
.sec-title{ margin:16px 2px 8px; }

/* 行情表：BI 数据表行（hover 高亮、对齐、等宽数字） */
.qhead, .qrow{ display:grid; grid-template-columns:1fr 112px 120px; align-items:center; gap:8px; }
.qhead{ color:var(--faint); font-size:.72rem; font-weight:700; letter-spacing:.05em; padding:2px 10px 7px; }
.qhead span:nth-child(2), .qhead span:nth-child(3){ text-align:right; }
.qrow{ padding:8px 10px; border-radius:9px; border-bottom:1px solid var(--border-soft); transition:background .12s; }
.qrow:hover{ background:var(--panel2); }
.qname{ font-weight:700; color:var(--text); display:flex; align-items:center; gap:7px; min-width:0; }
.qname .qcode{ color:var(--faint); font-size:.73rem; font-family:'JetBrains Mono',monospace; font-weight:500; }
.qname .qstar{ color:var(--amber); flex:0 0 auto; }
.qprice, .qchg{ text-align:right; font-family:'JetBrains Mono',monospace; font-weight:700; font-variant-numeric:tabular-nums; }
.qprice{ font-size:1.0rem; color:var(--text); }

/* —— UI/UX 质量基线（ui-ux-pro-max：accessibility / 数字对齐 / 动效） —— */
/* 数字等宽对齐：价格/涨跌/KPI 列不再左右跳动（number-tabular） */
code, pre, [data-testid="stMetricValue"], .chip .v, .dbadge,
[style*="JetBrains Mono"]{ font-variant-numeric:tabular-nums; font-feature-settings:"tnum"; }
/* 可见焦点态：键盘用户可达（focus-states, HIGH） */
.stButton>button:focus-visible, a:focus-visible,
[data-baseweb="input"] input:focus-visible, [data-baseweb="select"] > div:focus-within,
[data-testid="stDateInputField"]:focus-visible{
  outline:2px solid var(--brand) !important; outline-offset:2px; }
a{ cursor:pointer; }
/* 尊重系统"减弱动效"：关闭脉冲/过渡，避免眩晕（reduced-motion, HIGH a11y） */
@media (prefers-reduced-motion: reduce){
  *, *::before, *::after{ animation-duration:.001ms !important; animation-iteration-count:1 !important;
    transition-duration:.001ms !important; scroll-behavior:auto !important; }
  .step.active .dot, .pill.run{ animation:none !important; }
}
"""


def inject_theme(theme: str):
    t = THEMES.get(theme, THEMES["dark"])
    root = (":root{"
            f"--bg:{t['bg']};--panel:{t['panel']};--panel2:{t['panel2']};--soft:{t['soft']};"
            f"--border:{t['border']};--border-soft:{t['border_soft']};"
            f"--text:{t['text']};--muted:{t['muted']};--faint:{t['faint']};"
            f"--brand:{t['brand']};--brand2:{t['brand2']};--cyan:{t['cyan']};"
            f"--green:{t['green']};--red:{t['red']};--amber:{t['amber']};--sidebar:{t['sidebar']};"
            f"--app-bg:{t['app_bg']};--hero-grad:{t['hero_grad']};--dec-grad:{t['dec_grad']};"
            f"--h1-grad:{t['h1_grad']};--shadow:{t['shadow']};}}")
    st.markdown(f"<style>{root}{STATIC_CSS}</style>", unsafe_allow_html=True)


theme = st.session_state.get("theme", "dark")
inject_theme(theme)


# ===========================================================================
# 配置数据
# ===========================================================================
MARKETS = {
    "🇨🇳 A股": dict(asset_type="stock", examples=["600519.SS", "000001.SZ", "300750.SZ"],
                   data="akshare · 行情/财报/新闻/宏观/千股千评情绪"),
    "🇺🇸 美股": dict(asset_type="stock", examples=["NVDA", "AAPL", "TSLA"],
                   data="yfinance · +Reddit/StockTwits/Polymarket/FRED"),
    "🇭🇰 港股": dict(asset_type="stock", examples=["0700.HK", "9988.HK", "3690.HK"],
                   data="yfinance"),
    "₿ 虚拟币": dict(asset_type="crypto", examples=["BTC-USD", "ETH-USD", "SOL-USD"],
                   data="yfinance · +Reddit/StockTwits/Polymarket 事件盘"),
}
NAV_MARKET = "📈 行情"
NAV_ANALYSIS = "🔬 智能分析"
NAV_HISTORY = "📜 历史"


def _mkt_short(m: str) -> str:
    # 市场分段控件只显示短名（去掉旗帜前缀，Windows 下旗帜会显示成 "CN" 占宽、4 个挤不下一行）
    return m.split(" ", 1)[1] if " " in m else m
PROVIDERS = {
    "Anthropic / Claude（含中转站）": ("anthropic", True),
    "OpenAI 兼容中转站": ("openai_compatible", True),
    "OpenAI": ("openai", False),
    "DeepSeek": ("deepseek", False),
    "Kimi (Moonshot)": ("kimi", False),
    "Google Gemini": ("google", False),
    "通义千问 Qwen": ("qwen", False),
}
# 各 provider 的可选模型（下拉选择，不手填）。中转站(anthropic/openai_compatible)
# 走统一中转、服务全部家族，故给全集。
_GPT = ["gpt-5.5", "gpt-5.4", "gpt-5.3-codex"]
_CLAUDE = ["claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"]
_DS = ["deepseek-v4-pro", "deepseek-v4-flash"]
_KIMI = ["kimi-k2.7-code", "kimi-k2.6"]
_RELAY = _GPT + _CLAUDE  # 中转站只服务 gpt 和 claude（deepseek/kimi 走各自官方直连）
PROVIDER_MODELS = {
    "anthropic": _RELAY,
    "openai_compatible": _RELAY,
    "openai": _GPT,
    "deepseek": _DS,
    "kimi": _KIMI,
    "google": ["gemini-1.5-pro", "gemini-1.5-flash"],
    "qwen": ["qwen-plus", "qwen-turbo"],
}
# 每个 provider 的 (deep 默认, quick 默认)
PROVIDER_DEFAULT = {
    "anthropic": ("gpt-5.5", "gpt-5.4"),
    "openai_compatible": ("gpt-5.5", "gpt-5.4"),
    "openai": ("gpt-5.5", "gpt-5.4"),
    "deepseek": ("deepseek-v4-pro", "deepseek-v4-flash"),
    "kimi": ("kimi-k2.6", "kimi-k2.6"),
    "google": ("gemini-1.5-pro", "gemini-1.5-flash"),
    "qwen": ("qwen-plus", "qwen-turbo"),
}
ANALYSTS = [
    ("市场 / 技术分析师", "📊", "market_report", "market"),
    ("情绪分析师", "💬", "sentiment_report", "social"),
    ("新闻分析师", "📰", "news_report", "news"),
    ("基本面分析师", "📑", "fundamentals_report", "fundamentals"),
]

# ⭐ 自选库：持久化到 ~/.tradingagents/watchlist.json（重启/重开都在）
WATCHLIST_PATH = os.path.join(os.path.expanduser("~"), ".tradingagents", "watchlist.json")


def load_watchlist() -> list:
    try:
        with open(WATCHLIST_PATH, encoding="utf-8") as f:
            wl = json.load(f)
        return [e for e in wl if isinstance(e, dict) and e.get("ticker")]
    except Exception:  # noqa: BLE001
        return []


def save_watchlist(wl: list) -> None:
    try:
        os.makedirs(os.path.dirname(WATCHLIST_PATH), exist_ok=True)
        with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
            json.dump(wl, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass


def add_to_watchlist(ticker: str, market: str) -> bool:
    ticker = (ticker or "").strip()
    if not ticker:
        return False
    wl = load_watchlist()
    if any(e["ticker"] == ticker and e.get("market") == market for e in wl):
        return False
    wl.append({"ticker": ticker, "market": market, "name": ui_data.resolve_name(ticker, market)})
    save_watchlist(wl)
    return True


def remove_from_watchlist(ticker: str, market: str) -> None:
    save_watchlist([e for e in load_watchlist()
                    if not (e["ticker"] == ticker and e.get("market") == market)])


# 🔐 UI 设置（API key / base_url）持久化到本机 ~/.tradingagents/ui_settings.json
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".tradingagents", "ui_settings.json")


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_settings(s: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass


# 📜 历史记录：每次完成的分析存一份到 ~/.tradingagents/history/，手动删除
HISTORY_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "history")


def save_history(state: dict, cfg: dict) -> None:
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        now = datetime.now()
        hid = now.strftime("%Y%m%d_%H%M%S")
        ticker, market = cfg.get("ticker", ""), cfg.get("market", "")
        label, cls = _decision_action(state.get("final_trade_decision") or "")
        item = {"id": hid, "ts": now.strftime("%Y-%m-%d %H:%M"),
                "ticker": ticker, "name": ui_data.resolve_name(ticker, market) if ticker else ticker,
                "market": market, "date_str": cfg.get("date_str", ""),
                "decision": label, "decision_cls": cls, "state": state, "cfg": cfg}
        with open(os.path.join(HISTORY_DIR, f"{hid}.json"), "w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def load_history_index() -> list:
    keys = ("id", "ts", "ticker", "name", "market", "date_str")
    items = []
    try:
        for fn in os.listdir(HISTORY_DIR):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(HISTORY_DIR, fn), encoding="utf-8") as f:
                        d = json.load(f)
                    it = {k: d.get(k) for k in keys}
                    # 徽章每次都从决策原文重算（用修好的解析），自动纠正旧的错误徽章
                    fd = (d.get("state") or {}).get("final_trade_decision") or ""
                    if fd:
                        it["decision"], it["decision_cls"] = _decision_action(fd)
                    else:
                        it["decision"], it["decision_cls"] = d.get("decision") or "—", d.get("decision_cls") or "hold"
                    items.append(it)
                except Exception:  # noqa: BLE001
                    pass
    except FileNotFoundError:
        pass
    return sorted(items, key=lambda x: x.get("id", ""), reverse=True)


def load_history_item(hid: str) -> dict:
    try:
        with open(os.path.join(HISTORY_DIR, f"{hid}.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def delete_history(hid: str) -> None:
    try:
        os.remove(os.path.join(HISTORY_DIR, f"{hid}.json"))
    except Exception:  # noqa: BLE001
        pass


def _set_target(market: str, ticker: str) -> None:
    # 在 on_click 回调里改 widget state 才合法（内联改 widget key 会抛异常）
    if market in MARKETS:
        st.session_state["market_sel"] = market
        st.session_state["_mkt"] = market
    st.session_state["ticker"] = ticker


def _focus(market: str, ticker: str) -> None:
    """选中某标的 -> 右侧（行情页）展示其 K 线详情。"""
    _set_target(market, ticker)
    st.session_state["focus"] = {"market": market, "ticker": ticker}
    st.session_state["nav_page"] = NAV_MARKET


def _select_watchlist(market: str, ticker: str) -> None:
    _focus(market, ticker)  # 点自选股 -> 右侧出 K 线行情


def _pick_search(market: str, ticker: str) -> None:
    _focus(market, ticker)  # 搜索选中 -> 右侧出 K 线行情
    st.session_state["search_q"] = ""  # 选中后清空搜索框


def _back_to_list() -> None:
    st.session_state.pop("focus", None)  # 返回行情列表


def _start_analysis(market: str, ticker: str) -> None:
    _set_target(market, ticker)
    st.session_state["nav_page"] = NAV_ANALYSIS
    st.session_state["start_run"] = True  # 详情页「开始 AI 分析」-> 直接跑


def _view_history(hid: str) -> None:
    item = load_history_item(hid)
    if item.get("state"):
        st.session_state["result"] = {"state": item["state"], "cfg": item.get("cfg", {})}
        st.session_state["nav_page"] = NAV_ANALYSIS  # 在分析页重新展示该次结果
        st.session_state.pop("focus", None)


def _del_history(hid: str) -> None:
    delete_history(hid)


# ===========================================================================
# 渲染辅助
# ===========================================================================
def _nonempty(s) -> bool:
    return bool((s or "").strip())


def stages_of(state: dict):
    ad = sum(_nonempty(state.get(k)) for _, _, k, _ in ANALYSTS)
    ids = state.get("investment_debate_state") or {}
    rds = state.get("risk_debate_state") or {}
    return [
        ("分析师", "🔍", ad == 4, f"{ad}/4 完成"),
        ("研究辩论", "🐂", _nonempty(ids.get("judge_decision")), "多空 + 裁决"),
        ("交易员", "💼", _nonempty(state.get("trader_investment_plan")), "交易方案"),
        ("风控辩论", "🛡️", _nonempty(rds.get("judge_decision")), "三方评估"),
        ("最终决策", "🎯", _nonempty(state.get("final_trade_decision")), "买/卖/持有"),
    ]


def render_stepper(state: dict, running: bool):
    stages = stages_of(state)
    active_idx = next((i for i, s in enumerate(stages) if not s[2]), None) if running else None
    html = ['<div class="stepper">']
    for i, (label, icon, done, sub) in enumerate(stages):
        cls = "done" if done else ("active" if i == active_idx else "todo")
        dot = "✓" if done else icon
        html.append(f'<div class="step {cls}"><div class="dot">{dot}</div>'
                    f'<div class="lbl">{label}</div><div class="sub">{sub}</div></div>')
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def render_kpis(state: dict, elapsed: float | None):
    done = sum(s[2] for s in stages_of(state))
    chips = [("进度", f"{done}/5"), ("LLM 调用", f"{state.get('llm_calls', 0)}")]
    toks = (state.get("tok_in", 0) or 0) + (state.get("tok_out", 0) or 0)
    if toks:
        chips.append(("Tokens", f"{toks:,}"))
    if elapsed is not None:
        chips.append(("用时", f"{int(elapsed//60)}:{int(elapsed%60):02d}"))
    html = '<div class="kpi">' + "".join(
        f'<div class="chip"><div class="k">{k}</div><div class="v">{v}</div></div>' for k, v in chips) + "</div>"
    st.markdown(html, unsafe_allow_html=True)


def _pill(done: bool, started: bool):
    if done:
        return '<span class="pill done">✓ 完成</span>'
    if started:
        return '<span class="pill run">● 进行中</span>'
    return '<span class="pill wait">待开始</span>'


def _first_line(text: str) -> str:
    for ln in (text or "").splitlines():
        s = ln.strip().lstrip("#").strip()
        if s and not s.startswith("FINAL TRANSACTION"):
            return ("摘要 · " + s)[:120]
    return ""


def render_analysts(state: dict, ph, any_started: bool):
    with ph.container():
        cols = st.columns(2)
        for i, (name, icon, key, _) in enumerate(ANALYSTS):
            rep = (state.get(key) or "").strip()
            with cols[i % 2]:
                with st.container(border=True):
                    st.markdown(
                        f'<div class="ahead"><div class="who"><div class="ic">{icon}</div>{name}</div>'
                        f'{_pill(bool(rep), any_started)}</div>', unsafe_allow_html=True)
                    if rep:
                        with st.expander("查看完整报告"):
                            st.markdown(rep)
                        st.caption(_first_line(rep))
                    else:
                        st.caption("分析中…取数与推理进行中" if any_started else "等待开始")


def render_debate(state: dict, ph):
    ids = state.get("investment_debate_state") or {}
    bull, bear = (ids.get("bull_history") or "").strip(), (ids.get("bear_history") or "").strip()
    judge = (ids.get("judge_decision") or "").strip()
    plan = (state.get("investment_plan") or "").strip()
    with ph.container():
        if not (bull or bear or judge):
            st.caption("分析师报告完成后，多空研究员开始辩论…")
            return
        a, b = st.columns(2)
        with a:
            st.markdown('<div class="btitle">🐂 多头研究员</div>', unsafe_allow_html=True)
            with st.container(border=True):
                st.markdown(bull or "_等待发言_")
        with b:
            st.markdown('<div class="btitle">🐻 空头研究员</div>', unsafe_allow_html=True)
            with st.container(border=True):
                st.markdown(bear or "_等待发言_")
        if judge or plan:
            st.markdown('<div class="btitle">🧑‍⚖️ 研究经理裁决 → 投资计划</div>', unsafe_allow_html=True)
            with st.container(border=True):
                st.markdown(plan or judge)


def render_trader(state: dict, ph):
    plan = (state.get("trader_investment_plan") or "").strip()
    with ph.container():
        if plan:
            with st.container(border=True):
                st.markdown('<div class="btitle">💼 交易员方案</div>', unsafe_allow_html=True)
                st.markdown(plan)
        else:
            st.caption("等待研究结论…")


def render_risk(state: dict, ph):
    rds = state.get("risk_debate_state") or {}
    items = [("🔥 激进", rds.get("aggressive_history")), ("🛡️ 保守", rds.get("conservative_history")),
             ("⚖️ 中立", rds.get("neutral_history"))]
    with ph.container():
        if not any((v or "").strip() for _, v in items):
            st.caption("等待交易方案…")
            return
        cols = st.columns(3)
        for col, (title, v) in zip(cols, items, strict=False):
            with col:
                st.markdown(f'<div class="btitle">{title}</div>', unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown((v or "").strip() or "_…_")


_ACTIONS = [("BUY", "buy", ["buy", "买入", "增持", "看多", "overweight", "超配"]),
            ("SELL", "sell", ["sell", "卖出", "减持", "看空", "underweight", "低配"]),
            ("HOLD", "hold", ["hold", "持有", "观望", "中性", "neutral", "equal-weight"])]

# 裁决词 -> 动作。注意 overweight/超配=增持=BUY，underweight/低配=减仓=SELL（常被误解为相反）。
# 不收 long/short：它们是 long-term/short-term 的前缀，前缀匹配会把"短期…我们看多"判反（与
# "买入看跌期权"同类的判读 bug）。英文一律精确匹配，中文裁决词才用前缀匹配（区分度高、无歧义）。
_VERDICT = {
    "buy": "BUY", "买入": "BUY", "增持": "BUY", "加仓": "BUY", "建仓": "BUY", "满仓": "BUY",
    "看多": "BUY", "逢低": "BUY", "overweight": "BUY", "超配": "BUY", "outperform": "BUY",
    "accumulate": "BUY", "bullish": "BUY",
    "sell": "SELL", "卖出": "SELL", "减持": "SELL", "减仓": "SELL", "清仓": "SELL", "空仓": "SELL",
    "止损": "SELL", "看空": "SELL", "underweight": "SELL", "低配": "SELL", "underperform": "SELL",
    "reduce": "SELL", "bearish": "SELL",
    "hold": "HOLD", "持有": "HOLD", "观望": "HOLD", "中性": "HOLD", "neutral": "HOLD",
    "equal-weight": "HOLD", "equalweight": "HOLD", "maintain": "HOLD", "market-perform": "HOLD",
}
_CJK_VERDICT = {k: v for k, v in _VERDICT.items() if any(c >= "一" for c in k)}


def _word_to_action(w: str):
    w = (w or "").strip().strip("：:*。.，, ").lower()
    if not w:
        return None
    if w in _VERDICT:  # 整词精确（含 equal-weight 这类带连字符的）
        lab = _VERDICT[w]
        return lab, lab.lower()
    head = w.split("-", 1)[0]  # 英文带连字符取首段精确（short-term 的 'short' 已不在表中，不会误判）
    if head != w and head in _VERDICT:
        lab = _VERDICT[head]
        return lab, lab.lower()
    # 中文裁决词：在(短的)首词/标记词里做子串匹配（'建议加仓至80%'→加仓）。只扫这个短 token，
    # 不扫全文，所以不会重蹈"买入看跌期权"那种全文误命中。
    for key, lab in _CJK_VERDICT.items():
        if key in w:
            return lab, lab.lower()
    return None


def _decision_action(text: str):
    tl = (text or "").lower()
    # 1) 显式裁决最可靠：FINAL TRANSACTION PROPOSAL: X、**Rating/Action/最终判断/最终决策**: X
    for pat in (r"final\s+transaction\s+proposal\s*[:：]\s*\*{0,2}\s*([a-z一-鿿\-]+)",
                r"\*{0,2}\s*(?:rating|action|recommendation|stance|最终判断|最终决策|最终建议|投资评级|评级|结论)"
                r"\s*\*{0,2}\s*[:：]\s*\*{0,2}\s*([a-z一-鿿\-]+)"):
        m = re.search(pat, tl)
        if m:
            a = _word_to_action(m.group(1))
            if a:
                return a
    # 2) 文首裸词（很多决策第一行就是裁决词）
    m = re.match(r"\s*\*{0,2}\s*([a-z一-鿿\-]+)", tl)
    if m:
        a = _word_to_action(m.group(1))
        if a:
            return a
    # 3) 兜底：全文关键词扫描（最不可靠——会被论证里顺带提到的词如"买入看跌期权"误导，故放最后）
    for label, cls, keys in _ACTIONS:
        if any(k in tl for k in keys):
            return label, cls
    return "—", "hold"


def render_final(state: dict, ph):
    decision = (state.get("final_trade_decision") or "").strip()
    with ph.container():
        if not decision:
            st.caption("等待风控辩论…")
            return
        label, cls = _decision_action(decision)
        st.markdown(
            f'<div class="decision"><div class="dbadge {cls}">{label}</div>'
            f'<div style="flex:1;min-width:200px"><div style="color:var(--muted);font-size:.85rem">'
            f'投资组合经理 · 最终交易决策</div></div></div>', unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown(decision)


def render_pipeline(state: dict, phs, running: bool, any_started: bool):
    render_analysts(state, phs["a"], any_started)
    render_debate(state, phs["d"])
    render_trader(state, phs["t"])
    render_risk(state, phs["r"])
    render_final(state, phs["f"])


# ===========================================================================
# 可下载的 HTML 报告（双击即可在任意浏览器打开 / 打印）
# ===========================================================================
_REPORT_CSS = """
*{box-sizing:border-box} body{margin:0;background:#eef1f6;color:#1f2733;line-height:1.7;
  font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif}
.wrap{max-width:880px;margin:24px auto;background:#fff;border-radius:14px;box-shadow:0 8px 34px rgba(30,40,80,.10);padding:38px 46px}
.hd{display:flex;align-items:center;gap:18px;border-bottom:2px solid #eef1f6;padding-bottom:20px}
.badge{color:#fff;font-weight:800;font-size:1.5rem;padding:8px 22px;border-radius:11px;letter-spacing:.04em}
h1{font-size:1.4rem;margin:0;color:#16213c} .meta{color:#8a93a5;font-size:.88rem;margin-top:5px}
section{margin:8px 0 6px}
h2{font-size:1.18rem;color:#1b2440;border-left:4px solid #6a5bff;padding-left:12px;margin:32px 0 12px}
h3{font-size:1.02rem;color:#34405c;margin:20px 0 8px}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:.9rem}
th,td{border:1px solid #e3e8f0;padding:8px 11px;text-align:left;vertical-align:top}
th{background:#f4f7fb;font-weight:700} tr:nth-child(even) td{background:#fafbfd}
code{background:#f0f2f7;padding:2px 5px;border-radius:4px;font-size:.9em}
blockquote{border-left:3px solid #d6dbe6;margin:10px 0;padding:4px 14px;color:#5a6473}
ul,ol{padding-left:22px} hr{border:none;border-top:1px solid #eef1f6;margin:20px 0}
.ft{margin-top:34px;padding-top:16px;border-top:1px solid #eef1f6;color:#9aa3b2;font-size:.82rem;text-align:center}
.flow{display:flex;align-items:stretch;flex-wrap:wrap;gap:2px;margin:18px 0 10px}
.fl-step{flex:1;min-width:88px;text-align:center;background:#f4f7fb;border:1px solid #e3e8f0;border-radius:11px;padding:11px 5px;display:flex;flex-direction:column;gap:4px;justify-content:center}
.fl-ic{font-size:1.35rem}
.fl-step .lb{font-size:.8rem;font-weight:600;color:#34405c}
.fl-arrow{display:flex;align-items:center;color:#c2cad8;font-weight:700;padding:0 2px}
.fl-final{background:#fff;border-width:2px}
.fl-final .lb{font-size:1rem;font-weight:800}
.chartbox{margin:14px 0;border:1px solid #e3e8f0;border-radius:12px;padding:12px 14px 8px;background:#fafbfd}
.chartbox .hd2{display:flex;justify-content:space-between;color:#8a93a5;font-size:.82rem;margin-bottom:4px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0 4px}
.kc{flex:1;min-width:120px;border:1px solid #e3e8f0;border-radius:11px;padding:10px 14px;background:#fafbfd}
.kc .k{font-size:.72rem;color:#8a93a5;font-weight:600}
.kc .v{font-size:1.12rem;font-weight:800;margin-top:3px;color:#1b2440}
@media print{body{background:#fff}.wrap{box-shadow:none;margin:0;max-width:100%;border-radius:0}}
"""


def _md2html(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if _md is None:
        import html
        return f"<pre>{html.escape(text)}</pre>"
    return _md.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "nl2br"])


def _flow_diagram_html(label: str, cls: str) -> str:
    """决策流程图：分析师 → 多空辩论 → 交易员 → 风控 → 组合经理 → 决策（纯 HTML/CSS）。"""
    color = {"buy": "#16a34a", "sell": "#dc2626", "hold": "#d97706"}.get(cls, "#64748b")
    stages = [("🔍", "分析师团队"), ("🐂", "多空辩论"), ("💼", "交易员"), ("🛡️", "风控辩论"), ("🧭", "组合经理")]
    html = '<div class="flow">'
    for ic, lb in stages:
        html += f'<div class="fl-step"><div class="fl-ic">{ic}</div><div class="lb">{lb}</div></div><div class="fl-arrow">▶</div>'
    html += (f'<div class="fl-step fl-final" style="border-color:{color}">'
             f'<div class="fl-ic">🎯</div><div class="lb" style="color:{color}">{label}</div></div></div>')
    return html


def _svg_candles(rows: list, w: int = 840, h: int = 230) -> str:
    """把 K 线画成内联 SVG（无需 JS，报告 HTML 里任意浏览器可显示）。红涨绿跌。"""
    rows = [r for r in (rows or []) if r][-90:]
    if len(rows) < 2:
        return ""
    pad_l, pad_r, pad_t, pad_b = 6, 54, 10, 18
    iw, ih = w - pad_l - pad_r, h - pad_t - pad_b
    lo = min(r["low"] for r in rows)
    hi = max(r["high"] for r in rows)
    rng = (hi - lo) or 1
    n = len(rows)
    cw = iw / n
    bw = max(1.4, cw * 0.62)

    def yv(v):
        return pad_t + (hi - v) / rng * ih

    up, down = "#e23a3a", "#16a34a"
    grid = []
    for frac, val in [(0.0, hi), (0.5, lo + rng / 2), (1.0, lo)]:
        gy = pad_t + frac * ih
        grid.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + iw}" y2="{gy:.1f}" stroke="#eef1f6"/>')
        grid.append(f'<text x="{pad_l + iw + 4}" y="{gy + 3:.1f}" font-size="10" fill="#8a93a5">{val:.2f}</text>')
    grid.append(f'<text x="{pad_l}" y="{h - 4}" font-size="10" fill="#8a93a5">{rows[0]["date"]}</text>')
    grid.append(f'<text x="{pad_l + iw}" y="{h - 4}" font-size="10" fill="#8a93a5" text-anchor="end">{rows[-1]["date"]}</text>')
    body = []
    for i, r in enumerate(rows):
        cx = pad_l + cw * (i + 0.5)
        col = up if r["close"] >= r["open"] else down
        body.append(f'<line x1="{cx:.1f}" y1="{yv(r["high"]):.1f}" x2="{cx:.1f}" y2="{yv(r["low"]):.1f}" stroke="{col}" stroke-width="1"/>')
        oy, cyy = yv(r["open"]), yv(r["close"])
        by, bh = min(oy, cyy), max(1.0, abs(cyy - oy))
        body.append(f'<rect x="{cx - bw / 2:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{col}"/>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" '
            f'xmlns="http://www.w3.org/2000/svg">{"".join(grid)}{"".join(body)}</svg>')


@st.cache_data(ttl=600, show_spinner=False)
def _report_kline(ticker: str) -> list:
    try:
        return ui_data.kline(ticker, "6mo", "1d")
    except Exception:  # noqa: BLE001
        return []


def build_report_html(state: dict, cfg: dict) -> str:
    label, cls = _decision_action(state.get("final_trade_decision") or "")
    badge_color = {"buy": "#16a34a", "sell": "#dc2626", "hold": "#d97706"}.get(cls, "#64748b")
    parts = []
    for name, icon, key, _ in ANALYSTS:
        rep = (state.get(key) or "").strip()
        if rep:
            parts.append(f"<section><h2>{icon} {name}</h2>{_md2html(rep)}</section>")
    ids = state.get("investment_debate_state") or {}
    bull, bear = (ids.get("bull_history") or "").strip(), (ids.get("bear_history") or "").strip()
    judge = (state.get("investment_plan") or ids.get("judge_decision") or "").strip()
    if bull or bear or judge:
        d = ""
        if bull:
            d += f"<h3>🐂 多头研究员</h3>{_md2html(bull)}"
        if bear:
            d += f"<h3>🐻 空头研究员</h3>{_md2html(bear)}"
        if judge:
            d += f"<h3>🧑‍⚖️ 研究经理 · 投资计划</h3>{_md2html(judge)}"
        parts.append(f"<section><h2>研究员辩论</h2>{d}</section>")
    tp = (state.get("trader_investment_plan") or "").strip()
    if tp:
        parts.append(f"<section><h2>💼 交易员方案</h2>{_md2html(tp)}</section>")
    rds = state.get("risk_debate_state") or {}
    risk = ""
    for t, k in [("🔥 激进", "aggressive_history"), ("🛡️ 保守", "conservative_history"), ("⚖️ 中立", "neutral_history")]:
        v = (rds.get(k) or "").strip()
        if v:
            risk += f"<h3>{t}</h3>{_md2html(v)}"
    if risk:
        parts.append(f"<section><h2>风控辩论</h2>{risk}</section>")
    fd = (state.get("final_trade_decision") or "").strip()
    if fd:
        parts.append(f"<section><h2>🎯 最终决策</h2>{_md2html(fd)}</section>")

    # 决策概览：流程图 + 价格走势图（让满屏文字之外先有"图"）
    ticker = cfg.get("ticker", "")
    name = ui_data.resolve_name(ticker, cfg.get("market", "")) if ticker else ticker
    flow = _flow_diagram_html(label, cls)
    kl = _report_kline(ticker)
    chart = ""
    if kl:
        last, first = kl[-1]["close"], kl[0]["close"]
        chg = (last / first - 1) * 100 if first else 0
        cc = "#e23a3a" if chg >= 0 else "#16a34a"  # 红涨绿跌
        chart = (f'<div class="chartbox"><div class="hd2">'
                 f'<span>📈 {name or ticker} · 近 6 月走势（日线 · 红涨绿跌）</span>'
                 f'<span>最新 <b>{last:.2f}</b> · 区间 <b style="color:{cc}">{chg:+.2f}%</b></span>'
                 f'</div>{_svg_candles(kl)}</div>')
    # KPI 卡片：决策 / 现价·区间 / 目标价 / 时间跨度（从决策文本提取），让报告可一眼扫读
    fd_text = state.get("final_trade_decision") or ""

    def _grab(pats):
        for p in pats:
            m = re.search(p, fd_text, re.I)
            if m:
                return m.group(1).strip()
        return ""

    target = _grab([r"price\s*target[*\s]*[:：][*\s]*([0-9][0-9.,]*)",
                    r"目标价[位]?[*\s]*[:：]?[*\s]*([0-9][0-9.,]*)"])
    horizon = _grab([r"time\s*horizon[*\s]*[:：][*\s]*([^\n*]{1,16})",
                     r"时间(?:跨度|区间|周期|框架)[*\s]*[:：]?[*\s]*([^\n*]{1,16})"])
    dcolor = {"buy": "#16a34a", "sell": "#dc2626", "hold": "#d97706"}.get(cls, "#64748b")
    cards = f'<div class="cards"><div class="kc"><div class="k">决策</div>' \
            f'<div class="v" style="color:{dcolor}">{label}</div></div>'
    if kl:
        cards += (f'<div class="kc"><div class="k">最新价 · 区间</div><div class="v">{last:.2f} '
                  f'<span style="font-size:.82rem;color:{cc}">{chg:+.1f}%</span></div></div>')
    if target:
        cards += f'<div class="kc"><div class="k">目标价</div><div class="v">{target}</div></div>'
    if horizon:
        cards += f'<div class="kc"><div class="k">时间跨度</div><div class="v" style="font-size:.95rem">{horizon}</div></div>'
    cards += "</div>"
    intro = f'<h2 style="margin-top:20px">决策概览</h2>{cards}{flow}{chart}'

    return (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{cfg.get('ticker','')} 分析报告</title><style>{_REPORT_CSS}</style></head>"
        '<body><div class="wrap"><div class="hd">'
        f'<div class="badge" style="background:{badge_color}">{label}</div>'
        f"<div><h1>{APP_NAME} · {cfg.get('ticker','')} 分析报告</h1>"
        f'<div class="meta">分析日期 {cfg.get("date_str","")} · '
        f"生成于 {datetime.now():%Y-%m-%d %H:%M}</div></div></div>"
        + intro
        + "".join(parts)
        + f'<div class="ft">由 {APP_NAME}（TradingAgents 多智能体）生成 · 仅供研究，不构成投资建议</div>'
        "</div></body></html>"
    )


def _report_fname(cfg: dict) -> str:
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", str(cfg.get("ticker", "report"))) or "report"
    return f"{safe}_{cfg.get('date_str', '')}_report.html"


_STATIC_REPORTS = os.path.join(_HERE, "static", "reports")


def _publish_report(html_str: str, fname: str) -> str:
    """把报告写进 Streamlit 静态目录，返回真实同源 URL。
    这是最稳的下载/查看方式：真实文件 + 真实 URL，没有 JS / iframe / data: / blob，
    Chrome 一定按 URL 文件名命名，也能直接在新标签打开（彻底避开 UUID 问题）。"""
    try:
        os.makedirs(_STATIC_REPORTS, exist_ok=True)
        # 顺手清掉 7 天前的旧报告，避免无限堆积
        cutoff = time.time() - 7 * 86400
        for old in os.listdir(_STATIC_REPORTS):
            p = os.path.join(_STATIC_REPORTS, old)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except Exception:  # noqa: BLE001
                pass
        with open(os.path.join(_STATIC_REPORTS, fname), "w", encoding="utf-8") as f:
            f.write(html_str)
        return f"/app/static/reports/{quote(fname)}"
    except Exception:  # noqa: BLE001
        return ""


def render_report_download(state: dict, cfg: dict) -> None:
    fname = _report_fname(cfg)
    url = _publish_report(build_report_html(state, cfg), fname)
    if not url:
        st.warning("报告生成失败，请重试。")
        return
    st.markdown(
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:8px">'
        f'<a href="{url}" target="_blank" rel="noopener" '
        'style="padding:11px 20px;border-radius:11px;font-weight:700;color:#fff;text-decoration:none;'
        'background:linear-gradient(120deg,var(--brand),var(--brand2));'
        'box-shadow:0 8px 24px -8px rgba(124,108,255,.55)">📄 查看完整报告（新标签打开）</a>'
        f'<a href="{url}" download="{fname}" '
        'style="padding:11px 18px;border-radius:11px;font-weight:700;text-decoration:none;'
        'border:1px solid var(--border);background:var(--panel2);color:var(--text)">⬇️ 下载 HTML</a>'
        f'</div><div style="color:var(--faint);font-size:.8rem;margin-top:6px">'
        f'文件：{fname} · 「查看」直接在浏览器打开；「下载」存到本地。</div>',
        unsafe_allow_html=True)


# ===========================================================================
# 行情页（打开先看到的页面）
# ===========================================================================
@st.cache_data(ttl=30, show_spinner="📡 加载行情…")
def _quotes_cached(tickers: tuple, market: str) -> dict:
    return ui_data.quotes([{"ticker": t, "market": market} for t in tickers])


_LIST_SPINNER = {
    "🇨🇳 A股": "📥 正在加载 A 股名单（首次约 20 秒，之后秒开）…",
    "🇭🇰 港股": "📥 正在加载港股名单（首次约 15 秒，之后秒开）…",
    "🇺🇸 美股": "📥 正在加载美股名单（首次约 30~40 秒，之后秒开）…",
}


def _warm_list(market: str) -> None:
    """确保该市场名称全表已加载（首次带 spinner；今天已缓存则瞬回）。"""
    if market not in _LIST_SPINNER:
        return
    if ui_data._read_list(market).get("day") == time.strftime("%Y%m%d"):
        return  # 今天已缓存，search / resolve_name 直接读缓存
    with st.spinner(_LIST_SPINNER[market]):
        ui_data.instrument_list(market)


def _fmt_price(p) -> str:
    if p is None:
        return "—"
    return f"{p:,.2f}" if p >= 1 else f"{p:.4f}"


def render_quotes_table(market: str) -> None:
    if market == "🇨🇳 A股":
        _warm_list(market)
    wl = [e for e in load_watchlist() if e.get("market") == market]
    names = {e["ticker"]: e.get("name") for e in wl}
    tickers: list[str] = []
    for t in [e["ticker"] for e in wl] + ui_data.default_tickers(market):
        if t not in tickers:
            tickers.append(t)
    if not tickers:
        st.caption("这里还没有内容，去左侧搜名称后「⭐ 加入自选」。")
        return
    qd = _quotes_cached(tuple(tickers), market)
    st.markdown('<div class="qhead"><span>名称 / 代码</span><span>最新价</span><span>涨跌幅</span></div>',
                unsafe_allow_html=True)
    for t in tickers:
        q = qd.get(t) or {}
        name = names.get(t) or ui_data.resolve_name(t, market)
        price, chg = q.get("price"), q.get("chg")
        star = '<span class="qstar">★</span>' if t in names else ""
        if chg is None:
            chg_html = '<span style="color:var(--faint)">—</span>'
        else:
            col = "var(--red)" if chg >= 0 else "var(--green)"  # 红涨绿跌
            arr = "▲" if chg >= 0 else "▼"  # 箭头：不只靠颜色传达（a11y color-not-only）
            chg_html = f'<span style="color:{col}">{arr} {chg:+.2f}%</span>'
        c = st.columns([8.4, 1.6], vertical_alignment="center")
        c[0].markdown(
            f'<div class="qrow"><div class="qname">{star}{name}'
            f'<span class="qcode">{t}</span></div>'
            f'<div class="qprice">{_fmt_price(price)}</div>'
            f'<div class="qchg">{chg_html}</div></div>', unsafe_allow_html=True)
        c[1].button("查看", key=f"an_{market}_{t}", use_container_width=True,
                    on_click=_focus, args=(market, t))


def render_market_page() -> None:
    st.markdown('<div class="sec-title">📈 实时行情 · 点开看 K 线、做分析</div>', unsafe_allow_html=True)
    mname = st.segmented_control("市场", list(MARKETS.keys()), default="🇨🇳 A股", format_func=_mkt_short,
                                 key="mkt_page_sel", label_visibility="collapsed")
    mname = mname or "🇨🇳 A股"
    rc = st.columns([6, 1], vertical_alignment="center")
    rc[0].caption(f"📡 {MARKETS[mname]['data']} · 自选 + 热门，每 30 秒缓存")
    if rc[1].button("🔄 刷新", use_container_width=True):
        _quotes_cached.clear()
        st.rerun()
    with st.container(border=True):
        render_quotes_table(mname)
    st.info("点某行的 **📈 查看** 看 K 线走势并一键分析；或在左侧搜名称 / 用 **⭐ 自选库** 挑选。")


# ---- 个股详情：K 线走势 + 一键 AI 分析 -------------------------------------
@st.cache_data(ttl=120, show_spinner=False)
def _kline_cached(ticker: str, period: str, interval: str) -> list:
    return ui_data.kline(ticker, period, interval)


# 周期 -> (yfinance period, interval)。分时/5日为日内级别（参考富途/同花顺的分时+多周期）
_PERIODS = {
    "分时": ("1d", "5m"), "5 日": ("5d", "30m"), "1 月": ("1mo", "1d"),
    "3 月": ("3mo", "1d"), "6 月": ("6mo", "1d"), "1 年": ("1y", "1d"),
}


def _kline_figure(rows: list):
    t = THEMES.get(theme, THEMES["dark"])
    up, down = t["red"], t["green"]  # A 股惯例：红涨绿跌
    grid = t["border_soft"]
    x = [r["dt"] for r in rows]  # 类别轴：candle 等距排列，自动无周末/隔夜跳空（同主流软件）
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.02,
                        row_heights=[0.76, 0.24])
    fig.add_trace(go.Candlestick(
        x=x, open=[r["open"] for r in rows], high=[r["high"] for r in rows],
        low=[r["low"] for r in rows], close=[r["close"] for r in rows],
        increasing_line_color=up, decreasing_line_color=down,
        increasing_fillcolor=up, decreasing_fillcolor=down, line_width=1,
        name="K线", showlegend=False), row=1, col=1)
    vol_colors = [up if r["close"] >= r["open"] else down for r in rows]
    fig.add_trace(go.Bar(x=x, y=[r["volume"] for r in rows], marker_color=vol_colors,
                         marker_line_width=0, opacity=0.55, showlegend=False, name="量"), row=2, col=1)
    fig.update_layout(height=440, margin=dict(l=6, r=54, t=6, b=6), dragmode="pan",
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color=t["muted"], size=11), hovermode="x unified",
                      xaxis_rangeslider_visible=False, bargap=0.2)
    fig.update_xaxes(type="category", showgrid=False, nticks=8, row=1, col=1)
    fig.update_xaxes(type="category", showgrid=False, nticks=8, row=2, col=1)
    fig.update_yaxes(gridcolor=grid, side="right", row=1, col=1, tickfont=dict(size=10))
    fig.update_yaxes(showgrid=False, side="right", row=2, col=1, tickfont=dict(size=9))
    return fig


def render_detail_page(focus: dict) -> None:
    market, ticker = focus["market"], focus["ticker"]
    name = ui_data.resolve_name(ticker, market)
    top = st.columns([1.4, 6], vertical_alignment="center")
    top[0].button("← 行情列表", use_container_width=True, on_click=_back_to_list)
    # 头部：名称 + 现价 + 涨跌
    q = (_quotes_cached((ticker,), market) or {}).get(ticker) or {}
    price, chg = q.get("price"), q.get("chg")
    flag = market.split(" ")[0]
    if chg is None:
        px_html = '<span style="color:var(--faint)">—</span>'
    else:
        cc = "var(--red)" if chg >= 0 else "var(--green)"  # 红涨绿跌
        arr = "▲" if chg >= 0 else "▼"
        px_html = (f'<span style="font-size:1.7rem;font-weight:800;font-family:JetBrains Mono;color:{cc}">'
                   f'{_fmt_price(price)}</span>'
                   f'<span style="color:{cc};font-weight:700;margin-left:10px">{arr} {chg:+.2f}%</span>')
    st.markdown(
        f'<div class="card" style="display:flex;align-items:center;justify-content:space-between;'
        f'flex-wrap:wrap;gap:10px;margin-top:10px">'
        f'<div><div style="font-size:1.35rem;font-weight:800">{flag} {name}</div>'
        f'<div style="color:var(--faint);font-family:JetBrains Mono;font-size:.85rem">{ticker}</div></div>'
        f'<div style="text-align:right">{px_html}</div></div>', unsafe_allow_html=True)
    # 周期 + K 线（分时 / 5日 / 1月 / 3月 / 6月 / 1年）
    per = st.segmented_control("周期", list(_PERIODS.keys()), default="3 月",
                               key="kline_period", label_visibility="collapsed")
    period, interval = _PERIODS.get(per or "3 月", ("3mo", "1d"))
    with st.spinner("加载 K 线…"):
        rows = _kline_cached(ticker, period, interval)
    if not rows:
        st.warning("暂无 K 线数据（该股票 / 币种可能在 Yahoo 无对应周期行情）。")
    elif go is None:
        st.line_chart({"收盘价": [r["close"] for r in rows]})
    else:
        with st.container(border=True):
            st.plotly_chart(_kline_figure(rows), use_container_width=True,
                            config={"displayModeBar": False, "scrollZoom": True})
    # 一键分析
    st.button(f"🔬 用多智能体分析 {name}", type="primary", use_container_width=True,
              on_click=_start_analysis, args=(market, ticker))
    st.caption("进入「智能分析」：分析师团队 → 多空辩论 → 交易员 → 风控 → 组合经理，逐步推演到买/卖/持有决策。")


# ---- 历史记录页 ------------------------------------------------------------
_DEC_COLOR = {"buy": "var(--green)", "sell": "var(--red)", "hold": "var(--amber)"}


@st.cache_data(ttl=1800, show_spinner=False)
def _history_report_html(hid: str):
    """构建该次分析的报告 (fname, html)，按 hid 缓存避免重复生成。"""
    item = load_history_item(hid)
    state, cfg = item.get("state"), item.get("cfg", {})
    if not (state and (state.get("final_trade_decision") or "").strip()):
        return None
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", str(cfg.get("ticker") or item.get("ticker") or "report")) or "report"
    # 带上唯一 hid，避免同票同日的多次分析撞文件名、导出到别次的报告
    fname = f"{safe}_{cfg.get('date_str') or item.get('date_str', '')}_{hid}_report.html"
    return fname, build_report_html(state, cfg)


def render_history_page() -> None:
    st.markdown('<div class="sec-title">📜 历史分析记录</div>', unsafe_allow_html=True)
    items = load_history_index()
    if not items:
        st.info("还没有历史记录。每完成一次分析会自动存档在这里，可随时查看；想删时点 🗑️ 删除。")
        return
    st.caption(f"共 {len(items)} 条 · 自动存档，仅手动删除")
    for it in items:
        flag = (it.get("market") or "").split(" ")[0]
        col = _DEC_COLOR.get(it.get("decision_cls"), "var(--faint)")
        dec = it.get("decision") or "—"
        with st.container(border=True):
            c = st.columns([1.0, 3.4, 0.95, 0.95, 0.95], vertical_alignment="center")
            c[0].markdown(
                f'<div style="text-align:center;font-weight:800;color:{col};border:1px solid {col};'
                f'border-radius:9px;padding:5px 0;font-size:.95rem">{dec}</div>', unsafe_allow_html=True)
            c[1].markdown(
                f"<div style='font-weight:700'>{flag} {it.get('name') or it.get('ticker')}</div>"
                f"<div style='color:var(--faint);font-size:.76rem;font-family:JetBrains Mono'>"
                f"{it.get('ticker')} · 分析日 {it.get('date_str')} · 跑于 {it.get('ts')}</div>",
                unsafe_allow_html=True)
            c[2].button("👁️ 查看", key=f"hv_{it['id']}", use_container_width=True,
                        on_click=_view_history, args=(it["id"],))
            _rep = _history_report_html(it["id"])
            _url = _publish_report(_rep[1], _rep[0]) if _rep else ""
            if _url:
                c[3].markdown(
                    f'<a href="{_url}" download="{_rep[0]}" target="_blank" rel="noopener" '
                    'style="display:block;text-align:center;padding:7px 0;border-radius:10px;'
                    'border:1px solid var(--border);background:var(--panel2);color:var(--text);'
                    'text-decoration:none;font-size:.82rem;font-weight:600">📥 导出</a>',
                    unsafe_allow_html=True)
            else:
                c[3].caption("—")
            c[4].button("🗑️ 删除", key=f"hd_{it['id']}", use_container_width=True,
                        on_click=_del_history, args=(it["id"],))


# ===========================================================================
# 侧边栏
# ===========================================================================
with st.sidebar:
    st.markdown("### 🎯 分析对象")
    # 4 大类切换（A股 / 美股 / 港股 / 虚拟币）—— 替代下拉
    market_name = st.segmented_control("市场 / 资产", list(MARKETS.keys()), default="🇨🇳 A股",
                                       format_func=_mkt_short, key="market_sel", label_visibility="collapsed")
    market_name = market_name or "🇨🇳 A股"
    mkt = MARKETS[market_name]
    if st.session_state.get("_mkt") != market_name:
        st.session_state["_mkt"] = market_name
        st.session_state["ticker"] = mkt["examples"][0]
    # 名称 / 代码搜索：输入即出最匹配的几只，点选即填
    if market_name == "🇨🇳 A股":
        _warm_list(market_name)  # A股：进入即预热（当前标的也要显示中文名）
    _q = st.text_input("🔎 搜名称 / 代码", key="search_q", placeholder="如 茅台 / 智谱 / 2513 / NVDA")
    if (_q or "").strip():
        _warm_list(market_name)  # 港股/美股：搜索时才加载其全表（带 spinner）
        _matches = ui_data.search(_q.strip(), market_name, n=8)
        if _matches:
            for _m in _matches:
                st.button(f"{_m['name']}　·　{_m['ticker']}", key=f"srch_{_m['market']}_{_m['ticker']}",
                          use_container_width=True, on_click=_pick_search,
                          args=(_m["market"], _m["ticker"]))
        else:
            st.caption("无匹配，可直接在下方填代码")
    ticker = st.text_input("代码", key="ticker")
    _cur_name = ui_data.resolve_name(ticker, market_name)
    if _cur_name and _cur_name != (ticker or "").strip():
        st.caption(f"📌 当前：{_cur_name}")
    if st.button("⭐ 加入自选", use_container_width=True):
        if add_to_watchlist(ticker, market_name):
            st.toast(f"已加入自选：{_cur_name or ticker}")
        st.rerun()
    st.caption(f"📡 {mkt['data']}")

    # ⭐ 自选库（可折叠，默认收起；显示名称）
    _wl = load_watchlist()
    with st.expander(f"⭐ 自选库 · {len(_wl)}", expanded=False):
        if not _wl:
            st.caption("自选库还是空的。搜索或填代码后点「⭐ 加入自选」。")
        for _e in _wl:
            _mk = _e.get("market", market_name)
            _flag = (_mk or "").split(" ")[0]
            _nm = _e.get("name") or ui_data.resolve_name(_e["ticker"], _mk)
            wc1, wc2 = st.columns([5, 1], vertical_alignment="center")
            wc1.button(f"{_flag} {_nm}", key=f"wl_{_mk}_{_e['ticker']}", use_container_width=True,
                       on_click=_select_watchlist, args=(_mk, _e["ticker"]), help=_e["ticker"])
            if wc2.button("✕", key=f"wldel_{_mk}_{_e['ticker']}", use_container_width=True):
                remove_from_watchlist(_e["ticker"], _mk)
                st.rerun()

    st.markdown("### 🔌 LLM / API Key")
    _settings = load_settings()
    _saved_keys = _settings.get("keys", {})
    _provs = list(PROVIDERS.keys())
    prov_name = st.selectbox("Provider", _provs, index=_provs.index("DeepSeek"))
    provider, needs_url = PROVIDERS[prov_name]
    base_url = st.text_input("Base URL（中转站）",
                             value=(_settings.get("base_url") or os.environ.get("TRADINGAGENTS_LLM_BACKEND_URL", "")) if needs_url else "",
                             disabled=not needs_url, placeholder="https://...")
    key_env = PROVIDER_API_KEY_ENV.get(provider)
    has_env_key = bool(key_env and os.environ.get(key_env))
    _saved_key = _saved_keys.get(key_env, "") if key_env else ""
    api_key = st.text_input(f"API Key · {key_env or '—'}", type="password", value=_saved_key,
                            placeholder="留空用 .env 中的值" if has_env_key else "粘贴 key")
    remember = st.checkbox("💾 记住 API Key（本机明文保存，下次自动填）", value=True)
    # 持久化：仅在有变化时写盘
    _new_keys = dict(_saved_keys)
    if key_env:
        if remember and (api_key or "").strip():
            _new_keys[key_env] = api_key.strip()
        elif not remember:
            _new_keys.pop(key_env, None)
    _new_settings = {**_settings, "keys": _new_keys,
                     "base_url": base_url if needs_url else _settings.get("base_url", "")}
    if _new_settings != _settings:
        save_settings(_new_settings)
    # 模型用下拉选择（不可手填），每个 provider 一组、各自默认
    models = PROVIDER_MODELS.get(provider, [])
    ddef, qdef = PROVIDER_DEFAULT.get(provider, (models[0] if models else "", models[-1] if models else ""))
    dk, qk = f"deep_{provider}", f"quick_{provider}"
    st.session_state.setdefault(dk, ddef if ddef in models else (models[0] if models else ""))
    st.session_state.setdefault(qk, qdef if qdef in models else (models[-1] if models else ""))
    # 满宽上下排（原来两列并排太窄，模型名被截断成 "deepseek…"）
    deep_model = st.selectbox("Deep 模型 · 主力（深度思考）", models, key=dk)
    quick_model = st.selectbox("Quick 模型 · 快速（轻量步骤）", models, key=qk)

    st.markdown("### ⚙️ 参数")
    # 分析师：可点选的 pills（比 multiselect 的标签框更直观美观），默认全选
    _apill = {f"{ic} {nm.split(' / ')[0].replace('分析师', '').strip()}": nm for nm, ic, *_ in ANALYSTS}
    _sel = st.pills("分析师团队 · 可多选", list(_apill), selection_mode="multi",
                    default=list(_apill), key="analysts_pills")
    analysts_sel = [_apill[lb] for lb in (_sel or [])]
    rc = st.columns(2)
    debate_rounds = rc[0].slider("多空轮数", 1, 3, 1)
    risk_rounds = rc[1].slider("风控轮数", 1, 3, 1)
    language = st.radio("输出语言", ["中文", "English"], horizontal=True)
    # 分析基准日：默认今天（用最新数据）。仅回测历史某天才需要改 -> 收进折叠项。
    with st.expander("🗓️ 分析基准日 · 默认最新", expanded=False):
        st.caption("默认用最新数据分析；仅当你想回测历史某一天时才需要在此修改。")
        trade_date = st.date_input("基准日", value=date.today(), max_value=date.today(),
                                   label_visibility="collapsed")

    run = st.button("🚀 开始分析", type="primary", use_container_width=True)
    if st.session_state.get("result") and st.button("🗑️ 清除结果", use_container_width=True):
        st.session_state.pop("result", None)
        st.rerun()


# ===========================================================================
# 主区
# ===========================================================================
# 右上角小巧主题切换（不抢眼）
_tcols = st.columns([11, 1])
with _tcols[1]:
    if st.button("☀️" if theme == "dark" else "🌙", key="theme_toggle",
                 help="切换深色 / 浅色主题", use_container_width=True):
        st.session_state["theme"] = "light" if theme == "dark" else "dark"
        st.rerun()

st.markdown(
    '<div class="apphead"><div class="logo">'
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.3" '
    'stroke-linecap="round" stroke-linejoin="round"><polyline points="3 17 9 11 13 15 21 6"/>'
    '<polyline points="15 6 21 6 21 12"/></svg></div>'
    f'<div><div class="bn">{APP_NAME}<span class="en">TRADINGAGENTS</span></div>'
    '<div class="bt">你的 AI 股市助手 · 看行情 · 多智能体帮你分析买卖</div></div></div>',
    unsafe_allow_html=True)

# dev 预览：?demo=1 加载最近一次已完成的 run（须在 nav_page 控件实例化前设置 widget 状态，
# 否则页面内容与导航控件不同步）
if st.query_params.get("demo") and "result" not in st.session_state:
    import glob
    _files = sorted(glob.glob(os.path.join(_HERE, ".uiruns", "*progress*.json")))
    for _fp in reversed(_files):
        try:
            _d = json.load(open(_fp, encoding="utf-8"))
            if _d.get("status") == "done" and (_d.get("final_trade_decision") or "").strip():
                st.session_state["result"] = {"state": _d,
                    "cfg": {"ticker": "BTC-USD", "date_str": "2026-06-23", "market": "₿ 虚拟币"}}
                st.session_state["nav_page"] = NAV_ANALYSIS
                break
        except Exception:  # noqa: BLE001
            pass

# 顶部导航：行情（打开默认）/ 智能分析 / 历史
do_run = bool(run) or bool(st.session_state.get("start_run"))
if do_run:
    st.session_state["nav_page"] = NAV_ANALYSIS  # 开始分析 -> 切到分析页
page = st.segmented_control("页面", [NAV_MARKET, NAV_ANALYSIS, NAV_HISTORY], default=NAV_MARKET,
                            key="nav_page", label_visibility="collapsed")
page = page or NAV_MARKET


def _empty_state():
    st.markdown('<div class="sec-title">分析流程</div>', unsafe_allow_html=True)
    render_stepper({}, running=False)
    st.markdown(
        '<div class="card soft" style="margin-top:14px">'
        '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:14px;text-align:center">'
        '<div><div style="font-size:1.6rem">🔍</div><b>分析师团队</b><div style="color:var(--muted);font-size:.82rem;margin-top:4px">市场·情绪·新闻·基本面，各自取数产报告</div></div>'
        '<div><div style="font-size:1.6rem">🐂</div><b>研究员辩论</b><div style="color:var(--muted);font-size:.82rem;margin-top:4px">多头 vs 空头，研究经理裁决</div></div>'
        '<div><div style="font-size:1.6rem">💼</div><b>交易员</b><div style="color:var(--muted);font-size:.82rem;margin-top:4px">拟定带价位的交易方案</div></div>'
        '<div><div style="font-size:1.6rem">🛡️</div><b>风控辩论</b><div style="color:var(--muted);font-size:.82rem;margin-top:4px">激进·保守·中立三方评估</div></div>'
        '<div><div style="font-size:1.6rem">🎯</div><b>组合经理</b><div style="color:var(--muted);font-size:.82rem;margin-top:4px">最终买/卖/持有 + 仓位止损</div></div>'
        '</div></div>', unsafe_allow_html=True)
    st.info("👈 在左侧选市场、填代码（或搜名称）、配置 API Key，点 **开始分析**。数据源会按市场自动路由。")


def _build_ui_cfg():
    name2key = {a[0]: a[3] for a in ANALYSTS}
    return {
        "ticker": (ticker or "").strip(), "date_str": trade_date.strftime("%Y-%m-%d"),
        "asset_type": mkt["asset_type"], "market": market_name, "provider": provider,
        # Only relay providers use a base_url. Do NOT fall back to the .env
        # relay URL for native providers (DeepSeek/OpenAI/...), or their
        # requests get sent to the relay and 401 (#relay-leak).
        "base_url": (base_url or os.environ.get("TRADINGAGENTS_LLM_BACKEND_URL", "")) if needs_url else "",
        "api_key": api_key, "key_env": key_env, "deep_model": deep_model, "quick_model": quick_model,
        "output_language": "Chinese" if language == "中文" else "English",
        "debate_rounds": debate_rounds, "risk_rounds": risk_rounds,
        "selected_analysts": [name2key[a[0]] for a in ANALYSTS if a[0] in analysts_sel],
    }


def _placeholders():
    out = {}
    st.markdown('<div class="sec-title">① 分析师团队</div>', unsafe_allow_html=True); out["a"] = st.empty()
    st.markdown('<div class="sec-title">② 研究员辩论（多空）</div>', unsafe_allow_html=True); out["d"] = st.empty()
    st.markdown('<div class="sec-title">③ 交易员</div>', unsafe_allow_html=True); out["t"] = st.empty()
    st.markdown('<div class="sec-title">④ 风控辩论</div>', unsafe_allow_html=True); out["r"] = st.empty()
    st.markdown('<div class="sec-title">⑤ 最终决策</div>', unsafe_allow_html=True); out["f"] = st.empty()
    return out


if page == NAV_HISTORY and not do_run:
    render_history_page()

elif page == NAV_MARKET and not do_run:
    if st.session_state.get("focus"):
        render_detail_page(st.session_state["focus"])  # 右侧：个股 K 线 + 一键分析
    else:
        render_market_page()

elif do_run:
    st.session_state.pop("start_run", None)
    ui_cfg = _build_ui_cfg()
    if not ui_cfg["ticker"]:
        st.error("请填写股票代码"); st.stop()

    here = os.path.dirname(os.path.abspath(__file__))
    workdir = os.path.join(here, ".uiruns"); os.makedirs(workdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cfg_path = os.path.join(workdir, f"cfg_{stamp}.json")
    progress_path = os.path.join(workdir, f"progress_{stamp}.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(ui_cfg, f, ensure_ascii=False)

    st.markdown('<div class="sec-title">实时进度</div>', unsafe_allow_html=True)
    step_ph, kpi_ph, status_ph = st.empty(), st.empty(), st.empty()
    status_ph.info(f"🔧 分析进行中 · {ui_cfg['ticker']} @ {ui_cfg['date_str']} · {provider}/{deep_model}"
                   "　|　约几分钟（数十次 LLM 调用），请保持页面打开")
    phs = _placeholders()

    # 启动前先终止上一次可能仍在运行的 worker：rerun / 切页会丢弃下面的轮询循环，
    # 但子进程会继续跑成孤儿。存句柄到 session_state，下次启动时回收。
    _prev = st.session_state.get("worker")
    if _prev is not None and _prev.poll() is None:
        try:
            _prev.terminate()
        except Exception:  # noqa: BLE001
            pass
    proc = subprocess.Popen([sys.executable, os.path.join(here, "ui_worker.py"), cfg_path, progress_path], cwd=here)
    st.session_state["worker"] = proc

    def _read():
        try:
            with open(progress_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None

    state, status, t0 = {}, "running", time.time()
    while True:
        s = _read()
        if s is not None:
            state, status = s, s.get("status", "running")
        with step_ph.container():
            render_stepper(state, running=(status == "running"))
        with kpi_ph.container():
            render_kpis(state, time.time() - t0)
        render_pipeline(state, phs, running=(status == "running"), any_started=True)
        if status in ("done", "error"):
            break
        if proc.poll() is not None:
            s = _read()
            if s is not None:
                state, status = s, s.get("status", "running")
            if status not in ("done", "error"):
                status, state = "error", {**state, "error": f"worker 进程异常退出 (code {proc.returncode})"}
            break
        time.sleep(1.3)

    if status == "error":
        status_ph.error("运行出错")
        st.error(state.get("error", "未知错误"))
        st.info("💡 怎么办：① 确认左侧 **Provider 与 API Key 匹配**（最常见：选了 DeepSeek 却填了中转站的 "
                "key，或反之）；② 用中转站要填 **Base URL**；③ 检查网络 / 额度后，点 **🚀 开始分析** 重试。")
    else:
        status_ph.success(f"✅ 分析完成 · 用时 {int((time.time()-t0)//60)} 分 {int((time.time()-t0)%60)} 秒")
        st.session_state["result"] = {"state": state, "cfg": ui_cfg}
        if state.get("final_trade_decision"):
            save_history(state, ui_cfg)  # 自动存档到历史记录
            render_report_download(state, ui_cfg)

elif st.session_state.get("result"):
    res = st.session_state["result"]; state = res["state"]; cfg = res.get("cfg", {})
    label, _ = _decision_action(state.get("final_trade_decision") or "")
    st.markdown(
        f'<div class="card soft">最近一次分析 · <b style="color:var(--text)">{cfg.get("ticker", "")}</b> '
        f'@ {cfg.get("date_str", "")} · <span class="pill done">{label}</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="sec-title">分析流程</div>', unsafe_allow_html=True)
    render_stepper(state, running=False)
    render_kpis(state, None)
    phs = _placeholders()
    render_pipeline(state, phs, running=False, any_started=True)
    if state.get("final_trade_decision"):
        render_report_download(state, cfg)

else:
    _empty_state()
