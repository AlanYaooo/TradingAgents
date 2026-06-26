"""
子进程：用 akshare 取 A股「代码↔名称」全表，写成 JSON。

为何子进程：akshare 内部用 py_mini_racer(V8)，在 Streamlit 脚本线程里初始化会
FATAL 崩进程；放到独立子进程主线程就安全。本表按天缓存，每天首次用到时才刷新。

用法：python cn_list_worker.py <out.json>
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

# 除权(XD)/除息(XR)/除权除息(DR) 是临时前缀，次日消失，剥掉让名称干净可搜。
_PREFIX = re.compile(r"^(XD|XR|DR)")


def _clean(name: str) -> str:
    n = "".join(str(name).split())  # 去掉所有空白（含全角空格）
    return _PREFIX.sub("", n) or n


def main() -> None:
    out_path = sys.argv[1]
    # A股官方源 / eastmoney 直连（绕过 Clash 代理）
    cn = ("sse.com.cn,query.sse.com.cn,szse.cn,bse.cn,eastmoney.com,push2.eastmoney.com,"
          "80.push2.eastmoney.com,quote.eastmoney.com,hq.sinajs.cn")
    cur = os.environ.get("NO_PROXY", "")
    os.environ["NO_PROXY"] = (cur + "," + cn).strip(",")
    os.environ["no_proxy"] = os.environ["NO_PROXY"]

    import akshare as ak

    # 官方交易所源 -> 干净全名。注意：SH 的「证券简称」被截断到 6 字（"XD贵州茅"），
    # 用「证券全称」才完整（"贵州茅台"）；stock_info_a_code_name 同样是截断名，弃用。
    sources = [
        (lambda: ak.stock_info_sh_name_code(symbol="主板A股"), "证券代码", "证券全称"),
        (lambda: ak.stock_info_sh_name_code(symbol="科创板"), "证券代码", "证券全称"),
        (ak.stock_info_sz_name_code, "A股代码", "A股简称"),
        (ak.stock_info_bj_name_code, "证券代码", "证券简称"),
    ]
    seen: dict[str, str] = {}
    for fn, ccol, ncol in sources:
        try:
            df = fn()
        except Exception:  # noqa: BLE001
            continue
        for r in df.to_dict("records"):
            code = str(r.get(ccol, "")).strip()
            name = _clean(r.get(ncol, ""))
            if code and name and code not in seen:
                seen[code] = name
    lst = [{"code": c, "name": n} for c, n in seen.items()]
    if not lst:
        sys.exit(2)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"day": time.strftime("%Y%m%d"), "list": lst}, f, ensure_ascii=False)
    os.replace(tmp, out_path)  # 原子替换


if __name__ == "__main__":
    main()
