# -*- coding: utf-8 -*-
"""按 desktop_model_manifest.json 下载并校验 IndexTTS 2.5 模型。

只用标准库，方便用还没装依赖的内置 Python 直接跑。

  python _fetch_t8star_models.py --fetch  --manifest <..json> --target <模型目录>
  python _fetch_t8star_models.py --verify --manifest <..json> --target <模型目录>

默认走 hf-mirror.com（国内可达），可用 --endpoint 或环境变量 HF_ENDPOINT 覆盖。
支持断点续传：反复运行只会补下没完成/校验不过的文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = os.environ.get("HF_ENDPOINT") or "https://hf-mirror.com"
MAIN_REPO = "t8star/IndexTTS-2.5-Comfy"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ChromaBot-model-fetch"
CHUNK = 1024 * 1024
WORKERS = 3

_lock = threading.Lock()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}GB"


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def file_url(endpoint: str, repo: str, revision: str, path: str) -> str:
    return f"{endpoint}/{repo}/resolve/{revision}/{path}?download=true"


def build_tasks(manifest: dict) -> list[dict]:
    """把清单转成下载任务。

    注意：带 sourceRepository 的辅助文件，本地要放到 hf_cache/子目录里
    （桌面的 HF_HUB_CACHE 结构），但在源仓库里它们是根目录下的文件，
    所以 URL 路径要取文件名而不是完整的相对路径。
    """
    main_rev = manifest["modelRevision"]
    tasks = []
    for rel, meta in manifest["files"].items():
        rel = rel.replace("\\", "/")
        source_repo = meta.get("sourceRepository")
        if source_repo:
            remote_path = rel.rsplit("/", 1)[-1]
        else:
            source_repo = MAIN_REPO
            remote_path = rel
        rev = meta.get("sourceRevision") or main_rev
        tasks.append({
            "path": rel,
            "remote": remote_path,
            "size": int(meta["size"]),
            "sha256": str(meta["sha256"]).lower(),
            "repo": source_repo,
            "rev": rev,
        })
    tasks.sort(key=lambda t: -t["size"])  # 大文件先下，早点暴露带宽/网络问题
    return tasks


def _finalize_part(part: str, dest: str, task: dict, results: list) -> bool:
    """校验 .part 并转正。返回 True 表示这条任务已完成。"""
    size = os.path.getsize(part)
    if size != task["size"]:
        return False
    if sha256_of(part) != task["sha256"]:
        with _lock:
            print(f"  [X] {task['path']} SHA-256 不符，删除重下", flush=True)
        os.remove(part)
        return False
    os.replace(part, dest)
    with _lock:
        results.append(("ok", task["path"], size))
        print(f"  [完成] {task['path']}  {human(size)}  校验通过", flush=True)
    return True


def download_one(task: dict, target: str, endpoint: str, results: list) -> None:
    rel = task["path"]
    dest = os.path.join(target, rel.replace("/", os.sep))
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    if os.path.exists(dest) and os.path.getsize(dest) == task["size"]:
        got = sha256_of(dest)
        if got == task["sha256"]:
            with _lock:
                results.append(("skip", rel, task["size"]))
                print(f"  [跳过] {rel} 已存在且校验通过", flush=True)
            return
        os.remove(dest)

    # .part 已经完整（上次下完但没转正）时直接转正，别再发请求
    if os.path.exists(part) and os.path.getsize(part) == task["size"]:
        if _finalize_part(part, dest, task, results):
            return

    url = file_url(endpoint, task["repo"], task["rev"], task["remote"])
    attempt = 0
    while attempt < 5:
        attempt += 1
        done = os.path.getsize(part) if os.path.exists(part) else 0
        if done > task["size"]:
            os.remove(part)
            done = 0
        if done == task["size"]:
            # 服务器会为"已完整"的续传请求返回 416，这里直接收尾
            if _finalize_part(part, dest, task, results):
                return
            done = 0
        headers = {"User-Agent": UA}
        if done:
            headers["Range"] = f"bytes={done}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                if done and resp.status != 206:
                    # 服务器不支持续传，从头来
                    done = 0
                    if os.path.exists(part):
                        os.remove(part)
                started = time.time()
                got = done
                mode = "ab" if done else "wb"
                with open(part, mode) as fh:
                    while True:
                        block = resp.read(CHUNK)
                        if not block:
                            break
                        fh.write(block)
                        got += len(block)
                        if got % (64 * 1024 * 1024) < CHUNK:
                            speed = got / max(time.time() - started, 0.001)
                            with _lock:
                                print(f"    {rel}  {human(got)}/{human(task['size'])}"
                                      f"  {human(speed)}/s", flush=True)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and os.path.exists(part) and os.path.getsize(part) == task["size"]:
                if _finalize_part(part, dest, task, results):
                    return
            wait = min(2 ** attempt, 30)
            with _lock:
                print(f"  [!] {rel} 第{attempt}次失败：HTTP {exc.code}，{wait}s 后重试", flush=True)
            time.sleep(wait)
            continue
        except Exception as exc:  # noqa: BLE001
            wait = min(2 ** attempt, 30)
            with _lock:
                print(f"  [!] {rel} 第{attempt}次失败：{type(exc).__name__}: {str(exc)[:90]}"
                      f"，{wait}s 后重试", flush=True)
            time.sleep(wait)
            continue

        if os.path.exists(part) and os.path.getsize(part) == task["size"]:
            if _finalize_part(part, dest, task, results):
                return
        with _lock:
            print(f"  [!] {rel} 大小不符（{os.path.getsize(part) if os.path.exists(part) else 0}"
                  f" != {task['size']}），重试", flush=True)

    with _lock:
        results.append(("fail", rel, 0))
        print(f"  [失败] {rel} 多次重试仍未成功", flush=True)


def verify_only(tasks: list[dict], target: str) -> int:
    bad = 0
    total = 0
    for task in tasks:
        dest = os.path.join(target, task["path"].replace("/", os.sep))
        total += task["size"]
        if not os.path.exists(dest):
            print(f"  [缺] {task['path']}")
            bad += 1
            continue
        size = os.path.getsize(dest)
        if size != task["size"]:
            print(f"  [大小不符] {task['path']} {size} != {task['size']}")
            bad += 1
            continue
        if sha256_of(dest) != task["sha256"]:
            print(f"  [哈希不符] {task['path']}")
            bad += 1
            continue
        print(f"  [OK] {task['path']}  {human(size)}")
    print()
    if bad:
        print(f"校验未通过：{bad} 个文件有问题（清单共 {len(tasks)} 个，总计 {human(total)}）")
        return 1
    print(f"全部 {len(tasks)} 个文件校验通过，总计 {human(total)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    if not (args.fetch or args.verify):
        args.fetch = True

    manifest = json.load(open(args.manifest, encoding="utf-8"))
    tasks = build_tasks(manifest)
    os.makedirs(args.target, exist_ok=True)

    print(f"模型目录：{args.target}")
    print(f"镜像：{args.endpoint}")
    print(f"文件数：{len(tasks)}   总大小：{human(sum(t['size'] for t in tasks))}")
    print()

    if args.verify and not args.fetch:
        return verify_only(tasks, args.target)

    results: list = []
    pending = list(tasks)
    threads = []
    while pending or threads:
        while pending and len(threads) < WORKERS:
            task = pending.pop(0)
            th = threading.Thread(target=download_one,
                                  args=(task, args.target, args.endpoint, results),
                                  daemon=True)
            th.start()
            threads.append(th)
        time.sleep(0.3)
        threads = [t for t in threads if t.is_alive()]

    ok = sum(1 for r in results if r[0] in ("ok", "skip"))
    failed = [r[1] for r in results if r[0] == "fail"]
    print()
    print(f"完成 {ok}/{len(tasks)} 个文件")
    if failed:
        print("未完成：")
        for name in failed:
            print("  - " + name)
        print("可以重新运行本脚本继续下载（已完成的会跳过）。")
        return 1

    print()
    print("开始最终校验...")
    return verify_only(tasks, args.target)


if __name__ == "__main__":
    raise SystemExit(main())
