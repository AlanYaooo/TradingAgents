# TradingAgents Web UI

一个 Streamlit 可视化界面，完整展示多智能体分析流程：选标的 → 配 API key →
逐 agent 实时看分析/辩论/协作 → 最终决策报告。

## 运行

```bash
# 在仓库目录、用装好依赖的环境
pip install streamlit akshare        # 若尚未安装
streamlit run app.py
# 或： .\.venv\Scripts\streamlit run app.py
```

浏览器打开 `http://localhost:8501`。

## 功能

- **市场 / 资产选择**：🇨🇳 A股 · 🇺🇸 美股 · 🇭🇰 港股 · ₿ 虚拟币
  - 数据源按市场**自动路由**：A股→akshare（行情/财报/新闻/宏观/千股千评情绪），
    美股/港股/币→yfinance（+Reddit/StockTwits/Polymarket/FRED）。
  - 代码写法随市场，如 `600519.SS`、`AAPL`、`0700.HK`、`BTC-USD`。
- **LLM / API Key 配置**：provider（含 Anthropic 格式中转站）、Base URL、API Key、
  深/快模型、辩论轮数、输出语言。表单会**自动预填** `.env` 里已有的值（key 留空即用 `.env`）。
- **逐 agent 实时可视化**：流程状态条 + 五个阶段
  1. 分析师团队（市场/情绪/新闻/基本面，各自取数产报告）
  2. 研究员辩论（多头🐂 vs 空头🐻，研究经理裁决）
  3. 交易员（拟定带价位的交易方案）
  4. 风控辩论（激进/保守/中立三方）
  5. 投资组合经理（最终买/卖/持有 + 仓位 + 止损）
- **实时统计**：LLM 调用次数、token 用量。
- **下载**：最终决策可下载；完整报告树存到 `~/.tradingagents/logs/reports/`。

## 架构说明

- 分析在**独立子进程**（`ui_worker.py`）里运行，进度以 JSON 写盘、Streamlit 轮询渲染。
  这是必须的：akshare 内部用 `py_mini_racer`(V8)，在 Streamlit 的脚本线程里初始化会
  与其原生库冲突直接崩进程；放进子进程主线程后稳定。
- 中转站健壮性（streaming / max_tokens / timeout / retries）由 `.env` 的
  `TRADINGAGENTS_*` 驱动，UI 自动带上。

## 注意

- 一次完整分析有**几十次 LLM 调用**、消耗真实 token，约几分钟。**分析期间请保持页面打开**
  （子进程会继续跑并存报告，但页面关闭后 UI 不再刷新当前进度）。
- **A股数据源 akshare 偶发不稳定**（免费抓取，eastmoney 间歇性掉连接）：已加重试 +
  per-call 超时 + OHLCV 缓存兜底；遇到不稳定时段，A股部分数据可能稀薄或耗时较长。
  美股/港股/虚拟币走 yfinance，稳定。
