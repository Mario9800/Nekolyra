<div align="center">

# Nekolyra

**QQ 群管 AI 助手**

一个会说话、有记性、能自己管群、还能装插件的 QQ 机器人

`Windows 桌面版` · `插件化` · `本地优先` · `数据全在自己手里`

<sub>当前版本 <b>1.0.3.8</b> · <a href="CHANGELOG.md">更新日志</a> · <a href="https://github.com/awnpw/Nekolyra/releases/latest">下载最新版</a></sub>

</div>

> 它带数据库、带长期记忆、
> 带知识库检索、带插件运行时、带一个完整的 Web 管理界面
> 上手要花几分钟配一个 AI 接口和一个 QQ 协议端。

---

## 目录

| | | |
|---|---|---|
| [它是什么](#它是什么) | [AI 接口怎么选](#ai-接口怎么选) | [插件系统](#插件系统) |
| [功能](#功能) | [语音怎么开](#语音怎么开) | [目录结构](#目录结构) |
| [五分钟跑起来](#五分钟跑起来) | [配置项](#配置项) | [常见问题](#常见问题) |
| [项目状态](#项目状态说实话) | [隐私](#隐私) | [参与贡献](#参与贡献) |

---

## 它是什么

一个跑在 QQ 群里的 AI 助手。和"接个 API 回话"的机器人比，它多做了几件事：

| | |
|---|---|
| **有记性** | 记住群里发生的事，也记住每个人说过的话。分群隔离，互不串味 |
| **会说话** | 接 IndexTTS，能按情绪换语气发语音 —— 开心是一种声音，生气是另一种 |
| **能查资料** | 带文件夹层级的知识库，问设定、问群规、问常见问题都能答，命中数会统计 |
| **能管群** | 入群验证、广告拦截、黑名单、签到积分、发言排行、关键词自动回复 |
| **能装插件** | 抽奖、群报、签到、今日老婆都是插件 —— 不想要就卸掉，想加就自己写 |
| **有管理界面** | 不用改配置文件，浏览器里点点就行。二十多个页面，含移动端适配 |
| **能自动更新** | 检查新版本 → 下载 → **验签** → 替换。签名不对直接拒绝 |

**它不做什么**：不做多租户、不做云托管、不搞账号体系。它就是**你自己电脑上
跑的一个程序**，数据全在 `data/` 里，删掉就干净了。

---

## 功能

### 🔌 插件系统

主程序**不内置**游戏类功能，全挪进插件。仓库里自带 7 个：

| 插件 | 做什么 |
|---|---|
| `adfilter` | 广告拦截 —— 带自带管理页的完整示例，照着它学写插件最快 |
| `autoreply` | 关键词自动回复，带命中统计和管理页 |
| `sign` | 每日签到、积分、排行榜 |
| `lottery` | 从最近活跃的群成员里抽一个 |
| `report` | 统计群里某天的发言排行 |
| `wife` | 每天抽一个"今日老婆"（AI 写介绍 + 配图），可用积分换 |
| `hello` | 最小示例，被 @ 时回一句话 |

**插件能做到什么**：注册命令、挂消息钩子（可吞掉消息不让主程序处理）、
开自己的管理页、挂自己的 API、读写自己的配置、调 AI、发图、发语音、
撤回消息、踢人、拉黑、查权限。

**插件市场**：管理页里能列出社区插件并一键安装，注册表在
[`Nekolyra-plugins`](https://github.com/awnpw/Nekolyra-plugins)。
装进来的插件目录名受白名单约束（`^[\w.\-]{1,48}$`），防止路径穿越。

### 🤖 AI 对话

- 兼容 **OpenAI 协议**，任何能说 `POST /v1/chat/completions` 的服务都能接；
  也能接本地跑的（Ollama、LM Studio、vLLM…）
- **流式输出**，首字延迟记进日志
- **人格可写** —— 系统提示词在管理页里编辑，支持按群/按私聊分别设置
- **管理员识别** —— 超级管理员有额外的人格分支
- **用户画像注入** —— 发言数、活跃时段这些客观统计会喂给模型，
  "我是谁""我常来吗"这类问题才答得上
- **拆分回复** —— 长回复按长度和标点拆成多条发，模拟真人打字节奏
- **深度思考** —— 支持推理模型的思维链，可选是否展示

### 🧠 长期记忆

分三层，互不干扰：

1. **群记忆** —— 这个群发生过什么（谁和谁怎么了、群里定的规矩）
2. **用户记忆** —— 这个人说过什么、什么脾气
3. **用户画像** —— 客观统计，不是模型总结的，不会瞎编

记忆是**后台异步分析**出来的，不阻塞对话。每 N 轮触发一次分析，
新记忆按相关性注入到后续对话里。

### 🔊 语音合成

- 接 **IndexTTS**（本地部署）
- **情绪映射** —— 模型回复里带情绪标记，映射到不同的音色/参考音频
- **多语言音色表** —— 中文/日文/英文各一套，可在配置里改
- **自动降级** —— TTS 服务连不上就只发文字，不会卡住
- 连接状态和每次生成的耗时都进日志

### 📚 知识库

- **两级文件夹** —— 游戏 → 角色图鉴 / 主线剧情 / 卡池…
- **自动分类** —— 导入时按关键词猜分类，不用一条条填
- **检索注入** —— 命中数排序，命中次数会累加统计
- **批量导入** —— 支持 `问题|答案` 每行一条的纯文本，也支持 JSON
- **不写库的试跑** —— `/api/chat` 带 `dry_run` 可以只查不写，连命中计数都不动

### 🛡️ 群管

- **入群验证** —— 新人进群发验证，超时踢；支持自定义题库
- **广告拦截** —— 正则 + 图片 OCR 双通道，命中可选撤回/禁言/踢
- **黑名单** —— 群级和全局两级
- **申请审批** —— 加群申请在管理页里通过/拒绝
- **撤回 / 禁言 / 踢人** —— 命令和 API 都有

### 🖥️ 管理界面

单文件内嵌的 Web 界面（FastAPI + 原生 JS，无前端构建），
地址 `http://127.0.0.1:8080/admin?token=xxx`

二十多个页面：总览、AI 对话、知识库、记忆、聊天记录、插件、外观、
语音、群管、黑名单、申请、更新…… **支持深色模式、移动端、壁纸自定义**
（内置预设 + 上传自己的图 + 透明度/模糊/暗化调节）。

#### 检查更新与下载校验（安全边界）

自动更新做了这些事，缺一不可：

1. 拉 `version.json`（三级兜底：`api.github.com` → jsDelivr → ghproxy）
2. 版本号比对（四段式）
3. 下载 zip 到临时目录
4. **拉 `SHA256SUMS.txt` 和 `SHA256SUMS.txt.sig`**
5. **用内置的 Ed25519 公钥验签** —— 私钥不在仓库里，也不在发布机上
6. 校验 zip 的 SHA256 跟签名文件里的一致
7. 全过了才替换 `Nekolyra.exe`

**任何一步不过就中止并报错。** 这样即使有人劫持了下载源、或者伪造了镜像，
也装不上被改过的包。

### 其它

- **零配置启动** —— 第一次运行自动生成 `config.json` 和管理员 token
- **配置热重载** —— 管理页改完立即生效，不用重启
- **单文件 SQLite** —— 没有外部数据库依赖
- **解压即用** —— PyInstaller onedir，不装 Python

---

## 五分钟跑起来

### ① 下载桌面版

去 [Releases](https://github.com/awnpw/Nekolyra/releases/latest) 下载
`Nekolyra-desktop-v*.zip`，解压到**一个空目录**，双击 `Nekolyra.exe`。

第一次启动会：

- 生成 `config.json`
- 生成一个管理员 token 并**打印在控制台**（Web 界面要用的就是它）
- 在 `8080` 端口起管理界面

> **压缩包校验**：Release 里有 `SHA256SUMS.txt` 和它的 `.sig`。
> ```bash
> sha256sum -c SHA256SUMS.txt
> ```
> 签名用的 Ed25519 公钥指纹是 `89e7d909c17e6320…`（完整值见 [DESIGN.md](DESIGN.md)）。

### ② 配一个 QQ 协议端

机器人自己连不上 QQ，需要一个协议端实现。推荐 **NapCat**：

1. 装 [NapCat](https://github.com/NapNeko/NapCatQQ)，登录你的 QQ 小号
2. 网络配置里加一个 **WebSocket 客户端**，指向 `ws://127.0.0.1:8081/onebot/v11/ws`
3. 或者用反向 WebSocket：让 NapCat 连机器人的 `3001` 端口

连上之后管理页的「总览」会显示在线状态。

### ③ 跑源码（不想用桌面版）

```bash
git clone https://github.com/awnpw/Nekolyra.git
cd Nekolyra
pip install -r requirements.txt
python bot.py
```

需要 **Python 3.10+**（开发用的是 3.14）。

### ④ 打包成 exe

```bash
python build.py            # 或者 pyinstaller Nekolyra.spec
python _build_guard.py     # 35 项发布前探测，全过才让发
```

---

## AI 接口怎么选

程序只要求对方兼容 **OpenAI 协议**。改 `config.json` 里这三项：

```json
{
  "deepseek_api_host": "https://your-endpoint/v1",
  "deepseek_api_key":  "sk-...",
  "deepseek_model":    "your-model-name"
}
```

（字段名保留 `deepseek_` 前缀只是历史原因，**不绑定任何厂商**）

### 实测参考

同一句话、同一个网络，各模型首字延迟：

| 模型 | 首字 | 备注 |
|---|---|---|
| `kimi-k2.6` | 0.3s | 最快 |
| `qwen3.8-27b` | 0.5s | |
| `deepseek-v4-flash-0731` | 0.9s | **推荐**：快，回复质量稳 |
| `deepseek-v4-pro-0813` | 11.8s | 慢一个量级 |
| `intern-s2` | 1.4s / 16.9s | 波动很大 |
| `minimax-m3` | 0.5s | ⚠ 会把 `<mm:think>` 思维链一起吐出来 |

**首字延迟和总时长是两回事**：首字慢是排队/线路问题，总时长慢是生成速度问题。
程序每次调用都会记：

```
[AI] deepseek 首字 1.7s | 总 1.7s | 29 字
```

> **本地跑**：Ollama 的话 `host` 填 `http://127.0.0.1:11434/v1`，
> `key` 随便填个非空值。

---

## 语音怎么开

1. 本地部署一个 **IndexTTS** 服务
2. 配置里开 `voice_enabled`，填服务地址
3. 准备几个参考音频，在管理页的「语音」页里配**情绪 → 音色**映射
4. 支持**多语言音色表**（中/日/英各一套）

**没配也能用** —— TTS 连不上会自动降级成纯文字，不会卡住对话。

---

## 配置项

配置文件在程序目录下的 `config.json`，第一次启动自动生成。
**大部分项在管理页里改更方便**（改完热重载，不用重启）。

<details>
<summary>点开看主要配置项</summary>

| 键 | 说明 |
|---|---|
| `port` | 管理界面端口，默认 `8080` |
| `admin_token` | 管理界面 token，第一次启动生成 |
| `superusers` | 超级管理员 QQ 列表，**只能改配置文件**（接口不支持改） |
| `ai_enabled` | AI 对话总开关 |
| `deepseek_api_host` / `_key` / `_model` | AI 接口 |
| `persona_speech` | 人格提示词 |
| `ai_min_gap` / `ai_max_gap` | 回复前的最小/最大等待秒数，`0` = 不等待 |
| `split_reply` / `split_max_length` / `split_delay_seconds` | 拆分回复 |
| `talk_value` | 触发回复的概率，`0` = 每条都回 |
| `kb_enabled` / `kb_auto_inject` / `kb_inject_count` | 知识库 |
| `memory_inject_count` / `memory_user_inject_count` | 记忆注入条数 |
| `voice_enabled` / `voice_host` / `voice_emotion_voices` | 语音 |
| `wallpaper_preset` / `wallpaper_dim` / `wallpaper_blur` | 外观 |
| `ad_filter_*` | 广告拦截 |
| `auto_approve` | 入群申请自动通过 |

</details>

---

## 插件系统

### 装插件

管理页「插件」页 → 从市场一键安装，或者把插件目录丢进 `data/plugins/`。

市场注册表是一份 JSON 索引：

```json
{
  "plugins": [
    {
      "name": "hello",
      "description": "示例插件",
      "version": "1.0.0",
      "path": "plugins/hello",
      "author": "someone"
    }
  ]
}
```

### 自己写插件

最小插件长这样：

```python
# data/plugins/hello/main.py
from plugin_api import PluginContext

def setup(ctx: PluginContext):
    @ctx.command("打招呼", "hello")
    async def hello(event, text):
        await ctx.send_group(event.group_id, f"你好，{ctx.nick(event.user_id)}！")
        return True        # 吞掉这条消息，主程序不再处理

    @ctx.on_message()
    async def watch(event, text):
        if "广告" in text:
            await ctx.delete_msg(event.message_id)
            return True        # 同样吞掉
        return False           # 放行给主程序
```

**可用的东西**（`PluginContext` 上）：

| | |
|---|---|
| `@ctx.command(名, 别名…)` | 注册命令，**精确匹配**整条消息 |
| `@ctx.on_message()` | 消息钩子，返回 `True` 吞掉、`False` 放行 |
| `ctx.cfg(k, default)` / `ctx.set_cfg(k, v)` | 读写插件自己的配置 |
| `ctx.register_config(...)` | 在管理页生成配置表单 |
| `ctx.register_page(...)` | 挂一个自己的管理页 |
| `ctx.register_api(...)` | 挂自己的 HTTP 接口 |
| `ctx.send_group` / `ctx.send_private` / `ctx.mention` | 发消息 |
| `ctx.image(path)` / `ctx.voice(text)` | 发图 / 发语音 |
| `ctx.delete_msg` / `ctx.kick` / `ctx.bl_add` | 撤回 / 踢人 / 拉黑 |
| `ctx.is_super(uid)` / `ctx.nick(uid)` | 权限 / 昵称 |

> ⚠️ **一个坑**：`Rule(...)` 的回调**必须是有名字、带类型注解的函数**。
> 写成 `Rule(lambda e: ...)` 会报 `ValueError: Unknown parameter e`。

---

## 目录结构

```
Nekolyra/
├─ Nekolyra.exe           主程序
├─ config.json            配置（首次运行生成）
├─ VERSION                当前版本
├─ data/
│  ├─ bot.db              SQLite：记忆 / 知识库 / 积分 / 聊天记录 / 黑名单
│  ├─ plugins/            插件
│  ├─ wallpaper.jpg       自定义壁纸（如果设了）
│  └─ voice_cache/        语音缓存
└─ startup.log            运行日志
```

源码这边：

```
bot.py                    主程序（单文件，约 1.4 万行）
official_bot.py           QQ 官方机器人接口的另一套实现
build.py / Nekolyra.spec  打包
_build_guard.py           发布前 35 项探测
_sign_release.py          Release 签名（Ed25519）
_check_ui.py / _check_html.py / _check_refs.py   三个自检
data/plugins/             自带的 7 个插件
```

---

## 常见问题

<details>
<summary><b>启动报 <code>ModuleNotFoundError</code></b></summary>

桌面版不该出现这个。源码跑的话：

```bash
pip install -r requirements.txt
```
</details>

<details>
<summary><b>管理页打不开 / 401</b></summary>

- 确认端口：默认 `8080`，看控制台输出
- token 在 `config.json` 的 `admin_token`，或者看启动时打印的那一行
- **token 里有 `#` `&` `=` 或中文时 URL 必须转义**，否则会被截断。
  程序生成的 token 是纯 ASCII，自己改过的话注意这点
</details>

<details>
<summary><b>机器人不回话</b></summary>

按顺序查：

1. 管理页「总览」里协议端显示在线吗
2. `ai_enabled` 开了吗
3. 看日志有没有 `[AI] deepseek 首字 X.Xs` —— **有这行说明 AI 通了**
4. 没有这行就看 `deepseek_api_host` / `_key` / `_model` 三项
5. `talk_value` 是不是 `0`（`0` = 每条都回）
</details>

<details>
<summary><b>回话很慢</b></summary>

**先看日志里的这一行**：

```
[AI] deepseek 首字 1.7s | 总 1.7s | 29 字
```

- **首字慢**（> 5s）→ 接口在排队，换个模型或换个服务商。上面有实测对比表
- **总时长慢**（首字快但总时长长）→ 模型生成速度问题
- **两个都快但回复慢** → 检查 `ai_min_gap` / `ai_max_gap`，别设太大

> 九成情况是**模型选太重了**。同一句话，`flash` 档首字 0.9 秒，
> `pro` 档首字 11.8 秒 —— 差一个数量级，跟程序无关。
</details>

<details>
<summary><b>语音发不出来</b></summary>

1. 日志里找 `[语音] IndexTTS 连接正常` —— 没这行说明服务没连上
2. 检查 `voice_host` 地址和端口
3. 看音色表里引用的参考音频文件是否还在
4. 连不上会**自动降级成纯文字**，不会卡住对话
</details>

<details>
<summary><b>壁纸换不了 / 换了不生效</b></summary>

1. 管理页「外观」页点「我的图片」→ 上传
2. 换完之后 **`Ctrl + F5` 强制刷新**（管理页是 `no-store`，但图片会缓存）
3. 预设缩略图用的是 `background-image` + `cover`；
   如果显示一片白，多半是浏览器缓存了旧页面
</details>

<details>
<summary><b>提示"有新版本"但下载失败</b></summary>

更新走三级兜底：`api.github.com` → jsDelivr → ghproxy。
国内网络下 `api.github.com` 可能被限流，程序会自动试下一个。

**如果三个都失败**：手动去 [Releases](https://github.com/awnpw/Nekolyra/releases/latest)
下载覆盖 `Nekolyra.exe` 就行，配置和数据都不受影响。
</details>

<details>
<summary><b>改了仓库地址，自动更新还能用吗</b></summary>

能用。GitHub 对旧地址做 301 跳转，`api.github.com` 会跟。
**但 jsDelivr 不跟 301** —— 所以项目换过仓库地址的话，
旧版本的 CDN 兜底会失效，升级到最新版就恢复。
</details>

---

## 项目状态（说实话）

**能用的部分**（自己天天在用）：

- AI 对话、记忆、知识库、插件系统、管理界面 —— 稳定
- 广告拦截、入群验证、签到积分 —— 稳定
- 自动更新 + 签名校验 —— 稳定

**不太成熟的部分**：

- **QQ 官方机器人接口**（`official_bot.py`）—— 能用，但比 NapCat 那条路少测。
  腾讯的接口有配额限制，报错信息也不友好
- **语音** —— 依赖 IndexTTS 的部署质量，参考音频不好听起来就很怪
- **群管** —— 只覆盖常见场景，复杂的权限体系没做

**已知不做**：

- 多租户 / 云托管 / 账号体系
- 移动端 App（管理页有响应式，够用）

**版本号是四段**：`主.次.功能组.修bug`

- 最后一位是**修 bug**
- 第三位是**加功能**
- 第二位是**破坏性改动**（升级要动配置）
- tag 必须是**四段**，跟 `version.json` 对得上

---

## 隐私

**数据不出你的机器**：

- 所有对话、记忆、知识库、积分都在本地 `data/bot.db`（SQLite）
- 没有遥测、没有统计上报、没有云端账号
- 唯一的外部请求是：AI 接口、TTS 服务、检查更新（可关）

**发到 AI 服务的内容**：只有对话本身，加上被判定相关的记忆片段和知识库条目。
系统提示词也在里面。**别的群的数据不会发过去**（分群隔离）。

**管理界面**：默认只听 `127.0.0.1`。想局域网访问的话自己改，
但**务必改掉默认 token**。

---

## 参与贡献

**Issue / PR 都欢迎**，尤其是：

- 新的插件（写好了可以提 PR 到 [`Nekolyra-plugins`](https://github.com/awnpw/Nekolyra-plugins)）
- 文档修正 —— 上面「常见问题」里没覆盖到的坑
- Bug 报告 —— **附日志**。程序记的东西挺全的，有日志基本能定位

**提 PR 之前**跑一下：

```bash
python _check_ui.py       # UI 结构自检
python _check_html.py     # 标签配对
python _check_refs.py     # 引用完整性
python -m pyflakes bot.py
```

---

## 许可

**GPL-3.0** —— 可以自由使用、修改、分发，但**衍生作品必须同样开源**。

---

## 免责声明

- 这是**非官方粉丝作品**，与腾讯、任何游戏公司均无关联
- **仓库里不包含任何官方美术资源**。内置的默认壁纸是程序自带的原创角色插画
- 使用本程序产生的任何后果由使用者自行承担
- 请遵守 QQ 的用户协议，**不要用来发广告、刷屏、骚扰他人**

**如果你是权利人，认为仓库里有侵犯你权益的内容，开个 Issue 说明，我会立刻处理。**

---

<div align="center">
<sub>

**Nekolyra** · GPL-3.0 · [更新日志](CHANGELOG.md) · [设计文档](DESIGN.md) · [下载](https://github.com/awnpw/Nekolyra/releases/latest)

</sub>
</div>
