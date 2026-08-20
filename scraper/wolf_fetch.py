#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wolf_fetch.py - 免登录采集 NGA 狼楼(楼主 啊狼) 发言。

原理(已在沙箱验证):
  NGA 对游客直接访问 read.php 返回 ERROR:15 拦截页, 但拦截页自带一段 JS:
  种 guestJs cookie -> 300ms 后 location.replace 重载本页 -> 重载后放游客进帖。
  用 headless Chrome 的 --dump-dom + --virtual-time-budget 真实执行这段 JS,
  即可零登录、零验证码拿到完整帖子 DOM。

用法:
  python wolf_fetch.py
  python wolf_fetch.py --tid 47288722 --out ../data/wolf_posts.json
  WOLF_TID=47288722 python wolf_fetch.py

依赖: 本机需有 google-chrome / chromium。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta

CHROME_BINS = [
    "google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
]
DEFAULT_TID = "47288722"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.30 NetType/WIFI Language/zh_CN")


def find_chrome():
    from shutil import which
    for b in CHROME_BINS:
        p = which(b)
        if p:
            return p
    for p in ("/usr/bin/google-chrome-stable", "/usr/bin/chromium"):
        if os.path.exists(p):
            return p
    return None


def dump_dom(url, chrome, timeout=60):
    """用 headless chrome 执行 JS 后导出 DOM; 返回 HTML 文本。"""
    fd, path = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    cmd = [
        chrome, "--headless", "--disable-gpu", "--no-sandbox",
        "--disable-dev-shm-usage", "--virtual-time-budget=8000",
        "--user-agent=" + UA, "--dump-dom", url,
    ]
    try:
        with open(path, "w", encoding="utf-8", errors="ignore") as fh:
            subprocess.run(cmd, stdout=fh, stderr=subprocess.DEVNULL,
                           timeout=timeout, check=True)
    except subprocess.TimeoutExpired:
        print("chrome 超时", file=sys.stderr)
    with open(path, encoding="utf-8", errors="ignore") as fh:
        html = fh.read()
    os.remove(path)
    return html


def parse_posts(html):
    """抽出所有楼层帖子并按楼主 uid 过滤(只保留楼主发言)。

    NGA 帖子页每个楼层带 id="postauthorN" 指向作者 uid, 楼主(主楼)是
    idx=0 的那位。个人楼里粉丝会回帖"前排", 必须按 uid 过滤。
    """
    def clean(s):
        s = re.sub(r"<br\s*/?>", "\n", s)
        s = re.sub(r"<[^>]+>", "", s)
        s = s.replace("&nbsp;", " ").replace("&amp;", "&")
        s = s.replace("&lt;", "<").replace("&gt;", ">")
        return s.strip()

    subj = dict(re.findall(r'id="postsubject(\d+)"[^>]*>(.*?)</h3>', html, re.S))
    cont = dict(re.findall(r'id="postcontent(\d+)"[^>]*>(.*?)</(?:p|span)>', html, re.S))
    # 楼层 idx -> 作者 uid (DOM: href="...uid=XXX" id="postauthorN")
    au = dict((int(n), int(u)) for u, n in
              re.findall(r'href="[^"]*uid=(\d+)"[^>]*id="postauthor(\d+)"', html))
    if not au and cont:
        au = {n: 0 for n in cont}

    posts = []
    for n in sorted(set(subj) | set(cont), key=lambda x: int(x)):
        posts.append({
            "idx": int(n),
            "uid": au.get(int(n), 0),
            "subject": clean(subj.get(n, "")),
            "content": clean(cont.get(n, "")),
        })
    posts = [p for p in posts if p["content"] or p["subject"]]

    owner_uid = posts[0]["uid"] if posts else 0
    owner_only = [p for p in posts if p["uid"] == owner_uid]
    # 保底: 过滤后为空则退回全量(如 DOM 解析 uid 失败), 并标记
    if owner_only and len(owner_only) < len(posts):
        return owner_only, owner_uid, len(posts) - len(owner_only)
    return posts, owner_uid, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tid", default=os.environ.get("WOLF_TID", DEFAULT_TID))
    ap.add_argument("--authorid", type=int, default=150058,
                    help="只看楼主参数(默认 150058=啊狼; 0=不过滤)")
    ap.add_argument("--out", default="../data/wolf_posts.json")
    ap.add_argument("--txt", default=None,
                    help="额外导出合并 TXT 路径, 如 ../data/wolf_posts.txt")
    ap.add_argument("--chrome", default=None)
    args = ap.parse_args()

    chrome = args.chrome or find_chrome()
    if not chrome:
        print("找不到 chrome/chromium, 请安装或 --chrome 指定路径", file=sys.stderr)
        sys.exit(1)

    url = "https://ngabbs.com/read.php?tid=" + args.tid
    if args.authorid > 0:
        url += "&authorid=%d" % args.authorid
    print("抓取 " + url + " (headless chrome 免登录)")
    html = dump_dom(url, chrome)
    if "访客不能直接访问" in html:
        print("仍被拦截(guestJs 重载未生效, 可能需调 virtual-time-budget)",
              file=sys.stderr)
        sys.exit(1)
    posts, owner_uid, skipped = parse_posts(html)
    print("解析到 " + str(len(posts)) + " 条发言" +
          ("(已滤掉 %d 条粉丝回帖)" % skipped if skipped else ""))
    tz8 = timezone(timedelta(hours=8))
    fetched_at = datetime.now(tz8).strftime("%Y-%m-%d %H:%M:%S")
    out = {
        "tid": args.tid,
        "url": url,
        "fetched_at": fetched_at,
        "owner": "啊狼",
        "owner_uid": owner_uid,
        "posts": posts,
    }
    out_dir = os.path.dirname(args.out) or "."
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("已写入 " + args.out)

    if args.txt:
        export_txt(out, args.txt)
        print("已写入 " + args.txt)

    if posts:
        print("--- 首条预览 ---")
        print("标题:", posts[0]["subject"])
        print("正文:", posts[0]["content"][:120])


def export_txt(data, path):
    """把发言合集导出为可下载 TXT。"""
    lines = []
    sep = "=" * 46
    lines.append(sep)
    lines.append("NGA 狼大发言合集")
    lines.append("来源: %s" % data["url"])
    lines.append("抓取时间: %s" % data["fetched_at"])
    lines.append("楼主: %s (uid=%s) | 共 %d 条发言" %
                 (data["owner"], data.get("owner_uid", "?"), len(data["posts"])))
    lines.append(sep)
    lines.append("")
    for i, p in enumerate(data["posts"], 1):
        lines.append("【%d】" % i)
        if p.get("subject"):
            lines.append("标题: %s" % p["subject"])
        lines.append(p["content"])
        lines.append("")
        lines.append("-" * 46)
        lines.append("")
    out_dir = os.path.dirname(path) or "."
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    main()
