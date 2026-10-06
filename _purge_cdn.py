# -*- coding: utf-8 -*-
r"""清 jsDelivr 缓存 —— 发版后必须跑一次。

为什么需要它：版本检查的 CDN 兜底读的是
  https://cdn.jsdelivr.net/gh/awnpw/Nekolyra@main/version.json
而 jsDelivr 对 @main 有约 12 小时缓存。不清的话，发完新版之后的
最多 12 小时里，兜底路径会报**旧版本号** —— 用户点"检查更新"看到
"已是最新"，其实新版早发了。

purge 接口是 jsDelivr 官方的，免费、无需鉴权。
"""
import json
import sys
import time
import urllib.request

REPO = "awnpw/Nekolyra"
FILES = ["version.json", "VERSION", "CHANGELOG.md", "README.md",
         "data/plugins/adfilter/main.py",
         "data/plugins/adfilter/metadata.json"]
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
UA = {"User-Agent": "Nekolyra-release"}


def read(f, n=140):
    rq = urllib.request.Request(
        f"https://cdn.jsdelivr.net/gh/{REPO}@main/{f}", headers=UA)
    with OP.open(rq, timeout=20) as r:
        return r.read(n)


def purge(f):
    rq = urllib.request.Request(
        f"https://purge.jsdelivr.net/gh/{REPO}@main/{f}", headers=UA)
    with OP.open(rq, timeout=25) as r:
        return json.loads(r.read()).get("status")


def main():
    want = ""
    try:
        want = read("VERSION", 40).decode().strip()
    except Exception:
        pass
    print("  CDN 上应该是版本 %r" % want)
    print()
    print("  清缓存:")
    for f in FILES:
        try:
            print("    %-40s %s" % (f, purge(f)))
        except Exception as e:
            print("    %-40s %s" % (f, type(e).__name__))
    print()
    print("  等 8 秒再验…")
    time.sleep(8)
    ok = True
    for f in FILES:
        for attempt in range(3):
            try:
                print("    %-40s %r" % (f, read(f, 140)[:70]))
                break
            except Exception as e:
                if attempt == 2:
                    print("    %-40s %s" % (f, type(e).__name__))
                    ok = False
                time.sleep(3)
    try:
        # 读全 —— version.json 带 notes，截断了会 json 解析失败
        d = json.loads(read("version.json", 65536))
        good = want and d.get("tag", "").lstrip("vV") == want
        print()
        print("  version.json tag = %s  %s"
              % (d.get("tag"), "✅ 跟 VERSION 一致" if good else "❌ 对不上"))
        if not good:
            ok = False
    except Exception as e:
        print("  读 version.json 失败: %s" % e)
        ok = False
    print()
    print("  %s" % ("✅ 完成" if ok else "⚠ 有项目没通过，看一下上面"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
