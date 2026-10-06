# -*- coding: utf-8 -*-
"""打包脚本：python build.py

==================== 版本号约定（重要）====================
格式四段：1.0.0.0

  · 修 bug / 小改动    ->  1.0.0.1 → 1.0.0.2 → 1.0.0.3 …   ← 只动最后一位
  · 加了一组新功能     ->  1.0.1.0
  · 大改 / 不兼容      ->  1.1.0.0

改版本号同步三处：
  1. 打包脚本里的 V 变量
  2. 包里的 VERSION 文件（打包时自动写）
  3. 用户实例的文件夹名（可选，不影响运行）
==========================================================
"""
import os
import sys
import shutil
import subprocess

NAME = "Nekolyra"
BASE = os.path.dirname(os.path.abspath(__file__))


def check_env():
    print("=" * 62)
    print("  Nekolyra 打包工具")
    print("=" * 62)
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  目录:   {BASE}")
    print()

    try:
        import PyInstaller
        print(f"  ✓ PyInstaller {PyInstaller.__version__}")
    except ImportError:
        print("  ✗ PyInstaller 未安装")
        print()
        print("  请先运行：python -m pip install pyinstaller")
        sys.exit(1)

    try:
        import webview
        print(f"  ✓ pywebview")
    except ImportError:
        print("  ✗ pywebview 未安装")
        print()
        print("  请先运行：python -m pip install pywebview")
        sys.exit(1)

    for f in ["bot.py", "config.json"]:
        if not os.path.exists(os.path.join(BASE, f)):
            print(f"  ✗ 缺少 {f}")
            sys.exit(1)
        print(f"  ✓ {f}")

    print()


def build():
    for d in ["build", "dist", "__pycache__"]:
        shutil.rmtree(os.path.join(BASE, d), ignore_errors=True)
    spec = os.path.join(BASE, f"{NAME}.spec")
    if os.path.exists(spec):
        os.remove(spec)

    print("=" * 62)
    print("  开始打包（1-3 分钟）…")
    print("=" * 62)
    print()

    args = [
        sys.executable, "-m", "PyInstaller",
        os.path.join(BASE, "bot.py"),
        f"--name={NAME}",
        "--onedir",
        "--noconsole",     # 关键：不要命令行！
        "--clean",
        "--noconfirm",
        # 程序图标（根目录的 Nekolyra.ico，没有就退回默认）
        *([f"--icon={os.path.join(BASE, 'Nekolyra.ico')}"]
          if os.path.exists(os.path.join(BASE, "Nekolyra.ico")) else []),

        # NoneBot
        "--collect-all=nonebot",
        "--collect-all=nonebot_adapter_onebot",
        "--collect-all=nonebot_plugin_localstore",

        # Web
        "--collect-all=fastapi",
        "--collect-all=starlette",
        "--collect-all=uvicorn",
        "--collect-all=websockets",
        "--collect-all=httpx",
        "--collect-all=httpcore",
        "--collect-all=anyio",

        # pywebview + Windows GUI
        "--collect-all=webview",
        "--collect-all=pythonnet",
        "--hidden-import=clr",
        "--hidden-import=clr_loader",
        "--hidden-import=System",
        "--hidden-import=System.Windows.Forms",
        "--hidden-import=webview.platforms.edgechromium",
        "--hidden-import=webview.platforms.winforms",
        "--hidden-import=tkinter",

        # 基础
        "--collect-all=pydantic",
        "--collect-all=pydantic_core",
        "--collect-all=loguru",
        "--hidden-import=sqlite3",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.lifespan.on",
    ]

    result = subprocess.run(args, cwd=BASE)
    print()

    if result.returncode == 0:
        # onedir 模式下可执行文件在 dist/<NAME>/<NAME>.exe
        exe = os.path.join(BASE, "dist", NAME, f"{NAME}.exe")
        if not os.path.exists(exe):
            exe = os.path.join(BASE, "dist", f"{NAME}.exe")
        # 把 VERSION 也写进 dist —— 程序启动时读它来显示版本号、
        # 并跟 GitHub 上的最新版比较。只写进发布包的话，
        # 从源码构建后直接跑 dist 里的 exe 就看不到版本号了。
        try:
            _vfile = os.path.join(os.path.dirname(exe), "VERSION")
            _vsrc = os.path.join(BASE, "VERSION")
            _v = (open(_vsrc, encoding="utf-8").read().strip()
                  if os.path.exists(_vsrc) else "")
            if _v:
                open(_vfile, "w", encoding="utf-8").write(_v + "\n")
                print(f"  VERSION: {_v} -> {_vfile}")
        except Exception as _e:
            print(f"  VERSION 写入失败: {_e}")

        if os.path.exists(exe):
            size = os.path.getsize(exe) / 1024 / 1024
            print("=" * 62)
            print("  ✓ 打包成功！")
            print("=" * 62)
            print(f"  exe: {exe}")
            print(f"  体积: {size:.1f} MB")
            print()
            print("  使用：")
            print("    1. 把 dist/Nekolyra 整个文件夹拷到任意位置")
            print("    2. 双击里面的 Nekolyra.exe 运行")
            print("    3. 首次运行会生成 config.json，弹窗提示你填")
            print("    4. 编辑后再次双击，白色窗口自动打开")
            print()
    else:
        print(f"  ✗ 打包失败（退出码 {result.returncode}）")
        print()
        print("  排查建议：")
        print("    1. 换 Python 3.12 再试")
        print("    2. 看控制台输出里的具体报错")


if __name__ == "__main__":
    check_env()
    build()
