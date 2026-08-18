"""
NGA 股市情绪分析（板块情绪指数 + 狼楼风向）

任务：
1. 板块情绪指数：抓 stid=47206901 [股市]技术分析 板块当天新帖标题，
   关键词分类统计情绪（情绪兴奋/晒收益/看多/崩溃清仓/担忧看空/本人愤怒），
   输出 今日新发帖/今日总回复/提高因素/压低因素/情绪净分/情绪指数分/等级/饼图%。
2. 狼楼风向：抓 read.php?tid=47288722（楼主"啊狼"）当天发言，
   统计发言股市风向（看多/看空/中性 + 关键词），特别统计楼主"啊狼"的发言。

输出：data/nga_data.json（GitHub Pages 手机看板读取）

运行：
  python analyze.py            # cookie 有效直接分析；失效自动 iframe 登录
  python analyze.py --login    # 强制重新登录
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("nga-analyze")

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

# ============ 配置 ============
def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def today_str() -> str:
    return datetime.now().strftime("%m-%d")


# ============ 浏览器 ============
def make_driver(cfg: dict, headless: bool = True):
    opts = EdgeOptions()
    if headless:
        opts.add_argument("--headless=new")
    # 平台自适应：Windows 用配置路径；Linux CI 用系统 Edge（由 Selenium Manager 自动找驱动）
    edge_binary = cfg["browser"].get("edge_binary", "")
    if edge_binary and os.path.exists(edge_binary):
        opts.binary_location = edge_binary
    else:
        # Linux：优先环境变量，其次常见系统路径
        for cand in [os.environ.get("EDGE_BINARY", ""), "/usr/bin/microsoft-edge-stable",
                     "/usr/bin/microsoft-edge", "/opt/microsoft/msedge/msedge"]:
            if cand and os.path.exists(cand):
                opts.binary_location = cand
                break
    opts.add_argument(f"--window-size={cfg['browser']['window_size']}")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Edge(options=opts)
    driver.set_page_load_timeout(cfg["browser"]["page_load_timeout_sec"])
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"},
    )
    return driver


# ============ cookie ============
def cookies_path(cfg: dict) -> Path:
    return ROOT / cfg["session"]["cookies_path"]


def save_cookies(driver, cfg: dict) -> None:
    p = cookies_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"saved_at": datetime.now().isoformat(timespec="seconds"), "cookies": driver.get_cookies()}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info("已保存 %d 条 cookie", len(payload["cookies"]))


def apply_cookies(driver, cfg: dict) -> None:
    p = cookies_path(cfg)
    if not p.exists():
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    driver.get("https://ngabbs.com/")
    time.sleep(2)
    driver.delete_all_cookies()
    for c in payload["cookies"]:
        try:
            driver.add_cookie({
                "name": c["name"], "value": c["value"],
                "domain": c.get("domain", ".ngabbs.com"), "path": c.get("path", "/"),
            })
        except Exception:
            pass
    log.info("已注入 %d 条 cookie", len(payload["cookies"]))


def has_real_uid(driver) -> bool:
    for c in driver.get_cookies():
        if "uid" in c["name"].lower() and not str(c["value"]).startswith("guest"):
            return True
    return False


# ============ 登录（iframe + OCR） ============
def click_visible(driver, xpath_texts):
    for txt in xpath_texts:
        for el in driver.find_elements(By.XPATH, f"//*[contains(text(),'{txt}')]"):
            try:
                if el.is_displayed():
                    el.click()
                    return True
            except Exception:
                continue
    return False


def handle_alerts(driver, timeout=2):
    try:
        WebDriverWait(driver, timeout).until(EC.alert_is_present())
        alert = driver.switch_to.alert
        text = alert.text
        alert.accept()
        return text
    except Exception:
        return None


def login_via_iframe(driver, cfg: dict) -> bool:
    import base64
    import ddddocr
    ocr_default = ddddocr.DdddOcr(show_ad=False)
    ocr_beta = ddddocr.DdddOcr(show_ad=False, beta=True)
    # 优先读环境变量（CI secret），否则用 config.json
    user = os.environ.get("NGA_USER") or cfg["nga"].get("username", "")
    pwd = os.environ.get("NGA_PASS") or cfg["nga"].get("password", "")

    log.info("打开 NGA 首页并触发登录 iframe")
    driver.get("https://ngabbs.com/")
    time.sleep(10)
    driver.execute_script("if (window.commonui) commonui.loginUi();")
    time.sleep(5)

    target1 = None
    for f in driver.find_elements(By.TAG_NAME, "iframe"):
        if "login_ui" in (f.get_attribute("src") or ""):
            target1 = f
            break
    if not target1:
        log.error("找不到 login_ui iframe")
        return False
    driver.switch_to.frame(target1)
    time.sleep(3)

    target2 = None
    for f in driver.find_elements(By.TAG_NAME, "iframe"):
        if "account_copy" in (f.get_attribute("src") or ""):
            target2 = f
            break
    if not target2:
        log.error("找不到 account_copy iframe")
        return False
    driver.switch_to.frame(target2)
    time.sleep(4)

    try:
        driver.find_element(By.XPATH, "//*[contains(text(),'使用密码登录')]").click()
        time.sleep(3)
    except Exception:
        pass
    try:
        driver.find_element(By.ID, "name").send_keys(user)
        driver.find_element(By.ID, "password").send_keys(pwd)
    except Exception as e:
        log.warning("自动填账号失败: %s", e)

    click_visible(driver, ["登 录"])
    time.sleep(5)
    handle_alerts(driver, 1)

    def read_store():
        store = driver.execute_script(
            "return window.script_muti_get_var_store ? JSON.stringify(window.script_muti_get_var_store) : 'NONE';"
        )
        if store == "NONE":
            return None
        try:
            return json.loads(store)
        except Exception:
            return None

    def find_cap_src():
        for img in driver.find_elements(By.TAG_NAME, "img"):
            src = img.get_attribute("src") or ""
            if "login_check_code" in src or "captcha" in src.lower():
                return src
        return None

    def find_code_input(max_wait=3):
        deadline = time.time() + max_wait
        while time.time() < deadline:
            for el in driver.find_elements(By.CSS_SELECTOR, "input"):
                if "验证码" in (el.get_attribute("placeholder") or ""):
                    return el
            time.sleep(0.5)
        return None

    def fetch_cap_img(src):
        result = driver.execute_async_script("""
            const src = arguments[0];
            const done = arguments[1];
            fetch(src, {credentials: 'include'})
                .then(r => r.blob())
                .then(blob => new Promise((res, rej) => {
                    const fr = new FileReader();
                    fr.onload = () => res(fr.result);
                    fr.onerror = rej;
                    fr.readAsDataURL(blob);
                }))
                .then(dataUrl => done(dataUrl.split(',')[1]))
                .catch(e => done('ERROR:' + e.message));
        """, src)
        if result.startswith("ERROR"):
            raise RuntimeError(result)
        return base64.b64decode(result)

    logged_in = False
    for attempt in range(1, 21):
        handle_alerts(driver, 0.5)
        store = read_store()
        if store and "登录成功" in json.dumps(store, ensure_ascii=False):
            logged_in = True
            break
        cap_src = find_cap_src()
        code_input = find_code_input()
        if cap_src and code_input:
            try:
                img = fetch_cap_img(cap_src)
                r1 = ocr_default.classification(img)
                r2 = ocr_beta.classification(img)
            except Exception:
                time.sleep(1)
                continue
            cands = list(dict.fromkeys([r1, r2]))
            six = [c for c in cands if len(c) == 6 and c.isdigit()]
            candidate = (six + [c for c in cands if c not in six])[0] if cands else None
            if not candidate:
                click_visible(driver, ["换一个"])
                time.sleep(2)
                continue
            log.info("  验证码[%d] %s", attempt, candidate)
            handle_alerts(driver, 0.5)
            code_input = find_code_input() or code_input
            try:
                code_input.clear()
                code_input.send_keys(candidate)
            except Exception:
                continue
            time.sleep(0.4)
            click_visible(driver, ["继 续", "继续"])
            for _ in range(10):
                time.sleep(1.5)
                handle_alerts(driver, 1)
                store = read_store()
                if store:
                    s = json.dumps(store, ensure_ascii=False)
                    if "登录成功" in s:
                        logged_in = True
                        break
                    if store.get("error"):
                        break
            if logged_in:
                break
            click_visible(driver, ["换一个"])
            time.sleep(2)
        else:
            click_visible(driver, ["登 录"])
            time.sleep(4)
            handle_alerts(driver, 1)

    driver.switch_to.default_content()
    time.sleep(8)
    return logged_in or has_real_uid(driver)


# ============ 板块情绪 ============
def parse_board_posts(html_list, cfg: dict) -> list[dict]:
    """解析板块列表（每页单独解析后合并）：tid/标题/回复数/作者/发帖时间。"""
    posts = []
    seen = set()
    for html in html_list:
        soup = BeautifulSoup(html, "lxml")
        for row in soup.select("tr.topicrow"):
            tid_el = row.select_one("a.topic")
            if not tid_el:
                continue
            href = tid_el.get("href", "")
            m = re.search(r"tid=(\d+)", href)
            if not m:
                continue
            tid = m.group(1)
            title = tid_el.get_text(strip=True)
            if not title or len(title) < 2:
                continue
            if tid == "47206901" or title.startswith("[置顶]"):
                continue
            if tid in seen:
                continue
            seen.add(tid)
            post = {"tid": tid, "title": title}
            # 回复数
            rc = row.select_one("a.replies")
            post["replies"] = int(rc.get_text(strip=True)) if rc and rc.get_text(strip=True).isdigit() else None
            # 作者
            au = row.select_one("a.author")
            post["author"] = au.get_text(strip=True) if au else None
            # 发帖时间（title 属性是完整时间）
            pt = row.select_one("span.postdate")
            post["time"] = pt.get("title", "") if pt else ""
            posts.append(post)
    # 按时间倒序（新帖在前）
    posts.sort(key=lambda p: p.get("time") or "", reverse=True)
    return posts


def classify_title(title: str, cfg: dict) -> Optional[str]:
    cats = cfg["sentiment"]["categories"]
    ordered = sorted(cats.items(), key=lambda kv: kv[1]["priority"], reverse=True)
    for key, meta in ordered:
        for kw in meta["keywords"]:
            if kw in title:
                return key
    return None


def compute_index(new_posts: int, bullish: int, bearish: int, cfg: dict) -> dict:
    f = cfg["sentiment"]["index_formula"]
    k = float(f["net_score_k"])
    a = float(f["index_factor_a"])
    net = k * (bullish - bearish) / new_posts if new_posts else 0.0
    index_score = round(max(0.0, min(100.0, 50 + net * a)), 1)  # clamp 0-100
    level = "未知"
    for th in sorted(f["level_thresholds"], key=lambda x: x["min"], reverse=True):
        if index_score >= th["min"]:
            level = th["label"]
            break
    return {
        "net_score": round(net, 1),
        "index_score": index_score,
        "level": level,
    }


def analyze_board(driver, cfg: dict) -> dict:
    board_url = cfg["nga"]["board_url"]
    pages = int(cfg["nga"].get("board_pages", 1))
    log.info("=== 抓板块 %s (%d 页) ===", board_url, pages)
    html_list = []
    for page in range(1, pages + 1):
        url = f"{board_url}&page={page}" if page > 1 else board_url
        driver.get(url)
        time.sleep(6)
        html_list.append(driver.page_source)

    if cfg["output"].get("save_raw_html"):
        raw_path = ROOT / cfg["output"]["data_dir"] / cfg["output"]["raw_html_name"]
        raw_path.write_text("\n".join(html_list), encoding="utf-8")

    posts = parse_board_posts(html_list, cfg)
    log.info("解析到 %d 个帖子", len(posts))

    # 分类
    cats = cfg["sentiment"]["categories"]
    counts = {key: 0 for key in cats}
    classified_posts = []
    for p in posts:
        key = classify_title(p["title"], cfg)
        if key:
            counts[key] += 1
            p["category"] = key
            classified_posts.append(p)

    excitement = counts.get("excitement", 0)
    showoff = counts.get("showoff_gain", 0)
    bullish = counts.get("bullish", 0)
    panic = counts.get("panic_sell", 0)
    worry = counts.get("worry_bear", 0)
    anger = counts.get("anger_verified", 0)

    bullish_total = excitement + showoff + bullish
    bearish_total = panic + worry + anger
    # 今日新发帖 = 当天发的帖子（time 以今天日期开头）
    today = datetime.now().strftime("%y-%m-%d")
    today_posts = [p for p in posts if p.get("time", "").startswith(today)]
    new_posts = len(today_posts) if today_posts else len(posts)
    # 今日总回复 = 当天新帖回复数之和（避免统计历史大帖）
    total_replies = sum(p.get("replies") or 0 for p in today_posts) if today_posts else 0

    index = compute_index(new_posts, bullish_total, bearish_total, cfg)

    # 饼图百分比（五分类占比）
    pie_total = excitement + showoff + bullish + panic + worry + anger
    pie = {
        "excitement": round(excitement / pie_total * 100) if pie_total else 0,
        "showoff_gain": round(showoff / pie_total * 100) if pie_total else 0,
        "bullish": round(bullish / pie_total * 100) if pie_total else 0,
        "panic_sell": round(panic / pie_total * 100) if pie_total else 0,
        "worry_bear": round(worry / pie_total * 100) if pie_total else 0,
        "anger_verified": round(anger / pie_total * 100) if pie_total else 0,
    }

    return {
        "board_url": board_url,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": today_str(),
        "metrics": {
            "today_new_posts": new_posts,
            "today_total_replies": total_replies,
            "bullish_total": bullish_total,
            "excitement": excitement,
            "showoff_gain": showoff,
            "bullish": bullish,
            "bearish_total": bearish_total,
            "panic_sell": panic,
            "worry_bear": worry,
            "anger_verified": anger,
        },
        "index": index,
        "pie": pie,
        "classified_posts": classified_posts,
        "all_posts": posts,
    }


# ============ 狼楼风向 ============
def fetch_wolf_pages(driver, tid: str, max_pages: int = 15) -> list[str]:
    """抓狼楼最新几页（狼楼按正序，当天发言在尾页）。
    首页取总页数，然后抓最后 max_pages 页（含 start 页，range stop 需 -1）。"""
    # 首页：取总页数
    driver.get(f"https://ngabbs.com/read.php?tid={tid}")
    time.sleep(5)
    first_html = driver.page_source
    page_nums = [int(p) for p in re.findall(r"read\.php\?tid=\d+[^\"']*page=(\d+)", first_html)]
    total = max(page_nums) if page_nums else 1
    log.info("狼楼总页数: %d", total)

    # 只抓尾页（首页是 7 月旧帖，不解析）
    html_list = []
    start = max(total - max_pages + 1, 1)
    for p in range(total, start - 1, -1):
        driver.get(f"https://ngabbs.com/read.php?tid={tid}&page={p}")
        time.sleep(5)
        html_list.append(driver.page_source)
    log.info("已抓尾页: %d ~ %d（共 %d 页）", start, total, len(html_list))
    return html_list


def parse_post_replies(html_list) -> list[dict]:
    """解析帖子内每楼（多页独立解析后合并）：作者/uid/内容/时间/是否楼主。

    NGA 结构：每楼 <a id="postauthorN" href="...uid=XXX"> + <span id="postdateN">时间</span>
    + <span id="postcontentN" class="postcontent">内容；
    用户名映射在 commonui.userInfo.setAll({uid:{username:...}}) JSON 里。
    """
    replies = []
    for html in html_list:
        # uid → username 映射
        uid_map = {}
        m = re.search(r"commonui\.userInfo\.setAll\(\s*(\{.*?\})\s*\)", html, re.S)
        if m:
            try:
                data = json.loads(m.group(1))
                for uid, info in data.items():
                    uid_map[str(uid)] = info.get("username", "")
            except Exception:
                pass

        soup = BeautifulSoup(html, "lxml")
        for content_el in soup.select("[id^='postcontent']"):
            el_id = content_el.get("id", "")
            if "andsubject" in el_id or not el_id[len("postcontent"):].isdigit():
                continue  # 跳过 postcontentandsubject 容器
            content = content_el.get_text("\n", strip=True)
            if not content or len(content) < 2:
                continue
            n = el_id.replace("postcontent", "")
            author_el = soup.select_one(f"a[id='postauthor{n}']")
            uid = None
            if author_el:
                m2 = re.search(r"uid=(\d+)", author_el.get("href", ""))
                if m2:
                    uid = m2.group(1)
            author = uid_map.get(str(uid), "") if uid else ""
            time_el = soup.select_one(f"span[id='postdate{n}']")
            t = time_el.get_text(strip=True) if time_el else ""
            replies.append({
                "author": author,
                "uid": uid,
                "content": content,
                "time": t,
                "is_owner": (uid == "150058") or ("阿狼" in author),
            })
    # 按时间倒序（最新在前）
    replies.sort(key=lambda r: r.get("time") or "", reverse=True)
    return replies


def analyze_wolf_house(driver, cfg: dict) -> dict:
    wolf_cfg = cfg.get("wolf_house", {})
    tid = wolf_cfg["tid"]
    max_pages = int(wolf_cfg.get("max_pages", 3))
    owner = wolf_cfg.get("owner_keywords", ["啊狼"])
    log.info("=== 抓狼楼 read.php?tid=%s ===", tid)
    html_list = fetch_wolf_pages(driver, tid, max_pages)
    (ROOT / cfg["output"]["data_dir"] / "nga_wolf_house.html").write_text(
        "\n".join(html_list), encoding="utf-8")

    replies = parse_post_replies(html_list)
    log.info("解析到 %d 楼发言", len(replies))

    # 过滤今天（按时间戳）
    today_full = datetime.now().strftime("%Y-%m-%d")
    today_replies = [r for r in replies if r.get("time", "").startswith(today_full)]
    scope = today_replies if today_replies else replies
    log.info("其中今天发言 %d 楼", len(today_replies))

    # 风向分类（基于发言内容关键词，口语化实盘用语）
    bull_kw = [
        "看多", "看涨", "做多", "买入", "加仓", "抄底", "上车", "低吸", "补仓",
        "涨", "突破", "起飞", "满仓", "重仓", "梭哈", "大肉", "涨停", "翻红",
        "拉升", "反攻", "买", "干", "冲", "机会", "看好", "逻辑", "新周期",
        "见底", "筑底", "企稳", "反转", "爆发", "牛市", "加仓了", "买入了",
        "回血", "回本", "赚", "盈利", "吃肉", "新低买入", "持有", "拿着"
    ]
    bear_kw = [
        "看空", "做空", "卖出", "减仓", "清仓", "割肉", "暴跌", "跳水",
        "跌", "崩", "逃顶", "止损", "套", "完蛋", "亏", "跑", "撤退",
        "危险", "凉了", "出货", "砸盘", "下杀", "破位", "新低", "熊市",
        "恐慌", "崩盘", "腰斩", "血亏", "亏麻", "退市", "归零", "利好出尽"
    ]
    def direction(text: str) -> str:
        # 看空优先（谨慎）
        if any(k in text for k in bear_kw):
            return "bear"
        if any(k in text for k in bull_kw):
            return "bull"
        return "neutral"

    stats = {"bull": 0, "bear": 0, "neutral": 0}
    owner_posts = []
    for r in scope:
        d = direction(r["content"])
        r["direction"] = d
        stats[d] += 1
        if r.get("is_owner"):
            owner_posts.append(r)

    # 楼主特别统计
    owner_stats = {"bull": 0, "bear": 0, "neutral": 0}
    for p in owner_posts:
        owner_stats[p["direction"]] += 1

    # 热词（全部发言 + 楼主发言）
    def top_keywords(texts, n=10):
        from collections import Counter
        c = Counter()
        for t in texts:
            for kw in bull_kw + bear_kw:
                if kw in t:
                    c[kw] += 1
        return c.most_common(n)

    all_texts = [r["content"] for r in scope]
    owner_texts = [p["content"] for p in owner_posts]

    return {
        "tid": tid,
        "url": f"https://ngabbs.com/read.php?tid={tid}",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": today_str(),
        "today_replies": len(today_replies),
        "total_replies": len(replies),
        "direction_stats": stats,
        "owner_direction_stats": owner_stats,
        "owner_posts": owner_posts[:20],
        "top_keywords": top_keywords(all_texts),
        "owner_top_keywords": top_keywords(owner_texts),
    }


# ============ main ============
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="强制重新登录")
    args = ap.parse_args()

    cfg = load_config()
    log.info("=" * 50)
    log.info("NGA 股市情绪分析 - 启动")
    log.info("=" * 50)

    os.makedirs(ROOT / cfg["output"]["data_dir"], exist_ok=True)

    driver = make_driver(cfg, headless=cfg["browser"]["headless"])
    try:
        if args.login:
            ok = login_via_iframe(driver, cfg)
            if ok:
                save_cookies(driver, cfg)
            else:
                log.error("强制登录失败")
                return 2
        else:
            apply_cookies(driver, cfg)
            driver.get("https://ngabbs.com/")
            time.sleep(4)
            if not has_real_uid(driver):
                log.info("cookie 失效，转 iframe 登录")
                ok = login_via_iframe(driver, cfg)
                if not ok:
                    log.error("登录失败")
                    return 2
                save_cookies(driver, cfg)
            else:
                log.info("cookie 有效，直接分析")

        result = {}
        result["board"] = analyze_board(driver, cfg)
        result["wolf_house"] = analyze_wolf_house(driver, cfg)

        out_path = ROOT / cfg["output"]["data_dir"] / cfg["output"]["json_name"]
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        log.info("已写入 %s", out_path)

        # 同源再写一份 data/nga_data.js（window.NGA_DATA = {...}），
        # 让 index.html 通过 <script src> 加载——file:// 双击即用，
        # 浏览器对 file:// 下的 fetch 默认禁用，必须走 <script>。
        js_path = out_path.with_suffix(".js")
        js_body = (
            "// Auto-generated by analyze.py — do not edit by hand.\n"
            "// Read by index.html via <script src>; file:// and http(s):// both work.\n"
            f"window.NGA_DATA = {json.dumps(result, ensure_ascii=False)};\n"
        )
        js_path.write_text(js_body, encoding="utf-8")
        log.info("已写入 %s（%d bytes）", js_path, len(js_body.encode("utf-8")))

        # 终端摘要
        b = result["board"]
        m = b["metrics"]
        ix = b["index"]
        log.info("-" * 50)
        log.info("板块情绪: 新发帖=%d 总回复=%d 提高=%d(兴奋%d/晒%d/看多%d) 压低=%d(崩%d/忧%d/怒%d)",
                 m["today_new_posts"], m["today_total_replies"],
                 m["bullish_total"], m["excitement"], m["showoff_gain"], m["bullish"],
                 m["bearish_total"], m["panic_sell"], m["worry_bear"], m["anger_verified"])
        log.info("指数: 净分=%.1f 指数分=%.1f 等级=%s", ix["net_score"], ix["index_score"], ix["level"])
        w = result["wolf_house"]
        log.info("狼楼: %d 楼 看多%d/看空%d/中性%d | 楼主发言 %d 条 看多%d/看空%d",
                 w["total_replies"], w["direction_stats"]["bull"], w["direction_stats"]["bear"],
                 w["direction_stats"]["neutral"], len(w["owner_posts"]),
                 w["owner_direction_stats"]["bull"], w["owner_direction_stats"]["bear"])
        log.info("=" * 50)
        return 0
    except Exception as e:
        log.exception("运行异常: %s", e)
        return 1
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())