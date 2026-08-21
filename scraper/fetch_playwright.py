#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_playwright.py - playwright 真浏览器版狼楼抓取器 (Actions 用)。

与 wolf_fetch.py 等价, 但用 playwright 完整执行 JS 并等待 guestJs 自动重载
(拦截页种 cookie -> 300ms 后 location.replace), 用于 GitHub Actions 环境
(headless --dump-dom 在 Actions IP 上拿不到帖子 DOM)。

用法:
  python fetch_playwright.py --out ../data/wolf_posts.json --txt ../data/wolf_posts.txt
依赖: pip install playwright && playwright install chromium
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.30 NetType/WIFI Language/zh_CN")


def fetch_all(tid, authorid, chrome_path=None, max_pages=0):
    from wolf_fetch import parse_posts  # 复用解析逻辑
    from playwright.sync_api import sync_playwright

    base = "https://ngabbs.com/read.php?tid=" + str(tid)
    if authorid:
        base += "&authorid=%d" % authorid

    all_posts = {}
    owner_uid = 0
    empty_streak = 0
    page = 1
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]}
        if chrome_path:
            launch["executable_path"] = chrome_path
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 390, "height": 844})
        pg = ctx.new_page()
        while True:
            url = base if page == 1 else base + "&page=%d" % page
            try:
                pg.goto(url, timeout=45000, wait_until="domcontentloaded")
                pg.wait_for_timeout(2500)  # 等 guestJs 自动重载放行
                html = pg.content()
            except Exception as e:  # noqa
                print("  第 %d 页异常: %s" % (page, e), file=sys.stderr)
                empty_streak += 1
                if empty_streak >= 2 or (max_pages and page >= max_pages):
                    break
                page += 1
                continue
            posts_page, oid, skipped = parse_posts(html)
            if not owner_uid:
                owner_uid = oid
            new_cnt = 0
            for post in posts_page:
                if post["idx"] not in all_posts:
                    all_posts[post["idx"]] = post
                    new_cnt += 1
            print("  第 %d 页: %d 条(新 %d, 滤粉丝 %d) size=%d" %
                  (page, len(posts_page), new_cnt, skipped, len(html)))
            if new_cnt == 0:
                empty_streak += 1
                if empty_streak >= 2:
                    break
            else:
                empty_streak = 0
            if max_pages and page >= max_pages:
                break
            if page >= 60:
                break
            page += 1
        browser.close()
    return [all_posts[k] for k in sorted(all_posts)], owner_uid, page


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tid", default=os.environ.get("WOLF_TID", "47288722"))
    ap.add_argument("--authorid", type=int, default=150058)
    ap.add_argument("--pages", type=int, default=0)
    ap.add_argument("--out", default="../data/wolf_posts.json")
    ap.add_argument("--txt", default=None)
    ap.add_argument("--chrome", default=None,
                    help="chromium 可执行路径 (Actions 上 playwright 自带时留空)")
    args = ap.parse_args()

    posts, owner_uid, pages = fetch_all(args.tid, args.authorid,
                                        chrome_path=args.chrome, max_pages=args.pages)
    print("共抓 %d 页, 合并去重后 %d 条发言" % (pages, len(posts)))
    if not posts:
        print("❌ 抓取结果为空(NGA 拦截), 退出避免覆盖已有数据", file=sys.stderr)
        sys.exit(2)

    tz8 = timezone(timedelta(hours=8))
    base = "https://ngabbs.com/read.php?tid=" + args.tid
    if args.authorid:
        base += "&authorid=%d" % args.authorid
    out = {
        "tid": args.tid,
        "url": base,
        "fetched_at": datetime.now(tz8).strftime("%Y-%m-%d %H:%M:%S"),
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
        from wolf_fetch import export_txt
        export_txt(out, args.txt)
        print("已写入 " + args.txt)


if __name__ == "__main__":
    main()
