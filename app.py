"""
TradingAgents 可视化界面 (Streamlit)
=================================================================
- 市场/资产选择：A股 / 美股 / 港股 / 虚拟币（数据源自动路由）
- LLM / API Key 配置（含 Anthropic 格式中转站）
- 逐 agent 实时展示：每个分析师在做什么、研究员/风控如何辩论协作、
  每一步的输出，直到最终决策报告

运行：  streamlit run app.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import date, datetime

import streamlit as st

st.set_page_config(page_title="TradingAgents 多智能体分析", page_icon="📈", layout="wide")

# --- 框架导入（放在 set_page_config 之后，便于报错显示在页面上）---
try:
    from langchain_core.callbacks import BaseCallbackHandler

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV
except Exception as exc:  # noqa: BLE001
    st.error(f"框架导入失败：{exc}\n\n请在仓库目录用 venv 运行：\n"
             f"`.\\.venv\\Scripts\\streamlit run app.py`")
    st.stop()


# ---------------------------------------------------------------------------
# 配置数据
# ---------------------------------------------------------------------------
MARKETS = {
    "🇨🇳 A股": dict(asset_type="stock", example="600519.SS",
                   hint="沪 .SS / 深 .SZ / 北 .BJ，例：600519.SS、000001.SZ、830799.BJ",
                   data="akshare（行情/财报/新闻/宏观/千股千评情绪）"),
    "🇺🇸 美股": dict(asset_type="stock", example="NVDA",
                   hint="例：AAPL、NVDA、TSLA、MSFT",
                   data="yfinance + Reddit/StockTwits + Polymarket + FRED"),
    "🇭🇰 港股": dict(asset_type="stock", example="0700.HK",
                   hint="例：0700.HK（腾讯）、9988.HK（阿里）",
                   data="yfinance"),
    "₿ 虚拟币": dict(asset_type="crypto", example="BTC-USD",
                   hint="例：BTC-USD、ETH-USD、SOL-USD",
                   data="yfinance + Reddit/StockTwits + Polymarket（事件盘）"),
}

# provider 友好名 -> (provider id, 是否需要 base_url)
PROVIDERS = {
    "Anthropic / Claude（含中转站）": ("anthropic", True),
    "OpenAI 兼容中转站": ("openai_compatible", True),
    "OpenAI": ("openai", False),
    "DeepSeek": ("deepseek", False),
    "Google / Gemini": ("google", False),
    "通义千问 Qwen": ("qwen", False),
    "本地 Ollama": ("ollama", True),
}

# 分析流水线阶段定义：(标题, 图标, 完成判定函数)
ANALYSTS = [
    ("市场/技术分析师", "📊", "market_report"),
    ("情绪分析师", "💬", "sentiment_report"),
    ("新闻分析师", "📰", "news_report"),
    ("基本面分析师", "📑", "fundamentals_report"),
]


class StatsCallback(BaseCallbackHandler):
    """统计 LLM 调用次数与 token 用量，用于页面成本展示。"""

    def __init__(self):
        self.llm_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def on_llm_end(self, response, **kwargs):  # noqa: D401
        self.llm_calls += 1
        try:
            usage = (response.llm_output or {}).get("token_usage") or {}
            self.tokens_in += usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0
            self.tokens_out += usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 侧边栏：配置
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ 配置")

    st.subheader("1. 标的")
    market_name = st.selectbox("市场 / 资产类型", list(MARKETS.keys()))
    mkt = MARKETS[market_name]
    ticker = st.text_input("代码", value=mkt["example"], help=mkt["hint"])
    st.caption(f"📡 数据源：{mkt['data']}")
    trade_date = st.date_input("分析日期", value=date(2026, 6, 23),
                               help="作为『当前日期』；回测会按此日期截断数据，防 look-ahead")

    st.subheader("2. LLM / API Key")
    prov_name = st.selectbox("Provider", list(PROVIDERS.keys()))
    provider, needs_url = PROVIDERS[prov_name]
    # 预填：已配置的 .env / 环境变量
    env_url = os.environ.get("TRADINGAGENTS_LLM_BACKEND_URL", "")
    base_url = st.text_input("Base URL（中转站端点，如 https://xxx）",
                             value=env_url if needs_url else "",
                             disabled=not needs_url,
                             help="Anthropic 格式中转站选 Anthropic + 填这里")
    key_env = PROVIDER_API_KEY_ENV.get(provider)
    has_env_key = bool(key_env and os.environ.get(key_env))
    api_key = st.text_input(
        f"API Key（env: {key_env or '无需'}）", type="password",
        placeholder="留空则用 .env / 环境变量中的值" if has_env_key else "",
        help="留空时使用 .env 里已配置的 key",
    )
    c1, c2 = st.columns(2)
    deep_model = c1.text_input("Deep 模型", value=os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", "gpt-5.5"))
    quick_model = c2.text_input("Quick 模型", value=os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM", "gpt-5.5"))

    st.subheader("3. 分析参数")
    language = st.radio("输出语言", ["中文", "English"], horizontal=True)
    cc1, cc2 = st.columns(2)
    debate_rounds = cc1.slider("多空辩论轮数", 1, 3, 1)
    risk_rounds = cc2.slider("风控辩论轮数", 1, 3, 1)
    analysts_sel = st.multiselect(
        "启用的分析师", [a[0] for a in ANALYSTS], default=[a[0] for a in ANALYSTS],
    )

    run = st.button("🚀 开始分析", type="primary", use_container_width=True)


# ---------------------------------------------------------------------------
# 主区：标题 + 协作说明
# ---------------------------------------------------------------------------
st.title("📈 TradingAgents 多智能体交易分析")
st.caption("模拟真实交易公司：分析师团队 → 多空研究辩论 → 交易员 → 风控辩论 → 投资组合经理")

with st.expander("🤝 智能体如何协作（点开看流程）", expanded=not run):
    st.markdown(
        """
| 阶段 | 角色 | 协作方式 | 产出 |
|---|---|---|---|
| **① 分析师团队** | 市场/情绪/新闻/基本面 | 各自独立调用工具取数、并行产出报告 | 4 份分析报告 |
| **② 研究员辩论** | 多头 🐂 vs 空头 🐻 | 轮流出论点互相反驳（可多轮），研究经理裁决 | 投资计划 |
| **③ 交易员** | Trader | 综合分析+研究结论，拟定带价位的交易方案 | 交易计划 |
| **④ 风控辩论** | 激进 / 保守 / 中立 | 三方就交易方案辩论风险 | 风险评估 |
| **⑤ 投资组合经理** | Portfolio Manager | 拍板买/卖/持有 + 仓位 + 止损 | **最终决策** |

数据源按市场自动路由：A股→akshare，美股/港股/币→yfinance（+Reddit/StockTwits/Polymarket/FRED）。
        """
    )


def render_analysts(state, ph):
    cols = ph.columns(4)
    for (name, icon, key), col in zip(ANALYSTS, cols, strict=False):
        rep = state.get(key, "") or ""
        with col:
            done = bool(rep.strip())
            st.markdown(f"**{icon} {name}** {'✅' if done else '⏳'}")
            if done:
                with st.expander("查看报告", expanded=False):
                    st.markdown(rep)
            else:
                st.caption("分析中…")


def render_debate(state, ph):
    ids = state.get("investment_debate_state", {}) or {}
    bull, bear = (ids.get("bull_history") or "").strip(), (ids.get("bear_history") or "").strip()
    judge = (ids.get("judge_decision") or "").strip()
    with ph.container():
        if not (bull or bear or judge):
            st.caption("等待分析师报告完成后开始辩论…")
            return
        a, b = st.columns(2)
        with a:
            st.markdown("**🐂 多头研究员**")
            st.markdown(bull or "_（未发言）_")
        with b:
            st.markdown("**🐻 空头研究员**")
            st.markdown(bear or "_（未发言）_")
        if judge:
            st.success("**🧑‍⚖️ 研究经理裁决 / 投资计划**")
            st.markdown(state.get("investment_plan") or judge)


def render_trader(state, ph):
    plan = (state.get("trader_investment_plan") or "").strip()
    with ph.container():
        if plan:
            st.markdown("**💼 交易员方案**")
            st.markdown(plan)
        else:
            st.caption("等待研究结论…")


def render_risk(state, ph):
    rds = state.get("risk_debate_state", {}) or {}
    agg = (rds.get("aggressive_history") or "").strip()
    con = (rds.get("conservative_history") or "").strip()
    neu = (rds.get("neutral_history") or "").strip()
    with ph.container():
        if not (agg or con or neu):
            st.caption("等待交易方案…")
            return
        a, b, c = st.columns(3)
        a.markdown("**🔥 激进**"); a.markdown(agg or "_…_")
        b.markdown("**🛡️ 保守**"); b.markdown(con or "_…_")
        c.markdown("**⚖️ 中立**"); c.markdown(neu or "_…_")


def render_final(state, ph):
    decision = (state.get("final_trade_decision") or "").strip()
    with ph.container():
        if decision:
            st.markdown("### 🎯 最终决策（投资组合经理）")
            st.markdown(decision)
        else:
            st.caption("等待风控辩论…")


def stage_status(state):
    """顶部流程状态条。"""
    analysts_done = sum(bool((state.get(k) or "").strip()) for _, _, k in ANALYSTS)
    ids = state.get("investment_debate_state", {}) or {}
    rds = state.get("risk_debate_state", {}) or {}
    steps = [
        (f"分析师 {analysts_done}/4", analysts_done == 4),
        ("研究辩论", bool((ids.get("judge_decision") or "").strip())),
        ("交易员", bool((state.get("trader_investment_plan") or "").strip())),
        ("风控辩论", bool((rds.get("judge_decision") or "").strip())),
        ("最终决策", bool((state.get("final_trade_decision") or "").strip())),
    ]
    return " ➜ ".join(f"{'✅' if done else '⏳'} {label}" for label, done in steps)


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------
if run:
    name2key = {"市场/技术分析师": "market", "情绪分析师": "social",
                "新闻分析师": "news", "基本面分析师": "fundamentals"}
    ui_cfg = {
        "ticker": ticker.strip(),
        "date_str": trade_date.strftime("%Y-%m-%d"),
        "asset_type": mkt["asset_type"],
        "provider": provider,
        "base_url": base_url or os.environ.get("TRADINGAGENTS_LLM_BACKEND_URL", ""),
        "api_key": api_key,
        "key_env": key_env,
        "deep_model": deep_model,
        "quick_model": quick_model,
        "output_language": "Chinese" if language == "中文" else "English",
        "debate_rounds": debate_rounds,
        "risk_rounds": risk_rounds,
        "selected_analysts": [name2key[a[0]] for a in ANALYSTS if a[0] in analysts_sel],
    }

    here = os.path.dirname(os.path.abspath(__file__))
    workdir = os.path.join(here, ".uiruns")
    os.makedirs(workdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cfg_path = os.path.join(workdir, f"cfg_{stamp}.json")
    progress_path = os.path.join(workdir, f"progress_{stamp}.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(ui_cfg, f, ensure_ascii=False)

    st.divider()
    status_ph = st.empty()
    status_ph.info(f"🔧 启动分析（独立进程）：{ticker} @ {ui_cfg['date_str']} · "
                   f"{provider}/{deep_model} · {mkt['data']}")

    # 在独立子进程运行图：akshare 内部用 py_mini_racer(V8)，在 Streamlit 脚本线程
    # 里初始化会与其原生库冲突直接崩进程；子进程主线程隔离后稳定。
    proc = subprocess.Popen(
        [sys.executable, os.path.join(here, "ui_worker.py"), cfg_path, progress_path],
        cwd=here,
    )

    st.subheader("① 分析师团队"); ph_analysts = st.empty()
    st.subheader("② 研究员辩论（多空）"); ph_debate = st.empty()
    st.subheader("③ 交易员"); ph_trader = st.empty()
    st.subheader("④ 风控辩论（激进/保守/中立）"); ph_risk = st.empty()
    st.subheader("⑤ 最终决策"); ph_final = st.empty()
    stats_ph = st.empty()

    def _read_progress():
        try:
            with open(progress_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001 — file may be mid-write/absent
            return None

    state, status = {}, "running"
    with st.spinner("多智能体分析进行中（独立进程，几十次 LLM 调用，约几分钟）…"):
        while True:
            s = _read_progress()
            if s is not None:
                state, status = s, s.get("status", "running")
            status_ph.info(stage_status(state))
            render_analysts(state, ph_analysts)
            render_debate(state, ph_debate)
            render_trader(state, ph_trader)
            render_risk(state, ph_risk)
            render_final(state, ph_final)
            stats_ph.caption(
                f"📊 LLM 调用 {state.get('llm_calls', 0)} · "
                f"输入 {state.get('tok_in', 0):,} / 输出 {state.get('tok_out', 0):,} tokens"
            )
            if status in ("done", "error"):
                break
            if proc.poll() is not None:
                s = _read_progress()
                if s is not None:
                    state, status = s, s.get("status", "running")
                if status not in ("done", "error"):
                    status = "error"
                    state = {**state, "error": f"worker 进程异常退出 (code {proc.returncode})"}
                break
            time.sleep(1.3)

    if status == "error":
        status_ph.error("运行出错")
        st.error(state.get("error", "未知错误"))
    else:
        status_ph.success(stage_status(state) + "  ——  ✅ 完成")
        decision = state.get("final_trade_decision") or ""
        if decision:
            st.download_button("⬇️ 下载最终决策", decision,
                               file_name=f"{ui_cfg['ticker']}_{ui_cfg['date_str']}_decision.md")
        st.caption("📁 完整报告树已保存到 ~/.tradingagents/logs/reports/ 下。")
else:
    st.info("👈 在左侧配置市场/标的和 API Key，点击「开始分析」。"
            "支持 A股 / 美股 / 港股 / 虚拟币；数据源会自动路由。")
