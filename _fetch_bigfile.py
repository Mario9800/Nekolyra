# -*- coding: utf-8 -*-
"""多连接分块 + 断点续传下载器（专治单连接被限速的源）。

  python _fetch_bigfile.py --url A [--url B ...] --out <输出文件> [--size 字节] [--sha256 hash]

原理：把文件切成 N 块，每块一个连接并行下（各块独立续传文件 .partN），
全部完成后按顺序拼接。实测同一源单连接 0.8MB/s、8 连接可到 7MB/s。
已完成的块会写入 .done 标记，重跑只补缺块。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import threading
import time
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CHUNK = 512 * 1024
DEFAULT_PARTS = 8

_print_lock = threading.Lock()


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


def content_length(url: str) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return int(resp.headers.get("Content-Length") or 0)


def fetch_part(idx: int, start: int, end: int, urls: list[str], part_path: str) -> bool:
    """下载 [start, end] 区间（含闭区间），返回是否完整。"""
    if os.path.exists(part_path) and os.path.getsize(part_path) == end - start + 1:
        return True
    total = end - start + 1
    for attempt in range(1, 9):
        done = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        if done >= total:
            return True
        url = urls[(idx + attempt - 1) % len(urls)]
        headers = {"User-Agent": UA, "Range": f"bytes={start + done}-{end}"}
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                mode = "ab" if done else "wb"
                with open(part_path, mode) as fh:
                    got = done
                    last = time.time()
                    while True:
                        block = resp.read(CHUNK)
                        if not block:
                            break
                        fh.write(block)
                        got += len(block)
                        if time.time() - last > 20:
                            last = time.time()
                            with _print_lock:
                                print(f"    块{idx:>2} {human(got)}/{human(total)}", flush=True)
        except Exception as exc:  # noqa: BLE001
            with _print_lock:
                print(f"    块{idx:>2} 第{attempt}次出错：{type(exc).__name__}: {str(exc)[:70]}",
                      flush=True)
            time.sleep(min(2 ** attempt, 20))
            continue
    return os.path.exists(part_path) and os.path.getsize(part_path) == total


def combine(parts: list[tuple[int, str]], out: str) -> None:
    tmp = out + ".joining"
    with open(tmp, "wb") as dst:
        for _, path in parts:
            with open(path, "rb") as src:
                while True:
                    block = src.read(8 * 1024 * 1024)
                    if not block:
                        break
                    dst.write(block)
    os.replace(tmp, out)
    for _, path in parts:
        try:
            os.remove(path)
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="append", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, default=0)
    ap.add_argument("--sha256", default="")
    ap.add_argument("--parts", type=int, default=DEFAULT_PARTS)
    args = ap.parse_args()

    out = args.out
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    if os.path.exists(out):
        size = os.path.getsize(out)
        if args.size and size != args.size:
            print(f"已存在的文件大小不符（{size} != {args.size}），重新下载")
            os.remove(out)
        elif args.sha256 and sha256_of(out) != args.sha256:
            print("已存在的文件 SHA-256 不符，重新下载")
            os.remove(out)
        else:
            print(f"已存在且校验通过：{out}  {human(size)}")
            return 0

    total = args.size
    if not total:
        for url in args.url:
            try:
                total = content_length(url)
                if total:
                    break
            except Exception as exc:  # noqa: BLE001
                print(f"  取大小失败 {url.split('/')[2]}：{type(exc).__name__}")
    if not total:
        print("拿不到文件大小，无法分块。请用 --size 指定。")
        return 2

    parts = max(1, min(args.parts, 16))
    chunk = (total + parts - 1) // parts
    tasks = []
    for i in range(parts):
        start = i * chunk
        if start >= total:
            break
        end = min(start + chunk - 1, total - 1)
        tasks.append((i, start, end, f"{out}.part{i}"))

    print(f"目标：{out}")
    print(f"大小：{human(total)}   分 {len(tasks)} 块   源 {len(args.url)} 个")
    t0 = time.time()
    results = {}

    def worker(idx, start, end, path):
        results[idx] = fetch_part(idx, start, end, args.url, path)

    threads = [threading.Thread(target=worker, args=(i, s, e, p), daemon=True)
               for i, s, e, p in tasks]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = all(results.get(i, False) for i, _, _, _ in tasks)
    got = sum(os.path.getsize(p) for _, _, _, p in tasks if os.path.exists(p))
    speed = got / max(time.time() - t0, 0.001)
    print(f"分块下载结束：{human(got)}/{human(total)}  平均 {human(speed)}/s")
    if not ok:
        print("有块没下完。重跑本命令即可续传缺失的块。")
        return 1

    print("拼接中...")
    combine([(i, p) for i, _, _, p in tasks], out)
    size = os.path.getsize(out)
    if size != total:
        print(f"拼接后大小不符：{size} != {total}")
        return 1
    if args.sha256:
        digest = sha256_of(out)
        if digest != args.sha256:
            print(f"SHA-256 不符：{digest}")
            return 1
        print("SHA-256 校验通过")
    print(f"完成：{out}  {human(size)}  用时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
