# TradingAgents Web UI

一个 Streamlit 可视化界面，完整展示多智能体分析流程：选标的 → 配 API key →
逐 agent 实时看分析/辩论/协作 → 最终决策报告。

## 运行

```bash
# 在仓库目录、用装好依赖的环境
pip install streamlit akshare markdown   # 若尚未安装（markdown 用于导出 HTML 报告）
streamlit run app.py
# 或： .\.venv\Scripts\streamlit run app.py
```

浏览器打开 `http://localhost:8501`。

## 功能

- **两个页面**（顶部「📈 行情 / 🔬 智能分析」切换）：
  - **行情页**（打开默认）：选市场 → 看自选 + 热门标的的**实时报价**（名称/价格/涨跌幅），
    点任一行 **🔬 分析** 即对该标的启动分析。A股实时报价走 eastmoney 直连，
    美股/港股/币走 yfinance；每 30 秒缓存，可手动刷新。
  - **智能分析页**：逐 agent 实时可视化（见下）。
- **市场 4 大类切换**：🇨🇳 A股 · 🇺🇸 美股 · 🇭🇰 港股 · ₿ 虚拟币（分段按钮，非下拉）。
  - 数据源按市场**自动路由**：A股→akshare（行情/财报/新闻/宏观/千股千评情绪），
    美股/港股/币→yfinance（+Reddit/StockTwits/Polymarket/FRED）。
- **按名称搜标的**：输入「茅台 / 智谱 / 2513 / 苹果 / NVDA」即出最匹配的几只，点选自动填代码。
  - A股 / 港股 / 美股都是**全市场**可搜（A股≈5500、港股≈4700、美股≈13500 只），按天缓存。
  - A股名单走交易所官方源（子进程 akshare，首次约 20 秒）；港股/美股名单直连 eastmoney
    分页（首次约 15 / 35 秒，之后秒开）。代码自动转 Yahoo 格式（如 `02513`→`2513.HK`）。
- **⭐ 自选库**：收藏关注的标的（**按名称显示**），可折叠列表（标的多了不占地方），
  点一下即载入（市场 + 代码一起切好），✕ 删除。持久化到 `~/.tradingagents/watchlist.json`。
- **LLM / API Key 配置**：provider（默认 DeepSeek；含 Anthropic 格式中转站）、Base URL、
  API Key、深/快模型（按 provider **下拉选择不手填**）、辩论轮数、输出语言。
  表单会**自动预填** `.env` 里已有的值（key 留空即用 `.env`）。
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
- **行情 / 名称数据层**（`ui_data.py`）刻意**不 import akshare**（同样为避开 py_mini_racer）：
  A股名称/报价直连 eastmoney（纯 requests），美股/港股/币用 yfinance。唯一需要 akshare 的
  「A股代码↔名称全表」由 `cn_list_worker.py` **子进程**用交易所官方源拉取、按天缓存到
  `~/.tradingagents/cn_stocks.json`。
- 中转站健壮性（streaming / max_tokens / timeout / retries）由 `.env` 的
  `TRADINGAGENTS_*` 驱动，UI 自动带上。

## 注意

- 一次完整分析有**几十次 LLM 调用**、消耗真实 token，约几分钟。**分析期间请保持页面打开**
  （子进程会继续跑并存报告，但页面关闭后 UI 不再刷新当前进度）。
- **A股数据源 akshare 偶发不稳定**（免费抓取，eastmoney 间歇性掉连接）：已加重试 +
  per-call 超时 + OHLCV 缓存兜底；遇到不稳定时段，A股部分数据可能稀薄或耗时较长。
  美股/港股/虚拟币走 yfinance，稳定。
