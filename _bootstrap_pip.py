# -*- coding: utf-8 -*-
"""给嵌入式 Python 手动安装 pip / setuptools / wheel。

用法（用目标解释器自己跑）：
    python _bootstrap_pip.py <site-packages 目录> [镜像 JSON API 根]

嵌入式发行版没有 ensurepip；bootstrap.pypa.io 的 /pip/3.10/get-pip.py 也已下线（404），
所以走最可靠的路：从镜像取纯 Python wheel，直接解压进 site-packages。
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.request
import zipfile

UA = "Mozilla/5.0"
PKGS = ("pip", "setuptools", "wheel")
DEFAULT_MIRRORS = (
    "https://pypi.tuna.tsinghua.edu.cn/pypi",
    "https://pypi.org/pypi",
)


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def pick_wheel(name: str, mirrors) -> str:
    data = None
    for base in mirrors:
        try:
            data = fetch_json(f"{base}/{name}/json")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  [!] {base} 查询 {name} 失败：{type(exc).__name__}")
    if data is None:
        raise RuntimeError(f"{name} 在所有镜像上都查不到")
    version = data["info"]["version"]
    files = data["releases"].get(version) or []
    for item in files:
        fname = item["filename"]
        if fname.endswith(("-py3-none-any.whl", "-py2.py3-none-any.whl")):
            return item["url"]
    for item in files:
        if item["filename"].endswith(".whl") and "none-any" in item["filename"]:
            return item["url"]
    raise RuntimeError(f"{name} {version} 没有纯 Python wheel")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    site = sys.argv[1]
    mirrors = [sys.argv[2]] if len(sys.argv) > 2 else list(DEFAULT_MIRRORS)
    mirrors += [m for m in DEFAULT_MIRRORS if m not in mirrors]

    os.makedirs(site, exist_ok=True)
    os.makedirs(os.path.join(os.path.dirname(site.rstrip("\\/")), "Scripts"), exist_ok=True)

    for pkg in PKGS:
        try:
            url = pick_wheel(pkg, mirrors)
        except Exception as exc:  # noqa: BLE001
            print(f"  [X] {pkg}: {exc}")
            continue
        name = url.rsplit("/", 1)[-1]
        print(f"  下载 {name}")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=300) as resp:
            blob = resp.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            zf.extractall(site)
        print(f"  [OK] {pkg}  {len(blob) // 1024} KB")
    print("pip 引导完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
