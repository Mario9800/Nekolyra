# -*- coding: utf-8 -*-
r"""把仓库内容渲染成一个本地 HTML —— GitHub 打不开时看这个。

数据从本地文件读，不需要联网。下载链接走 ghproxy（实测能通）。
"""
import html
import io
import json
import os
import re
import urllib.request

BASE = r"D:\ai\qq_bot"
INST = r"D:\ai\Nekoe-v1.0.0.0"
MIRROR = r"D:\ai\echo-qq-bot"
REPO = "Mario9800/Nekolyra"
V = io.open(os.path.join(BASE, "VERSION"), encoding="utf-8").read().strip()


def md(t):
    r"""极简 markdown —— 够用就行，不引第三方库。"""
    out, in_code, in_ul = [], False, False
    for line in t.splitlines():
        if line.startswith("```"):
            if in_ul:
                out.append("</ul>"); in_ul = False
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line))
            continue
        s = html.escape(line)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)",
                   r'<a href="\2" target="_blank">\1</a>', s)
        s = re.sub(r"(?<![\"'=])(https?://[^\s<]+)",
                   r'<a href="\1" target="_blank">\1</a>', s)
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            if in_ul:
                out.append("</ul>"); in_ul = False
            lv = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lv, html.escape(m.group(2)), lv))
            continue
        if re.match(r"^\s*[-*]\s+", line):
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append("<li>%s</li>" % re.sub(r"^\s*[-*]\s+", "", s))
            continue
        if in_ul:
            out.append("</ul>"); in_ul = False
        if not line.strip():
            out.append("")
        else:
            out.append("<p>%s</p>" % s)
    if in_ul:
        out.append("</ul>")
    if in_code:
        out.append("</pre>")
    return "\n".join(out)


def read(f, n=None):
    p = os.path.join(BASE, f)
    if not os.path.exists(p):
        return ""
    t = io.open(p, encoding="utf-8", errors="replace").read()
    return t[:n] if n else t


readme = read("README.md")
change = read("CHANGELOG.md")
# 只列镜像里的文件 —— 那就是 GitHub 上真实存在的东西
files = []
for root, dirs, fs in os.walk(MIRROR):
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
    for f in fs:
        p = os.path.join(root, f)
        rel = os.path.relpath(p, MIRROR).replace("\\", "/")
        if rel in ("config.json", "startup.log"):
            continue
        files.append((rel, os.path.getsize(p)))
files.sort()

# Release 资产（本地已知的）
# 资产名跟着发版走。v1.0.3.3 是改名之前发的，还是 Nekoe- 开头。
assets = []
try:
    import subprocess as _sp, json as _js
    _r = _sp.run(["git", "credential", "fill"],
                 input="protocol=https\nhost=github.com\n\n",
                 capture_output=True, text=True, timeout=30)
    _tk = [x.split("=", 1)[1].strip() for x in _r.stdout.splitlines()
           if x.startswith("password=")][0]
    _op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    _rq = urllib.request.Request(
        "https://api.github.com/repos/%s/releases/latest" % REPO)
    _rq.add_header("Authorization", "Bearer " + _tk)
    _rq.add_header("User-Agent", "Nekolyra")
    with _op.open(_rq, timeout=40) as _x:
        _d = _js.loads(_x.read())
    for a in (_d.get("assets") or []):
        if a["name"].endswith(".zip"):
            assets.append((a["name"], int(a.get("size") or 0)))
    _tag = _d.get("tag_name") or ("v" + V)
except Exception as e:
    print("  (拿不到 Release 资产，用已知名: %s)" % type(e).__name__)
    _tag = "v" + V
    assets = [("Nekoe-desktop-v%s.zip" % V, 0),
              ("Nekoe-source-v%s.zip" % V, 0)]

DL = "https://ghproxy.net/https://github.com/%s/releases/download/%s/" % (REPO, _tag)

HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(name)s %(ver)s —— 本地项目页</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0d1117;color:#e6edf3;
 font:15px/1.75 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:940px;margin:0 auto;padding:32px 24px 80px}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
 padding-bottom:18px;border-bottom:1px solid #30363d;margin-bottom:26px}
h1{font-size:26px;margin:0}
.tag{background:#1f6feb;color:#fff;font-size:12px;padding:3px 9px;
 border-radius:20px;font-weight:600}
.note{background:#1c2128;border:1px solid #30363d;border-left:3px solid #d29922;
 padding:12px 16px;border-radius:6px;margin:0 0 26px;font-size:13.5px;
 color:#c9d1d9}
h2{font-size:19px;margin:34px 0 12px;padding-bottom:7px;
 border-bottom:1px solid #21262d}
h3{font-size:16px;margin:24px 0 8px;color:#e6edf3}
h4{font-size:14px;margin:18px 0 6px;color:#8b949e}
a{color:#58a6ff;text-decoration:none;word-break:break-all}
a:hover{text-decoration:underline}
code{background:#161b22;padding:2px 6px;border-radius:4px;font-size:13px;
 font-family:"Cascadia Mono",Consolas,monospace;color:#ffa657}
pre{background:#161b22;border:1px solid #30363d;border-radius:8px;
 padding:14px 16px;overflow-x:auto;font-size:13px;
 font-family:"Cascadia Mono",Consolas,monospace;line-height:1.6}
pre code{background:none;padding:0;color:#c9d1d9}
ul{padding-left:22px}
li{margin:4px 0}
table{width:100%%;border-collapse:collapse;margin:12px 0;font-size:13.5px}
th,td{border:1px solid #30363d;padding:8px 12px;text-align:left}
th{background:#161b22}
.dl{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0 6px}
.dl a{background:#238636;color:#fff;padding:9px 16px;border-radius:7px;
 font-size:13.5px;font-weight:600;text-decoration:none}
.dl a:hover{background:#2ea043}
.ft{margin-top:44px;padding-top:18px;border-top:1px solid #21262d;
 color:#8b949e;font-size:12.5px}
</style></head><body><div class="wrap">
<header><h1>%(name)s</h1><span class="tag">v%(ver)s</span>
<span style="color:#8b949e;font-size:13px">%(nfile)d 个文件 · 本地快照</span>
</header>
<div class="note"><b>这是本地页面，不经过 GitHub。</b><br>
你那边 <code>github.com</code> 连不上（实测官方站和 15 个免费镜像都不通，
那些镜像只代理文件下载、不镜像网页）。这个页面直接读本地文件生成，
双击就能看。<br>
下载安装包走加速站，实测能通。</div>

<h2>下载</h2>
<div class="dl">%(links)s</div>
<p style="color:#8b949e;font-size:12.5px">链接走 ghproxy 加速（实测 0.9 秒响应）。
仓库地址：<code>github.com/%(repo)s</code></p>

<h2>README</h2>
%(readme)s

<h2>更新日志</h2>
%(change)s

<h2>文件（%(nfile)d 个）</h2>
<table><tr><th>文件</th><th style="width:110px">大小</th></tr>
%(filelist)s
</table>

<div class="ft">由 <code>_make_page.py</code> 生成 · 版本 %(ver)s ·
重新生成：<code>python _make_page.py</code></div>
</div></body></html>
"""

links = ""
for f, sz in assets:
    _label = (f.replace(".zip", "")
              .replace("Nekoe-desktop-", "桌面版 ")
              .replace("Nekoe-source-", "源码 ")
              .replace("Nekolyra-desktop-", "桌面版 ")
              .replace("Nekolyra-source-", "源码 "))
    _sz = ("（%.1f MB）" % (sz / 1024 / 1024)) if sz else ""
    links += '<a href="%s%s">%s%s</a>' % (DL, f, _label, _sz)
if not links:
    links = '<span style="color:#8b949e">没找到本地安装包</span>'

filelist = "\n".join(
    '<tr><td><code>%s</code></td><td style="color:#8b949e">%s</td></tr>'
    % (html.escape(r), ("%.0f KB" % (s / 1024)) if s >= 1024 else "%d B" % s)
    for r, s in files)

out = HTML % {
    "name": "Nekolyra", "ver": V, "repo": REPO, "links": links,
    "readme": md(readme), "change": md(change),
    "nfile": len(files), "filelist": filelist,
}
dst = os.path.join(INST, "项目主页.html")
io.open(dst, "w", encoding="utf-8", newline="\n").write(out)
print("  生成 %s" % dst)
print("    %.0f KB   %d 个文件  %d 个下载链接"
      % (os.path.getsize(dst) / 1024, len(files), len(assets)))
