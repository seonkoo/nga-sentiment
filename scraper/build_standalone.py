#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_standalone.py - 把 wolf.html 打成"单文件自包含版"。

把 data/wolf_posts.json + data/wolf_analysis.json 内嵌进 wolf.html,
使其不依赖 data/ 目录也能独立跑(GitHub Pages 手机粘贴 1 个文件即上线)。
同时内置"下载 TXT"按钮(前端 Blob 生成, 无需服务器)。

用法:
  python build_standalone.py
输出: ../wolf.html (自包含版, 覆盖原 fetch 版)
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # nga-sentiment/
TEMPLATE = ROOT / "wolf.html"
POSTS = ROOT / "data" / "wolf_posts.json"
ANALYSIS = ROOT / "data" / "wolf_analysis.json"


def js_escape(s):
    """JS 字符串安全: 转义 </script、反斜杠、引号。"""
    s = s.replace("\\", "\\\\").replace("'", "\\'")
    return s.replace("</", "<\\/")


def main():
    if not (TEMPLATE.exists() and POSTS.exists() and ANALYSIS.exists()):
        print("缺文件: 需要 wolf.html + data/wolf_posts.json + data/wolf_analysis.json",
              file=__import__("sys").stderr)
        raise SystemExit(1)

    html = TEMPLATE.read_text(encoding="utf-8")
    posts_json = POSTS.read_text(encoding="utf-8").replace("</", "<\\/")
    ana_json = ANALYSIS.read_text(encoding="utf-8").replace("</", "<\\/")

    # 幂等: 先清掉之前注入的内嵌数据块(防止重复注入累积)
    html = re.sub(
        r"<script>\s*/\* 内嵌数据: build_standalone\.py 生成.*?</script>\s*",
        "", html, flags=re.S)

    # 1) 注入内嵌数据
    embed = ("<script>\n"
             "/* 内嵌数据: build_standalone.py 生成, 更新数据请重跑 */\n"
             "const EMBED_POSTS = " + posts_json + ";\n"
             "const EMBED_ANALYSIS = " + ana_json + ";\n"
             "</script>")
    html = html.replace("</head>", embed + "\n</head>")

    # 2) fetch 逻辑改为读内嵌数据
    html = html.replace(
        """const [posts, ana] = await Promise.all([
      getJSON("./data/wolf_posts.json").catch(()=>null),
      getJSON("./data/wolf_analysis.json").catch(()=>null),
    ]);""",
        "const posts = EMBED_POSTS || null;\n    const ana = EMBED_ANALYSIS || null;")

    # 3) 加"下载 TXT"按钮
    html = html.replace(
        '<button class="copy-btn" id="copyAll">📋 复制全部发言</button>',
        '<button class="copy-btn" id="copyAll">📋 复制全部发言</button> '
        '<button class="copy-btn" id="dlTxt">⬇️ 下载 TXT</button>')

    download_js = """
    const dl = document.getElementById("dlTxt");
    if (dl) dl.onclick = () => {
      const lines = [];
      lines.push("NGA 狼大发言合集");
      lines.push("来源: " + (posts.url||""));
      lines.push("采集: " + (posts.fetched_at||"") + " | 共 " + (posts.posts?.length||0) + " 条");
      lines.push("==============================================");
      (posts.posts||[]).forEach((p,i) => {
        lines.push("【" + (i+1) + "】" + (p.subject||""));
        lines.push(p.content);
        lines.push("");
        lines.push("----------------------------------------------");
        lines.push("");
      });
      const blob = new Blob([lines.join("\\n")], {type:"text/plain;charset=utf-8"});
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "wolf_posts.txt";
      a.click();
      URL.revokeObjectURL(a.href);
      dl.textContent = "已导出 ✅";
    };
"""
    html = html.replace("main();", "main();\n" + download_js)

    TEMPLATE.write_text(html, encoding="utf-8")
    size = os.path.getsize(TEMPLATE)
    print("✅ 已生成自包含 wolf.html (%.1f KB), 单文件无外部依赖" % (size / 1024))


if __name__ == "__main__":
    main()
