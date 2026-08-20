#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zhipu_analyze.py - 用智谱 GLM 解读狼大(啊狼) 发言 + 输出操作策略。

读取 scraper 产出的 data/wolf_posts.json, 调用智谱 chat API,
写出 data/wolf_analysis.json 供前端展示。

环境变量:
  ZHIPU_API_KEY   智谱 API Key (open.bigmodel.cn 获取, glm-4-flash 有免费额度)
  WOLF_MAX_POSTS  参与解读的最近发言条数 (默认 15)

没设 ZHIPU_API_KEY 时: 不调用模型, 直接产出 wolf_analysis.json 并标记
ai_enabled=false, 前端照常展示原始发言, 只是没有 AI 解读。

依赖: pip install requests
"""
import json
import os
import sys

import requests

API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = os.environ.get("ZHIPU_MODEL", "glm-4-flash")
MAX_POSTS = int(os.environ.get("WOLF_MAX_POSTS", "15"))

SYSTEM_PROMPT = (
    "你是 A 股资深复盘助手, 风格务实、反模板、不喊单。下面给用户(蓝筹长线交易者) "
    "提供基于某 NGA 大V发言的解读。严格按 JSON 输出, 字段: "
    "core_view(核心观点, 他在看多/看空什么、逻辑是什么), "
    "bias(多空倾向: bull/bear/neutral + 置信度 0-1), "
    "strategy(操作策略: 结合蓝筹长线、低吸分批, 给具体价位区间思路, 非喊单), "
    "risk(风险提示: 他的盲区/矛盾点), "
    "disclaimer(固定: 内容为 AI 基于发言的归纳, 非投资建议)。"
    "全部用简体中文, 不要多余解释。"
)


def load_posts(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    posts = d.get("posts", [])
    # 取最近 MAX_POSTS 条, 拼接标题+正文
    recent = posts[-MAX_POSTS:]
    blocks = []
    for i, p in enumerate(recent, 1):
        subj = p.get("subject", "")
        body = p.get("content", "")
        head = ("【%d】%s\n%s" % (i, subj, body)) if subj else ("【%d】\n%s" % (i, body))
        blocks.append(head)
    return d, "\n\n".join(blocks)


def call_zhipu(api_key, text):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "以下是 NGA 用户「啊狼」近期的发言原文:\n\n" + text},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": "Bearer " + api_key,
               "Content-Type": "application/json"}
    r = requests.post(API_URL, json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def normalize_analysis(a):
    """把智谱返回的任意键名/嵌套结构展平成前端 wolf.html 期望的格式。

    模型有时回英文键(core_view), 有时回中文键(看多/看空), 有时嵌套 dict,
    统一为纯字符串字段: core_view / bias{bias,confidence} / strategy / risk / disclaimer。
    """
    if not isinstance(a, dict):
        return a

    def flat(v, wrap):
        """取嵌套 dict 里的取值, dict 展平成 'k: v; k: v' 字符串。"""
        if isinstance(v, dict):
            parts = []
            for k, val in v.items():
                if val is None:
                    continue
                if isinstance(val, (dict, list)):
                    val = json.dumps(val, ensure_ascii=False)
                parts.append("%s: %s" % (str(k).strip("【】"), val))
            return "；".join(parts)
        if isinstance(v, list):
            return "；".join(str(x) for x in v)
        return str(v) if v is not None else ""

    out = {}
    # core_view: 兼容英文键 / 中文键 / 嵌套
    cv = a.get("core_view") or a.get("核心观点")
    if isinstance(cv, dict) and "看多/看空" in cv:
        # {"看多/看空": "看空", "逻辑": "..."} -> "看空；逻辑: ..."
        out["core_view"] = flat(cv, None)
    else:
        out["core_view"] = flat(cv, None)

    # bias: {"bias":"bear","confidence":0.7} 或 {"多空倾向":"bear","置信度":0.7}
    b = a.get("bias") or a.get("多空倾向")
    bias_val, conf = "", None
    if isinstance(b, dict):
        bias_val = b.get("bias") or b.get("多空倾向") or ""
        conf = b.get("confidence", b.get("置信度"))
    elif isinstance(b, str):
        bias_val = b
    out["bias"] = {"bias": str(bias_val or "neutral"),
                   "confidence": (float(conf) if conf is not None else None)}

    for src, dst in (("strategy", "strategy"), ("risk", "risk"),
                     ("disclaimer", "disclaimer"),
                     ("操作策略", "strategy"), ("风险提示", "risk"),
                     ("免责声明", "disclaimer")):
        if dst not in out and a.get(src) is not None:
            out[dst] = flat(a.get(src), None)
    return out


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    posts_path = os.path.join(base, "data", "wolf_posts.json")
    out_path = os.path.join(base, "data", "wolf_analysis.json")

    if not os.path.exists(posts_path):
        print("找不到 %s, 请先跑 wolf_fetch.py" % posts_path, file=sys.stderr)
        sys.exit(1)

    meta, text = load_posts(posts_path)
    api_key = os.environ.get("ZHIPU_API_KEY")

    result = {
        "tid": meta.get("tid"),
        "owner": meta.get("owner", "啊狼"),
        "fetched_at": meta.get("fetched_at"),
        "analyzed_at": "",
        "ai_enabled": bool(api_key),
        "model": MODEL if api_key else "",
        "analysis": None,
    }

    if not api_key:
        print("⚠ 未设 ZHIPU_API_KEY, 仅产出原始发言, 不调用智谱")
        result["note"] = "未配置智谱 API Key, 暂无 AI 解读"
    else:
        print("▶ 调用智谱 %s 解读 %d 条发言..." % (MODEL, len(text.split("【")) - 1))
        try:
            raw = call_zhipu(api_key, text)
            analysis = json.loads(raw)
            analysis = normalize_analysis(analysis)
        except Exception as e:  # noqa
            print("⚠ 智谱调用失败: %s" % e, file=sys.stderr)
            analysis = None
        result["analysis"] = analysis
        from datetime import datetime, timezone, timedelta
        result["analyzed_at"] = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("✅ 已写入 %s" % out_path)


if __name__ == "__main__":
    main()
