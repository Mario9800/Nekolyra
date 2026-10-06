#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鸣潮 WIKI  ->  QQ机器人知识库   一键脚本
================================================

这是给"你自己运行"用的脚本：全程本地执行，不调用任何 AI / 大模型接口，
所以不消耗你的 API 余额。只有抓取公开 wiki 和自己机器人的本地接口。

运行方式（在 D:\\ai\\qq_bot 目录下）：
    python wiki_to_kb.py              # 增量更新（推荐）：只抓新页面，导入新条目
    python wiki_to_kb.py --crawl      # 强制重新爬取所有页面
    python wiki_to_kb.py --dry-run    # 只解析和统计，不导入
    python wiki_to_kb.py --no-crawl   # 不联网，只用本地缓存重新解析

依赖：只用 Python 标准库 + 本目录下的 _build_wiki_entries.py（解析器）。
     不需要 httpx / requests。

免费说明：
  - 抓取 wiki.biligame.com 是公开页面，无 API 费用
  - 导入走本机 127.0.0.1:8080 的管理接口，不涉及外部服务
"""
import argparse
import concurrent.futures
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

# ============================ 配置区 ============================
BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "_wiki_cache")
ENTRIES_JSON = os.path.join(BASE, "_wiki_entries.json")
BUILDER = os.path.join(BASE, "_build_wiki_entries.py")

API_WIKI = "https://wiki.biligame.com/wutheringwaves/api.php"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"

# 要抓的分类（左边是本站分类名，右边是知识库里的归类）
CATEGORIES = ["共鸣者", "声骸", "世界观", "剧情角色", "名词注释"]
SUBPAGE_PREFIXES = ["角色经历", "设定拾遗", "社区回响", "简介"]

# 机器人管理接口
BOT_API = "http://127.0.0.1:8080"
BOT_TOKEN = "YOUR_ADMIN_TOKEN"          # 与 config.json 里的 admin_token 一致
GROUP_ID = 0                    # 0 = 全局知识（所有群可用）
FOLDER_ROOT = "鸣潮"            # 归档到 鸣潮/<分类>
BATCH = 150                     # 每批导入条数
CRAWL_WORKERS = 6               # 并发数（别调太高，避免被站点限流）
CRAWL_DELAY = 0.1               # 每次请求后的间隔（秒）
# ================================================================


def log(msg=""):
    print(msg, flush=True)


def safe_name(title):
    h = hashlib.md5(title.encode("utf-8")).hexdigest()[:10]
    base = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", title)[:60]
    return f"{base}__{h}.json"


# ------------------------- 1. 爬取 -------------------------
def wiki_get(params, timeout=45, retries=3):
    """请求 MediaWiki API，返回 dict。纯标准库实现。"""
    params = dict(params)
    params["format"] = "json"
    url = API_WIKI + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    return {"__error__": f"{type(last).__name__}: {last}"}


def list_category(cat):
    """列出一个分类下的所有主命名空间页面标题。"""
    titles, cont = [], None
    for _ in range(60):
        kw = dict(action="query", list="categorymembers",
                  cmtitle=f"Category:{cat}", cmlimit="500", cmnamespace="0")
        if cont:
            kw["cmcontinue"] = cont
        d = wiki_get(kw)
        titles += [m["title"] for m in d.get("query", {}).get("categorymembers", [])]
        cont = d.get("continue", {}).get("cmcontinue")
        if not cont:
            break
    return titles


def list_prefix(prefix):
    """列出某个前缀下的所有页面（用于 角色经历/xxx 这类子页面）。"""
    titles, cont = [], None
    for _ in range(30):
        kw = dict(action="query", list="allpages", apprefix=prefix + "/",
                  aplimit="500", apnamespace="0")
        if cont:
            kw["apcontinue"] = cont
        d = wiki_get(kw)
        titles += [p["title"] for p in d.get("query", {}).get("allpages", [])]
        cont = d.get("continue", {}).get("apcontinue")
        if not cont:
            break
    return titles


def collect_plan():
    """收集所有需要抓的页面 -> [(分类, 标题)]"""
    plan, seen = [], set()
    for cat in CATEGORIES:
        ts = list_category(cat)
        log(f"  Category:{cat:<8} {len(ts):>4} 个")
        for t in ts:
            if t not in seen:
                seen.add(t)
                plan.append((cat, t))
    for pre in SUBPAGE_PREFIXES:
        ts = list_prefix(pre)
        log(f"  {pre + '/':<12} {len(ts):>4} 个")
        for t in ts:
            if t not in seen:
                seen.add(t)
                plan.append((pre, t))
    return plan


def fetch_one(item):
    cat, title = item
    d = wiki_get(dict(action="parse", page=title, prop="wikitext", redirects="1"))
    time.sleep(CRAWL_DELAY)
    wt = (d.get("parse", {}).get("wikitext") or {}).get("*", "")
    rec = {"title": title, "cat": cat, "ok": bool(wt), "len": len(wt),
           "wikitext": wt, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with io.open(os.path.join(CACHE, safe_name(title)), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    return bool(wt)


def crawl(force=False):
    os.makedirs(CACHE, exist_ok=True)
    log("[1/4] 收集页面清单")
    plan = collect_plan()
    log(f"  合计 {len(plan)} 个页面")

    if force:
        todo = plan
    else:
        todo = [(c, t) for c, t in plan
                if not os.path.exists(os.path.join(CACHE, safe_name(t)))]
    log(f"  已缓存 {len(plan) - len(todo)} 个，本次需抓 {len(todo)} 个")
    if not todo:
        log("  无需抓取（如需强制重抓，加 --crawl）")
        return 0

    log("[2/4] 抓取中")
    t0, done, fail = time.time(), 0, 0
    total = len(todo)
    with concurrent.futures.ThreadPoolExecutor(max_workers=CRAWL_WORKERS) as ex:
        for ok in ex.map(fetch_one, todo):
            done += 1
            if not ok:
                fail += 1
            if done % 50 == 0 or done == total:
                el = time.time() - t0
                sp = done / el if el else 0
                eta = (total - done) / sp if sp else 0
                log(f"    {done:>4}/{total}  失败{fail}  {sp:.1f} 页/秒  "
                    f"剩余约 {eta:.0f} 秒")
    log(f"  完成：{done} 个，失败 {fail}，耗时 {(time.time()-t0)/60:.1f} 分钟")
    return fail


# ------------------------- 2. 解析 -------------------------
def build_entries():
    log("[3/4] 解析缓存 -> 问答对（本地，不联网）")
    if not os.path.exists(BUILDER):
        raise SystemExit(f"缺少解析器：{BUILDER}")
    r = subprocess.run([sys.executable, BUILDER], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=BASE)
    out = (r.stdout or "")
    for line in out.splitlines()[:16]:
        log("    " + line)
    if not os.path.exists(ENTRIES_JSON):
        raise SystemExit("解析未生成 _wiki_entries.json\n" + (r.stderr or "")[:800])
    items = json.load(io.open(ENTRIES_JSON, encoding="utf-8"))["items"]
    log(f"    解析出 {len(items)} 条")
    return items


# ------------------------- 3. 导入 -------------------------
def bot_get(path, **params):
    params["token"] = BOT_TOKEN
    url = BOT_API + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.loads(r.read())


def bot_post(path, body, timeout=300):
    url = BOT_API + path + "?" + urllib.parse.urlencode({"token": BOT_TOKEN})
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def check_bot():
    """确认机器人在跑。"""
    try:
        bot_get("/admin/api/kb", group_id=-1, q="")
        return True
    except Exception as e:
        log(f"  ✗ 连不上机器人管理接口 {BOT_API}")
        log(f"    {type(e).__name__}: {e}")
        log("    请先把 bot.py 启动起来，再运行本脚本。")
        return False


def existing_questions():
    """已存在的问句集合，用于跳过重复。"""
    try:
        rows = bot_get("/admin/api/kb", group_id=-1)["items"]
        return {r["question"] for r in rows}
    except Exception:
        return set()


def import_entries(items):
    log("[4/4] 导入知识库（走本机管理接口）")

    have = existing_questions()
    todo = [it for it in items if it["question"] not in have]
    log(f"  已有 {len(have)} 条，本次新增 {len(todo)} 条"
        + (f"（跳过重复 {len(items)-len(todo)} 条）" if len(todo) < len(items) else ""))
    if not todo:
        log("  没有新条目需要导入")
        return 0, []

    # 条数上限，防止导入到一半被拦
    need = len(have) + len(todo) + 200
    try:
        r = bot_post("/admin/api/config", {"kb_max_per_group": max(2000, need)})
        if r.get("changed"):
            log(f"  已把每群条数上限调到 {max(2000, need)}")
    except Exception as e:
        log(f"  （上限调整失败，继续）{type(e).__name__}: {e}")

    # 备份现有知识库
    try:
        exp = bot_get("/admin/api/kb/export", group_id=-1)
        bak = os.path.join(BASE, "data", "backup",
                          f"kb_before_wiki_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        io.open(bak, "w", encoding="utf-8").write(exp.get("text", ""))
        log(f"  已备份现有 {exp.get('count')} 条 -> {os.path.basename(bak)}")
    except Exception as e:
        log(f"  （备份失败，继续）{type(e).__name__}: {e}")

    added, failed = 0, []
    nb = (len(todo) + BATCH - 1) // BATCH
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        try:
            res = bot_post("/admin/api/kb/import", {"group_id": GROUP_ID, "items": chunk})
        except Exception as e:
            log(f"    批次 {i//BATCH+1} 失败：{type(e).__name__}: {e}")
            continue
        added += res.get("added", 0)
        failed += res.get("failed", []) or []
        log(f"    批次 {i//BATCH+1:>2}/{nb}  新增 {res.get('added')}  "
            f"失败 {res.get('failed_count')}  累计 {added}")

    log(f"  导入完成：成功 {added}，失败 {len(failed)}")
    for f in failed[:8]:
        log(f"     失败: {f}")

    # 归档到 鸣潮/<分类>
    if added:
        log("  按分类归档")
        try:
            rows = bot_get("/admin/api/kb", group_id=-1, folder="")["items"]
            by_cat = defaultdict(list)
            for it in rows:
                by_cat[it.get("category") or "其他"].append(it["id"])
            moved = 0
            for cat, ids in sorted(by_cat.items(), key=lambda x: -len(x[1])):
                folder = f"{FOLDER_ROOT}/{cat}"
                bot_post("/admin/api/kb/folder/create",
                         {"path": folder, "group_id": GROUP_ID})
                r3 = bot_post("/admin/api/kb/move",
                              {"ids": ids, "folder": folder, "group_id": GROUP_ID})
                moved += r3.get("moved", 0)
                log(f"    {cat:<12} {r3.get('moved'):>4} 条 -> 📁 {folder}")
        except Exception as e:
            log(f"  （归档失败，条目在根目录）{type(e).__name__}: {e}")
    return added, failed


# ------------------------- main -------------------------
def main():
    ap = argparse.ArgumentParser(description="鸣潮WIKI -> 知识库 一键脚本（本地运行，不花钱）")
    ap.add_argument("--crawl", action="store_true", help="强制重新爬取所有页面")
    ap.add_argument("--no-crawl", action="store_true", help="不爬，只用本地缓存")
    ap.add_argument("--dry-run", action="store_true", help="只解析统计，不导入")
    args = ap.parse_args()

    log("=" * 62)
    log("  鸣潮 WIKI -> 知识库    （全程本地，不消耗 AI 额度）")
    log("=" * 62)

    if not args.no_crawl:
        fail = crawl(force=args.crawl)
        if fail:
            log(f"  （有 {fail} 个页面抓取失败，不影响其它）")
    else:
        log("[1-2/4] 跳过爬取（--no-crawl）")

    items = build_entries()

    if args.dry_run:
        log("[4/4] --dry-run：不导入")
        from collections import Counter
        c = Counter()
        for it in items:
            q = it["question"]
            for k in ("声骸", "基本资料", "简介", "基础属性", "技能", "共鸣链"):
                if k in q:
                    c[k] += 1
                    break
            else:
                c["其他/名词"] += 1
        log("  问题类型分布:")
        for k, v in c.most_common():
            log(f"    {k:<10} {v:>5}")
        log(f"\n输出文件: {ENTRIES_JSON}")
        return

    if not check_bot():
        raise SystemExit(1)
    added, failed = import_entries(items)

    log()
    log("=" * 62)
    try:
        f = bot_get("/admin/api/kb/folders")
        log(f"  知识库现有 {f['total']} 条，根目录 {f['root_count']} 条")
        for x in sorted(f["folders"], key=lambda y: -y["count"])[:12]:
            log(f"    📁 {x['path']:<20} {x['count']:>5}")
    except Exception:
        pass
    log(f"  本次新增 {added} 条，失败 {len(failed)} 条")
    log("  刷新管理页（Ctrl+F5）查看：知识库 -> 鸣潮")
    log("=" * 62)


if __name__ == "__main__":
    main()
