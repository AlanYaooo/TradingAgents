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

import streamlit as st

APP_NAME = "千机智能体"

st.set_page_config(
    page_title=f"{APP_NAME} · 多智能体交易分析",
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
THEMES = {
    "dark": dict(
        bg="#0B1220", panel="#121E38", panel2="#18233D", soft="#0F1A30",
        border="#243352", border_soft="#1C2A47",
        text="#E8EEF8", muted="#93A1BC", faint="#6B7A99",
        brand="#7C6CFF", brand2="#4F8DFD", cyan="#34D3EE",
        green="#22C55E", red="#F0506E", amber="#F6A723",
        sidebar="#0C1730",
        app_bg="radial-gradient(1100px 560px at 12% -8%, rgba(124,108,255,.16), transparent 60%),"
               "radial-gradient(900px 520px at 96% 2%, rgba(52,211,238,.10), transparent 55%), #0B1220",
        hero_grad="linear-gradient(125deg,#16223f 0%,#111c34 55%,#0e1830 100%)",
        dec_grad="linear-gradient(125deg,#16223f,#0f1a30)",
        h1_grad="linear-gradient(90deg,#fff,#bfd0ff 60%,#9fe9f5)",
        shadow="0 10px 30px -16px rgba(0,0,0,.6)",
    ),
    "light": dict(
        bg="#F3F6FC", panel="#FFFFFF", panel2="#EEF2FB", soft="#F8FAFE",
        border="#E0E7F2", border_soft="#EAEFF8",
        text="#16213C", muted="#56627B", faint="#93A0B8",
        brand="#6A5BFF", brand2="#3F7DF0", cyan="#0E9FC4",
        green="#16A34A", red="#DC2C56", amber="#C77508",
        sidebar="#EFF3FB",
        app_bg="radial-gradient(1100px 560px at 12% -8%, rgba(124,108,255,.10), transparent 60%),"
               "radial-gradient(900px 520px at 96% 2%, rgba(52,211,238,.07), transparent 55%), #F3F6FC",
        hero_grad="linear-gradient(125deg,#eaf0fe 0%,#e6edfb 55%,#f3f7ff 100%)",
        dec_grad="linear-gradient(125deg,#eaf0fe,#f3f7ff)",
        h1_grad="linear-gradient(90deg,#1b2552,#3a4fa6 55%,#0e7490)",
        shadow="0 10px 28px -18px rgba(40,60,110,.35)",
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
.stButton>button{ border-radius:10px; font-weight:600; border:1px solid var(--border); background:var(--panel2); color:var(--text); transition:.15s; }
.stButton>button:hover{ border-color:var(--brand); }
.stButton>button[kind="primary"]{ background:linear-gradient(120deg,var(--brand),var(--brand2)); border:none; color:#fff;
  box-shadow:0 8px 24px -8px rgba(124,108,255,.6); font-weight:700; letter-spacing:.02em; }
.stButton>button[kind="primary"]:hover{ filter:brightness(1.08); }
div[data-testid="stVerticalBlockBorderWrapper"]{ background:var(--panel) !important; border-color:var(--border) !important; border-radius:14px !important; box-shadow:var(--shadow); }
[data-testid="stExpander"]{ border:1px solid var(--border) !important; border-radius:12px !important; background:var(--soft) !important; }
[data-testid="stExpander"] summary{ color:var(--text) !important; }

.hero{ border:1px solid var(--border); border-radius:20px; padding:24px 30px; margin-bottom:18px; background:var(--hero-grad); position:relative; overflow:hidden; }
.hero::after{ content:""; position:absolute; right:-60px; top:-60px; width:240px; height:240px; background:radial-gradient(circle,rgba(124,108,255,.32),transparent 70%); }
.hero .brandrow{ display:flex; align-items:center; gap:14px; }
.hero .logo{ width:46px; height:46px; border-radius:13px; display:flex; align-items:center; justify-content:center; flex:0 0 auto;
  background:linear-gradient(135deg,var(--brand),var(--brand2)); box-shadow:0 10px 24px -8px rgba(124,108,255,.75); }
.hero h1{ font-size:1.8rem; font-weight:800; margin:0; letter-spacing:.01em; background:var(--h1-grad); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
.hero .en{ font-size:.82rem; color:var(--faint); font-weight:600; letter-spacing:.18em; }
.hero p{ color:var(--muted); margin:.55rem 0 0; font-size:.95rem; }
.hero .tag{ display:inline-block; margin-top:12px; padding:4px 12px; border-radius:999px; font-size:.74rem; font-weight:600;
  background:rgba(124,108,255,.14); color:var(--brand); border:1px solid rgba(124,108,255,.3); margin-right:8px; }

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


_ACTIONS = [("BUY", "buy", ["buy", "买入", "增持", "看多", "overweight"]),
            ("SELL", "sell", ["sell", "卖出", "减持", "看空", "underweight"]),
            ("HOLD", "hold", ["hold", "持有", "观望", "中性", "neutral"])]


def _decision_action(text: str):
    t = (text or "").lower()
    m = re.search(r"final transaction proposal:\s*\*?\*?\s*(buy|sell|hold)", t)
    word = m.group(1) if m else None
    if not word:
        m2 = re.search(r"\*\*(?:action|rating)\*\*\s*[:：]\s*\*?\*?\s*(buy|sell|hold|买入|卖出|持有)", t)
        word = m2.group(1) if m2 else None
    if not word:
        for label, cls, keys in _ACTIONS:
            if any(k in t for k in keys):
                return label, cls
        return "—", "hold"
    for label, cls, keys in _ACTIONS:
        if word in keys or word == label.lower():
            return label, cls
    return word.upper(), "hold"


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

    return (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{cfg.get('ticker','')} 分析报告</title><style>{_REPORT_CSS}</style></head>"
        '<body><div class="wrap"><div class="hd">'
        f'<div class="badge" style="background:{badge_color}">{label}</div>'
        f"<div><h1>{APP_NAME} · {cfg.get('ticker','')} 分析报告</h1>"
        f'<div class="meta">分析日期 {cfg.get("date_str","")} · '
        f"生成于 {datetime.now():%Y-%m-%d %H:%M}</div></div></div>"
        + "".join(parts)
        + f'<div class="ft">由 {APP_NAME}（TradingAgents 多智能体）生成 · 仅供研究，不构成投资建议</div>'
        "</div></body></html>"
    )


# ===========================================================================
# 侧边栏
# ===========================================================================
with st.sidebar:
    st.markdown("### 🎯 分析对象")
    market_name = st.selectbox("市场 / 资产", list(MARKETS.keys()), label_visibility="collapsed")
    mkt = MARKETS[market_name]
    if st.session_state.get("_mkt") != market_name:
        st.session_state["_mkt"] = market_name
        st.session_state["ticker"] = mkt["examples"][0]
    ticker = st.text_input("代码", key="ticker")
    chip_cols = st.columns(len(mkt["examples"]))
    for c, ex in zip(chip_cols, mkt["examples"], strict=False):
        if c.button(ex, key=f"ex_{ex}", use_container_width=True):
            st.session_state["ticker"] = ex
            st.rerun()
    st.caption(f"📡 {mkt['data']}")
    trade_date = st.date_input("分析日期", value=date(2026, 6, 23))

    st.markdown("### 🔌 LLM / API Key")
    prov_name = st.selectbox("Provider", list(PROVIDERS.keys()))
    provider, needs_url = PROVIDERS[prov_name]
    base_url = st.text_input("Base URL（中转站）",
                             value=os.environ.get("TRADINGAGENTS_LLM_BACKEND_URL", "") if needs_url else "",
                             disabled=not needs_url, placeholder="https://...")
    key_env = PROVIDER_API_KEY_ENV.get(provider)
    has_key = bool(key_env and os.environ.get(key_env))
    api_key = st.text_input(f"API Key · {key_env or '—'}", type="password",
                            placeholder="留空用 .env 中的值" if has_key else "粘贴 key")
    # 模型用下拉选择（不可手填），每个 provider 一组、各自默认
    models = PROVIDER_MODELS.get(provider, [])
    ddef, qdef = PROVIDER_DEFAULT.get(provider, (models[0] if models else "", models[-1] if models else ""))
    dk, qk = f"deep_{provider}", f"quick_{provider}"
    st.session_state.setdefault(dk, ddef if ddef in models else (models[0] if models else ""))
    st.session_state.setdefault(qk, qdef if qdef in models else (models[-1] if models else ""))
    cmc = st.columns(2)
    deep_model = cmc[0].selectbox("Deep 模型 · 主力", models, key=dk)
    quick_model = cmc[1].selectbox("Quick 模型 · 快速", models, key=qk)

    st.markdown("### ⚙️ 参数")
    language = st.radio("输出语言", ["中文", "English"], horizontal=True)
    rc = st.columns(2)
    debate_rounds = rc[0].slider("多空轮数", 1, 3, 1)
    risk_rounds = rc[1].slider("风控轮数", 1, 3, 1)
    analysts_sel = st.multiselect("分析师", [a[0] for a in ANALYSTS], default=[a[0] for a in ANALYSTS])

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
    '<div class="hero"><div class="brandrow"><div class="logo">'
    '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" '
    'stroke-linecap="round" stroke-linejoin="round"><polyline points="3 17 9 11 13 15 21 6"/>'
    '<polyline points="15 6 21 6 21 12"/></svg></div>'
    f'<div><h1>{APP_NAME}</h1><div class="en">TRADINGAGENTS</div></div></div>'
    '<p>多智能体交易分析 · 模拟真实交易公司的分工与协作，逐步推演到最终决策</p>'
    '<span class="tag">分析师团队</span><span class="tag">多空辩论</span>'
    '<span class="tag">风控评估</span><span class="tag">组合经理决策</span></div>',
    unsafe_allow_html=True)

# dev 预览：?demo=1 加载最近一次已完成的 run，用于设计调试（无需实跑）
if st.query_params.get("demo") and "result" not in st.session_state:
    import glob
    _files = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".uiruns", "*progress*.json")))
    for _fp in reversed(_files):
        try:
            _d = json.load(open(_fp, encoding="utf-8"))
            if _d.get("status") == "done" and (_d.get("final_trade_decision") or "").strip():
                st.session_state["result"] = {"state": _d, "cfg": {"ticker": "BTC-USD", "date_str": "2026-06-23"}}
                break
        except Exception:  # noqa: BLE001
            pass


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
    st.info("👈 在左侧选择市场与标的、配置 API Key，点击 **开始分析**。数据源会按市场自动路由。")


def _build_ui_cfg():
    name2key = {a[0]: a[3] for a in ANALYSTS}
    return {
        "ticker": (ticker or "").strip(), "date_str": trade_date.strftime("%Y-%m-%d"),
        "asset_type": mkt["asset_type"], "provider": provider,
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


if run:
    ui_cfg = _build_ui_cfg()
    if not ui_cfg["ticker"]:
        st.error("请填写标的代码"); st.stop()

    here = os.path.dirname(os.path.abspath(__file__))
    workdir = os.path.join(here, ".uiruns"); os.makedirs(workdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cfg_path = os.path.join(workdir, f"cfg_{stamp}.json")
    progress_path = os.path.join(workdir, f"progress_{stamp}.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(ui_cfg, f, ensure_ascii=False)

    st.markdown('<div class="sec-title">实时进度</div>', unsafe_allow_html=True)
    step_ph, kpi_ph, status_ph = st.empty(), st.empty(), st.empty()
    status_ph.info(f"🔧 启动分析 · {ui_cfg['ticker']} @ {ui_cfg['date_str']} · {provider}/{deep_model}")
    phs = _placeholders()

    proc = subprocess.Popen([sys.executable, os.path.join(here, "ui_worker.py"), cfg_path, progress_path], cwd=here)

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
    else:
        status_ph.success(f"✅ 分析完成 · 用时 {int((time.time()-t0)//60)} 分 {int((time.time()-t0)%60)} 秒")
        st.session_state["result"] = {"state": state, "cfg": ui_cfg}
        if state.get("final_trade_decision"):
            st.download_button("⬇️ 下载完整报告 (HTML，浏览器打开)", build_report_html(state, ui_cfg),
                               file_name=f"{ui_cfg['ticker']}_{ui_cfg['date_str']}_report.html",
                               mime="text/html")

elif st.session_state.get("result"):
    res = st.session_state["result"]; state = res["state"]; cfg = res["cfg"]
    label, _ = _decision_action(state.get("final_trade_decision") or "")
    st.markdown(
        f'<div class="card soft">最近一次分析 · <b style="color:var(--text)">{cfg["ticker"]}</b> '
        f'@ {cfg["date_str"]} · <span class="pill done">{label}</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="sec-title">分析流程</div>', unsafe_allow_html=True)
    render_stepper(state, running=False)
    render_kpis(state, None)
    phs = _placeholders()
    render_pipeline(state, phs, running=False, any_started=True)
    if state.get("final_trade_decision"):
        st.download_button("⬇️ 下载完整报告 (HTML，浏览器打开)", build_report_html(state, cfg),
                           file_name=f"{cfg['ticker']}_{cfg['date_str']}_report.html", mime="text/html")

else:
    _empty_state()
