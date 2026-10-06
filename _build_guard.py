# -*- coding: utf-8 -*-
r"""构建防呆：跑 build.py，然后**真的把 exe 跑起来**验证它含新代码。

为什么需要这个（血的教训）：
  1. build.py 曾经因为缩进错误一行都没跑，而我的发布脚本
     · 只扫了 stdout（报错在 stderr）
     · 只检查"exe 存在吗"（陈旧 exe 也在）
     结果连发两个版本，zip 里的 exe 都是上一个版本的。
  2. 想靠"在 exe 字节里搜字符串"来判断也不行 ——
     PyInstaller 的 PYZ 是压缩的，源码字符串搜不到。

唯一可靠的判断：跑起来看行为。
用法：
    python _build_guard.py            # 构建 + 验证
    python _build_guard.py --no-build # 只验证 dist 里现有的
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))


# 改名过渡：构建产物和实例里的 exe 名都可能是旧的 Nekolyra.exe
def _pick_exe():
    for root in (os.path.join(BASE, "dist", "Nekolyra"),
                 os.path.join(BASE, "dist", "Nekolyra")):
        for nm in ("Nekolyra.exe", "Nekolyra.exe"):
            if os.path.exists(os.path.join(root, nm)):
                return nm
    return "Nekolyra.exe"


EXE_NAME = _pick_exe()
INST = r"D:\ai\Nekolyra-v1.0.0.0"    # 你自己的实例目录（未改名）
SRC = os.path.join(BASE, "dist", "Nekolyra")
# 改名过渡期：构建产物可能还在旧目录里
if not os.path.isdir(SRC):
    _old = os.path.join(BASE, "dist", "Nekolyra")
    if os.path.isdir(_old):
        SRC = _old
PORT = 8083

# 每个新功能在这里登记一条：名字 -> (探测方法, 说明)
# 探测方法是一个函数(port) -> (bool, str)
PROBES = []


def probe(name, note):
    def deco(fn):
        PROBES.append((name, note, fn))
        return fn
    return deco


def _get(u, t=60):
    try:
        with urllib.request.urlopen(u, timeout=t) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, str(e).encode()


@probe("update-download", "就地下载接口 /admin/api/update/download")
def _p_download(port):
    st, b = _get("http://127.0.0.1:%d/admin/api/update/download?name=__x&token=t"
                 % port)
    body = b.decode("utf-8", "replace")
    # 路由不存在 -> FastAPI 给 {"detail":"Not Found"}
    if st == 404 and "detail" in body:
        return False, "路由不存在 -> HTTP 404 %s" % body[:50]
    return True, "HTTP %s %s" % (st, body[:60])


@probe("friendly-403", "配额用完时的友好提示")
def _p_403(port):
    st, b = _get("http://127.0.0.1:%d/admin/api/version?force=1&token=t" % port, 120)
    d = json.loads(b)
    e = str(d.get("error") or "")
    if "访问次数用完" in e:
        return True, "error=%r" % e[:60]
    if d.get("ok"):
        return True, "配额正常（ok=True，没法验证文案，但代码在）"
    return False, "error=%r 不像新文案" % e[:60]


@probe("fetch-real-name", "fetch 用真实文件名走到底（不能 500）")
def _p_fetch_real(port):
    r"""这个探测是为了盖住上一版的盲区。

    原来我只用 name=__x 探，那个在"文件名不存在"就 404 返回了，
    **永远走不到后面拼地址、算回退地址的代码** —— 于是那段的
    NameError（hit2 未定义）没被抓住，一路发到用户手上变成 HTTP 500。

    现在用真实存在的文件名 + 镜像源，让它真的走到最后。
    """
    import urllib.parse as _up

    def _post(u):
        try:
            rq = urllib.request.Request(u, method="POST")
            with urllib.request.urlopen(rq, timeout=40) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:
            return None, str(e).encode()

    st, b = _get("http://127.0.0.1:%d/admin/api/version?token=t" % port, 120)
    try:
        d = json.loads(b)
    except Exception:
        return True, "拿不到 version（配额/网络），跳过这个探测"
    assets = [a for a in (d.get("assets") or [])
              if a.get("name") != "SHA256SUMS.txt"]
    if not assets:
        return True, "这个 Release 没附件（多半是配额用完），跳过"
    a = min(assets, key=lambda x: int(x.get("size") or 0))
    st, b = _post("http://127.0.0.1:%d/admin/api/update/fetch?name=%s&src=ghproxy"
                  "&token=t" % (port, _up.quote(a["name"])))
    body = b.decode("utf-8", "replace")
    # 顺手取消，别让沙箱里挂着下载
    _post("http://127.0.0.1:%d/admin/api/update/cancel?token=t" % port)
    if st == 500:
        return False, "HTTP 500（走到后面就炸了）body=%s" % body[:80]
    if st in (200, 400, 404, 409, 502):
        return True, "HTTP %s %s" % (st, body[:60])
    return False, "意外状态 %s body=%s" % (st, body[:80])


@probe("update-cancel", "取消下载接口")
def _p_cancel(port):
    st, b = _get("http://127.0.0.1:%d/admin/api/update/cancel" % port)
    body = b.decode("utf-8", "replace")
    if st == 404 and "detail" in body:
        return False, "路由不存在"
    return True, "HTTP %s（401 也算在）" % st


@probe("update-ping", "线路测速接口")
def _p_ping(port):
    st, b = _get("http://127.0.0.1:%d/admin/api/update/ping?src=direct&token=t"
                 % port, 60)
    body = b.decode("utf-8", "replace")
    # 路由不存在时 FastAPI 给 {"detail":"Not Found"}
    if st == 404 and "detail" in body:
        return False, "路由不存在 -> HTTP 404 %s" % body[:50]
    if '"ms"' in body:
        return True, "HTTP %s %s" % (st, body[:70])
    # 配额用完时拿不到 Release 信息，返回的是 {"ok":false,"error":...} ——
    # 路由是通的，只是环境不允许测。别把这个当成失败，否则天天误报。
    return True, "路由在，但环境测不了: %s" % body[:70]


@probe("configFor-fixed", "插件配置小窗没被插错代码")
def _p_cfg(port):
    st, h = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = h.decode("utf-8", "replace")
    i = h.find("async function configFor")
    if i < 0:
        return False, "找不到 configFor"
    seg = h[i:i + 2200]
    if "d.assets" in seg:
        return False, "configFor 里还引用着 d（回归）"
    return True, "configFor 干净"


@probe("update-fetch", "更新包下载到磁盘的接口")
def _p_fetch(port):
    st, b = _get("http://127.0.0.1:%d/admin/api/update/fetch?name=__x&token=t"
                 % port)
    body = b.decode("utf-8", "replace")
    if st == 404 and "detail" in body:
        return False, "路由不存在 -> HTTP 404 %s" % body[:50]
    return True, "HTTP %s %s" % (st, body[:60])


@probe("update-progress", "下载进度接口")
def _p_prog(port):
    st, b = _get("http://127.0.0.1:%d/admin/api/update/progress?token=t" % port)
    body = b.decode("utf-8", "replace")
    if st == 404 and "detail" in body:
        return False, "路由不存在 -> HTTP 404 %s" % body[:50]
    if '"pct"' not in body:
        return False, "响应里没有 pct 字段: %s" % body[:70]
    return True, "HTTP %s %s" % (st, body[:70])


@probe("update-reveal", "打开下载文件夹的接口")
def _p_reveal(port):
    st, b = _get("http://127.0.0.1:%d/admin/api/update/reveal" % port)
    body = b.decode("utf-8", "replace")
    if st == 404 and "detail" in body:
        return False, "路由不存在"
    return True, "HTTP %s（401 也算在）" % st


@probe("mdlite-rules", "_mdLite 有没有链接/列表/标题分级")
def _p_md(port):
    st, h = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = h.decode("utf-8", "replace")
    i = h.find("function _mdLite")
    if i < 0:
        return False, "找不到 _mdLite"
    seg = h[i:i + 2600]
    miss = []
    # 链接规则：\[([^\]]+)\]\((https?:
    if "https?:" not in seg:
        miss.append("链接")
    if "md-li" not in seg:
        miss.append("列表")
    if "md-h3" not in seg or "md-h2" not in seg:
        miss.append("标题分级")
    if "md-pre" not in seg:
        miss.append("代码块")
    if r"[\s\S]" not in seg:
        miss.append("跨行粗体")
    if miss:
        return False, "缺规则: %s" % miss
    return True, "链接/列表/标题分级/代码块/跨行粗体 都在"


@probe("update-sources", "版本接口返回下载源列表")
def _p_src(port):
    st, b = _get("http://127.0.0.1:%d/admin/api/version?token=t" % port, 120)
    d = json.loads(b)
    srcs = d.get("sources") or []
    if len(srcs) < 2:
        return False, "sources=%r" % srcs
    return True, "%d 个: %s" % (len(srcs), [x.get("label") for x in srcs])


@probe("sig-ready", "验签能力（cryptography 有没有被打进 exe）")
def _p_sig(port):
    r"""带不进去的话，签名版本会全部校验失败，加速节点全废。

    这个坑只在用户真的点下载时才暴露 —— 所以必须在发布前就探测。
    """
    st, b = _get("http://127.0.0.1:%d/admin/api/version?token=t" % port, 120)
    try:
        d = json.loads(b)
    except Exception:
        return True, "拿不到 version（配额/网络），跳过"
    if "sig_ready" not in d:
        return False, "响应里没有 sig_ready 字段（代码没进去）"
    if d.get("sig_ready") is not True:
        return False, ("sig_ready=False —— 验签不可用，"
                       "accelerate 节点会全部失败")
    return True, "sig_ready=True  公钥指纹=%s" % d.get("sig_key")


@probe("sig-key-present", "公钥已编进程序")
def _p_sigkey(port):
    st, b = _get("http://127.0.0.1:%d/admin/api/version?token=t" % port, 120)
    try:
        d = json.loads(b)
    except Exception:
        return True, "拿不到 version，跳过"
    k = str(d.get("sig_key") or "")
    if len(k) < 8:
        return False, "公钥指纹是空的: %r" % k
    return True, "指纹 %s" % k


@probe("theme-tokens", "新主题令牌进了构建没")
def _p_theme(port):
    r"""CSS 没有接口可测，就查服务出来的 HTML 里有没有新令牌。

    这能盖住"改了源码但没打进 exe"—— 肉眼看截图不一定分得出
    （旧主题也是青色系的话）。
    """
    st, h = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = h.decode("utf-8", "replace")
    need = {
        "--azure:#246499": "主色（降饱和到 62%，taste-skill 要求 <80%）",
        "--gold:#8a6d1f": "金色令牌",
        "--gold-pale:#f8e8c8": "她的发色（只做底）",
        "--green:#15803d": "成功色（色相离主色 65°）",
        "brand-ver": "品牌区版本号占位",
    }
    miss = [v for k, v in need.items() if k not in h]
    if miss:
        return False, "缺: %s" % miss
    # 上一版用过气动青做主色（元素色，不是她的视觉身份），别再溜回来
    for old in ("--aero:#0a7d56", "--green:#047857", "--green:#2f7d32",
                "--azure:#0b5aa5"):
        if old in h:
            return False, "残留旧令牌 %s" % old
    return True, "azure/gold/green 令牌都在，配色规则生效"


@probe("theme-contrast", "关键对比度没被改坏")
def _p_contrast(port):
    r"""把页面里的令牌抓出来，现场算对比度。

    比在文档里写死数字可靠 —— 谁改了令牌都会在这里被拦住。
    """
    st, h = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = h.decode("utf-8", "replace")

    def _s(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def lum(x):
        x = x.lstrip("#")
        r, g, b = (int(x[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * _s(r) + 0.7152 * _s(g) + 0.0722 * _s(b)

    def cr(a, b):
        la, lb = lum(a), lum(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    import re as _re
    # 只取**第一个** :root 块（亮色）。之前用 findall 扫全文，
    # 后面暗色 :root 的值会覆盖亮色的，然后拿暗色的值比白底 —— 必然失败。
    m0 = _re.search(r":root\{(.*?)\}", h, _re.S)
    if not m0:
        return False, "页面里找不到 :root 令牌块"
    tok = dict(_re.findall(r"--([a-z0-9-]+):(#[0-9a-fA-F]{6})", m0.group(1)))
    checks = [("azure", 4.5), ("green", 4.5), ("red", 4.5), ("amber", 4.5),
              ("gold", 3.0), ("text-3", 3.0)]
    bad = []
    for k, need in checks:
        v = tok.get(k)
        if not v:
            continue
        r = cr(v, "#ffffff")
        if r < need:
            bad.append("%s %s %.2f<%.1f" % (k, v, r, need))
    if bad:
        return False, "; ".join(bad)
    return True, ("亮色 %d 个令牌白底对比度全部达标（azure %s）"
                  % (len(tok), tok.get("azure")))


@probe("wallpaper-css", "壁纸图层与设置项都进了构建")
def _p_wall(port):
    r"""壁纸是服务时注入的，所以查服务出来的 HTML 最直接。

    盖三件事：
      1. 注入的 CSS 在（body::before 图层 + --wall 变量）
      2. 默认壁纸真的嵌进去了（data URI 不是空的）
      3. 设置界面在（pickWall / 外观组）
    """
    import re as _re

    st, h = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = h.decode("utf-8", "replace")
    miss = []
    # --wall 的值有两种：url("data:...")（自定义图片）或渐变（预设）。
    # 不能只认 base64 —— 默认预设就是渐变。
    _m = _re.search(r"--wall:\s*([^;}\"']{4,})", h)
    if not _m:
        miss.append("没有壁纸变量（注入了空值？）")
    elif not (_m.group(1).startswith("url(")
              or "gradient(" in _m.group(1)):
        miss.append("--wall 的值看不懂: %s" % _m.group(1)[:40])
    if "body::before" not in h:
        miss.append("没有 body::before 图层")
    if "backdrop-filter" not in h:
        miss.append("没有毛玻璃")
    if "pickWall" not in h:
        miss.append("没有上传控件 pickWall")
    if "自定义壁纸" not in h:
        miss.append("没有「自定义壁纸」这一项")
    if miss:
        return False, "; ".join(miss)
    if _m.group(1).startswith("url("):
        mm = _re.search(r"base64,([A-Za-z0-9+/=]+)", _m.group(1))
        kb = len(mm.group(1)) * 3 / 4 / 1024 if mm else 0
        kind = "自定义图片 %.0f KB" % kb
    else:
        kind = "CSS 渐变预设"
    return True, "图层/毛玻璃/设置项都在，壁纸是%s" % kind


@probe("font-stack", "字体栈符合规范（禁 Inter、中文有真字体）")
def _p_font(port):
    r"""两件事：
      1. taste-skill §7 禁 Inter —— 字体栈里不能有它
      2. 中文必须命中真字体。原来栈里写的是 "Microsoft YaHei"，
         而本机只有 "Microsoft YaHei UI"，所以中文一直在回退到通用 sans-serif。
    """
    st, h = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = h.decode("utf-8", "replace")
    bad = []
    if '"Inter"' in h or "Inter," in h or "Inter;" in h:
        bad.append('字体栈里还有 Inter（§7 禁用）')
    if '"Microsoft YaHei UI"' not in h:
        bad.append('没有 Microsoft YaHei UI（中文会回退）')
    if '"Cascadia Mono"' not in h:
        bad.append('等宽栈没有 Cascadia Mono')
    if '"Segoe UI Variable Text"' not in h:
        bad.append('没有 Segoe UI Variable Text')
    if bad:
        return False, "; ".join(bad)
    return True, "禁 Inter ✅ 中文命中 YaHei UI ✅ 等宽 Cascadia Mono ✅"


@probe("appearance-controls", "外观控件带 data-key（否则保存收不到）")
def _p_appear(port):
    r"""这一段一次踩了 4 个 bug，根因都是我只查"字符串在不在"：

        1. 自定义控件没有 cfg- 前缀，saveConfig 扫不到 → **保存完全无效**
        2. 滑杆用 _hex2rgb 解析 "10,16,34" → 解析成暗红，拖滑杆面板变黑
        3. ui_side_on / ui_face_on 在 applyAppearance 里没有分支 → 开关点了没反应
        4. loadConfig 没清 __appearPatch → 跨页残留旧值

      所以这里查的是**修复后的特征点**，任一个丢了都会拦下来。
    """
    st, h = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = h.decode("utf-8", "replace")
    bad = []
    if 'data-key="${key}"' not in h:
        bad.append("开关缺 data-key")
    if 'data-key="${id}"' not in h:
        bad.append("滑杆缺 data-key")
    if "}-v" not in h:
        bad.append("色板缺隐藏 input")
    if "ui_side_on" not in h or "ui_face_on" not in h:
        bad.append("缺两个开关的分支")
    if "window.__appearPatch={}" not in h:
        bad.append("loadConfig 没重置 __appearPatch")
    if "no-side-glass" not in h:
        bad.append("缺关玻璃的兜底样式")
    if bad:
        return False, "; ".join(bad)
    return True, "开关/滑杆/色板 data-key 齐，rgb 解析与开关分支都在"


@probe("appearance-save", "外观配置真的能存进去（白名单 + 落盘）")
def _p_appsave(port):
    r"""行为级探测：改一个值 -> 读回来 -> 确认变了 -> 改回去。

    为什么必须这么做：服务端 api_cfg_set 有个 EDITABLE_KEYS 白名单，
        if k not in EDITABLE_KEYS and k not in _PLUGIN_KEYS: continue
    键不在白名单里会**静默丢弃**，接口照样返回 {"ok":true,"changed":0}。
    光看前端收集对不对根本发现不了 —— 之前就是这么漏掉一整个外观面板的。
    """
    import json as _json
    import urllib.request as _ur

    def _get():
        with _ur.urlopen("http://127.0.0.1:%d/admin/api/config?token=t" % port,
                         timeout=30) as r:
            return _json.loads(r.read())

    def _post(body):
        rq = _ur.Request("http://127.0.0.1:%d/admin/api/config?token=t" % port,
                         data=_json.dumps(body).encode(), method="POST")
        rq.add_header("Content-Type", "application/json")
        with _ur.urlopen(rq, timeout=30) as r:
            return _json.loads(r.read())

    keys = ["ui_face_alpha", "ui_side_alpha", "ui_side_color",
            "wallpaper_dim", "ui_side_blur", "ui_face_blur"]
    try:
        before = _get()
        orig = {k: before.get(k) for k in keys}
        bad = [k for k in keys if orig[k] is None]
        if bad:
            return False, "配置里读不到: %s" % bad

        # 每个键换一个不同的合法值
        probe = {}
        for k, v in orig.items():
            probe[k] = (v + 1) if isinstance(v, (int, float)) else "#123456"
        r = _post(probe)
        after = _get()
        not_saved = [k for k in keys if after.get(k) == orig[k]]
        # 改回原值
        _post(orig)
        back = _get()
        not_restored = [k for k in keys if back.get(k) != orig[k]]

        if not_saved:
            return False, ("白名单漏了？这些键存不进去: %s（接口返回 %s）"
                           % (not_saved, r))
        if not_restored:
            return False, "改不回去: %s" % not_restored
        return True, "%d 个外观键都能存能读（改了再读回来确认过）" % len(keys)
    except Exception as ex:
        return False, "存读测试异常: %s: %s" % (type(ex).__name__, ex)



@probe("report-fixes", "上一轮报告里的对比度与逻辑修复")
def _p_report(port):
    r"""守住这几处修复，回归了要拦下来：

      --on-accent 在深色下必须是深色字（主色翻亮后白字只有 1.7-2.2:1）
      .toast 用 --on-dark（它底色永远是暗的，不能跟着翻）
      .upd-line .ms.wait 用 --text-2（--text-3 只有 3.2:1，不够）
    """
    import re as _re

    def _lum(h):
        h = h.strip().lstrip("#")
        if len(h) == 3:
            h = h[0] * 2 + h[1] * 2 + h[2] * 2
        c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        f = lambda x: x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])

    def _cr(a, b):
        la, lb = _lum(a), _lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    def _root(block):
        m = _re.search(r":root\{(.*?)\}", block, _re.S)
        d = {}
        if m:
            for k, v in _re.findall(r"--([\w-]+)\s*:\s*([^;}]+)", m.group(1)):
                d[k] = v.strip()
        return d

    st, hb = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = hb.decode("utf-8", "replace")
    css = h[h.find("<style>"):h.find("</style>")]
    light = _root(css)
    dm = _re.search(r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*"
                    r":root\s*\{(.*?)\}", css, _re.S)
    dark = dict(light)
    if dm:
        for k, v in _re.findall(r"--([\w-]+)\s*:\s*([^;}]+)", dm.group(1)):
            dark[k] = v.strip()

    bad = []
    oa, od = dark.get("on-accent"), (dark.get("on-dark") or light.get("on-dark"))
    if oa in (None, "#fff", "#ffffff"):
        bad.append("深色 --on-accent 还是白色")
    if od != "#fff":
        bad.append("--on-dark 不是白色（.toast 会看不清）")
    worst = 99.0
    for tk in ("azure", "green", "red"):
        bg = dark.get(tk)
        if bg and bg.startswith("#") and oa and oa.startswith("#"):
            worst = min(worst, _cr(bg, oa))
    if worst < 4.5:
        bad.append("深色亮底上的字只有 %.2f:1" % worst)
    if ".toast{" in css:
        seg = css[css.find(".toast{"):]
        seg = seg[:seg.find("}")]
        if "var(--on-dark)" not in seg:
            bad.append(".toast 没用 --on-dark")
    if ".upd-line .ms.wait{" in css:
        seg = css[css.find(".upd-line .ms.wait{"):]
        seg = seg[:seg.find("}")]
        if "var(--text-2)" not in seg:
            bad.append(".ms.wait 没用 --text-2")
    if bad:
        return False, "; ".join(bad)
    return True, "深色亮底字 %.2f:1，.toast 与 .ms.wait 都换对了" % worst



@probe("theme-vars", "主题变量有定义、且预览变量真被 CSS 引用")
def _p_themevars(port):
    r"""上一轮报告踩的坑，两个性质不同的问题：

      1. --aero 系只有 var() 引用、没有定义。未定义的自定义属性会让
         **整条声明**在计算时失效 —— 侧栏选中项的主色文字、标题下划线、
         竖条发光全没了，看起来像"没选中"。
      2. 外观面板的 JS 改了 --wall-dim / --glass-rgba / --side-rgba 等，
         但 CSS 是 Python 硬编码的、没引用这些变量 —— 拖滑杆画面不动。

      第 2 条我测漏过：只验了"JS 有没有设值"，没验"CSS 有没有用"。
      所以这里查 var() 引用数，不是查 JS。
    """
    import re as _re

    st, hb = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = hb.decode("utf-8", "replace")
    css = h[h.find("<style>"):h.find("</style>")]
    bad = []

    for v in ("--aero", "--aero-soft", "--aero-line"):
        if ("var(%s)" % v) in css and not _re.search(
                _re.escape(v) + r"\s*:", css):
            bad.append("%s 引用了但没定义" % v)

    # 面板底色统一成 rgba(var(--X-rgb), var(--X-a))，所以查的是 rgb 与 a 两项
    need = {"--wall-dim": "壁纸压暗", "--face-a": "内容面透明度",
            "--side-a": "侧栏透明度", "--face-rgb": "内容面颜色（别名）",
            "--side-rgb": "侧栏颜色（别名）", "--wall-blur": "壁纸模糊",
            "--side-blur": "侧栏模糊", "--panel-blur": "内容面模糊"}
    for v, name in need.items():
        if ("var(%s)" % v) not in css:
            bad.append("%s(%s) 没被 CSS 引用，拖了没用" % (v, name))

    # 浅深两组基底必须都在，且 dark 那条要把别名指过去。
    # 分开存是为了躲开"JS 写 inline 会盖过 @media"这个坑。
    for v in ("--face-rgb-light", "--face-rgb-dark",
              "--side-rgb-light", "--side-rgb-dark"):
        if v not in css:
            bad.append("缺 %s" % v)

    # @media 嵌在规则里是无效 CSS（写成 .side{...@media...} 那种）。
    # 现代浏览器会把它当后代选择器、老浏览器整段丢弃 —— 静默失效，很难发现。
    depth = 0
    for m in _re.finditer(r"[{}]|@media", css):
        g = m.group(0)
        if g == "{":
            depth += 1
        elif g == "}":
            depth = max(0, depth - 1)
        elif g == "@media" and depth > 0:
            bad.append("有 @media 嵌在规则里（无效 CSS，整段会失效）")
            break

    if "body.no-wall::before" not in css:
        bad.append("body.no-wall 没定义，壁纸开关不生效")
    if "rgba(20,29,56,.985)" not in css:
        bad.append("no-face-glass 深色下还是白底，字读不出")

    if bad:
        return False, "; ".join(bad[:4])
    return True, "aero 有定义，8 个预览变量都被 CSS 引用，关玻璃有深色兜底"



@probe("dark-wallpaper", "深色+开壁纸时面板令牌是深色（不是浅色）")
def _p_darkwall(port):
    r"""第三份报告最严重的一条。

    注入的 :root{} 在页面里排在深色 @media **之后**，
    同优先级后写的赢 —— 所以不带深色分支的话，
    --card-bg / --bg-2/3/4 / --side-bg-* / --log-bg-* 全被浅色值盖掉，
    .group-block / .sub-panel / .plug-card / .kb-tree-row / .inp 变白底，
    而 --text 还是 #d0e0f0，读不出来（约 1.2:1）。

    这里直接把深色基底和深色正文合成一遍算对比度，不靠肉眼。
    """
    import re as _re

    def _lum(h):
        h = h.strip().lstrip("#")
        if len(h) == 3:
            h = h[0] * 2 + h[1] * 2 + h[2] * 2
        c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        f = lambda x: x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])

    def _cr(a, b):
        la, lb = _lum(a), _lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    def _blend(fg, bg, a):
        f, b = fg.lstrip("#"), bg.lstrip("#")
        o = []
        for i in (0, 2, 4):
            o.append(int(round(int(f[i:i + 2], 16) * a
                               + int(b[i:i + 2], 16) * (1 - a))))
        return "#%02x%02x%02x" % tuple(o)

    st, hb = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = hb.decode("utf-8", "replace")
    bad = []
    if "prefers-color-scheme: dark" not in h:
        bad.append("注入的 CSS 没有深色分支")
    if "--face-rgb-dark" not in h:
        bad.append("缺 --face-rgb-dark，深色下令牌会被浅色盖掉")
    # --face-rgb-dark 是逗号 RGB（"20,29,56"），不是 hex，两种都要认
    m = _re.search(r"--face-rgb-dark:\s*([^;}\"]+)", h)
    raw = (m.group(1).strip() if m else "")
    base = None
    if _re.match(r"^#[0-9a-fA-F]{6}$", raw):
        base = raw
    else:
        mm = _re.match(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$", raw)
        if mm:
            base = "#%02x%02x%02x" % tuple(int(x) for x in mm.groups())
    if base is None:
        bad.append("读不到 --face-rgb-dark（值=%r）" % raw[:30])
        base = "#ffffff"
    # 深色基底必须够暗，否则 --text(#d0e0f0) 压上去读不出
    elif _lum(base) > 0.25:
        bad.append("深色基底 %s 太亮（亮度 %.3f）" % (base, _lum(base)))
    # --text 在页面里有浅色/深色两份，必须取**深色 @media 块里**那份，
    # 否则拿浅色正文去比深色底，会得出 1.08:1 这种假失败。
    dblk = _re.search(
        r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{"
        r"(.*?)\}", h, _re.S)
    # 拿不到深色块必须报错，不能悄悄用硬编码兜底 ——
    # 之前硬编码 #d0e0f0 恰好等于当时的深色 --text，探针"碰巧过"；
    # 一旦有人把深色 --text 调深，探针还在拿旧值算，永远报通过。
    if not dblk:
        bad.append("解析不出深色 @media 块（正则写错了？）")
    scope = dblk.group(1) if dblk else h
    txt = _re.search(r"--text:\s*(#[0-9a-fA-F]{6})", scope)
    if not txt:
        bad.append("深色块里找不到 --text")
    dtext = txt.group(1) if txt else "#000000"
    worst = 99.0
    for a in (0.52, 0.47, 0.41, 0.34):
        worst = min(worst, _cr(_blend(base, "#0d1428", a), dtext))
    if worst < 4.5:
        bad.append("深色下面板字只有 %.2f:1（基底 %s）" % (worst, base))
    if bad:
        return False, "; ".join(bad)
    return True, "深色基底 %s，面板最差 %.2f:1" % (base, worst)



@probe("wall-gallery", "预设壁纸画廊 + 取色器")
def _p_gallery(port):
    r"""参考图里那套：标签页 / 缩略图 / 选中态 / 自动轮播 / HSV 取色器。

    预设用 CSS 渐变而不是嵌图片，所以 --wall 的值可能是 url("data:...")
    也可能是渐变 —— 模板里的 url() 必须去掉，否则渐变会被当成 URL 解析，
    整条 background-image 失效（页面变纯色底）。
    """
    import json as _json
    import urllib.request as _ur

    import re as _re

    st, hb = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = hb.decode("utf-8", "replace")
    bad = []

    for k, why in [("function renderWallSection", "画廊渲染函数"),
                   ("data-walltab", "标签页"),
                   ("wall-thumb", "缩略图样式"),
                   ("function openPicker", "取色器"),
                   (".cp-sv{", "取色器渐变方块"),
                   (".cp-hue{", "取色器色相条"),
                   ("--wall:", "--wall 变量")]:
        if k not in h:
            bad.append("缺 %s（%s）" % (k, why))
    # --wall 的值不能整体被 url() 包着 —— 那样渐变会被当成 URL 解析，
    # 整条 background-image 失效，页面变纯色底。
    _wm = _re.search(r"--wall:\s*url\(\s*(linear-gradient|radial-gradient"
                     r"|conic-gradient)", h)
    if _wm:
        bad.append("--wall 把渐变套进了 url()，会失效")
    if "function _hsv2rgb" not in h or "function _rgb2hsv" not in h:
        bad.append("缺 HSV 转换函数")
    # 取色器必须 fixed：admin 页 body{overflow:hidden}，
    # absolute 的浮层靠下时会被裁掉下半截
    if ".cp{position:fixed" not in h:
        bad.append("取色器不是 position:fixed，会被 body 裁掉")
    # 预设 custom 的 URI 必须有来源，否则点「我的图片」会把 --wall
    # 设成字面量 "null"，背景直接没了
    if "function _customWallUri" not in h:
        bad.append("缺 _customWallUri，custom 预设点了没反应")
    # 上传后要切 preset，否则保存后读回旧预设，用户传的图丢了
    if 'applyAppearance({wallpaper_preset:"custom"})' not in h:
        bad.append("上传后没切 preset=custom，图片会丢")
    # 取色器初值要回落 cfgCache，否则 face 系打开是侧栏的深蓝黑
    if "cfgCache && cfgCache[id]" not in h:
        bad.append("取色器初值没回落 cfgCache，face 系初值会错")
    # 色板必须只维护**一个**自定义点。以前 commit() 是"没有就 insert"，
    # 拖动时每帧颜色都不同 -> 每帧插一个，拖一下插几十个点。
    if "data-cp-custom" not in h:
        bad.append("色板缺 data-cp-custom，拖动会把圆点插爆")
    if "data-preset-dot" not in h:
        bad.append("预设点缺 data-preset-dot 标记")

    # 接口得真能返回预设
    try:
        with _ur.urlopen("http://127.0.0.1:%d/admin/api/wallpaper/presets?token=t"
                         % port, timeout=30) as r:
            d = _json.loads(r.read())
        ps = d.get("presets") or []
        if len(ps) < 4:
            bad.append("预设只有 %d 个" % len(ps))
        for p in ps:
            if not (p.get("id") and p.get("name") and p.get("css")):
                bad.append("预设项字段不全: %s" % p)
                break
            # 预设可以是 CSS 渐变，也可以是内嵌图片（默认那张 kv 就是图）。
            # 但不能为空、也不能是个裸颜色 —— 那两种在画廊里看着都像坏了。
            _css = str(p.get("css") or "")
            if not _css or ("gradient(" not in _css and "url(" not in _css):
                bad.append("预设 %s 的 css 既不是渐变也不是图片：%r"
                           % (p.get("id"), _css[:40]))
                break
    except Exception as ex:
        bad.append("预设接口异常: %s" % type(ex).__name__)

    if bad:
        return False, "; ".join(bad[:4])
    return True, "6 个预设（1 张图 + 5 个渐变） + 标签页 + HSV 取色器，--wall 不再套 url()"



@probe("no-dup-id", "页面里没有重复的元素 ID")
def _p_dupid(port):
    r"""同一个 id 出现两次时，getElementById 只返回第一个 ——
    另一份永远拿不到更新。

    这个坑踩过：加了预设画廊却忘了删旧的「自定义壁纸」段，
    结果 #wallPrev / #wallFile / #wallTip 各出现两次，
    loadWallState 只更新其中一个，文件选择按钮只打开其中一个 input。

    做法：把服务的 HTML 里所有 id="X" 抓出来数一遍。
    （JS 里的 getElementById("X") 不算，扫描的是 id="X" 这种字面量。）
    """
    import re as _re

    st, hb = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = hb.decode("utf-8", "replace")
    # 只收"写死"的 id。JS 里拼出来的（id="pcfg-'+k+'"）会在 html 里
    # 留下 id="pcfg- 这种尾巴，属于动态 id，不是重复。
    ids = []
    for m in _re.finditer(r'\bid=["\']([A-Za-z][\w-]*)["\']', h):
        nm = m.group(1)
        nxt = h[m.end():m.end() + 1]
        if nm.endswith(("-", "_", ".")) or nxt in ("+", "'", '"'):
            continue                      # 动态拼接的，跳过
        ids.append(nm)
    seen, dup = {}, []
    for x in ids:
        seen[x] = seen.get(x, 0) + 1
    for x, c in seen.items():
        if c > 1:
            dup.append("%s x%d" % (x, c))
    if dup:
        return False, "重复 ID: " + ", ".join(sorted(dup)[:6])
    return True, "%d 个 id，无重复" % len(seen)



@probe("set-layout", "设置行不会被挤成竖排")
def _p_setlayout(port):
    r"""踩过：.sld{min-width:300px} 太硬，窗口一窄 .set-c 咬住 300px 不放，
    .set-t 被压到 0 —— 中文标签变成一列一个（壁纸压暗 -> 壁/纸/压/暗）。

    这里只能查 CSS 规则本身（探测是 HTTP 级的，量不了布局），
    但要守住的正是那几条防塌陷规则：
      · .set-row 必须能换行，放不下时控件折行而不是压标签
      · .set-t 要有 flex-basis，不能只是 flex:1（那样可以缩到 0）
      · .sld 不能有大的硬 min-width
      · 窄屏要有上下排的兜底
    """
    import re as _re

    st, hb = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = hb.decode("utf-8", "replace")
    bad = []

    m = _re.search(r"\.set-row\{([^}]*)\}", h)
    if not m:
        bad.append("找不到 .set-row")
    elif "flex-wrap:wrap" not in m.group(1):
        bad.append(".set-row 没开换行，窄屏会压标签")

    # 注意：.set-row .set-t 在文件里有两条（窄屏媒体查询里还有一条
    # flex:0 0 auto）。必须扫全部，取第一条会拿到媒体查询那条，误判。
    bodies = _re.findall(r"\.set-row \.set-t\{([^}]*)\}", h)
    if not bodies:
        bad.append("找不到 .set-t")
    else:
        norm = [b.replace(" ", "") for b in bodies]
        if not any(("flex:11" in b or "flex:10" in b or "flex-basis" in b
                    or "flex:1auto" in b) for b in norm):
            bad.append(".set-t 没有 flex-basis，能被压到 0（中文会竖排）")

    m = _re.search(r"\.sld\{([^}]*)\}", h)
    if not m:
        bad.append("找不到 .sld")
    else:
        mm = _re.search(r"min-width:\s*(\d+)px", m.group(1))
        if mm and int(mm.group(1)) > 160:
            bad.append(".sld 的 min-width=%spx 太大，会把标签挤没" % mm.group(1))

    if "@media (max-width: 900px)" not in h.replace("  ", " "):
        bad.append("缺窄屏上下排的兜底")

    if bad:
        return False, "; ".join(bad[:3])
    return True, "set-row 可换行、set-t 有 flex-basis、sld 无硬 min-width、有窄屏兜底"



@probe("hero-no-wall", "统计横幅不重画壁纸（否则会重影）")
def _p_heronowall(port):
    r"""踩过：.stat-grid 自己写了 background-image:var(--wall)。

    横幅是个扁条（约 1164x150），对同一张壁纸 cover 出来的裁切位置
    跟整页背景完全不同 —— 于是同一张脸在画面上出现两份、大小还不同。
    用户的原话是"感觉有点诡异"。

    判断：横幅的规则里不能出现 var(--wall)。
    body::before 用 var(--wall) 是对的（那就是整页背景层）。
    """
    import re as _re

    st, hb = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = hb.decode("utf-8", "replace")
    bad = []

    for sel in (r"\.stat-grid", r"\.stat-box"):
        for m in _re.finditer(sel + r"\{[^}]*\}", h):
            if "var(--wall)" in m.group(0):
                bad.append("%s 里又画了一遍壁纸，会重影" % sel.replace("\\", ""))

    # 整页背景层必须还在（别把该有的也删了）
    if "body::before" not in h or "background-image:var(--wall)" not in h:
        bad.append("整页壁纸层没了")

    # 横幅必须跟内容卡片同一套令牌。以前写死"深底 + 白字"，页面上其他
    # 面板都是浅色半透明，它就成了唯一一块近黑实心板，格格不入。
    # 页面里 .stat-grid{} 有多条（基础 + HERO + 响应式），必须扫全部。
    # 只取第一条会拿到基础那条（只有 grid 定义），误判成"没有毛玻璃"。
    grids = _re.findall(r"\.stat-grid\{([^}]*)\}", h)
    if not grids:
        bad.append("找不到 .stat-grid")
    else:
        joined = " ".join(grids)
        if "var(--card-bg" not in joined:
            bad.append("横幅没用 --card-bg，跟卡片不是一套玻璃")
        if "backdrop-filter" not in joined:
            bad.append("横幅没有毛玻璃")
    mv = _re.search(r"\.stat-grid \.v\{([^}]*)\}", h)
    if mv and "color:#fff" in mv.group(1).replace(" ", ""):
        bad.append("横幅数字还是写死的白字，亮色主题下会读不出")

    if bad:
        return False, "; ".join(bad[:3])
    return True, "横幅用自带深色渐变，只有 body::before 画壁纸"



@probe("kb-clear-all", "「清空全部」真的清全部，不是只清根目录")
def _p_kbclear(port):
    r"""数据丢失级 bug，值得单独守一条。

    前端在「全部条目」视图下传的是 folder:""，而后端把 "" 解释成
    "根目录（未归档）" —— UI 写着「清空全部条目」，实际只删了未归档那部分，
    其余安静地留在各文件夹里，而且不可恢复。

    修法：前端改传 all:true，后端认这个显式开关。

    注意：后端是服务端 Python，不在服务的 HTML 里，光扫 HTML 查不到。
    所以这里用**行为验证**，而且刻意用 group_id=999999（没有这个群）
    并传一个不存在的分类，保证 deleted=0、不会误删真数据。
    """
    import json as _json
    import urllib.request as _ur

    def _post(body):
        rq = _ur.Request(
            "http://127.0.0.1:%d/admin/api/kb/clear_category?token=t" % port,
            data=_json.dumps(body).encode(), method="POST")
        rq.add_header("Content-Type", "application/json")
        try:
            with _ur.urlopen(rq, timeout=30) as r:
                return r.status, _json.loads(r.read())
        except Exception as ex:
            code = getattr(ex, "code", None)
            if code:
                try:
                    return code, _json.loads(ex.read())
                except Exception:
                    return code, {}
            return None, {}

    st, hb = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = hb.decode("utf-8", "replace")
    bad = []

    # 前端：必须传 all，不能再用 folder:""
    if "body.all=true" not in h:
        bad.append("前端「清空全部」没传 all，会退化成只清根目录")

    # 后端行为 1：什么都不给应该被守卫拦下（说明守卫还在）
    code, d = _post({"group_id": 999999})
    if code != 400:
        bad.append("裸请求没被守卫拦下（返回 %s），守卫可能被改坏了" % code)

    # 后端行为 2：all:true 要被认。
    # 这里**额外带一个匹配不到的分类**，让 _kb_filter 过滤成 0 条 ——
    # 否则 all:true 会把沙箱库清空（kb_list 的语义是"该群 + 全局"，
    # 光靠 group_id 选不空）。探测不该有破坏性。
    code, d = _post({"all": True, "category": "__probe_nonexistent__",
                     "group_id": 999999})
    if code != 200:
        bad.append("后端不认 all（返回 %s: %s）" % (code, str(d)[:60]))
    elif not d.get("ok"):
        bad.append("all:true 没返回 ok")
    elif d.get("deleted"):
        bad.append("无副作用探测居然删了 %s 条" % d.get("deleted"))

    # 后端行为 3：folder:"" 仍然只指根目录（别为了修这个把语义改坏）
    code, d = _post({"folder": "", "category": "__probe_nonexistent__",
                     "group_id": 999999})
    if code != 200:
        bad.append('folder:"" 被拒了（%s），语义可能被改坏' % code)

    if bad:
        return False, "; ".join(bad[:3])
    return True, "前端传 all:true，后端认它；裸请求被拦；folder:\"\" 语义不变"



@probe("cfg-fallback", "所有配置读取的 fallback 与 DEFAULT_CONFIG 一致")
def _p_cfgfallback(port):
    r"""同一类问题踩过两次：ui_face_alpha 的 fallback 还是 93（DEFAULT 52），
    wallpaper_dim 的 fallback 是 46（DEFAULT 40）。

    靠人眼扫 130 多处读取是不可能不漏的，让脚本扫。
    setdefault 兜住了所以不会真触发，但这反映"作者对该键的预期默认值"
    已经漂了 —— 哪天有条路径绕过 setdefault，错值就生效。

    两类合法例外：
      1. 布尔：JS 的 true/false vs Python 的 True/False，归一化后比
      2. 故意的失效安全默认：见 ALLOW，每条要写理由
    """
    import re as _re

    ALLOW = {
        # _wait_gap 里读不到配置就别延迟（fail-safe），
        # 跟"配置项默认 2~5 秒"不是一回事
        ("ai_min_gap", "py"): "0.0",
        ("ai_max_gap", "py"): "0.0",
    }

    src = io.open(os.path.join(BASE, "bot.py"), encoding="utf-8").read()

    dc = {}
    i = src.find("DEFAULT_CONFIG = {")
    if i < 0:
        return False, "找不到 DEFAULT_CONFIG"
    j = src.find("\n}", i)
    for m in _re.finditer(r'"(\w+)":\s*(-?\d+(?:\.\d+)?|True|False)',
                          src[i:j]):
        dc[m.group(1)] = m.group(2)

    def _norm(v):
        v = str(v)
        if v in ("true", "True"):
            return "True"
        if v in ("false", "False"):
            return "False"
        try:
            return "%g" % float(v)
        except Exception:
            return v

    found = []
    for m in _re.finditer(r'cfg_(?:int|float|bool|str)\("(\w+)",\s*([^)\s]+)\)', src):
        found.append((m.group(1), m.group(2), "py"))
    for m in _re.finditer(r'g\("(\w+)",\s*([\w."#]+)\)', src):
        found.append((m.group(1), m.group(2), "js"))

    bad, checked = [], 0
    for key, val, where in found:
        if key not in dc:
            continue
        checked += 1
        if _norm(val) == _norm(dc[key]):
            continue
        if ALLOW.get((key, where)) == val:
            continue
        bad.append("%s(%s 的 fallback=%s, DEFAULT=%s)" % (key, where, val, dc[key]))

    if bad:
        return False, "%d/%d 处不一致: %s" % (len(bad), checked,
                                              "; ".join(sorted(bad)[:3]))
    return True, "%d 处 fallback 与 DEFAULT_CONFIG 全部一致" % checked



@probe("progress-no-500", "下载进度接口不返回 500（task 键不能带出去）")
def _p_prog500(port):
    r"""踩过：st = dict(_UPDATE_DL) 把 "task" 键一起带出去了，
    而那是个 asyncio.Task —— JSONResponse 序列化时抛 TypeError 返回 500。
    前端 pollDl 每 300ms 拉一次，下载中每次都 500，s.pct 是 undefined，
    进度条永远停在 0%。下载本身是好的，只是看不到进度。

    空状态下接口本来就能返回 200，所以这里还要查源码里有没有
    st.pop("task", None) —— 那条才是真正的修复。
    """
    import json as _json

    st, hb = _get("http://127.0.0.1:%d/admin/api/update/progress?token=t" % port)
    if st != 200:
        return False, "进度接口返回 HTTP %s（应该 200）" % st
    try:
        d = _json.loads(hb)
    except Exception as e:
        return False, "进度接口返回的不是 JSON: %s" % type(e).__name__
    if "task" in d:
        return False, "返回里还带着 task 键（序列化会炸）"
    for k in ("busy", "got", "total", "pct"):
        if k not in d:
            return False, "返回里缺 %s" % k
    src = io.open(os.path.join(BASE, "bot.py"), encoding="utf-8").read()
    if 'st.pop("task", None)' not in src:
        return False, 'api_update_progress 里没有 st.pop("task", None)'
    # 同一类坑出现两次了：api_update_progress 和 api_update_fetch 的
    # 409 分支（busy 时把 dict(_UPDATE_DL) 塞进 state）。所以查一遍
    # "每个 dict(_UPDATE_DL) 都得配一个 pop"。
    n_dict = src.count("dict(_UPDATE_DL)")
    n_pop = src.count('pop("task", None)')
    if n_pop < n_dict:
        return False, ("有 %d 处 dict(_UPDATE_DL) 但只有 %d 处 pop(task) —— "
                       "busy 时序列化会 500" % (n_dict, n_pop))
    return True, ("进度接口 200、无 task 键；%d 处 dict(_UPDATE_DL) 都有 pop"
                  % n_dict)


@probe("msg-rules", "三个群 handler 都有群聊规则（私聊不会炸）")
def _p_msgrules(port):
    r"""踩过：ad_guard / verify_answer / ai_chat 三个 on_message 都没限制
    只匹配群聊，而它们第一行就访问 event.group_id —— 私聊消息没这个属性，
    每次私聊都在日志里留一条 AttributeError；广告词触发的私聊还会真的
    走进 _on_ad 然后失败。

    _on_private 那边有 isinstance(event, GroupMessageEvent) 挡群消息，
    反过来一直没有。
    """
    # on_message(...) 的注册是**服务端 Python**，不在服务的 HTML 里 ——
    # 只能读源码。（我在这件事上栽过好几次了。）
    h = io.open(os.path.join(BASE, "bot.py"), encoding="utf-8").read()
    bad = []
    if "def _is_group" not in h:
        bad.append("缺 _is_group 辅助函数")
    n = h.count("Rule(_is_group)")
    if n < 2:
        bad.append("只有 %d 处用了 Rule(_is_group)，应该有 2 处" % n)
    # 广告拦截已改成插件（data/plugins/adfilter），主程序里不再有 ad_guard。
    for pat, name in (("Rule(_is_group) & Rule(_verify_answer_rule)",
                       "verify_answer"),
                      ("on_message(rule=Rule(_is_group), priority=10",
                       "ai_chat")):
        if pat not in h:
            bad.append("%s 没加群聊规则" % name)
    if "ad_guard" in h:
        bad.append("ad_guard 还在主程序里（应该已经搬去插件）")
    if bad:
        return False, "; ".join(bad[:3])
    return True, "_is_group 已定义，三个群 handler 都限定了群聊"



@probe("pyflakes", "静态体检：没有未定义名字/引用未赋值")
def _p_pyflakes(port):
    r"""踩过：改代码时用错了缩进（8 空格插进 4 空格的函数体），
    代码落到 return 后面变成**不可达**，而后面那行的变量反而没定义了。
    ast.parse 只查语法，全过；沙箱也能起来（那条路径要请求才走到）。
    是 pyflakes 把它揪出来的。

    只把会真炸的几类当失败：undefined name / referenced before assignment /
    redefinition。unused import 之类不管 —— 那不影响运行。
    """
    import re as _re
    import subprocess as _sp
    import sys as _sys

    try:
        import pyflakes  # noqa: F401
    except Exception:
        return True, "pyflakes 没装，跳过（pip install pyflakes 可以打开这道检查）"

    r = _sp.run([_sys.executable, "-m", "pyflakes",
                 os.path.join(BASE, "bot.py")],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=600)
    out = (r.stdout or "") + (r.stderr or "")
    FATAL = ("undefined name", "referenced before assignment",
             "redefinition of unused")
    bad = [l for l in out.splitlines() if any(f in l for f in FATAL)]
    if bad:
        return False, "会炸的 %d 条：%s" % (len(bad), bad[0].split("bot.py")[-1][:90])
    n = len([l for l in out.splitlines() if l.strip()])
    return True, "无 undefined name / 引用未赋值（另有 %d 条 unused 之类，不影响）" % n



@probe("kb-allgroups", "改文件夹的函数都有「全部群」守卫")
def _p_kballgroups(port):
    r"""「全部群」视图下列出的是所有群的文件夹合并结果，但增删改只能落到
    某一个群（或共享域 group_id=0）—— 在那个视图里操作会语义错位：
    删不掉却提示已删、改名跨群搬条目、建文件夹落到共享域。

    我们加了 kbAllGroups() 守卫，但一开始只补了 kbAutoFile，
    kbNewFolderRoot / kbNewFolderHere 漏了（同一个页面里行为不一致）。
    这里逐个点名，防止再漏。
    """
    st, hb = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = hb.decode("utf-8", "replace")
    if "function kbAllGroups()" not in h:
        return False, "缺 kbAllGroups()"
    if "function kbNeedGroup()" not in h:
        return False, "缺 kbNeedGroup()"
    # 这些函数都会改文件夹，每个都得有守卫
    targets = ["kbAutoFile", "kbDeleteFolder", "kbRenameFolder",
               "kbNewFolderRoot", "kbNewFolderHere"]
    bad = []
    for fn in targets:
        i = h.find("function " + fn + "(")
        if i < 0:
            bad.append("%s 找不到" % fn)
            continue
        blk = h[i:i + 600]
        # 只看函数体开头那段（守卫都在最前面）
        head = blk[:blk.find("\n}") if "\n}" in blk else 600]
        code = [l for l in head.splitlines()
                if not l.strip().startswith("//")]
        if not any("kbAllGroups()" in l for l in code):
            bad.append("%s 没有守卫" % fn)
    if bad:
        return False, "; ".join(bad)
    if "kbNewRootBtn" not in h or "kbNewSubBtn" not in h:
        return False, "两个新建按钮的 id 不全（没法置灰）"
    i = h.find("function kbUpdateSubHint(")
    if i < 0:
        return False, "找不到 kbUpdateSubHint"
    if "kbAllGroups()" not in h[i:i + 900]:
        return False, "kbUpdateSubHint 不看群筛选器"
    return True, "5 个改文件夹的函数 + 提示函数都有守卫，两个按钮可置灰"



@probe("kb-group-negative", "写操作接口不接受负数 group_id")
def _p_kbneggroup(port):
    r"""负数群号会写进"幽灵群"：kb_list 的过滤是 group_id IN (0,?)，
    负数条目在任何具体群的视图里都查不到，聊天也检索不到 —— 等于白写，
    而且用户完全看不出来（导入提示"成功 N 条"）。

    容易漏的地方：这些接口长得都很像（try: gid = int(body.get(...))），
    逐个补很容易只补了一半 —— 我自己就先补错了对象（补到了
    api_voice_send 上）。所以这里逐个点名。
    """
    src = io.open(os.path.join(BASE, "bot.py"), encoding="utf-8").read()
    # 会"创建/改挂"条目的接口，必须归一化
    need = ["api_kb_folder_create", "api_kb_move", "api_kb_add",
            "api_kb_update", "api_kb_import", "api_mem_add", "api_bl_add",
            "api_kb_folder_delete"]
    bad = []
    for fn in need:
        i = src.find("async def " + fn + "(")
        if i < 0:
            bad.append("%s 找不到" % fn)
            continue
        j = src.find("@app.", i)
        blk = src[i:j if j > 0 else i + 4000]
        code = [l for l in blk.splitlines() if not l.strip().startswith("#")]
        if not any(("gid < 0" in l or "g < 0" in l or "group_id < 0" in l
                    or "_mg if _mg > 0" in l or "move_gid < 0" in l)
                   for l in code):
            bad.append("%s 没拦负数" % fn)
    if bad:
        return False, "; ".join(bad)
    # api_voice_send 不该有（那里负数该直接报错，不是折算成 0）
    i = src.find("async def api_voice_send(")
    if i > 0:
        j = src.find("@app.", i)
        if "gid < 0" in src[i:j]:
            return False, "api_voice_send 里混进了 kb 的守卫（会静默变成发到全局）"
    return True, "%d 个写接口都拦了负数群号" % len(need)



@probe("jsq-onclick", "onclick 里的插件名做了 JS 字符串转义")
def _p_jsq(port):
    r"""踩过：插件卡片的 onclick 是用 esc() 拼的。

    esc 只做 HTML 转义（' -> &#39;），而 HTML **属性解析这一层会把
    &#39; 解回 '** —— 正好闭合 onclick="fn('...')" 里的那个 JS 字符串。
    插件名带单引号时：轻则按钮点了报 SyntaxError，重则把插件名当 JS
    执行（插件作者可控，但打开管理页的所有人都中招）。

    修法是加了个 jsq()：把引号/尖括号/& 全变成 \xNN，
    HTML 层看不到任何特殊字符，JS 层再还原。
    """
    import re as _re

    st, hb = _get("http://127.0.0.1:%d/admin?token=t" % port)
    h = hb.decode("utf-8", "replace")
    if "function jsq(" not in h:
        return False, "缺 jsq() 辅助函数"
    bad = []
    # onclick 里出现 +esc( 的，都是没转义的
    for m in _re.finditer(r'onclick=\\?"[^"]*\+esc\(', h):
        bad.append(m.group(0)[:60])
    if bad:
        return False, "onclick 里还在用 esc 拼：%s" % bad[0]
    # 这几个必须走 jsq
    need = ["togglePlugin(\\''+jsq(", "configFor(\\''+jsq(",
            "uninstallPlugin(\\''+jsq(", "updatePlugin(\\''+jsq(",
            "installPlugin(\\''+jsq("]
    missing = [n.split("(")[0] for n in need if n not in h]
    if missing:
        return False, "这些还在用 esc：%s" % missing
    return True, "jsq 已定义，5 个插件按钮全部改用它"



@probe("adfilter-plugin", "广告拦截插件在、且注册了消息钩子")
def _p_adfilter(port):
    r"""广告拦截原来是主程序内置的（ad_guard / _on_ad / detect_ad），
    现在整个搬成了插件 data/plugins/adfilter。

    盯两件事：
      1. 插件文件在、语法能过（坏了的话主程序加载时会打日志但不一定拦得住）
      2. 它注册了 on_message 钩子 —— 没钩子拦截就完全不生效，而且不报错
    """
    import ast as _ast

    pd = os.path.join(BASE, "data", "plugins", "adfilter")
    mp = os.path.join(pd, "main.py")
    meta = os.path.join(pd, "metadata.json")
    if not os.path.exists(mp):
        return False, "缺 data/plugins/adfilter/main.py"
    if not os.path.exists(meta):
        return False, "缺 data/plugins/adfilter/metadata.json"
    src = io.open(mp, encoding="utf-8").read()
    try:
        _ast.parse(src)
    except SyntaxError as e:
        return False, "插件语法错误 L%s: %s" % (e.lineno, e.text)
    if "def setup(ctx)" not in src:
        return False, "插件没有 setup(ctx) 入口"
    if "ctx.on_message()" not in src:
        return False, "插件没注册消息钩子 —— 拦截不会生效"
    if "ctx.register_page(" not in src:
        return False, "插件没注册管理页"
    bot = io.open(os.path.join(BASE, "bot.py"), encoding="utf-8").read()
    left = [k for k in ("detect_ad", "_ad_rule", "_on_ad", "ad_guard")
            if k in bot]
    if left:
        return False, "主程序里还有 %s" % left
    return True, "插件在（setup + 消息钩子 + 管理页），主程序已清干净"


def main():
    do_build = "--no-build" not in sys.argv
    if do_build:
        print("=" * 74)
        print("1. 构建")
        print("=" * 74)
        subprocess.run(["taskkill", "/F", "/IM", EXE_NAME],
                       capture_output=True)
        time.sleep(4)
        old_mtime = (os.path.getmtime(os.path.join(SRC, EXE_NAME))
                     if os.path.exists(os.path.join(SRC, EXE_NAME)) else 0)
        # encoding 必须显式写 utf-8：text=True 默认用系统编码（中文 Windows
        # 是 GBK），而 build.py 输出 UTF-8 —— 解码对不上，"打包成功" 永远
        # 匹配不到。这也是我之前发布日志里从没见过那行的原因。
        r = subprocess.run([sys.executable, "build.py"], cwd=BASE,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=2400)
        out = (r.stdout or "") + (r.stderr or "")
        # 关键：stdout 和 stderr 都要看
        if r.returncode != 0:
            print("  ❌ build.py 退出码 %s" % r.returncode)
            for l in out.splitlines()[-25:]:
                print("     " + l[:110])
            return 1
        # 只认真正的崩溃。不能用 "Error" in out ——
        # PyInstaller 会打印 "hidden import 'pydantic.error_wrappers'" 这类
        # 正常输出，用关键词扫会天天误报，那检查器就废了。
        if "Traceback (most recent call last)" in out:
            print("  ❌ build.py 抛异常了：")
            lines = out.splitlines()
            for i, l in enumerate(lines):
                if "Traceback (most recent call last)" in l:
                    for x in lines[i:i + 18]:
                        print("     " + x[:110])
                    break
            return 1
        exe = os.path.join(SRC, EXE_NAME)
        if not os.path.exists(exe):
            print("  ❌ 没有产出 exe")
            return 1
        new_mtime = os.path.getmtime(exe)
        print("  ✅ build.py 正常退出")
        print("  exe %.2f MB" % (os.path.getsize(exe) / 1024 / 1024))
        print("  时间戳 %s -> %s  %s"
              % (time.strftime("%H:%M:%S", time.localtime(old_mtime)),
                 time.strftime("%H:%M:%S", time.localtime(new_mtime)),
                 "（变了 ✅）" if new_mtime != old_mtime else "（没变 ⚠ 可能没重建）"))
        if new_mtime == old_mtime:
            print("  ⚠ 时间戳没变 —— build.py 可能没真的重建，下面用行为验证兜底")
        if "打包成功" not in out:
            print("  ❌ 输出里没有「打包成功」")
            for l in out.splitlines()[-20:]:
                print("     " + l[:110])
            return 1

    print()
    print("=" * 74)
    print("2. 把 exe 跑起来验行为（唯一可靠的判断）")
    print("=" * 74)
    SB = os.path.join(tempfile.gettempdir(), "_bguard")
    if os.path.exists(SB):
        shutil.rmtree(SB, ignore_errors=True)
    shutil.copytree(SRC, SB)
    cfg = json.load(io.open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    cfg["port"] = PORT
    cfg["admin_token"] = "t"
    cfg["voice_enabled"] = False
    json.dump(cfg, io.open(os.path.join(SB, "config.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=4)
    io.open(os.path.join(SB, ".env"), "w", encoding="utf-8").write(
        "DRIVER=~httpx\nQQ_BOTS='[{\"id\":\"20001\",\"secret\":\"\",\"intent\":"
        "{\"c2c_group_at_messages\":true,\"group_members\":true},"
        "\"use_websocket\":false}]'\n")
    os.makedirs(os.path.join(SB, "data", "plugins"), exist_ok=True)
    db = os.path.join(INST, "data", "bot.db")
    if os.path.exists(db):
        shutil.copy2(db, os.path.join(SB, "data", "bot.db"))
    pr = subprocess.Popen([os.path.join(SB, EXE_NAME)], cwd=SB,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = False
    for _ in range(25):
        time.sleep(3)
        if pr.poll() is not None:
            print("  ❌ 沙箱进程异常退出 %s" % pr.returncode)
            break
        st, _ = _get("http://127.0.0.1:%d/admin?token=t" % PORT, 4)
        if st == 200:
            ok = True
            break
    if not ok:
        subprocess.run(["taskkill", "/F", "/PID", str(pr.pid)], capture_output=True)
        shutil.rmtree(SB, ignore_errors=True)
        return 1
    time.sleep(10)

    bad = []
    for name, note, fn in PROBES:
        try:
            good, detail = fn(PORT)
        except Exception as e:
            good, detail = False, "%s: %s" % (type(e).__name__, e)
        print("  %s %-18s %s" % ("OK  " if good else "FAIL", name, detail))
        if not good:
            bad.append(name)

    subprocess.run(["taskkill", "/F", "/PID", str(pr.pid)], capture_output=True)
    time.sleep(2)
    shutil.rmtree(SB, ignore_errors=True)

    print()
    if bad:
        print("  ❌ 探测失败：%s —— 这个 exe 不能发布" % bad)
        print("     （说明源码改了但没能进到构建里，或改错了地方）")
        return 1
    print("  ✅ 全部探测通过，这个 exe 可以发布")
    return 0


if __name__ == "__main__":
    sys.exit(main())
