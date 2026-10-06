# -*- coding: utf-8 -*-
"""示例插件：打招呼。

想写自己的插件就照这个改：
  1. 建一个目录 data/plugins/你的插件名/
  2. 放两文件：metadata.json（元信息）+ main.py（本文件）
  3. main.py 里定义 setup(ctx)，在管理界面点「重新加载插件」即可生效

不需要 import 宿主任何东西 —— 只依赖 setup(ctx) 这个约定。
这样打包成 exe 之后也能正常加载。
"""


def setup(ctx):
    """插件入口。加载时被调用一次。"""

    # ---- 读配置（在 metadata.json 的 defaults 或管理界面里设）----
    reply = ctx.cfg("hello_reply", "你好呀。")
    suffix = ctx.cfg("hello_suffix", "")

    # ---- 注册一个命令：被 @ 且内容正好是「你好」时触发 ----
    @ctx.command("你好", "Nekolyra会跟你打招呼")
    async def on_hello(event, cmd):
        text = reply + ("\n" + suffix if suffix else "")
        await ctx.send_group(event.group_id, text)
        ctx.log(f"在群 {event.group_id} 回应了 {event.user_id}")
        return True          # True = 已处理，不再走内置逻辑

    # ---- 再注册一个，演示多个命令 ----
    @ctx.command("插件测试", "验证插件系统是否工作")
    async def on_test(event, cmd):
        await ctx.send_group(
            event.group_id,
            f"插件系统正常。\n"
            f"插件名：{ctx.display_name} v{ctx.version}\n"
            f"注册命令：{'、'.join(sorted(ctx.commands.keys()))}\n"
            f"插件目录：{ctx.dir}"
        )
        return True

    # ---- 注册配置项，会出现在「系统配置」页最下面 ----
    ctx.register_config("打招呼插件", [
        ["hello_reply", "回复内容", "text"],
        ["hello_suffix", "附加后缀（留空不显示）", "text"],
    ])

    ctx.log("示例插件已就绪")
