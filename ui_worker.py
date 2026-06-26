"""
UI worker — 在独立子进程里运行 TradingAgents 图，把进度写成 JSON。

必须独立进程：akshare 内部用 py_mini_racer(V8)，在 Streamlit 的脚本线程里
初始化会和其原生库冲突直接 FATAL 崩溃；放进子进程主线程就没问题。

用法：python ui_worker.py <config.json> <progress.json>
config.json 里是 UI 收集的配置；progress.json 由本进程原子覆盖写入。
"""
from __future__ import annotations

import json
import os
import sys
import time


def main():
    cfg_path, progress_path = sys.argv[1], sys.argv[2]
    with open(cfg_path, encoding="utf-8") as f:
        ui = json.load(f)

    # 运行期 env：key / 中转站
    if ui.get("api_key") and ui.get("key_env"):
        os.environ[ui["key_env"]] = ui["api_key"]
    if ui.get("base_url"):
        os.environ["TRADINGAGENTS_LLM_BACKEND_URL"] = ui["base_url"]
    else:
        os.environ.pop("TRADINGAGENTS_LLM_BACKEND_URL", None)  # don't leak .env relay to native providers

    from langchain_core.callbacks import BaseCallbackHandler

    from tradingagents.dataflows.cn_market import is_ashare, set_active_market, set_analysis_date
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    class Stats(BaseCallbackHandler):
        def __init__(self):
            self.llm_calls = 0
            self.tok_in = 0
            self.tok_out = 0

        def on_llm_end(self, response, **kwargs):
            self.llm_calls += 1
            try:
                u = (response.llm_output or {}).get("token_usage") or {}
                self.tok_in += u.get("prompt_tokens", 0) or u.get("input_tokens", 0) or 0
                self.tok_out += u.get("completion_tokens", 0) or u.get("output_tokens", 0) or 0
            except Exception:
                pass

    stats = Stats()

    cfg = DEFAULT_CONFIG.copy()
    cfg["llm_provider"] = ui["provider"]
    # Empty base_url -> None (use the provider's native endpoint). Never fall
    # back to DEFAULT_CONFIG's .env relay URL, or a native provider (DeepSeek/
    # OpenAI/...) would be sent to the relay and 401.
    cfg["backend_url"] = ui.get("base_url") or None
    cfg["deep_think_llm"] = ui["deep_model"]
    cfg["quick_think_llm"] = ui["quick_model"]
    cfg["output_language"] = ui["output_language"]
    cfg["max_debate_rounds"] = ui["debate_rounds"]
    cfg["max_risk_discuss_rounds"] = ui["risk_rounds"]
    cfg["streaming"] = True
    cfg["max_tokens"] = 16384
    cfg["request_timeout"] = 180
    cfg["max_retries"] = 6

    ticker = ui["ticker"]
    date_str = ui["date_str"]
    asset_type = ui["asset_type"]
    selected = tuple(ui["selected_analysts"]) or ("market", "social", "news", "fundamentals")

    DEBATE = ("investment_debate_state", "risk_debate_state")
    FIELDS = ("market_report", "sentiment_report", "news_report", "fundamentals_report",
              "investment_plan", "trader_investment_plan", "final_trade_decision")

    def slim(state, status, error=""):
        out = {"status": status, "error": error,
               "llm_calls": stats.llm_calls, "tok_in": stats.tok_in, "tok_out": stats.tok_out}
        for k in FIELDS:
            out[k] = state.get(k, "") or ""
        for k in DEBATE:
            d = state.get(k) or {}
            out[k] = {kk: (vv if isinstance(vv, str) else str(vv)) for kk, vv in d.items()}
        return out

    def write(state, status, error=""):
        # 绝不抛异常：Windows 上 reader 正打开目标文件时 os.replace 会 WinError 5，
        # 单次失败不该把整个分析进程搞崩 —— 重试，再不行就直接覆盖写（reader 容忍偶发半包）。
        payload = json.dumps(slim(state, status, error), ensure_ascii=False)
        tmp = progress_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:  # noqa: BLE001
            _direct_write(payload)
            return
        for _ in range(10):  # 原子替换，目标被占用时重试
            try:
                os.replace(tmp, progress_path)
                return
            except PermissionError:
                time.sleep(0.05)
            except Exception:  # noqa: BLE001
                break
        _direct_write(payload)  # 多次失败 -> 直接写主文件兜底
        try:
            os.remove(tmp)
        except Exception:  # noqa: BLE001
            pass

    def _direct_write(payload):
        try:
            with open(progress_path, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:  # noqa: BLE001
            pass

    last = {}
    try:
        ta = TradingAgentsGraph(selected_analysts=selected, debug=False,
                                config=cfg, callbacks=[stats])
        past = ta.memory_log.get_past_context(ticker)
        ictx = ta.resolve_instrument_context(ticker, asset_type)
        init = ta.propagator.create_initial_state(
            ticker, date_str, asset_type=asset_type, past_context=past, instrument_context=ictx)
        set_analysis_date(date_str)
        set_active_market("cn" if is_ashare(ticker) else None)
        last = init
        write(init, "running")

        for chunk in ta.graph.stream(init, **ta.propagator.get_graph_args()):
            last = chunk
            write(chunk, "running")

        try:
            ta.save_reports(last, ticker)
        except Exception:
            pass
        write(last, "done")
    except Exception as exc:  # noqa: BLE001
        import traceback
        write(last, "error", f"{exc}\n{traceback.format_exc()[-800:]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
