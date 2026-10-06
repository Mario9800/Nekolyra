# -*- coding: utf-8 -*-
"""Nekolyra —— 鸣潮主题 QQ 群管机器人（桌面版）"""



import os, sys, re, json, time, socket, random, asyncio, sqlite3, threading, shutil, hashlib, base64, traceback, functools
import subprocess, tempfile, zipfile
from typing import Optional
from collections import deque
from datetime import datetime, date, timedelta

# ==================== 控制台流修复（必须最先执行）====================
# --noconsole 打包后 sys.stdout / sys.stderr 可能是 None，任何日志写入都会抛
# "Cannot log to objects of type 'NoneType'"。nonebot 在 import 阶段就写日志，
# 所以这段必须在 import nonebot 之前跑完。
class _NullStream:
    """丢弃式写入器：接口齐全，永不抛异常。"""

    encoding = "utf-8"
    errors = "replace"

    def write(self, s):
        try:
            return len(s)
        except Exception:
            return 0

    def writelines(self, lines):
        try:
            for _ in lines:
                pass
        except Exception:
            pass

    def flush(self):
        pass

    def close(self):
        pass

    def isatty(self):
        return False

    def readable(self):
        return False

    def writable(self):
        return True

    def seekable(self):
        return False

    def fileno(self):
        raise OSError("no fileno")

    # 必须显式定义：__getattr__ 兜底返回一个 lambda，
    # getattr(stream, "closed", False) 会拿到真值，
    # _fix_console_streams 就会重复创建 NullStream。
    closed = False

    def __getattr__(self, name):
        # 任何未知属性都返回一个哑函数，避免三方库访问属性时报错
        return lambda *a, **k: None


def _fix_console_streams():
    """确保 stdout/stderr 是可用且 UTF-8 的流。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or getattr(stream, "closed", False):
            try:
                setattr(sys, name, _NullStream())
            except Exception:
                pass
            continue
        # 编码不是 utf-8 就改（否则中文日志会抛 UnicodeEncodeError）
        try:
            enc = (getattr(stream, "encoding", "") or "").lower()
            if enc and enc.replace("-", "") not in ("utf8", "utf_8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # 不能重配置就包一层，保证写入不炸
            try:
                setattr(sys, name, _NullStream())
            except Exception:
                pass


_fix_console_streams()

# 让子进程/三方库也走 UTF-8
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")



def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()

# ---- 注册向导模式：必须在下面的配置校验之前判断 ----
# 配置不完整时，桌面版要弹注册向导，而不是提前 sys.exit。
# 这个标记必须在这里设置：文件下方的配置校验（第 155 行附近）会先执行。
def _setup_needed_early() -> bool:
    try:
        import json as _j
        _p = os.path.join(BASE_DIR, "config.json")
        if not os.path.exists(_p):
            return True
        with open(_p, "r", encoding="utf-8") as _f:
            _c = _j.load(_f)
        if not isinstance(_c, dict):
            return True
        _su = _c.get("superusers") or []
        _tk = str(_c.get("admin_token") or "").strip()
        return (not _su) or (not _tk) or (_tk == "change-me-please")
    except Exception:
        return True


if _setup_needed_early():
    os.environ["ECHO_SETUP_WIZARD"] = "1"

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DB_PATH = os.path.join(BASE_DIR, "data", "bot.db")
BACKUP_DIR = os.path.join(BASE_DIR, "data", "backup")
VOICE_TMP_DIR = os.path.join(BASE_DIR, "data", "voice_tmp")
LOG_PATH = os.path.join(BASE_DIR, "startup.log")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(VOICE_TMP_DIR, exist_ok=True)
_logf = None
try:
    _logf = open(LOG_PATH, "w", encoding="utf-8")
except Exception:
    _logf = None


def log_startup(msg):
    """启动日志。写文件失败绝不影响主流程。"""
    if _logf is None:
        return
    try:
        _logf.write(f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}\n")
        _logf.flush()
    except Exception:
        pass


def show_msg(title, msg):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk(); root.withdraw()
        messagebox.showinfo(title, msg); root.destroy()
    except Exception:
        pass


DEFAULT_CONFIG = {
    "superusers": [],
    "ai_enabled": True,
    "ai_backend": "deepseek",
    "ollama_host": "http://localhost:11434",
    "ai_model": "qwen3:4b",
    "ollama_api_key": "ollama",
    "deepseek_api_host": "https://open.bigmodel.cn/api/paas/v4",
    "deepseek_model": "glm-4.7-flash",
    "deepseek_api_key": "",
    "ai_min_gap": 2.0,
    "ai_max_gap": 5.0,
    # 人格默认留空 —— 由 config.json 决定，注册后在「人格设定」页填
    "persona_name": "",
    "persona_identity": "",
    "persona_behavior": "",
    "persona_speech": "",
    "talk_value": 0.0,
    "reply_to_name": True,
    "split_reply": True,
    "split_max_length": 100,
    "split_max_parts": 3,
    "split_delay_seconds": 0.8,
    "enable_private_chat": True,
    "welcome_msg": "欢迎 {nickname} 加入本群！",
    "farewell_msg": "{nickname} 离开了群聊。",
    "reject_msg": "你已在黑名单中，无法再次加入本群。",
    "notify_admin_on_request": True,
    "ai_context_size": 4,
    "memory_check_every": 4,
    "memory_min_importance": 3,
    "memory_inject_count": 1,
    "memory_user_enabled": True,
    "memory_user_inject_count": 3,
    "chatlog_enabled": True,
    "chatlog_keep_days": 30,
    # 外观：壁纸
    "wallpaper_enabled": True,
    "wallpaper_dim": 40,
    "wallpaper_blur": 0,
    # 预设壁纸：kv / aurora / frost / rose / gold / ink / custom
    "wallpaper_preset": "kv",
    # 自动轮播：页面开着时定时切预设
    "wallpaper_rotate": False,
    "wallpaper_rotate_sec": 15,
    # 面板外观（可在「外观」里调）
    "ui_side_on": True,
    "ui_side_blur": 18,
    "ui_side_alpha": 72,
    "ui_side_color": "#0a1022",
    "ui_face_on": True,
    "ui_face_blur": 24,
    "ui_face_alpha": 52,
    "ui_face_color": "#ffffff",
    # 深色模式下内容面/侧栏的底色。不能沿用浅色那套：
    # 注入的 :root 排在页面深色 @media 之后，会用浅色值盖掉深色令牌，
    # 结果 --text(#d0e0f0) 压在 #fff 上只有 1.2:1。
    "ui_face_color_dark": "#141d38",
    "ui_side_color_dark": "#0a1022",
    "verify_enabled": True,
    "verify_timeout": 60,
    "min_qq_level": 25,
    "admin_token": "change-me-please",
    "port": 8080,
    "backup_enabled": True,
    "backup_hour": 3,
    "backup_keep_days": 7,
    "vision_enabled": False,
    "vision_api_host": "https://open.bigmodel.cn/api/paas/v4",
    "vision_api_key": "",
    "vision_model": "glm-4v-flash",
    "vision_timeout": 90,
    "vision_max_image_kb": 4096,
    # ---------------- 语音合成（IndexTTS / OpenAI 兼容） ----------------
    "voice_enabled": False,
    "voice_api_url": "http://127.0.0.1:7861/v1/audio/speech",
    "voice_api_key": "",
    "voice_name": "",
    "voice_speed": 1.0,
    "voice_timeout": 90,
    "voice_random_chance": 0.0,
    "voice_trigger_keywords": ["语音", "说话", "念出来", "读出来", "讲给我听", "voice"],
    "voice_max_chars": 200,
    # 情感控制：实测 speaker 模式在短句上几乎不在标点处换气（一口气念完），
    # vector 模式能让停顿数翻倍、断句自然。默认走 vector。
    # 向量顺序：喜, 怒, 哀, 惧, 厌恶, 低落, 惊喜, 平静
    "voice_emotion_mode": "vector",
    "voice_emotion_vector": [0.1, 0, 0, 0, 0, 0, 0, 0.4],
    "voice_emotion_strength": 0.65,
    # 音频后处理：off / voice_clarity / clear_narration / deharsh / warm / normalize
    # deharsh 和 warm 实测能把刺耳频段降约 17%（谱质心 2905->2670Hz）
    "voice_postprocess_preset": "deharsh",
    "voice_postprocess_strength": 1.0,
    # 情绪名 -> 音色名。命中时用该音色（走 reference_audio，情感参考音频已存在音色档案里）
    "voice_emotion_voices": {"平静": "卡提希娅-平静", "喜": "卡提希娅-喜", "怒": "卡提希娅-怒", "哀": "卡提希娅-哀", "惧": "卡提希娅-惧", "惊喜": "卡提希娅-惊喜", "低落": "卡提希娅-哀", "厌恶": "卡提希娅-怒"},
    # ---------------- 知识库 ----------------
    "kb_enabled": True,
    "kb_auto_inject": True,          # AI 对话时自动把命中的条目注入提示词
    "kb_match_threshold": 0.5,       # 匹配分数阈值（0-1），实测：真命中 0.70-0.75，弱相关 0.35
    "kb_inject_count": 3,            # 最多注入几条
    "kb_answer_max_chars": 500,      # 单条回答最大长度
    "kb_max_per_group": 500,         # 每个群最多条目数（防刷）
    "kb_add_keywords": ["补充知识", "添加知识", "记知识", "教你这个", "知识库添加"],
    "kb_del_keywords": ["删除知识", "删知识", "忘记知识"],
    "kb_list_keywords": ["知识库", "知识列表", "会什么"],
    "kb_search_keywords": ["是什么", "什么是", "是谁", "在哪", "怎么", "为什么",
                           "多少", "多久", "什么时候", "哪个", "多少级", "攻略"],
}

if not os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=4)
    # 桌面版会走注册向导，不走这个"手改文件"的提示
    if os.environ.get("ECHO_SETUP_WIZARD") != "1":
        show_msg("首次运行 · 请先配置",
                 f"已生成配置文件：\n{CONFIG_PATH}\n\n"
                 f"请用记事本打开，把以下两项改成你自己的：\n\n"
                 f"  1. superusers —— 你的QQ号\n"
                 f"  2. admin_token —— 管理密码\n\n"
                 f"改完后保存，再双击本程序启动。")
        sys.exit(0)


# ---------------- 配置读取与保存 ----------------
def _atomic_write_json(path, data):
    """先写临时文件再替换，避免进程被强杀时把配置文件写坏。"""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


def save_config():
    try:
        _atomic_write_json(CONFIG_PATH, CFG)
        return True, ""
    except Exception as e:
        log_startup(f"配置保存失败: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {e}"


def load_config_file():
    """读配置；文件损坏时自动备份并用默认值恢复，避免直接启动失败。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("配置根节点必须是对象")
        return data
    except Exception as e:
        log_startup(f"配置读取失败: {type(e).__name__}: {e}")
        try:
            shutil.copy2(CONFIG_PATH, CONFIG_PATH + ".broken")
        except Exception:
            pass
        show_msg("配置文件损坏",
                 f"读取 {CONFIG_PATH} 失败：{type(e).__name__}: {e}\n\n"
                 f"已备份为 config.json.broken，并恢复默认配置。")
        return {}


# 允许通过管理界面修改的配置项（白名单）
EDITABLE_KEYS = {
    "ai_backend", "ollama_host", "ai_model", "ollama_api_key",
    "deepseek_api_host", "deepseek_model", "deepseek_api_key",
    "ai_context_size", "memory_check_every",
    "memory_min_importance", "memory_inject_count", "min_qq_level",
    "memory_user_enabled", "memory_user_inject_count",
    "chatlog_enabled", "chatlog_keep_days",
    "verify_enabled", "verify_timeout",
    "welcome_msg", "farewell_msg", "reject_msg", "admin_token",
    "persona_name", "persona_identity", "persona_behavior", "persona_speech",
    "talk_value", "reply_to_name", "split_reply", "split_max_length",
    "split_max_parts", "split_delay_seconds", "enable_private_chat",
    "ai_min_gap", "ai_max_gap",
    "backup_enabled", "backup_hour", "backup_keep_days",
    "vision_enabled", "vision_api_host", "vision_api_key",
    "vision_model", "vision_timeout", "vision_max_image_kb",
    # 语音
    "voice_enabled", "voice_api_url", "voice_api_key", "voice_name",
    "voice_speed", "voice_timeout", "voice_random_chance",
    "voice_trigger_keywords", "voice_max_chars",
    "voice_emotion_mode", "voice_emotion_vector", "voice_emotion_strength",
    "voice_emotion_voices",
    "voice_postprocess_preset", "voice_postprocess_strength",
    # 知识库
    "kb_enabled", "kb_auto_inject", "kb_match_threshold", "kb_inject_count",
    "kb_answer_max_chars", "kb_max_per_group",
    "kb_add_keywords", "kb_del_keywords", "kb_list_keywords", "kb_search_keywords",
    # 外观 —— 这几个之前**漏在白名单外**，导致点保存静默丢弃
    # （api_cfg_set 直接 continue，前端收到 ok:true 但什么都没存）
    "wallpaper_enabled", "wallpaper_dim", "wallpaper_blur",
    "wallpaper_preset", "wallpaper_rotate", "wallpaper_rotate_sec",
    "ui_side_on", "ui_side_blur", "ui_side_alpha", "ui_side_color",
    "ui_face_on", "ui_face_blur", "ui_face_alpha", "ui_face_color",
    "ui_face_color_dark", "ui_side_color_dark",
}

# 数值型配置的取值范围，防止手滑填出负数/超大值导致功能异常
INT_RANGES = {
    "port": (1, 65535),
    "min_qq_level": (0, 200),
    "ai_context_size": (1, 50),
    "memory_check_every": (1, 100),
    "memory_min_importance": (1, 5),
    "memory_inject_count": (0, 20),
    "memory_user_inject_count": (0, 20),
    "chatlog_keep_days": (1, 3650),
    "verify_timeout": (10, 3600),
    "split_max_length": (10, 2000),
    "split_max_parts": (1, 10),
    "backup_hour": (0, 23),
    "backup_keep_days": (1, 3650),
    "vision_timeout": (5, 600),
    "vision_max_image_kb": (1, 102400),
    "voice_timeout": (5, 600),
    "voice_max_chars": (10, 2000),
    "kb_inject_count": (0, 10),
    "kb_answer_max_chars": (20, 5000),
    "kb_max_per_group": (1, 100000),
}
FLOAT_RANGES = {
    "talk_value": (0.0, 1.0),
    "split_delay_seconds": (0.0, 30.0),
    "ai_min_gap": (0.0, 600.0),
    "ai_max_gap": (0.0, 600.0),
    "min_qq_level": (0.0, 200.0),
    "voice_speed": (0.5, 2.0),
    "voice_random_chance": (0.0, 1.0),
    "voice_emotion_strength": (0.0, 1.0),
    "kb_match_threshold": (0.0, 1.0),
}


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def cfg_int(key, default=0):
    """把配置读成 int，脏值（''/None/'abc'）自动退回默认值。"""
    v = CFG.get(key, default)
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return int(default)
    rng = INT_RANGES.get(key)
    return _clamp(n, *rng) if rng else n


def cfg_float(key, default=0.0):
    v = CFG.get(key, default)
    try:
        f = float(v)
    except (TypeError, ValueError):
        f = float(default)
    if f != f:  # NaN
        f = float(default)
    rng = FLOAT_RANGES.get(key)
    return _clamp(f, *rng) if rng else f


def cfg_bool(key, default=False):
    v = CFG.get(key, default)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "开启")
    return bool(v)


def cfg_str(key, default=""):
    v = CFG.get(key, default)
    return default if v is None else str(v)


def cfg_list(key):
    """配置项读成字符串列表，兼容 'a,b' 与手填的单个字符串。"""
    v = CFG.get(key, [])
    if isinstance(v, str):
        v = [x for x in re.split(r"[\n,，;；]", v)]
    if not isinstance(v, (list, tuple, set)):
        return []
    out = []
    for x in v:
        s = str(x).strip()
        if s:
            out.append(s)
    return out


def normalize_config():
    """启动时把配置里的数字/布尔项统一成正确类型，脏数据就地纠正。"""
    changed = []
    for k in list(INT_RANGES):
        if k not in CFG:
            continue
        try:
            good = cfg_int(k, DEFAULT_CONFIG.get(k, 0))
        except Exception:
            continue
        if CFG[k] != good:
            CFG[k] = good
            changed.append(k)
    for k in list(FLOAT_RANGES):
        if k not in CFG:
            continue
        good = cfg_float(k, DEFAULT_CONFIG.get(k, 0.0))
        if CFG[k] != good:
            CFG[k] = good
            changed.append(k)
    for k, v in DEFAULT_CONFIG.items():
        if isinstance(v, bool) and k in CFG:
            good = cfg_bool(k, v)
            if CFG[k] != good:
                CFG[k] = good
                changed.append(k)
    # 这两个是浮点（在 FLOAT_RANGES 里），用 cfg_int 读会截断：
    # ai_min_gap=2.5 时 int() 变 2，"2.3 < 2" 判成 False 就不修正了。
    if cfg_float("ai_max_gap", 5.0) < cfg_float("ai_min_gap", 2.0):
        CFG["ai_max_gap"] = cfg_float("ai_min_gap", 2.0)
        changed.append("ai_max_gap")
    # 情感向量：必须是 8 个 0-1 的数字。
    # 不归一化的话，脏值（如 1.5、-1、字符串）会存进配置，
    # 之后合成时才 float() 报错，而且错误点离原因很远。
    if "voice_emotion_vector" in CFG:
        raw = CFG.get("voice_emotion_vector")
        if isinstance(raw, (list, tuple)):
            vec, dirty = [], False
            for x in list(raw)[:8]:
                try:
                    f = float(x)                 # None / 字符串 都会在这里抛
                    if f < 0.0 or f > 1.0:
                        dirty = True
                except (TypeError, ValueError):
                    f, dirty = 0.0, True
                vec.append(_clamp(f, 0.0, 1.0))
            if len(raw) != 8:
                dirty = True
            vec += [0.0] * (8 - len(vec))
            if dirty:
                CFG["voice_emotion_vector"] = vec
                changed.append("voice_emotion_vector")
        else:
            CFG["voice_emotion_vector"] = [0.0] * 8
            changed.append("voice_emotion_vector")
    if "voice_emotion_mode" in CFG:
        m = str(CFG.get("voice_emotion_mode") or "").strip().lower()
        if m not in ("vector", "speaker"):
            m = "vector"
        if CFG.get("voice_emotion_mode") != m:
            CFG["voice_emotion_mode"] = m
            changed.append("voice_emotion_mode")
    return changed


CFG = load_config_file()
for k, v in DEFAULT_CONFIG.items():
    CFG.setdefault(k, v)
normalize_config()

SUPERUSERS = set(str(x).strip() for x in cfg_list("superusers"))
ADMIN_TOKEN = cfg_str("admin_token", "change-me-please")
PORT = cfg_int("port", 8080)
MIN_QQ_LEVEL = cfg_int("min_qq_level", 25)
MAX_CTX = max(2, cfg_int("ai_context_size", 4) * 2)
CHECK_EVERY = cfg_int("memory_check_every", 4)
MIN_IMPORTANCE = cfg_int("memory_min_importance", 3)
INJECT_COUNT = cfg_int("memory_inject_count", 1)
AI_HARD_TIMEOUT = 90.0

save_config()

if (not SUPERUSERS or not ADMIN_TOKEN or ADMIN_TOKEN == "change-me-please") \
        and os.environ.get("ECHO_SETUP_WIZARD") != "1":
    _missing = []
    if not SUPERUSERS:
        _missing.append("  · superusers —— 填你的QQ号（可以填多个，用逗号隔开）")
    if not ADMIN_TOKEN or ADMIN_TOKEN == "change-me-please":
        _missing.append("  · admin_token —— 自己设一个密码，管理界面用它登录")
    show_msg("配置未完成 · 还差这两项",
             f"配置文件位置：\n{CONFIG_PATH}\n\n"
             f"用记事本打开，填上：\n" + "\n".join(_missing) +
             "\n\n填完保存，再双击本程序即可启动。\n\n"
             "（提示：如果只想要文字对话，deepseek_api_key 也建议一起填上）")
    sys.exit(0)


import httpx
import nonebot
from nonebot import on_notice, on_request, on_message
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import (
    Bot, Adapter as ONEBOT_V11,
    GroupIncreaseNoticeEvent, GroupDecreaseNoticeEvent,
    GroupRequestEvent, GroupMessageEvent, PrivateMessageEvent,
    MessageEvent,
    MessageSegment, Message,
)

nonebot.init(driver="~fastapi+~websockets", host="127.0.0.1", port=PORT,
             superusers=SUPERUSERS, log_level="WARNING")
driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11)
START_TIME = time.time()

REQUEST_THROTTLE_SECONDS = 60
REQUEST_EXPIRE_SECONDS = 86400

pending_requests = {}
request_cooldown = {}
vision_cache = {}
_backup_lock = threading.Lock()
_http_client = None
_http_loop = None
_voice_lock = threading.Lock()



class _ProxyFallbackTransport(httpx.AsyncBaseTransport):
    """先走代理；代理连不上就当场改走直连。

    为什么需要（真实 bug，用户日志里是 `ConnectError: All connection attempts failed`）：
      Clash / v2ray 这类工具的「系统代理」会随模式切换自动开关。
      httpx 是在**构造客户端时**读取系统代理并固化进 mounts 的，
      而我们的共享客户端是长期复用的 —— 代理一关，请求仍然发往那个
      已经没人监听的端口，于是每次调用都失败，且重启程序之前一直好不了。

    这里在连接失败时自动改直连，并 60 秒内不再试代理；
    60 秒后重新试一次，所以把代理重新打开也能自动恢复，不用重启。
    """

    def __init__(self, proxy_url):
        self._proxy_url = proxy_url
        self._proxy = None
        self._direct = httpx.AsyncHTTPTransport(trust_env=False, retries=0)
        self._skip_proxy_until = 0.0

    def _p(self):
        if self._proxy is None:
            self._proxy = httpx.AsyncHTTPTransport(
                proxy=self._proxy_url, trust_env=False, retries=0)
        return self._proxy

    async def handle_async_request(self, request):
        if time.time() < self._skip_proxy_until:
            return await self._direct.handle_async_request(request)
        try:
            return await self._p().handle_async_request(request)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError) as e:
            # 代理没了（或者端口关了）—— 直连重试，别再让用户对着报错发愣
            self._skip_proxy_until = time.time() + 60.0
            try:
                log("warn", "网络",
                    f"代理 {self._proxy_url} 连不上（{type(e).__name__}），"
                    f"这次改走直连，60 秒内不再试代理")
            except Exception:
                pass
            return await self._direct.handle_async_request(request)

    async def aclose(self):
        for t in (self._proxy, self._direct):
            if t is None:
                continue
            try:
                await t.aclose()
            except Exception:
                pass


def _http_mounts():
    """本机地址强制直连，远程地址照常走系统代理。

    为什么需要这个（真实 bug）：
      Windows 上 urllib.request.getproxies() 只读注册表的 ProxyServer，
      完全忽略 ProxyOverride 里的 "localhost;127.*;192.168.*;<local>" 绕过规则。
      所以一旦开了系统代理（Clash / v2ray 等），httpx 会把连本机服务的请求
      也发给代理 —— 代理不转发 localhost，直接回 502（响应体为空）。
      Clash 的系统代理会随模式/节点切换自动开关，表现就是「语音时好时坏」。

    httpx 按最长前缀匹配，本机 mount 比 "http://" 通配更具体，优先生效；
    远程地址仍由 httpx 按系统代理处理（NO_PROXY 语义保留）。
    """
    mounts = {}
    try:
        direct = httpx.AsyncHTTPTransport(trust_env=False, retries=0)
        for host in ("127.0.0.1", "localhost", "[::1]", "0.0.0.0"):
            mounts[f"http://{host}"] = direct
            mounts[f"https://{host}"] = direct
    except Exception:
        return None
    # 远程地址：有系统代理就挂"连不上自动直连"的传输层。
    # 注意客户端是 trust_env=False 建的 —— 代理只由这里决定，
    # 免得 httpx 在构造时把某一次的系统代理状态固化下来再也不会更新。
    try:
        import urllib.request as _ur
        _px = _ur.getproxies()
        _purl = (_px.get("https") or _px.get("http") or "").strip()
        if _purl:
            _t = _ProxyFallbackTransport(_purl)
            mounts["http://"] = _t
            mounts["https://"] = _t
    except Exception:
        pass
    return mounts or None


def get_http():
    """共享 HTTP 客户端：复用连接池，避免每次请求都重新握手。"""
    global _http_client, _http_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _http_client is None or _http_client.is_closed or _http_loop is not loop:
        old = _http_client
        # HTTP 头必须是 ASCII —— 中文字符会抛 UnicodeEncodeError（真实 bug：
        # 曾把 User-Agent 写成中文，导致 AI/识图/语音三条链路全部失败）
        # 以前这里是 `_ua = "Nekolyra/1.0"` 再 try encode 一遍再赋同一个
        # 字面量 —— 字面量不可能非 ASCII，except 分支永远不执行。用 _ua()。
        # 本地变量不能叫 _ua —— 那会遮蔽同名的模块函数，
        # 变成"在自己赋值之前引用自己"（NameError）。
        _ua_hdr = _ua()
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=15.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=True,
            headers={"User-Agent": _ua_hdr},
            # 代理完全由 _http_mounts 决定，见 _ProxyFallbackTransport 的说明。
            # trust_env=True 会在构造时把当时的系统代理固化进 mounts，
            # 之后代理开关一变就再也跟不上（代理一关就集体 ConnectError）。
            trust_env=False,
            mounts=_http_mounts(),          # 本机服务（TTS/Ollama）直连
        )
        _http_loop = loop
        if old is not None:
            try:
                if old.is_closed:
                    pass
                else:
                    asyncio.create_task(old.aclose())
            except Exception:
                pass
    return _http_client


async def close_http():
    global _http_client
    client, _http_client = _http_client, None
    if client is not None and not client.is_closed:
        try:
            await client.aclose()
        except Exception:
            pass


def _cleanup_voice_files():
    """清理 voice_tmp 目录里的历史临时文件（超过 1 小时的）。"""
    try:
        now = time.time()
        for f in os.listdir(VOICE_TMP_DIR):
            if not f.endswith(".mp3"):
                continue
            p = os.path.join(VOICE_TMP_DIR, f)
            try:
                if now - os.path.getmtime(p) > 3600:
                    os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


def backup_db(force=False):
    """备份数据库。返回 (ok, message)。"""
    if not force and not cfg_bool("backup_enabled", True):
        return False, "自动备份已关闭"
    if not os.path.exists(DB_PATH):
        return False, "数据库文件不存在"
    today = date.today().isoformat()
    dst = os.path.join(BACKUP_DIR, f"bot-{today}.db")
    if os.path.exists(dst) and not force:
        return True, "今天已备份过"
    with _backup_lock:
        try:
            DB.checkpoint()
            shutil.copy2(DB_PATH, dst)
            keep = cfg_int("backup_keep_days", 7)
            cutoff = (date.today() - timedelta(days=keep)).isoformat()
            removed = 0
            for f in os.listdir(BACKUP_DIR):
                if f.startswith("bot-") and f.endswith(".db"):
                    d = f[4:-3]
                    if d < cutoff:
                        try:
                            os.remove(os.path.join(BACKUP_DIR, f))
                            removed += 1
                        except Exception:
                            pass
            size_kb = os.path.getsize(dst) // 1024
            msg = f"数据库已备份（{size_kb}KB）" + (f"，清理 {removed} 份旧备份" if removed else "")
            log("success", "备份", msg)
            return True, msg
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            log("error", "备份", msg)
            return False, msg


async def daily_backup_task():
    if not cfg_bool("backup_enabled", True):
        log("info", "备份", "自动备份未开启")
        return
    backup_db()
    _cleanup_voice_files()
    while True:
        try:
            hour = cfg_int("backup_hour", 3)
            now = datetime.now()
            next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            wait_secs = (next_run - now).total_seconds()
            await asyncio.sleep(wait_secs)
            backup_db()
            _cleanup_voice_files()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log("warn", "备份", f"任务异常: {type(e).__name__}: {e}")
            await asyncio.sleep(3600)


async def describe_image(image_url, hint=""):
    if not cfg_bool("vision_enabled", False):
        return None
    image_url = str(image_url or "").strip()
    if not image_url:
        return None
    api_key = cfg_str("vision_api_key").strip()
    if not api_key:
        log("warn", "识图", "未配置 vision_api_key，跳过")
        return None

    cache_key = hashlib.md5(image_url.encode("utf-8", "ignore")).hexdigest()
    cached = vision_cache.get(cache_key)
    if cached:
        return cached

    host = cfg_str("vision_api_host").rstrip("/")
    if not host:
        log("warn", "识图", "未配置 vision_api_host，跳过")
        return None
    model = cfg_str("vision_model", "glm-4v-flash")
    timeout = cfg_float("vision_timeout", 90)
    max_kb = cfg_int("vision_max_image_kb", 4096)

    try:
        client = get_http()
        async with client.stream("GET", image_url, timeout=timeout,
                                 follow_redirects=True) as r:
            r.raise_for_status()
            chunks = []
            total = 0
            limit = max_kb * 1024
            async for chunk in r.aiter_bytes(64 * 1024):
                total += len(chunk)
                if total > limit:
                    log("warn", "识图", f"图片超过 {max_kb}KB 上限，跳过")
                    return None
                chunks.append(chunk)
            img_bytes = b"".join(chunks)
    except Exception as e:
        log("error", "识图", f"下载失败: {type(e).__name__}: {e}")
        return None

    if not img_bytes:
        log("warn", "识图", "图片内容为空")
        return None

    size_kb = len(img_bytes) // 1024
    mime = "image/jpeg"
    if img_bytes[:8].startswith(b"\x89PNG"):
        mime = "image/png"
    elif img_bytes[:4] == b"GIF8":
        mime = "image/gif"
    elif img_bytes[:2] == b"BM":
        mime = "image/bmp"
    elif img_bytes[:4] == b"RIFF" and img_bytes[8:12] == b"WEBP":
        mime = "image/webp"

    b64 = base64.b64encode(img_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    prompt_text = hint.strip() if hint.strip() else (
        "请识别这张图片里的内容。按以下顺序输出："
        "1. 画面主体（人/动物/物体）"
        "2. 如果是二次元角色，说出角色名字，并指出出自哪部游戏或哪部动漫；"
        "如果是真人明星、主播、网红，说出名字；"
        "如果不认识，就直接说「不认识」，不要瞎猜。"
        "3. 主要颜色（头发、衣服、背景）"
        "4. 画面中的文字（如果有，原样抄下来）"
        "5. 整体场景或风格"
        "直接输出，不要解释。总字数控制在 120 字以内。"
    )

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt_text},
                ],
            }
        ],
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        client = get_http()
        r = await client.post(f"{host}/chat/completions", json=payload,
                              headers=headers, timeout=timeout)
        if r.status_code >= 400:
            log("error", "识图", f"HTTP {r.status_code}: {r.text[:300]}")
            return None
        data = r.json()
        try:
            content = (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            content = ""
        if not content:
            log("warn", "识图", f"空响应: {str(data)[:200]}")
            return None
        if len(vision_cache) >= 200:
            vision_cache.clear()
        vision_cache[cache_key] = content
        log("success", "识图", f"{size_kb}KB → {content[:60]}")
        return content
    except Exception as e:
        log("error", "识图", f"{type(e).__name__}: {e}")
        return None


def extract_images(event):
    urls = []
    try:
        for seg in event.message:
            if seg.type == "image":
                url = seg.data.get("url") or seg.data.get("file")
                if url and str(url).startswith(("http://", "https://")):
                    urls.append(str(url))
    except Exception:
        pass
    return urls


async def enrich_text_with_images(event, text, urls=None):
    # urls 允许调用方传进来：_on_ai 为了判断"是不是空消息"已经算过一次了，
    # 这里再 extract_images(event) 等于把整段 event.message 又扫一遍。
    if not cfg_bool("vision_enabled", False):
        return text, False
    if urls is None:
        urls = extract_images(event)
    if not urls:
        return text, False

    descs = []
    for u in urls[:3]:
        d = await describe_image(u)
        if d:
            descs.append(d)

    if not descs:
        return text, False

    image_part = "[图片内容] " + " / ".join(descs)
    if text.strip():
        return f"{text.strip()}\n{image_part}", True
    return image_part, True


# ============================================================
# 语音合成（IndexTTS / OpenAI 兼容）
# ============================================================
def should_send_voice(user_text: str) -> bool:
    """判断这次要不要发语音：开关打开 + 命中关键词 或 随机概率命中。"""
    if not cfg_bool("voice_enabled", False):
        return False
    if not cfg_str("voice_api_url").strip():
        return False
    text = str(user_text or "")
    for kw in cfg_list("voice_trigger_keywords"):
        if kw and kw in text:
            return True
    chance = cfg_float("voice_random_chance", 0.0)
    if chance > 0 and random.random() < chance:
        return True
    return False


def _voice_prompt_hint(group_id) -> str:
    """给 AI 的能力说明：明确告诉它"你能发语音"，否则它会否认、甚至以为消息发失败。

    这是修复"AI 总以为自己没在发语音"的关键 —— 模型的自我认知只能来自提示词，
    它看不到自己实际发出了什么。
    """
    if not cfg_bool("voice_enabled", False):
        return ""
    voice_name = cfg_str("voice_name").strip()
    if not voice_name:
        return ""
    trigger_kws = [k for k in cfg_list("voice_trigger_keywords") if k]
    chance = cfg_float("voice_random_chance", 0.0)
    trigger_desc = "、".join(trigger_kws[:6]) if trigger_kws else "（未设置）"

    hint = (
        "【你的语音能力（真实存在，不要说不会）】\n"
        f"1. 你可以发语音消息，用的是音色「{voice_name}」。\n"
        f"2. 当群友的消息里出现这些词时，你说的内容会以语音形式发出去：{trigger_desc}。\n"
        "   语音和文字都是你说话的方式，你**不是**文字助手、不是纯文字 AI，"
        "不要把自己想成只能打字的东西。\n"
        # 这条单独排在最前的实质位置：它最容易违反，且违反时最惹人烦
        "3. 【最高优先级】当对方要求「重复/复述/念这句/照念/跟着说」某句话时，"
        "只输出那句话本身，前后**一个字都不要加**。\n"
        "   不要加「好啦」「念给你听哦」这类前言；"
        "不要加「念完有点心疼你」这类评论或感想；也不要加省略号拖音。\n"
    )
    n = 3
    if chance > 0:
        n += 1
        hint += f"{n}. 另外你有 {chance*100:.0f}% 的随机概率主动用语音回复。\n"
    hint += (
        f"{n+1}. 【最重要】永远不要主动提「语音」这件事，也绝对不要说"
        "「我发不出语音」「我只能打字」「我是文字 AI」「我没有语音功能」这类话。\n"
        "   只要收到指令，语音就会发出去 —— 你若说发不出，就是当场自相矛盾。\n"
        f"{n+2}. 【最重要】发语音时正文不会另发一遍文字，所以不要说"
        "「我发文字给你」「你听不到就看看字」之类的话。\n"
        f"{n+3}. 语音是把你说的内容读出来，所以回答要顺口：不要列表符号、"
        "不要 markdown、不要括号旁白，颜文字也可省。\n"
        f"{n+4}. 被点名要求「带感情」时，用语气词和标点表达情绪即可"
        "（例如「呜——」「出发！！」），不要描述自己在用什么感情。\n"
        # 这一条让 AI 自己给出情绪，bot 据此选择音色（智能选音色）
        f"{n+5}. 【必须】每次回复的**最后**加上情绪标签，格式严格是"
        "「[情绪:xx]」，xx 只能是这几个之一：喜、怒、哀、惧、厌恶、低落、惊喜、平静。\n"
        "   标签要符合你这句话的真实语气，不要随便填。例如：\n"
        "     好。——那我去准备。[情绪:平静]\n"
        "     她还在衰弱……我一天都不敢停。[情绪:哀]\n"
        "     你居然真的做到了！[情绪:惊喜]\n"
        "   这个标签是内部标记，不会给对方看到，也不要在正文里提到它。"
    )
    return hint



# 模型偶尔仍会冒出"我发不出语音"这类话（提示词说明再清楚也压不住），
# 而这句话恰恰会被当成语音内容念出去 —— 当场自相矛盾。
# 这里做一次兜底替换，避免出现"用语音说自己发不出语音"的荒唐情况。
_VOICE_DENY_PATTERNS = [
    re.compile(r'[^。！？!?\n]{0,30}(发不出|发不了|不能发|没法发)[^。！？!?\n]{0,6}语音[^。！？!?\n]{0,20}'),
    re.compile(r'[^。！？!?\n]{0,30}语音[^。！？!?\n]{0,8}(失败|不行|不可用|坏了)[^。！？!?\n]{0,20}'),
    re.compile(r'[^。！？!?\n]{0,30}(只能|只会)(打字|发文字)[^。！？!?\n]{0,20}'),
    re.compile(r'[^。！？!?\n]{0,20}(我是|人家是|就是个?)[^。！？!?\n]{0,6}(纯|个|的)?(文字|文本)[^。！？!?\n]{0,4}(AI|ai|助手|机器人|程序)[^。！？!?\n]{0,20}'),
    re.compile(r'[^。！？!?\n]{0,20}(是|只是|就是个?)[^。！？!?\n]{0,6}(纯|个|的)?(文字|文本)(AI|助手|机器人|程序)[^。！？!?\n]{0,20}'),
    re.compile(r'[^。！？!?\n]{0,20}(没有|不支持|不具备)语音(功能|能力)[^。！？!?\n]{0,20}'),
]


def strip_voice_denial(text: str) -> str:
    """去掉"我发不出语音"这类与事实矛盾的话。"""
    if not text:
        return text
    out = str(text)
    hit = False
    for pat in _VOICE_DENY_PATTERNS:
        new = pat.sub("", out)
        if new != out:
            hit = True
            out = new
    if not hit:
        return text
    out = re.sub(r'[ \t]{2,}', ' ', out)
    out = re.sub(r'\n{2,}', '\n', out).strip(" \t\n，,。.、~～")
    log("warn", "语音", f"模型说了与事实矛盾的话，已清洗：{text[:50]} -> {out[:50]}")
    # 整句都是矛盾话时（清洗后为空），给一个中性兜底。
    # 不能用"收到啦"这种万能回复 —— 会让它形成"不管说什么都回这句"的复读。
    return out or "嗯嗯～"


def _at_display(seg) -> str:
    """把 at 段转成可读文本。

    nonebot 的 get_plaintext() 只取 text 段，会把 @ 的人整个丢掉 ——
    「把 @洛 踢了」变成「把  踢了」，AI 和超管命令都看不到目标是谁。
    """
    data = getattr(seg, "data", None) or {}
    qq = str(data.get("qq") or "").strip()
    if not qq:
        return ""
    if qq == "all":
        return "@全体成员"
    name = str(data.get("name") or data.get("card") or "").strip()
    return f"@{name}({qq})" if name else f"@{qq}"


def _at_is_self_bot(seg, bot_self_id) -> bool:
    """判断这个 at 段是不是 @ 机器人自己。"""
    if not bot_self_id:
        return False
    data = getattr(seg, "data", None) or {}
    qq = str(data.get("qq") or "").strip()
    return bool(qq) and qq == str(bot_self_id).strip()


def plaintext_with_at(event, skip_self_id=None) -> str:
    """取消息明文，但保留 @ 信息（文本段 + @昵称(QQ)），其它段忽略。

    为什么不能直接用 event.get_plaintext()：
      它是纯文本视图，at/image/face 等非 text 段会被丢弃。
      群里「把 @某人 踢了」这类指令，目标只在 at 段里，丢了就等于没说。

    skip_self_id：传入机器人的 QQ 时，会跳过"@机器人自己"的那一段。
    命令解析必须用它 —— 否则「@机器人 踢 @某人」的明文会以「@机器人」开头，
    cmd.startswith(命令词) 永远匹配不上，所有管理指令都会静默失效。
    """
    try:
        parts = []
        for seg in event.message:
            if seg.type == "text":
                parts.append(str(seg.data.get("text") or ""))
            elif seg.type == "at":
                if skip_self_id and _at_is_self_bot(seg, skip_self_id):
                    continue
                s = _at_display(seg)
                if not s:
                    continue
                # @ 前后各补一个空格，避免 "@甲你好" 这种粘连
                if parts and not parts[-1].endswith((" ", "\u3000")):
                    parts.append(" ")
                parts.append(s)
                parts.append(" ")
        text = "".join(parts)
    except Exception:
        return event.get_plaintext()
    text = re.sub(r'[ \t\u3000]{2,}', ' ', text)
    return text.strip()


# ==================== 语音情感 ====================
# IndexTTS 的 emotion_mode=text（服务端自动按文本判断情感）在本机被 low-VRAM 禁用了
# （实测 HTTP 400），所以只能由 bot 从用户话里识别想要的情绪，映射成 8 维向量传过去。
# 向量顺序固定：喜, 怒, 哀, 惧, 厌恶, 低落, 惊喜, 平静
_VOICE_EMO_INDEX = {"喜": 0, "怒": 1, "哀": 2, "惧": 3,
                    "厌恶": 4, "低落": 5, "惊喜": 6, "平静": 7}

_VOICE_EMO_KEYWORDS = {
    # 中文 / 英文 / 日文 三语并列。英文一律小写（匹配时已把输入转小写）。
    "喜": (
        "开心", "高兴", "快乐", "兴奋", "喜悦", "欢快", "愉快", "笑", "活泼",
        "元气", "元气满满", "甜甜", "甜", "雀跃", "欢喜",
        "happy", "happily", "joy", "joyful", "cheerful", "cheerfully",
        "excited", "glad", "delighted", "merry", "laughing", "laugh",
        "嬉し", "嬉しい", "楽しい", "楽しく", "喜ん", "喜び", "楽しそう",
        "上機嫌", "ウキウキ", "るんるん",
    ),
    "怒": (
        "生气", "愤怒", "恼火", "凶", "发火", "气愤", "暴躁", "愤慨",
        "angry", "angrily", "mad", "furious", "rage", "irritated", "annoyed",
        "怒っ", "怒り", "怒って", "腹立", "ムカつ", "激怒",
    ),
    "哀": (
        "伤感", "悲伤", "难过", "伤心", "哀伤", "忧伤", "哭", "委屈", "心酸",
        "眼泪", "凄", "痛心", "哽咽", "催泪", "愁",
        "sad", "sadly", "sadness", "sorrow", "sorrowful", "cry", "crying",
        "tearful", "grief", "grieving", "mournful", "heartbroken", "melancholy",
        "悲し", "悲しく", "悲しい", "哀し", "切な", "泣き", "泣い", "涙",
        "しんみり", "物悲し",
    ),
    "惧": (
        "害怕", "恐惧", "惊恐", "慌张", "惧怕", "惶恐",
        "afraid", "scared", "fear", "fearful", "terrified", "frightened", "panic",
        "怖", "恐い", "怖い", "恐ろし", "怯え", "ビクビク",
    ),
    "厌恶": (
        "讨厌", "厌恶", "嫌弃", "恶心", "反感",
        "disgust", "disgusted", "disgusting", "hate", "hateful", "revolted",
        "嫌", "嫌い", "嫌悪", "気持ち悪", "うんざり",
    ),
    "低落": (
        "低落", "失落", "沮丧", "消沉", "郁闷", "无力", "疲惫", "惆怅",
        "压抑", "沉郁", "苍凉",
        "down", "downcast", "depressed", "depressing", "gloomy", "tired",
        "weary", "exhausted", "hopeless", "melancholic", "blue",
        "落ち込", "落ち着かな", "憂鬱", "憂うつ", "沈ん", "疲れ", "げんなり",
        "しょんぼり",
    ),
    "惊喜": (
        "惊喜", "惊讶", "震惊", "吃惊", "意外", "诧异",
        "surprised", "surprise", "surprising", "shocked", "astonished",
        "amazed", "startled",
        "驚", "驚き", "びっくり", "仰天", "驚い",
    ),
    "平静": (
        "平静", "冷静", "淡定", "平稳", "温和", "温柔", "轻声", "安静",
        "缓缓", "淡然",
        "calm", "calmly", "gentle", "gently", "softly", "quiet", "quietly",
        "serene", "peaceful", "soothing", "neutral",
        "落ち着い", "静か", "穏やか", "優しく", "そっと", "淡々", "冷静",
    ),
}

# 只要求"带感情/有感情/生动点"，但没说具体哪种 -> 用最外放的惊喜
_VOICE_EMO_GENERIC = (
    # 只有"明确要求加感情"才算泛化情感诉求。
    # 不能把裸的"感情/emotion"算进来 —— "感情还是不行啊"是在评价效果，
    # 会被误判成"要求带感情"，从而套用外放的「惊喜」（真实 bug）。
    "带感情", "带点感情", "加点感情", "有感情", "有点感情",
    "带情绪", "加情绪", "有情绪", "情绪点",
    "起伏", "有起伏", "抑扬顿挫", "生动点", "生动一点",
    "别平淡", "不要平淡", "别像念稿", "别念稿", "加点戏", "激情点",
    # 英文
    "with emotion", "with feeling", "emotionally", "with expression",
    "expressively", "add emotion", "more emotion", "some emotion",
    "not flat", "no monotone", "with intonation",
    # 日文
    "感情を込め", "感情込め", "感情を入れて", "気持ちを込め",
    "抑揚", "抑揚をつけ", "棒読み", "淡々としない", "表現豊か",
)

_VOICE_EMO_DEFAULT = "惊喜"


_EMO_TAG_RE = re.compile(r"[\[【]\s*情绪\s*[:：]\s*([^\]】]{1,6})\s*[\]】]")
_VALID_EMO = ("喜", "怒", "哀", "惧", "厌恶", "低落", "惊喜", "平静")


def parse_emotion_tag(text: str):
    """提取并剥离 AI 回复里的 [情绪:xx] 标签。

    返回 (干净文本, 情绪)。没有标签时情绪为 ""。
    """
    if not text:
        return text, ""
    emo = ""
    for m in _EMO_TAG_RE.finditer(text):
        cand = m.group(1).strip()
        if cand in _VALID_EMO:
            emo = cand
            break
    clean = _EMO_TAG_RE.sub("", text)
    # 清掉标签留下的多余空白/空行
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, emo


def detect_reply_emotion(reply_text: str, ai_emotion: str = "",
                         user_text: str = "") -> str:
    """挑一个情绪来选音色。优先级：

      1) AI 自己标的情绪（最懂自己这句话是什么语气）
      2) 用户明确要求的情绪（"伤感一点"）
      3) 从回复正文里做关键词兜底
      4) 都没有 -> ""（用默认音色）
    """
    if ai_emotion and ai_emotion in _VALID_EMO:
        return ai_emotion
    if user_text:
        ue = detect_voice_emotion(user_text)
        if ue:
            return ue
    if reply_text:
        re_emo = detect_voice_emotion(reply_text)
        if re_emo:
            return re_emo
    return ""


def detect_voice_emotion(user_text) -> str:
    """从用户的话里识别想要的情绪。识别不出返回 ""。

    优先级：
      1) 引号/书名号内的文字里的情绪（如「重复这句："我好难过"」）
      2) 具体情绪词（伤感/开心/低落…）
      3) 泛化要求（"带感情"）-> 默认外放情绪
    具体词必须压过泛化词，否则「这首要有感情的，伤感一点」会被"感情"抢走。
    """
    t = str(user_text or "")
    if not t:
        return ""

    def scan(s):
        s = str(s or "").lower()        # 英文匹配不区分大小写
        hits = []
        for emo, kws in _VOICE_EMO_KEYWORDS.items():
            for kw in kws:
                pos = s.find(kw)
                if pos >= 0:
                    hits.append((pos, emo))
                    break
        hits.sort()
        return hits[0][1] if hits else ""

    # 1) 先看引号里的话本身是什么情绪
    for m in re.finditer(r'[「『“"\']([^」』”"\']{2,200})[」』”"\']', t):
        inner = scan(m.group(1))
        if inner:
            return inner

    # 2) 再看整句里的具体情绪词
    outer = scan(t)
    if outer:
        return outer

    # 3) 最后才看泛化要求
    if any(kw in t.lower() for kw in _VOICE_EMO_GENERIC):
        return _VOICE_EMO_DEFAULT
    return ""


def emotion_vector_for(emo: str):
    """把情绪名转成 8 维向量：主情绪突出，并留一点"平静"打底，避免发音发飘。"""
    v = [0.0] * 8
    if not emo or emo not in _VOICE_EMO_INDEX:
        return v
    strength = _clamp(cfg_float("voice_emotion_strength", 0.65), 0.0, 1.0)
    # 情绪矢量拉太满会失真，压到 0.7 以内
    level = min(1.0, 0.55 + strength * 0.35)
    v[_VOICE_EMO_INDEX[emo]] = level
    if emo != "平静":
        v[_VOICE_EMO_INDEX["平静"]] = round((1.0 - level) * 0.6, 3)
    return v


# 语言 -> 音色语言后缀
_VOICE_LANG_NAMES = {"ZH": "中文", "JA": "日语", "EN": "英语"}
# 默认情绪音色映射（fallback）。配置里的 voice_emotion_voices 优先。
_DEFAULT_LANG_VOICES = {
    "ZH": {"平静": "卡提希娅-中文-平静", "喜": "卡提希娅-中文-喜",
           "怒": "卡提希娅-中文-怒", "哀": "卡提希娅-中文-哀",
           "惧": "卡提希娅-中文-惧", "惊喜": "卡提希娅-中文-惊喜",
           "厌恶": "卡提希娅-中文-厌恶", "低落": "卡提希娅-中文-哀"},
    "JA": {"平静": "卡提希娅-日语-平静", "喜": "卡提希娅-日语-喜",
           "怒": "卡提希娅-日语-怒", "哀": "卡提希娅-日语-哀",
           "惧": "卡提希娅-日语-惧", "惊喜": "卡提希娅-日语-惊喜",
           "厌恶": "卡提希娅-日语-厌恶", "低落": "卡提希娅-日语-哀"},
    "EN": {"平静": "卡提希娅-英语-平静", "喜": "卡提希娅-英语-喜",
           "怒": "卡提希娅-英语-怒", "哀": "卡提希娅-英语-哀",
           "惧": "卡提希娅-英语-惧", "惊喜": "卡提希娅-英语-惊喜",
           "厌恶": "卡提希娅-英语-怒", "低落": "卡提希娅-英语-哀"},
}


def detect_text_language(text: str) -> str:
    """粗判文本语言，返回 ZH / JA / EN。

    只看假名和拉丁字母的占比：
      - 有假名 -> JA
      - 拉丁字母占比高且无汉字 -> EN
      - 其余 -> ZH
    """
    t = str(text or "")
    if not t:
        return "ZH"
    kana = sum(1 for c in t if "\u3040" <= c <= "\u30ff")
    han = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
    latin = sum(1 for c in t if ("a" <= c <= "z") or ("A" <= c <= "Z"))
    if kana > 0:
        return "JA"
    if latin > 0 and han == 0:
        return "EN"
    if latin > han and han < 2:
        return "EN"
    return "ZH"


# 服务端可用音色缓存（避免每次合成前都请求一次）
_VOICE_LIST_CACHE = {"names": None, "ts": 0.0, "loading": False}
# 后台线程写 names/ts、主线程读 —— 加把锁，免得读到
#「新 names + 旧 ts」这种半更新状态。
# threading 在文件头就 import 了（L6 那行合并 import），不用 __import__。
_VOICE_LIST_LOCK = threading.Lock()
_VOICE_LIST_TTL = 300.0        # 5 分钟过期


def _fetch_voice_names(force: bool = False):
    """取服务端可用音色名集合。失败返回 None（表示未知，不拦）。"""
    import time as _t
    now = _t.time()
    with _VOICE_LIST_LOCK:
        if (not force and _VOICE_LIST_CACHE["names"] is not None
                and now - _VOICE_LIST_CACHE["ts"] < _VOICE_LIST_TTL):
            return _VOICE_LIST_CACHE["names"]
    api = cfg_str("voice_api_url").strip()
    if not api:
        return None
    # /v1/audio/speech -> /v1/audio/voices
    url = api.rstrip("/")
    if url.endswith("/speech"):
        url = url[: -len("/speech")] + "/voices"
    else:
        url = url + "/voices"
    headers = {}
    key = cfg_str("voice_api_key").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    # 这个函数被 pick_existing_voice（同步）从 text_to_voice（async）里调到。
    # 以前直接 urlopen(timeout=15) —— 缓存过期后的第一次合成会把整个
    # 事件循环卡住最多 15 秒。
    # 改成后台线程拉，本次直接返回旧值（没有就是 None，不拦合成）。
    with _VOICE_LIST_LOCK:
        if _VOICE_LIST_CACHE["loading"]:
            return _VOICE_LIST_CACHE["names"]
        _VOICE_LIST_CACHE["loading"] = True

    def _bg():
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            names = {v.get("name", "") for v in (data.get("data") or [])
                     if v.get("name")}
            if names:
                with _VOICE_LIST_LOCK:
                    _VOICE_LIST_CACHE["names"] = names
                    _VOICE_LIST_CACHE["ts"] = time.time()
        except Exception as e:
            log("warn", "语音",
                f"取音色清单失败（不拦截合成）: {type(e).__name__}")
        finally:
            with _VOICE_LIST_LOCK:
                _VOICE_LIST_CACHE["loading"] = False

    try:
        import threading
        threading.Thread(target=_bg, daemon=True).start()
    except Exception:
        with _VOICE_LIST_LOCK:
            _VOICE_LIST_CACHE["loading"] = False
    with _VOICE_LIST_LOCK:
        return _VOICE_LIST_CACHE["names"]


# 没配任何音色名时，兜底用哪个角色。音色名形如「卡提希娅-中文-平静」。
_DEFAULT_VOICE_CHAR = "卡提希娅"


def _voice_char_prefix(voice: str = "") -> str:
    r"""取当前用的角色名前缀（音色名形如「钟离-中文-平静」取「钟离」）。

    为什么要有这个（真实问题）：
      原来 _voice_fallback_chain 里把角色名写死成「卡提希娅」。
      用户把 voice_name 换成别的角色（比如「钟离-中文」）之后，
      fallback 会去试「卡提希娅-中文」—— 而那个音色在服务端**确实存在**，
      于是请求钟离却念出卡提希娅的声音。

    优先取传入的音色名，其次取配置里的 voice_name，都没有才用默认角色。
    """
    for cand in (voice, cfg_str("voice_name").strip()):
        c = (cand or "").strip()
        if not c:
            continue
        head = c.split("-", 1)[0].strip()
        if head:
            return head
    return _DEFAULT_VOICE_CHAR


def _voice_fallback_chain(voice: str, emotion: str, text: str):
    """给出候选音色列表，第一个存在的就用。"""
    chain = [voice] if voice else []
    lang = detect_text_language(text)
    cn = _VOICE_LANG_NAMES.get(lang)
    if cn:
        # 角色前缀跟着实际配置走，不要写死
        pfx = _voice_char_prefix(voice)
        if emotion:
            chain.append(f"{pfx}-{cn}-{emotion}")
        chain.append(f"{pfx}-{cn}")
        chain.append(f"{pfx}-{cn}-平静")
    chain.append(cfg_str("voice_name").strip())
    seen, out = set(), []
    for c in chain:
        c = (c or "").strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def pick_existing_voice(voice: str, emotion: str = "", text: str = "") -> str:
    """从候选里挑第一个服务端确实有的音色。都取不到时返回原值。"""
    cands = _voice_fallback_chain(voice, emotion, text)
    if not cands:
        return voice
    names = _fetch_voice_names()
    if not names:
        return cands[0]                       # 拿不到清单就不改
    for c in cands:
        if c in names:
            if c != cands[0]:
                log("warn", "语音", f"音色 {cands[0]!r} 不存在，退回 {c!r}")
            return c
    log("warn", "语音", f"候选音色都不存在: {cands[:3]}，试第一个")
    return cands[0]


def resolve_emotion_voice(emotion: str, text: str = "") -> str:
    """按情绪 + 文本语言挑音色名。返回 "" 表示用默认音色。"""
    if not emotion:
        return ""
    lang = detect_text_language(text)
    vm = CFG.get("voice_emotion_voices")
    table = None
    if isinstance(vm, dict):
        # 新格式：{语言: {情绪: 音色}}
        if lang in vm and isinstance(vm.get(lang), dict):
            table = vm[lang]
        # 旧格式：{情绪: 音色}（单语言，向后兼容）
        elif emotion in vm and isinstance(vm.get(emotion), str):
            return str(vm[emotion])
    if table is None:
        table = _DEFAULT_LANG_VOICES.get(lang) or {}
    return str(table.get(emotion, "") or "")


def _voice_emotion_payload(emotion: str = "") -> dict:
    """把情感配置转成 IndexTTS 请求参数。

    为什么需要它：
      不传情感参数时，IndexTTS 走 speaker 模式 —— 实测短句上几乎不在标点处换气，
      听起来"一口气念完、不会断句"。传 vector 模式后停顿数翻倍、断句自然。

    emotion 非空时优先用该情绪（来自用户话里的"开心点""带感情哟"这类要求），
    否则用配置里的固定向量。
    向量顺序：喜, 怒, 哀, 惧, 厌恶, 低落, 惊喜, 平静（8 个，0-1）
    """
    mode = str(cfg_str("voice_emotion_mode") or "").strip().lower()
    if mode in ("", "speaker", "none", "off", "关闭"):
        return {}
    if mode not in ("vector", "emotion", "情感"):
        log("warn", "语音", f"voice_emotion_mode={mode!r} 不认识，已按 speaker 处理")
        return {}

    strength = _clamp(cfg_float("voice_emotion_strength", 0.65), 0.0, 1.0)

    # 1) 用户指定了情绪 -> 用该情绪的向量
    if emotion:
        vec = emotion_vector_for(emotion)
        if any(vec):
            log("info", "语音", f"按用户要求使用情绪「{emotion}」")
            return {"emotion_mode": "vector", "emotion_vector": vec,
                    "emotion_strength": strength}

    # 2) 回退到配置里的固定向量
    raw = cfg_list("voice_emotion_vector")
    vec = []
    for x in list(raw)[:8]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            v = 0.0
        vec.append(_clamp(v, 0.0, 1.0))
    while len(vec) < 8:
        vec.append(0.0)
    if not any(vec):
        # 全 0 等于没有情感，某些实现会直接报错，这里退回 speaker
        log("warn", "语音", "情感向量全为 0，已跳过")
        return {}
    return {
        "emotion_mode": "vector",
        "emotion_vector": vec,
        "emotion_strength": strength,
    }


def _admin_prompt_hint(is_super: bool) -> str:
    """给 AI 的权限说明。

    没有这段时，超管让机器人踢人/拉黑，模型会凭"我是 AI 没有权限"的直觉
    回绝（"我没有权限，请找管理员吧"）—— 但用户本人就是管理员。
    模型看不到谁有权限，必须由提示词告诉它。
    """
    cmds = "、".join(ADMIN_CMD_TARGET)
    if is_super:
        return (
            "【当前说话人的权限】\n"
            "这个人就是本机器人的超级管理员（管理员权限在机器人程序里，不在群头衔里）。\n"
            f"他已经可以直接对你说这些指令：{cmds}（后面跟 @某人 或 QQ号）。\n"
            "所以：\n"
            '1. 绝对不要说「我没有权限」「请找管理员」——他本人就是管理员。\n'
            "2. 踢人/拉黑这类操作由程序直接执行，不是你能做的动作。"
            "所以严禁说「好嘞这就踢了」「已经送走了」「安排上了」这类假装已执行的话 —— "
            "如果你这样说了而程序又没接住，就是纯骗人（真实发生过）。\n"
            "3. 收到这类指令时：若消息里带了 @某人 或 QQ号，程序会执行，"
            "你只需极短确认（例如「好」）；若没带目标，就回一句正确写法提示：\n"
            f"   「{cmds.split('、')[0]} @某人」或「{cmds.split('、')[0]} QQ号」\n"
            "4. 你本人没有踢人/禁言能力，不要声称自己能做别的管理操作。"
        )
    return (
        "【当前说话人的权限】\n"
        "这个人不是管理员。管理员专属操作（踢人/拉黑/解除等）他做不了。\n"
        "被要求做这类操作时，礼貌说明只有管理员能用，不要假装去执行、也不要编造结果。"
    )


def _voice_text_clean(text: str) -> str:
    """去掉语音不适合念的内容：颜文字、括号旁白、markdown 符号等。"""
    if not text:
        return ""
    s = str(text)
    # 去掉括号内容（表情/旁白）
    s = re.sub(r'[（(【\[][^）)】\]]{0,30}[）)】\]]', '', s)
    # 去掉常见颜文字片段（连续的日文假名/符号组合，长度很短的话）
    s = re.sub(r'[（(]?[・ω・๑•̀ㅂㅅ｡･ﾟ°˘▽][^。！？\s]{0,20}[）)]?', '', s)
    # 去掉 markdown 强调符
    s = re.sub(r'[*_`~#>]+', '', s)
    # 去掉末尾多余的换行
    s = s.strip()
    max_chars = cfg_int("voice_max_chars", 200)
    if len(s) > max_chars:
        s = s[:max_chars]
    return s


# ==================== 知识库 ====================
_KB_STOP = set("的了是我你他她它们在有和与就都而及或一个这那什么怎么吗呢吧啊哦嗯"
               "请问一下么样为什如何哪里哪个多少时候可以能不能要不要")

# 疑问词/虚词片段：不参与"实体命中"判断，否则"是谁""什么"会把所有条目都命中
_KB_QUESTION_WORDS = {
    "是谁", "是谁呀", "什么", "是什么", "哪个", "哪里", "在哪", "怎么", "怎么样",
    "为什么", "多少", "多久", "什么时候", "介绍", "讲讲", "说说", "一下", "告诉",
    "知道", "谁啊", "啥", "吗", "呢", "啊",
}

_KB_PUNCT_RE = re.compile("[" + re.escape(
    " \t\r\n，。！？、；：\"'“”‘’（）()【】[]{}<>《》"
    "~!?,.;:|/\\-_+=*&#@$%^`"
) + "]+")

# 玩家社区说法 -> 游戏官方说法。检索前归一化，否则「世一冰c」这类
# 提问和知识库里的「冷凝」一个字都对不上，永远检索不到（真实问题）。
# 键值都写在下面这张表里，改这里就够了。
_KB_SYNONYMS = {
    # 属性：社区叫法 -> 官方属性名
    "冰系": "冷凝", "冰属性": "冷凝", "冰元素": "冷凝",
    "冰c": "冷凝", "冰主c": "冷凝", "冰c位": "冷凝",
    "火系": "热熔", "火属性": "热熔", "火元素": "热熔",
    "火c": "热熔", "火主c": "热熔",
    "雷系": "导电", "雷属性": "导电", "雷元素": "导电",
    "雷c": "导电", "雷主c": "导电",
    "风系": "气动", "风属性": "气动", "风元素": "气动",
    "风c": "气动", "风主c": "气动", "风c是谁": "气动", "风队": "气动",
    "光系": "衍射", "光属性": "衍射", "光元素": "衍射",
    "光c": "衍射", "光主c": "衍射",
    "暗系": "湮灭", "暗属性": "湮灭", "暗元素": "湮灭",
    "暗c": "湮灭", "暗主c": "湮灭",
    "奶": "治疗", "辅助": "增益",
    # 社区黑话
    "世一": "最强", "天花板": "最强", "t0": "最强", "t1": "很强",
    "深渊": "逆境深塔", "深塔": "逆境深塔", "逆境深塔": "逆境深塔",
    "抽卡": "卡池", "up池": "卡池", "限定池": "卡池",
    "专武": "专属武器", "命座": "共鸣链", "武器池": "卡池", "大保底": "卡池",
    "站场": "主力输出", "副c": "辅助", "奶妈": "治疗", "亲密度": "共鸣链",
    "破防": "减防", "拐": "辅助", "c位": "主c",
    "大世界": "开放世界", "坐骑": "滑翔",
    "主c": "主力输出",
    "强度榜": "最强", "节奏榜": "最强",
}

# 按长度降序的键，供 _kb_apply_synonyms 做「长键优先」匹配
_KB_SYNONYM_KEYS = tuple(sorted(_KB_SYNONYMS, key=len, reverse=True))

# 检索时没有区分度的通用词（去掉后才靠实体词排序）。
# 注意：不要加进 _KB_STOP —— 那里是抽词阶段用的，加了会影响其它逻辑。
_KB_GENERIC_TERMS = {
    "最强", "很强", "是谁", "是谁呀", "是啥", "什么", "是什么", "哪个", "哪里",
    "怎么", "怎么样", "多少", "多久", "为什么", "介绍", "一下", "知道",
    "强是", "是谁", "什么", "熔最", "属最", "系最",
}


def _kb_apply_synonyms(s: str) -> str:
    """把社区叫法换成官方叫法。

    两个要点：
      1) 长键优先，避免「冰主c」被「冰c」抢先切碎
      2) 替换后跳过已替换的片段，否则「深塔」会把刚换好的「逆境深塔」
         再换一次变成「逆境逆境深塔」（真实 bug）
    """
    if not s:
        return s
    out = []
    i = 0
    n = len(s)
    while i < n:
        for k in _KB_SYNONYM_KEYS:          # 已按长度降序
            if s.startswith(k, i):
                out.append(_KB_SYNONYMS[k])
                i += len(k)
                break
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


# 合成时只在这个打分器范围内生效，不影响其它还在用这些通用词的逻辑。
def _kb_drop_generic(terms: set) -> set:
    """去掉检索里没有区分度的通用词。

    「最强」「是谁」「是什么」这类词会让所有标题相似的条目同时顶到
    同一个分数（实测「火系最强是谁」把冰c、衍射、气动等 8 条全顶到 0.60，
    排序完全失去区分度）。去掉后只剩实体词参与打分。
    """
    return {t for t in terms if t not in _KB_GENERIC_TERMS}


def _kb_norm(text: str) -> str:
    """归一化：去标点空白、转小写、替换同义词。用于中文匹配。"""
    s = _KB_PUNCT_RE.sub("", str(text or "")).lower()
    return _kb_apply_synonyms(s)


def _kb_terms(text: str) -> set:
    """抽词：中文按 2-gram（滤掉纯虚词），英文/数字按单词。

    v2：去掉低信息量的单字，并过滤"都是虚词"的 bigram，
    否则像"岁主是谁"会被"是/谁/主是/是谁"这类噪声拉低有效相似度。
    """
    s = _kb_norm(text)
    out = set()
    # 英文/数字词
    for w in re.findall(r'[a-z0-9]{2,}', s):
        out.add(w)
    # 中文 2-gram：两端至少一边是有信息量的字
    han = "".join(ch for ch in s if '\u4e00' <= ch <= '\u9fff')
    for i in range(len(han) - 1):
        bg = han[i:i + 2]
        if bg[0] in _KB_STOP and bg[1] in _KB_STOP:
            continue
        out.add(bg)
    # 去掉「最强」「是谁」这类通用词：否则所有标题相似的条目都顶到同一分
    return _kb_drop_generic(out)


def _kb_score(terms_q: set, q_norm: str, kb: dict) -> float:
    """给一条知识打分（0-1）。

    分层设计，避免"所有条目都顶到同一个分数、排序没有区分度"：
      1) 查询整体出现在问题里 / 问题整体出现在查询里        -> 0.75 起
      2) 标签精确命中                                      -> 0.70
      3) 问题字段的 Dice 相似度（主信号，权重高）          -> 0.55-0.65
      4) 答案字段的 Dice 相似度（弱信号，权重低）          -> 0.40 以下
      5) 纯字符覆盖率（兜底，必须够高才有意义）            -> 0.30 以下
    """
    q_text = kb.get("question") or ""
    tags = kb.get("tags") or ""
    ans = kb.get("answer") or ""

    kb_norm = _kb_norm(q_text)
    terms_k = _kb_terms(q_text)
    terms_ans = _kb_terms(ans)

    def dice(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        return 2 * len(a & b) / (len(a) + len(b))

    q_in_kb = bool(q_norm) and q_norm in kb_norm
    kb_in_q = bool(kb_norm) and kb_norm in q_norm

    # 1) 包含关系：最强信号
    base = 0.78 if q_in_kb else 0.0
    if kb_in_q:
        base = max(base, 0.72)
    # 注意：不要用"查询前 N 字是否出现"这种规则 —— 本库所有标题都以"鸣潮"开头，
    # 查询前 4 字"鸣潮35"会命中每一条，导致全部顶到同一分数、排序失去区分度。

    # 2) 标签精确命中。要求标签本身够长（>=3 字）：
    #    否则像"鸣潮"这种所有条目都带的通用词会让全部条目并列最高分。
    if tags:
        for t in re.split(r'[,，;；\s]+', tags):
            t = t.strip().lower()
            if len(t) >= 3 and t in q_norm:
                base = max(base, 0.72)
                break

    # 3) 实体命中：查询里的实词是否有哪一个"出现在"问题/标签/答案里。
    #    这一档是主力 —— 短名字（"秧秧""今汐"）在长标题里用 Dice 会被严重稀释，
    #    但"名字出现在正文里"是可靠的强证据。
    q_terms = [t for t in terms_q if t not in _KB_QUESTION_WORDS]
    if q_terms:
        if any(t in kb_norm for t in q_terms):
            base = max(base, 0.60)
        else:
            tag_norm = _kb_norm(tags)
            if tag_norm and any(t in tag_norm for t in q_terms):
                base = max(base, 0.62)
            elif terms_ans and any(t in _kb_norm(ans) for t in q_terms):
                base = max(base, 0.42)

    # 4) 问题字段相似度（辅助信号，用平方放大区分度）
    d_q = dice(terms_q, terms_k)
    if d_q > 0:
        base = max(base, 0.36 + d_q ** 0.5 * 0.42)

    # 5) 答案字段相似度（弱信号）
    if d_q < 0.25:
        d_a = dice(terms_q, terms_ans)
        if d_a > 0:
            base = max(base, 0.14 + d_a ** 0.5 * 0.20)

    # 6) 纯字符覆盖兜底：要求覆盖率高，且给的分很低
    if q_norm and kb_norm and base < 0.30:
        inter = len(set(q_norm) & set(kb_norm))
        cover_q = inter / max(len(set(q_norm)), 1)
        cover_k = inter / max(len(set(kb_norm)), 1)
        if cover_q >= 0.8:
            base = max(base, 0.24 + cover_k * 0.12)

    return round(min(1.0, base), 4)


def kb_search(group_id, text, limit=None):
    """返回 [(score, 条目)]，按分数降序。"""
    if not cfg_bool("kb_enabled", True):
        return []
    text = (text or "").strip()
    if not text:
        return []
    limit = limit or cfg_int("kb_inject_count", 3)
    q_norm = _kb_norm(text)
    terms_q = _kb_terms(text)
    if not terms_q and not q_norm:
        return []
    try:
        rows = DB.kb_list(group_id, limit=2000)
    except Exception as e:
        log("warn", "知识库", f"读取失败: {type(e).__name__}: {e}")
        return []
    scored = []
    for row in rows:
        s = _kb_score(terms_q, q_norm, row)
        if s > 0:
            scored.append((s, row))
    scored.sort(key=lambda x: (-x[0], -(x[1].get("hits") or 0), -x[1]["id"]))
    thr = cfg_float("kb_match_threshold", 0.5)
    return [x for x in scored[:limit] if x[0] >= thr]


def kb_format_hits(hits, with_score=False):
    """把命中条目排版成可读文本（群命令回复用）。"""
    lines = []
    for i, (score, row) in enumerate(hits, 1):
        prefix = f"{i}. " if len(hits) > 1 else ""
        sc = f"（匹配 {score*100:.0f}%）" if with_score else ""
        lines.append(f"{prefix}【{row['question']}】{sc}\n{row['answer']}")
    return "\n".join(lines)


def kb_reference_block(hits):
    """把命中条目排版成注入 AI 提示词的参考资料。"""
    if not hits:
        return ""
    parts = []
    for score, row in hits:
        parts.append(f"- 问：{row['question']}\n  答：{row['answer']}")
    return ("【群知识库（由群友整理，可信度较高；请优先依据它回答，"
            "不要编造它没提到的细节）】\n" + "\n".join(parts))


# ==================== 知识库分类 ====================
# 两级标签：游戏（鸣潮/原神…） + 分类（角色图鉴/主线剧情…）
# 分类按"你会怎么找它"设计，不是按剧情章节。

# ---------- 第一级：游戏 ----------
KB_GAMES = ["鸣潮"]
# 判定某个条目属于哪个游戏。除了游戏名本身，也认它独有的地名/专有名词，
# 否则"心月狐是谁"这种不含"鸣潮"三个字的条目会掉进未分类。
KB_GAME_HINTS = {
    "鸣潮": (
        # 游戏本体与专有名词
        "鸣潮", "瑝珑", "今州", "黑海岸", "黎那汐塔", "拉海洛", "梦州", "玄方",
        "七丘", "拉古那", "穗波", "星炬学院", "岁主", "鸣式", "声骸", "残星会",
        "漂泊者", "御者", "共鸣者", "泰缇斯", "无相燹主", "文明之匣", "归字枢",
        "今州城", "夜归军", "华胥研究院", "深空联合", "愚人剧团", "莫塔里",
        "翡萨烈", "隐海修会", "边庭", "虚诞虫", "隧者",
        # 角色名（很多条目只写角色名不含"鸣潮"，不列出来会掉进"通用"）
        "忌炎", "吟霖", "安可", "凌阳", "鉴心", "维里奈", "卡卡罗", "今汐",
        "折枝", "相里要", "守岸人", "椿", "珂莱塔", "洛可可", "菲比", "布兰特",
        "坎特蕾拉", "赞妮", "夏空", "卡提希娅", "露帕", "弗洛洛", "奥古斯塔",
        "尤诺", "嘉贝莉娜", "仇远", "千咲", "琳奈", "莫宁", "爱弥斯", "陆·赫斯",
        "西格莉卡", "绯雪", "达妮娅", "洛瑟菈", "秧秧", "穗穗", "清宵", "景燃",
        "锁暝", "炽霞", "白芷", "莫特斐", "散华", "桃祈", "渊武", "丹瑾",
        "秋水", "釉瑚", "灯灯", "卜灵", "长离", "伤痕", "忌焰", "心月狐",
        "英白拉多", "普罗米厄", "亢金龙", "氐土貉", "房日兔", "尾火虎", "箕水豹",
        "漂泊", "镇海", "无妄者", "蜜芽", "芬莱克", "罗蕾莱", "涅索", "阿维狄亚",
        "莉莉贝", "洛瑟菈", "辛吉勒姆", "西格莉卡", "马小芳", "椋羽", "木禺", "蛇菰",
    ),
}
KB_GAME_NONE = "通用"          # 认不出游戏时的标签

KB_CATEGORY_ORDER = [
    "主线剧情", "角色图鉴", "岁主与神明", "地区势力",
    "属性与武器", "版本与卡池", "世界观设定", "其他",
]
KB_CATEGORY = "其他"        # 兜底分类名


def kb_game(question: str, tags=None) -> str:
    """判断条目属于哪个游戏。认不出返回 KB_GAME_NONE。"""
    text = str(question or "")
    lst = kb_clean_tags(tags)
    if lst:
        text += " " + " ".join(lst)
    for g in KB_GAMES:
        if g in text:
            return g
        for hint in KB_GAME_HINTS.get(g, ()):
            if hint in text:
                return g
    return KB_GAME_NONE

# 世界里实体/组织相关词的指纹，用来把"设定类"问句认出来
_KB_LORE_HINTS = (
    "岁主", "共鸣者", "权能", "能力", "属性", "是什么", "为什么",
    "本体", "身份", "隧者", "归字枢",
)
# 属性名与武器类型
_KB_ELEMENT_WORDS = ("热熔", "冷凝", "气动", "导电", "衍射", "湮灭")
_KB_WEAPON_WORDS = ("迅刀", "长刃", "佩枪", "臂铠", "音感仪")
# 地区 / 势力名
_KB_REGION_WORDS = (
    "瑝珑", "今州", "黑海岸", "黎那汐塔", "拉海洛", "梦州", "玄方",
    "七丘", "拉古那", "穗波", "星炬学院", "深穹", "天城",
)
_KB_FACTION_WORDS = (
    "残星会", "愚人剧团", "莫塔里家族", "翡萨烈家族", "隐海修会",
    "深空联合", "华胥研究所", "夜归军", "边庭", "修会", "家族", "剧团",
)
_KB_LORE_ENTITIES = (
    "遂者", "隧者", "普罗米厄", "亢金龙", "氐土貉", "房日兔", "尾火虎",
    "箕水豹", "漂泊者", "文明之匣", "泰缇斯", "御者",
)

_KB_CAT_RE_VERSION = re.compile(r'\d+\.\d+\s*版本')


def kb_clean_tags(tags) -> list:
    """把任意形式的 tags 归成去重后的列表（支持逗号/顿号/空格分隔）。"""
    if tags is None:
        return []
    if isinstance(tags, (list, tuple, set)):
        raw = []
        for x in tags:
            raw.extend(re.split(r'[,，、;；\s]+', str(x)))
    else:
        raw = re.split(r'[,，、;；\s]+', str(tags))
    out = []
    for t in raw:
        t = t.strip()
        if t and t not in out:
            out.append(t)
    return out


def kb_classify(question: str) -> str:
    """按问题内容猜分类。用于新条目自动打标和批量重分类。

    顺序 = 优先级，"主线剧情"必须最先判断（最具体、最容易误伤）。
    """
    q = str(question or "")

    # 0) 版本/卡池类优先于剧情：像"鸣潮3.5版本剧情讲了什么"应该归卡池
    #    如果放后面判断会被"剧情"两个字先吃掉。
    #
    #    补充：以前这里只认"角色/五星/卡池/池/抽"，而注释举的例子
    #    "鸣潮3.5版本剧情讲了什么"一个都不沾 —— 于是它落到下面的
    #    "剧情"分支，注释描述的行为根本没发生。把"剧情"加进来对齐。
    if ("版本" in q or "卡池" in q or "周年庆" in q) and \
            ("角色" in q or "五星" in q or "卡池" in q or "池" in q
             or "抽" in q or "剧情" in q):
        return "版本与卡池"

    # 1) 主线剧情（最具体，先判）
    if "剧情" in q or "主线" in q:
        return "主线剧情"

    # 2) 版本与卡池（补充：只提版本没提角色也归这里）
    if ("版本" in q and ("角色" in q or "五星" in q or "卡池" in q)) or _KB_CAT_RE_VERSION.search(q):
        return "版本与卡池"

    # 3) 角色图鉴：单角色介绍
    if re.search(r'是什么角色|是谁|什么强度|值得练|怎么获得|怎么打', q):
        # 但"XX的岁主是谁"这类属于设定，不算角色图鉴
        if not any(h in q for h in ("岁主", "共鸣者", "权能", "本体", "身份")):
            return "角色图鉴"

    # 4) 属性与武器：必须有具体的属性名/武器类型，
    #    不能只因为出现"属性"两个字就算（"心是什么属性"是在问角色属性）。
    if any(w in q for w in _KB_ELEMENT_WORDS) or any(w in q for w in _KB_WEAPON_WORDS):
        return "属性与武器"

    # 5) 岁主与神明（必须在"是什么"兜底之前：像"鸣潮岁主是什么"含明确的岁主词）
    if any(w in q for w in ("岁主", "共鸣者", "权能", "双神", "真神", "心月狐", "英白拉多")):
        return "岁主与神明"

    # 6) 世界观设定：具体专有名词
    if any(w in q for w in _KB_LORE_ENTITIES):
        return "世界观设定"

    # 6b) 问"属性/能力/本体/身份"但没提"地区/地方"时，多半在问设定而不是地点。
    #     例："为什么漂泊者在拉海洛没有新属性"含地名，但问的是属性。
    if any(w in q for w in ("属性", "能力", "本体", "身份")) and "地区" not in q \
            and "地方" not in q:
        return "世界观设定"

    # 7) 地区势力（"是什么地方"也算）
    if "地区" in q or "地方" in q or "有哪些角色" in q \
            or any(f in q for f in _KB_FACTION_WORDS) or any(r in q for r in _KB_REGION_WORDS):
        return "地区势力"

    # 8) 最后才用通用问法兜底
    if any(w in q for w in ("是什么", "为什么")):
        return "世界观设定"

    return KB_CATEGORY


def kb_normalize_tags(tags, question=None) -> list:
    """规整标签：游戏标签 + 分类标签 + 其它标签，去重且顺序固定。

    结构：[游戏, 分类, 其余标签...]
    只靠标签判不出分类（"鸣潮,剧情,主线"这三个词自身不含分类信息），
    所以 question 传入时会按问题内容补一个。
    """
    lst = kb_clean_tags(tags)
    # 只去掉真正的噪声词；游戏名保留（它现在是第一级分类）
    drop = {"剧情", "主线", "知识", "其他"}
    kept = [t for t in lst if t not in drop]

    game = kb_game(question, kept) if question else \
        ([t for t in kept if t in KB_GAMES + [KB_GAME_NONE]] or [KB_GAME_NONE])[0]
    cats = [t for t in kept if t in KB_CATEGORY_ORDER]
    if not cats:
        cats = [kb_classify(question) if question else KB_CATEGORY]
    # 其余标签（既不是游戏也不是分类的，比如具体角色名）
    other = [t for t in kept
             if t not in KB_GAMES and t != KB_GAME_NONE and t not in KB_CATEGORY_ORDER]
    # 去重且保持 [游戏, 分类, ...] 顺序
    out = []
    for x in [game] + cats + other:
        if x and x not in out:
            out.append(x)
    return out


def kb_reclassify(group_id=None) -> dict:
    """重新打标签（游戏 + 分类，保留具体名词标签）。返回统计。"""
    rows = DB.kb_list(group_id, limit=100000)
    changed, stat, games = 0, {}, {}
    for row in rows:
        cat = kb_classify(row["question"])
        game = kb_game(row["question"], row.get("tags"))
        new_tags = kb_normalize_tags([game, cat] + kb_clean_tags(row.get("tags")),
                                     row["question"])
        new_s = ",".join(new_tags)
        if new_s != (row.get("tags") or ""):
            DB.kb_update(row["id"], tags=new_s)
            changed += 1
        stat[cat] = stat.get(cat, 0) + 1
        games[game] = games.get(game, 0) + 1
    return {"total": len(rows), "changed": changed,
            "categories": stat, "games": games}


def kb_category_counts(group_id=None, game=None) -> list:
    """返回 [(分类, 条数)]，按预设顺序排列。game 传入时只统计该游戏。"""
    rows = DB.kb_list(group_id, limit=100000)
    if game:
        rows = [r for r in rows if kb_game(r["question"], r.get("tags")) == game]
    cnt = {}
    for row in rows:
        cats = [t for t in kb_clean_tags(row.get("tags")) if t in KB_CATEGORY_ORDER]
        cat = cats[0] if cats else KB_CATEGORY
        cnt[cat] = cnt.get(cat, 0) + 1
    out = [(c, cnt[c]) for c in KB_CATEGORY_ORDER if cnt.get(c)]
    for c, n in cnt.items():
        if c not in KB_CATEGORY_ORDER:
            out.append((c, n))
    return out


def kb_game_counts(group_id=None) -> list:
    """返回 [(游戏, 条数)]，鸣潮优先，通用放最后。"""
    rows = DB.kb_list(group_id, limit=100000)
    cnt = {}
    for row in rows:
        g = kb_game(row["question"], row.get("tags"))
        cnt[g] = cnt.get(g, 0) + 1
    out = [(g, cnt[g]) for g in KB_GAMES if cnt.get(g)]
    for g, n in cnt.items():
        if g not in KB_GAMES and g != KB_GAME_NONE:
            out.append((g, n))
    if cnt.get(KB_GAME_NONE):
        out.append((KB_GAME_NONE, cnt[KB_GAME_NONE]))
    return out


def kb_add_entry(group_id, question, answer, tags="", uid=0, uname="", folder=""):
    """新增条目。返回 (ok, 结果或错误信息)。

    tags 留空时自动分类（kb_classify），省得手动整理。
    """
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question or not answer:
        return False, "问题和答案都不能为空"
    if len(question) > 200:
        return False, "问题太长了（最多 200 字）"
    max_ans = cfg_int("kb_answer_max_chars", 500)
    if len(answer) > max_ans:
        return False, f"答案太长了（最多 {max_ans} 字）"
    tag_list = kb_normalize_tags(tags, question)
    if not tag_list:
        tag_list = [kb_classify(question)]
    tags = ",".join(tag_list)
    cap = cfg_int("kb_max_per_group", 500)
    try:
        if DB.kb_count(int(group_id)) >= cap:
            return False, f"本群知识库已达上限（{cap} 条），请先清理"
    except Exception:
        pass
    try:
        kid = DB.kb_add(group_id, question, answer, tags, uid, uname, folder=folder)
    except Exception as e:
        return False, f"写入失败：{type(e).__name__}: {e}"
    log("success", "知识库", f"新增 #{kid} @群{group_id}：{question[:30]}")
    return True, {"id": kid, "question": question, "answer": answer,
                  "tags": tags, "folder": folder or ""}


def parse_kb_add(text: str):
    """解析「补充知识 问题 | 答案」或「补充知识 问：xx 答：yy」等写法。

    支持：
      补充知识 岁主是谁 | 磐古
      补充知识 问：岁主是谁  答：磐古
      补充知识 岁主是谁=磐古
    返回 (question, answer) 或 (None, None)
    """
    s = (text or "").strip()
    # 去掉命令词
    for kw in cfg_list("kb_add_keywords"):
        if kw and s.startswith(kw):
            s = s[len(kw):].strip()
            break
    if not s:
        return None, None
    # 形式一：问：xx 答：yy
    m = re.search(r'问\s*[:：]\s*(.+?)\s*答\s*[:：]\s*(.+)', s, re.S)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # 形式二：分隔符
    for sep in ("|", "｜", "=>", "→", "=", "："):
        if sep in s:
            q, a = s.split(sep, 1)
            q, a = q.strip(), a.strip()
            if q and a:
                return q, a
    # 形式三：换行分隔
    lines = [x.strip() for x in s.splitlines() if x.strip()]
    if len(lines) >= 2:
        return lines[0], "\n".join(lines[1:])
    return None, None


async def text_to_voice(text: str, emotion: str = "") -> Optional[str]:
    """调 IndexTTS API 生成语音，返回 MP3 临时文件路径。失败返回 None。

    emotion 非空时按该情绪合成（来自用户"开心点""带感情哟"这类要求）。
    """
    if not cfg_bool("voice_enabled", False):
        return None
    api_url = cfg_str("voice_api_url").strip()
    if not api_url:
        return None
    clean = _voice_text_clean(text)
    if not clean:
        return None
    voice_name = cfg_str("voice_name").strip()
    # 有情绪且配了对应的情绪音色时，换成那个音色。
    # 那些音色是 reference_audio 模式，情感参考音频写在音色档案里，
    # 所以这里不需要再传 emotion_vector（传了也会被服务端忽略）。
    emo_voice = resolve_emotion_voice(emotion, clean) if emotion else ""
    if emo_voice:
        _lg = detect_text_language(clean)
        log("block", "语音",
            f"情绪「{emotion}」+ 语言 {_lg} -> 音色 {emo_voice}")
        voice_name = emo_voice
    # 兜底：映射表可能指向已被删除的音色，直接发会 400
    voice_name = pick_existing_voice(voice_name, emotion, clean)
    if not voice_name:
        log("warn", "语音", "未配置 voice_name，跳过")
        return None

    api_key = cfg_str("voice_api_key").strip()
    speed = cfg_float("voice_speed", 1.0)
    timeout = cfg_float("voice_timeout", 90)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": "tts-1",
        "input": clean,
        "voice": voice_name,
        "response_format": "mp3",
        "speed": speed,
    }
    # 情感控制：不传的话 IndexTTS 走 speaker 模式，短句几乎不在标点处换气
    # 情绪音色自带情感参考音频，不要再传向量（会互相干扰）
    if not emo_voice:
        _emo = _voice_emotion_payload(emotion)
        if _emo:
            payload.update(_emo)

    # 音频后处理：主要是去刺音（deharsh）/ 变温暖（warm）
    _pp = str(cfg_str("voice_postprocess_preset") or "").strip().lower()
    if _pp and _pp != "off":
        payload["postprocess_preset"] = _pp
        payload["postprocess_strength"] = _clamp(
            cfg_float("voice_postprocess_strength", 1.0), 0.0, 1.0)

    tmp_path = None
    t0 = time.time()
    try:
        client = get_http()
        r = await client.post(api_url, json=payload, headers=headers, timeout=timeout)
        if r.status_code >= 400:
            # 502/504 通常是"中间有代理或网关"，把响应头一起打出来才好判断来源
            hdrs = "、".join(f"{k}={v}" for k, v in list(r.headers.items())[:6])
            log("error", "语音",
                f"HTTP {r.status_code}（耗时 {time.time() - t0:.1f}s）"
                f"｜地址 {api_url}｜音色 {voice_name}｜Key {'已设置' if api_key else '未设置'}"
                f"｜响应头 {hdrs or '无'}｜响应体 {r.text[:300]!r}")
            return None
        content = r.content
        if not content:
            log("warn", "语音", "返回内容为空")
            return None
        # 落到 voice_tmp 目录，方便定时清理
        fname = f"v-{int(time.time() * 1000)}-{random.randint(1000, 9999)}.mp3"
        tmp_path = os.path.join(VOICE_TMP_DIR, fname)
        with open(tmp_path, "wb") as f:
            f.write(content)
        log("success", "语音", f"生成 {len(content)//1024}KB（{len(clean)}字，耗时 {time.time()-t0:.1f}s）")
        return tmp_path
    except Exception as e:
        log("error", "语音",
            f"生成失败（耗时 {time.time() - t0:.1f}s）{type(e).__name__}: {e}"
            f"｜地址 {api_url}｜音色 {voice_name}")
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return None


def _drop_voice_file(path):
    if not path:
        return
    try:
        if os.path.exists(path):
            os.unlink(path)
    except Exception:
        pass


async def try_send_voice(bot, event, reply_text):
    """尝试发语音。

    返回 (是否成功, 实际念出去的文本)。
    第二个值必须回传给调用方写进对话历史 —— 否则历史里存的是"模型原本写的
    那句否认话"（如"人家只会打字"），模型下一轮看到自己说过这话，会更加确信
    自己不会发语音，形成自我强化的死循环（真实 bug）。

    **不管发不发得出去，第二个值都是剥掉 [情绪:xx] 标签的文本。**
    以前三条失败路径都直接 `return False, reply_text`（原样带回标签），
    结果上下文、记忆流水、聊天记录里存的都是带标签的版本，
    只有真正发到群里的文字被剥了 —— 记录和实际看到的对不上，排查时会困惑。
    """
    # 标签先剥出来：发语音要用 ai_emo 选音色，不发语音也要靠它把文本弄干净
    cleaned_reply, ai_emo = parse_emotion_tag(reply_text)
    user_text = plaintext_with_at(event, skip_self_id=getattr(event, "self_id", None))
    if not should_send_voice(user_text):
        return False, cleaned_reply
    if ai_emo:
        log("block", "语音", f"AI 自标情绪「{ai_emo}」")
    # 优先级：AI 自标 > 用户点名 > 回复关键词 > 默认音色
    emotion = detect_reply_emotion(cleaned_reply, ai_emo, user_text)
    # 清洗与事实矛盾的话（"我发不出语音"会被念出去，很荒唐）
    spoken = strip_voice_denial(cleaned_reply)
    vpath = await text_to_voice(spoken, emotion=emotion)
    if not vpath:
        return False, cleaned_reply
    try:
        # NapCat 支持 file:/// 本地路径
        seg = MessageSegment.record(f"file:///{vpath.replace(os.sep, '/')}")
        await bot.send(event, seg)
        return True, spoken
    except Exception as e:
        log("error", "语音", f"发送失败: {type(e).__name__}: {e}")
        return False, cleaned_reply
    finally:
        # 延迟一点再删，避免 NapCat 还来不及读就删了
        async def _later_del(p, delay=15):
            try:
                await asyncio.sleep(delay)
            except Exception:
                pass
            _drop_voice_file(p)
        spawn_bg(_later_del(vpath), "语音临时文件清理")


async def startup_ai_check():
    await asyncio.sleep(8)
    if not AI.enabled:
        return
    try:
        r = await asyncio.wait_for(
            AI.raw([{"role": "user", "content": "hi"}], timeout=30, use_gap=False),
            timeout=45.0
        )
        if r:
            log("success", "AI", f"{AI.backend} 连接正常（{AI.model}）")
        else:
            log("error", "AI", f"{AI.backend} 无响应（{AI.host}）")
            await _notify_admins(f"⚠️ AI 后端「{AI.backend}」无响应，请检查 {AI.host}")
    except asyncio.TimeoutError:
        log("error", "AI", "启动自检超时")
        await _notify_admins(f"⚠️ AI 后端「{AI.backend}」启动自检超时，请检查服务是否在跑。")
    except Exception as e:
        log("error", "AI", f"连接检查失败: {type(e).__name__}: {e}")
        await _notify_admins(f"⚠️ AI 连接失败：{type(e).__name__}")


async def startup_vision_check():
    await asyncio.sleep(12)
    if not cfg_bool("vision_enabled", False):
        return
    if not cfg_str("vision_api_key").strip():
        log("warn", "识图", "已开启但未填 API Key，请去管理界面配置")
        return
    test_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    test_url = f"data:image/png;base64,{test_b64}"
    try:
        host = cfg_str("vision_api_host").rstrip("/")
        model = cfg_str("vision_model", "glm-4v-flash")
        api_key = cfg_str("vision_api_key").strip()
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": test_url}},
                {"type": "text", "text": "测试"},
            ]}],
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        r = await get_http().post(f"{host}/chat/completions", json=payload,
                                  headers=headers, timeout=120.0)
        if r.status_code < 400:
            log("success", "识图", f"视觉模型连接正常（{model}）")
        else:
            log("error", "识图", f"视觉模型返回 {r.status_code}: {r.text[:200]}")
            await _notify_admins(f"⚠️ 视觉模型连接失败 HTTP {r.status_code}，请检查 API Key。")
    except Exception as e:
        log("error", "识图", f"视觉模型自检失败: {type(e).__name__}: {e}")


async def startup_chatlog_prune():
    """启动时删掉过期的聊天记录，防止库无限膨胀。"""
    await asyncio.sleep(20)
    if not cfg_bool("chatlog_enabled", True):
        return
    days = cfg_int("chatlog_keep_days", 30)
    try:
        total = DB.log_count()
        n = DB.log_prune(days)
        if n:
            log("info", "聊天记录", f"清理 {days} 天前的记录 {n} 条（原有 {total} 条）")
        elif total:
            log("info", "聊天记录", f"共 {total} 条，无需清理（保留 {days} 天）")
    except Exception as e:
        log("warn", "聊天记录", f"清理失败: {type(e).__name__}: {e}")


async def startup_voice_check():
    """启动后探测一次 IndexTTS 服务是否在线，避免第一条语音等到天荒地老。"""
    await asyncio.sleep(15)
    if not cfg_bool("voice_enabled", False):
        return
    if not cfg_str("voice_api_url").strip():
        log("warn", "语音", "已开启但未填 API 地址，请去管理界面配置")
        return
    if not cfg_str("voice_name").strip():
        log("warn", "语音", "已开启但未填音色名称，请去管理界面配置")
        return
    log("info", "语音", f"测试 IndexTTS 连接（{cfg_str('voice_api_url')}）...")
    vpath = await text_to_voice("你好，我是你的语音助手。")
    if vpath:
        size_kb = os.path.getsize(vpath) // 1024
        log("success", "语音", f"IndexTTS 连接正常（测试音频 {size_kb}KB）")
        _drop_voice_file(vpath)
    else:
        log("error", "语音", "IndexTTS 无响应，语音功能将不可用")
        await _notify_admins("⚠️ IndexTTS 语音服务未响应，请检查 API 是否启动。")


async def _notify_admins(msg):
    bot = get_any_bot()
    if not bot:
        return
    for admin in SUPERUSERS:
        try:
            await bot.send_private_msg(user_id=int(admin), message=msg)
        except Exception as e:
            log("warn", "通知", f"私聊超管 {admin} 失败: {type(e).__name__}")


# 后台任务强引用，防止被垃圾回收提前取消
_BG_TASKS = set()


def spawn_bg(coro, name=""):
    """把协程挂到当前事件循环上并保存引用。必须在有事件循环的线程里调用。"""
    try:
        task = asyncio.ensure_future(coro)
    except RuntimeError:
        log("warn", "任务", f"没有可用事件循环，{name or coro} 未启动")
        try:
            coro.close()
        except Exception:
            pass
        return None
    _BG_TASKS.add(task)
    task.add_done_callback(_on_bg_done)
    return task


def _on_bg_done(task):
    _BG_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log("error", "任务", f"后台任务异常: {type(exc).__name__}: {exc}")


def print_banner():
    try:
        token = ADMIN_TOKEN
        masked = (token[:4] + "*" * max(0, len(token) - 4)) if len(token) > 4 else "***"
        superusers = ", ".join(sorted(SUPERUSERS)) or "（未设置）"
        ai_on = "开启" if cfg_bool("ai_enabled", True) else "关闭"
        backend = AI.backend
        backend_label = "本地 Ollama" if backend == "ollama" else "云端 API"
        host = AI.host
        model = AI.model
        gap_min = cfg_float("ai_min_gap", 2.0)
        gap_max = cfg_float("ai_max_gap", 5.0)
        verify_on = "开启" if cfg_bool("verify_enabled", True) else "关闭"
        backup_on = "开启" if cfg_bool("backup_enabled", True) else "关闭"
        vision_on = "开启" if cfg_bool("vision_enabled", False) else "关闭"
        v_model = cfg_str("vision_model")
        voice_on = "开启" if cfg_bool("voice_enabled", False) else "关闭"
        voice_name = cfg_str("voice_name") or "（未设置音色）"
        line = "=" * 60
        print(line, flush=True)
        print("  Nekolyra —— QQ 群管 AI 助手", flush=True)
        print(line, flush=True)
        print(f"  管理界面    http://127.0.0.1:{PORT}/admin?token={masked}", flush=True)
        print(f"  监听端口    {PORT}", flush=True)
        print(f"  AI 对话     {ai_on}", flush=True)
        print(f"  AI 后端     {backend_label}", flush=True)
        print(f"  AI 地址     {host}", flush=True)
        print(f"  AI 模型     {model}", flush=True)
        print(f"  请求间隔    {gap_min:g}~{gap_max:g} 秒", flush=True)
        print(f"  超管 QQ     {superusers}", flush=True)
        print(f"  入群验证    {verify_on}", flush=True)
        print(f"  数据库备份  {backup_on}", flush=True)
        print(f"  视觉识图    {vision_on}（{v_model}）", flush=True)
        print(f"  语音合成    {voice_on}（{voice_name}）", flush=True)
        print(line, flush=True)
        print("  正在等待 NapCat 连接…", flush=True)
        print(line, flush=True)
        print("", flush=True)
    except Exception:
        pass


@driver.on_bot_connect
async def _on_bot_connect(bot):
    print(f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] ✓ 连接 | 机器人 {bot.self_id} 上线", flush=True)
    if not getattr(_on_bot_connect, "_tasks_started", False):
        _on_bot_connect._tasks_started = True
        spawn_bg(daily_backup_task(), "每日备份")
        spawn_bg(startup_ai_check(), "AI自检")
        spawn_bg(startup_vision_check(), "识图自检")
        spawn_bg(startup_voice_check(), "语音自检")
        spawn_bg(startup_chatlog_prune(), "聊天记录清理")


@driver.on_bot_disconnect
async def _on_bot_disconnect(bot):
    print(f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] ✗ 断开 | 机器人 {bot.self_id} 掉线", flush=True)


class LogBuffer:
    def __init__(self, maxlen=1000):
        self.buf = deque(maxlen=maxlen)
        self.seq = 0
        self._lock = threading.Lock()

    def add(self, level, module, msg):
        with self._lock:
            self.seq += 1
            self.buf.append({"seq": self.seq,
                             "time": datetime.now().strftime("%m-%d %H:%M:%S"),
                             "level": level, "module": module, "msg": str(msg)})

    def since(self, seq):
        with self._lock:
            return [x for x in self.buf if x["seq"] > seq]


LOG_BUF = LogBuffer()

TERMINAL_SILENT_MODULES = {"收到"}

ALIAS_LEVELS = {
    "msg": "msg", "reply": "reply", "mem": "mem",
    "join": "join", "leave": "leave", "verify": "verify",
    "block": "block", "req": "req",
}
LEVEL_ICONS = {
    "info":    "·",
    "success": "✓",
    "warn":    "!",
    "error":   "✗",
    "msg":     "收",
    "reply":   "发",
    "mem":     "记",
    "join":    "入",
    "leave":   "退",
    "verify":  "验",
    "block":   "拦",
    "req":     "申",
}


def log(level, module, msg):
    level = str(level)
    if level not in LEVEL_ICONS and module in ALIAS_LEVELS:
        level, module = module, level
    LOG_BUF.add(level, module, msg)
    log_startup(f"[{module}] {msg}")

    if module in TERMINAL_SILENT_MODULES:
        return

    ts = datetime.now().strftime("%m-%d %H:%M:%S")
    icon = LEVEL_ICONS.get(level, "·")
    try:
        print(f"[{ts}] {icon} [{module}] {msg}", flush=True)
    except Exception:
        pass




# ============================================================
#  插件系统 —— 阶段 1：框架
# ============================================================
#  插件目录：<程序目录>/data/plugins/<插件名>/
#      metadata.json    元信息（名称/版本/作者/入口/描述）
#      main.py          入口，里面定义 setup(ctx)
#
#  插件入口只依赖 setup(ctx) 这个约定，不需要 import 宿主任何东西 ——
#  这样打包成 exe 之后也能正常加载（冻结环境里 import 宿主模块很容易出问题）。
#
#  ctx 提供：
#     命令注册 ctx.command("关键词", "说明")
#     消息钩子 ctx.on_message()
#     配置声明 ctx.register_config(标题, [[键, 标签, 类型], ...])
#     页面声明 ctx.register_page("id", "标题", html)
#     接口声明 ctx.register_api("/admin/api/xxx", handler)
#     日志     ctx.log() / ctx.warn()
#     配置读写 ctx.cfg(键, 默认值) / ctx.set_cfg(键, 值)
#     发消息   await ctx.send_group(gid, 文本) / await ctx.send_private(uid, 文本)
#     数据库   ctx.db()
# ============================================================

PLUGIN_DIR = os.path.join(BASE_DIR, "data", "plugins")
PLUGIN_STATE_PATH = os.path.join(BASE_DIR, "data", "plugins.json")

PLUGINS = {}                       # name -> PluginContext（含加载失败的）
_PLUGIN_STATE = {"disabled": [], "installed": {}}

# 插件能改的配置键会临时加进白名单，让管理界面保存时不拒绝
_PLUGIN_KEYS = set()


def _check_handler_sig(fn, what, nargs=2, argnames="event, cmd"):
    """检查处理函数收不收得下 nargs 个位置参数。

    为什么要在注册时就查：不查的话，参数写错要等到群里发命令才在日志里
    冒一句 TypeError，插件作者多半看不到 —— 表现就是"命令没反应"。
    这里直接抛，setup() 会失败、卡片变红、错误就写在卡片上。

    返回空串表示没问题（查不出来的也当没问题，不误伤）。
    """
    if not callable(fn):
        return f"{what} 必须是函数，现在给的是 {type(fn).__name__}"
    try:
        import inspect as _ins
        sig = _ins.signature(fn)
    except Exception:
        return ""
    try:
        sig.bind(*([None] * nargs))
        return ""
    except TypeError:
        pass
    try:
        n = len([p for p in sig.parameters.values()
                 if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)])
    except Exception:
        n = -1
    name = getattr(fn, "__name__", "?")
    return (f"{what} 的参数个数不对：应该收 {nargs} 个（{argnames}），"
            f"但 {name} 收 {n} 个")


class PluginContext:
    """传给插件的上下文。插件入口只需要 `def setup(ctx)`。"""

    def __init__(self, meta, pdir, epath):
        self.meta = meta
        self.dir = pdir
        self.entry_path = epath
        self.name = str(meta.get("name") or os.path.basename(pdir))
        self.display_name = str(meta.get("display_name") or self.name)
        self.version = str(meta.get("version") or "1.0.0")
        self.author = str(meta.get("author") or "")
        self.description = str(meta.get("description") or "")
        self.tags = [str(t) for t in (meta.get("tags") or [])]
        # 插件注册进来的东西
        self.commands = {}         # cmd -> (handler, help, prefix)
        self.message_hooks = []    # [handler]
        self.config_schema = []    # [{"title", "fields"}]
        self.pages = []            # [{"id", "title", "html"}]
        self.apis = []             # [{"path", "handler", "methods"}]
        self.loaded = False
        self.enabled = True
        self.error = ""
        self.activated = False      # setup() 是否已经执行过
        self._pending_setup = None  # 扫描阶段先存着，核心就绪后再调
        self.order = 100

    # ---------------- 日志 ----------------
    def log(self, msg):
        log("info", "插件", f"{self.display_name}：{msg}")

    def warn(self, msg):
        log("warn", "插件", f"{self.display_name}：{msg}")

    # ---------------- 配置 ----------------
    # 插件配置跟内置配置一样，**平铺在 config.json 顶层**。
    # 这样管理界面（它就是把值平铺提交的）跟插件的读写能对上，
    # 老用户配置里已有的 lottery_* 这类键也天然兼容。
    @property
    def config(self):
        pc = CFG.get("plugin_config")
        if isinstance(pc, dict):
            d = pc.get(self.name)
            if isinstance(d, dict):
                return d
        return {}

    def cfg(self, key, default=None):
        """读插件配置。顺序：顶层平铺键 -> plugin_config.<插件名> -> metadata 里的 defaults。"""
        v = CFG.get(key)
        if v is None:
            v = self.config.get(key)
        if v is None:
            v = (self.meta.get("defaults") or {}).get(key)
        return default if v is None else v

    def set_cfg(self, key, value):
        """写插件配置（写到顶层平铺键，跟管理界面一致）。"""
        CFG[key] = value
        _PLUGIN_KEYS.add(key)
        save_config()

    # ---------------- 注册能力 ----------------
    def command(self, cmd, help="", prefix=False):
        """注册一个群命令（被 @ 时触发）。handler(event, cmd) 返回 True 表示已处理。

        命令是**精确匹配**整条消息的。想让「查询游戏 鸣潮」这种带参数的也能触发，
        传 prefix=True —— 那样匹配「查询游戏」本身，或者「查询游戏 + 空格 + 任意参数」。
        handler 收到的 cmd 仍然是**整条消息**，自己 split 取参数：

            @ctx.command("查询游戏", "查资源", prefix=True)
            async def h(event, cmd):
                arg = cmd[len("查询游戏"):].strip()   # 「查询游戏 鸣潮」→「鸣潮」
                ...

        参数个数不对会**当场报错**（而不是等群里发命令时才在日志里报）。
        """
        def deco(fn):
            err = _check_handler_sig(fn, f"命令「{cmd}」的处理函数", 2, "event, cmd")
            if err:
                raise TypeError(err)
            self.commands[str(cmd)] = (fn, help, bool(prefix))
            return fn
        return deco

    def on_message(self):
        """注册全量消息钩子。handler(event, text) 返回 True 表示吞掉这条消息。

        参数个数不对会**当场报错**。
        """
        def deco(fn):
            err = _check_handler_sig(fn, "消息钩子", 2, "event, text")
            if err:
                raise TypeError(err)
            self.message_hooks.append(fn)
            return fn
        return deco

    def register_config(self, title, fields):
        """声明这个插件的配置项。

        fields 格式跟内置的一样：[[键, 标签, 类型], ...]
        类型：bool / number / text / backend(下拉) 等，跟内置渲染器一致。

        这些配置**不会**混进「系统配置」页 —— 它们出现在**这个插件自己的配置小窗**里：
        管理界面「插件 → 已安装」的卡片上点「配置」就弹出来。
        """
        self.config_schema.append({"title": str(title), "fields": list(fields)})
        for f in fields:
            try:
                _PLUGIN_KEYS.add(f[0])
            except Exception:
                pass

    def register_page(self, pid, title, html=""):
        """往管理界面「扩展」分组下加一个侧边栏页面。

        pid 建议带插件名前缀避免撞车（它同时是页面元素 id：page-<pid>）。
        html 里可以直接写 <script>，会被执行 —— 但脚本跑在全局作用域，
        函数名请自己加前缀，别用 load() / run() 这种通用名。

        插件被停用或卸载时，页面和导航项会自动消失。
        """
        self.pages.append({"id": str(pid), "title": str(title), "html": html})

    def register_api(self, path, handler, methods=("GET",)):
        """注册管理接口（跟插件一起装卸）。

        path 以 / 开头，**建议放 /admin/api/plugins/<插件名>/ 下面**避免撞车。
        会自动带上 token 校验（跟内置管理接口一致）。

        handler 可以是 async 或普通函数，签名 handler(request, body)：
            request = FastAPI 的 Request，查询参数用 request.query_params.get("x")
            body    = POST 的 JSON body（dict；GET 时是 {}）
        返回值会被 JSON 序列化成响应。

        handler 不是函数、或参数个数不对，会**当场报错**。
        """
        _e = _check_handler_sig(handler, f"接口 {path} 的处理函数", 2, "request, body")
        if _e:
            raise TypeError(_e)
        self.apis.append({
            "path": str(path),
            "handler": handler,
            "methods": [str(m).upper() for m in methods],
        })

    # ---------------- 服务 ----------------
    def mention(self, uid):
        """@某人的片段。跟字符串混在一个列表里传给 send_group / send_private。

        用法：
            await ctx.send_group(gid, [ctx.mention(uid), " 恭喜中奖！"])
        """
        return ("__at__", int(uid))

    def image(self, url):
        """图片片段。同样放进列表里。

        用法：
            await ctx.send_group(gid, [ctx.mention(uid), " 你的图：", ctx.image(url)])
        """
        return ("__img__", str(url))

    @staticmethod
    def _build_message(content):
        """把字符串或列表拼成 OneBot 的 Message。"""
        m = Message()
        if isinstance(content, str):
            if content:
                m.append(MessageSegment.text(content))
            return m
        if isinstance(content, (list, tuple)):
            for part in content:
                if (isinstance(part, tuple) and len(part) == 2
                        and part[0] == "__at__"):
                    m.append(MessageSegment.at(int(part[1])))
                elif (isinstance(part, tuple) and len(part) == 2
                        and part[0] == "__img__"):
                    m.append(MessageSegment.image(str(part[1])))
                elif part is not None and str(part) != "":
                    m.append(MessageSegment.text(str(part)))
            return m
        m.append(MessageSegment.text(str(content)))
        return m

    async def send_group(self, gid, content):
        """发群消息。content 可以是字符串，或 [ctx.mention(uid), "文字"] 这样的列表。"""
        msg = self._build_message(content)
        if not len(msg):
            return False
        for bot in list(nonebot.get_bots().values()):
            try:
                await bot.send_group_msg(group_id=int(gid), message=msg)
                return True
            except Exception:
                continue
        return False

    async def send_private(self, uid, content):
        """发私聊。content 同上，支持字符串或列表。"""
        msg = self._build_message(content)
        if not len(msg):
            return False
        for bot in list(nonebot.get_bots().values()):
            try:
                await bot.send_private_msg(user_id=int(uid), message=msg)
                return True
            except Exception:
                continue
        return False

    # ---------------- 群管动作 ----------------
    # 管群类插件（反广告、刷屏、违规词…）都要这几样。
    # 没做成"插件自己 import nonebot"—— 那是内部实现，不该让插件依赖。
    async def delete_msg(self, event):
        r"""撤回一条消息。失败返回 False（比如不是管理员、消息太旧）。"""
        try:
            for bot in list(nonebot.get_bots().values()):
                try:
                    await bot.delete_msg(message_id=int(event.message_id))
                    return True
                except Exception:
                    continue
        except Exception as e:
            self.warn(f"撤回失败 {type(e).__name__}: {e}")
        return False

    async def kick(self, gid, uid, reject_add=True):
        r"""把某人踢出群。reject_add=True 时同时拒绝他再申请。"""
        try:
            for bot in list(nonebot.get_bots().values()):
                try:
                    await bot.set_group_kick(group_id=int(gid), user_id=int(uid),
                                             reject_add_request=bool(reject_add))
                    return True
                except Exception:
                    continue
        except Exception as e:
            self.warn(f"踢出失败 {type(e).__name__}: {e}")
        return False

    def bl_add(self, uid, gid, nick="", reason=""):
        r"""加进黑名单（主程序那张表，管理界面「黑名单」能看见）。"""
        try:
            DB.bl_add(int(uid), int(gid), str(nick or ""), str(reason or ""))
            return True
        except Exception as e:
            self.warn(f"拉黑失败 {type(e).__name__}: {e}")
            return False

    def is_super(self, uid):
        r"""是不是超管（配置里的 superusers）。插件通常该放行超管。"""
        try:
            return str(uid) in SUPERUSERS
        except Exception:
            return False

    async def nick(self, gid, uid):
        r"""取群昵称。取不到时返回 QQ 号字符串。"""
        try:
            for bot in list(nonebot.get_bots().values()):
                n = await safe_nick(bot, int(gid), int(uid))
                if n:
                    return n
        except Exception:
            pass
        return str(uid)

    def db(self):
        """开一个主库连接（插件自己的表自己建，建议加插件名前缀）。"""
        return sqlite3.connect(DB_PATH, timeout=15)

    def self_id(self):
        """当前登录的机器人 QQ（字符串）。没有在线 bot 时返回空串。"""
        try:
            bots = list(nonebot.get_bots().values())
            return str(bots[0].self_id) if bots else ""
        except Exception:
            return ""

    def act_recent(self, gid, days=7):
        """最近 N 天在群里说过话的成员。

        返回 [{user_id, group_id, last_active, msg_count, nickname}, ...]，
        按最近活跃时间倒序。注意字段是 msg_count，不是 count。

        依赖主库的 activity 表。表不存在或查询失败时返回空列表，
        插件自己判断"没人活跃"即可，不用额外 try。
        """
        try:
            return DB.act_recent(int(gid), days=int(days)) or []
        except Exception as e:
            log("warn", "插件", f"{self.display_name}：act_recent 失败 {type(e).__name__}: {e}")
            return []

    def db_exec(self, sql, params=()):
        """执行写语句（插件可以建自己的表、写自己的数据）。

        表名建议加插件前缀，避免跟别的插件撞车。
        """
        with DB._lock, DB._conn() as c:
            cur = c.execute(sql, params if params is not None else ())
            c.commit()
            return cur.rowcount

    def db_query(self, sql, params=()):
        """执行查询，返回 [dict, ...]。"""
        with DB._conn() as c:
            return [dict(r) for r in c.execute(sql, params if params is not None else ())]

    async def http_get(self, url, params=None, timeout=15.0, headers=None):
        """GET 请求。

        用主程序的共享客户端，本机地址会自动直连 —— 系统代理开着也不会
        把 localhost 请求发给代理（那会导致 502，是个踩过的真坑）。
        """
        return await get_http().get(url, params=params, timeout=float(timeout),
                                    headers=headers or {})

    async def ai(self, prompt, timeout=15.0, use_gap=False):
        """让模型生成一段文字。模型没启用或调用失败时返回空字符串（不抛异常）。"""
        try:
            if not AI.enabled:
                return ""
            r = await AI.raw([{"role": "user", "content": str(prompt)}],
                             timeout=float(timeout), use_gap=use_gap)
            return (r or "").strip()
        except Exception as e:
            self.warn(f"AI 调用失败 {type(e).__name__}: {e}")
            return ""

    def sign_do(self, uid, gid, nick, base, bonus_per, bonus_cap, today):
        """原子签到（同一天重复调用会返回 already=True）。返回 dict。"""
        return DB.sign_do_atomic(int(uid), int(gid), str(nick or ""), int(base),
                                 int(bonus_per), int(bonus_cap), str(today))

    def sign_get(self, uid, gid):
        """某人的积分记录（没签过返回 None）。"""
        return DB.sign_get(int(uid), int(gid))

    def sign_rank(self, gid, limit=10):
        """本群积分榜：[{user_id, nickname, points, sign_count, streak}, ...]。"""
        try:
            return DB.sign_rank(int(gid), limit=int(limit)) or []
        except Exception as e:
            log("warn", "插件", f"{self.display_name}：sign_rank 失败 {type(e).__name__}: {e}")
            return []

    def sign_rank_all(self):
        """所有群的积分汇总：[{group_id, cnt, total}, ...]。"""
        try:
            return DB.sign_rank_all() or []
        except Exception:
            return []

    def sign_clear_group(self, gid):
        """清空某群所有人的积分。"""
        return DB.sign_clear_group(int(gid))

    def points_spend(self, uid, gid, amount):
        """扣积分。返回 (是否成功, 剩余积分)。积分不够就返回 (False, 当前值)。"""
        return DB.points_spend(int(uid), int(gid), int(amount))

    def points_refund(self, uid, gid, amount):
        """退还积分。"""
        return DB.points_refund(int(uid), int(gid), int(amount))

    def daily_rank(self, gid, date_str, limit=10):
        """某天的发言榜：[{user_id, nickname, msg_count}, ...] 按条数降序。"""
        try:
            return DB.daily_rank(int(gid), str(date_str), limit=int(limit)) or []
        except Exception as e:
            log("warn", "插件", f"{self.display_name}：daily_rank 失败 {type(e).__name__}: {e}")
            return []

    def daily_summary(self, gid, date_str):
        """某天的汇总：{"users": 发言人数, "total": 总条数}。"""
        try:
            r = DB.daily_summary(int(gid), str(date_str)) or {}
            return {"users": int(r.get("users") or 0),
                    "total": int(r.get("total") or 0)}
        except Exception as e:
            log("warn", "插件", f"{self.display_name}：daily_summary 失败 {type(e).__name__}: {e}")
            return {"users": 0, "total": 0}

    async def group_name(self, gid):
        """群名（走一次接口，有失败兜底）。

        群名不存库 —— 数据库里只有 activity/points 这些业务表，
        群名一直是实时调 bot.get_group_info() 拿的。
        """
        try:
            for bot in list(nonebot.get_bots().values()):
                info = await bot.get_group_info(group_id=int(gid), no_cache=False)
                n = (info or {}).get("group_name")
                if n:
                    return str(n)
        except Exception:
            pass
        return f"群{gid}"

    def kv_get(self, key, default=None):
        """插件私有键值存储（存在 data/plugins.json 的 installed 下）。"""
        try:
            st = _PLUGIN_STATE.get("installed") or {}
            me = st.get(self.name) or {}
            kv = me.get("kv") or {}
            v = kv.get(key)
            return default if v is None else v
        except Exception:
            return default

    def kv_set(self, key, value):
        """写插件私有键值。适合存少量状态（大量数据请用自己的表）。"""
        try:
            st = _PLUGIN_STATE.setdefault("installed", {})
            me = st.setdefault(self.name, {})
            kv = me.setdefault("kv", {})
            kv[key] = value
            _save_plugin_state()
            return True
        except Exception:
            return False

    def info(self):
        return {
            "name": self.name,
            "display_name": self.display_name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "tags": self.tags,
            "dir": self.dir,
            "loaded": self.loaded,
            "activated": bool(self.activated),
            "enabled": self.enabled,
            "error": self.error,
            "commands": sorted(self.commands.keys()),
            "hooks": len(self.message_hooks),
            "pages": [p["id"] for p in self.pages],
            "page_titles": {p["id"]: p["title"] for p in self.pages},
            "apis": [a["path"] for a in self.apis],
            "config": list(self.config_schema),
        }


def _load_plugin_state():
    global _PLUGIN_STATE
    try:
        with open(PLUGIN_STATE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            _PLUGIN_STATE = {
                "disabled": [str(x) for x in (d.get("disabled") or [])],
                "installed": dict(d.get("installed") or {}),
            }
    except Exception:
        pass
    return _PLUGIN_STATE


def _save_plugin_state():
    try:
        os.makedirs(os.path.dirname(PLUGIN_STATE_PATH), exist_ok=True)
        _atomic_write_json(PLUGIN_STATE_PATH, _PLUGIN_STATE)
        return True
    except Exception as e:
        log("warn", "插件", f"状态保存失败：{type(e).__name__}: {e}")
        return False


def _read_plugin_meta(pdir):
    """读插件元信息。优先 metadata.json；没有再看 metadata.yaml。"""
    jp = os.path.join(pdir, "metadata.json")
    if os.path.exists(jp):
        try:
            with open(jp, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d, ""
            return None, "metadata.json 根节点必须是对象"
        except Exception as e:
            return None, f"metadata.json 解析失败：{e}"
    yp = os.path.join(pdir, "metadata.yaml")
    if os.path.exists(yp):
        try:
            import yaml
        except ImportError:
            return None, "有 metadata.yaml 但缺 PyYAML，请改用 metadata.json"
        try:
            with open(yp, "r", encoding="utf-8") as f:
                d = yaml.safe_load(f)
            if isinstance(d, dict):
                return d, ""
            return None, "metadata.yaml 根节点必须是对象"
        except Exception as e:
            return None, f"metadata.yaml 解析失败：{e}"
    return None, "缺少 metadata.json"


def load_plugin(pdir, force=False):
    """加载一个插件目录。失败也会记进 PLUGINS（带上 error），方便界面显示。"""
    import importlib.util as _ilu
    folder = os.path.basename(pdir)
    meta, err = _read_plugin_meta(pdir)
    if meta is None:
        # 元数据读不出来时，只要这个目录"看着像插件"（有 metadata.json /
        # metadata.yaml / main.py），就留一张带错误的卡片。
        # 直接 return None 的话它会从管理界面彻底消失，用户只能翻日志 ——
        # 跟本函数开头那句"失败也会记进 PLUGINS"的承诺不符。
        # 三者都没有的目录（随手放的文件夹、备份）照旧跳过，免得刷屏。
        _looks_like_plugin = any(
            os.path.exists(os.path.join(pdir, f))
            for f in ("metadata.json", "metadata.yaml", "main.py"))
        if not _looks_like_plugin:
            return None
        log("warn", "插件", f"{folder}：{err}")
        _c = PluginContext({"name": folder, "display_name": folder}, pdir,
                           os.path.join(pdir, "main.py"))
        _c.error = err
        _c.loaded = False
        PLUGINS.setdefault(folder, _c)
        return _c
    pname = str(meta.get("name") or folder)
    if pname in PLUGINS and not force:
        # 同名插件会被静默吞掉，以前只在日志里少一个数字，很难发现
        _old = PLUGINS[pname]
        log("warn", "插件",
            f"{folder} 的 name 是「{pname}」，已经被 {os.path.basename(_old.dir)} "
            f"占用了 —— 这个会被忽略。name 是唯一键，改一个再放进来。")
        return PLUGINS[pname]
    entry = str(meta.get("entry") or "main.py")
    epath = os.path.join(pdir, entry)
    # 入口文件也不能指到插件目录外面（metadata 是插件自己写的）
    try:
        _pd = os.path.abspath(pdir)
        if os.path.commonpath([os.path.abspath(epath), _pd]) != _pd:
            entry = "main.py"
            epath = os.path.join(pdir, entry)
    except Exception:
        entry = "main.py"
        epath = os.path.join(pdir, entry)
    meta.setdefault("name", pname)
    ctx = PluginContext(meta, pdir, epath)
    ctx.enabled = folder not in _PLUGIN_STATE["disabled"] and pname not in _PLUGIN_STATE["disabled"]
    if not os.path.exists(epath):
        ctx.error = f"找不到入口文件 {entry}"
        PLUGINS[pname] = ctx
        log("error", "插件", f"{ctx.display_name}：{ctx.error}")
        return ctx
    modname = "nekoe_plugin_" + re.sub(r"\W+", "_", pname)
    try:
        spec = _ilu.spec_from_file_location(modname, epath)
        mod = _ilu.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)
    except Exception as e:
        ctx.error = f"导入失败：{type(e).__name__}: {e}"
        PLUGINS[pname] = ctx
        log("error", "插件", f"{ctx.display_name} {ctx.error}")
        return ctx
    # 检查有没有 import 主程序 —— 源码环境能跑，装到桌面版（exe）必炸
    try:
        with open(epath, encoding="utf-8", errors="replace") as _f:
            _ep = _f.read()
        if re.search(r"^\s*(?:import\s+bot\b|from\s+bot\s+import)", _ep, re.M):
            ctx.warn("入口文件里 import 了主程序（bot）—— 源码环境能跑，"
                     "但装到桌面版（exe）里会 ModuleNotFoundError。"
                     "插件需要的东西都在 ctx 上，不要 import 主程序。")
    except Exception:
        pass

    setup = getattr(mod, "setup", None)
    if not callable(setup):
        ctx.error = "入口文件里没有 setup(ctx) 函数"
        PLUGINS[pname] = ctx
        log("error", "插件", f"{ctx.display_name}：{ctx.error}")
        return ctx
    # 这里只把 setup 存起来，不立刻执行。
    # 插件扫描发生在模块很靠前的位置（管理界面可能比机器人先打开），
    # 那时 DB / AI / app 都还没建好 —— setup() 里一碰数据库就 NameError。
    # 真正的初始化交给 activate_all_plugins()，在核心就绪后统一跑。
    ctx._pending_setup = setup
    ctx.loaded = True
    PLUGINS[pname] = ctx
    if ctx.enabled:
        log("info", "插件", f"{ctx.display_name} v{ctx.version} 已装载（待初始化）")
    else:
        log("info", "插件", f"{ctx.display_name} v{ctx.version} 已装载（当前停用）")
    return ctx


def activate_plugin(ctx):
    """执行一个插件的 setup()。必须在 DB / AI 建好之后调。"""
    if ctx is None or getattr(ctx, "activated", False):
        return False
    setup = getattr(ctx, "_pending_setup", None)
    if not callable(setup):
        ctx.activated = True
        return False
    try:
        setup(ctx)
    except Exception as e:
        ctx.error = f"setup() 抛异常：{type(e).__name__}: {e}"
        ctx.loaded = False
        log("error", "插件", f"{ctx.display_name} {ctx.error}")
        return False
    ctx.activated = True
    ctx.error = ""
    log("info", "插件", f"{ctx.display_name} v{ctx.version} 已加载"
        f"（{len(ctx.commands)} 个命令 / {len(ctx.message_hooks)} 个钩子）")
    return True


def activate_all_plugins():
    """核心（DB / AI）就绪后，把所有扫描到的插件初始化一遍。"""
    n = 0
    for ctx in list(PLUGINS.values()):
        try:
            if activate_plugin(ctx):
                n += 1
        except Exception as e:
            log("error", "插件", f"初始化 {ctx.display_name} 异常：{type(e).__name__}: {e}")
    if n:
        log("info", "插件", f"初始化完成：{n} 个插件已就绪")
    return n


def load_all_plugins():
    """扫描插件目录并全部加载。启动时调一次。"""
    _load_plugin_state()
    try:
        os.makedirs(PLUGIN_DIR, exist_ok=True)
    except Exception as e:
        log("warn", "插件", f"插件目录创建失败：{e}")
        return
    try:
        names = sorted(os.listdir(PLUGIN_DIR))
    except Exception:
        return
    n_ok = n_bad = 0
    for d in names:
        pdir = os.path.join(PLUGIN_DIR, d)
        if not os.path.isdir(pdir) or d.startswith((".", "_")):
            continue
        ctx = load_plugin(pdir)
        if ctx is None:
            continue
        if ctx.loaded:
            n_ok += 1
        else:
            n_bad += 1
    log("info", "插件", f"扫描完成：{n_ok} 个可用"
        + (f"，{n_bad} 个有问题" if n_bad else "")
        + "（等核心就绪后初始化）")


def unload_plugin(name):
    """从内存里卸掉一个插件（目录还在）。

    注意：必须同时把路由从 app.routes 里摘掉。只清 _plugin_route_index 的话，
    reload_all_plugins() 就不知道这些路径已经挂过、清理不掉，
    之后重新装同名插件会一直报"与已有路由冲突，跳过"（接口静默失效）。
    """
    ctx = PLUGINS.pop(name, None)
    if ctx is None:
        return False
    paths = set()
    for a in ctx.apis:
        try:
            paths.add(a["path"])
            _plugin_route_index.pop(a["path"], None)
        except Exception:
            pass
    _drop_routes(paths)
    return True


def _drop_routes(paths):
    """把指定路径的路由从 FastAPI 上摘掉（app 还没建时什么都不做）。"""
    if not paths:
        return 0
    try:
        if app is None:
            return 0
        before = len(app.router.routes)
        app.router.routes = [r for r in app.router.routes
                             if getattr(r, "path", None) not in paths]
        return before - len(app.router.routes)
    except Exception:
        return 0


def reload_all_plugins():
    """重新扫描并加载所有插件。

    注意：必须先把上次挂的插件路由摘掉。否则改了接口代码再点「重新加载」，
    路由还是旧的（FastAPI 里路径已存在会被跳过），表现为"改了没反应"。
    """
    global PLUGINS, _PLUGIN_KEYS
    _drop_routes(set(_plugin_route_index.keys()))
    PLUGINS = {}
    _PLUGIN_KEYS = set()
    _plugin_route_index.clear()
    load_all_plugins()
    activate_all_plugins()   # 重载是在运行时点的，这时 DB/AI 早就有了
    _register_plugin_apis()
    return len([c for c in PLUGINS.values() if c.loaded])


def set_plugin_enabled(folder_or_name, enabled):
    """启用/停用插件。停用后重启生效（命令和钩子立刻不响应）。"""
    dis = _PLUGIN_STATE.setdefault("disabled", [])
    key = str(folder_or_name)
    if enabled:
        dis[:] = [x for x in dis if x != key]
    else:
        if key not in dis:
            dis.append(key)
    # 同步到内存里的 ctx
    for ctx in PLUGINS.values():
        folder = os.path.basename(ctx.dir)
        if key in (ctx.name, folder):
            ctx.enabled = bool(enabled)
    _save_plugin_state()
    return True


def plugin_config_schema():
    """收集所有可用插件声明的配置分组。

    每项都带上 plugin（插件名）和 plugin_title（显示名）——
    管理界面靠它把配置项归到对应插件的小窗里，而不是全塞进系统配置页。
    """
    out = []
    for ctx in sorted(PLUGINS.values(), key=lambda c: c.order):
        if not (ctx.loaded and ctx.enabled):
            continue
        for g in ctx.config_schema:
            d = dict(g)
            d["plugin"] = ctx.name
            d["plugin_title"] = ctx.display_name
            out.append(d)
    return out


def plugin_pages():
    out = []
    for ctx in sorted(PLUGINS.values(), key=lambda c: c.order):
        if ctx.loaded and ctx.enabled:
            out.extend(ctx.pages)
    return out


# ============================================================
#  插件市场 —— 阶段 3
# ============================================================
#  注册表是一份 JSON 索引（默认在 nekoe-plugins 仓库里），格式：
#    {"version": 1, "plugins": [
#       {"name":"hello", "display_name":"打招呼", "version":"1.0.0",
#        "author":"...", "description":"...",
#        "repo":"https://github.com/user/nekoe-plugin-hello",
#        "tags":["示例"]}, ...]}
#
#  安装方式两种：
#    · 从注册表里的名字装（走市场页面点一下）
#    · 直接给一个 GitHub 仓库地址装（不用等收录）
#
#  安全：下载大小上限、解压拒绝越界路径、文件数上限。
#  但插件终究是要在你机器上跑的 Python 代码，装之前自己想清楚。
# ============================================================

# 市场索引的多个来源，按顺序试。第一个能用的就用。
# 为什么不用 raw.githubusercontent.com 打头：这个域名在国内被单独墙，
# 直连和代理都是 SSL EOF；jsDelivr 和 api.github.com 反而直连就通。
PLUGIN_MARKET_REPO = os.environ.get("NEKOE_PLUGIN_REPO", "Mario9800/nekoe-plugins")
PLUGIN_MARKET_BRANCH = os.environ.get("NEKOE_PLUGIN_BRANCH", "main")


def _market_urls():
    """生成市场索引的候选地址（按可用性排序）。"""
    env = os.environ.get("NEKOE_PLUGIN_MARKET")
    if env:
        return [env]
    owner_repo = PLUGIN_MARKET_REPO
    br = PLUGIN_MARKET_BRANCH
    # 顺序讲究：api.github.com 永远是最新的；jsDelivr 对 @main 引用会缓存 12 小时，
    # 我加了个插件但市场里看不到就是因为这个。所以 API 排第一，
    # CDN 用 @latest（会跟着最新 tag / 默认分支走，缓存策略也不一样）当备选。
    return [
        f"https://api.github.com/repos/{owner_repo}/contents/index.json",
        f"https://cdn.jsdelivr.net/gh/{owner_repo}@latest/index.json",
        f"https://raw.githubusercontent.com/{owner_repo}/{br}/index.json",
    ]


PLUGIN_MARKET_URL = _market_urls()[0]      # 界面显示用
PLUGIN_MARKET_CACHE = os.path.join(BASE_DIR, "data", "plugin_market.json")
PLUGIN_MAX_DOWNLOAD = 30 * 1024 * 1024      # 单包最大 30 MB
PLUGIN_MAX_FILES = 3000                     # 解压后文件数上限
PLUGIN_MAX_UNPACK = 120 * 1024 * 1024       # 解压后总大小上限

_PLUGIN_MARKET = {"ts": 0.0, "items": [], "error": ""}
_MARKET_LOCK = threading.Lock()


# 发布签名公钥（Ed25519，32 字节 hex）。
# 私钥在作者本机，不在这个仓库里 —— 这样即使 GitHub 账号被拿走，
# 攻击者也换不了包：他签不出有效签名。
# 轮换：换新私钥后，这里换成新公钥，发一版（用户必须通过"直连"拿到这一版）。
RELEASE_PUBKEY = "89e7d909c17e63206c89d83c99701bcbf991eba9debd6452813c2bb75736663d"
RELEASE_KEY_FP = RELEASE_PUBKEY[:16]


_SIG_READY = None


def _sig_ready():
    r"""验签能力自检：cryptography 在不在。

    它是传递依赖，打包时不一定被带上。带不上就会**静默**地把所有签名都
    判成无效 -> 加速节点全废。所以要在启动时就知道，并且暴露出来。
    """
    global _SIG_READY
    if _SIG_READY is None:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa
                Ed25519PublicKey)
            _SIG_READY = True
        except Exception as e:
            _SIG_READY = False
            try:
                log("error", "更新",
                    f"⚠ 验签能力不可用（{type(e).__name__}: {e}）——"
                    f"带签名的版本会全部校验失败，加速节点将不可用。"
                    f"请报告作者。")
            except Exception:
                pass
    return _SIG_READY


def _verify_release_sig(msg: bytes, sig: bytes) -> bool:
    r"""用编译进来的公钥验签 SHA256SUMS.txt。

    验签失败一律返回 False，绝不抛异常出去 —— 调用方要能明确区分
    "验过了" 和 "没验成"，不能因为一个异常就当成通过。
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey)
        pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(RELEASE_PUBKEY))
        pk.verify(sig, msg)
        return True
    except Exception as e:
        try:
            log("warn", "更新", f"签名校验未通过：{type(e).__name__}: {e}")
        except Exception:
            pass
        return False


def _ua():
    r"""HTTP User-Agent。必须是 ASCII。

    HTTP 头只能是 ASCII，非 ASCII 会让 httpx 构请求头时抛
    UnicodeEncodeError（曾经真实发生过：UA 写成中文，AI/识图/语音三条链路全挂）。
    APP_VERSION 是读 VERSION 文件来的 —— 源码运行或有人手改文件都可能塞进中文，
    所以每个 per-request 的 UA 都得走这里，不能直接拼。
    """
    u = "Nekolyra/" + (APP_VERSION or "0")
    try:
        u.encode("ascii")
        return u
    except UnicodeEncodeError:
        return "Nekolyra/0"


def _read_app_version():
    r"""读程序旁边的 VERSION 文件（打包时由 build.py 写进去）。

    读不到就返回空串 —— 界面会隐藏版本号，而不是显示一个假的 0.0.0.0。
    源码运行和打包运行的位置不同，所以两个地方都找一下。
    """
    for base in (BASE_DIR, os.path.dirname(os.path.abspath(__file__))):
        try:
            p = os.path.join(base, "VERSION")
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                v = f.read().strip()
            if v:
                return v
        except Exception:
            pass
    return ""


APP_VERSION = _read_app_version()

# 检查更新用的 GitHub 仓库。想指向自己的 fork 就设这个环境变量。
UPDATE_REPO = os.environ.get("NEKOE_UPDATE_REPO", "Mario9800/Nekolyra")
# 可选的 GitHub token：未登录的 api.github.com 只有 60 次/小时，
# 带上 token 是 5000 次/小时。普通用户不用管（有缓存，一天就几次），
# 但如果机器人频繁重启、或者同一 IP 上还有别的工具在打 GitHub，就会撞上限。
# 只要只读权限，不需要任何 scope。
UPDATE_TOKEN = os.environ.get("NEKOE_UPDATE_TOKEN", "").strip()

# 下载更新包的源。GitHub 在国内直连不稳定，"加速节点"是公益反代，
# 走它们能快很多 —— 但**第三方有能力替换你下到的 exe**，
# 所以非直连的源一律强制校验 SHA-256（见 _update_expected_sha）。
# 想加自己的节点：往这里加一行，前缀就是反代地址。
# 下面这几个是实测过的（2026-10 从本机测）：
#   ghproxy.net   完整下 388081 字节，sha256 与官方一致
#   gh-proxy.com  同上
#   gitproxy.click 返回的是 HTML 跳转页（563 字节），sha256 对不上 -> 没收录
#   ghfast.top / gh.llkk.cc / ghproxy.cc 等一律超时 -> 没收录
# 加速节点是公益服务，哪天挂了就把那一行删掉，不影响直连。
UPDATE_SOURCES = [
    ("direct", "直连（GitHub 官方）", ""),
    ("ghproxy", "加速节点 ghproxy.net", "https://ghproxy.net/"),
    ("ghproxycom", "加速节点 gh-proxy.com", "https://gh-proxy.com/"),
]
UPDATE_SRC_MAP = {k: (label, prefix) for k, label, prefix in UPDATE_SOURCES}
# 下载目录：放在程序的 data 下，用户好找
UPDATE_DIR = os.path.join(BASE_DIR, "data", "updates")


def _ver_tuple(v):
    """把 "1.2.3" 这类版本号变成可比较的元组。非数字段当 0。"""
    out = []
    for part in str(v or "0").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    # 补到 4 位：Nekolyra 用四段号，而且这样 "1.0.1" 与 "1.0.1.0" 会相等。
    # 补 3 位时 (1,0,1) < (1,0,1,0)，会把 "1.0.0.0" 误判成比 "1.0.0" 新。
    # 注意：三段 tag（如 1.0.10）的歧义**不是**补齐位数能解决的 ——
    # 左对齐比第三段 10>1，它本来就大于 1.0.1.9。
    while len(out) < 4:
        out.append(0)
    return tuple(out[:4])


def _load_market_cache():
    """读本地缓存的市场索引（启动时调，断网也有内容可看）。"""
    global _PLUGIN_MARKET
    try:
        with open(PLUGIN_MARKET_CACHE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            _PLUGIN_MARKET = {
                "ts": float(d.get("ts") or 0),
                "items": list(d.get("items") or []),
                "error": "",
            }
    except Exception:
        pass
    return _PLUGIN_MARKET


def _save_market_cache():
    try:
        os.makedirs(os.path.dirname(PLUGIN_MARKET_CACHE), exist_ok=True)
        _atomic_write_json(PLUGIN_MARKET_CACHE, {
            "ts": _PLUGIN_MARKET["ts"],
            "items": _PLUGIN_MARKET["items"],
        })
    except Exception:
        pass


# 检查更新的结果缓存：别每次开页面都去打 GitHub
_VER_CACHE = {"ts": 0.0, "data": None, "error": "", "fail_ts": 0.0,
              "last_force": 0.0}
_VER_TTL = 1800             # 成功结果缓存 30 分钟
_VER_FAIL_TTL = 300         # 失败也缓存 5 分钟 —— 不然一直重试，配额烧得飞快
_VER_FORCE_MIN_GAP = 60     # 手动「重新检查」最短间隔，防连点


# 版本检查的兜底源：仓库根目录的 version.json，走 jsDelivr（CDN，不限次数）。
# api.github.com 未登录只有 60 次/小时、而且是按 IP 算的 —— 同一台机器上
# 跑别的脚本把配额打满，管理页就只剩一条 403 警告，啥也看不到。
_VER_FALLBACK_URLS = [
    "https://cdn.jsdelivr.net/gh/{repo}@{branch}/version.json",
    "https://raw.githubusercontent.com/{repo}/{branch}/version.json",
]
# 跟插件市场保持一致：分支可配，不写死 main（主分支改名后兜底会跟着失效）
_VER_FALLBACK_BRANCH = os.environ.get("NEKOE_UPDATE_BRANCH", "main")


async def _fetch_version_json():
    r"""兜底：从 CDN / raw 读 version.json。

    只回 {latest, name, url, notes, tag}，没有 assets —— 所以它只能告诉
    用户「有新版」，下载还得等 api.github.com 恢复。够用了。
    失败返回 None。
    """
    for tpl in _VER_FALLBACK_URLS:
        u = tpl.format(repo=UPDATE_REPO, branch=_VER_FALLBACK_BRANCH)
        for trust in (False, True):
            try:
                async with httpx.AsyncClient(
                        timeout=12.0, follow_redirects=True, trust_env=trust,
                        mounts=None if trust else _http_mounts()) as c:
                    r = await c.get(u, headers={"User-Agent": _ua()})
                if r.status_code != 200:
                    continue
                d = r.json()
                tag = str(d.get("tag") or d.get("latest") or "").strip()
                if not tag:
                    continue
                return {
                    "latest": tag.lstrip("vV"),
                    "name": str(d.get("name") or ("Nekolyra " + tag)),
                    "url": str(d.get("url") or
                               f"https://github.com/{UPDATE_REPO}/releases/tag/{tag}"),
                    "notes": str(d.get("notes") or "")[:6000],
                    "published": str(d.get("published") or ""),
                    "tag": tag,
                    "assets": [],
                    "via": "cdn",
                }
            except Exception:
                continue
    return None


async def fetch_latest_release(force=False):
    r"""查 GitHub 上的最新 Release。失败返回 None，不抛异常。

    用 api.github.com —— 实测国内直连可用（github.com 网页版反而不通）。
    查不到只影响"有没有新版"这个提示，不能影响别的功能。
    """
    now = time.time()
    # 手动刷新限流：连点版本号不该把未登录配额（60 次/小时）烧光
    if force and now - _VER_CACHE["last_force"] < _VER_FORCE_MIN_GAP:
        force = False
    if force:
        _VER_CACHE["last_force"] = now
    if not force:
        if (_VER_CACHE["data"] is not None
                and now - _VER_CACHE["ts"] < _VER_TTL):
            return _VER_CACHE["data"]
        # 上次失败也冷静一会儿。否则每次开管理页都重试一遍，
        # 遇上 403（配额用完）会越试越糟。
        if (_VER_CACHE["data"] is None and _VER_CACHE["fail_ts"]
                and now - _VER_CACHE["fail_ts"] < _VER_FAIL_TTL):
            return None
    out, err = None, ""
    try:
        cli = get_http()
        _h = {"Accept": "application/vnd.github+json",
              "User-Agent": _ua()}
        if UPDATE_TOKEN:
            _h["Authorization"] = "Bearer " + UPDATE_TOKEN
        r = await cli.get(
            f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
            headers=_h, timeout=12.0)
        if r.status_code == 200:
            d = r.json()
            out = {
                "latest": str(d.get("tag_name") or "").lstrip("vV"),
                "name": str(d.get("name") or ""),
                "url": str(d.get("html_url") or ""),
                "notes": str(d.get("body") or "")[:6000],
                "published": str(d.get("published_at") or ""),
                # tag 是拼加速节点地址用的
                "tag": str(d.get("tag_name") or ""),
                "assets": [
                    {"name": str(a.get("name") or ""),
                     "size": int(a.get("size") or 0),
                     # url 是给"在浏览器里打开 GitHub"用的（国内可能不通），
                     # api_url 才是服务端能走通的那条（经它 302 到
                     # release-assets.githubusercontent.com）
                     "url": str(a.get("browser_download_url") or ""),
                     "api_url": str(a.get("url") or ""),
                     "id": int(a.get("id") or 0)}
                    for a in (d.get("assets") or [])
                ],
            }
        elif r.status_code == 404:
            err = "仓库里还没有 Release"
        elif r.status_code == 403:
            # 未登录的 api.github.com 是 60 次/小时，用完了就 403
            err = ("HTTP 403 —— GitHub 接口访问次数用完了"
                   "（未登录每小时 60 次），过一会儿再试")
        else:
            err = f"HTTP {r.status_code}"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    if out is None:
        # 主源挂了（最常见的是 403 配额用完）—— 试试 CDN 兜底。
        # 兜底拿到的话不报 WARN：用户能看到"有新版"才是重点。
        fb = await _fetch_version_json()
        if fb is not None:
            out = fb
            log("info", "更新", f"GitHub API 不可用（{err}），已用 CDN 兜底")
    if out is not None:
        _VER_CACHE["data"] = out
        _VER_CACHE["ts"] = now
        _VER_CACHE["error"] = ""
    else:
        _VER_CACHE["error"] = err
        _VER_CACHE["fail_ts"] = now
        log("warn", "更新", f"检查更新失败：{err}")
    return out


async def fetch_market(force=False, ttl=600):
    """拉插件市场索引。10 分钟内重复调用走缓存；网络失败时保留旧的。"""
    now = time.time()
    with _MARKET_LOCK:
        if not force and _PLUGIN_MARKET["items"] and now - _PLUGIN_MARKET["ts"] < ttl:
            return dict(_PLUGIN_MARKET)
    items, err = [], ""
    tried = []
    for u in _market_urls():
        try:
            # 每个源都用一份「不走代理」的客户端试一次 —— jsDelivr 和
            # api.github.com 直连就通，走代理反而更慢甚至失败。
            got = None
            for trust in (False, True):
                try:
                    async with httpx.AsyncClient(
                            timeout=20.0, follow_redirects=True,
                            trust_env=trust,
                            mounts=None if trust else _http_mounts()) as c:
                        r = await c.get(u, headers={"Accept": "application/json"})
                    if r.status_code != 200:
                        continue
                    d = r.json()
                    # api.github.com 的 contents 接口返回 base64
                    if isinstance(d, dict) and "content" in d and "encoding" in d:
                        import base64 as _b64
                        d = json.loads(_b64.b64decode(d["content"]).decode("utf-8"))
                    if isinstance(d, dict):
                        got = d.get("plugins") or []
                    elif isinstance(d, list):
                        got = d
                    if isinstance(got, list) and got:
                        break
                    got = None
                except Exception as e:
                    tried.append(f"{u.split('/')[2]}:{type(e).__name__}")
                    continue
            if got:
                items = got
                err = ""
                break
        except Exception as e:
            tried.append(f"{u.split('/')[2]}:{type(e).__name__}")
    if not items and not err:
        err = ("所有市场源都连不上（" + "、".join(tried[:6]) + "）。"
               "可以设环境变量 NEKOE_PLUGIN_MARKET 指向你自己的索引地址。")
    with _MARKET_LOCK:
        if items:
            _PLUGIN_MARKET["items"] = items
            _PLUGIN_MARKET["ts"] = now
            _PLUGIN_MARKET["error"] = ""
            _save_market_cache()
        else:
            _PLUGIN_MARKET["error"] = err
    return dict(_PLUGIN_MARKET)


def _safe_extract(zf, dest):
    """解压 zip，拒绝越界路径、超大文件、软链接。

    很多 zip 解压漏洞就出在 "../" 和绝对路径上 —— 装插件是下载陌生人的包，
    这一步不能省。
    """
    total = 0
    count = 0
    names = []
    for info in zf.infolist():
        raw = info.filename.replace("\\", "/")
        if not raw or raw.endswith("/"):
            continue
        parts = [p for p in raw.split("/") if p not in ("", ".")]
        if raw.startswith("/") or ".." in parts:
            raise ValueError(f"压缩包里有越界路径：{raw}")
        # 符号链接（unix 模式位 0xA000）
        mode = (info.external_attr >> 16) & 0xF000
        if mode == 0xA000:
            raise ValueError(f"压缩包含有软链接：{raw}")
        total += int(info.file_size or 0)
        count += 1
        if count > PLUGIN_MAX_FILES:
            raise ValueError(f"文件数超过上限 {PLUGIN_MAX_FILES}")
        if total > PLUGIN_MAX_UNPACK:
            raise ValueError("解压后总大小超过上限")
        names.append(raw)
    zf.extractall(dest)
    return names


def _find_plugin_root(root):
    """在解压出来的目录里找插件根（含 metadata.json 的那一层）。

    支持两种打包习惯：
      · 仓库根就是插件（metadata.json 在最外层）
      · 仓库里放一个子目录（GitHub zipball 会套一层 owner-repo-hash/）
    """
    hits = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        if "metadata.json" in files or "metadata.yaml" in files:
            hits.append(cur)
    if not hits:
        return None, "压缩包里找不到 metadata.json"
    # 取层级最浅的那个
    hits.sort(key=lambda p: (p.count(os.sep), len(p)))
    if len(hits) > 1:
        # 有多个就用最浅的，并提示一下
        return hits[0], ""
    return hits[0], ""


async def _plugin_http_get(url, accept="application/zip, */*", timeout=180.0):
    """下载插件包 / 查仓库信息。先直连，失败再走系统代理。

    为什么这个顺序：国内直连 api.github.com 是通的（1.3 秒），
    走 Clash 反而经常被 reset。
    返回 (bytes, "") 或 (None, 错误说明)。
    """
    last = ""
    for trust in (False, True):
        try:
            kw = {"timeout": timeout, "follow_redirects": True, "trust_env": trust}
            if not trust:
                kw["mounts"] = _http_mounts()
            async with httpx.AsyncClient(**kw) as c:
                r = await c.get(url, headers={"Accept": accept,
                                              "User-Agent": "Nekolyra/1.0"})
                if r.status_code == 200:
                    return r.content, ""
                last = f"HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    return None, last or "未知错误"


def _looks_like_github_repo(url):
    m = re.match(r"^https?://(?:www\.)?github\.com/([\w.\-]+)/([\w.\-]+?)(?:\.git)?/?$",
                 str(url or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)


async def install_plugin_from_url(url, expect_name="", subpath=""):
    """从 GitHub 仓库地址（或直接 zip 地址）安装插件。

    返回 {ok, name, files, message} 或 {ok: False, error}
    """
    raw = str(url or "").strip()
    if not raw:
        return {"ok": False, "error": "地址不能为空"}
    if not re.match(r"^https?://", raw):
        raw = "https://" + raw
    dl = raw
    gh = _looks_like_github_repo(raw)
    if gh:
        owner, repo = gh
        branch = "main"
        info, ierr = await _plugin_http_get(
            f"https://api.github.com/repos/{owner}/{repo}",
            accept="application/vnd.github+json", timeout=25.0)
        if info:
            try:
                branch = (json.loads(info.decode("utf-8")) or {}).get("default_branch") or "main"
            except Exception:
                pass
        elif ierr.startswith("HTTP 404"):
            return {"ok": False,
                    "error": f"仓库不存在或不是公开仓库：{owner}/{repo}"}
        # 用 API 的 zipball（是官方稳定入口，会自动重定向到 codeload）
        dl = f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch}"
    data, derr = await _plugin_http_get(
        dl, accept="application/vnd.github+json, application/zip, */*",
        timeout=240.0)
    if data is None:
        return {"ok": False, "error": f"下载失败：{derr}"}
    if not data:
        return {"ok": False, "error": "下载到 0 字节"}
    if len(data) > PLUGIN_MAX_DOWNLOAD:
        return {"ok": False,
                "error": f"包太大（{len(data)/1024/1024:.1f} MB，上限 {PLUGIN_MAX_DOWNLOAD//1024//1024} MB）"}
    if data[:2] != b"PK":
        head = data[:120].decode("utf-8", "replace").replace("\n", " ")
        return {"ok": False, "error": f"下载到的不是 zip（开头是 {head[:80]!r}）"}

    tmp = tempfile.mkdtemp(prefix="nekoe_plug_")
    try:
        zp = os.path.join(tmp, "p.zip")
        with open(zp, "wb") as f:
            f.write(data)
        ex = os.path.join(tmp, "x")
        os.makedirs(ex, exist_ok=True)
        try:
            with zipfile.ZipFile(zp) as zf:
                files = _safe_extract(zf, ex)
        except Exception as e:
            return {"ok": False, "error": f"解压失败：{e}"}
        # 先在指定的子目录里找（注册表仓库会这么用）
        sub = str(subpath or "").strip().strip("/")
        if sub:
            parts = [p for p in sub.split("/") if p not in ("", ".")]
            if ".." in parts:
                return {"ok": False, "error": f"path 不合法：{subpath}"}
            cand = os.path.join(ex, *parts)
            if not os.path.isdir(cand):
                # GitHub zipball 会套一层 owner-repo-hash/，这里也试一下
                inner = [d for d in os.listdir(ex) if os.path.isdir(os.path.join(ex, d))]
                if len(inner) == 1:
                    cand = os.path.join(ex, inner[0], *parts)
            if os.path.isdir(cand):
                root, err = _find_plugin_root(cand)
            else:
                root, err = None, f"仓库里找不到目录 {sub}"
        else:
            root, err = _find_plugin_root(ex)
        if root is None:
            return {"ok": False, "error": err}
        meta, merr = _read_plugin_meta(root)
        if meta is None:
            return {"ok": False, "error": f"metadata 有问题：{merr}"}
        pname = str(meta.get("name") or "").strip()
        if not pname:
            return {"ok": False, "error": "metadata.json 里没有 name"}
        # 注意：这里必须用 _plugin_dst 而不能只做正则 ——
        # 正则里的点是字面量，".." 能通过，而 join 出来会指到 data/，
        # 后面那条 rename + copytree 会把整个 data/ 挪走再覆盖。
        _dst, _derr = _plugin_dst(pname)
        if _dst is None:
            return {"ok": False, "error": _derr}
        if expect_name and pname != expect_name:
            return {"ok": False,
                    "error": f"装到的是 {pname}，跟预期的 {expect_name} 对不上。"
                             f"如果这个插件放在仓库的子目录里，"
                             f"安装时要指定子目录（市场索引里的 path 字段）"}
        entry = str(meta.get("entry") or "main.py")
        if not os.path.exists(os.path.join(root, entry)):
            return {"ok": False, "error": f"入口文件 {entry} 不存在"}

        os.makedirs(PLUGIN_DIR, exist_ok=True)
        dst = _dst
        old_ver = ""            # 覆盖安装时记下旧版本，返回给调用方打日志
        # 已装过就先备份旧的
        if os.path.exists(dst):
            pm, _ = _read_plugin_meta(dst)
            if pm:
                old_ver = str(pm.get("version") or "")
            bak = dst + f".bak_{datetime.now():%Y%m%d_%H%M%S}"
            try:
                os.rename(dst, bak)
            except Exception:
                shutil.rmtree(dst, ignore_errors=True)
            # 注意：以前这里写的是 meta["_replaced"] = old_ver —— meta 是内存里的
            # 字典，后面 copytree 只复制文件，这个字段永远不会落盘，也没人读。
            # 旧版本号改成从返回值带给调用方，让它能写进日志（"1.0.0 -> 1.0.1"）。
        shutil.copytree(root, dst, dirs_exist_ok=True)
        # 清掉可能带进来的缓存
        for cur, dirs, fs in os.walk(dst):
            if "__pycache__" in dirs:
                shutil.rmtree(os.path.join(cur, "__pycache__"), ignore_errors=True)
        try:
            st = _PLUGIN_STATE.setdefault("installed", {})
            st[pname] = {"version": str(meta.get("version") or "1.0.0"),
                         "source": raw,
                         # 仓库子目录必须一起存！不然更新的时候
                         # 会去仓库根目录找 metadata.json，找不到就更新失败
                         # （插件放子目录的仓库，比如 nekoe-plugins 的 plugins/<名>）
                         "path": sub,
                         "installed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            _save_plugin_state()
        except Exception:
            pass
        reload_all_plugins()
        ctx = PLUGINS.get(pname)
        return {
            "ok": True,
            "name": pname,
            "replaced": old_ver,
            "display_name": str(meta.get("display_name") or pname),
            "version": str(meta.get("version") or "1.0.0"),
            "files": len(files),
            "loaded": bool(ctx and ctx.loaded),
            "error": (ctx.error if ctx else ""),
            "message": f"已安装 {meta.get('display_name') or pname}",
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _plugin_dst(pname):
    r"""把插件名变成插件目录下的路径，顺便把路径穿越挡住。

    返回 (路径, 错误信息)；路径为 None 表示被拒。

    为什么需要这个（真实漏洞）：
      原来的校验是 `re.match(r"^[\w.\-]{1,48}$", pname)` —— 里面的点是**字面量**，
      所以 ".." 和 "." 都能通过。而 `os.path.join(PLUGIN_DIR, "..")` 会指到
      `data/`（PLUGIN_DIR 是 data/plugins）。接下来那条"已装过就先备份旧的"
      会 `os.rename(data/, data.bak_xxx/)`，再用 copytree 把插件内容铺成 data/ ——
      **数据库、配置、其他插件目录全被挪走**。

    这里两道防线：
      1. 名字本身：纯点的名字（"."、".."、"..."）直接拒
      2. 兜底：解析出来的绝对路径必须是 PLUGIN_DIR 的直接子目录。
         以后就算改了正则、或者名字从别的地方来，这道都能挡住。
    """
    name = str(pname or "").strip()
    if not name:
        return None, "插件名不能为空"
    if not re.match(r"^[\w.\-]{1,48}$", name):
        return None, f"插件名不合法：{pname!r}（只能用字母数字下划线点横线，48 字以内）"
    if set(name) <= {"."}:
        # "." / ".." / "..." 之类 —— join 出来会指到插件目录本身或它的上级
        return None, f"插件名不合法：{pname!r}（不能是纯点）"
    # 正则只保证"字符合法"，不管点的位置 —— 像 "a..b"、"a." 都能过。
    # 下面那道 os.path.dirname 兜底确实拦得住（a..b join 出来还是直接
    # 子目录），但正则本身不该放过这种名字：哪天有人把兜底删了，
    # 它就成漏洞了。这里把点收紧。
    if ".." in name or name.startswith(".") or name.endswith("."):
        return None, (f"插件名不合法：{pname!r}"
                      "（不能有连续的点，也不能以点开头或结尾）")
    try:
        base = os.path.abspath(PLUGIN_DIR)
        dst = os.path.abspath(os.path.join(PLUGIN_DIR, name))
        if os.path.dirname(dst) != base or os.path.basename(dst) != name:
            return None, f"插件名会把安装路径指到插件目录外面：{pname!r}"
    except Exception as e:
        return None, f"插件名解析失败：{type(e).__name__}: {e}"
    return os.path.join(PLUGIN_DIR, name), ""


def uninstall_plugin(name):
    """卸载插件：删目录 + 清状态。返回 {ok, message}。"""
    key = str(name or "").strip()
    if not key:
        return {"ok": False, "error": "插件名不能为空"}
    # target 是从 os.listdir 里挑出来的，天然在插件目录内，
    # 这里只是把纯点名字一并拒掉，跟安装那边保持一致
    if not re.match(r"^[\w.\-]{1,64}$", key) or set(key) <= {"."}:
        return {"ok": False, "error": "插件名不合法"}
    # 找到实际目录（可能 name 和文件夹名不一致）
    target = None
    for d in os.listdir(PLUGIN_DIR) if os.path.isdir(PLUGIN_DIR) else []:
        p = os.path.join(PLUGIN_DIR, d)
        if not os.path.isdir(p):
            continue
        if d == key:
            target = p
            break
        m, _ = _read_plugin_meta(p)
        if m and str(m.get("name")) == key:
            target = p
            break
    if target is None:
        return {"ok": False, "error": f"找不到插件 {key}"}
    # 这里以前是 `with _MARKET_LOCK: pass` —— 拿到锁什么都不做，纯死代码，
    # 还会让人误以为卸载有并发保护。_MARKET_LOCK 管的是市场缓存，
    # 跟插件装卸没关系；而这个函数只在事件循环里被管理接口调用，不存在竞争。
    unload_plugin(key)
    for ctx_name, ctx in list(PLUGINS.items()):
        if os.path.basename(ctx.dir) == os.path.basename(target):
            PLUGINS.pop(ctx_name, None)
    try:
        shutil.rmtree(target)
    except Exception as e:
        return {"ok": False, "error": f"删除失败：{type(e).__name__}: {e}"}
    try:
        st = _PLUGIN_STATE.setdefault("installed", {})
        st.pop(key, None)
        dis = _PLUGIN_STATE.get("disabled") or []
        if os.path.basename(target) in dis or key in dis:
            _PLUGIN_STATE["disabled"] = [x for x in dis
                                         if x not in (key, os.path.basename(target))]
        _save_plugin_state()
    except Exception:
        pass
    reload_all_plugins()
    return {"ok": True, "message": f"已卸载 {key}"}


def market_with_status():
    """市场索引 + 每个插件的本地状态（已装 / 可更新）。"""
    with _MARKET_LOCK:
        items = list(_PLUGIN_MARKET.get("items") or [])
        err = _PLUGIN_MARKET.get("error") or ""
        ts = _PLUGIN_MARKET.get("ts") or 0
    local = {}
    for ctx in PLUGINS.values():
        local[ctx.name] = {"version": ctx.version, "loaded": ctx.loaded,
                           "enabled": ctx.enabled, "error": ctx.error}
        local[os.path.basename(ctx.dir)] = local[ctx.name]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        d = dict(it)
        nm = str(d.get("name") or "")
        cur = local.get(nm) or {}
        d["installed"] = bool(cur)
        d["installed_version"] = cur.get("version") or ""
        d["loaded"] = bool(cur.get("loaded"))
        d["enabled"] = bool(cur.get("enabled"))
        d["load_error"] = cur.get("error") or ""
        mv = d.get("version") or ""
        d["updatable"] = bool(cur) and _ver_tuple(mv) > _ver_tuple(cur.get("version"))
        out.append(d)
    return {"ok": True, "items": out, "error": err, "ts": ts,
            "count": len(out), "source": PLUGIN_MARKET_URL}


_load_market_cache()



# 忘了 return 只提醒一次，别每次消息都刷屏
_NORETURN_WARNED = set()


async def plugin_dispatch_command(bot, event, cmd):
    """给插件一次处理命令的机会。返回 True 表示已处理。"""
    for ctx in sorted(PLUGINS.values(), key=lambda c: c.order):
        if not ctx.loaded or not ctx.enabled:
            continue
        hit = ctx.commands.get(cmd)
        if not hit:
            # 前缀匹配：注册了「查询游戏」且 prefix=True 时，
            # 「查询游戏 鸣潮」也能触发（cmd 原样传给 handler，自己取参数）
            for _k, _v in ctx.commands.items():
                if not _v[2] or not cmd.startswith(_k):
                    continue
                _rest = cmd[len(_k):]
                if _rest[:1] in (" ", "\t", "\u3000"):
                    hit = _v
                    break
        if not hit:
            continue
        fn = hit[0]
        try:
            r = fn(event, cmd)
            if asyncio.iscoroutine(r):
                r = await r
            if r:
                return True
            if r is None:
                # 处理函数没写 return —— 这是个特别难查的错：
                # 消息可能发出去了，但 Nekolyra 认为"没人处理"，继续往下传，
                # 结果被内置逻辑或 AI 对话接走，重复回一遍。
                key = (ctx.name, cmd)
                if key not in _NORETURN_WARNED:
                    _NORETURN_WARNED.add(key)
                    log("warn", "插件",
                        f"{ctx.display_name} 的命令「{cmd}」处理函数没有 return True/False"
                        f"（返回了 None）。消息会继续往下传，可能被内置逻辑或 AI 对话接走。"
                        f"处理完请 return True。")
        except Exception as e:
            log("error", "插件",
                f"{ctx.display_name} 处理「{cmd}」出错：{type(e).__name__}: {e}")
    return False


async def plugin_dispatch_message(bot, event, text):
    """给插件一次截获全量消息的机会。返回 True 表示吞掉。"""
    for ctx in sorted(PLUGINS.values(), key=lambda c: c.order):
        if not ctx.loaded or not ctx.enabled:
            continue
        for fn in ctx.message_hooks:
            try:
                r = fn(event, text)
                if asyncio.iscoroutine(r):
                    r = await r
                if r:
                    return True
                if r is None:
                    key = (ctx.name, "__hook__")
                    if key not in _NORETURN_WARNED:
                        _NORETURN_WARNED.add(key)
                        log("warn", "插件",
                            f"{ctx.display_name} 的消息钩子没有 return True/False"
                            f"（返回了 None）。不打算处理这条消息就应该 return False。")
            except Exception as e:
                log("error", "插件",
                    f"{ctx.display_name} 消息钩子出错：{type(e).__name__}: {e}")
    return False


# 插件在模块级加载：管理界面可能在机器人连上之前就被打开，
# 那时也要能看到插件列表和插件页。
try:
    load_all_plugins()
except Exception as _e:
    log("error", "插件", f"插件加载阶段异常：{type(_e).__name__}: {_e}")


def to_message(*parts):
    result = Message()
    for p in parts:
        if p is None:
            continue
        try:
            if isinstance(p, str):
                if p:
                    result.append(MessageSegment.text(p))
            elif isinstance(p, MessageSegment):
                result.append(p)
            elif isinstance(p, Message):
                result.extend(p)
            elif isinstance(p, (list, tuple)):
                for item in p:
                    if item is None:
                        continue
                    if isinstance(item, str):
                        if item:
                            result.append(MessageSegment.text(item))
                    elif isinstance(item, MessageSegment):
                        result.append(item)
                    elif isinstance(item, Message):
                        result.extend(item)
                    else:
                        result.append(MessageSegment.text(str(item)))
            else:
                result.append(MessageSegment.text(str(p)))
        except Exception as e:
            log("warn", "构建消息", f"跳过一段：{type(e).__name__}: {e}")
    return result


async def safe_reply(bot, event, *parts):
    content = to_message(*parts)
    if len(content) == 0:
        content = to_message("（空）")
    try:
        full = Message([MessageSegment.reply(event.message_id)])
        full.extend(content)
        await bot.send(event, full)
        return True
    except Exception as e1:
        log("warn", "发送", f"reply 失败({type(e1).__name__}: {e1})，改用普通发送")
        try:
            await bot.send(event, content)
            return True
        except Exception as e2:
            log("error", "发送", f"普通发送也失败: {type(e2).__name__}: {e2}")
            return False


NICK_CACHE_TTL = 600
_nick_cache = {}
_nick_cache_lock = threading.Lock()


async def safe_nick(bot, gid, uid, refresh=False):
    """取群名片/昵称。带缓存，避免同一个人刷屏时反复调接口。"""
    key = (str(getattr(bot, "self_id", "")), int(gid), int(uid))
    now = time.time()
    if not refresh:
        with _nick_cache_lock:
            hit = _nick_cache.get(key)
            if hit and now - hit[1] < NICK_CACHE_TTL:
                return hit[0]
    try:
        info = await bot.get_group_member_info(group_id=gid, user_id=uid, no_cache=refresh)
        name = (info.get("card") or "").strip() or (info.get("nickname") or "").strip() or str(uid)
    except Exception:
        name = str(uid)
    with _nick_cache_lock:
        if len(_nick_cache) > 3000:
            _nick_cache.clear()
        _nick_cache[key] = (name, now)
    return name


def get_any_bot():
    try:
        bots = nonebot.get_bots()
        if not bots:
            return None
        return list(bots.values())[0]
    except Exception:
        return None


class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass
        return c

    def checkpoint(self):
        try:
            with self._lock:
                with self._conn() as c:
                    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            log_startup(f"数据库 checkpoint 失败: {type(e).__name__}: {e}")

    def close(self):
        self.checkpoint()

    def _init(self):
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS blacklist(
                user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
                nickname TEXT, reason TEXT, added_at TEXT,
                PRIMARY KEY(user_id, group_id))""")
            c.execute("""CREATE TABLE IF NOT EXISTS memory(
                id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL,
                content TEXT NOT NULL, category TEXT DEFAULT '其他',
                importance INTEGER DEFAULT 3, created_at TEXT NOT NULL)""")
            # 新增：用户级记忆。scope='user' 时 user_id 是归属人；
            # scope='group'（或 user_id=0）时是原来的群级记忆。
            # 老库没有这两列，用 ALTER 补上，不重建表（不丢数据）。
            _mem_cols = [r[1] for r in c.execute("PRAGMA table_info(memory)").fetchall()]
            if "user_id" not in _mem_cols:
                c.execute("ALTER TABLE memory ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
                log("info", "数据库", "memory 表新增 user_id 列")
            if "scope" not in _mem_cols:
                c.execute("ALTER TABLE memory ADD COLUMN scope TEXT NOT NULL DEFAULT 'group'")
                log("info", "数据库", "memory 表新增 scope 列")
            c.execute("CREATE INDEX IF NOT EXISTS idx_mem ON memory(group_id, id DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_mem_user ON memory(group_id, user_id, id DESC)")
            # 聊天记录：群聊和私聊都存，供管理界面按群/按人查看
            c.execute("""CREATE TABLE IF NOT EXISTS chat_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL DEFAULT 0,
                user_id INTEGER NOT NULL DEFAULT 0,
                nickname TEXT,
                text TEXT NOT NULL,
                is_bot INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_log_g ON chat_log(group_id, id DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_log_u ON chat_log(user_id, id DESC)")
            c.execute("""CREATE TABLE IF NOT EXISTS points(
                user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
                points INTEGER DEFAULT 0, sign_count INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0, best_streak INTEGER DEFAULT 0,
                last_sign_date TEXT, nickname TEXT,
                PRIMARY KEY (user_id, group_id))""")
            c.execute("""CREATE TABLE IF NOT EXISTS activity(
                user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
                last_active TEXT, msg_count INTEGER DEFAULT 0, nickname TEXT,
                PRIMARY KEY (user_id, group_id))""")
            c.execute("""CREATE TABLE IF NOT EXISTS daily_activity(
                user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
                date TEXT NOT NULL, msg_count INTEGER DEFAULT 0, nickname TEXT,
                PRIMARY KEY (user_id, group_id, date))""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_daily ON daily_activity(group_id, date)")
            # 知识库：group_id = 0 表示"全局知识"（所有群都能命中）
            c.execute("""CREATE TABLE IF NOT EXISTS kb(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL DEFAULT 0,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                tags TEXT DEFAULT '',
                author_id INTEGER DEFAULT 0,
                author_name TEXT DEFAULT '',
                hits INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                folder TEXT DEFAULT '')""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_kb ON kb(group_id, id DESC)")
            # 空文件夹也要能被记住，所以单独一张表存文件夹路径
            c.execute("""CREATE TABLE IF NOT EXISTS kb_folders(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL DEFAULT 0,
                path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(group_id, path))""")
            # 老库补 folder 列（可重复执行）
            try:
                kb_cols = [r[1] for r in c.execute("PRAGMA table_info(kb)").fetchall()]
                if kb_cols and "folder" not in kb_cols:
                    log("warn", "数据库", "检测到旧 kb 表，正在补 folder 列...")
                    c.execute("ALTER TABLE kb ADD COLUMN folder TEXT DEFAULT ''")
                c.execute("CREATE INDEX IF NOT EXISTS idx_kb_folder ON kb(group_id, folder)")
            except Exception as e:
                log("warn", "数据库", f"检查 kb 表出错：{e}")
            c.commit()

    def bl_add(self, u, g, n="", r="主动退群"):
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO blacklist VALUES(?,?,?,?,?)",
                      (u, g, n, r, datetime.now().isoformat()))
            c.commit()

    def bl_check(self, u, g):
        with self._conn() as c:
            return c.execute("SELECT 1 FROM blacklist WHERE user_id=? AND group_id=?",
                            (u, g)).fetchone() is not None

    def bl_remove(self, u, g):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM blacklist WHERE user_id=? AND group_id=?", (u, g))
            c.commit()

    def bl_list_group(self, g):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM blacklist WHERE group_id=? ORDER BY added_at DESC", (g,))]

    def bl_list_all(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM blacklist ORDER BY added_at DESC")]

    def bl_count(self):
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM blacklist").fetchone()[0]

    def mem_add(self, g, content, cat="其他", imp=3, uid=0, uname="", scope="group"):
        """写一条记忆。

        scope="group" -> 群级记忆（谁能看到都行）
        scope="user"  -> 用户级记忆，按 (群, 用户) 隔离；私聊时群固定为 0
        """
        scope = "user" if str(scope) == "user" and int(uid or 0) else "group"
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO memory(group_id,user_id,scope,content,category,"
                      "importance,created_at) VALUES(?,?,?,?,?,?,?)",
                      (int(g), int(uid or 0), scope, content, cat, imp,
                       datetime.now().isoformat()))
            c.commit()

    def mem_recent(self, g, limit=3):
        """群级记忆（不含用户级）。"""
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT content,category,importance,created_at FROM memory "
                "WHERE group_id=? AND scope='group' ORDER BY id DESC LIMIT ?",
                (g, limit))]

    def mem_recent_user(self, g, uid, limit=3):
        """某个用户在某个群里的专属记忆（按群隔离）。"""
        if not uid:
            return []
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT content,category,importance,created_at FROM memory "
                "WHERE group_id=? AND user_id=? AND scope='user' "
                "ORDER BY importance DESC, id DESC LIMIT ?",
                (g, int(uid), limit))]

    # ---------------- 聊天记录 ----------------
    def log_add(self, g, uid, nick, text, is_bot=False):
        if not cfg_bool("chatlog_enabled", True):
            return
        txt = str(text or "").strip()
        if not txt:
            return
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO chat_log(group_id,user_id,nickname,text,is_bot,"
                      "created_at) VALUES(?,?,?,?,?,?)",
                      (int(g or 0), int(uid or 0), str(nick or ""), txt[:2000],
                       1 if is_bot else 0, datetime.now().isoformat()))
            c.commit()

    def log_list(self, g=None, uid=None, limit=200, keyword="", before_id=0):
        """按群或按人查聊天记录。g/uid 传 None 表示不限制。"""
        sql = ["SELECT id,group_id,user_id,nickname,text,is_bot,created_at FROM chat_log WHERE 1=1"]
        args = []
        if g is not None:
            sql.append("AND group_id=?")
            args.append(int(g))
        if uid is not None:
            sql.append("AND user_id=?")
            args.append(int(uid))
        if keyword:
            sql.append("AND text LIKE ?")
            args.append(f"%{keyword}%")
        if before_id:
            sql.append("AND id<?")
            args.append(int(before_id))
        sql.append("ORDER BY id DESC LIMIT ?")
        args.append(int(limit))
        with self._conn() as c:
            return [dict(r) for r in c.execute(" ".join(sql), args)]

    def log_count(self, g=None, uid=None):
        sql = ["SELECT COUNT(*) FROM chat_log WHERE 1=1"]
        args = []
        if g is not None:
            sql.append("AND group_id=?")
            args.append(int(g))
        if uid is not None:
            sql.append("AND user_id=?")
            args.append(int(uid))
        with self._conn() as c:
            return c.execute(" ".join(sql), args).fetchone()[0]

    def log_groups(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT group_id, COUNT(*) as cnt, MAX(created_at) as last "
                "FROM chat_log GROUP BY group_id ORDER BY MAX(id) DESC")]

    def log_users(self, g=None, limit=300):
        sql = ("SELECT user_id, nickname, COUNT(*) as cnt, MAX(created_at) as last "
               "FROM chat_log WHERE is_bot=0")
        args = []
        if g is not None:
            sql += " AND group_id=?"
            args.append(int(g))
        sql += " GROUP BY user_id ORDER BY MAX(id) DESC LIMIT ?"
        args.append(int(limit))
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def log_clear(self, g=None, uid=None):
        sql = ["DELETE FROM chat_log WHERE 1=1"]
        args = []
        if g is not None:
            sql.append("AND group_id=?")
            args.append(int(g))
        if uid is not None:
            sql.append("AND user_id=?")
            args.append(int(uid))
        with self._lock, self._conn() as c:
            cur = c.execute(" ".join(sql), args)
            c.commit()
            return cur.rowcount or 0

    def log_prune(self, keep_days=30):
        """删掉太老的记录，防止库无限膨胀。"""
        try:
            # 文件头是 from datetime import datetime, date, timedelta ——
            # datetime 是**类**不是模块，datetime.timedelta 根本不存在。
            # 原来这里每次进 try 就抛 AttributeError，被
            # `except Exception: return 0` 吞掉，函数永远返回 0：
            # 启动日志一直打"共 N 条，无需清理"，实际一行没删，
            # chat_log 表无限膨胀。文件里另外三处（daily_backup_task /
            # act_recent / backup_db）用的都是正确的 timedelta(days=...)。
            cutoff = (datetime.now() - timedelta(days=int(keep_days))).isoformat()
        except Exception as e:
            # 别再静默 return 0 了 —— 就是它让上面那个 bug 藏了这么久。
            log("warn", "聊天记录",
                f"计算保留日期失败: {type(e).__name__}: {e}")
            return 0
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM chat_log WHERE created_at<?", (cutoff,))
            c.commit()
            return cur.rowcount or 0

    # ---------------- 用户画像 ----------------
    def user_facts(self, g, uid):
        """汇总 bot 已经掌握的关于这个人的事实（用于注入提示词）。"""
        out = {}
        with self._conn() as c:
            if g:
                r = c.execute("SELECT nickname,msg_count,last_active FROM activity "
                              "WHERE user_id=? AND group_id=?", (int(uid), int(g))).fetchone()
                if r:
                    out["nickname"], out["msg_count"], out["last_active"] = r[0], r[1], r[2]
                p = c.execute("SELECT points,sign_count,streak FROM points "
                              "WHERE user_id=? AND group_id=?", (int(uid), int(g))).fetchone()
                if p:
                    out["points"], out["sign_count"], out["streak"] = p[0], p[1], p[2]
                d = c.execute("SELECT COUNT(*), MIN(date) FROM daily_activity "
                              "WHERE user_id=? AND group_id=?", (int(uid), int(g))).fetchone()
                if d and d[0]:
                    out["active_days"], out["first_date"] = d[0], d[1]
            # 跨群总发言（说明是老熟人）
            t = c.execute("SELECT SUM(msg_count) FROM activity WHERE user_id=?",
                          (int(uid),)).fetchone()
            if t and t[0]:
                out["total_msgs"] = t[0]
            gn = c.execute("SELECT COUNT(DISTINCT group_id) FROM activity WHERE user_id=?",
                           (int(uid),)).fetchone()
            if gn and gn[0]:
                out["group_count"] = gn[0]
            # 和这个人聊过多少次
            lc = c.execute("SELECT COUNT(*) FROM chat_log WHERE user_id=? AND is_bot=1",
                           (int(uid),)).fetchone()
            if lc and lc[0]:
                out["replied"] = lc[0]
        return out

    def mem_count(self, g=None):
        with self._conn() as c:
            if g is None:
                return c.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
            return c.execute("SELECT COUNT(*) FROM memory WHERE group_id=?", (g,)).fetchone()[0]

    def mem_list_group(self, g, limit=100):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT id,content,category,importance,created_at,user_id,scope "
                "FROM memory WHERE group_id=? ORDER BY id DESC LIMIT ?", (g, limit))]

    def mem_clear_group(self, g):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM memory WHERE group_id=?", (g,))
            c.commit()

    def mem_clear_user(self, g, uid):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM memory WHERE group_id=? AND user_id=? AND scope='user'",
                      (int(g), int(uid)))
            c.commit()

    def mem_delete_one(self, mid):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM memory WHERE id=?", (mid,))
            c.commit()

    def mem_users(self):
        """所有有专属记忆的用户，跨群聚合（用于"按人"视图）。"""
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT user_id, COUNT(*) as cnt, COUNT(DISTINCT group_id) as gcnt, "
                "MAX(id) as last_id FROM memory WHERE scope='user' AND user_id>0 "
                "GROUP BY user_id ORDER BY MAX(id) DESC")]

    def mem_list_by_user(self, uid, limit=300):
        """某个人在所有群的记忆（用于"按人"视图）。"""
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT id,group_id,user_id,content,category,importance,created_at,scope "
                "FROM memory WHERE scope='user' AND user_id=? "
                "ORDER BY group_id, importance DESC, id DESC LIMIT ?",
                (int(uid), int(limit)))]

    def mem_clear_user_all(self, uid):
        """删掉某人在所有群的专属记忆（群级记忆不动）。返回删除条数。"""
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM memory WHERE scope='user' AND user_id=?",
                            (int(uid),))
            c.commit()
            return cur.rowcount or 0

    def mem_groups_of_user(self, uid):
        """某人分布在哪些群（删之前给用户看清楚）。"""
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT group_id, COUNT(*) as cnt FROM memory "
                "WHERE scope='user' AND user_id=? GROUP BY group_id ORDER BY group_id",
                (int(uid),))]

    def mem_all_groups(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT group_id, COUNT(*) as cnt, "
                "SUM(CASE WHEN scope='user' THEN 1 ELSE 0 END) as user_cnt "
                "FROM memory GROUP BY group_id ORDER BY MAX(id) DESC")]

    # ---------------- 知识库 ----------------
    def kb_add(self, g, question, answer, tags="", uid=0, uname="", folder=""):
        now = datetime.now().isoformat()
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO kb(group_id,question,answer,tags,author_id,author_name,"
                "hits,created_at,updated_at,folder) VALUES(?,?,?,?,?,?,0,?,?,?)",
                (int(g), question.strip(), answer.strip(), tags.strip(), int(uid),
                 uname, now, now, (folder or "").strip()))
            c.commit()
            return cur.lastrowid

    def kb_update_folder(self, kid, folder):
        with self._lock, self._conn() as c:
            cur = c.execute("UPDATE kb SET folder=?, updated_at=? WHERE id=?",
                            ((folder or "").strip(), datetime.now().isoformat(), int(kid)))
            c.commit()
            return cur.rowcount > 0

    def kb_set_folders_bulk(self, ids, folder):
        now = datetime.now().isoformat()
        n = 0
        with self._lock, self._conn() as c:
            for kid in ids:
                try:
                    n += c.execute("UPDATE kb SET folder=?, updated_at=? WHERE id=?",
                                   ((folder or "").strip(), now, int(kid))).rowcount
                except (TypeError, ValueError):
                    continue
            c.commit()
        return n

    def kb_folder_list(self, g=None):
        """实际被条目使用的文件夹路径 + 显式创建的空文件夹。"""
        with self._conn() as c:
            if g is None:
                used = [r[0] for r in c.execute(
                    "SELECT DISTINCT folder FROM kb WHERE folder IS NOT NULL AND folder<>''")]
                created = [r[0] for r in c.execute("SELECT path FROM kb_folders")]
            else:
                used = [r[0] for r in c.execute(
                    "SELECT DISTINCT folder FROM kb WHERE folder IS NOT NULL AND folder<>'' "
                    "AND group_id IN (0,?)", (int(g),))]
                created = [r[0] for r in c.execute(
                    "SELECT path FROM kb_folders WHERE group_id IN (0,?)", (int(g),))]
        return sorted(set(used) | set(created))

    def kb_folder_create(self, g, path):
        with self._lock, self._conn() as c:
            c.execute("INSERT OR IGNORE INTO kb_folders(group_id,path,created_at) "
                      "VALUES(?,?,?)", (int(g), path.strip(), datetime.now().isoformat()))
            c.commit()

    def kb_folder_delete(self, path, g=None):
        """删除文件夹定义，并把里面的条目移回根目录（不删条目）。"""
        with self._lock, self._conn() as c:
            if g is None:
                c.execute("DELETE FROM kb_folders WHERE path=?", (path,))
                c.execute("UPDATE kb SET folder='' WHERE folder=?", (path,))
            else:
                c.execute("DELETE FROM kb_folders WHERE path=? AND group_id IN (0,?)",
                          (path, int(g)))
                c.execute("UPDATE kb SET folder='' WHERE folder=? AND group_id IN (0,?)",
                          (path, int(g)))
            c.commit()

    def kb_get(self, kid):
        with self._conn() as c:
            row = c.execute("SELECT * FROM kb WHERE id=?", (int(kid),)).fetchone()
            return dict(row) if row else None

    def kb_update(self, kid, question=None, answer=None, tags=None, group_id=None):
        sets, args = [], []
        if question is not None:
            sets.append("question=?"); args.append(question.strip())
        if answer is not None:
            sets.append("answer=?"); args.append(answer.strip())
        if tags is not None:
            sets.append("tags=?"); args.append(tags.strip())
        if group_id is not None:
            sets.append("group_id=?"); args.append(int(group_id))
        if not sets:
            return False
        sets.append("updated_at=?"); args.append(datetime.now().isoformat())
        args.append(int(kid))
        with self._lock, self._conn() as c:
            cur = c.execute(f"UPDATE kb SET {','.join(sets)} WHERE id=?", tuple(args))
            c.commit()
            return cur.rowcount > 0

    def kb_delete(self, kid):
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM kb WHERE id=?", (int(kid),))
            c.commit()
            return cur.rowcount > 0

    def kb_touch(self, kid):
        """命中计数 +1"""
        try:
            with self._lock, self._conn() as c:
                c.execute("UPDATE kb SET hits=hits+1 WHERE id=?", (int(kid),))
                c.commit()
        except Exception:
            pass

    def kb_count(self, g=None):
        with self._conn() as c:
            if g is None:
                return c.execute("SELECT COUNT(*) FROM kb").fetchone()[0]
            return c.execute("SELECT COUNT(*) FROM kb WHERE group_id IN (0,?)",
                             (int(g),)).fetchone()[0]

    def kb_list(self, g=None, limit=200):
        """g 为 None 列出全部；否则列出该群 + 全局条目"""
        with self._conn() as c:
            if g is None:
                rows = c.execute("SELECT * FROM kb ORDER BY group_id, id DESC LIMIT ?",
                                 (int(limit),)).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM kb WHERE group_id IN (0,?) ORDER BY group_id, id DESC LIMIT ?",
                    (int(g), int(limit))).fetchall()
            return [dict(r) for r in rows]

    def kb_groups(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT group_id, COUNT(*) as cnt FROM kb GROUP BY group_id "
                "ORDER BY group_id")]

    def kb_clear_group(self, g):
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM kb WHERE group_id=?", (int(g),))
            c.commit()
            return cur.rowcount

    def sign_get(self, u, g):
        with self._conn() as c:
            row = c.execute("SELECT * FROM points WHERE user_id=? AND group_id=?",
                            (u, g)).fetchone()
            return dict(row) if row else None

    def sign_do(self, u, g, nick, points_added, new_streak, today_str):
        with self._conn() as c:
            existing = c.execute("SELECT * FROM points WHERE user_id=? AND group_id=?",
                                 (u, g)).fetchone()
            if existing:
                new_points = existing["points"] + points_added
                new_count = existing["sign_count"] + 1
                best = max(existing["best_streak"] or 0, new_streak)
                c.execute("""UPDATE points SET points=?, sign_count=?, streak=?,
                             best_streak=?, last_sign_date=?, nickname=?
                             WHERE user_id=? AND group_id=?""",
                          (new_points, new_count, new_streak, best, today_str, nick, u, g))
            else:
                new_points = points_added
                c.execute("""INSERT INTO points(user_id,group_id,points,sign_count,
                             streak,best_streak,last_sign_date,nickname)
                             VALUES(?,?,?,?,?,?,?,?)""",
                          (u, g, points_added, 1, new_streak, new_streak, today_str, nick))
            c.commit()
            return new_points

    def points_spend(self, u, g, amount):
        amount = int(amount or 0)
        if amount <= 0:
            info = self.sign_get(u, g)
            return True, (info["points"] if info else 0)
        with self._lock, self._conn() as c:
            cur = c.execute(
                "UPDATE points SET points=points-? "
                "WHERE user_id=? AND group_id=? AND points>=?",
                (amount, u, g, amount))
            c.commit()
            if cur.rowcount:
                row = c.execute("SELECT points FROM points WHERE user_id=? AND group_id=?",
                                (u, g)).fetchone()
                return True, (row["points"] if row else 0)
            row = c.execute("SELECT points FROM points WHERE user_id=? AND group_id=?",
                            (u, g)).fetchone()
            return False, (row["points"] if row else 0)

    def points_refund(self, u, g, amount):
        amount = int(amount or 0)
        if amount <= 0:
            return
        with self._lock, self._conn() as c:
            c.execute("UPDATE points SET points=points+? WHERE user_id=? AND group_id=?",
                      (amount, u, g))
            c.commit()

    def sign_do_atomic(self, u, g, nick, base, bonus_per, bonus_cap, today_str):
        with self._lock:
            c = sqlite3.connect(self.db_path, timeout=10)
            c.row_factory = sqlite3.Row
            try:
                c.execute("PRAGMA busy_timeout=5000")
                c.execute("BEGIN IMMEDIATE")
                row = c.execute("SELECT * FROM points WHERE user_id=? AND group_id=?",
                                (u, g)).fetchone()
                if row and row["last_sign_date"] == today_str:
                    return {"ok": False, "already": True,
                            "total": row["points"] or 0,
                            "streak": row["streak"] or 0,
                            "sign_count": row["sign_count"] or 0}

                cur_streak = (row["streak"] or 0) if row else 0
                last_date = row["last_sign_date"] if row else None
                new_streak = 1
                if last_date:
                    try:
                        if (date.fromisoformat(today_str) - date.fromisoformat(last_date)).days == 1:
                            new_streak = cur_streak + 1
                    except Exception:
                        new_streak = 1

                bonus = min(max(0, (new_streak - 1)) * bonus_per, bonus_cap)
                added = base + bonus
                if row:
                    total = (row["points"] or 0) + added
                    c.execute("""UPDATE points SET points=?, sign_count=sign_count+1,
                                 streak=?, best_streak=?, last_sign_date=?, nickname=?
                                 WHERE user_id=? AND group_id=?""",
                              (total, new_streak, max(row["best_streak"] or 0, new_streak),
                               today_str, nick, u, g))
                else:
                    total = added
                    c.execute("""INSERT INTO points(user_id,group_id,points,sign_count,
                                 streak,best_streak,last_sign_date,nickname)
                                 VALUES(?,?,?,?,?,?,?,?)""",
                              (u, g, added, 1, new_streak, new_streak, today_str, nick))
                c.commit()
                return {"ok": True, "already": False, "points_added": added,
                        "bonus": bonus, "total": total, "streak": new_streak}
            except Exception:
                try:
                    c.rollback()
                except Exception:
                    pass
                raise
            finally:
                c.close()

    def sign_rank(self, g, limit=10):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT user_id, nickname, points, sign_count, streak "
                "FROM points WHERE group_id=? ORDER BY points DESC LIMIT ?",
                (g, limit))]

    def sign_rank_all(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT group_id, COUNT(*) as cnt, SUM(points) as total "
                "FROM points GROUP BY group_id ORDER BY total DESC")]

    def sign_clear_group(self, g):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM points WHERE group_id=?", (g,))
            c.commit()

    def act_touch(self, u, g, nick):
        now = datetime.now().isoformat()
        today_str = date.today().isoformat()
        with self._lock, self._conn() as c:
            c.execute("""INSERT INTO activity(user_id,group_id,last_active,msg_count,nickname)
                         VALUES(?,?,?,1,?)
                         ON CONFLICT(user_id,group_id) DO UPDATE SET
                           last_active=excluded.last_active,
                           msg_count=activity.msg_count+1,
                           nickname=excluded.nickname""",
                      (u, g, now, nick))
            c.execute("""INSERT INTO daily_activity(user_id,group_id,date,msg_count,nickname)
                         VALUES(?,?,?,1,?)
                         ON CONFLICT(user_id,group_id,date) DO UPDATE SET
                           msg_count=daily_activity.msg_count+1,
                           nickname=excluded.nickname""",
                      (u, g, today_str, nick))
            c.commit()

    def act_recent(self, g, days=7):
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM activity WHERE group_id=? AND last_active>=? "
                "ORDER BY last_active DESC", (g, cutoff))]

    def daily_rank(self, g, date_str, limit=10):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT user_id, nickname, msg_count FROM daily_activity "
                "WHERE group_id=? AND date=? ORDER BY msg_count DESC LIMIT ?",
                (g, date_str, limit))]

    def daily_summary(self, g, date_str):
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) as users, COALESCE(SUM(msg_count),0) as total "
                "FROM daily_activity WHERE group_id=? AND date=?",
                (g, date_str)).fetchone()
            return {"users": row["users"] or 0, "total": row["total"] or 0}


    def nick_lookup(self, u, g):
        try:
            with self._conn() as c:
                row = c.execute(
                    "SELECT nickname FROM activity WHERE user_id=? AND group_id=? "
                    "AND nickname IS NOT NULL AND nickname != '' LIMIT 1",
                    (u, g)).fetchone()
                if row and row["nickname"]:
                    return row["nickname"]
                row = c.execute(
                    "SELECT nickname FROM points WHERE user_id=? AND group_id=? "
                    "AND nickname IS NOT NULL AND nickname != '' LIMIT 1",
                    (u, g)).fetchone()
                if row and row["nickname"]:
                    return row["nickname"]
        except Exception:
            pass
        return None


DB = Database(DB_PATH)


class AIHandler:
    """AI 后端：支持流式接收，主通道/辅助通道分离限速"""

    def __init__(self):
        self.backend = "deepseek"
        self.host = ""
        self.model = ""
        self.api_key = ""
        self.enabled = False
        self._last_call = 0.0
        self._last_aux_call = 0.0
        self.reload()

    def reload(self):
        backend = cfg_str("ai_backend", "deepseek").lower().strip()
        if backend not in ("ollama", "deepseek"):
            backend = "deepseek"
        self.backend = backend

        if backend == "deepseek":
            self.host = cfg_str("deepseek_api_host",
                                "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
            self.model = cfg_str("deepseek_model", "glm-4.7-flash").strip() or "glm-4.7-flash"
            self.api_key = cfg_str("deepseek_api_key").strip()
        else:
            self.host = cfg_str("ollama_host", "http://localhost:11434").rstrip("/")
            self.model = cfg_str("ai_model", "qwen3:4b").strip() or "qwen3:4b"
            self.api_key = cfg_str("ollama_api_key", "ollama").strip() or "ollama"

        self.enabled = cfg_bool("ai_enabled", True) and bool(self.host)

    def endpoint(self):
        base = self.host
        if base.endswith("/v1") or base.endswith("/v4"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def models_endpoint(self):
        base = self.host
        if base.endswith("/v1") or base.endswith("/v4"):
            return f"{base}/models"
        return f"{base}/v1/models"

    async def _wait_gap(self, use_gap):
        if use_gap:
            lo = cfg_float("ai_min_gap", 0.0)
            hi = cfg_float("ai_max_gap", 0.0)
            gap = random.uniform(min(lo, hi), max(lo, hi))
            if gap > 0:
                elapsed = time.time() - self._last_call
                if elapsed < gap:
                    await asyncio.sleep(gap - elapsed)
            self._last_call = time.time()
        else:
            aux_gap = 1.0
            elapsed = time.time() - self._last_aux_call
            if elapsed < aux_gap:
                await asyncio.sleep(aux_gap - elapsed)
            self._last_aux_call = time.time()

    @staticmethod
    def _has_user_message(messages):
        for m in messages:
            if m.get("role") != "user":
                continue
            content = m.get("content")
            if isinstance(content, str):
                if content.strip():
                    return True
            elif isinstance(content, (list, tuple)) and content:
                return True
        return False

    async def raw(self, messages, timeout=120.0, use_gap=True):
        if not self.enabled:
            return None

        await self._wait_gap(use_gap)

        if not self._has_user_message(messages):
            log("warn", "AI", "消息列表中没有有效的用户消息，跳过调用")
            return None

        payload = {"model": self.model, "messages": messages, "stream": True}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        full_text = []
        reasoning_text = []
        try:
            client = get_http()
            async with client.stream("POST", self.endpoint(), json=payload,
                                     headers=headers, timeout=timeout) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", errors="ignore")
                    log("error", "AI", f"[{self.backend}] HTTP {r.status_code}: {body[:200]}")
                    return None

                async for line in r.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        chunk = line[5:].strip()
                    elif line.startswith("{"):
                        chunk = line.strip()
                    else:
                        continue
                    if not chunk:
                        continue
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                    except Exception:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    c = delta.get("content")
                    if c:
                        full_text.append(c)
                    rc = delta.get("reasoning_content")
                    if rc:
                        reasoning_text.append(rc)

            reasoning = "".join(reasoning_text).strip()
            if reasoning:
                log("mem", "AI思维链", reasoning[:200] + ("..." if len(reasoning) > 200 else ""))
            return "".join(full_text).strip()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log("error", "AI", f"[{self.backend}] {type(e).__name__}: {e}")
            return None

    def _build_persona(self, mode="group"):
        name = cfg_str("persona_name").strip()
        identity = cfg_str("persona_identity").strip()
        behavior = cfg_str("persona_behavior").strip()
        speech = cfg_str("persona_speech").strip()
        parts = []
        if name:
            parts.append(f"你的名字是「{name}」。")
        if identity:
            parts.append(f"【人格设定】{identity}")
        if behavior:
            parts.append(f"【行为准则】{behavior}")
        if speech:
            parts.append(f"【表达风格】{speech}")
        parts.append(
            "【硬性铁律】\n"
            "1. 死死盯住每条消息的发送者标识，严禁张冠李戴。\n"
            "2. 只针对当前跟你说话的那个人回答。\n"
            "3. 不要在回复里 @ 任何人。\n"
            "4. 不要提及其他群成员的昵称或 QQ。\n"
            "5. 不要输出括号说明、旁白、内心戏。\n"
            "6. 严禁提及自己是 AI、机器人或系统。\n"
            "7. 如果消息里有[图片内容]前缀，说明是别人发的图，描述已经是你看过的，可以自然回应。")
        if mode != "group":
            parts[-1] = (
                "【硬性铁律】\n"
                "1. 不要在回复里 @ 对方。\n"
                "2. 不要输出括号说明、旁白、内心戏。\n"
                "3. 严禁提及自己是 AI、机器人或系统。\n"
                "4. 如果消息里有[图片内容]前缀，说明是别人发的图，描述已经是你看过的，可以自然回应。")
        return "\n\n".join(parts)

    async def chat(self, user_msg, speaker=None, history=None, memories=None,
                   mode="group", knowledge=None, group_id=None, is_super=False,
                   user_memories=None, user_name="", profile=None):
        if not self.enabled:
            return None
        sys_p = self._build_persona(mode)
        if mode == "group" and group_id is not None:
            _vh = _voice_prompt_hint(group_id)
            if _vh:
                sys_p += "\n\n" + _vh
            sys_p += "\n\n" + _admin_prompt_hint(is_super)
        if speaker and speaker.get("name"):
            sys_p += (f"\n\n【当前正在跟你说话的人】\n"
                      f"昵称：{speaker['name']}\nQQ：{speaker.get('qq', '未知')}")
        # 用户画像：bot 自己数据库里的客观统计。有了它，"我是谁""我常来吗"
        # 这类问题才答得上 —— 否则模型只看得到当场这一句昵称。
        if profile:
            sys_p += "\n\n" + profile
        # 知识库优先于长期记忆：它是群友明确整理过的准确信息
        if knowledge:
            sys_p += "\n\n" + knowledge
        if memories:
            lines = [f"- {m.get('content','').strip()}" for m in memories if m.get("content", "").strip()]
            if lines:
                sys_p += "\n\n【你记住的关于本群的历史】\n" + "\n".join(lines)
                sys_p += "\n（相关才自然体现，不相关不要提。）"
        if user_memories:
            lines = [f"- {m.get('content','').strip()}"
                     for m in user_memories if m.get("content", "").strip()]
            if lines:
                who = user_name or (speaker or {}).get("name") or "这个人"
                sys_p += (f"\n\n【你记住的关于「{who}」的事】\n" + "\n".join(lines))
                sys_p += ("\n（这是你对他的了解，用来让对话更自然；"
                          "不要生硬罗列，也不要让他觉得被监视。）")
        msgs = [{"role": "system", "content": sys_p}]
        if history: msgs.extend(history)
        msgs.append({"role": "user", "content": user_msg})
        return await self.raw(msgs, use_gap=True)

    async def analyze_memory(self, dialogue):
        prompt = (
            "分析下面这段QQ群对话，分别提取【群级记忆】和【用户级记忆】。\n"
            "\n"
            "【群级】整个群共享的事：群规约定、重要话题及结论、群内共识、未解决问题。\n"
            "【用户级】属于某个具体人的事：稳定偏好、身份信息、口头习惯、"
            "他个人的约定或承诺、他的近况。判断依据是发言里标注的 QQ 号。\n"
            "\n"
            "【不要记】寒暄、刷屏、一次性提问、玩笑、表情包、转述别人的话。\n"
            "【不要揣测】只写对话里明确说出来的，不要推断性格或编造。\n"
            "\n"
            "只输出纯JSON，不要markdown代码块，不要任何解释：\n"
            '{"group_memories":[{"content":"一句话","importance":3,"category":"话题"}],'
            '"user_memories":[{"qq":"123456","content":"一句话","importance":4,"category":"偏好"}]}\n'
            "\n"
            "importance 1-5（5 最重要）；category 从「偏好/话题/规则/成员/近况/其他」中选。\n"
            "qq 必须填发言前缀里那个数字 QQ 号，不要填昵称。\n"
            "某一类为空就给空数组 []。\n\n"
            f"对话：\n{dialogue}")
        r = await self.raw([{"role": "user", "content": prompt}], timeout=120.0, use_gap=False)
        if not r:
            return None
        r = r.strip()
        if r.startswith("```"):
            r = re.sub(r'^```\w*\n?', '', r)
            r = re.sub(r'\n?```$', '', r)
        try:
            data = json.loads(r)
            return data if isinstance(data, dict) else None
        except Exception as e:
            log("warn", "记忆", f"JSON解析失败: {e}")
            return None


AI = AIHandler()

# ---- 核心就绪：现在才让插件执行 setup() ----
# 放在这里是因为 DB（上面）和 AI（刚建好）都已存在，
# 插件在 setup() 里可以放心建自己的表、调 AI。
# 后面 _register_plugin_apis() 会用到插件注册的接口列表。
try:
    activate_all_plugins()
except Exception as _e:
    log("error", "插件", f"插件初始化阶段异常：{type(_e).__name__}: {_e}")

ai_context = {}
private_context = {}
turn_counter = {}
summarize_lock = {}
mem_stats = {"saved": 0, "skipped": 0, "failed": 0}
_context_lock = threading.RLock()


def get_context(g):
    """取出历史给模型用。去掉内部的 [语音] 标记，避免它误以为要输出这个前缀。"""
    with _context_lock:
        d = ai_context.get(g)
        if not d:
            return []
        out = []
        for m in d:
            if m.get("role") == "assistant" and m.get("content", "").startswith(_VOICE_MARK):
                out.append({"role": "assistant", "content": m["content"][len(_VOICE_MARK):]})
            else:
                out.append(dict(m))
        return out


_VOICE_MARK = "[语音] "


def push_context(g, u, a, as_voice=False):
    """写入群对话上下文。

    as_voice=True 时给自己的回复加一个标记 —— 否则模型只看到"文字版回复"，
    会以为自己一直在打字，被追问"你怎么不发语音"时就否认（真实 bug）。
    """
    content = (_VOICE_MARK + a) if as_voice else a
    with _context_lock:
        if g not in ai_context:
            ai_context[g] = deque(maxlen=MAX_CTX)
        ai_context[g].append({"role": "user", "content": u})
        ai_context[g].append({"role": "assistant", "content": content})


def get_private_context(uid):
    with _context_lock:
        d = private_context.get(uid)
        return list(d) if d else []


def push_private_context(uid, u, a):
    with _context_lock:
        if uid not in private_context:
            private_context[uid] = deque(maxlen=30)
        private_context[uid].append({"role": "user", "content": u})
        private_context[uid].append({"role": "assistant", "content": a})


def get_lock(g):
    with _context_lock:
        if g not in summarize_lock:
            summarize_lock[g] = asyncio.Lock()
        return summarize_lock[g]


# 记忆分析专用的发言流水：带 QQ 归属，才能把"某人的偏好"记到某个人头上。
# 跟 ai_context 分开存 —— 后者给模型看，格式里不该出现 QQ。
_mem_turns = {}
MEM_TURNS_MAX = 60          # 每群只留最近这么多条，够分析用


def push_mem_turn(g, uid, name, text, is_bot=False):
    """记一条发言用于记忆分析。

    key 约定：正数 = 群号；负数 -uid = 某人的私聊。
    （群号一定是正数，所以不会撞。）
    """
    txt = str(text or "").strip()
    if not txt:
        return
    with _context_lock:
        lst = _mem_turns.setdefault(g, [])
        lst.append({"uid": int(uid or 0), "name": str(name or ""),
                    "text": txt, "bot": bool(is_bot)})
        if len(lst) > MEM_TURNS_MAX * 2:
            del lst[:-MEM_TURNS_MAX]


def take_mem_turns(g, n):
    """取最近 n 条并清空（分析完就不重复分析）。"""
    with _context_lock:
        lst = _mem_turns.get(g) or []
        got = lst[-n:] if n else list(lst)
        _mem_turns[g] = []
        return got


def _bump_turn(g):
    with _context_lock:
        n = turn_counter.get(g, 0) + 1
        turn_counter[g] = n
        return n


def user_profile_block(gid, uid, name=""):
    """把 bot 数据库里关于某人的客观事实拼成提示词块。

    为什么需要：数据库里本来就有发言次数、积分、活跃天数，但以前一条都没进
    提示词。结果模型只看得到当场那句昵称，遇到不认识的昵称就说"你还没告诉
    我名字"（真实反馈）。
    """
    if not uid:
        return ""
    try:
        f = DB.user_facts(gid, uid)
    except Exception as e:
        log("warn", "画像", f"读取失败: {type(e).__name__}: {e}")
        return ""
    if not f:
        return ""
    lines = []
    nm = str(name or f.get("nickname") or "").strip()
    if nm:
        lines.append(f"昵称：{nm}")
    lines.append(f"QQ：{uid}")
    if f.get("msg_count"):
        lines.append(f"在本群发言 {f['msg_count']} 次")
    if f.get("total_msgs"):
        lines.append(f"所有群累计发言 {f['total_msgs']} 次")
    if f.get("group_count"):
        lines.append(f"和你在 {f['group_count']} 个群共处过")
    if f.get("first_date"):
        lines.append(f"最早记录 {f['first_date']}，"
                     f"共 {f.get('active_days', 0)} 天有活动")
    if f.get("points") is not None:
        lines.append(f"积分 {f['points']}"
                     f"（签到 {f.get('sign_count', 0)} 次，连续 {f.get('streak', 0)} 天）")
    if f.get("replied"):
        lines.append(f"你回复过他 {f['replied']} 次")
    if len(lines) <= 2:
        # 只有 QQ 和昵称，没什么信息量，不注入免得占字数
        return ""
    return ("【你对这个人的已知信息（来自你的记录，不是猜测）】\n"
            + "\n".join(f"- {x}" for x in lines)
            + "\n（可以自然使用；不要机械复述数字，也别让他觉得被监视。）")


async def maybe_analyze_memory(g):
    """分析记忆。g>0 = 群聊；g<0 = 与 |-g| 这个人的私聊。

    私聊时只写用户级记忆（group_id=0, user_id=uid），不会写群级，
    也不会跟其他人私聊的记忆混在一起。
    """
    solo_uid = -g if g < 0 else 0
    gid = g if g > 0 else 0
    if turn_counter.get(g, 0) < CHECK_EVERY:
        return
    lock = get_lock(g)
    if lock.locked():
        return
    async with lock:
        with _context_lock:
            if turn_counter.get(g, 0) < CHECK_EVERY:
                return
            turn_counter[g] = 0
        # 用带 QQ 归属的发言流水（不是给模型看的 ai_context）
        turns = take_mem_turns(g, CHECK_EVERY * 3)
        if not turns:
            return
        lines = []
        for t in turns:
            if t["bot"]:
                lines.append(f"我：{t['text']}")
            else:
                nm = t["name"] or t["uid"] or "未知"
                lines.append(f"{nm}(QQ{t['uid']})：{t['text']}")
        dialogue = "\n".join(lines)
        where = f"群 {g}" if g > 0 else f"私聊 {solo_uid}"
        log("mem", "记忆", f"{where} 分析 {len(turns)} 条发言...")
        try:
            data = await asyncio.wait_for(AI.analyze_memory(dialogue), timeout=120.0)
        except asyncio.TimeoutError:
            log("warn", "记忆", "分析超时")
            mem_stats["failed"] += 1
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log("warn", "记忆", f"分析失败: {type(e).__name__}: {e}")
            mem_stats["failed"] += 1
            return
        if not data:
            mem_stats["failed"] += 1
            return

        saved = 0
        seen = set()

        def _save(items, uid, uname, scope):
            nonlocal saved
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                try:
                    c = str(item.get("content", "")).strip()
                    imp = int(item.get("importance", 3) or 3)
                    cat = str(item.get("category", "其他")).strip() or "其他"
                    key = (scope, uid, c)
                    if not c:
                        continue
                    if imp < MIN_IMPORTANCE or key in seen:
                        # 这两种才算"跳过分析"：重要性不够、或者跟已有的重复。
                        # 以前 continue 了但不计数，概览页那张卡片永远是 0。
                        mem_stats["skipped"] += 1
                        continue
                    seen.add(key)
                    DB.mem_add(gid, c, cat, imp, uid=uid, uname=uname, scope=scope)
                    saved += 1
                    who = f"[{uname or uid}] " if scope == "user" else ""
                    log("mem", "记忆", f"  {who}[{cat} · {imp}星] {c[:40]}")
                except Exception as e:
                    log("warn", "记忆", f"写入失败: {e}")

        # 私聊：只写用户级记忆，归到这个人头上
        if solo_uid:
            _save(data.get("user_memories"), solo_uid, "", "user")
            _save(data.get("group_memories"), solo_uid, "", "user")
        else:
            # 群级
            _save(data.get("group_memories"), 0, "", "group")
        # 用户级：只认本群真实出现过的人，防止 AI 编造 QQ
        if not solo_uid:
            known = {}
            for t in turns:
                if not t["bot"] and t["uid"]:
                    known[str(t["uid"])] = t["name"] or str(t["uid"])
            for item in data.get("user_memories") or []:
                if not isinstance(item, dict):
                    continue
                qq = str(item.get("qq", "")).strip()
                # 容错：AI 可能返回"123456(张三)"这种
                qq = re.sub(r"\D", "", qq)
                if not qq or qq not in known:
                    if qq:
                        log("warn", "记忆", f"忽略不认识的 QQ: {qq}")
                    continue
                _save([item], int(qq), known[qq], "user")

        if saved:
            mem_stats["saved"] += saved
            ucnt = sum(1 for k in seen if k[0] == "user")
            log("success", "记忆",
                f"群 {g} 新增 {saved} 条（群级 {saved - ucnt}，用户级 {ucnt}）")


def _hard_wrap(s, max_len):
    out = []
    while len(s) > max_len:
        window = s[:max_len]
        cut = -1
        for sep in ("，", "、", "；", "：", ",", ";", " ", "\n"):
            idx = window.rfind(sep)
            if idx > cut:
                cut = idx + (0 if sep == "\n" else 1)
        if cut < max_len // 3:
            cut = max_len
        out.append(s[:cut].strip())
        s = s[cut:].strip()
    if s:
        out.append(s)
    return out


def split_reply_text(text):
    if not text:
        return []
    text = str(text).strip()
    if not text:
        return []
    if not cfg_bool("split_reply", True):
        return [text]
    max_len = max(10, cfg_int("split_max_length", 100))
    max_parts = max(1, cfg_int("split_max_parts", 3))
    # 设成 1 条 = 明确要求不拆，此时长度约束让位
    if max_parts <= 1:
        return [text]
    if len(text) <= max_len:
        return [text]

    pieces = re.findall(r'[^。！？!?\.]+[。！？!?\.]*', text)
    pieces = [p.strip() for p in pieces if p.strip()]
    if not pieces:
        pieces = [text]

    parts = []
    cur = ""
    for p in pieces:
        if len(p) > max_len:
            if cur:
                parts.append(cur)
                cur = ""
            parts.extend(_hard_wrap(p, max_len))
            continue
        if not cur:
            cur = p
        elif len(cur) + len(p) <= max_len:
            cur += p
        else:
            parts.append(cur)
            cur = p
    if cur:
        parts.append(cur)

    # 优先级：max_len（"拆分单条最大字数"）是硬约束，必须守住；
    # max_parts 只是防刷屏的软上限。内容超过 max_parts×max_len 时两者不可能兼得，
    # 这时宁可按 max_len 多发几条，也不要挤出超长的一条（那看起来就像"没拆"）。
    if len(parts) > max_parts:
        log("warn", "回复",
            f"回复较长（{len(text)} 字），按 {max_len} 字上限拆成 {len(parts)} 条，"
            f"超过「最多拆几条」设置（{max_parts}）")
    return [p for p in parts if p]
async def do_chat(group_id, user_id, text, nickname="", mode="group",
                  dry_run=False):
    """给外部（Web 控制台）调用的对话接口。

    dry_run=True 时照常读上下文/记忆（回复是真实的），但一个字节都不写：
    不动记忆流水、不写聊天记录、不动轮次计数。
    控制台的「AI 对话」页默认用它 —— 调试不该污染真实用户的私聊记忆。
    """
    if not AI.enabled:
        return {"ok": False, "error": "AI 未启用"}
    gid = int(group_id); uid = int(user_id)
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "内容为空"}

    if not nickname:
        bot = get_any_bot()
        if bot:
            nickname = await safe_nick(bot, gid, uid)
        else:
            nickname = str(uid)

    speaker = {"name": nickname, "qq": uid}
    reply = None
    try:
        if mode == "group":
            history = get_context(gid)
            mems = DB.mem_recent(gid, limit=INJECT_COUNT)
            umems = (DB.mem_recent_user(gid, uid, limit=cfg_int("memory_user_inject_count", 3))
                     if cfg_bool("memory_user_enabled", True) else [])
            # 知识库：按这句问话去找相关条目，注入提示词
            kb_block = ""
            if cfg_bool("kb_enabled", True) and cfg_bool("kb_auto_inject", True):
                hits = kb_search(gid, text, limit=cfg_int("kb_inject_count", 3))
                if hits:
                    kb_block = kb_reference_block(hits)
                    for _s, _row in hits:
                        if not dry_run:
                            DB.kb_touch(_row["id"])
                    log("info", "知识库", f"[群{gid}] 注入 {len(hits)} 条给 AI：{text[:30]}")
            if not dry_run:
                push_mem_turn(gid, uid, nickname, text)
                DB.log_add(gid, uid, nickname, text)
            profile = user_profile_block(gid, uid, nickname)
            reply = await asyncio.wait_for(
                AI.chat(text, speaker=speaker, history=history, memories=mems,
                        mode="group", knowledge=kb_block, group_id=gid,
                        is_super=str(uid) in SUPERUSERS,
                        user_memories=umems, user_name=nickname, profile=profile),
                timeout=AI_HARD_TIMEOUT
            )
            if reply:
                # 跟 _on_ai 发到群里的文字保持一致：先剥 [情绪:xx] 再存
                _clean, _ = parse_emotion_tag(reply)
                # AI 只输出了 [情绪:xx]、没有正文时 _clean 是空串 ——
                # 以前 `_clean or reply` 会把带标签的原文发出去。
                reply = _clean if _clean else "……"
                if not dry_run:
                    push_context(gid, f"{nickname}：{text}", reply)
                    # _on_ai 和私聊分支都有这一行，群分支以前漏了 ——
                    # 从 Web 控制台触发的群对话，AI 的回复不会进记忆分析。
                    push_mem_turn(gid, 0, "", reply, is_bot=True)
                    DB.log_add(gid, 0, "我", reply, is_bot=True)
                    bump = _bump_turn(gid)
                    if bump >= CHECK_EVERY:
                        spawn_bg(maybe_analyze_memory(gid), "记忆分析")
        else:
            history = get_private_context(uid)
            umems = (DB.mem_recent_user(0, uid, limit=cfg_int("memory_user_inject_count", 3))
                     if cfg_bool("memory_user_enabled", True) else [])
            if not dry_run:
                push_mem_turn(-uid, uid, nickname, text)
                DB.log_add(0, uid, nickname, text)
            profile = user_profile_block(0, uid, nickname)
            reply = await asyncio.wait_for(
                AI.chat(text, speaker=speaker, history=history, mode="private",
                        user_memories=umems, user_name=nickname, profile=profile),
                timeout=AI_HARD_TIMEOUT
            )
            if reply:
                _clean, _ = parse_emotion_tag(reply)
                # 跟另外两处保持一致：只有 [情绪:xx]、没有正文时
                # 别把带标签的原文发出去。
                reply = _clean if _clean else "……"
                if not dry_run:
                    push_private_context(uid, f"{nickname}：{text}", reply)
                    push_mem_turn(-uid, 0, "", reply, is_bot=True)
                    DB.log_add(0, 0, "我", reply, is_bot=True)
                    if _bump_turn(-uid) >= CHECK_EVERY:
                        spawn_bg(maybe_analyze_memory(-uid), "私聊记忆分析")
    except asyncio.TimeoutError:
        log("warn", "AI", f"群{gid} 对话硬超时（{AI_HARD_TIMEOUT:.0f}s）")
        return {"ok": False, "error": "AI 响应超时"}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log("error", "AI", f"对话异常: {type(e).__name__}: {e}")
        return {"ok": False, "error": f"AI 调用失败: {type(e).__name__}"}

    if not reply:
        return {"ok": False, "error": "AI 无回复"}
    return {
        "ok": True, "reply": reply,
        "nickname": nickname, "user_id": uid, "group_id": gid, "mode": mode,
    }


async def _extract_target_qq(bot, event, arg_text):
    """找出要做操作的目标 QQ。

    必须跳过"@机器人自己"的那一段 —— 否则「@机器人 踢 @某人」会取到机器人自己的
    QQ，导致把自己踢了（真实 bug）。
    """
    try:
        self_id = str(getattr(event, "self_id", "") or "")
    except Exception:
        self_id = ""
    try:
        for seg in event.message:
            if seg.type != "at":
                continue
            qq = seg.data.get("qq")
            if not qq or str(qq) == "all":
                continue
            if self_id and str(qq) == self_id:
                continue          # 跳过 @机器人自己
            return int(qq), None
    except Exception:
        pass

    arg_text = (arg_text or "").strip()
    if not arg_text:
        return None, None

    m = re.search(r'\d{5,12}', arg_text)
    if m:
        return int(m.group()), None

    try:
        members = await bot.get_group_member_list(group_id=event.group_id)
    except Exception as e:
        log("warn", "查找", f"get_group_member_list 失败: {type(e).__name__}: {e}")
        return None, "读取群成员失败"

    exact, fuzzy = [], []
    for mb in (members or []):
        card = (mb.get("card") or "").strip()
        nick = (mb.get("nickname") or "").strip()
        uid = mb.get("user_id")
        if not uid:
            continue
        if arg_text == card or arg_text == nick:
            exact.append((uid, card or nick))
        elif arg_text in card or arg_text in nick:
            fuzzy.append((uid, card or nick))

    cands = exact if exact else fuzzy
    if not cands:
        return None, f"没找到叫「{arg_text}」的群成员"
    if len(cands) == 1:
        return cands[0][0], None

    names = " / ".join(f"{n}({u})" for u, n in cands[:5])
    more = " 等" if len(cands) > 5 else ""
    return None, f"匹配到多个：{names}{more}，请用 QQ 号或 @ 目标"


ADMIN_CMD_TARGET = ("踢", "拉黑", "解除", "解黑", "解除黑名单", "放出来")
_SEP_RE = re.compile(r'[\s\u2005\u3000:：,，]+')
# 命令词前后的语气词/称呼，匹配时忽略，这样「把 @某人 踢了」也能识别
_CMD_NOISE = ("把", "帮", "帮我", "给我", "请", "麻烦", "你", "去", "快", "赶紧",
              "的话", "一下", "了", "吧", "呗", "谢谢")
# 动词后缀：中文没有词间空格，所以「踢了」「踢出去」要能还原成「踢」
_CMD_VERB_SUFFIX = ("出去", "出去吧", "掉", "走", "了", "吧", "呗", "人", "一下")
_CMD_SPLIT_RE = re.compile(r'[\s\u2005\u3000:：,，。！？!?、~～]+')


def cmd_contains(text, kw):
    """命令词是否出现在文本里（容忍中文连写和动词后缀）。

    中文没有词间空格，「把这个人踢出去」是一个整词，所以不能只做整词比较，
    必须在词内部找命令词。用前后边界排除误伤：
      - 前面不能是「禁」——否则「禁言」会被「言」命中
      - 后面只能是动词后缀或边界——否则「踢球」「了解」会被误判
    """
    if not kw or not text:
        return False
    pat = (r'(?<![禁查看了解得知晓])' + re.escape(kw)
           + r'(?:' + "|".join(re.escape(s) for s in _CMD_VERB_SUFFIX) + r')?'
           + r'(?=$|[\s\u2005\u3000:：,，。！？!?、~～@他她它祂你我])')
    return re.search(pat, str(text)) is not None


async def handle_admin_command(bot, event, cmd):
    uid = event.user_id
    gid = event.group_id
    nick = await safe_nick(bot, gid, uid)

    for kw in ADMIN_CMD_TARGET:
        # 三种写法都认：
        #   1) 以命令词开头              「踢 @某人」
        #   2) 命令词连写在句子里          「把这个人踢出去 @某人」
        #   3) 命令词独立成词              「把 @某人 踢了」
        at_start = (cmd == kw) or (cmd.startswith(kw) and _SEP_RE.match(cmd, len(kw)))
        if at_start or cmd_contains(cmd, kw):
            arg = _SEP_RE.sub(" ", cmd[len(kw):]).strip() if at_start else cmd
            target, err = await _extract_target_qq(bot, event, arg)
            if err:
                await safe_reply(bot, event, err)
                return True
            if not target:
                await safe_reply(bot, event,
                    f"用法：{kw} @某人 或 {kw} QQ号 或 {kw} 昵称")
                return True

            target_nick = await safe_nick(bot, gid, target)

            if kw in ("踢",):
                DB.bl_add(target, gid, target_nick, f"超管 {nick} 手动踢出")
                kicked = False
                try:
                    await bot.set_group_kick(group_id=gid, user_id=target, reject_add_request=True)
                    kicked = True
                except Exception as e:
                    log("error", "超管", f"踢 {target} 失败: {type(e).__name__}: {e}")
                log("block", "超管", f"{nick} 踢出 {target_nick}({target}) @群{gid}｜{'OK' if kicked else 'FAIL'}")
                msg = f"已踢出 {target_nick}（{target}）" + ("" if kicked else "（踢出失败，可能没有管理员权限）")
                await safe_reply(bot, event, msg)
                return True

            if kw in ("拉黑",):
                DB.bl_add(target, gid, target_nick, f"超管 {nick} 手动拉黑")
                log("block", "超管", f"{nick} 拉黑 {target_nick}({target}) @群{gid}")
                await safe_reply(bot, event, f"已拉黑 {target_nick}（{target}）")
                return True

            if kw in ("解除", "解黑", "解除黑名单", "放出来"):
                DB.bl_remove(target, gid)
                log("success", "超管", f"{nick} 解除 {target_nick}({target}) 黑名单")
                await safe_reply(bot, event, f"已解除 {target_nick}（{target}）的黑名单")
                return True

    if cmd in ("清空记忆", "清记忆"):
        cnt = DB.mem_count(gid)
        DB.mem_clear_group(gid)
        log("success", "超管", f"{nick} 清空本群记忆 {cnt} 条 @群{gid}")
        await safe_reply(bot, event, f"已清空本群记忆（{cnt} 条）")
        return True

    if cmd in ("清空黑名单", "清黑"):
        items = DB.bl_list_group(gid)
        cnt = len(items)
        for it in items:
            DB.bl_remove(it["user_id"], gid)
        log("success", "超管", f"{nick} 清空本群黑名单 {cnt} 条 @群{gid}")
        await safe_reply(bot, event, f"已清空本群黑名单（{cnt} 条）")
        return True

    if cmd in ("状态", "bot状态"):
        uptime = time.time() - START_TIME
        h = int(uptime // 3600)
        m = int(uptime % 3600 // 60)
        mem_total = DB.mem_count(gid)
        bl_total = DB.bl_count()
        pending = len(pending_verify)
        pending_req = len(pending_requests)
        mem_saved = mem_stats["saved"]
        vision_on = "开启" if cfg_bool("vision_enabled", False) else "关闭"
        voice_on = "开启" if cfg_bool("voice_enabled", False) else "关闭"
        backend_label = "Ollama" if AI.backend == "ollama" else "云端 API"
        text = (f"运行时长：{h}h {m}m\n"
                f"本群记忆：{mem_total} 条\n"
                f"全库黑名单：{bl_total} 条\n"
                f"已存记忆：{mem_saved} 条\n"
                f"待验证：{pending} 人\n"
                f"待审批：{pending_req} 人\n"
                f"AI：{'开启' if AI.enabled else '关闭'}（{backend_label} · {AI.model}）\n"
                f"视觉识图：{vision_on}\n"
                f"语音合成：{voice_on}")
        await safe_reply(bot, event, text)
        return True

    if cmd in ("AI开", "ai开", "AI关", "ai关"):
        enabled = cmd in ("AI开", "ai开")
        CFG["ai_enabled"] = enabled
        save_config()
        AI.reload()
        log("success", "超管", f"{nick} {'开启' if enabled else '关闭'} AI")
        await safe_reply(bot, event, "AI 已开启" if enabled else "AI 已关闭（重启后仍生效）")
        return True

    if cmd in ("语音开", "语音关"):
        enabled = cmd == "语音开"
        CFG["voice_enabled"] = enabled
        save_config()
        log("success", "超管", f"{nick} {'开启' if enabled else '关闭'} 语音合成")
        await safe_reply(bot, event, "语音合成已开启" if enabled else "语音合成已关闭")
        return True

    return False


pending_verify = {}
_verify_lock = threading.RLock()


def gen_math():
    a = random.randint(1, 20)
    b = random.randint(1, 20)
    return f"{a} + {b} = ?", a + b


def _prune_pending_verify():
    now = time.time()
    with _verify_lock:
        for key, info in list(pending_verify.items()):
            if info.get("deadline", 0) < now - 60:
                pending_verify.pop(key, None)


async def _verify_timeout(bot, gid, uid, nick, t, token):
    try:
        await asyncio.sleep(t)
        key = (int(gid), int(uid))
        with _verify_lock:
            info = pending_verify.get(key)
            if not info or info.get("token") != token:
                return
            pending_verify.pop(key, None)
        try:
            await bot.set_group_kick(group_id=gid, user_id=uid, reject_add_request=False)
        except Exception as e:
            log("error", "验证", f"踢出失败: {type(e).__name__}: {e}")
        try:
            await bot.send_group_msg(group_id=gid, message=f"{nick} 未完成验证，已移出。")
        except Exception:
            pass
        log("verify", "验证", f"{nick}({uid}) @群{gid} 超时未答")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log("warn", "验证", f"超时任务异常: {type(e).__name__}: {e}")


async def start_verify(bot, gid, uid, nick):
    t = cfg_int("verify_timeout", 60)
    q, a = gen_math()
    token = f"{gid}-{uid}-{time.time_ns()}"
    key = (int(gid), int(uid))
    with _verify_lock:
        old = pending_verify.pop(key, None)
        if old and old.get("task"):
            old["task"].cancel()
        pending_verify[key] = {
            "group_id": gid, "nickname": nick, "answer": a,
            "deadline": time.time() + t, "token": token, "task": None,
        }
    try:
        await bot.send_group_msg(
            group_id=gid,
            message=(
                MessageSegment.at(uid) +
                f" 欢迎加入！请在 {t} 秒内回答下面的问题：\n\n  {q}\n\n直接回复数字即可。"
            )
        )
    except Exception as e:
        log("error", "验证", f"群内发送失败: {type(e).__name__}: {e}")
        with _verify_lock:
            cur = pending_verify.get(key)
            if cur and cur.get("token") == token:
                pending_verify.pop(key, None)
        return
    task = spawn_bg(_verify_timeout(bot, gid, uid, nick, t, token), "验证超时")
    with _verify_lock:
        cur = pending_verify.get(key)
        if cur and cur.get("token") == token:
            cur["task"] = task
    log("verify", "验证", f"向 {nick}({uid}) @群{gid} 提问：{q}")


group_increase = on_notice(priority=5, block=False)


@group_increase.handle()
async def _on_join(bot: Bot, event: GroupIncreaseNoticeEvent):
    if str(event.user_id) == str(bot.self_id):
        log("join", "入群", f"机器人被拉入群 {event.group_id}")
        return
    nick = await safe_nick(bot, event.group_id, event.user_id)
    log("join", "入群", f"{nick}({event.user_id}) -> {event.group_id}")
    if DB.bl_check(event.user_id, event.group_id):
        try:
            await bot.set_group_kick(group_id=event.group_id, user_id=event.user_id, reject_add_request=True)
            await bot.send_group_msg(group_id=event.group_id,
                message=f"检测到黑名单成员 {nick}，已自动移除。")
            log("block", "拦截", f"黑名单 {nick} 被踢")
        except Exception as e:
            log("error", "拦截", f"失败: {type(e).__name__}: {e}")
        return
    if cfg_bool("verify_enabled", True):
        await start_verify(bot, event.group_id, event.user_id, nick)
        return
    try:
        template = cfg_str("welcome_msg", "欢迎 {nickname} 加入本群！")
        msg = template.format(nickname=nick, user_id=event.user_id, group_id=event.group_id)
        await bot.send_group_msg(group_id=event.group_id, message=msg)
    except Exception as e:
        log("error", "欢迎", f"发送失败: {type(e).__name__}: {e}")


def _is_group(event) -> bool:
    r"""只匹配群消息。

    群 handler 第一行就访问 event.group_id —— 私聊消息没有这个属性，
    不加这个 rule 的话每次私聊都会在日志里留下一条 AttributeError。

    _on_private 那边有 isinstance(event, GroupMessageEvent) 挡群消息，
    反过来一直没有。
    """
    return isinstance(event, GroupMessageEvent)


async def _verify_answer_rule(event: GroupMessageEvent) -> bool:
    info = pending_verify.get((int(event.group_id), int(event.user_id)))
    return bool(info)


verify_answer = on_message(rule=Rule(_is_group) & Rule(_verify_answer_rule),
                           priority=4, block=True)


@verify_answer.handle()
async def _on_verify_answer(bot: Bot, event: GroupMessageEvent):
    key = (int(event.group_id), int(event.user_id))
    with _verify_lock:
        info = pending_verify.get(key)
    if not info:
        return
    text = event.get_plaintext().strip()
    if not text:
        return
    m = re.fullmatch(r'[-+]?\d{1,6}', text)
    if not m:
        await safe_reply(bot, event, "请只回复数字答案，例如：35")
        return
    if int(m.group()) == info["answer"]:
        with _verify_lock:
            cur = pending_verify.get(key)
            if not cur or cur.get("token") != info.get("token"):
                return
            pending_verify.pop(key, None)
        task = info.get("task")
        if task:
            task.cancel()
        log("success", "验证", f"{info['nickname']}({event.user_id}) @群{event.group_id} 通过")
        await safe_reply(bot, event, f"{info['nickname']} 验证通过，欢迎！")
    else:
        await safe_reply(bot, event, "答案不正确，请再试一次。")


# 以前是毫无限制，进函数第一行再 `if isinstance(event, GroupMessageEvent): return`
# 挡一下 —— 每条群消息都要白走一趟 _on_private。改成 rule，跟另外三个
# 群 handler 的写法对称。
def _is_private(event: MessageEvent) -> bool:
    r"""只匹配私聊。

    注解必须是 MessageEvent（PrivateMessageEvent 和 GroupMessageEvent
    的基类）：这个 rule 就是要**看到**群消息才能把它过滤掉，标成
    PrivateMessageEvent 与事实不符。

    别写成 Rule(lambda e: ...) —— nonebot2 的 Rule 会反射回调的参数
    类型注解，裸 lambda 没有注解，直接抛
    ValueError: Unknown parameter e。所以必须是带注解的具名函数。

    也别去掉注解 —— 去掉同样是那个 ValueError。
    """
    return not isinstance(event, GroupMessageEvent)


private_chat = on_message(rule=Rule(_is_private), priority=3, block=False)


@private_chat.handle()
async def _on_private(bot: Bot, event: PrivateMessageEvent):
    # 群消息在 rule（_is_private）那层就已经挡掉了，正常走不到这个 return。
    # 留着是有意的双保险：万一哪天有人把 rule 删了或改错，这里还能兜住，
    # 不至于让群消息被当成私聊处理（记忆 key 用 -uid，会写错地方）。
    if isinstance(event, GroupMessageEvent):
        return
    uid = event.user_id
    if not cfg_bool("enable_private_chat", True):
        return
    if not AI.enabled:
        return

    raw_text = event.get_plaintext().strip()
    image_urls = extract_images(event)

    if not raw_text and not image_urls:
        return

    try:
        info2 = await bot.get_stranger_info(user_id=uid, no_cache=False)
        nick = info2.get("nickname") or str(uid)
    except Exception:
        nick = str(uid)

    text, has_img = await enrich_text_with_images(event, raw_text,
                                                  urls=image_urls)
    if has_img:
        log("msg", "识图", f"私聊 {nick} 发来 {len(image_urls)} 张图")
    if not text:
        text = "在吗"

    speaker = {"name": nick, "qq": uid}
    log("msg", "私聊", f"{nick}({uid}): {text[:60]}")
    history = get_private_context(uid)
    _pm = (DB.mem_recent_user(0, uid, limit=cfg_int("memory_user_inject_count", 3))
           if cfg_bool("memory_user_enabled", True) else [])
    _pf = user_profile_block(0, uid, nick)
    try:
        reply = await asyncio.wait_for(
            AI.chat(text, speaker=speaker, history=history, mode="private",
                    user_memories=_pm, user_name=nick, profile=_pf),
            timeout=AI_HARD_TIMEOUT
        )
    except asyncio.TimeoutError:
        log("warn", "AI", f"私聊 {uid} 硬超时")
        return
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log("error", "AI", f"私聊 {uid} 异常: {type(e).__name__}: {e}")
        return
    if reply:
        # 语音优先：命中关键词或随机概率时先发语音，失败再发文字
        voice_sent = False
        # 初值先剥一遍：万一 try_send_voice 抛异常（下面 except 会吞掉），
        # spoken 就还是这个初值 —— 不剥的话上下文和记忆流水里会存带标签的版本
        spoken = parse_emotion_tag(reply)[0] or reply
        try:
            voice_sent, spoken = await try_send_voice(bot, event, reply)
        except Exception as e:
            log("warn", "语音", f"私聊语音失败，降级文字: {type(e).__name__}: {e}")
        # 历史存实际发出的那句（语音时是清洗后的），避免它记住自己的错话
        push_private_context(uid, f"{nick}：{text}", spoken)
        # 私聊也记长期记忆：key 用 -uid，跟群号（正数）不会撞
        push_mem_turn(-uid, uid, nick, text)
        push_mem_turn(-uid, 0, "", spoken, is_bot=True)
        DB.log_add(0, uid, nick, text)
        DB.log_add(0, 0, "我", spoken, is_bot=True)
        if _bump_turn(-uid) >= CHECK_EVERY:
            spawn_bg(maybe_analyze_memory(-uid), "私聊记忆分析")
        if voice_sent:
            # 注意：以前这里硬编码了 "..."，短句也会显示成被截断，看着像 bug。
            log("reply", "语音",
                f"-> {nick}: {spoken[:60]}" + ("..." if len(spoken) > 60 else ""))
            return

        # 必须剥掉 [情绪:xx] 再发 —— 标签是给语音选音色用的，不该让用户看到。
        # 群聊分支一直是这么做的（那边有 text_reply = parse_emotion_tag(reply)），
        # 私聊这边漏了，于是私聊会收到「好呀～[情绪:平静]」这种，群里却不会。
        text_reply, _ = parse_emotion_tag(reply)
        # 跟 do_chat 群分支保持一致：AI 只输出了 [情绪:xx]、没有正文时
        # text_reply 是空串，\or reply\ 会把带标签的原文发出去。
        text_reply = text_reply if text_reply else "……"
        try:
            await bot.send(event, text_reply)
            log("reply", "回复",
                f"-> {nick}: {text_reply[:60]}" + ("..." if len(text_reply) > 60 else ""))
        except Exception as e:
            log("error", "回复", f"发送失败: {type(e).__name__}: {e}")


async def _notify_leave(bot, gid, uid, nick, reason):
    gname = ""
    try:
        info = await bot.get_group_info(group_id=gid, no_cache=False)
        gname = info.get("group_name", "")
    except Exception:
        pass
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"群成员变动\n────────────\n群号：{gid}"
    if gname: msg += f"（{gname}）"
    msg += f"\n昵称：{nick}\nQQ：{uid}\n类型：{reason}\n时间：{now}\n────────────\n已加入黑名单"
    for admin in SUPERUSERS:
        try:
            await bot.send_private_msg(user_id=int(admin), message=msg)
        except Exception:
            log("warn", "退群通知", f"私聊 {admin} 失败")


group_decrease = on_notice(priority=5, block=False)


@group_decrease.handle()
async def _on_leave(bot: Bot, event: GroupDecreaseNoticeEvent):
    uid = event.user_id
    gid = event.group_id
    sub = event.sub_type or ""
    if str(uid) == str(bot.self_id) or sub == "kick_me":
        log("leave", "退群", f"机器人离开群 {gid}（{sub or '未知'}）")
        return

    key = (int(gid), int(uid))
    with _verify_lock:
        stale = pending_verify.pop(key, None)
    if stale and stale.get("task"):
        stale["task"].cancel()

    nick = DB.nick_lookup(uid, gid) or str(uid)
    if sub == "leave":
        reason = "主动退群"
        note = f"{nick}（{uid}）离开了群聊。已自动加入黑名单。"
    elif sub == "kick":
        reason = "被管理员移出"
        note = f"{nick} 被管理员移出群聊，已加入黑名单。"
    else:
        reason = sub or "未知方式"
        note = f"{nick}（{uid}）离开了群聊。"
    DB.bl_add(uid, gid, nick, reason)
    log("leave", "退群" if sub == "leave" else "移出", f"{nick}({uid}) {reason}，已拉黑")
    try:
        template = cfg_str("farewell_msg", "{nickname} 离开了群聊。")
        msg = template.format(nickname=nick, user_id=uid, group_id=gid)
        await bot.send_group_msg(group_id=gid, message=f"{msg}\n{note}")
    except Exception as e:
        log("warn", "退群", f"群内提示发送失败: {type(e).__name__}")
    await _notify_leave(bot, gid, uid, nick, reason)


async def get_qq_level(bot, user_id):
    try:
        info = await bot.call_api("get_stranger_info", user_id=user_id, no_cache=False)
    except Exception as e:
        log("warn", "等级", f"查询 {user_id} 失败: {type(e).__name__}: {e}")
        return None

    candidates = []
    if isinstance(info, dict):
        for key in ("qqLevel", "qq_level", "level", "lv", "grade"):
            v = info.get(key)
            if v is not None:
                candidates.append(v)
        d = info.get("data")
        if isinstance(d, dict):
            for key in ("qqLevel", "qq_level", "level", "lv", "grade"):
                v = d.get(key)
                if v is not None:
                    candidates.append(v)

    for v in candidates:
        try:
            n = int(v)
        except Exception:
            continue
        if n > 0:
            return n

    return None


def _cleanup_pending_requests():
    now_ts = time.time()
    expired = [k for k, v in pending_requests.items()
               if now_ts - v.get("created_at", 0) > REQUEST_EXPIRE_SECONDS]
    for k in expired:
        pending_requests.pop(k, None)
    return len(expired)


group_request = on_request(priority=5, block=False)


@group_request.handle()
async def _on_request(bot: Bot, event: GroupRequestEvent):
    if event.sub_type != "add":
        return
    uid = event.user_id
    gid = event.group_id
    comment = (event.comment or "").strip()
    now_ts = time.time()

    _cleanup_pending_requests()

    last_ts = request_cooldown.get(uid, 0)
    if now_ts - last_ts < REQUEST_THROTTLE_SECONDS:
        try:
            await bot.set_group_add_request(flag=event.flag, sub_type="add",
                approve=False, reason="申请太频繁，请稍后再试")
        except Exception:
            pass
        return
    request_cooldown[uid] = now_ts

    log("req", "申请", f"QQ {uid} 申请进群 {gid}")
    if comment:
        log("info", "申请", f"验证信息：{comment}")

    if DB.bl_check(uid, gid):
        try:
            await bot.set_group_add_request(flag=event.flag, sub_type="add",
                approve=False, reason=cfg_str("reject_msg", "你已在黑名单中，无法再次加入本群。"))
            log("block", "申请", f"{uid} 结果：拒绝（在黑名单）")
        except Exception as e:
            log("error", "申请", f"拒绝失败: {type(e).__name__}: {e}")
        return

    level = await get_qq_level(bot, uid)

    if level is not None and level >= MIN_QQ_LEVEL:
        try:
            await bot.set_group_add_request(flag=event.flag, sub_type="add", approve=True)
            log("success", "申请", f"{uid} 结果：自动通过（{level}级 >= {MIN_QQ_LEVEL}级）")
        except Exception as e:
            log("error", "申请", f"通过失败: {type(e).__name__}: {e}")
        return

    if level is not None and level < MIN_QQ_LEVEL:
        reason = f"你的QQ等级为 {level} 级，本群要求 {MIN_QQ_LEVEL} 级以上。"
        try:
            await bot.set_group_add_request(flag=event.flag, sub_type="add",
                approve=False, reason=reason)
            log("block", "申请", f"{uid} 结果：拒绝（{level}级 < {MIN_QQ_LEVEL}级）")
        except Exception as e:
            log("error", "申请", f"拒绝失败: {type(e).__name__}: {e}")
        return

    try:
        nick = await safe_nick(bot, gid, uid)
    except Exception:
        nick = str(uid)
    pending_requests[event.flag] = {
        "flag": event.flag,
        "user_id": uid,
        "group_id": gid,
        "nickname": nick,
        "comment": comment,
        "level": level,
        "created_at": time.time(),
    }
    log("warn", "申请", f"{uid} 结果：等级未知，转人工审核（待审批 {len(pending_requests)} 人）")

    msg = (
        f"📥 新的入群申请（待审批）\n"
        f"────────────\n"
        f"群号：{gid}\n"
        f"用户：{nick}（{uid}）\n"
        f"等级：查不到（可能 0 级或隐私保护）\n"
        f"验证信息：{comment or '（无）'}\n"
        f"────────────\n"
        f"打开管理界面「入群管理」页可以一键通过/拒绝"
    )
    for admin in SUPERUSERS:
        try:
            await bot.send_private_msg(user_id=int(admin), message=msg)
        except Exception:
            pass


KB_DELETE_KW = {"删除知识", "删知识", "忘记知识"}
KB_LIST_KW = {"知识库", "知识列表", "会什么"}


def _kb_strip_prefix(text, keywords):
    """去掉命令前缀词，返回剩余参数文本。"""
    s = (text or "").strip()
    for kw in keywords:
        if kw and s.startswith(kw):
            return s[len(kw):].strip()
    return s


async def kb_do_add(bot, event, gid, uid, raw):
    """群内补充知识。普通群友只能补充，不能改别人的。"""
    if not cfg_bool("kb_enabled", True):
        await safe_reply(bot, event, "知识库功能已关闭")
        return True
    question, answer = parse_kb_add(raw)
    if not question or not answer:
        await safe_reply(bot, event,
                         "用法：补充知识 问题 | 答案\n"
                         "例如：补充知识 鸣潮3.5岁主是谁 | 磐古\n"
                         "也支持「补充知识 问：xx 答：yy」或换行分隔")
        return True
    bot_ref = get_any_bot()
    nick = await safe_nick(bot_ref, gid, uid) if bot_ref else str(uid)
    ok, res = kb_add_entry(gid, question, answer, "", uid, nick)
    if not ok:
        await safe_reply(bot, event, f"添加失败：{res}")
        return True
    await safe_reply(bot, event,
                     f"已记下（#{res['id']}）：{question}")
    return True


async def kb_do_list(bot, event, gid, uid):
    if not cfg_bool("kb_enabled", True):
        await safe_reply(bot, event, "知识库功能已关闭")
        return True
    rows = DB.kb_list(gid, limit=200)
    if not rows:
        await safe_reply(bot, event,
                         "本群知识库还是空的。\n"
                         "可以用「补充知识 问题 | 答案」教我，例如：\n"
                         "补充知识 本群群规 | 不许发广告")
        return True
    # 群里只列摘要，避免刷屏
    # 不能用 len(rows)：kb_list 默认 limit=200，超过 200 条时会显示"共 200 条"
    try:
        total = DB.kb_count(gid)
    except Exception:
        total = len(rows)
    lines = [f"本群知识库共 {total} 条（最多显示 15 条）："]
    for row in rows[:15]:
        scope = "全局" if row["group_id"] == 0 else "本群"
        lines.append(f"  #{row['id']} [{scope}] {row['question'][:24]}")
    lines.append("发「知识库」查看列表；问问题我会自动查。")
    await safe_reply(bot, event, "\n".join(lines))
    return True


async def kb_do_delete(bot, event, gid, uid, raw):
    """删除知识：需超级管理员，或该条目的添加者本人。"""
    if not cfg_bool("kb_enabled", True):
        await safe_reply(bot, event, "知识库功能已关闭")
        return True
    arg = _kb_strip_prefix(raw, cfg_list("kb_del_keywords") or KB_DELETE_KW)
    m = re.search(r'#?(\d+)', arg)
    if not m:
        await safe_reply(bot, event, "用法：删除知识 条目编号（编号见「知识库」列表）")
        return True
    kid = int(m.group(1))
    row = DB.kb_get(kid)
    if not row:
        await safe_reply(bot, event, f"没有编号为 {kid} 的条目")
        return True
    if row["group_id"] not in (0, int(gid)):
        await safe_reply(bot, event, "这条知识不属于本群")
        return True
    is_super = str(uid) in SUPERUSERS
    is_author = int(row.get("author_id") or 0) == int(uid)
    if not (is_super or is_author):
        await safe_reply(bot, event, "只有这条知识的添加者或超级管理员能删除")
        return True
    DB.kb_delete(kid)
    log("block", "知识库", f"{uid} 删除 #{kid} @群{gid}")
    await safe_reply(bot, event, f"已删除 #{kid}：{row['question'][:30]}")
    return True


async def handle_group_command(bot, event, cmd):
    gid = event.group_id
    uid = event.user_id

    if str(uid) in SUPERUSERS:
        # 这段以前不在 try 里。handle_admin_command 内部的 safe_reply 自带
        # 兜底，但 DB.bl_add、_extract_target_qq 里的
        # bot.get_group_member_list 都没有 —— 真抛了会一路冒到 nonebot，
        # 这条消息就没人处理了。知识库那段一直有 try，超管命令反而没有。
        try:
            handled = await handle_admin_command(bot, event, cmd)
        except Exception as e:
            log("error", "命令", f"超管命令 {cmd!r} 失败: {type(e).__name__}: {e}")
            handled = False
        if handled:
            return True

    try:
        # ---------------- 知识库（放在最前面，避免被其他命令吃掉）----------------
        if cfg_bool("kb_enabled", True):
            if any(cmd.startswith(kw) for kw in cfg_list("kb_add_keywords") if kw):
                return await kb_do_add(bot, event, gid, uid, cmd)
            # 这三行原先只有 add 读了配置，del/list 用的是硬编码集合，
            # 用户在配置页改了「删除知识关键词 / 列表关键词」群里没反应。
            # 配置为空时回落到内置集合，保持原来的默认行为。
            if any(cmd.startswith(kw)
                   for kw in (cfg_list("kb_del_keywords") or KB_DELETE_KW) if kw):
                return await kb_do_delete(bot, event, gid, uid, cmd)
            if cmd in (cfg_list("kb_list_keywords") or KB_LIST_KW):
                return await kb_do_list(bot, event, gid, uid)

    except Exception as e:
        log("error", "命令", f"处理 {cmd!r} 失败: {type(e).__name__}: {e}")
        try:
            await safe_reply(bot, event, f"处理出错：{type(e).__name__}: {e}")
        except Exception:
            pass

    # 内置命令都没接住，给插件一次机会
    try:
        if await plugin_dispatch_command(bot, event, cmd):
            return True
    except Exception as e:
        log("error", "插件", f"命令分发异常：{type(e).__name__}: {e}")
    return False


ai_chat = on_message(rule=Rule(_is_group), priority=10, block=False)


@ai_chat.handle()
async def _on_ai(bot: Bot, event: GroupMessageEvent):
    gid = event.group_id
    uid = event.user_id
    # 排除"@机器人自己"的那一段：否则明文以「@机器人」开头，
    # cmd.startswith(命令词) 永远不成立，所有管理指令都会静默失效。
    raw_text = plaintext_with_at(event, skip_self_id=event.self_id).strip()
    image_urls = extract_images(event)

    nick_for_act = str(uid)
    try:
        nick_for_act = await safe_nick(bot, gid, uid)
        DB.act_touch(uid, gid, nick_for_act)
    except Exception:
        pass

    if raw_text:
        preview = raw_text[:80]
    elif image_urls:
        preview = f"[图片 x{len(image_urls)}]"
    else:
        preview = "(空)"
    log("msg", "收到", f"[群{gid}] {nick_for_act}({uid}): {preview}")

    if not raw_text and not image_urls:
        return

    # 插件消息钩子（返回 True 表示这条消息被插件吞掉，不再走内置逻辑）
    try:
        if await plugin_dispatch_message(bot, event, raw_text):
            return
    except Exception as e:
        log("error", "插件", f"消息钩子异常：{type(e).__name__}: {e}")

    to_me = event.to_me

    if to_me and raw_text:
        cmd = raw_text.strip()
        handled = await handle_group_command(bot, event, cmd)
        if handled:
            return

    name = cfg_str("persona_name").strip()
    reply_to_name = cfg_bool("reply_to_name", True)
    mentioned_by_name = bool(name) and reply_to_name and bool(raw_text) and (name in raw_text)
    talk_value = cfg_float("talk_value", 0.0)
    triggered_by_chance = (not to_me) and (not mentioned_by_name) and (random.random() < talk_value)

    if not (to_me or mentioned_by_name or triggered_by_chance):
        return
    if not AI.enabled:
        return

    # image_urls 上面为了判断"是不是空消息"已经算过一次了，
    # 传进去省掉 enrich 里那次 event.message 全段扫描。
    text, has_img = await enrich_text_with_images(event, raw_text,
                                                  urls=image_urls)
    if has_img:
        log("msg", "识图", f"[群{gid}] {nick_for_act} 发来 {len(image_urls)} 张图，已转描述")

    if not text:
        return

    speaker_name = nick_for_act
    speaker = {"name": speaker_name, "qq": uid}
    trigger_type = "被@" if to_me else ("提及名字" if mentioned_by_name else "主动")
    log("msg", "消息", f"[群{gid}] {trigger_type} {speaker_name}({uid}): {text[:60]}")

    clean_text = text
    if mentioned_by_name and name:
        clean_text = clean_text.replace(name, "").strip()
    if not clean_text:
        clean_text = "在吗"

    history = get_context(gid)
    mems = DB.mem_recent(gid, limit=INJECT_COUNT)
    umems = (DB.mem_recent_user(gid, uid, limit=cfg_int("memory_user_inject_count", 3))
             if cfg_bool("memory_user_enabled", True) else [])
    # 这里要传**说话人**的名字（speaker_name = nick_for_act）。
    # 以前传的是 name（persona_name，机器人自己的人格名）——
    # 用户画像里会写成"昵称：卡提希娅"，提示词也会变成
    # "你记住的关于「卡提希娅」的事"，把用户的专属记忆归到机器人名下。
    profile = user_profile_block(gid, uid, speaker_name)

    # 知识库：命中相关条目就注入提示词，让回答有依据而不是靠模型记忆
    kb_block = ""
    if cfg_bool("kb_enabled", True) and cfg_bool("kb_auto_inject", True):
        hits = kb_search(gid, clean_text, limit=cfg_int("kb_inject_count", 3))
        if hits:
            kb_block = kb_reference_block(hits)
            for _s, _row in hits:
                DB.kb_touch(_row["id"])
            log("info", "知识库", f"[群{gid}] 注入 {len(hits)} 条给 AI：{clean_text[:30]}")

    try:
        reply = await asyncio.wait_for(
            AI.chat(clean_text, speaker=speaker, history=history, memories=mems,
                    mode="group", knowledge=kb_block, group_id=gid,
                    is_super=str(uid) in SUPERUSERS,
                    user_memories=umems,
                    user_name=speaker_name or str(uid),
                    profile=profile),
            timeout=AI_HARD_TIMEOUT
        )
    except asyncio.TimeoutError:
        log("warn", "AI", f"[群{gid}] 硬超时（{AI_HARD_TIMEOUT:.0f}s），已放弃回复")
        return
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log("error", "AI", f"[群{gid}] 调用失败: {type(e).__name__}: {e}")
        return

    if reply:
        # 先决定走语音还是文字，再写上下文 —— 否则上下文里记的是"文字版"，
        # 模型会以为自己一直在打字，被问"怎么不发语音"时否认（真实 bug）
        voice_sent = False
        spoken = parse_emotion_tag(reply)[0] or reply
        try:
            voice_sent, spoken = await try_send_voice(bot, event, reply)
        except Exception as e:
            log("warn", "语音", f"群语音失败，降级文字: {type(e).__name__}: {e}")

        # 历史里存"实际念出去的那句"。若存模型原文，它会在下一轮看到自己说过
        # "人家只会打字"，从而更加确信不会发语音 —— 自我强化的死循环。
        push_context(gid, f"{speaker_name}：{text}", spoken, as_voice=voice_sent)

        # 记忆分析用的流水：带 QQ 才能把"他的偏好"记到对的人头上
        push_mem_turn(gid, uid, speaker_name, text)
        push_mem_turn(gid, getattr(event, "self_id", 0), "", spoken, is_bot=True)
        DB.log_add(gid, uid, speaker_name, text)
        DB.log_add(gid, 0, "我", spoken, is_bot=True)

        if voice_sent:
            # 同私聊：不要硬编码 "..."，只在真的超长时才加
            log("reply", "语音",
                f"-> {speaker_name}: {spoken[:60]}" + ("..." if len(spoken) > 60 else ""))
        else:
            # 走文字时也要剥掉 [情绪:xx] 标签，否则标签会被发到群里
            text_reply, _ = parse_emotion_tag(reply)
            parts = split_reply_text(text_reply if text_reply else "……")
            delay = cfg_float("split_delay_seconds", 0.8)
            for i, part in enumerate(parts):
                try:
                    if i == 0:
                        await bot.send(event, MessageSegment.reply(event.message_id) + part)
                    else:
                        await bot.send(event, part)
                except Exception as e:
                    log("error", "回复", f"第 {i+1} 段失败: {type(e).__name__}: {e}")
                    try:
                        await bot.send(event, part)
                    except Exception:
                        pass
                if i < len(parts) - 1 and delay > 0:
                    await asyncio.sleep(delay)
            # 日志要打实际发出去的那句（text_reply）。以前打的是 reply，
            # 于是日志里带 [情绪:xx]、群里却没有 —— 排查时会以为是发送出了问题。
            log("reply", "回复",
                f"-> {speaker_name}: {text_reply[:60]}"
                + ("..." if len(text_reply) > 60 else ""))

        if _bump_turn(gid) >= CHECK_EVERY:
            spawn_bg(maybe_analyze_memory(gid), "记忆分析")


from fastapi import Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
app = nonebot.get_app()

MAIN_LOOP = None
MAIN_LOOP_READY = threading.Event()


def run_on_main(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        loop = MAIN_LOOP
        if loop is None or not MAIN_LOOP_READY.is_set() or asyncio.get_running_loop() is loop:
            return await func(*args, **kwargs)
        fut = asyncio.run_coroutine_threadsafe(func(*args, **kwargs), loop)
        try:
            return await asyncio.wrap_future(fut)
        except asyncio.CancelledError:
            fut.cancel()
            raise
    return wrapper


def _auth(token: str = ""):
    return token == ADMIN_TOKEN



_plugin_route_index = {}


def _register_plugin_apis():
    """把插件声明的接口挂到 FastAPI 上。

    插件改动后重新调用即可。路径冲突时跳过并告警，不覆盖内置接口。
    """
    added = 0
    for ctx in sorted(PLUGINS.values(), key=lambda c: c.order):
        if not ctx.loaded or not ctx.enabled:
            continue
        for a in ctx.apis:
            path = a["path"]
            if path in _plugin_route_index:
                continue
            if any(getattr(r, "path", None) == path for r in app.routes):
                log("warn", "插件", f"{ctx.display_name} 的接口 {path} 与已有路由冲突，跳过")
                continue
            handler = a["handler"]

            def make(h):
                async def wrapper(request: Request):
                    # token 走 query（跟前端 post() 一致），也兼容 body 里的 token
                    token = request.query_params.get("token") or ""
                    body = {}
                    if request.method in ("POST", "PUT", "PATCH"):
                        try:
                            body = await request.json()
                            if not isinstance(body, dict):
                                return JSONResponse({"error": "invalid body"}, status_code=400)
                        except Exception:
                            body = {}
                    if not _auth(token):
                        token = str(body.get("token") or "")
                    if not _auth(token):
                        return JSONResponse({"error": "invalid"}, status_code=401)
                    try:
                        r = h(request, body)
                        if asyncio.iscoroutine(r):
                            r = await r
                        if isinstance(r, (dict, list)):
                            return JSONResponse(r)
                        return r
                    except Exception as e:
                        log("error", "插件", f"{path} 出错：{type(e).__name__}: {e}")
                        return JSONResponse({"error": f"{type(e).__name__}: {e}"},
                                            status_code=500)
                return wrapper

            try:
                app.add_api_route(path, make(handler), methods=a["methods"])
                _plugin_route_index[path] = ctx.name
                added += 1
            except Exception as e:
                log("warn", "插件", f"{ctx.display_name} 接口 {path} 挂载失败：{e}")
    if added:
        log("info", "插件", f"已挂载 {added} 个插件接口")
    return added


# ---------------- 插件管理接口 ----------------
try:
    _register_plugin_apis()
except Exception as _e:
    log("error", "插件", f"插件接口挂载异常：{type(_e).__name__}: {_e}")


@app.get("/admin/api/plugins")
async def api_plugins(token: str = ""):
    """插件列表（含加载失败的，带 error 字段）。"""
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    _load_plugin_state()
    dis = set(_PLUGIN_STATE.get("disabled") or [])
    items = []
    for ctx in sorted(PLUGINS.values(), key=lambda c: (c.order, c.name)):
        folder = os.path.basename(ctx.dir)
        d = ctx.info()
        d["folder"] = folder
        d["enabled"] = folder not in dis and ctx.name not in dis
        items.append(d)
    return JSONResponse({
        "dir": PLUGIN_DIR,
        "items": items,
        "count": len(items),
        "loaded": len([c for c in PLUGINS.values() if c.loaded]),
        "disabled": sorted(dis),
    })


@app.get("/admin/api/plugins/schema")
async def api_plugins_schema(token: str = ""):
    """插件声明的配置分组，管理界面追加到配置页。"""
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    return JSONResponse(plugin_config_schema())


@app.get("/admin/api/plugins/pages")
async def api_plugins_pages(token: str = ""):
    """插件声明的管理页面。"""
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    return JSONResponse(plugin_pages())


@app.post("/admin/api/plugins/toggle")
async def api_plugins_toggle(token: str = "", request: Request = None):
    """启用/停用一个插件。停用后命令和钩子立刻不响应。

    token 走 query 参数（跟前端 post() 一致），也兼容放 body 里。
    """
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        body = {}
    if not _auth(token):
        token = str(body.get("token") or "")
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    key = str(body.get("key") or "").strip()
    # 严格判 True：bool("false") 是 True，字符串传进来会误开
    want = body.get("enabled") is True
    if not key:
        return JSONResponse({"error": "缺少 key"}, status_code=400)
    if key not in PLUGINS and key not in [os.path.basename(c.dir) for c in PLUGINS.values()]:
        return JSONResponse({"error": "插件不存在"}, status_code=404)
    set_plugin_enabled(key, want)
    return JSONResponse({"ok": True, "key": key, "enabled": want})


@app.get("/admin/api/plugins/market")
async def api_plugin_market(token: str = "", refresh: int = 0):
    """插件市场列表（带每项的本地安装状态）。"""
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        await fetch_market(force=bool(refresh))
    except Exception as e:
        log("warn", "插件", f"拉市场索引失败：{type(e).__name__}: {e}")
    return JSONResponse(market_with_status())


@app.post("/admin/api/plugins/install")
async def api_plugin_install(token: str = "", request: Request = None):
    """装插件。body: {url} 或 {name}（name 从市场索引里找 repo）。"""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        body = {}
    if not _auth(token):
        token = str(body.get("token") or "")
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    url = str(body.get("url") or "").strip()
    want = str(body.get("name") or "").strip()
    if not url and want:
        try:
            await fetch_market()
        except Exception:
            pass
        with _MARKET_LOCK:
            items = list(_PLUGIN_MARKET.get("items") or [])
        hit = None
        for it in items:
            if isinstance(it, dict) and str(it.get("name")) == want:
                hit = it
                break
        if not hit:
            return JSONResponse({"error": f"市场里没有 {want}"}, status_code=404)
        url = str(hit.get("repo") or hit.get("url") or "").strip()
        if not url:
            return JSONResponse({"error": f"{want} 没填 repo 地址"}, status_code=400)
        _subpath = str(hit.get("path") or "").strip()
    else:
        _subpath = str(body.get("path") or "").strip()
    if not url:
        return JSONResponse({"error": "要装哪个？给个 url 或 name"}, status_code=400)
    log("info", "插件", f"开始安装：{url}" + (f"（子目录 {_subpath}）" if _subpath else ""))
    try:
        r = await install_plugin_from_url(url, expect_name=want, subpath=_subpath)
    except Exception as e:
        log("error", "插件", f"安装异常：{type(e).__name__}: {e}")
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    if r.get("ok"):
        log("success", "插件",
            f"已安装 {r.get('display_name')} v{r.get('version')}"
            + ("" if r.get("loaded") else f"（但没加载成功：{r.get('error')}）"))
    else:
        log("warn", "插件", f"安装失败：{r.get('error')}")
    return JSONResponse(r, status_code=(200 if r.get("ok") else 400))


@app.post("/admin/api/plugins/uninstall")
async def api_plugin_uninstall(token: str = "", request: Request = None):
    """卸载插件（删目录）。"""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        body = {}
    if not _auth(token):
        token = str(body.get("token") or "")
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    key = str(body.get("name") or body.get("key") or "").strip()
    if not key:
        return JSONResponse({"error": "缺少 name"}, status_code=400)
    try:
        r = uninstall_plugin(key)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    if r.get("ok"):
        log("info", "插件", r.get("message") or f"已卸载 {key}")
    return JSONResponse(r, status_code=(200 if r.get("ok") else 400))


@app.post("/admin/api/plugins/update")
async def api_plugin_update(token: str = "", request: Request = None):
    """更新已装插件（重新拉一遍源地址）。"""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        body = {}
    if not _auth(token):
        token = str(body.get("token") or "")
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    key = str(body.get("name") or "").strip()
    if not key:
        return JSONResponse({"error": "缺少 name"}, status_code=400)
    st = (_PLUGIN_STATE.get("installed") or {}).get(key) or {}
    url = str(st.get("source") or "").strip()
    # 装的时候存下来的仓库子目录（老版本装的没有这个键，会走下面的市场兜底）
    subpath = str(st.get("path") or "").strip()
    if not url or not subpath:
        try:
            await fetch_market()
        except Exception:
            pass
        with _MARKET_LOCK:
            items = list(_PLUGIN_MARKET.get("items") or [])
        for it in items:
            if isinstance(it, dict) and str(it.get("name")) == key:
                if not url:
                    url = str(it.get("repo") or it.get("url") or "").strip()
                if not subpath:
                    subpath = str(it.get("path") or "").strip()
                break
    if not url:
        return JSONResponse({"error": f"不知道 {key} 从哪来的，没法更新"}, status_code=400)
    try:
        r = await install_plugin_from_url(url, expect_name=key, subpath=subpath)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    if r.get("ok"):
        _old = str(r.get("replaced") or "")
        log("success", "插件",
            f"已更新 {key} " + (f"{_old} -> " if _old else "-> ") + f"v{r.get('version')}")
    return JSONResponse(r, status_code=(200 if r.get("ok") else 400))


@app.post("/admin/api/plugins/reload")
async def api_plugins_reload(token: str = "", request: Request = None):
    """重新扫描并加载所有插件（改完插件代码点这个，不用重启）。"""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        body = {}
    if not _auth(token):
        token = str(body.get("token") or "")
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        n = reload_all_plugins()
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    bad = [c.display_name for c in PLUGINS.values() if not c.loaded]
    return JSONResponse({"ok": True, "loaded": n, "total": len(PLUGINS), "failed": bad})





async def _vision_test_impl(url):
    return await describe_image(url)


@run_on_main
async def _voice_test_impl(text):
    """测试语音生成，返回 {ok, size_kb} 或 {ok:False, error}"""
    if not cfg_bool("voice_enabled", False):
        return {"ok": False, "error": "语音功能未开启"}
    if not cfg_str("voice_name").strip():
        return {"ok": False, "error": "未配置音色名称"}
    vpath = await text_to_voice(text)
    if not vpath:
        return {"ok": False, "error": "生成失败，请查看日志"}
    try:
        size_kb = os.path.getsize(vpath) // 1024
    except Exception:
        size_kb = 0
    _drop_voice_file(vpath)
    return {"ok": True, "size_kb": size_kb}


@run_on_main
async def _chat_impl(gid, uid, text, nickname, mode, dry_run=False):
    return await do_chat(gid, uid, text, nickname, mode, dry_run)


@run_on_main
async def _send_impl(gid, msg, at):
    bot = get_any_bot()
    if not bot:
        return False
    if at:
        await bot.send_group_msg(group_id=gid, message=MessageSegment.at(int(at)) + " " + msg)
    else:
        await bot.send_group_msg(group_id=gid, message=msg)
    return True


@run_on_main
async def _send_voice_impl(gid, text):
    """群内发送一段语音（管理员调试用）"""
    bot = get_any_bot()
    if not bot:
        return {"ok": False, "error": "机器人未连接"}
    if not cfg_bool("voice_enabled", False):
        return {"ok": False, "error": "语音功能未开启"}
    vpath = await text_to_voice(text)
    if not vpath:
        return {"ok": False, "error": "语音生成失败"}
    try:
        seg = MessageSegment.record(f"file:///{vpath.replace(os.sep, '/')}")
        await bot.send_group_msg(group_id=gid, message=seg)
        return {"ok": True, "size_kb": os.path.getsize(vpath) // 1024 if os.path.exists(vpath) else 0}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        # 延迟一点再删
        async def _later_del(p, delay=15):
            try:
                await asyncio.sleep(delay)
            except Exception:
                pass
            _drop_voice_file(p)
        spawn_bg(_later_del(vpath), "语音临时文件清理")



@app.get("/api/vision/test")
async def api_vision_test(token: str = "", url: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid token"}, status_code=401)
    if not url: return JSONResponse({"error": "缺少 url"}, status_code=400)
    d = await _vision_test_impl(url)
    if d:
        return JSONResponse({"ok": True, "description": d})
    return JSONResponse({"ok": False, "error": "识图失败，看日志"})


@app.get("/api/voice/test")
async def api_voice_test(token: str = "", text: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid token"}, status_code=401)
    text = (text or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "缺少 text"})
    return JSONResponse(await _voice_test_impl(text))


@app.post("/api/voice/send")
async def api_voice_send(token: str = "", request: Request = None):
    if not _auth(token): return JSONResponse({"error": "invalid token"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    try:
        gid = int(body.get("group_id", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "group_id 必须是数字"}, status_code=400)
    text = str(body.get("text", "")).strip()
    # 群号必须为正。以前写的是 `not gid` —— 但 `not -1` 是 False，
    # 负数会一路传到 bot.send_group_msg(group_id=-1)，拿一个 NapCat 的
    # 接口报错回来。不折算成 0：那会把"发到负群号"静默变成"发到别处"。
    if gid <= 0 or not text:
        return JSONResponse(
            {"error": "group_id 必须是正整数，且 text 不能为空"},
            status_code=400)
    return JSONResponse(await _send_voice_impl(gid, text))



@app.post("/api/chat")
async def api_chat(token: str = "", request: Request = None):
    if not _auth(token): return JSONResponse({"error": "invalid token"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    try:
        gid = int(body.get("group_id", 0))
        uid = int(body.get("user_id", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "group_id/user_id 必须是数字"}, status_code=400)
    text = str(body.get("text", "")).strip()
    nickname = str(body.get("nickname", "")).strip()
    mode = "private" if str(body.get("mode", "group")) == "private" else "group"
    # 私聊模式根本不用 group_id（do_chat 走的是 get_private_context(uid)），
    # 所以 gid=0 是合法的 —— 以前 `not gid` 会把它当成"没填"直接拒掉，
    # 用户只能随便填个假群号绕过去。
    # uid 允许为正数即可；群聊模式下 gid 也必须为正
    # （`not gid` 同样挡不住负数）。
    if uid <= 0 or (mode == "group" and gid <= 0):
        return JSONResponse({"error": "user_id / group_id 必须是正整数"},
                            status_code=400)
    if not text:
        return JSONResponse({"error": "text 不能为空"}, status_code=400)
    dry = bool(body.get("dry_run"))
    return JSONResponse(await _chat_impl(gid, uid, text, nickname, mode, dry))


@app.post("/api/send")
async def api_send(token: str = "", request: Request = None):
    if not _auth(token): return JSONResponse({"error": "invalid token"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    try:
        gid = int(body.get("group_id", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "group_id 必须是数字"}, status_code=400)
    msg = str(body.get("message", "")).strip()
    at = body.get("at_user_id")
    # 同上：`not gid` 挡不住负数（not -1 是 False）。
    if gid <= 0 or not msg:
        return JSONResponse(
            {"error": "group_id 必须是正整数，且 message 不能为空"},
            status_code=400)
    try:
        ok = await _send_impl(gid, msg, at)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    if not ok:
        return JSONResponse({"error": "机器人未连接"}, status_code=503)
    return JSONResponse({"ok": True})


@app.get("/api/nickname")
async def api_nickname(token: str = "", group_id: int = 0, user_id: int = 0):
    if not _auth(token): return JSONResponse({"error": "invalid token"}, status_code=401)
    if not group_id or not user_id:
        return JSONResponse({"error": "缺少参数"}, status_code=400)
    bot = get_any_bot()
    if not bot:
        return JSONResponse({"nickname": str(user_id), "source": "no_bot"})
    try:
        info = await bot.get_group_member_info(
            group_id=int(group_id), user_id=int(user_id), no_cache=False
        )
        card = (info.get("card") or "").strip()
        nick = (info.get("nickname") or "").strip()
        name = card or nick or str(user_id)
        return JSONResponse({
            "nickname": name,
            "card": card,
            "qq_nick": nick,
            "source": "card" if card else ("nick" if nick else "fallback"),
        })
    except Exception as e:
        log("warn", "昵称", f"查 {user_id} 失败: {type(e).__name__}: {e}")
        return JSONResponse({"nickname": str(user_id), "source": "error"})


@app.get("/api/backup/list")
async def api_backup_list(token: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid token"}, status_code=401)
    items = []
    if os.path.isdir(BACKUP_DIR):
        for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if f.startswith("bot-") and f.endswith(".db"):
                p = os.path.join(BACKUP_DIR, f)
                try:
                    items.append({
                        "file": f,
                        "date": f[4:-3],
                        "size_kb": os.path.getsize(p) // 1024,
                        "mtime": datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M"),
                    })
                except Exception:
                    pass
    return JSONResponse({"items": items, "dir": BACKUP_DIR})


@app.post("/api/backup/now")
async def api_backup_now(token: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid token"}, status_code=401)
    ok, msg = backup_db(force=True)
    if not ok:
        return JSONResponse({"ok": False, "error": msg}, status_code=500)
    dst = os.path.join(BACKUP_DIR, f"bot-{date.today().isoformat()}.db")
    return JSONResponse({"ok": True, "file": os.path.basename(dst),
                         "size_kb": (os.path.getsize(dst) // 1024) if os.path.exists(dst) else 0})


@app.get("/admin/api/requests")
async def api_requests(token: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    _cleanup_pending_requests()
    items = list(pending_requests.values())
    items.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return JSONResponse([{
        "flag": it["flag"],
        "user_id": it["user_id"],
        "group_id": it["group_id"],
        "nickname": it.get("nickname", ""),
        "comment": it.get("comment", ""),
        "created_at": int(it.get("created_at", 0)),
    } for it in items])


@run_on_main
async def _approve_request(flag, approve, reason):
    bot = get_any_bot()
    if not bot:
        return None, "机器人未连接"
    try:
        if approve:
            await bot.set_group_add_request(flag=flag, sub_type="add", approve=True)
        else:
            await bot.set_group_add_request(flag=flag, sub_type="add",
                                            approve=False, reason=reason)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return True, ""


@app.post("/admin/api/requests/approve")
async def api_requests_approve(token: str = "", flag: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    info = pending_requests.get(flag)
    if not info:
        return JSONResponse({"error": "该申请已不存在或已处理"}, status_code=404)
    ok, err = await _approve_request(flag, True, "")
    if ok is None:
        return JSONResponse({"error": err}, status_code=503)
    if not ok:
        log("error", "审批", f"通过失败: {err}")
        return JSONResponse({"error": err}, status_code=500)
    log("success", "审批", f"手动通过 {info['nickname']}({info['user_id']}) @群{info['group_id']}")
    pending_requests.pop(flag, None)
    return JSONResponse({"ok": True})


@app.post("/admin/api/requests/reject")
async def api_requests_reject(token: str = "", flag: str = "", reason: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    info = pending_requests.get(flag)
    if not info:
        return JSONResponse({"error": "该申请已不存在或已处理"}, status_code=404)
    ok, err = await _approve_request(flag, False, reason or "管理员拒绝了你的申请")
    if ok is None:
        return JSONResponse({"error": err}, status_code=503)
    if not ok:
        log("error", "审批", f"拒绝失败: {err}")
        return JSONResponse({"error": err}, status_code=500)
    log("block", "审批", f"手动拒绝 {info['nickname']}({info['user_id']}) @群{info['group_id']}")
    pending_requests.pop(flag, None)
    return JSONResponse({"ok": True})



# ===================== 外观：壁纸 =====================
# 默认壁纸嵌在这里（base64），不依赖打包时有没有带上图片文件。
# 用户上传的壁纸放 data/wallpaper.<ext>，优先于默认。
WALL_STEM = os.path.join(BASE_DIR, "data", "wallpaper")
WALL_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_WALL_CACHE = {"key": None, "uri": None}


def wall_custom_path():
    for e in WALL_EXTS:
        p = WALL_STEM + e
        if os.path.isfile(p):
            return p
    return None



# ---------------- 预设壁纸 ----------------
# 用 CSS 渐变而不是嵌图片：0 字节开销、任意分辨率不糊。
# 配色都取自卡提希娅那张官方 KV（深蓝紫族）。
# 出厂默认壁纸：内嵌的 JPEG（data URI）。
# 这张是**原创角色插画**（不是任何游戏的官方美术），缩到 1920px、质量 85。
# 内嵌后 bot.py 大约多 400KB，换来新装即有像样的底图。
WALL_BAKED = ("data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAQDAwMDAgQDAwMEBAQFBgoGBgUFBgwICQcKDgwPDg4MDQ0PERYTDxAVEQ0NExoTFRcYGRkZDxIbHRsYHRYYGRj/2wBDAQQEBAYFBgsGBgsYEA0QGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBj/wAARCAUAB4ADASIAAhEBAxEB/8QAHQABAAEFAQEBAAAAAAAAAAAAAAYBAgMFBwQICf/EAGMQAAEDAwIDBAUHBwgHBgIBFQEAAgMEBREGIRIxQQcTUWEUIjJxgQgVI0KRobEzQ1JicsHRFiRTY3OCkvA0g5Oio7LhFyVEVGTxJjWzwhg2RXSElMPSJ1Vl0wlGZnWktOLy/8QAGwEBAQEBAQEBAQAAAAAAAAAAAAECAwQFBgf/xAAwEQEBAAICAgICAgICAgEDBQAAAQIRAzESIQRBE1EFIjJhFCNCcQYVgZEzUqHB8P/aAAwDAQACEQMRAD8A+/kREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREIyEBFRreEY3+JVUBERARDyVkcnecXqubg49YYQXoiICZQ8l5oaZtNNPMJZX967jLXuLg3bHqjoPJBmkijlbiRjXDngjKuAAGAqNe17eJrgR4hXICKmRnCqgIiICIiDzmjpzMJe6bxg5DhsVlw7j57K9EBW94zj4eIZ8M7q5eZ9BSSVgqnwMdMBwh56BB6UQDARAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREGNzXmRpa/DRzbjmsi88FZBUVM8ETnGSBwbIC0jBIyOfP4L0ICIiAiIgIiICIiAiIgIiICxymUM+iDScj2j0WREFByVUQ8kBFRpLmgkEHwKqgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAqHlsqogsjDwwCRwc7qQMZV6IgIiICIiAiIgIiICIiAiIgIiICYREBERARCqDON0HmqKKOfLmufDJ/SRO4T/1+K9EbOCMNLi7Axk8yrkQeWW30s1dFWSRAzxexICQR5efNepEQEREBFQkAZKAgjIQVTKplWOjLpmP43DhzsDsfegyIisLCZA4OIx06FBeiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiKE9rHaLb+y7sruWra5rZpYW93R0pdg1NQ7ZkY953J6AE9EEwjq6WWqlpo6iJ80WO8ia8FzMjIyOYysy/Luz9pddW6pqNVV95rYNQ1FU+olvFsqO4qI3k5DMew6LYDgeC3Axsvp7s9+VRNTxxUfaXBDUUGzRqi1xERs86unGXQ+b25Z7lbjYPqZF5LZdLderTBdLTXU9dRVDBJDU08gkjkaeRDhsQvWoCIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICZRUAA5IKoiICIiAiIgIiICIiAiIgIiICIiAiIgIsU4n7r+bmMSZHtgkY68lkHJBVERAREQEREBERAREQEREBERAwiIgIrQ1wcTxHHgrkBERAREQEREBERAREQEREBERAREQEREBERARUc3iaQCRnqOiNBDQCSfMoKoiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAioHB3I+SqgIiICIiAiIgIiwNq4X1r6VrvpWAOc3B5FBnREQEREBERAREQUyM4VVQtBIPUKqAiIgIiICIiAiIgIiICIiAiIgIiICY2REHmZSCO4y1YmmJla1hjc7LG4zuB0O+69KxTzNgj43h3DkD1Wk81lCAiIgIiICIiAiIgIiICKxs0b5nxNcC9mOIeGeSvQEREBULckHJGPBVRAREQEREBERAREQEREBERAREQEREA8l+dfyzu1mbUXabNpO3z/APdWniaduD6stYW/Sv8APgBaweB4/FfcfarraPs77Hr/AKvc0PloaVxpoiM95O71Ym465e5o92V+QerrlUXK/Tvqqh1RNxuM0pOTJISTI8+JLiStYTdStDHUzxVbJaeR0Ug5FpU103r2rttSx08r4iPzkfI+8eHly8ll0Fpi33C1VdxvFI2qinzTQMeccAHtSA9H5wAemD4rQ6j03U6fubadzzNTykmmqsY70D6p6CQdR15hdNul4rMfJ9KdmXazftG1wr9F3GlpY53B9TZ6gn5vrD9Yho/0d/67MN8W4X2j2adtele0cfNzO8s2oo4xJPZK5wEob+nE4erNH4Pbt44X5H2u+V9oqQ+CT1M7xk7H+C61prXdFc4qeGpdK2aB3ewyQzGGopJOkkEjd2Ozg4GxI3BUuG2Nyv1gRfLHZX8piSightPaXXsrbeXNjp9TxRcBizybXRt/Jnp3zfUPXByvqKmqqespI6qknjnglaHxyxODmvaeRBGxC5DKiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICJlEBFhqnVDKV7qWJksoGWse7hDj4Z6JHMC1olHdvI3aTyPv6oMyJlEBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQFQkAZPJVVHNDmlrgCDzBQBjGQqqjWhrQ0DAG2FVAREQEREBERAVMDOcbquUQEREBERARUJA5qoOUBEVDuEFcqjhxNwtRPS3qmq3z0FXHNE459GqM7HycOXuWWGvuRfw1Fnkj23c2Vrgg2Dg8REMI4sbZ5LyRPubY3d/BA5w9nu3kA/aFa64VJl7uO2VBP6Ti1rftyV7mFxYC9oB6jOUGOCSWWnDpYXQvPNhIOPsSnbO2Mioe17snDmjG3RZSQ0ZOwVGva9uWnIQXIiICIiAiIgIiICLxzRV7rtBLFVRtpGtcJYXR5c8nkQ7O2F7OiCgcCSAQSOfkqqgY0OJAAJ5kdVVAREQMIiICIiAitBdxkFu2OauQEREBERAVr3cDC7BOBnAGSVUnAyqMeJGBwzgjIyMIMFDVMrqGOrjiljbIMhsrOFw94XpREBERAVocS4jBGPvVyICIiAiIgIiICIiAiIgIiICIiAiIeSD5F+XBrcUNhsWjIJN3mS71Tf1YwY4AffI8n+4vzzm7yWYgNL35wB1JK+hvlYawbqbt01DURPL6eCoba4CTtwUww8jyMr3/AGLjWi7eK/WFK+RmY6fNS7I6j2fvIXXH1Fxm8pHTrTbharLSWtmP5vEI3EdXc3H7SVkuNupLtbJbfcIu8heNwDgsI5OYehHQr1M33OSryON2MqPqyTXi4ne7FWWa7m31p7wkGSCoAwKhnjjo4fXZ058lqsz00wkYSwg7PaeS7berTTXq0vt9U8xkHvIZ284ZBykH7x1Gy5LPS1FJX1FtuMAhq6c4lYPYIPJw8WnmPDkrK+dzcXhfXST6Y1xPSTsjqZeBxGDJ0cDsQ8eB6/evorsm7Z9R9n8kUenmx19je7jqNOTS8MZzuX0T3fknf1Z9R3ThOF8gTQPgfxsHq9fJbuw6nq7VKGEmSDO7M7j3K2bcpX679nvaZpPtM05866Zry58Z4KqgqG91VUcnWOaM7tcDkeBxsSpgvzD0hrWriu9Nqaw3qe23mnYIornT7vDf6KeM7SR+LHZIHsuX2L2TfKKt+q6qm0xriCnseoZAG09Sx+aG6HxgkPJx/o3esuVmls07uiA5RRBERAREQEREBERAREQEREBERAREQETKZCAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiE4GVRpy0HBHkUFUREBERAREQEREBU5hHtDmFpzgjGyw0lLFR0cdNCXmOMcLeNxcceZPNBhoKOqpHVHpFxmrBJKZGd61o7pp+oOEDIHmvaiICoWhwwQCPAqqIMYi4T6hLR4dFc0OA9Yq5EBERAREQFhqoX1FJJCyeSBz2kCSPHE3zGQVlOcbLT6dr75cKOokvtlbapmTvjjibOJeOMcn5HLKDZ0sT4KOKGWd872NDXSvABeR1OFmREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEVoBDic5B6eCuQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERATmFQjIwqoLGRNjc9zc+ueI5OVeiICIiAiIgoQCcqqIgLG1jxISZCQeTcDZZEQEREDAREQWvIDCSMgfFGFrmBzMYO4KuQDHJARUJA5qqAiIgIiICoeSqiDHC974g6SMxu/RJysiIgIiICIiDBVSzxMaaeESuLsYLsYHiqtmf3HeSQvaereZ+5ZkwEFrHh7A4AjPiMK3vmd/3OTxY4sY6LIqYGcoKoiICIiAiIgJhEQEREBERAREQEREBERAREQEREBERAREQEREBaDW+o6fSHZxfNUVJAjtlDNVEHqWMJA+JwPit+uA/K81E219gjbC2UNlv1who3N8YWEyyn3cMePirB+b2r6merurY6mUvna0Gd55mSQmR5PnxPKkXZ9Qd1bquvkGDNIIgfIbn7yPsUOuVUa26VFW787KX/auo6apBSaXoacjD+67xw83b/vC6u/xpvLbagAYY1uVc4epxbbq5vqNJ5HphWbOb0+Ky93kxO8Co5qzTb77QR1dJiO7UgJpnnlKOsT/I9PA+8qTEettvhWAB7cc/ErKZ6zmq4vDLHUU3eBjoyCY5IXj1onDm0gryVFMI8Sx/kycYPQ+Cm+u7GaOpfqmljc6J2I7lCzq3kJh5jYH4HxUYBDHFj8SMcOY+uD1XTF8zkxuN0xWy8VlqrBPTS8LuRDuTx4HxXV9P6mo79bHUc0ccjJMd/Ry7tz0Lf3EYI8QuP1MBjkweR5FvUKtJWVFFUtkgkfG9p2IRJdPvPsv+UXdtGxRWrWElVfNNRta0Vm8tdbG+L+tRCB9Yeu0e0CBlfXtqu1tvdmprtaK6CuoalglhqYHh7JGnkQRzX5P6W1rHWOjgqH91VR7jhPPzB/yV2nsy7VNQdnF7fWWCRtVbqg8dbYJZAynqT1lhJ2gm93qO+sG81i4D9A0US7P+0XTPaRpdt507VPJae7qaKob3dRRydY5Yzu1w+w9CVLVgEREBERAREQEREBEWrv8AqKyaXs0l2v8AcoKCkYQ3vJT7Tjya0Ddzj0aASegQbRafUWqtNaStZuWpr7b7TS5wJaydsQcfBuTufIbqGTXLtG1u8NsUf8iLE7ncLhTia51Ddt4qc5ZADvh0vE/+rHNYZNP9mHZZE3Vmo62N1ycQ0XzUFSauumfj2Yi7LgT/AEcTQPBqJt729ptdeTjRegdR3mM44a6thFqpMHrx1HDI4fsRuRze2W5Rvca3RmnWH2WxwVF0kb/eLoG5/urzN1lrzVO2iNCyUFI72btqt7qNrh4spWAzPHk/ul626B1NdwX6u7R7zOHHLqOwtbaqceQczinPvMv2IPPUWLWVDSuqNQdsk9JFy7yC20VJG34ytefvUTOobHSSyMi+VN6RMBkxGS01HB/djgDlP6Dsk7N7fP6QzR9tq6nGDU3FhrZj/rJi533qV01tt9E0No6Gmp2jbEUTWD7grtY4M3tHudvmdLT/ACgdFXOIDIiu1kMH2yRTMA/wraWXttvdVUCnbT6E1Y/i4S3SWqYXVGP7CpEfhy7xdtMbCMFjSPctRc9I6VvUZjvGmrRcGnmKujjlB/xNKgjdP2waNZURUmopa/SlXK7gZDqOkfQh7vBsrh3T/wC68qdRSxTQtlhkbJG8Za9hyHDxBUBn7HNJR0z4bBLdtOxvBDoLZWvFK4EYw6lk44HDyMag83Zfr7RE7q3Q1whlixl0VpDKB5Pi6jeTRzdM8ApnbHD0HeUXG9Odt7aasltPaFRstlRT4E1fDE+KOEHk6qgk+kpgdvpPpIT0mK7BDPDU07J6eVksUjQ5kjHBzXA8iCOYQZEREBERAREQEREBERAREQEREBERAREQEREBERAREQWvaXDZxb7lcOSIgIiICIiAiIgIiICIiAiIgIiIKEZGEAwMZyqogIiICIiAiIgIiICIiAiIg8ddX+gtZI+lqJYycOdCzj4PMgb49wK9EM8U8YfE8PaeoWTCoGgHIG6CqIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAtPUVGo49RxxU9toZrW4DjnNQWSs8cM4SD9oW4RAHJERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQERWgu4jkDHTdBciIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiIC+G/lwajM3aDZbFHOeG02iWqc1p5S1Mgib8eGNx+K+5DyX5hfKj1C6+fKC1fMN2MuENti/YpoQHY/1jyVrDscMhpzUVkVOzJMkgjG3icLscXdsbwNGzdguZaZi7zU9K7H5MmTfyB/6LqMIxEBtsFuvV8aeqzb8WQcs81Qg+74K9gPMnc7cuSq5mGkA7+aj0RiAJ8isT2YZ4+WFnw7hztnohZ62OvNZ6WPLJFHJC+OWMSRyAscx42LSMEHyIOPiuVXGzPs17fZSXuhLDUW+Qn2ousZ827j3brrhYe99UHmtTqbT8moLA6KjPd3Gld6TQyeEo6e53L7PBWXTly8flHLWsZUQGB+3gf0CtZLE6GYxvHrA4Pmty/E1LS3WKItp6niYW4/JTNx3sJ82kgj9R7PNUqab0um44wDK0bY6jwXSV4WnbKWPEjHlhByCDyKnem9aB7o6O5PDX59WY9ff4fgVBOLZWYw7YKpK+mNLatvOn9S0t/09eX2u7U/qR1GOKOZn9DUM/ORHz9ZnNvgvtvso7a7H2kxutFTELPqmmiEtVaZX542H89A787EfEcuR8/y303qyWgLKWseZKbkD1YP3hdh09fiaihq6a4TU9RSSCegr6V/0tHIeboz1b4sOzuRHVYuO17fpwi4z2P9uNJrOWLSmqXU9BqpkPeRlh4YLrGOc1OT1/Sj5tOei7MuQIiICIiAiKLa41RVaetNNS2ajbX3+6Teh2uicSGvl4S4vkI3bExoc9x8G4G5CCzVusnWargsFhoBeNTVjC+ltrZOBsbAcGed+/dQtPN3Mn1Wgk4Xi0/op1LdWam1dXDUGpQCBVPj4IKMHnHSxZIjHTi3e76zjyHr0Zo6m0pbZ3Pq5bneK94nud3nH0tZLjnj6rG8mMHqsbsOpOmqpKjtPqam1WutqKTSET3QV1wppCyS6PacPggePZhBy18g3ccsYQAXImlKvV171VdqixdmsVLJFTyGGt1NVtMlHSyDnHCxpBqZR1GQxp9pxPqLbac7NrFY7wdQV8lRf9RvBD73dSJZ2g82xDAbAz9SMNHjnmpRbbbb7Paae12ujgo6KmjEUNPAwMZG0bAADkF6kUxhERAREQEREBERBo9SaQ0/qujZBere2Z8WTBUxuMU9OTzMcrSHMPjgjPI5Gy5GLJr7sPrZLhp2OXVmhi4vqrTEwMq6EZJMsDG4YcZ9ZrA0HAPA08Tz3hCMoNVpvUdm1bpik1Bp+virrdVs44pozz6EEcw4HIIO4IIK2q5lJbIezbtPp7laom0+ndT1Yp6+lYMMpq9wPdzsA2aJCOBw2HEWnqV00ckBERAREQEREBERAREQEREBERAREQEREBERAREKAisZI1+eE5xzV6AiIgIiICIiAiIgIiICIiAiIgwyvnYcxxCQdQHYKvZJxYy1zT4OCvTCAiIgIiICIiAiIgIiICIiAiIgoc42VRyREBERAREQYZvSeOPuO64eL1+POceWOqzIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiDAY5/S+8E/0WMGMtHPxBWVpJcQRgK5EBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQY6iZlPSyTyu4WRtL3HwAGSvx41vdTedTSXNzy51ZPVXB7ndTNUvcD/ha1fqp2xXz+TnYDrK9A4fTWepcz9oxlrfvIX5L3vDLr3AGBTwxQBv7LAD9+VvDsbbRUAfdamUt2jiDPtI/gV0SMEsyQoZoiMCjqp/0pms+wH+KmrRhm4HlkLVe7hn9WTfhw4IATk8fx8VUezl2ykWkNPWjU19jtNdqWKx1M5DKWWppjJBI8/Ve8PHdk9MjB8Qo7dRHeDDs4KuA43cAwSOeOi7Fd/k29qNsDn0dvtF6jb/5Ct7uT/BKGD/fW47O22XT88Whu2fs5oaKCeYi33a722NndyOd+SfUYw8E+y8HY4B2xjLleWSenL+z2x6bv3aBR6f1YaiKkuf8AM2VdM/u5KWc/knAnYgn1SCCPXHgpB2kdieseziOW6ejPv1gjJe65UEJ7ynb176AZLf2mZb1PAvoC/wDyZ9BXSmc+wTXPTtVza+lqnzxA8weCUnGDuOAt966VpgamprWy16pfFUV9OAz5ypRwxVreQkLCcxyeLdxncEjYSvPef3uPzT05pOi1H2oVWiXVlPBSa3hM9orw7MNPd4Q50L8j2myjvI3f/dHiwLnE1JX2q71VrulJLQ19HNJT1NLIPpKeVpw6M+4/aMHqvur5QnyfDSWmo7S+yaldRXy0VLL4+007Po5ZoXd531O0exLseJg2kHg7BPLPlJ6Nt+uuzXT/AMp7RdMG0V1o4Pn6nYOLhBAayc46xu+hkPgGnoVcc/bllZbuPlO50g4/SIx6sntAdD4/FasFzNjuxSshj4iwjbGCCOi0FTRlkjmM3xyH6Y/iu2mNPG4cBD2Hbp/BSLTmpZ7VMGPy6nJ9aPP3jwKjjHFjiw7s6gqrmGMccZy0nY4+4rKPoW03qjuluijfUTmISCanqaeTuqikmHsywu5se0/byOQvrjsX+UDFc/RtF9pVypKe+iMmgvJIiprzG3biGdo5x9eM9dxsRn82bDqSrs9aDx8cROHRk8/4e9deo6606n026jqg+ooZyC+Nr+CSF45SRv8Azcjeh+By04Us232/QeXtr0rWvfDouivOt52OMbv5O0hqIGO8HVLi2Fvu48+S8kmqO2+7N/7l7NLDYm52k1FexI8j+zpWPHw41w3sQ7WdTXfUFv7INX61ZZKuClHzDcqC3QN+fKdoxjMoe2OdgBDowzfBIzzP0g7RMtU3jqNd6uqD4x1scH3RRtC56Z+0WfQfKMqpXOfq/s/tw+qyns1XUj/E6VqrFR/KHpnAt1f2eXLGxjns9VTk/wB5szsfYpO3QUkW9NrjWcLh9Y3Bs33SMePuQWztBtj+Oi1Jb79ECP5veKQQSkeU0Gw+MRUVFK3tI7VNGUclbrrssjuVtiBfNcdH1xrDE0ZyXU8rY5DsPq8Sv7LNQ03apfbr2s0rZjZ3OdaNOmZpYXU0bgZ52jp3swLT14YGjqpfBq5pnjtF2oprJeZmkU0FWWviqZAM4ilYeGQ9eHZ+N+ELQ6cp6vsq+TTaqapt1TdLparRDG+hpQDLW1pYMxtwMZkmcRnl62T1KDwa/wBQ/wAo9b/9llouYoImU7a7U91Evd+g0TyQ2Br8gNmm4XNBzlsbXO6hbaLtd7EtNUUFlg7Q9HW+CjjEMVKy4wtETGjAaGh2wAC5PbOxi4OlluN70fbtZatuVU653m6alrZGWmCpeAO6pqVod3rI2gRtcWDZuzty0dBtmhNR2i29/LXdnmnqdrTxstemhHGwftvlH4BU03lP26djVVKIoO1LSLnnYNN0hBP2uUhpdd6JrsehawsNTnl3Nwifn7HKM2qmttc8tqL1adSNOx9FsrZIh/fjyPtK9Ff2YdnNyh4bj2ZaZriefe2qmafvGVBOYamCpiElPNHKw8nRuDh9oV5e0DJIHvXJZuw/smjcXU+gZLOeklolnpfs9GkB+5ZKbsuoWEM0t2p69tj4+ULLyKxrf2o6psiDqoljPJ7T7irshcrOnO2i0ummt2utNamZkcFNfrIaWQDznp3Yz590fcrZe0bUWnD/APHnZRdqSlaQHXTTz23Wnb4vLGBs7G+Zj2QdWyFVRTSWtdE64oHVejtUUdzaz8oymqMyReT4z6zD5OAUi7urYSWTskHhIzB+0fwQelF5zUOjGZ4XMA5ub6zf4/cszJGSMD2Oa5p5EHIKC5EVDyQQftFEl6ZatG0DBJWV1dT1Urh/4angmbK+V3hvGGN8XO25FTgclrrZZaS2VNXVs45ausfx1FTKcvfj2W+TWg4DRsPeSTskBERAREQETIVMhBVF4q+72q1wmW53Ojoox9epmbGPtcQoPde3nsfs9Q+nq+0KySTtGTDRzelPH92LiKDoqLg9y+Vn2bUzX/Nlr1Tdi3k6C2mBjvc6csChd2+WNUd0RZdAQwuzgOu13Y37Wwtk/FXQ+rFTIXwvePlZ9p9YSKWt01Z2HpR26SqeP70sjR8eFcz1H2+azuYkbee0rUkkb/ahpqyOhZ7sQMa74cSvhR+kt0vtkslMai83egt0I/OVlQyFv2uIXl09q7S+rKaao0xqG2XiKF/dyvoKlkwY7wJaTgr8l6zXenqqtdPPTwVtQTjv63vKyQn9qUldS7DNb3CzduWjL3p5rI/ne5tstxhp2d2yrhkHqlzRgEswSDz2HglxsH6YIqDkqrIIiICIiAiIgtcwOG4VQMDGSfeqogIiICIiAiIgIiICIiAiIgIiICIiAiIgIiZQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQUzvhVREBERAREQEREBERAREQEREBERAREQEREBERAREQcQ+VndX235Lt4p48cdxqaWhGfB8zSfuaV+Y1yn7+81MnR8xx9q/Qj5bN0fT9m2lbU3lVXl07x4tip5Dj7XNX52ufmZ5PVxXTDpK6LoxnBp1r8e3NIfwH7lMbfQVl0uMVFb4mS1Mx4Io3zRxcZ8MyPYPhlRLSW2k6Y74LpP8AnKk8QYY8PAdtuHDIR9Hi/wAI6ra/k5drtxcxstgo7a085K6viA+yIyFTGh+SbrCRnDcNW2KDI3ZDTTVH3ksXLdLa41dpdrI9Papu1vhbyp46gyQj/VScbR8Au26H7du0W4XO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4kP3qIXXtU1dc6Yw3XW+qK5h9oT3iWNp+EPd/guBR1t6r3lkElRLjmRyHvPReaaWQPLJKkyvHg4kKzCG3VKzUdnfMJ6ttFLKDkyVI7+UH9qTJ+9eGp7RKeFnBHVSvaOTYhwj7AuZOe89cKnBkk4296eKeSYVfaBPLnuKbPgZDk/flaap1de5xgVfd+TAtWIDw8cmzRvk8lu7Xpe7XUB9PAynpj/4qoBAPuHN/wCHmiyW300U1bcKhmZamUDqXOwstssV1vJzbrfNVNzvO4cEQ9737LpNr0VZKDhlqIjcKgb95VAcI90fL7cqSkAxjqAMAdB7h0U29PH8a3tx+9aQuFhtkNZWV9K6SSYRdxAHnGxPt4APJfQHyZrQ6t7a+zmiY3iEVzqq1+eghp3HP+Jy5PriXvbla6Dbbind9uBt9q+nfka6ffW9s7bo5hENlsD3Z/rqqc4/4bFMunDkxmOWo+7xyREXNkREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQOixsi4HvcHPPEc4ccge7wWREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREHxr8t66vOpdGWQDLGUdfWOHme7jafvK+Es+t5lfZ3y0K2Gbt1tNDxevTacLyPDjqT/+zXxdnku2HTLp+kD/APCVKP1pP+cqV0NPUVlZDR00E1RUzO4IYIWGSSQ+DGAZJ9yh/Z/e9EUVtjj1jWagbFE5381s1HE+SXfP5WV4a3/A4r6K0f8AKV7NtE03/wAFdjctKC3hdXVtxAqpR+vIY3k+7jwud7e2cusZEv7PPk03y4shuWt6l9npCMi3wEPqnD9c7tj+GT7ivpfTelLDpGzi3adtUFHB9buxl0h8XvO7j5krhuhPlLai7SNVCxaS7KnVMjQHVFRJeQIqVpzh8pEBwNthzPQFfQtI+rNGw1sUMdQW/SNgJc0HyJAJ+xS1wzzyy7evG2yuVB7RVeijmKyTi4D3ZAdjbKuwg5oMURkMI71oY/qAchZTzVeQWN0rB1QaXUlPqqqsUsWlLpa7bczju6i40T6uIDqO7bLGfjn4L4p+VVd+3LTXZt6B2kO7KNR6fuM/c04p4Kimr4pQNp4GPlyHs23jLsZ9YYJX0trztjfaNRP0J2dWOTWeuizjdbaeQR01saeU1dPyhb14fbdyAGcqI6Z+Tgy9a4Z2kduV5brzVpAbDROZ3dptoByI4YT+UAPV+xO/DndWD4r7JPk19pna6yC401GLNYH44r5dWGOOUdTDH7U3vGG/rr7q7LPkx9mXZeyKtorX893toGbtdWsllYfGJmOGLfwGf1iuzsjYGhgxwgYACuaCNslW5C1rBx5Oc9c9VcefL4q5o25e5UweWVlpX62MqgIwqbYTl1RlkzluwVOL3qL6m13p/SkkVJWzzVV0mb3lPaqCPv6uYcuMRj2WeMjsMHUhQGt7Ru0avfx2uzabsMB5fOU0lwnx5shMcbD5CR3vQdnBwFipaenpYBFTwRwxgnEcbQ0DJydh5ri9Lr7tNophJWv01fYfr08VNLbpf7khklbn3ge8LpumtWWvVVE+SkZUU1VBgVNDVNDZoCeWcZBBxs5pIONig9V+pq+vtwoKGsfRNmfioqIj9KyLqIz9V55cX1RkjcBR5l4sNnii07Y2Mk9FHA23W2B0/o48xGCAc/pEeJ3UouVthutCaOplqWQucC8QSmMyD9Akb4PUDCpBT2qwWwQ08dLbqKPkBwxRj/PiixomT3WVvefMF3wRzPcg/YZVkbcaeF7WVomoXE4ArY+6BPgH+yftWypNT6cr6z0Sh1Baqmo5dzDWRSO+wOytuWB7Cx7NjsQ4ZBWTbUsBYRuQSMheyGapby+lYPPJXk+ZvR2h9qm9Bf8A0PDxwH/V59X+6R8VSK5up6gUt0gFFO5wZHJxZhlPQNftv+q7B8M81pW3EjHM4h6p67clqLjp+13CpFZJE6nrcbVtI8xTe7jZuR5HI8luHgPiPiOqs/ySjKNuqNUWI5qIP5RUI/OQMbFWRjzZtHL/AHeA+DStrbrxbbzSvnt9Y2oa36OVhBbJE79GRhwWHyIBXvB+IWquVkpa+f0+EvpLkxvDHWU+GvA8D0kb+q7I93NB8/dsXyTNO60E950BVx6XvT3GR9HwH5uqnnmTEPyTj+nGN+Zaea+HNe6I152ZXv5p1hYKi1TSE91JjjgqvOKVnqu/HxAX60W2W4ve+kudOGzR7iogH0Mw8R1afFp+BIWHUembBquwzWXUtno7pbpxiWlq4RJG/wA8HkfMbhal0PxmludY959fHkvO+sqH+qZXHz5r7K7a/kUVtsZUai7I31FypGgySafnkzURjn/N5D+UH6jvW8CeS+PqmjnpKuWnqYJYpoXGOWOSMskjcObXg7sI8Duuku2XgMkjx7ZO++SrSXh3PHks5YOhPllVEeDkjJ6LUGJuSMk4Cux03KysjfJIyONj3PccAAZJUloNNxRQ+m3uVkMTdzHxY+0/uCDRUFrrLhJwUcD3+J5BnvK3j7FZ7JCye81PfSkZEDBz9w5n3nAVa/VsdPD6JY6dkTWjAmLeXuZ/H7FFpZJ6iYyzyOkkk3L3nJKyNlcr5PWQ+iU8bKOjHKCPr7z1+4LVHA5DCqGZd+7C9LaeNkJlqX92wLWxgiifK7DRjzWyt1rqa+rNNb4O/lH5R52jj956e7mVvbLpSruHBPXiSipNi2EbSyjz/ox96ntDb6SjoWUlJTshib7MbBgA/v8Aes7eji+Pcu0ds+kqKimbPWH06q5h8jfooz+ow/ifuUkEb/E5HNy9bYxjG3JU4PVzzWXuw45jPTB3fQu+9WOBDduS9Dh6vCdvNeCuqI6K21FTIciKMv8AuTTW9RALm99z1zVGHJDS2mj+G345X6B/IxsPo3Z3qTU5aOC43MUlM7GMwUzBG3HlxF/xXwBppjoqt1wmHEadklU845ua0kffhfqx2H6Xdo75PWk7BKwsqIreyaoDufey/SyZ8+J7lMuny8ru7dAREWGRERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQFZI97ACyMv3xgFWyOla4FjA5vXfBV7XtdsOY5g8wgqM43VURAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERARU4hxYyM+CqgIiICIiAiIgIiICIiAiIgIiICKhOyNJLQXDB8EFUREBERAREQEREBERAREQEREBERAREQERE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kjulK2a5gcuP6QGOL+r5nG5x6q+erx2Zdotm433LQ9+hawbvhpTO0f34uMYXOPRxZ2+0SIPDj7SrHtzsVcQA4jJBBwQeYPgVa7HCB06lbehhdjcFuR5qKazq+7tUdEPbqJMHb6o3P7lK3YGc7rnl8qfnPVr2Rv44qcd03B659b7/AMFY482WsUx7JtJO1Z2g6e0wyIPN2ucNPLluwp4z3sx+DIwP7y/V9jWsjDGgBoGAB0C+JPkZ6LNZ2kXPVtVDmGyUDaOAnl6TUHje4eYiawf3l9urGd3XzxERZBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQFTAzlVRBQuaHBpIyeQVVTAVUBERBQjIwVUbBEQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERARYX+kd6BGI+DqXZz8FmHJAREQeU2+kN1bcTEPSWs7vvMn2fDC9SIgIiICIiAiIgIiICIiAiIgoTg8kIBGCqqzhf3ueL1cYxjr4oL0REBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBRnWGubJoq3tnuja2onl2p6Khpnzz1DsgcLGtHPJHMgKTLj/AGg3+46yvNXoLTVxloLTSkR6hvNK/EreIZFDTu6TPbu9/wCbYf0iAg+J+3fVlVq35TepKmttrLdPT2+ihdSsqY6kxfW4XvYS0uGfWaDscjfGV88TE+kyZ/Tdj7Svort9oaC0/KauVstVLFR2+n0/bYqaliGGxRNBAaPdj+K+d6jIr5hyxK7H2ld8OipBpXW980PNW1ump46G6VUJpm3Rg/nFLE78oIDyje7YGQesAMAjJzbpez3zXuv7Xpe3Pllr7tWNpGPJJIdI71pSd+Q45CeexJUbeTsMhSC33efTtFU09slfDcqyIwVNVG4h1PAecLD0Lvzh8PU5F+c6H6Ma0+U12R9kVqbpCwsnvlRZqdlMKS1Y7imEbeFsb5z6uQBjDOIgjcBdr0tcrld9F2m7Xq3sttfV0sVRPRMkMgp3uaHd3kgZxnGcDcL8lezuz/yr7W9JaUZFxRV92pKe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5V9oekKKato5yJNR2CmbxuqmgAemUzP/ADLWjdg/LNGPbDCtMPDK6bsRu3fRZk7M6qXMzG7nTUjj+UH/AKFxO4/Mk5H0ee765FIyWMSMeHMcMgg5BC1tsuln1Npemu1pqqa52m4QCWGeEiSKaJ4+8EdPguaMqKjsUukVqq5nv7Oq2YQW+ulOf5PTPOG00zif9Ec44jkP5IkRn1SwgOyDki0kVwmjnPekuAOHMxuP+q3DXtewOacgjIKCp6LW3i0Wu/WSotN6oIK+gqWGOamqGB8cg8wVszzWnvlbd7bZ5KyzWT53qI9/QhUNhfIPBjn+rnyJA8wg+Bu3nshvPZdqr0uCaet0zXzfzCukcXuhkO/o8x/T/RefaHmDnjdVX+j0b5X/AFRvuvsrXHyqexa+aSuWkdbaQ1a+mqhJSVtBJRRd5DI04LTiXMcjXjY7EEAjovhe/wBbRVFymprPV1dVQd870eetiEU0kWTw96wEgSYxnBxnw5Lrh+nWZ3Ty0LJKmpfO/wBdxPPxJK+0PkX6K9K1Xfdd1MGae2Qts9C87gyv+kqHjzA7tvuK+SLbS+g0hqeAudC3jawD2nn1WD7cL9Quw7QruzrsH0/puojDa9tP6TXnq6pl9eTPjgnh9zQmd9acnQ0RFyBERAREQEREBERAREQEREBERAREQEREBERAREQEREBFY9zmkYbxDyKuB2QVREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERBQjIwVY1r2HGS5vTPMfxWREDoiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiE4QeS6XS32Wy1V2utXFSUNJE6aeoldwtjY0ZLiegwuY2K2XXV2p/+0vUUEtI3u3Q6etk7MPoKV4w6oe3pUTDHmxhDOZevSSO1XU4nljL9D2eqBhafYvVXG78of0qeJw2HKR4zuGDild8u9JZ7VPc61/0UWGNYOcsjjwNjHiXOIA8ygieoJ2sq47dH+aAfMf8AlZ+/7PFa1pJcGMBJOwA3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BkSRvGCCPMHC5B8n+KbTLtS9l1fNI+s0lPFQQOkO9RQEOfRTe/uSyE+dMsjuHwUT7QtFUPaD2d3HS1dPLS+ktD6esh/K0lQ0h0M8Z6OjkDXD3KVj2E5oPnii1beL52WW7XV2pRFrns6uMlHqSkgbvI1jQys4R1jkgLKhnTIZ4L6EpqiCro4qqmlbLDKwSRyMOQ5pGQR5YXFNfiPs27drL2kBjRpvVDotNanjI+jZKSRRVbhy2cTTuJ+rKzwUv7Kal9BarroOqlc6p0xWOooeM+s+id9JSP8AMd05rM+MbkHQUREBERAREQEREBERAREQEREBERAREQUxvlVREBERAREQERW4PFnO3gguREQEREBERARUOcjCqgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiINHe9H6Z1Hd7XdL5ZKSurLVN6RQzzMy6B/i0/AHHLIB5hbxEQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBUPJVVk0jYad8r9msaXH3AIOB6X4dQfKsiuG0kNHS3W6F/DykkqY7dBv/Z0VRj9s+K7+OS4D2CSR1evtR1rG54LBY2Bx8ZxVVj/tNUCu/DkgIiHkg5x223m5WjsQvMFkkxeru1lktfCcH0qreKeMj9kyd57mFc5q9OUlv+UBZtI2SMi36Q0H6FBxPwI3VTzTxZ6A8NLknwyVMtYP/lF8pzQukx61NZaar1TVsIyO8DPRKX7553jzjXPKCOftM+UD2o6cpe+Zb5qyjtd4q2swIqGlhOaZsn9JPLPMzA9iNkj9i+PIifdm1vi1NqA9pHrG0spfmjTLJOlCHAy1ePGpexpB/oo4v0iupNJyrKaCCko46Wnhjhp4WCOOKMYbG0DAAA5AAYws2OaCh3ycq0n1UYQScHODjPuV/LPIosW+rw7cleOXP7lj4fJXAHh5qRUV7RHSz6JksFMXNqb7PHZ2FnNjZjiV4/YhEr/7ik4ZBS0Qija2GCJgYANmsaB9wAUVqXm7ds9DRtGYLDb3V0ozynqCYovsijqf9oFou2i7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rc8/EfH966T3CPL2qOPZr2l2ftrpmvFq4Y7HqtjRt6E6T6CrPnBK/c/0cr/BdmZIySNr2PDmEZBacgha2aG1ao0vPRVlJFW22vp3U9RTzDIkY4Fj43j7QVzHskuVbo7UFb2H6mrJamsssIqbBXTn1rlaCeGPJ+tLAfoZPIRu+sssuy9PBc37bbNX3PserrjZoO8vdgmi1BbAOZnpHiXux/aMEkXukK6QOatP2rLTl3YxeKOqj1BbLfL3ltfVR361OxgOorhGKluPISuqG/3V1RfOXZOZNK9qtFpcmUR26W6aRfxnYxwyNuFu+ylqJGj3FfRvRAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBDyREGGmqoKuEyQSB7Q50bsfVc0kOB8wQQsy5nra+XHs11JDqyCimuGnLnPHTXWlhIElLO7DI6mPiIBDvVY9pIyeAjfOZtp/U1i1TbHV1iuUNZEx5ila3LXwyDnHIw4dG8dWuAI6hBtkTIRBy3tR0D87Oq79Q2YXmKtt7rVfbGHhjrpREkju3EgNqIiXOjJIzxObkEtc2O6B7UobJZDadY3z5xtdC5sEWp5ozE+FvJsV1jIDqOoHIyPAikxnLSeFdtq6ykoaKSrrqmGmp4xxPmmeGMYPEuOwXOtQ2+y6/qoZ7JpmjrakNLGahq2Pp2RxnYtjewtlnaQfZaRG4HBd0Q222q+0nSWkbTTVtbchW1FceC3W63EVVXcn9I6eJpzIfPkOZIGSo1p7Rd+1Nq+j7QO1KOBtwpHGazaZikE1LZsj8q9w2qKvGxl9lm4j6uPv7OuxjQPZk2et05YqNt1q8mpuhp4o5XgniMbBGA2KLP5uMBvU5OSZ++RkcRke8MaBlxJ5e9CLwMJtndW5zzC1OoLyLRau8YR6VLlkIPj1PuH8EEc1veWyONrikxDD69Qc7EjcN+HP348FI9M0LqLS9J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T5jp4j3LWU+yxb2d68t3aDoiK/UUNRRVDJZKS4W2p/L26riOJqaUdHNP2ggjYhTDplcg1zYbzonW0vaxoW2zXAzMbHqfT9OPXutMwYbUwDkauIch+djzHzDMdE01qKzas0rQak09c4rja66ITU9VActe0/gQcgg7ggg7hYo4ZrMs0z8pW5Vokc3vpLFqFnniaW11P/Cnp8r6OHJfO/yhmOpNb6cr4GjirtP6gtrj+k9lPHWw/Y+jyF9AW6qFbaKWsbynhZL/AImg/vUHpREQEREBERAREQEREBERAREQEREAnAytbHc5aiqLaWhlkgBwZyQ0H3A8wtg9ofGWHkRhGMaxgY0AADAA6ILhuEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQeW426hu9pqbXc6SGroqqJ0M9PM0OZKxwwWuB5ghcE1B2Z02jrt84XCivdyszAGU+pLLVSw3m0RgbRzuiIdWQNA9VzuNzAMFrgOIfQqEZQcps+mdV1lkgrtJdud1uVunAdBUVtFRXAPb4iRkcefiStq3ReuKmMMuna3e+HqLdbqOlz8XRvI+1e65dnVC66T3rS1yq9L3ad3HNPbuEwVL/0p6d2Y5D+tgP/AFgsLq3tStfqVFgsGooxt39FWvoJCPOGVr2/8VXY9ND2faepJ4autjqr3WxEFlVeqh1a9hHVokJaw/sNapSduvvUJfrfVEU5ik7JtVOcB7cFRQPYT4ZNS38FR9z7T7qXQ2vR9psMZO1Ve7h6Q9o8qenBDvcZgglVyuVDarVPcbnW09FR07DJNU1MgiiiaOZc47NHvXP9Ha6Hahf62t05BI3SFqn9GFymYYzdKtu5EQOCIYtsvIzI7YYDDx6/WunXaZ0bVau1PdptX6jhLIrNR1UTY6KKukcI4BDSty3i43N9d/G8AEghT3R2mqfSGiLfpyCR07qaMmaoecvqZnuL5pnHq58j3OPm5QbapnhpqSSonf3c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ZPb3O/tI3MB+GVllIo3NfGHsIIO4Pirse5c77DNR1Gq/k66QvVY9z6426KmrMu4nCoh+hmz595G/K6MOSMoZrLs20druGE6ms0c9XTHNLcYHOgq6U+MVRGRJH8DjxyoHUdmPalp8E6U7QqPUNO3HDQ6vouOYDw9Npu7kPvfHIfeu3qh5o1HzTqrXXaXpKs0xS6k7JatoN6aYZrFe6euiqJBBOe7jjk7mQEjL8EAYYd8853pztitF00ZbrpV6c1pb2S00Tsv09VTteCwbtdE2QOac7EHdYe3eTgm7Psc26ldMdxsGW6tcefuU17L4G03YppKBpJDLPSjJ/smoOZTVvZJU3B9ZarNrO010m5m0/YLvbpHnnl4jibG8/tAqrL7r3v20WmT2s3JnJslytNsp2D3yztjdjz4XFd4wFXAQcus2jO0S8zCbXmtayChx/wDKLZLG1z84yJaiOKMkdOFgHM+sV0igt9FbLfFQ2+lipqaJvCyKJoa1o9wXpRAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAUd17exprst1HqHvO7NvtlTVNd4FkTnD7wFIlzbt/3+TRrKHfE9udTnHhI4MP/ADILtIU50b8nmwUrGltRR2imi9b607o2jJ/1jyT8VEamsorZZ6m4V872UNJF3ksjRxvLW88D6zicADmSQOqmmvqhlNRUFriGGBzpcDwaOBo+1/3KM6btQ1Lr6nt0rC63WLuq+u/Rlq3DipoT4hjSZ3D9J8Pgr9Lr0mXZ7peotFvqL9eoGR367BklSwHIpImj6KlaeojBOT9Z7nu+ttwJ8RqO27WhMeRU9o9lp+XMR0vH+9fWPRfLMDWt7e77TkZce0+hf7gbVxj/AJVEfSuc8f7S8N4qTR6eq5mH1xHwNP6zth+K9zD5deij+sKjgttNTD89Nxn3NH8SFY059eqllJZ3Ec5poKSP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uv7NaTUlwj4K3UU8+oJ2EYLPS5nSxj4Q9wPgEx7WJdINuLyXXLSeOw0J8aeP8AuSn2CurWP/AOxq3jl/N4/wVpWxHCN+i+RtORy6tv2kbHwOJulLNWOHL+Z1d4lr53kc8GKihjz41UY6r6O7StQP0t2M6s1JGQJbdaKmrj4jzkbE4sHxOAuU/JssEdTHd9dh3e0LWQaasTzjHoVDFHTyStx/SzwvPmI2LH/km30KOaoRxBVyUyVTTiXbbRMsWo9K9osI7tkVWNPXZ42zR1jg2J7v7OqFO73Of4ro2hqkz6Mggkz3lLLJTOB6cLjgfZha/tc01/LDsL1bp1uRNV2udsBHNkzWl8Tx7pAw/BabsU1F/KbQsV8Ax85UdHdC3wdNTt4v95hQ+mXRjC35RnaYQRh8dpcQPH0eQfgAumLmPZyX1vbH2p3gZMButHb4j49xRRF3+/K5dOQMLDU0lLW07qespoqiJ3tRysD2n3grMiCNv7PNBSO4n6J064nmTbYd/wDdW0ttislmjLLRaKC3sPNtJTsiB/wgLYIgYCIiAiIgIiICIiAiIgIiICIiAiIgIiICIeS89LUmp73+bzw93IY/pWcPFj6zfFp6FB6EREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQFZJFHNE6KVjXscC1zXDIIPQhXoggzOzySxzvk0JqKq07A9xe61mJtVQA+LIXYMQz9WNzG+S9MNH2nHEVRfNKtGSDPFbZy4jHMMM+Ac+ZCmCII5ZdG261XiS+1c892vksfdPudbwmRrOfdxhoDYmZ34WAZ5nJ3UW7fnh/YDebc0F01ykprfCxvN75aiNgA+37l0skAEk4wuOal1LbNZ63oYqKKqutrsNSamGmtzGyy3OuAcxvAHbCCHLszOLWGQtAdljgiNbqu8CwW+tukVGa2pfUmCgoQN66qkcRDA0deJ2C7wY156KNTaFquynXukLlJWOqRqeidZL7O535e6Zkq4agn6xc91RC3wa9oXXNMaEn/lJBrDVghkusDHMoLfC4vp7Wxww7hJA7yZw2dLgZHqtAHPUfKLpah3YDcrxRMc6ssdVSXmDhGSHU9QyQ/7of8Mq7a2jtVAZdcWWOMb9zVA+4iP7N8L5q7b9Rt1voWG91te+l0pcb3FZ9PQcfBFLDHN3dXeKgciM/Qwh2zQ4uxkgrunbRPX0ul7lQ2F7orrfpotMWh4ByyatkDZZARy7uBj3E9NlqKizaUrI36aqLaJtHi2/yfNGzYi3YDGPYekgfGybOM5JKvauT1lhnsHanpPsm0/USDRt9vjbxHZqhz5TaHUEjpZ2xPdk9xIADwknDgfefrrTrHSahoS4cnyyH/Zn+K+buz7SVVoft9OndfXequl9p7Q6j0bdZsdxeLcZO8kLDzNY1vDG9mcmMHGeZ+pNKU/FdGS4/JwO3x1JA/cV5Mt3OPfwWY8GVevUFhMnFcLez6YbyxD855jz/FQ927cb+5dXHsqK6jsAlDrhRR/S85Ix9fzHn+KcnH9w+N8nX9MkJdk7Pdz+5Y3n1sgZzy81meBz926xcJxsNyvM+nixcAccH7PFa+82S0ajsktnvtvhuFDKQXQTA7OHJzCMGOQdHtIcOhWyO3L2PHxKuGzht7tui15F/TR2bW2r+y4im1KblrHRUY2urWOnu9pZ/wCpY0Zq4QPzrB3oHtMd7S7hZb5Z9R2GlvVguNLcbdUxiSnq6WQSRSt8QQuWNyJBI08BByCCox/J+76bvlTqTszukNiuVQ8y1trnjL7VdHdTLE3eKU/00WD+kJF2x5f2+fzfE+8X0WWjwyFQDz+C5zontctGpruNNX2ln0vqsN43WSvkB9IA5yUso9Wpj82bj6zWldHJXoeCyy6oOW6oVQkDmtRqDVGnNJ2x1y1Rf7bZqMZ/nFxqo6eP7XkZRNtueStzhy4tXfKNsNfGWdnWlNR63cfZq6Wn9Bt3PrWVPAwj9gOUXuWpe2TVbsXHVFr0ZQOO9FpiH0qrIPQ1tSzgaf7OL4q+Fp5O76m1fpfRlnN01dqG12SjHKevqWQA+Q4jufILk9y+US25/R9megL5qZh9m63AfNFt94lqB3kg/s4yoXbdB6Xtd2+ezbjc72f/ALcXmZ9xrCfESzE93/d4R5KQnMsvHK9zn9S85OPetTjZ201fdO1/VrXt1P2gR2CjdztuioTAfc+tnBlP+rZH715rHorS+nK83C12ambcXflLpUE1VZIfE1EpfJ9hA8lI4/XcSRv0Tuy6UeGOXiukkGMM7zcg5Pmrhs7PCqtANQ5jOcQDD5Z3/DH2rKGMB4eaosxwQ7euenmsjWZ32z5LFHPBPVVNOw8ZpHNilxyEhaH8PvDSwn9sL0gbcOFkWAH6o3VWgdPioX2pXmot+laexW6rlpbjfXyU4qIfylLSRRmWsqQehbCwgH9KRngptSsHokIZH3Y4G4YPqbDb4cvggyhmenNZI2N4/XLWNG5e87MHUnyCtA6Dp960d+o6jVl2ouzC1SyR1N9iMl0qI9nUNrBxNJno6U/zePxL3n82UyGt7M7yzVVBqLUbA9rLhe5JaeN4wWUno8IpifDihDJMf1ikWozXTWiHT9nmdFeNQVAtFC9nOHvGkzT/AOpgEsnvDB1Ws7PhTVNrvNbaqeKKkrr9XegwRACMQRTehwtYBsBw0zAPJTfsstTNTaqru0Wb17fTtfaLAekkQePSqsf2srAxp/o4WEe2pv0zt1O02qhsdhobJa6dkFDRQR01PCOTI2gMaPsC12pa7u4GUMZzJLu8fq+Hx/ct1UVEVJSSVM5xGwZP8FAK+5U0MdbfLrVx0tJBG+oqKiQ5ZFG0Zc4+QA+7zWYOMfKf1HXW7sPr9IWWTF21BA+KV+cGnoYy3v5Nv03OhgYOrp8eK6NSUEVsoobZBGGR0UTaSMDkGxMEYA9wYB8FAtR2WqvjtL32/wBA+nuOrr7TzMopQBJQ2ehD66OEg7hznxRyyD9N4H1QugtcXgvfzJyferj20tqJI6ejmq5zwRQxOkkJ6AAkrqOnnyHStrdIwiQ0kRcD0JaCVwPtUuj7d2V19JAJDXXci104Z0714ZI4+ADXhg/WljHVfRcTQxnABhoOA0dBySsuX9vslRVdk8el6FxFZqO7UNni/ZfOx8nw7pkmVquzBr+yC/P7Gr1xNs7p5qnSVfISW1ED3GR1G5x/PRFziOrm4IHq77jUTRqT5UGkLCGOfTacoanUFVkeq2WTNNTg+e87h+yp7qnStl1jpuWyX2lM1O9zZGPY4skgkacsljeN2Padw4bgrI3CrgrnlDedV6Jb826zpq6/W6MYp9RW2mMrywchVQRgvEni+Nrmu5kM5Laxdo+j6hjDQ3d9dI/cQUdLLPL/ALNjC4fEIbSaeaGKmkkqHsZExpe9zzgADmT5LgfyYbvBH2DUtXUuZT0tDbY+OZ4xwxNlqXMJ35CLuz8V1Wqttz1hSGlu9E+12KQh09HO5r6itaMHgl4SWsiP1mgkuHqnhGWnh+ibWansAodH0c8gqNb3qspg9juFzbayplfPIOeAYWloxtmZgHNGXX+xeknZ2SUl7rYTDW3+onv1Qx3MGqldM1p/ZjdG3+6ugrHBDFT00cEEbY4o2hjGNGA1oGAAsiNCIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiIKOBLSAceaNBDQHHJ6nxVUQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEWh1JrXSmkKdkupL9RW8yfkopZMyzHOMRxjLnnyaCVEz2qXq5jOkuynWN2jzgVNbFDaoj5j0l7JCPcxB0pFzd2te1GI8UnY3JKzwptQUpd9jwwfesUnbJDamcWrOz7XNhDRl8xtRr4WDxMlIZQB5nCDpqKLaY7SdBayGNM6ttVxlHOCKcCZv7UZw4fEKUZCCqIiDwXSzW69UwprnC6op/rQmRwY/wAnNBAcPI5CzUdBQ2+mbT0FHBSxNAAjgjDGgAYGwXpRAVkjGSROjkY17HDDmuGQR4Ly3S72ux2ma6Xm40tvooG8UtTVSiKNg8S52wXEdTdpmou0WkfaezOWWx2CX1KnWFbA5r5Yzz9AhOHSEg/lncLB0JOFLlJ21jhcrqI1rO+0mqe3Cpks74/mTRTZaKCSM+rJd6phEpaeR7iAuB/RdI3Gy8lPllSwAYwdtunJQ3ss4rVoyq0DcaOGg1HpWslp7pEx21Y6Z5kirxkkvZNHwDPQxgbAgKdW+DvakP5tG7l14+trrXqt/Pp/TWuNDu0NrWB5tZkE9vr4H93U2moG7ZYZBuzB3B6cjlp2xWbXWq+xy7M0723PFZZZXNgoO0Sn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TUz30pljPrUtG0Zqp/LDHBjf15GeCym2Lsop23efUfaRJDwu1JWj0Mkb+gU47mnO45PDXy/61dJXmt9DSWy1U1toKdlPSU0TYYYWDDY2NADWgeAAAXpQMKmFVCcIIp2kXuosHZjdqy3tMlylh9Et8QO8lVMRFC0e972/DJ6KF9jumY2zi+xkvtVpoI9N2BxwBJBDgVFSAP6aZmx6tiYeTlZfZ6/tP7UP5P2T0iKwWJzm1d5ifwsbUuYWSMhP15mxvLA4bRl73HLmxrrVDRUltttPb6Gnjp6WnjbDDDGMNjY0YDQPAABB6EREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQERYqmpp6OjlqqqaOCCJhkklkcGtY0DJJJ5ABBbV1lLQUM1bW1MVNTQsMks0zwxkbQMlzidgAOq5f/KjWHaY4t0NI7TmlHHhOpqiEOqa9vjQwuGGsIzieQEHILWnmtVTw1XbddY75e4JoOzqmmD7XapAWm/Oadqqobz9HB3jiPt443ergHrYd6nC0YAGAByCbWY2o1pXQGldIzSVltoXVFzl2qLvXSGprag9eOd+X4/VBDR0AUoMmeWfNYuP6vF9quBGVz83b8bIHuLtxhXtOW5BWPJ8HKmSB4J5s+KN6q7N9Ca4Z/8AFOlLXcpwMMqZYGieP9mVuJG/Ahc/r+yvXWlWtn7NO1TU9BAx3/yq7tjvFOxvg3vS2UDyD3Hwyuyh58VXK1s8XEKbX3bxZ3yRXKy6D1O2DZ/oVdPa5/7zJ2FjT7yOa2MfbZr4DFR2D38u/SpbxQSsPuJkb+C6XdLXR17A+qpXSlvsyQkiaP3EbkeX3FRSrsIpom1bbcy80h372iPdzge4ECT4YPksXLKOmGHHl36R+Xth7Sponegdir6d3R911JRQs/4ZkP3LwVOru2W7Exz3nSGmYjzF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uYXEOeHdUwM5VVFY6engpKWOmpoY4YYmhkcUbQ1rGgYAAGwA8FkREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAVksrYYXSPzwtGTgZV6YQWxyNlibIw5a4ZBwjuLHq4z5q5DyQUByFVeamo2U008jZZn98/jLXvLg0/qjoF6UBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERARF5rhW09ttNVcat4ZT00T55XH6rWtLifsCDi+pq+TVPyi5ImPzbdG0YYzfZ9zq2nfzMdMHeYMwXVbVTimtkMeMZGSuM9kNNUXDR9JqCth4a7UtXNf6knmfSHkxD3CBsQC7kAA0bbcguON3la9lnhhJ+wuOOuFb79leNm4VMYVrEWYH6RVzSeJUwOL3KvtOUxVkbkqpB4Ub+UQ7+a0x9qEdMqhGehV+UHNF2xEHO6c+Svdgtz1CtAHCi7WHIbsFkY/HIqhA4dlaNnAhGdbiBXfs5udnu8+p+yu6U9iuc8ne1doqmF9quTupkjbvDKf6aLB/SZJyXo0x2nUF4vY0pqS21WktWhpPzLc3D+cgc5KWYfR1MfmzcfWa3kp0yTx5rU6j0rpzWdh+Z9TWWluVESHiOdu8bxykjcN43jo5pBHQrXblZY3Wxz4qnCQAOfiuZ+g9ovZ361okq9f6bZ/4Csnay8UbP6qd5DasD9GUtk/rHnZSXSev9K60ZUMstzca6kIFbbKqN9LWUR8JqeQCSP3kYPQkISpP8Fimp4KuEwTxMkjPQrLtjOMIR62QpW9oVeNGyEOktxErefcPOCPcf4rnGrtFWHVdIy1az07S3RkJzEKuIsnpz4xSsxJGfNpC72PZ5Lz1NDSVsfd1VPHM3oHjOPd4LjeP3vF6Mfletck3HznbaTtV0LwM0LrtmoLUz2bFrgvne1v6MVwiHeDy71jgPFSej+UTa7PC1nappG/6Ffy9OqIfnC2SH9Stpg5oH9oGroldoqCQ8dDOYj/RyeuPt5j71pKmw3G38cgilY0+1JAcgjzx+9WcmePftr8XDy/43TPc71pvXmhXV2k73b79FG8SMltVQ2paehGYycbHr4Lnzt3cH1h0XkvXZD2c32vN0rNJUEFyJ4vnK08VuqgfHvaYxkn35Wql7MNSW+P/AOGu2TWFFGPZgvsFNfYmbcszMZJj++umHyp9ueXws50iWsJdQ2HVlXaKGoli072gzUVHdqiIyCalNLGe/jjLcEd/SRluc5BjfjmFtNTaVotI/KR/7PdHUbLZo6a1Qatr7PBH/NqeuhqXRQPhH5nvHsBc1uzu58ytX2g0HaxaNPUPpOpOze7d7dKOChElnrqSrnrDKBE2Jkcroy4+uCNhwl+dsrZ9pcOpbl8qzUUFBrP+SbK6G3WeiqZdNMr2VjooZal0MVTI/u45h6Q8mMjLhwEZwt+ePbh+PKXxs9t9WS01NQz1tZU09NSwDjnqJ5RHFC3xe954WfEqEUVOe22vpbfR01TH2YUVY2ruFxmaYf5TzxHMdNAw4PorXbvkI9YjbBAUkt/Y1pqor4bjrW43nXVbAeOFuoKgPpIHeMdHGGwj4hy6fFEAxkeNo2hjAAAGAcgANgB0HJcuT5O/Ue3i+Hd7zHmSWV8mBxuPGThCz1j+h0WXg9XJGFTu/V+OwXlfRYwcYGCsnq8aoG+thXM9rGc5OwAQtMZ3PTyV4H/Vae4ajpqa5yWe3Us15vMYBfbqQgejg8jUSn1adn7XrH6rHcltqGCsZRsNzqIJqhx45PR4yyJn6jM7kD9N2554HIGPPbIB0I5rK1mG7c0GC7luF77Zb5a2p7pvsDdx8ArJamWck2W+3z1r+BjeFo9qQ8gpPS2+npGDumZd1kPMr0Q08VPCIYhho+9Zm+3+5dphp4eTmuXXTGx8kTjwHLerCtTq3RmnNfafFr1BRmZrHiWnqI3d3PRTD2ZYZBux4PIj45Gy3RAP/wCSjC+N/EOnMeK6Y1wzxmTmNg1rqTs+1tR9n/alW+nUlwk7qwasLBGysdjamqgNo6jwd7MnTB2XYwcqN6p0vYtdaPrtM6hoxV26ujLJG+y5p+q9jvqPBwQRuCAVEOynVF5p7vdeyzWtSZ9R6fax8Fa/Y3W3u2hqh4u2LH+D2nxXV5nU0REBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREA8lQZxvzVUQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAXL/lA100PYVc7JRyFldqGWCw0pHPjqpGxH7GOefguoLjPapMy7duXZ9pziDo7c2u1LURnH5mLuYT/ALSoz/dUyuptrGbukn0tQUtLOylo4mx0lHGIYGAbNY0d20f4QFMR7OOi0OnYe5o3ucOZAW98iVx4/Uerk/y0EY5J14uSbOVwxw5K25reaq3gOFQjHIq9jQG5IQVPslUHs4V5Vu/FzTbP0qdxtsqZLU9xVOvrBBdjIWMbLI0+qsf1uePehDBVpOTjosjfawqcA6KaaWYwfFXNkIPNOTd8qw8lMV7elsmW7qJ6w7PNK62FPUXqhmiuNJ61Jd6CZ1JXUZ/qqiMh7fMZ4T1BUlBKvEh69F0253BzBp7YNCPIe2HtKsbeTx3VBeIW+Y2gqf8Agn3rdaa7WND6muossF0ktd+x61jvcLqCtb5CKUAv97eIeanIdnktNqPSmm9W2r5s1NYLfeKQ8oa6BsoYfEZ9k+Y3RhuPhhXbLmTOzC66fcDoHtDv9lgb7NtuhF3ogPAMmPfNH7MoXsivnanZQ1t60datQRA+vVacre4lx4+jVOB8BMUa06Aqb4UPt/abpOruEVtuFXVWO5SnEdHfKaShc8+DDIA2Q/skqY+9Z0PBV2i31m89M3iP127H7Qo9W6SdGHy0U3FgZEcrd/gR+8KYH2EWbhK64c2ePVfMAbcqv5Rms7u+2S3Wv7N9PRVmn7DsBWVlZBI+So5+scRiEEbDJ6rSv012ku0kbzf9UN1Lp7VNDFdLzL3bI5tO3LgD6e5UwH/ho3RxNI5tbHx74eV17tV7Lqy/X2k1/ofVU2jdbWukkgivMcLJoailzxmmqon7SRZ3B+od1wiG+SXTsd0Lp7Uva1p/TMPaBQz3LVNRW1UVO6KiMokFFQx5DIe8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CU/K5vlpp/kuX/T01wjZeL42GlttubmSprZe/jcWRRAFzzgHpgdcLwUei9ddsD46nVVi/wCz7RLqhtWbRlsl7u3DIJG+kyDakjJZGTFHmTIwSFbvbnLNXacUHaNqLSWo6PTfaxb6SnjrZBBQaptwLbfVSH2YpWuJdTynoHEtceTl1UHK0l/sNr1Rpiu09fKVlTb66J0NRE8e00jPwI5g8wQCoh2IX64XTs3msV7qnVN40zcaiwVs788Uzqd/CyU53PHGY3Z65JW2HSkREBERAREQEREBERAREQEREBERAREQEREBERAREQEUb1hrrTWhKKiq9S1zqSKtqW0kLmxOky8gncNBwMA5KkYOQCOqCqIiAiIgIiICIiAiIgIiICIiAiIgIiIMbi9pyG8Q8BzXiivVvluj7cJi2pZzje0t+wnYrYqzuo+Pj4RxeOEF4OUVA0N5bBVQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREDCIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgHkvm7RFykre3vte1VMR6Yy7Uun6d+N4oIYi4geGXEE+YX0ieS+X9PUs1n+UF2xWOUbyXKgvkA8WTQva4j+9gKZdNYdvof53po9PRXHheWPDQyNntOeSAGjzJOFWKiqqrElyn9X/ykBxEPInm/7h5Lnz7y+n03bHl+WUd2py7P6LiWf8zwp9S3mCVxY8BsmPHmm2rx36eyd9HQ0LqiokipYIW5dI4hjWN9/QLnGqOzDQ3adVd9fuzuyVcTsf8Aetyo+7q5P7Ms4JWftlzT4NPNS9r4b3qqoglHFSWvuy2M8nVDxx5I68LSzHm8nmARJBuq5uJw9gNz09k9nvbN2gabjGe6oqqsju1JH5CKqY84/vq6a0fKasI/7t1R2d6yiHtC62uotM7v78Mkjc/3F1e7XentNEyaaKWeSWQRQU8DcyTPPJjR47E5OAACSQAStV8zXS/AnUlV3NI7b5soZCGEeEsow6TzA4WdCH80N1y+k7X+1ihc8al7ArtUQROMcldp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oIR9LV1k7YIo/e95AUAb2jao1k7uuyjSL6qjdy1NqNslDb8eMEWO/qfgI4z/AEiM2ujV9dR2u2TXC41tPR0cDDJLUVMojiiaOZe87AeZXzjQS6lvPbNqC69idopbrpy9mKtmvl5ZJT2ulrgO7lmp8YdWiSNkZIjwziZnvNyF0+39kFvuFxp732l3ur15dYSJIY7kwR26ld409E36MHwfJ3km3troNfJLBa530cDZZomExRk4DyB7PlnGPirZLPZjyXHpAtN9kdro79BqjWV1qtaari/JXW6sb3dGfClph9HTjzAL/FxU0kujqO+09DVgcFS0+jy/puaMuYfPG48cHljfHRXunuNnpbjRl3dVETZW8fMA9D5haLUVZIZ7E8vOTd42D4xSg/is3JZx37Sae6xx+rEM+ZWkuNWay+2imJzwyyVh8hHGWD/elH2KrWPe/wBY8l5aVnea7rtzinoKdgA6d5JK8/8AIz7Fndta8JGPWlyqKDQF4qICRUyQGnhOfzkpEbf+fPwXhvlQ/TXZrX+hMw+20Ho1MB/S8Ajb/vFejXMXHb7DSH2Kq+UkbhjmAXPx9wWLXFI9+hRn87dYA7OORnx+JW0mkX7beLSfyONWUtKCDS2KO2s8+84Ij/zrrGkbc2z6BslpY0NbSUEFOABjHDG1vL4Ln3yj44v/AK2fUzpwDG11LM8Hlwtq4SfuC6vCWugYWY4SBjHgtOW9r0REBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQFw35QDXz647H6OMZedYR1B/Zjglc77l3JcF7WKt1w+U7oK0sw9lmtV0vkwG/CXRCmiJ8PXk2Uq49vVTgGiaQMZbk7rZaJgzFfhgcAu8hHPrDAT9+ftXkjjxTYA5N2wt1oKm47PdagA/T3erIyOjS2L/8AFrGT0R5NfQOi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kb5BYKCpNXDJLgCPvXMYR1AOM/aCrLjUPprbJIzHfHEcI8XnYfeV6KanZSUUNNH7MTQwfALrHKuJdvOoLto/XvZpquyWOW9VNPca6nkoYPyssDqKSWUR+LgyBzgOpaAuuaT1XYtbaPodT6br46221sYkilYeXi1w6OB2I6ELnnbZIaO+dld0D+Ewa2pIic42mp6iE/8A0i8OoLVP2SakrO0vSEMj9M1kpl1TYYW5ZGM4dcKdo9mRvORo2e0F3MZLaO1ovPQ1tJcrZT3CgqY6mlqI2zQzRO4myMcMtcD1BBBXoQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQDyXzY2pZfe27tC1IGksZXU2n6d5H1KWIvmx5GWox/dX0XXVcNBbKiuqHcMNPE6V7vBrQSfuC+ZOzKN83ZnbrpOCKi8SVF5lyNy6qqHygn/AFbo/sUy6bw7dF01E998hfHF3kjfXEePaI5fDqukUtNJG/vaiTvqg839GeTB0H4qK6FpwHV1W/AwGx5PQbk7/YpS1z6oZZxx0/R/J0o8vAefMrMXO+2Yyl8pZCONzThxPJn8T5fgr2xet3kh713i/kPcOivaGRsaxgDANgB0VktQ2LAOS8+yxgyT/nxVYjQ63lkGhK+CAnvaoNo2Y/SleI/3rdU0E1JTR08ZZIyJoYBw8BwBgKP6l72ru+nLd3bWGWv9JdGT9WFpfuffhSUTEbyRvb5jcfcs/a/S9kjDs8FjvAqpjHHlhLXeI6+9VHBKzo4H4qnA4P8AUf6o5sK0K8WB9JgefT/oqvZ3kRDHGMkbPAGQq5Zx93kZI9kq1/HGwmMF+PqZWtsrHSCnpmvqJM42Mgb9/kswcHsBYQQRzCtY8SMGMjbcOG494Vkve91/N+7yDuH+HUILSwxjMYyz9D+CzB4ezLFd9XmsTmEO449j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ySebiSSSTuTknmqw2k0rIYy5+cEhmwzuTj96ySn+bv26K4e/krJtqd/w/FaZQPtzI/8ArZte7D/5DWczj805SvSOf+z+x55/N9P/APRtUK7f5e5+S5r5wk4f+5akZ5c2EY/cpvpVpZoWysPMUEA/4bUG3REQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBeeuq4bfbKivqXcMNPE6aR3g1oJP3Behc27ernPb/k/6hpqKXu666RNtFLjmZKl7YRjzAeT8EHMOzlj5eze13Coa4VF1E94l4uZdVzSTgn+49n2Lq2jIz6TUygAuDWsb7yc5+5RKmpIKFjKCkZw09JG2miA5BjGho+4BdA0dA2Ozy1L9jLKce4DH8VzjvfWKSsYI2HmSeZPMrBXV9FbrfNXXCrhpaWFvHLPPIGRsHiSdgtFqDV8NquLbJbKKW83+ZneQ2yleGuDOXeTPO0MWfru58mhx2XhodHVFzucF51xWRXe4RESU9HE0toaF39VEfyjh/SyZPgGclvbjFn8pNQalcRpCgbRW8j/AOdXaJzGPHjDT7Ok/adwN8Mq6k0JabhIa3UM9ZqGpJLO8uT8xbbHEDcRgZzjYnzUpqi+OmkezeX2G58TsPvWZjI6akwHhrIm4y/y6lTTfXSBt0X83xtrtC3us03IZTG2kOaqglwSN6d59TlzjLfissXaBW6el9E7RLQLU3Ia280jjPbpD+s/2oT5SADwJUxdA2GGkgZsI+jf8+9Yp4xJC5hYHmVx2dyI6j3LPuHfbKx7Kh0NTS1YMXCCBHhwe3xBHML2EsezgI4gRgg9Queu0tX6eq5KvQ9XDb2cXHNZaoE0Ep8WAb0zvOP1fFh5rcWHWNFdLl8z11PPar5GzvJLXWY70s/pInj1Zo/1mk464OysyLikTRT2y3ve+ZwhjHGXPJPCF6YZY54WSwvY+NwyHt5EK4PD/YwR5Lz1DZ4qcGjYwlm/dkcx4DwW3Ne+CN87JsYkZyd5eHuVDUw+meilxEuMgEcx5LODlucY96qMc+aDGQD+4qhB4fxVjZn+lOhdC4ADLZOh8vesx5rKxjB9bBK88gZSMmqI4XOcfpHiMbvwMfbj8F6SOHJHxTlzV2qkcsdRTsnicHMcA9pHVJG95FlntDcHzWs4vm68NjP+iVbjwf1cvUe534+9bYbu2USxgdiSJpeOZGQR8CF8b9gNmq7VpzV/p+DVR6lks+eoit0Ipox9j19j1AIhlx1BI96+ZdI0/wA3647U7ORh1NrioqwB0bV0kEw+8FKuPbtfZ6wC33GTqZmsz7m5/epbVPEdunkI2EbifsKi/Z0M6crH4wTVnP8As2reX+XuNPz42dLiIfE7/dlT6X7YtMyA6MpGP+qx0ZHuJC2k1XFTUQqJeINwOm+/ktLpp3HpmVnQTSM+BGf3reOEHo0TH8PCeHhB6kbj8FqX0l7JziPr0/FDvsqyhr4zt4H71Q+KiRaPYGEaefmVQD8Fexhx62FmdqxyCpNXCyMfREkvPw2CxVs9SyvooIIyWSOPeHwACyipj9PdSDOY2cbj0CtNWx1x9EYwl4bxuI+p4Z960LpC7vAANs88pn6UeACseRxnyGyuByCeFZaC8MYSeQGSqtf3jA/GMjOCsb3hg3OxOEMg8cYQ+mVxZhWEjJ36qx0g55WJ04ZE89AMosi/0hgBd0JPNWwkGSSTbBdjn4D/AN1i7yMMaCdoxkn3JBj0QB/LhJd55ySi6XRyUM4lqaURO7wgSSMxl5HLPjsqRv8AWecbOd+C89PBT263Mgp88OS8k8yf8/gr4nHABx6o396Ej2NeTwYPMrMM4815o38b9jyC9A9nmpE0u4vJUAY15PXHJPq4z/eVG8A3A5nwVRSbhc1sZ5OI+zmsD7hB87Mt7XF8vAZHAfUb5+9ehuDIRy4R+K8j30wEtRBwd44924g+CbGOSXLyQeQOF45ZQXBnMcz7gscko4cZ26uXmfL6pkxz2asuuiefMxw72eXvPMri+o6v+VfymeBruO26JtXcDwNyrxl3+zpYwPEGVdQvd7tmmdMXHUd4lc23Wumlral/9XG0vI8ycYHmVybs9tlwoNEsuF9j4L9fKmW+XYdWT1B4+6/1cfdx4/qytaYvuukaXpBU6ghJZ6kJ793w5ffhSjVGrJ7VNTWOwU0Vx1JcQfQqSR5EUbR7VTMRuyGPO55uOGDc7QqO/mwUkNDbaD5y1LdM/N9t4+AFo5zTP/NQMzu7ryblxwpbpnSvzBTzz1dY66ahujhJcro9nAZiPZjYzP0cLc4bGOQ3OSSTpL7ezS+nYLFbZ81MtwuFZN39dcpwBLWz4wXED2AAOBkY2a0ADzlkbBGxkY5AbrzU8Qi4WM9iIYC9gHrkkeGFY51byc4jwSX2AMZy5v4qmPb36/Zsqv2YPJ4/FVHKvlNyGH5JWuXA4cbeGH4ysH7106xsMembdGRgtpYm49zAuafKTfA35MWpRUDETzSxu22wauILq0AaKdjWjADRgfBBkREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBca7Y6wXDtK7PtJNPGwVlRf6qPb8nSQu7vPkZpY/sXZTyXArhIb58qPWF1LwafT9mo7LE7I4WyTPdVTEnphscYPLmpl01j23Ecb+LZhL88gOa21ouV41HaoLZpCZtLQMy2r1AWiRoOd46Vh2lcORkOY2nl3hBAizePVLhFTiRtlkPdgsJZJcydsA844PPnJ0w3c9lpYIKG2xUkLI4oYGCNjIwGNYAMYAGwAHRYjryNVYLBa7DSywWuncHSScVRVTPMk9VL1klkO8jum/LkMDC3QGW7/AHLHCCKdmR6xGT7zusuG4/6rTDC8d7VwR42bmR34D7z9yrW0Zq4GxCYxxh4dIAPaaPq+SyU7MySyEc3YHuH/AFyscTa351qnSkeihrRCMDc43KMqyHMwJ6DP2qgGZR4tH4rJjj3HU7KnJheGpsYnQiRj359cnZae/abt1+oGUVzpvSGB/HE9jjHLE4cpIpGEGNw8Rhb0AFwGOSqxuXkkchss6b2gJvGpNFS41Aam+WMZ/wC9YIc1VK3/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EBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERARFRzmsYXOIAAySeiDiHb3cGXLU+htCN9dtRcXXytaDyp6NvG0HydMYh8FJ9C2d5YbzOzjIJZTh3U9XfDl9q5fpySo7Te1q+doTOMUNZKLNZH45UEDyXzDPSSZrnf6tq+haSnZR0UNPTxcMTW8AGfYaBt+5ZbnqPS3PAMjDsbhY2k8T3+J/6K6SURwulfsIwSVbED6O0HwGU2ReMlUGHz4P1Rn7f8lXY281SnGeJ56u29w2RKueyGR7e8DZHxnjaD081Q891aynDLjLV8ZLnNDAzwV3jt0RIc846hWjJwrj1x4I3Z3PyRtUAnkPeqEPLmeGclVB/R+Kqw/Svbw7NAwfP/OEZqyapEdTBBwkmUn1vAAZyraj14+D9IgfxWRssUsknDuYzwHyWMjMw8grTGe1SM48lST8kQOowrh7lTYuHvWI0taB6W8YBa1oGPvUa1RpuhuMcd0ZUVFBc6PenuVMQJocn2d9pI/GN2QfI7qTxexnHtHKxVkPfwRU/1XyguHkN/wBwWmd6qMWbVdbT3eLTmraeKjukn+i1cAPo1yA3PdZ3ZJjcxHccwXjdSepimnEUtNUOjMcgfgcpR1aVrL/arfdrVVUd1phU0rmjMeSDkbhwI3Y4HBDxgg7haCy6guGn9RUuktWTPqJKnLbVdpAG+mgDJhlxsKlo3I5SAF7dw9oS/s1tO+Nr3FgI4xuRncLzk+jT7+rC44/Zcf3H8VcaeP0z0xozJ3fCeH6w6fvV7ZIquB/dubI3Jjd5HkQtURzW076HTM16jH0lsfHWkfqNP0o+MRkC+frdGL/2/drFSAH082oYLe8/1dNQQslHxL+7/wBYV9LV9HFcbTNbqveKqidTTDxDgWH8V8z9jD5KnQFdfamcTVl0vlzrKuQf0prpoz/uxM+7dZanbpgJJyee52W405Smr1HT8Q9W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TXbVo2+3qPT10dW6W1E/YWXUMBo53nOPoy71ZQTyLHHKDoyJlEBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBMorJI+8AHE4YOfVOEF6J0RAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBEUG7VNdS6H0R3tqpxW6huUzbfZqDrUVT9m5H6DRl7j0DSggnaJcv+0PtTj0HC1s2mNOujr76SfUrKz2qaiPi1uO+kHg1oPNdP09bjBS+kzkvln+kcT1HT8cqC9mui4rLa4La6odWdw59VX17z61fWSHillcevE7I8mMAXUyQGYb7TvAcvNZb6mjcyE8mgYHn5ryXGp9Hojh30jtmr2eA3UbuNSKisJafo2+o3+Klq4xZSs7yvhZ/WD7lJfqrQWiPvLjk8o2k/uUhHs7qSLktiB717vD1B+/9yuZJDJO8AAvj9QnHLbOFdEMRjPM7lYqakio4XRxl54nmQl5ySScrTnV7htgHmVQvI9yvODv1CsPtjbbqVK1Fw3GE+t6qc1UBVWKogfPB3bH8OSMnyVtQ+o9LhZGCIySXvxy8llBkNS9hZiMAYPiVjhqBUsL4xs2QjPjj/qjKr/Yx4lVJ26+SbOk58gqgBGsVR7O6q1pBeT8Fa0dVfEJAz6TmSeXQdESrXvjLxC/BcRxkeQVrvXd+Kr3UbKmSox9I4AEnwCtJ9VEiz3dd1TLGPMj+UTclZPJecO7yt7gch9K/wC3DR92fgsxp6IWGOLcAvdufeoXr6Gtss9N2h2WGSaptDC2408TcmtoCcyxgdZI/wArH5tc384VMK6tjo6bvDu47MZ4/wDReK3Vr6mkmE543xuzv1B6K0/2tN2oJLDHeKepimoXQ+mMmjOWvi4TIHA+GMFfOHYeDH8njR0j2YfPbzVu8zNPNN/+NCmt2ubdF/Jk7RaQy93FpWC60sGT+aMJlpWD3MmiYP2VqdG2r5i7NdO2N7Cx1vtNHTOZ1Do6eMO+/KpJ7dB0i2Ft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gtFfdA6K1Qxw1HpGxXXIxmroYpT8CRkfanis5EYe+opqhpPewyA5acYIUZ7dKCu1V8nK4Xq2/wD2QaWnh1BRPaNxJTHjLgPOPvRjxW7m7CtP0be80XqXVekXjcQ225Pnpc+dNU97FjyAC18tl7adL0tRHHT6R7QqGSF0UkDw+xVkrSMEbd7BIceUY9ykliZZzKOraZvlNqbRlp1FR/6PcqOKsj8myMDgPvW1XAuxjtBtmhOyrTvZ52mw12jr7bofQWi+Rd1T1AaTw91VDMMnq8I2dnIOy71HLHNE2WJ7XscMtc05BHiCujmvREQEREBERAREQEREBERAREQEREBeeSlYagVDPUlAxxDqPA+K9CIKNzw781VEQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERARUOcbKo5boCIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICLzj0r5yPsejd2MfpcefwxhehAREQEREBERAREQEREBERAREQEREBERAREQcN+Vy8j5K15h4+Fk9ZQxPP6rqqIFbrTDzBFdgB6xdIAR+2QtV8rSlfUfJQ1DJGMuppqOp+DKqIlbGwPHHWn6rnOPD4gnKxk68X26i3LwMjoDn9yoch38VbBvTQu6uiafuWU+zhVhFK+A09fIwjYnIPiCvRaKfv68yluWRb/3ui2txovS6UhjR3se7fPyWSiphR0bYgcu5yO8Smm/L09H1cYVsT2Njkne4BuScnoBsqSkshcRnIG3vWXuIzTGAj1McGPJWOdA9ksLZI3bOGQfEK13Mb7ZVeFjGBrBgNGAB0CAesVGooeX71Q5zzVxHn5oPZwd8oRc3dWvia+Vjv6PPRXD28LHGyWNkneP43F2RgYwOgVjNYzFKbmZ3P+ia3DWA8z1V7juPEbqkL5JIA+VnA9xOB5Z2+5MEvJ88bLNJAH4LK32v3qgBJwN8bq4FjGkkgDzSNVSOSOVneRkYdyPiscMsVQzvYuWSzjxzx+5Xv7qKnwcNZ7AH3KwRR09MIYWcLGjgAVZimeble3fnyVo22CvaHcPPmUbc57WqoObo6xkjFy1L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fInaoulDPI3Eghq4DnIfG6MMJB5EZZzHitbkvtyx47ZuJQB4p1WMzCSF3cyMDyNieh6ZXltdyZcrdHUY4H5McsZO8crThzfgQU8j29xzn1UIPgrh9qr0SMtBf7yLA6hrapv/AHfLUNpKmQ8oTJtHIfLvMMP9oD0W6D+oXjvdqo77p+sslxjL6WshdTytBweF4wSD0PgfFRjs9vFZPaKnT19m72/2GUUVc923pDcZhqQPCWPDv2uMcwVftJ7ibYBGCMjqtRp64TVVNUUdaW+m0U7qaYDwBzG74xlh+1bUbct1GL1INO6mpNRnaiqjHb7h4My7EEx9zz3Z8pAfqKrpD/lD0Vzp+yeHWOn6uOkvWmLlT3amqJmkxsHH3cwlxv3XdSyF36rSpZ2edo1HrihqaOroJrJqa2OEN3sNWR31FL4g8pInc2Sty1zSCD0UivFsor5p6vstzhE1HW076WojP145Glrh9hK5dYdMnU+kaWmqq6S2a90fIbT8+QAOmaYwO7c9p/KQTxGOR0bvV+kOMEZBjTsqKEaN15Ndb5VaP1VQss+rqCMSzUYcTDWQ5wKqlefykRPMe0wnhcAcEzdaQREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREAr58v97frjtcrbmxxNk04+S12z9GetIxVVA234GkQNPi6Ujkuh9rWr6vTmkIrTY5Wt1HfZTbrXn804tJkqHeDImBzyfIDqueWu1UNls1JardG4UdLG2GEO9tzR9d/i5xJcT1JKlawm6yOzw+wR5H7FGtFQPj7UO0V5ZhstfbJPf8A92gfuUrIdnGM77rV6apwzVuralhH0tbRsO3VtHH+54Suv26BpqIyakpefq5efgCug0/H6M3jHAcnYe8qF6SgAq6usLfUhgPLxP8A0Cm0ZBgY5nItBH2KM5drech9wQ+05G75Pmrxnh9ZSMDc/wDVYaiOrfWUxhk4IWkmXz8AszOqsZJxvk4CC1p4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n6Rp5eKqPaymB9hyoPM/MNWZB9V+feP8le2oggq6aSKojZLDI0scx44muaRuCOoXll3qHO6bL1UknqGI/V5e5axrHJNyVxXsnq5+zDtTufYTdXv+aix910lUyuz3lGXEyUueronEjHPh36hd2XL+2nQlx1do2nu2lHCn1fp2pF1stRyzMz24HH9CVvqkcs8JPJSHsz19bu0ns3oNU29joXygxVdI8YfSVDdpIng7gtd49MHqtOSXoiICIiAiIgIiICIiAsUpkbhzG8QHNvXHksqIKNdxNBHXxVURAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAWi1VrHTWibE676nu8Fvpc8DO8OXyv6MjYMue49GtBJ8FE9e68vdFra39n+i4aD5+rKR9xqrhciTTWuja8MMz2AgyOLjwtYCMnckBafTvZ/TwXz+UkktdqjUr9nanvjQDAOrKOHHDAzc44ADg+09B4rrW3ztDg7/VVtnseliRJTaenPDVXAc2vrg05ji5EU49Z31yMcK3lBYq28A1tW8UtEBxGZ4G48GDwxsOg6eClUNht1CDX3V7aqYHJfNu0E+/mf8gZXtbST3SUSV0JipGnLKU85PAv8B+p9vgppZdNbbLbDPFH3NN3FrYeOON3tVTv6R/iPAdfctzcrgKKBrY4+/qpTwQQA7yO/cBzJ6BW3G5Mt8AcWmWWU8EEDPald0A/eeQG6x2+3zNlfcLgWSV8wwcezE3+jZ5eJ6nfwAqq222OpHS1VRJ31bUbzTY+xrfBoWe43CO3Ugle0ySOPdxRN5yuPJoV9xrYLfQSVdS/hiYOgySegA6k8l4LdRVE9X87XJnBUubiGB3/AIZh6ftHqfgsjPQUb6cPqastfWz4MzxyGOTR5D/qvZLPHBBJPPII42Ave93IAdVk5NwtMwi+1QfztcEnq+FTIDz/AGAftPkN9Ms1skq6ySSvqYzDFIMQRO9ru/0n+Z8Oi1d50258zq61holJ4nwchIfEeB+4+SlA3Wsu9xfQwsgpYhUV854IIM8z+kfBo5lZ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hcLu7st8bQVzx9WiuA9FlJ8hI+nf/cXX4nYnHgdio72laUZrjsh1LpIjD7nbpqeJ+fYlLD3bvg7B+Cvcc9+OW0LjD4nmNw+kY4gjwIOCpxpqo72091neFxHwO4XKdEahOrOz+x6lk2ludBFUzM/QmxwTD4SskHwXQdN1HdXHus7StLPiNx+9eXD1X1eb/s49piCeAFX+t4qxv/sq8Wdl6Xzj+6rXO9bbH8Vc7OfE+CtHXOyC4csbog5YROhTP3KnGDnH3I7kSnn+5ZXS0vwwq12CFVw9bcbJw5bhD0yUsnC7uT13H8Fzbtu0rdrrpSi1lpOInVuk6j51tnCN6hgGJ6U+LZI8tx1c1q6Jgjdh3G4XujkEkIeOvRblcuSfbTaJ1daNd6BterbHMJKK4QCZm+Sw8nMd+s1wLT5grfrhOkj/ANkfyj7hoKYiHSusny3ixF2zKeuGPSqVvQB2RI0eeB1XdlXMREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREGJ75mzNa2LiYebuLGPgsvREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERARWySMiidJI4NY0Zc5xwAPErmje119/qXRdnekblqamY/gdeZJG0FsByWktnk9aUAg5MTHjzQ06YXBrSSQAOZXKb32kXHU1RU2fs1ngbS08hhuGqp2B9LTEe1HTNO1RP8A8Nn1j0WpvFDe9UPc3Xeom1VE13/2PWAPp6NxHSomJ72oGfqgxsON2r1zxxyUH84dS2u00UYaI2hsNPTxjkHcmtaOg2HvXDLmnWL18XxbfefqNRRW6FkZtdijndTvlM09VO4yz10zvamleN3vPiemAABhbG0W+rvFe+i03UcEcTjFXX4NB7og7w0oOzpOhd7Mf6zthsbXYavU0IYYam2WB/5R0gMNVcG+AGzoYT1JxI/wYN39CpKOkoKCGjoaeKmpoWiOOCFoZHG0cgANgB4Lljx23eTvy/ImM8ON57PaKGx2mG322nEEEecNySSSclxJ3c4nJJO5J3XvHVV59DheGtuNPRtLC8Of+gOnvXfqPJjjcr/t7c9FQ+0CVw3tP7cbLoW5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qH1ZwR47Lq2iajvLIYgcPp5Tg+R9cfv+xL7I2Wo+3OR/yQ9I9nttrXNv09FJbtQSRuxJTUlPI6FkOej5msYPHhB8V89vnfPMS8DfAwBgADkB4AKSa+oG2ztK1TTRsxHUyw3iDi/Rl3d9jpD9ijJBDAVz48ZFzyuXagcRICOYX6N/I316zUvZk/S9aczUJ4Rk7lvRfnFy5jqu8/Je1vLpHtooIn1BZS1bhC9udiDyP2rec3GI/UtjQxga0YAVVhjqInwxycYw8AjzWZedoREQEREBERAXzH8rXsurNV6SZqC3Y46EFz2+IX04vDeLdT3ax1NBVRCSKWMtLT12Vl0PxnkEtJVbktex32EL7b+TP29wV1spNBX5rpX44GSO326D932L5t7a9BXHSHaXcYn2+WCjfMXQnh9XGVGNA6wqNF64pL3AwPMTsuaeq62biSvr7tXuDuyftutfavpunlMNH9DdKaJuPSqFx9dvmWE8Q8Nz9VfXNoutBfLDRXm11DKiirYGVNPMzcPje0OaR7wQuC2K6WPt27KXVD6eMVTGEFpb1xyPkQvH8mXUs+mrrd+w+9yv7y1F1dZJJTvLRPd60fvjeT8D4Bcqr6TREUBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBQHUlmazUE0A9SlvUZhcekc43Y/wDxY/xFT5ay/UAuFjmiGe8YO8jI5hzdx/D4oR8+zRvimdBPGWSRuLHM8CNiFYRn28+5qkWsIGi+x18WGsrohOSBsH8n/eM/FR8AHbGfeu2Lot6Y5jxVmMNIzk9fBXubx+OOhQjjZgAAdFYy8zxxE+XjyUJ7XLc659gWs6aLi76K2GvjLeklNI2YH7Iyp08dODYLz1lvZd7ZXWqVo4K6jnoznl9LE6P96l6Xbolrugv9itt7p/WZc6SCtbjr30TZP/q18qfKK+U3UXG31nZt2dXFzLMC6G6Xmllwa93J0FO8coQdnSDeQ5azAyTGtRdudSPko6N0DZ6uWjuMdp9A1DUsdiaKGGWWmbSxnmJJhFufqxj9dfN88hqHl5jY0YAaxgwGDo0DwC8/Fx6u69HPz+WMxi5075ZWk4GG4AAwGAcgAOQ8lmZkc/8AOV44vqB3IL2wYfCf02/gvVt4m7pQTbWPGCW5yt9SsLHteBniA+7K0VrIkjkiIHj9uxUgoCDbonkn6Mb48jgo3GypgOKVnD148e9e2FmaXh2zgsPwXlYMVLCd+JpYD5he+ADvXhh8HjP2LLpGSP14WyfpD7FezaYs59QqxMAe6M7AHI+O/wDFZcAOB4dwM/xRvaxzOLn4LGQe+OD7Yzy6hekxDiPkfuVszBjPgcoV43MHeuAxuAcKx4AqH89wCvVLGO+Bx5H4rBMzHC89Dg79CjLxvjw93kcrxOjH00fgc48iP45Wyewd97xzXnljInz4tx9h/wCqso072NMQOMZC1E0W3DjGOoPNb6QDcfon/wBlqalnr4Ow/crtxrUnbn96nOgqzguvo5ORURFnP6zdx93GoVKzgk57LdadqRSV0dRuDC9svPoD6w+wlCNh2wUHBfdN3vkyoimtE7xj9uPP+0P+BcyaSIgCNxsQu3dqluNz7KLn3XrTUTorjCRz+jOHH/ZvefguLSmOWpMseOCdonaB5jJ+w5+xZlL6rC7pjktjZ6+a23emrYHlkkTw8OHTBWsf4E+ayR+3knC0xH659iWrqTXfZLarn3olnijDJN9w4DBXTl8RfIe1jJ3lfpWaRzm472ME8uh/cvt1cLNVsREUBERAREQEREHzD8sq1wy9mENXHSsMjZN343X56Ss9fOV+rXbnpa06h7MquW7uf3NKwy8LepAX5a3qCD51qPR24jbIQ0HwyuvGlfQvyTe0R9n1w3TFdLilq8NbxHGD0+/b4rqXbxQXbSesrT2o6YiLbjp6p9NbGBj0mA/l4j5FmTjyPiviWw3apsmoqW6UkhZNBIHgjyK/SKlr7f2kfJ7ob/WP7mbuAeLG/GApl6pOnYNI6ntWtND2vVVknE1BcqZlTC7qA4btPmDkHzBW6Xyb8mDWUOldf3fseq5wLdVOfdbBxHZgJ+nph4cJBcB+iCfrL6yXNRERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQEREBERAREQf//Z")


WALL_PRESETS = [
    # 默认这张就是内嵌的图（原来是个 CSS 渐变，太素）。
    # 缩到 1600px、质量 82 —— 尺寸和观感之间的折中。
    # 用单引号：这个 css 会被拼进 style="background:..." 属性，
    # 双引号会把属性提前闭合，整条 background 失效（缩略图变一片白）。
    # 走接口而不是内嵌 data URI —— 它是几百 KB，内嵌的话
    # /admin 页面和 /admin/api/wallpaper/presets 会各传一遍。
    ("kv", "官方 KV", "url('/admin/api/wallpaper/builtin')"),
    ("aurora", "极光",
     "linear-gradient(140deg,#071019 0%,#12344a 30%,#1d5c66 48%,"
     "#2a3a6a 70%,#0a1022 100%)"),
    ("frost", "霜蓝",
     "linear-gradient(175deg,#0d1428 0%,#1b2a4c 34%,#2c4a72 58%,"
     "#16233f 80%,#0b1120 100%)"),
    ("rose", "绯樱",
     "radial-gradient(115% 90% at 22% 78%,#47203a 0%,#2a1a34 40%,"
     "#14101f 72%,#0a0a10 100%)"),
    ("gold", "香槟金",
     "radial-gradient(115% 90% at 74% 22%,#4a3a12 0%,#2a2110 38%,"
     "#14110a 70%,#0b0a0d 100%)"),
    ("ink", "墨",
     "linear-gradient(205deg,#0b0a0d 0%,#17171f 46%,#0e0e14 72%,"
     "#08080b 100%)"),
]
WALL_PRESET_MAP = {pid: (name, css) for pid, name, css in WALL_PRESETS}


def wall_value():
    """返回可直接放进 background-image 的值。

    可能是 url("data:...")（自定义图片），也可能是渐变（预设）。
    所以模板里不能再套 url()。
    """
    pid = cfg_str("wallpaper_preset", "kv")
    if pid == "custom":
        p = wall_custom_path()
        if p:
            uri = wall_uri()
            if uri:
                # 单引号：这个值可能被拼进 style="..." 属性，双引号会闭合属性
                return "url('%s')" % uri
        pid = "kv"          # 没图或读失败，回落到默认预设
    hit = WALL_PRESET_MAP.get(pid)
    if hit:
        return hit[1]
    return WALL_PRESET_MAP["kv"][1]


def wall_uri():
    """返回可直接放 CSS 的 data URI。按 mtime+size 缓存，不会每次读盘。"""
    p = wall_custom_path()
    if not p:
        return None
    try:
        st = os.stat(p)
        key = (p, st.st_mtime_ns, st.st_size)
        if _WALL_CACHE["key"] == key and _WALL_CACHE["uri"]:
            return _WALL_CACHE["uri"]
        ext = os.path.splitext(p)[1].lower()
        mime = {".png": "image/png", ".webp": "image/webp",
                ".gif": "image/gif"}.get(ext, "image/jpeg")
        with open(p, "rb") as f:
            raw = f.read()
        uri = "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())
        _WALL_CACHE.update({"key": key, "uri": uri})
        log("info", "外观", "壁纸已加载：%s（%.0f KB）"
            % (os.path.basename(p), len(raw) / 1024))
        return uri
    except Exception as ex:
        # 读失败返回 None，让 wall_value() 回落到预设渐变。
        # （以前这里返回一个内嵌的 23 KB JPEG 兜底，是纯浪费。）
        log("warn", "外观", "壁纸读取失败：%s" % ex)
        return None


GLASSOFF_CSS = r'''
/* 关掉侧栏/内容面玻璃时的兜底：给一个近乎不透明的底。
   不能只是"没玻璃" —— 那样文字直接压在壁纸上，读不了。 */
body.no-side-glass .side{background:rgba(16,20,28,.985)!important;
  backdrop-filter:none!important;-webkit-backdrop-filter:none!important}
body.no-face-glass .topbar,
body.no-face-glass .card{background:rgba(255,255,255,.985)!important;
  backdrop-filter:none!important;-webkit-backdrop-filter:none!important}
/* 深色模式下 --text 是 #d0e0f0（浅色），白底上只有 1.2:1，读不出东西。
   这里必须跟着主题给深色兜底底，侧栏那份本来就是深色所以没这个问题。 */

@media (prefers-color-scheme: dark){
  body.no-face-glass .topbar,
  body.no-face-glass .card{background:rgba(20,29,56,.985)!important}
}
'''


PICKER_CSS = r'''
/* ========== 取色器 ==========
   替代原来的 prompt() 手打 hex。 */
/* 用 fixed 而不是 absolute：admin 页 body{overflow:hidden}，
   靠下的色板会被裁掉下半截。fixed 不受祖先 overflow 裁剪。 */
.cp{position:fixed;z-index:200;width:236px;padding:12px;
  background:var(--card-bg);border:1px solid var(--line-2);
  border-radius:var(--radius-sm);box-shadow:var(--shadow-lg,0 12px 40px rgba(9,9,11,.22));
  backdrop-filter:blur(18px) saturate(1.5);
  -webkit-backdrop-filter:blur(18px) saturate(1.5)}
.cp[hidden]{display:none}
.cp-sv{position:relative;width:100%;height:150px;border-radius:var(--radius-xs);
  cursor:crosshair;overflow:hidden;touch-action:none;
  background:linear-gradient(to top,#000,rgba(0,0,0,0)),
             linear-gradient(to right,#fff,var(--cp-h,#f00))}
.cp-sv i{position:absolute;width:13px;height:13px;margin:-7px 0 0 -7px;
  border-radius:50%;border:2px solid #fff;pointer-events:none;
  box-shadow:0 0 0 1px rgba(0,0,0,.35),0 1px 4px rgba(0,0,0,.35)}
.cp-hue{position:relative;height:12px;margin-top:10px;border-radius:6px;
  cursor:pointer;touch-action:none;
  background:linear-gradient(to right,#f00 0%,#ff0 17%,#0f0 33%,#0ff 50%,
    #00f 67%,#f0f 83%,#f00 100%)}
.cp-hue i{position:absolute;top:-2px;width:5px;height:16px;margin-left:-2px;
  border-radius:3px;background:#fff;pointer-events:none;
  box-shadow:0 0 0 1px rgba(0,0,0,.3),0 1px 3px rgba(0,0,0,.3)}
.cp-foot{display:flex;align-items:center;gap:8px;margin-top:10px}
.cp-prev{width:26px;height:26px;border-radius:var(--radius-xs);flex-shrink:0;
  box-shadow:inset 0 0 0 1px rgba(9,9,11,.16)}
.cp-hex{flex:1;min-width:0;font-family:"Cascadia Mono",ui-monospace,Consolas,monospace;
  font-size:12px;padding:5px 8px;border:1px solid var(--line-2);
  border-radius:var(--radius-xs);background:var(--surface-2,var(--bg-2));
  color:var(--text)}
.cp-dots{display:flex;flex-wrap:wrap;gap:7px;margin-top:10px}
.cp-dots i{width:19px;height:19px;border-radius:50%;cursor:pointer;
  box-shadow:inset 0 0 0 1px rgba(9,9,11,.16)}
.cp-dots i:hover{transform:scale(1.14)}
.cp-reset{margin-top:10px;width:100%;justify-content:center}
'''


GALLERY_CSS = r'''
/* ========== 预设壁纸画廊 ========== */
.wall-tabs{display:flex;gap:8px;margin:2px 0 14px}
.wall-tab{flex:1;text-align:center;padding:9px 0;font-size:12.5px;font-weight:600;
  border:1px solid var(--line-2);border-radius:var(--radius-xs);cursor:pointer;
  color:var(--text-2);background:transparent;
  transition:background .16s var(--ease),border-color .16s var(--ease),
    color .16s var(--ease)}
.wall-tab:hover{color:var(--text);border-color:var(--line-3)}
.wall-tab.on{background:var(--azure-soft);border-color:var(--azure-line);
  color:var(--azure)}
.wall-grid{display:grid;gap:12px;
  grid-template-columns:repeat(auto-fill,minmax(136px,1fr))}
.wall-thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}
  .wall-thumb{position:relative;aspect-ratio:16/9;border-radius:var(--radius-sm);
  cursor:pointer;border:2px solid transparent;overflow:hidden;
  background-size:cover;background-position:center;
  transition:transform .14s var(--ease),border-color .14s var(--ease)}
.wall-thumb:hover{transform:translateY(-2px)}
.wall-thumb.on{border-color:var(--azure);
  box-shadow:0 0 0 3px var(--azure-soft)}
.wall-thumb.on::after{content:"✓";position:absolute;right:7px;bottom:7px;
  width:19px;height:19px;border-radius:50%;background:var(--azure);
  color:var(--on-accent);font-size:11px;font-weight:700;
  display:flex;align-items:center;justify-content:center}
.wall-thumb b{position:absolute;left:0;right:0;bottom:0;padding:18px 8px 6px;
  font-size:11px;font-weight:600;color:#fff;letter-spacing:.01em;
  background:linear-gradient(transparent,rgba(0,0,0,.62))}
.wall-empty{padding:22px 0;text-align:center;font-size:12.5px;
  color:var(--text-3);border:1px dashed var(--line-3);
  border-radius:var(--radius-sm)}
'''


SET_CSS = r'''
/* ========== 外观设置控件 ==========
   参考图那套：开关 / 滑杆(带数值框) / 色板。
   原来项目里一个都没有 —— 外观那几项只能填数字框，很难用。 */

/* 设置行：左边标题+说明，右边控件 */
/* 允许换行：放不下时控件整块折到下一行，
   而不是把左边的标签压成一条竖线（中文会一列一个）。 */
.set-row{display:flex;flex-wrap:wrap;align-items:center;
  gap:12px 18px;padding:13px 0;border-bottom:1px solid var(--line)}
/* 表单是两列栅格，横跨整行的设置项要显式跨栏，
   否则会被塞进其中一列（画廊就踩过这个）。 */
.set-row.full{grid-column:1/-1}
/* 窄屏（比如窗口缩到一半）时干脆上下排：标签在上、控件在下。
   两列挤在一起时英文数字还能忍，中文必竖排。 */
@media (max-width: 900px){
  .set-row{flex-direction:column;align-items:stretch;gap:8px}
  .set-row .set-t{flex:0 0 auto}
  .set-row .set-c{flex:0 0 auto}
  .sld{max-width:none}
}
.set-row:last-child{border-bottom:0}
/* flex-basis 给 150px：中文标签至少要这么宽才不会竖排 */
.set-row .set-t{flex:1 1 150px;min-width:0}
.set-row .set-t b{display:block;font-size:13px;font-weight:600;
  color:var(--text);letter-spacing:.005em}
.set-row .set-t span{display:block;font-size:11.5px;color:var(--text-3);
  margin-top:3px;line-height:1.5}
.set-row .set-c{flex:0 1 auto;min-width:0;
  display:flex;align-items:center;gap:12px}

/* 开关 */
.sw{position:relative;width:44px;height:25px;border-radius:13px;
  flex-shrink:0;background:var(--bg-4);border:1px solid var(--line-2);
  cursor:pointer;transition:background .18s var(--ease),
  border-color .18s var(--ease);-webkit-tap-highlight-color:transparent}
.sw::after{content:"";position:absolute;top:2px;left:2px;width:19px;height:19px;
  border-radius:50%;background:#fff;
  box-shadow:0 1px 3px rgba(9,9,11,.28);
  transition:transform .2s cubic-bezier(.22,.61,.36,1)}
.sw.on{background:var(--azure);border-color:var(--azure)}
.sw.on::after{transform:translateX(19px)}
.sw:active::after{width:22px}

/* 滑杆行 */
/* min-width 从 300px 降到 0 —— 那个硬值会把标签挤没。
   改用 flex-basis，够宽时照样撑满。 */
.sld{display:flex;align-items:center;gap:13px;
  flex:1 1 120px;min-width:0;max-width:340px}
.sld input[type=range]{flex:1;-webkit-appearance:none;appearance:none;
  height:4px;border-radius:2px;background:var(--bg-4);outline:none;
  cursor:pointer;margin:0}
.sld input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;
  width:15px;height:15px;border-radius:50%;background:#fff;
  border:2px solid var(--azure);cursor:pointer;
  box-shadow:0 1px 4px rgba(9,9,11,.22);
  transition:transform .12s var(--ease)}
.sld input[type=range]::-webkit-slider-thumb:hover{transform:scale(1.15)}
.sld input[type=range]::-moz-range-thumb{width:15px;height:15px;border-radius:50%;
  background:#fff;border:2px solid var(--azure);cursor:pointer}
.sld .v{width:58px;flex:0 0 auto;text-align:center;font-size:12px;font-weight:600;
  padding:5px 0;border:1px solid var(--line-2);border-radius:var(--radius-xs);
  background:var(--card-bg);color:var(--text);
  font-family:"Cascadia Mono",ui-monospace,Consolas,monospace;
  font-variant-numeric:tabular-nums}

/* 色板 */
.sws{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.sws i{width:28px;height:28px;flex:0 0 auto;border-radius:9px;cursor:pointer;display:block;
  border:2px solid transparent;box-shadow:inset 0 0 0 1px rgba(9,9,11,.14);
  transition:transform .12s var(--ease),border-color .12s var(--ease)}
.sws i:hover{transform:scale(1.08)}
.sws i.sel{border-color:var(--azure);
  box-shadow:inset 0 0 0 1px rgba(9,9,11,.14),0 0 0 3px var(--azure-soft)}
.sws .cus{font-size:11.5px;color:var(--text-3);cursor:pointer;
  padding:4px 9px;border:1px dashed var(--line-3);border-radius:var(--radius-xs)}
.sws .cus:hover{color:var(--azure);border-color:var(--azure-line)}
'''


SIDEBAR_CSS = r'''
/* 13. 侧栏做成深色玻璃：壁纸在上面透出来，导航用浅色字。
      这是"启动器"最典型的一层 —— 左轨道是图，右边是内容。 */
/* 底色不在这里给 —— 由 wall_css() 的 rgba(var(--side-rgb),var(--side-a))
   负责。这里如果也写 background，拼接顺序上会盖掉变量版，
   导致「侧栏玻璃颜色」和「侧栏不透明度」两个控件完全无效。 */
.side{border-color:rgba(255,255,255,.12)}
.side .nav-item{color:rgba(238,244,252,.80)}
.side .nav-item:hover{background:rgba(255,255,255,.10);color:#fff}
.side .nav-item.on{color:#fff;font-weight:650;
  background:linear-gradient(90deg,rgba(93,168,232,.30) 0%,
    rgba(93,168,232,.10) 62%,transparent 100%);
  box-shadow:inset 0 0 0 1px rgba(150,205,245,.34)}
.side .nav-item.on svg{color:#9ed4f7}
.side .nav-item.on::before{background:#7cc4f0}
.side .nav-section{color:rgba(226,236,248,.52)}
.side .nav-section::after{background:rgba(255,255,255,.12)}
.side .nav-item .badge{background:rgba(255,255,255,.16);
  color:rgba(240,246,255,.92);border-color:rgba(255,255,255,.20)}
.side .nav-item.on .badge{background:rgba(255,255,255,.22);color:#fff;
  border-color:rgba(255,255,255,.28)}
.side .nav-item .badge.alert{background:#d93a3a;color:#fff;border-color:#d93a3a}
.side-foot{background:rgba(255,255,255,.08);
  border-color:rgba(255,255,255,.14);color:rgba(226,236,248,.72)}
.side-foot .dot-live{background:#4ade80}
'''


FONT_CSS = r'''
/* 12. 字体分层（不嵌字体，靠系统栈）
   主流西文开源字体都没有中文字形，
   所以走"平台最优栈"这条路：0 字节，中文和拉丁各自拿到本机最好的那个。

   标题用 Display 变体（字重和字距更适合大字），正文用 Text 变体
   （小字号下 hinting 更好）。这是 Win11 可变字体的一对。 */
.topbar h1,.card-h h3,.cfg-group-title,.stat-grid .v{
  font-family:"Segoe UI Variable Display","Segoe UI Variable Text","Segoe UI",
    -apple-system,"PingFang SC","Noto Sans SC","Microsoft YaHei UI",
    "Microsoft YaHei",sans-serif}
.stat-grid .v,.metric .val,.clock,.log-line .log-ts{
  font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,Consolas,
    "Liberation Mono",monospace;
  font-variant-numeric:tabular-nums}
'''


TASTE_CSS = r'''
/* ========== 界面规范修正 ==========
   §7 NO Neon/Outer Glows —— 外发光全部换掉，改用内高光 + 同色低透明描边。
   §7 NO Oversized H1s  —— 层级靠字重和颜色，不靠字号。
   §3 Rule 3 ANTICENTER —— 统计行改非对称栅格（2fr 1fr 1fr 1fr 1fr 1fr）。
   §3 Rule 5 Tactile    —— :active 下沉 1px。
   §4 Liquid Glass      —— 1px 内边框 + inset 高光，模拟边缘折射。 */

/* 1. 去掉所有外发光 */
.btn.primary{box-shadow:inset 0 1px 0 rgba(255,255,255,.18)}
.btn.primary:hover{box-shadow:inset 0 1px 0 rgba(255,255,255,.24)}
.nav-item.on::before{box-shadow:none;width:3px}
.nav-item.on{box-shadow:inset 0 0 0 1px var(--aero-line)}
.upd-bar>i{box-shadow:none}
.dot-live{box-shadow:none}

/* 2. 页标题：降字号，改用字重 + 颜色拉层级（§7 NO Oversized H1s） */
.topbar h1{font-size:23px;font-weight:700;letter-spacing:-.022em;
  color:var(--text)}
.topbar h1::after{content:"";display:block;width:26px;height:2px;
  margin-top:7px;border-radius:1px;
  background:linear-gradient(90deg,var(--aero),transparent)}

/* 3. 统计行：非对称栅格（§7 NO 3-Column Card Layouts / §3 Rule 3） */
.stat-grid{grid-template-columns:2.1fr 1fr 1fr 1fr 1fr 1fr;gap:0}
.stat-grid>.stat-box{border-right:1px solid var(--line)}
.stat-grid>.stat-box:first-child{padding-left:30px}
.stat-grid>.stat-box:first-child .v{font-size:54px}
.stat-grid .v{font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  font-variant-numeric:tabular-nums;letter-spacing:-.045em}

/* 4. 触觉反馈（§3 Rule 5） */
.btn:active{transform:translateY(1px) scale(.995)}
.nav-item:active{transform:translateY(1px)}
.btn{transition:transform .1s var(--ease),background .15s var(--ease),
  border-color .15s var(--ease),color .15s var(--ease)}

/* 5. 液态玻璃的边缘折射（§4 Liquid Glass） */
.side,.topbar,.card{
  box-shadow:var(--glass-shadow),
    inset 0 1px 0 rgba(255,255,255,.55),
    inset 0 0 0 1px rgba(255,255,255,.10)}
@media (prefers-color-scheme: dark){
  .side,.topbar,.card{
    box-shadow:var(--glass-shadow),
      inset 0 1px 0 rgba(255,255,255,.09),
      inset 0 0 0 1px rgba(255,255,255,.04)}
}

/* 6. 状态点做"呼吸"（§4 Perpetual Micro-Interactions / MOTION_INTENSITY 6） */
@keyframes nekoe-breathe{
  0%,100%{opacity:1;transform:scale(1)}
  50%{opacity:.55;transform:scale(.86)}
}
.dot-live{animation:nekoe-breathe 2.6s cubic-bezier(.4,0,.6,1) infinite}

/* 7. 空状态：排版好一点（§3 Rule 5 Empty States） */
.empty{display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:9px;padding:54px 20px;text-align:center}
.empty .big{font-size:30px;opacity:.30;line-height:1;
  font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
.empty .msg{font-size:12.5px;color:var(--text-3);letter-spacing:.02em}
'''


SIDE_CSS = r'''
/* 10. 侧边栏做实 */
.nav{padding:10px 10px 0}
.nav-section{font-size:10.5px;letter-spacing:.15em;font-weight:700;
  color:var(--text-3);padding:16px 12px 7px;text-transform:uppercase;
  display:flex;align-items:center;gap:8px}
.nav-section::after{content:"";flex:1;height:1px;background:var(--line)}
.nav-item{padding:8px 11px;border-radius:var(--radius-s);gap:11px;
  font-size:13px;font-weight:450;color:var(--text-2);
  transition:background .13s var(--ease),color .13s var(--ease)}
.nav-item span{letter-spacing:.005em}
.nav-item:hover{background:var(--nav-hover);color:var(--text)}
/* 选中：填充 + 左侧竖条 + 主色文字，三重信号 */
.nav-item.on{background:linear-gradient(90deg,var(--aero-soft) 0%,
    rgba(28,94,155,.03) 62%,transparent 100%);
  color:var(--aero);font-weight:650;
  box-shadow:inset 0 0 0 1px var(--aero-line)}
.nav-item.on svg{color:var(--aero)}
.nav-item.on::before{width:3px;height:22px;
  box-shadow:0 0 9px var(--aero)}
/* 徽章：中性可见，只有"待处理"才红 */
.nav-item .badge{background:var(--bg-4);color:var(--text-2);
  border:1px solid var(--line-2);font-weight:650;
  font-size:10px;padding:1px 7px;border-radius:9px;min-width:19px}
.nav-item.on .badge{background:var(--card-bg);color:var(--aero);
  border-color:var(--aero-line)}
.nav-item .badge.alert{background:var(--red);color:var(--on-accent);
  border-color:var(--red);box-shadow:0 1px 4px rgba(220,38,38,.36)}

/* 11. 顶栏：指标改成一条"状态条"，不是一个一个空框 */
.topbar{gap:18px}
.top-metrics{background:var(--bg-2);border:1px solid var(--line-2);
  border-radius:var(--radius-s);padding:0;gap:0;overflow:hidden;
  box-shadow:var(--shadow-xs)}
.metric{background:transparent;border:0;border-radius:0;
  border-left:1px solid var(--line);padding:7px 15px;min-width:0;
  display:flex;flex-direction:column;align-items:flex-end;
  transition:background .13s var(--ease)}
.metric:first-child{border-left:0}
.metric:hover{background:transparent;box-shadow:none}
.metric .metric-l{font-size:9px;letter-spacing:.13em;font-weight:600;
  color:var(--text-3);text-transform:uppercase;line-height:1}
.metric .val{font-size:13px;font-weight:650;color:var(--text);
  margin-top:4px;line-height:1.2;
  font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
.clock{background:var(--bg-2);border-color:var(--line-2);
  font-weight:600;box-shadow:var(--shadow-xs)}
'''


FIX_CSS = r'''
/* 7. 顶栏指标：浅色玻璃上浅色叠浅色看不见，给它对比底 */
.topbar{background:var(--card-bg)}
.metric{background:var(--bg-3);border:1px solid var(--line-2);
  padding:6px 13px;border-radius:var(--radius-s);min-width:70px}
.metric:hover{background:transparent;border-color:var(--aero-line)}
.metric .metric-l{font-size:9.5px;letter-spacing:.11em;color:var(--text-3)}
.metric .val{font-size:13.5px;font-weight:650;margin-top:4px}
.clock{background:var(--bg-3);border-color:var(--line-2)}

/* 8. 侧边栏：分组标签加重量，选中项更像"选中" */
.nav-section{font-size:10px;letter-spacing:.17em;font-weight:700;
  color:var(--text-3);padding:15px 12px 7px;
  text-transform:uppercase}
.nav-item{padding:9px 11px;border-radius:var(--radius-s)}
.nav-item.on{background:var(--aero-soft)}
.nav-item.on svg{color:var(--aero)}
.nav-item svg{width:17px;height:17px}

/* 9. 浅色模式：用户实际看的是这套。玻璃加厚，壁纸留在四周和缝里 */
@media (prefers-color-scheme: light){
        --glass-line:rgba(10,14,26,.10);
        --glass-hi:inset 0 1px 0 rgba(255,255,255,.9);
        --glass-shadow:0 6px 26px rgba(8,14,32,.10)}
  .side .brand::before{background:linear-gradient(140deg,
    rgba(6,10,24,.66) 0%,rgba(6,10,24,.44) 55%,rgba(6,10,24,.22) 100%)}
}
'''


HERO_CSS = r'''
/* 6. 概览页：统计行 -> 全宽横幅
      跟内容卡片用**同一套玻璃**（--card-bg + 毛玻璃），数字用 --text。
      以前这里写死"深底 + 白字"，页面上其他面板都是浅色半透明，
      它就成了唯一一块近黑实心板 —— 看着格格不入。
      也不再重画壁纸：横幅是扁条，对整页壁纸 cover 出来的裁切跟页面
      背景完全不同，同一张脸会出现两份、大小还不同（重影）。

      --card-bg / --panel-blur 都带兜底：壁纸关掉时 wall_css 不注入，
      这两个变量根本不存在，不给兜底整条 background 会失效。 */
.stat-grid{position:relative;overflow:hidden;
  grid-template-columns:repeat(6,1fr);gap:0;
  border-radius:var(--radius);margin-bottom:20px;
  border:1px solid var(--glass-line);
  background:var(--card-bg,#fff);
  backdrop-filter:blur(var(--panel-blur,0px)) saturate(1.6);
  -webkit-backdrop-filter:blur(var(--panel-blur,0px)) saturate(1.6);
  box-shadow:var(--glass-shadow),var(--glass-hi)}
.stat-grid::before{content:"";position:absolute;inset:0;
  /* 一点方向光，给横幅加层次（不再是压图用的深罩） */
  background:linear-gradient(105deg,
    rgba(120,180,235,.055) 0%,transparent 58%)}
.stat-grid>.stat-box{position:relative;z-index:1;
  padding:26px 22px 24px;background:transparent;border:0;
  border-right:1px solid var(--line);border-radius:0;
  box-shadow:none;transition:background .18s var(--ease)}
.stat-grid>.stat-box:last-child{border-right:0}
.stat-grid>.stat-box:hover{background:transparent}
.stat-grid .stat-box::after{display:none}
.stat-grid .v{font-size:42px;font-weight:700;letter-spacing:-.04em;
  line-height:1;color:var(--text);font-variant-numeric:tabular-nums}
.stat-grid .l{margin-top:9px;font-size:12px;letter-spacing:.10em;
  text-transform:uppercase;color:var(--text-3)}
'''


EXTRA_CSS = r'''/* ================= 外观：启动器化 =================
   参考做法（游戏官网 / HoYo 系启动器）：
   大标题 + 英雄区露图 + 发光 + 大留白。
   这里的每条覆盖都写在注入块里，只有开了壁纸才生效。 */

/* 1. 英雄区：侧边栏顶部直接露壁纸，logo 压在上面 */
.side{padding:0;overflow:hidden;
  display:flex;flex-direction:column}
.side .brand{position:relative;padding:20px 16px 18px;
  margin:0;border-bottom:1px solid var(--glass-line);
  background-image:var(--wall);
  background-size:190% auto;background-position:38% 22%;
  background-repeat:no-repeat}
.side .brand::before{content:"";position:absolute;inset:0;
  background:linear-gradient(140deg,rgba(6,10,24,.72) 0%,
    rgba(6,10,24,.52) 55%,rgba(6,10,24,.30) 100%)}
.side .brand>*{position:relative;z-index:1}
.side .brand-title{color:#fff;text-shadow:0 1px 8px rgba(0,0,0,.6)}
.side .brand-sub{color:rgba(255,255,255,.62)}
.side .brand-ver{color:rgba(255,255,255,.72)}
.side .brand-mark{box-shadow:0 2px 8px rgba(0,0,0,.34),
  inset 0 1px 0 rgba(255,255,255,.28)}
.nav{padding:12px 12px 0}
.side-foot{margin:0 12px 12px;padding:10px 12px;border-radius:var(--radius-s);
  background:var(--glass-2);border:1px solid var(--glass-line)}

/* 2. 字放大、层级拉开 */
.topbar{padding:16px 26px}
.topbar h1{font-size:30px;font-weight:700;letter-spacing:-.025em;
  line-height:1.15}
.top-metrics{gap:8px}
.metric{padding:6px 14px;border-radius:var(--radius-s);
  background:var(--glass-2);border:1px solid var(--glass-line)}
.metric .val{font-size:14px;font-weight:650}
.metric-l{font-size:10px;letter-spacing:.12em}
.content{padding:22px 26px 18px}
.card{padding:26px 28px;margin-bottom:20px}
.card-h h3{font-size:17px;font-weight:650;letter-spacing:-.015em}
.card-h{padding-bottom:16px;margin-bottom:18px}
.cfg-group-title{font-size:14px;font-weight:650;letter-spacing:.01em;
  padding-left:11px;position:relative}
.cfg-group-title::before{content:"";position:absolute;left:0;top:50%;
  transform:translateY(-50%);width:3px;height:15px;
  background:linear-gradient(180deg,var(--azure),var(--azure-deep));
  border-radius:2px}

/* 3. 统计卡：数字放大 + 主色辉光 */
.stat,.stat-box,.ov-stat{position:relative;overflow:hidden}
.stat .v,.stat-box .v,.ov-stat .v,.stat .num,.ov-stat .num{
  font-size:40px;font-weight:750;letter-spacing:-.035em;line-height:1.05;
  font-variant-numeric:tabular-nums}
.stat .l,.stat-box .l,.ov-stat .l{font-size:12.5px;letter-spacing:.02em;
  margin-top:6px;color:var(--text-3)}
.stat::after,.stat-box::after,.ov-stat::after{content:"";position:absolute;
  right:-30px;top:-30px;width:110px;height:110px;border-radius:50%;
  background:radial-gradient(circle,var(--azure-soft) 0%,transparent 70%);
  pointer-events:none}

/* 4. 层级用描边和内高光表达，不用外发光
      （界面规范：不用外发光） */
.btn.primary{box-shadow:inset 0 1px 0 rgba(255,255,255,.18)}
.btn.primary:hover{box-shadow:inset 0 1px 0 rgba(255,255,255,.24)}
.nav-item.on{box-shadow:inset 0 0 0 1px var(--azure-line)}
.nav-item.on::before{border-radius:0 3px 3px 0}

/* 5. 大留白：表单与列表行都放开 */
.form-row label{font-size:12.5px;letter-spacing:.01em;margin-bottom:7px}
.form-row input,.form-row select,.form-row textarea{padding:11px 14px;
  font-size:13.5px;border-radius:var(--radius-s)}
.form-grid{gap:16px 18px}
.tbl td{padding:13px 12px}
.tbl th{padding:0 12px 14px;font-size:10.5px;letter-spacing:.13em}
.logs{padding:14px 10px}
'''


def _hex2rgb(h, dflt=(10, 16, 34)):
    """#RRGGBB 或 #RGB -> (r,g,b)。解析失败返回默认，不抛异常。"""
    try:
        h = str(h or "").strip().lstrip("#")
        if len(h) == 3:
            h = h[0] * 2 + h[1] * 2 + h[2] * 2
        if len(h) != 6:
            return dflt
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return dflt


def wall_css():
    """生成注入到 ADMIN_HTML 里的一整套外观 CSS。

    没开壁纸时返回空串 —— 页面退回原来的纯色底，不留半个空图层。
    """
    if not cfg_bool("wallpaper_enabled", True):
        return ""
    try:
        dim = max(0, min(88, cfg_int("wallpaper_dim", 40)))
    except Exception:
        dim = 40
    try:
        blur = max(0, min(40, cfg_int("wallpaper_blur", 0)))
    except Exception:
        blur = 0
    # 侧栏
    s_on = cfg_bool("ui_side_on", True)
    s_blur = max(0, min(40, cfg_int("ui_side_blur", 18)))
    s_alpha = max(0, min(100, cfg_int("ui_side_alpha", 72))) / 100.0
    s_rgb = _hex2rgb(cfg_str("ui_side_color", "#0a1022"))
    # 内容面
    f_on = cfg_bool("ui_face_on", True)
    f_blur = max(0, min(40, cfg_int("ui_face_blur", 24)))
    f_alpha = max(0, min(100, cfg_int("ui_face_alpha", 52))) / 100.0
    f_rgb = _hex2rgb(cfg_str("ui_face_color", "#ffffff"), (255, 255, 255))
    # 深色模式下的基底。浅色基底是白的，深色下必须换成深色，
    # 否则 --text 压在浅底上读不出来。
    f_rgb_d = _hex2rgb(cfg_str("ui_face_color_dark", "#141d38"),
                       (20, 29, 56))
    s_rgb_d = _hex2rgb(cfg_str("ui_side_color_dark", "#0a1022"), (10, 16, 34))

    d = dim / 100.0
    tpl = (
        # 浅深两组基底分开存，再用"别名"选当前生效的那个。
        # 不这么做的后果（踩过）：JS 改颜色时往 documentElement 写 inline 值，
        # inline 会盖过 @media —— 深色下反而被浅色值顶掉。
        # 分开存之后，JS 只写 -light / -dark 基底，别名交给 CSS 选。
        ":root{--wall:@WALL@;--wall-blur:@BLUR@px;"
        "--panel-blur:@FBLUR@px;--side-blur:@SBLUR@px;"
        "--face-rgb-light:@FRGB@;--face-rgb-dark:@FRGBD@;"
        "--side-rgb-light:@SRGB@;--side-rgb-dark:@SRGBD@;"
        "--face-rgb:var(--face-rgb-light);"
        "--side-rgb:var(--side-rgb-light);"
        "--face-a:@FA@;--side-a:@SA@;"
        "--face-loga:@LOGA@;"
        "--wall-dim:@DIM@;--wall-dim-1:@D1@;--wall-dim-2:@D2@;"
        "--wall-dim-4:@D4@;"
        # 面板令牌改成引用别名，alpha 用 calc 从 --face-a 派生 ——
        # 之前这里是 Python 写死的字面量，JS 改 --face-rgb 后它们不跟，
        # 表现为"拖滑杆 .card 变了、.group-block 这些没变"。
        "--card-bg:rgba(var(--face-rgb),var(--face-a));"
        "--bg-2:rgba(var(--face-rgb),calc(var(--face-a) * .90));"
        "--bg-3:rgba(var(--face-rgb),calc(var(--face-a) * .78));"
        "--bg-4:rgba(var(--face-rgb),calc(var(--face-a) * .66));"
        "--side-bg-1:rgba(var(--face-rgb),calc(var(--face-a) * .78));"
        "--side-bg-2:rgba(var(--face-rgb),calc(var(--face-a) * .66));"
        "--log-bg-1:rgba(var(--face-rgb),calc(var(--face-a) * .78));"
        "--log-bg-2:rgba(var(--face-rgb),calc(var(--face-a) * .66))}\n"
        # 只切别名。这段注入的 CSS 排在页面深色 @media 之后，
        # 所以这里能盖住页面的浅色令牌。
        "@media (prefers-color-scheme: dark){:root{"
        "--face-rgb:var(--face-rgb-dark);"
        "--side-rgb:var(--side-rgb-dark)}}\n"
        # 壁纸层 + 薄暗角（不再漂白壁纸）
        # 关掉壁纸时把两个图层收掉。之前 JS 只切了 class，CSS 里没这条规则，
        # 点开关画面纹丝不动，得刷新才生效。
        "body.no-wall::before,body.no-wall::after{display:none!important}\n"
        # .side .brand 直接引用了 var(--wall)，跟 body.no-wall 无关。
        # 不一起收掉的话，关壁纸后侧栏顶部还挂着图，看着像只生效了一半。
        "body.no-wall .side .brand,body.no-wall .brand{background-image:none!important}\n"
        "body.no-wall{background:var(--bg)}\n"
        "body::before{content:'';position:fixed;inset:0;z-index:-2;"
        "background-image:var(--wall);background-size:cover;"
        "background-position:center center;background-repeat:no-repeat;"
        "filter:blur(var(--wall-blur));transform:scale(1.08);"
        "will-change:transform}\n"
        "body::after{content:'';position:fixed;inset:0;z-index:-1;"
        "background:radial-gradient(130% 105% at 50% 42%,"
        "rgba(6,10,22,0) 38%,rgba(6,10,22,var(--wall-dim)) 100%)}\n"
        "@media (prefers-color-scheme: dark){body::after{"
        "background:linear-gradient(180deg,"
        "rgba(4,8,18,var(--wall-dim-1)) 0%,"
        "rgba(4,8,18,var(--wall-dim-2)) 52%,"
        "rgba(3,6,14,var(--wall-dim-4)) 100%)}}\n"
        # 面板之间留缝，让壁纸露出来
        ".app{background:transparent;padding:20px;gap:16px}\n"
        # 侧栏：颜色/不透明度/模糊全部可控
        ".side{background:rgba(var(--side-rgb),var(--side-a));"
        "backdrop-filter:blur(var(--side-blur)) saturate(1.6);"
        "-webkit-backdrop-filter:blur(var(--side-blur)) saturate(1.6);"
        "border:1px solid var(--glass-line);border-radius:var(--radius);"
        "box-shadow:var(--glass-shadow),var(--glass-hi)}\n"
        ".main{background:transparent}\n"
        # 内容面
        ".topbar{background:rgba(var(--face-rgb),var(--face-a));"
        "backdrop-filter:blur(var(--panel-blur)) saturate(1.6);"
        "-webkit-backdrop-filter:blur(var(--panel-blur)) saturate(1.6);"
        "border-bottom:1px solid var(--glass-line);"
        "border-radius:var(--radius) var(--radius) 0 0;"
        "box-shadow:var(--glass-hi)}\n"
        ".card{background:rgba(var(--face-rgb),var(--face-a));"
        "backdrop-filter:blur(var(--panel-blur)) saturate(1.6);"
        "-webkit-backdrop-filter:blur(var(--panel-blur)) saturate(1.6);"
        "border-color:var(--glass-line);"
        "box-shadow:var(--glass-shadow),var(--glass-hi)}\n"
        ".card:hover{border-color:var(--azure-line)}\n"
        # 关键一步：把面板类令牌本身改成半透明。
        # .group-block / .sub-panel / .plug-card / .inp / .kb-tree-row ...
        # 几十处都在用这些令牌，改令牌比改选择器省事，也不会漏。
        # 内层元素自己也要 blur，不然它们只是透出原图，不是毛玻璃
        ".group-block,.sub-panel,.plug-card,.raw-wrap,.upd-notes,.upd-dl,"
        ".logs{backdrop-filter:blur(calc(var(--panel-blur) * .8));"
        "-webkit-backdrop-filter:blur(calc(var(--panel-blur) * .8))}\n"
        ".logs{background:rgba(var(--face-rgb),var(--face-loga));"
        "backdrop-filter:blur(calc(var(--panel-blur) * .65));"
        "-webkit-backdrop-filter:blur(calc(var(--panel-blur) * .65))}\n"
    )
    # 侧栏/内容面的底色现在都由 CSS 里的 rgba(var(--X-rgb),var(--X-a)) 拼，
    # 这里不再需要预拼字符串。on/off 只体现在 body class 上。
    _ = (s_on, f_on)
    out = (tpl.replace("@WALL@", wall_value())
              .replace("@BLUR@", str(blur))
              .replace("@SBLUR@", str(s_blur))
              .replace("@FBLUR@", str(f_blur))
              .replace("@SRGB@", ",".join(map(str, s_rgb)))
              .replace("@FRGB@", ",".join(map(str, f_rgb)))
              .replace("@FRGBD@", ",".join(map(str, f_rgb_d)))
              .replace("@SRGBD@", ",".join(map(str, s_rgb_d)))
              .replace("@SA@", "%.3f" % s_alpha)
              .replace("@FA@", "%.3f" % f_alpha)
              .replace("@LOGA@", "%.3f" % min(1.0, max(0.0, f_alpha - 0.06)))
              .replace("@D1@", "%.3f" % (d * 0.78))
              .replace("@D2@", "%.3f" % d)
              .replace("@D4@", "%.3f" % min(0.92, d * 1.12))
              .replace("@DIM@", "%.3f" % min(0.72, 0.30 + d * 0.45)))
    # 这一串不能丢：磨砂、横幅、字号、控件样式都在里面
    return (out + EXTRA_CSS + HERO_CSS + FIX_CSS + SIDE_CSS + TASTE_CSS
            + FONT_CSS + SIDEBAR_CSS + SET_CSS + GLASSOFF_CSS + PICKER_CSS + GALLERY_CSS)


@app.get("/admin/api/wallpaper/presets")
async def api_wall_presets(token: str = ""):
    """预设列表。前端拿它画缩略图（缩略图直接渲染同一个渐变）。"""
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    return JSONResponse({
        "presets": [{"id": p, "name": n, "css": c}
                    for p, n, c in WALL_PRESETS],
        "current": cfg_str("wallpaper_preset", "kv"),
        "rotate": cfg_bool("wallpaper_rotate", False),
        "rotate_sec": cfg_int("wallpaper_rotate_sec", 15),
        "custom": bool(wall_custom_path()),
    })


@app.get("/admin/api/wallpaper/builtin")
async def api_wall_builtin():
    """把内置的那张默认壁纸吐出来。

    以前它是 `WALL_BAKED` 这个几百 KB 的 data URI，被拼进
    `WALL_PRESETS` —— 于是 /admin 页面和 /admin/api/wallpaper/presets
    各传一遍，开一次外观页光壁纸就下 500KB。
    改成走接口：两个地方都只带一个几十字节的 URL。
    不需要 token —— 它是内置资源，不含用户数据。
    """
    import base64 as _b64
    try:
        raw = _b64.b64decode(WALL_BAKED.split("base64,", 1)[1])
    except Exception as e:
        return JSONResponse({"error": f"内置壁纸损坏: {type(e).__name__}"},
                            status_code=500)
    return Response(content=raw, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/admin/api/wallpaper/raw")
async def api_wall_raw(token: str = ""):
    """把自定义壁纸原文件吐出来。

    为什么需要它：外观页的「我的图片」缩略图以前是个写死的黑块 ——
    api_wall_state 只回 custom/name/size，前端拿不到任何可用的图片地址。
    《token 走查询参数，跟这个页面其它接口一致》
    """
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    p = wall_custom_path()
    if not p or not os.path.exists(p):
        return JSONResponse({"error": "没有自定义壁纸"}, status_code=404)
    try:
        with open(p, "rb") as f:
            data = f.read()
    except Exception as e:
        return JSONResponse({"error": f"读取失败: {type(e).__name__}"},
                            status_code=500)
    ext = os.path.splitext(p)[1].lower()
    mime = {".png": "image/png", ".webp": "image/webp",
            ".gif": "image/gif"}.get(ext, "image/jpeg")
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "no-cache"})


@app.get("/admin/api/wallpaper/state")
async def api_wall_state(token: str = ""):
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    p = wall_custom_path()
    return JSONResponse({
        "on": cfg_bool("wallpaper_enabled", True),
        "dim": cfg_int("wallpaper_dim", 40),
        "blur": cfg_int("wallpaper_blur", 0),
        "custom": bool(p),
        "name": os.path.basename(p) if p else "",
        "size": (os.path.getsize(p) if p else 0),
    })


@app.post("/admin/api/wallpaper")
async def api_wall_set(token: str = "", request: Request = None):
    """上传壁纸。body: {"data":"<base64>","ext":".jpg"}，也接受 data URI。"""
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    raw = str(body.get("data") or "")
    if not raw:
        return JSONResponse({"error": "没有收到图片数据"}, status_code=400)
    ext = str(body.get("ext") or "").lower()
    if raw.startswith("data:"):
        m = re.match(r"data:image/([a-z+]+);base64,", raw)
        if m:
            ext = "." + ("jpg" if m.group(1) == "jpeg" else m.group(1))
        raw = raw.split(",", 1)[-1]
    if ext not in WALL_EXTS:
        ext = ".jpg"
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception:
        return JSONResponse({"error": "base64 解不开"}, status_code=400)
    if len(data) < 1024:
        return JSONResponse({"error": "图太小了（<1KB），不像是张图"},
                            status_code=400)
    if len(data) > 16 * 1024 * 1024:
        return JSONResponse({"error": "图太大了（>16MB），压一下再传"},
                            status_code=400)
    # 先清掉旧的，避免留下两种扩展名同时存在
    for e in WALL_EXTS:
        try:
            os.remove(WALL_STEM + e)
        except Exception:
            pass
    dst = WALL_STEM + ext
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)
    except Exception as ex:
        return JSONResponse({"error": "写文件失败：%s" % ex}, status_code=500)
    _WALL_CACHE.update({"key": None, "uri": None})
    log("success", "外观", "壁纸已更换：%s（%.0f KB）"
        % (os.path.basename(dst), len(data) / 1024))
    return JSONResponse({"ok": True, "name": os.path.basename(dst),
                         "size": len(data)})


@app.post("/admin/api/wallpaper/reset")
async def api_wall_reset(token: str = ""):
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    n = 0
    for e in WALL_EXTS:
        try:
            os.remove(WALL_STEM + e)
            n += 1
        except Exception:
            pass
    _WALL_CACHE.update({"key": None, "uri": None})
    log("info", "外观", "壁纸已恢复默认（删掉 %d 个自定义文件）" % n)
    return JSONResponse({"ok": True, "removed": n})


def render_admin_html():
    """把壁纸注入到管理页。

    用服务时替换而不是开一个图片接口：静态 CSS 里拿不到 token，
    而壁纸接口是要鉴权的。嵌进去最省事，也不用担心缓存。
    """
    try:
        css = wall_css()
    except Exception as ex:
        log("warn", "外观", "壁纸注入失败，退回无壁纸：%s" % ex)
        css = ""
    return ADMIN_HTML.replace("/*__WALLPAPER__*/", css)


@app.get("/admin")
async def admin_root(token: str = ""):
    if not _auth(token):
        _401_HINT = (
        "<div style='font-family:sans-serif;max-width:640px;margin:0 auto;"
        "padding:60px 24px;line-height:1.9;color:#333'>"
        "<h1 style='margin:0 0 8px'>401 Unauthorized</h1>"
        "<p style='color:#666;margin:0 0 20px'>管理密码不对。</p>"
        "<p><b>如果你刚跑完注册向导</b>，密码里可能带了 <code># &amp; = +</code> "
        "或空格、中文 —— 这类字符直接拼进地址会被浏览器截断。"
        "请手动把密码做 URL 编码后访问：</p>"
        "<p style='background:#f5f5f5;padding:12px 16px;border-radius:8px;"
        "font-family:monospace;word-break:break-all'>"
        "http://127.0.0.1:PORT/admin?token=<b>编码后的密码</b></p>"
        "<p style='color:#666;font-size:13px'>"
        "编码办法：在地址栏把特殊字符换成 <code>%XX</code>"
        "（<code>#</code>→<code>%23</code>，<code>&amp;</code>→<code>%26</code>，"
        "空格→<code>%20</code>）；或者干脆去 config.json 里把 "
        "<code>admin_token</code> 换成一个纯字母数字的密码。</p>"
        "</div>")
        return HTMLResponse(_401_HINT.replace("PORT", str(PORT)),
                            status_code=401)
    # 加 no-store：管理页每次发版都会变，绝不能让它被浏览器缓存。
    # 之前一个缓存头都不发，浏览器会启发式缓存 —— 升级后刷新
    # 还是旧界面，看着就像「新版本又出新 bug」。
    return HTMLResponse(render_admin_html(),
        headers={"Cache-Control":
             "no-store, no-cache, must-revalidate",
             "Pragma": "no-cache"})


@app.get("/admin/api/logs")
async def api_logs(token: str = "", since: int = 0):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    return JSONResponse(LOG_BUF.since(since))


@app.get("/admin/api/stats")
async def api_stats(token: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    _prune_pending_verify()
    bots = nonebot.get_bots()
    bot_info = []
    for sid, b in bots.items():
        bot_info.append({"self_id": str(sid)})
    return JSONResponse({
        "uptime": time.time() - START_TIME,
        "memory_total": DB.mem_count(),
        "blacklist_total": DB.bl_count(),
        "active_groups": len(ai_context),
        "pending_count": len(pending_verify),
        "request_count": len(pending_requests),
        "mem_saved": mem_stats["saved"],
        "mem_skipped": mem_stats["skipped"],
        "model": AI.model,
        "backend": AI.backend,
        "bots": bot_info,
        "vision_enabled": cfg_bool("vision_enabled", False),
        "vision_model": cfg_str("vision_model"),
        "voice_enabled": cfg_bool("voice_enabled", False),
        "voice_name": cfg_str("voice_name"),
    })



# ---------------- 知识库管理接口 ----------------
def _kb_row_out(row, score=None):
    tag_list = kb_clean_tags(row["tags"])
    cats = [t for t in tag_list if t in KB_CATEGORY_ORDER]
    out = {
        "id": row["id"],
        "group_id": row["group_id"],
        "scope": "全局" if row["group_id"] == 0 else "本群",
        "question": row["question"],
        "answer": row["answer"],
        "tags": row["tags"] or "",
        "tags_list": tag_list,
        "category": cats[0] if cats else KB_CATEGORY,
        "folder": (row.get("folder") or "") if hasattr(row, "get") else "",
        "author_id": row["author_id"] or 0,
        "author_name": row["author_name"] or "",
        "hits": row["hits"] or 0,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"] or row["created_at"],
    }
    if score is not None:
        out["score"] = round(float(score), 3)
    return out


def _kb_filter(rows, category="", game="", folder=None):
    """按 游戏 + 分类 + 文件夹 过滤（可叠加）。

    folder=None 表示不按文件夹过滤；folder="" 表示只看根目录。
    """
    cat = (category or "").strip()
    gm = (game or "").strip()
    out = []
    for r in rows:
        tags = kb_clean_tags(r.get("tags"))
        if gm and kb_game(r["question"], tags) != gm:
            continue
        if cat:
            cats = [t for t in tags if t in KB_CATEGORY_ORDER]
            own = cats[0] if cats else KB_CATEGORY
            if own != cat:
                continue
        if folder is not None:
            if (r.get("folder") or "").strip() != folder.strip():
                continue
        out.append(r)
    return out


@app.get("/admin/api/kb/categories")
async def api_kb_categories(token: str = "", group_id: int = -1, game: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    g = None if group_id < 0 else group_id
    return JSONResponse({
        "categories": [{"name": c, "count": n} for c, n in
                       kb_category_counts(g, game or None)],
        "games": [{"name": x, "count": n} for x, n in kb_game_counts(g)],
        "total": len(DB.kb_list(g, limit=100000)),
        "order": KB_CATEGORY_ORDER,
        "game_order": KB_GAMES,
    })


@app.post("/admin/api/kb/reclassify")
async def api_kb_reclassify(token: str = "", group_id: int = -1):
    """按问题内容重新打标签（游戏 + 分类，保留具体名词标签）。"""
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    g = None if group_id < 0 else group_id
    stat = kb_reclassify(g)
    log("success", "知识库",
        f"重新分类：{stat['total']} 条，改动 {stat['changed']} 条，游戏分布 {stat['games']}")
    return JSONResponse({"ok": True, **stat})


@app.post("/admin/api/kb/clear_category")
async def api_kb_clear_category(token: str = "", request: Request = None):
    """按 文件夹/游戏/分类 批量删除。body: {folder?, category?, game?, group_id?}

    folder 传 "" 表示"根目录（未归档）"；不传该键表示不按文件夹过滤。
    """
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    # all=True 才表示"不设范围、整库清"。必须显式传 ——
    # 光靠"不传 folder" 不行：下面的守卫会拦，而且太容易误用。
    #
    # 踩过的坑：前端在「全部条目」下传的是 folder:""，
    # 而后端把 "" 解释成"根目录（未归档）"。UI 写着「清空全部条目」，
    # 实际只删了未归档那部分，其余安静地留在各文件夹里 —— 不可恢复。
    all_ = bool(body.get("all"))
    cat = str(body.get("category", "")).strip()
    gm = str(body.get("game", "")).strip()
    folder = body.get("folder", None)
    if folder is not None:
        folder = str(folder).strip().strip("/")
    if not all_ and not cat and not gm and folder is None:
        return JSONResponse({"error": "缺少 all / folder / category / game"},
                            status_code=400)
    try:
        gid = int(body.get("group_id", -1))
    except (TypeError, ValueError):
        gid = -1
    rows = _kb_filter(DB.kb_list(None if gid < 0 else gid, limit=100000),
                      cat, gm, None if all_ else folder)
    deleted = 0
    for r in rows:
        if DB.kb_delete(r["id"]):
            deleted += 1
    label = " / ".join(x for x in (gm, cat, folder if folder is not None else "") if x)
    if not label:
        label = "全部（含所有文件夹）" if all_ else (
            "根目录（未归档）" if folder == "" else "全部")
    log("block", "知识库", f"按范围删除「{label}」：{deleted} 条")
    return JSONResponse({"ok": True, "deleted": deleted, "label": label})


@app.get("/admin/api/kb/folders")
async def api_kb_folders(token: str = "", group_id: int = -1):
    """文件夹树（含空文件夹）。"""
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    g = None if group_id < 0 else group_id
    rows = DB.kb_list(g, limit=100000)
    counts = {}
    root = 0
    for r in rows:
        f = (r.get("folder") or "").strip()
        if f:
            counts[f] = counts.get(f, 0) + 1
        else:
            root += 1
    paths = DB.kb_folder_list(g)
    for p in paths:
        counts.setdefault(p, 0)
    return JSONResponse({
        "folders": [{"path": p, "count": counts.get(p, 0)} for p in paths],
        "root_count": root,
        "total": len(rows),
    })


@app.post("/admin/api/kb/folder/create")
async def api_kb_folder_create(token: str = "", request: Request = None):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    path = str(body.get("path", "")).strip().strip("/")
    if not path:
        return JSONResponse({"error": "文件夹名不能为空"}, status_code=400)
    if len(path) > 120:
        return JSONResponse({"error": "文件夹名太长"}, status_code=400)
    if ".." in path:
        return JSONResponse({"error": "文件夹名不能包含 .."}, status_code=400)
    try:
        gid = int(body.get("group_id", 0))
    except (TypeError, ValueError):
        gid = 0
    if gid < 0:
        gid = 0          # 负数 = 幽灵群，文件夹谁也看不到
    DB.kb_folder_create(gid, path)
    log("success", "知识库", f"新建文件夹「{path}」@群{gid}")
    return JSONResponse({"ok": True, "path": path})


@app.post("/admin/api/kb/folder/delete")
async def api_kb_folder_delete(token: str = "", request: Request = None):
    """删除文件夹（只删文件夹本身，里面的条目移回根目录，不删条目）。"""
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    path = str(body.get("path", "")).strip()
    if not path:
        return JSONResponse({"error": "缺少 path"}, status_code=400)
    # kb_folders 是按 group_id 隔离存的（见 kb_folder_create）。
    # 这里以前调 DB.kb_folder_delete(path)，g 是 None -> 走全库分支，
    # 把**所有群**的同名文件夹一起删了：A 群建了「剧情」、B 群也建了，
    # 在 A 群点删除，B 群的一起没。
    # 默认取 0（共享域）而不是 None —— "没传"绝不该等于"删全部"。
    try:
        g = int(body.get("group_id", 0))
    except (TypeError, ValueError):
        g = 0
    # 跟 kb_folder_create 一个约定：-1（全部群视图）按共享域处理
    if g < 0:
        g = 0
    DB.kb_folder_delete(path, g)
    log("block", "知识库", f"删除文件夹「{path}」@群{g}（条目已移回根目录）")
    return JSONResponse({"ok": True, "path": path})
@app.post("/admin/api/kb/move")
async def api_kb_move(token: str = "", request: Request = None):
    """把条目移动到指定文件夹（folder 为空 = 移回根目录）。"""
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    folder = str(body.get("folder", "")).strip().strip("/")
    ids = body.get("ids")
    if ids is None and body.get("id") is not None:
        ids = [body.get("id")]
    if not isinstance(ids, list) or not ids:
        return JSONResponse({"error": "缺少 id 或 ids"}, status_code=400)
    if ".." in folder:
        return JSONResponse({"error": "文件夹名不能包含 .."}, status_code=400)
    # 移到不存在的文件夹时自动创建，避免"移了个假文件夹"
    if folder:
        try:
            _mg = int(body.get("group_id", 0))
            DB.kb_folder_create(_mg if _mg > 0 else 0, folder)
        except Exception:
            pass
    n = DB.kb_set_folders_bulk(ids, folder)
    log("success", "知识库", f"移动 {n} 条 -> 「{folder or '根目录'}」")
    return JSONResponse({"ok": True, "moved": n, "folder": folder})


@app.get("/admin/api/kb")
async def api_kb_list(token: str = "", group_id: int = -1, q: str = "",
                      category: str = "", game: str = "",
                      folder: Optional[str] = None):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    g = None if group_id < 0 else group_id
    meta = {
        "categories": [{"name": c, "count": n} for c, n in
                       kb_category_counts(g, game or None)],
        "games": [{"name": x, "count": n} for x, n in kb_game_counts(g)],
        "total": len(DB.kb_list(g, limit=100000)),
    }
    if q.strip():
        qn = _kb_norm(q)
        tq = _kb_terms(q)
        rows = DB.kb_list(g, limit=5000)
        scored = sorted(((_kb_score(tq, qn, r), r) for r in rows), key=lambda x: -x[0])
        items = [_kb_row_out(r, s) for s, r in scored if s > 0.05][:200]
        return JSONResponse({"items": items, "query": q, "groups": DB.kb_groups(), **meta})
    rows = DB.kb_list(g, limit=5000)
    rows = _kb_filter(rows, category, game, folder)
    items = [_kb_row_out(r) for r in rows]
    return JSONResponse({"items": items, "groups": DB.kb_groups(), **meta})


@app.post("/admin/api/kb/add")
async def api_kb_add(token: str = "", request: Request = None):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    try:
        gid = int(body.get("group_id", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "group_id 必须是数字"}, status_code=400)
    if gid < 0:
        gid = 0          # 负数 = 幽灵群，条目在任何群里都查不到
    ok, res = kb_add_entry(gid, body.get("question", ""), body.get("answer", ""),
                           tags=str(body.get("tags", "")), uid=0, uname="控制台",
                           folder=str(body.get("folder", "")))
    if not ok:
        return JSONResponse({"error": res}, status_code=400)
    return JSONResponse({"ok": True, **res})


@app.post("/admin/api/kb/update")
async def api_kb_update(token: str = "", request: Request = None):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    try:
        kid = int(body.get("id", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "id 必须是数字"}, status_code=400)
    if not kid:
        return JSONResponse({"error": "缺少 id"}, status_code=400)
    if not DB.kb_get(kid):
        return JSONResponse({"error": "条目不存在"}, status_code=404)
    max_ans = cfg_int("kb_answer_max_chars", 500)
    ans = body.get("answer")
    if ans is not None and len(str(ans)) > max_ans:
        return JSONResponse({"error": f"答案太长（最多 {max_ans} 字）"}, status_code=400)
    # 这里的 group_id 是"把条目改挂到哪个群"。
    # 负数会挪进幽灵群（kb_list 过滤是 IN (0,?)，之后任何群都看不到）；
    # 而且 int() 以前不在 try 里，传个非数字会直接 500。
    gid = body.get("group_id")
    move_gid = None
    if gid is not None and gid != "":
        try:
            move_gid = int(gid)
        except (TypeError, ValueError):
            return JSONResponse({"error": "group_id 必须是数字"}, status_code=400)
        if move_gid < 0:
            move_gid = 0
    ok = DB.kb_update(kid, question=body.get("question"), answer=ans,
                      tags=body.get("tags"), group_id=move_gid)
    if not ok:
        return JSONResponse({"error": "没有需要更新的字段"}, status_code=400)
    log("success", "知识库", f"更新 #{kid}")
    return JSONResponse({"ok": True, "item": _kb_row_out(DB.kb_get(kid))})


@app.post("/admin/api/kb/delete")
async def api_kb_delete(token: str = "", request: Request = None):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    ids = body.get("ids")
    if ids is None and body.get("id") is not None:
        ids = [body.get("id")]
    if not isinstance(ids, list) or not ids:
        return JSONResponse({"error": "缺少 id 或 ids"}, status_code=400)
    deleted = 0
    for raw in ids:
        try:
            if DB.kb_delete(int(raw)):
                deleted += 1
        except (TypeError, ValueError):
            continue
    log("block", "知识库", f"控制台删除 {deleted} 条")
    return JSONResponse({"ok": True, "deleted": deleted})


@app.post("/admin/api/kb/clear")
async def api_kb_clear(token: str = "", group_id: int = 0):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    n = DB.kb_clear_group(group_id)
    log("block", "知识库", f"清空群 {group_id} 的 {n} 条知识")
    return JSONResponse({"ok": True, "deleted": n})


@app.get("/admin/api/kb/search")
async def api_kb_search(token: str = "", group_id: int = 0, text: str = ""):
    """模拟群里的匹配效果，用来调阈值。"""
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    if not text.strip():
        return JSONResponse({"error": "缺少 text"}, status_code=400)
    qn = _kb_norm(text)
    tq = _kb_terms(text)
    rows = DB.kb_list(group_id, limit=5000)
    scored = sorted(((_kb_score(tq, qn, r), r) for r in rows), key=lambda x: -x[0])
    thr = cfg_float("kb_match_threshold", 0.5)
    return JSONResponse({
        "query": text,
        "threshold": thr,
        "would_hit": [_kb_row_out(r, s) for s, r in scored if s >= thr][:10],
        "near_miss": [_kb_row_out(r, s) for s, r in scored if 0.05 < s < thr][:10],
    })


@app.post("/admin/api/kb/import")
async def api_kb_import(token: str = "", request: Request = None):
    """批量导入：{"group_id":0,"items":[{"question":..,"answer":..,"tags":..}]}
    也接受纯文本 lines（每行「问题|答案」）。"""
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    try:
        gid = int(body.get("group_id", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "group_id 必须是数字"}, status_code=400)
    # 负数会写进"幽灵群"：kb_list 的过滤是 group_id IN (0,?)，-1 的条目
    # 在任何具体群里都查不到，聊天也检索不到 —— 等于白导入。
    # 接口层不该接受这种值，一律归到全局（0）。
    if gid < 0:
        gid = 0

    items = body.get("items")
    if items is None and isinstance(body.get("text"), str):
        parsed = []
        for line in body["text"].splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            q, a = parse_kb_add(line)
            if q and a:
                parsed.append({"question": q, "answer": a})
        items = parsed
    if not isinstance(items, list) or not items:
        return JSONResponse({"error": "没有可导入的条目（格式：每行「问题|答案」）"},
                            status_code=400)

    added, failed = 0, []
    folder_default = str(body.get("folder", "")).strip()
    for it in items:
        if not isinstance(it, dict):
            failed.append("格式错误")
            continue
        ok, res = kb_add_entry(gid, it.get("question", ""), it.get("answer", ""),
                               tags=str(it.get("tags", "")), uid=0, uname="导入",
                               folder=str(it.get("folder", folder_default)))
        if ok:
            added += 1
        else:
            failed.append(f"{str(it.get('question'))[:20]}: {res}")
    log("success", "知识库", f"导入 {added} 条到群 {gid}，失败 {len(failed)}")
    return JSONResponse({"ok": True, "added": added, "failed": failed[:20],
                         "failed_count": len(failed)})


@app.get("/admin/api/kb/export")
async def api_kb_export(token: str = "", group_id: int = -1):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    rows = DB.kb_list(None if group_id < 0 else group_id, limit=20000)
    lines = [f"{r['question']}|{r['answer']}" for r in rows]
    return JSONResponse({"text": "\n".join(lines), "count": len(rows)})


@app.get("/admin/api/memories")
async def api_mem(token: str = "", view: str = "group", user_id: int = 0):
    """view=group（默认）按群列；view=user 按人列（跨群）。"""
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    view = (view or "group").strip().lower()
    if view == "user":
        if not user_id:
            return JSONResponse({"view": "user", "users": DB.mem_users()})
        rows = DB.mem_list_by_user(user_id)
        return JSONResponse({"view": "user", "user_id": user_id, "count": len(rows),
                             "groups": DB.mem_groups_of_user(user_id),
                             "memories": rows})
    res = []
    for g in DB.mem_all_groups():
        gid = g["group_id"]
        ms = DB.mem_list_group(gid, limit=100)
        res.append({"group_id": gid, "count": g["cnt"],
                    "user_count": g.get("user_cnt") or 0,
                    "memories": [{"id": m["id"], "content": m["content"],
                                  "category": m.get("category", "其他"),
                                  "importance": m.get("importance", 3),
                                  "created_at": m["created_at"],
                                  "scope": m.get("scope") or "group",
                                  "user_id": m.get("user_id") or 0} for m in ms]})
    return JSONResponse(res)


@app.post("/admin/api/memories/clear")
async def api_mem_clear(token: str = "", group_id: int = 0):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    DB.mem_clear_group(group_id); return JSONResponse({"ok": True})


@app.post("/admin/api/memories/delete")
async def api_mem_del(token: str = "", id: int = 0):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    DB.mem_delete_one(id); return JSONResponse({"ok": True})


@app.post("/admin/api/memories/clear_user_all")
async def api_mem_clear_user_all(token: str = "", user_id: int = 0):
    """删掉某人在【所有群】的专属记忆（群级记忆不动）。"""
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    if not user_id:
        return JSONResponse({"error": "缺少 user_id"}, status_code=400)
    groups = DB.mem_groups_of_user(user_id)
    n = DB.mem_clear_user_all(user_id)
    log("block", "记忆", f"控制台清空 用户{user_id} 在 {len(groups)} 个群的全部专属记忆（{n} 条）")
    return JSONResponse({"ok": True, "deleted": n, "groups": len(groups)})


@app.post("/admin/api/memories/add")
async def api_mem_add(token: str = "", request: Request = None):
    """手动加一条记忆（AI 自动记忆之外的人工补充/纠正）。"""
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)

    content = str(body.get("content", "")).strip()
    if not content:
        return JSONResponse({"error": "内容不能为空"}, status_code=400)
    if len(content) > 200:
        return JSONResponse({"error": "内容太长（最多 200 字）"}, status_code=400)

    try:
        gid = int(body.get("group_id", 0) or 0)
        uid = int(body.get("user_id", 0) or 0)
        imp = int(body.get("importance", 3) or 3)
    except (TypeError, ValueError):
        return JSONResponse({"error": "群号/QQ/重要度必须是数字"}, status_code=400)
    # 负数群号会写进"幽灵群"，任何群视图里都看不到。
    # （注意：私聊记忆的 key 确实是负 uid，但那是 bot 自己写的，
    #   不走这个接口 —— 这里只归一化网页传进来的群号。）
    if gid < 0:
        gid = 0

    scope = str(body.get("scope", "group")).strip().lower()
    if scope not in ("group", "user"):
        return JSONResponse({"error": "scope 只能是 group 或 user"}, status_code=400)
    # 用户级必须有 QQ；私聊时群号固定 0
    if scope == "user" and not uid:
        return JSONResponse({"error": "用户级记忆必须提供 user_id"}, status_code=400)
    if scope == "group":
        uid = 0
    imp = max(1, min(5, imp))
    cat = str(body.get("category", "其他")).strip() or "其他"

    uname = ""
    if scope == "user":
        try:
            uname = DB.nick_lookup(uid, gid) or ""
        except Exception:
            uname = ""
    DB.mem_add(gid, content, cat, imp, uid=uid, uname=uname, scope=scope)
    who = f"群{gid}" if scope == "group" else f"群{gid} 用户{uid}"
    log("block", "记忆", f"控制台手动添加 [{who}] [{cat}·{imp}星] {content[:40]}")
    return JSONResponse({"ok": True})


@app.post("/admin/api/memories/clear_user")
async def api_mem_clear_user(token: str = "", group_id: int = 0, user_id: int = 0):
    """只清掉某人在某群（或私聊 user_id + group_id=0）的专属记忆。"""
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    if not user_id:
        return JSONResponse({"error": "缺少 user_id"}, status_code=400)
    DB.mem_clear_user(group_id, user_id)
    log("block", "记忆", f"控制台清空 群{group_id} 用户{user_id} 的专属记忆")
    return JSONResponse({"ok": True})


# ---------------- 聊天记录 ----------------
def _log_scope(group_id, user_id):
    """把 group_id/user_id 转成"不限制 / 具体值"。

    约定：group_id=-1 表示不限群；user_id=-1 表示不限人。
    （-1 不是合法群号也不是合法 QQ，用它当通配符最省事。）
    """
    g = None if int(group_id) < 0 else int(group_id)
    u = None if int(user_id) < 0 else int(user_id)
    return g, u


@app.get("/admin/api/chatlog")
async def api_chatlog(token: str = "", group_id: int = -1, user_id: int = -1,
                      limit: int = 200, keyword: str = "", before_id: int = 0):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    g, u = _log_scope(group_id, user_id)
    limit = max(1, min(500, int(limit or 200)))
    rows = DB.log_list(g=g, uid=u, limit=limit, keyword=keyword.strip(),
                       before_id=int(before_id or 0))
    total = DB.log_count(g=g, uid=u)
    # 倒序返回，前端自己按时间正序渲染
    return JSONResponse({"total": total, "count": len(rows), "items": rows})


@app.get("/admin/api/chatlog/groups")
async def api_chatlog_groups(token: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    return JSONResponse(DB.log_groups())


@app.get("/admin/api/chatlog/users_all")
async def api_chatlog_users_all(token: str = ""):
    """所有说过话的人（跨群聚合），用于"按人"清记录。"""
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    return JSONResponse(DB.log_users(g=None))


@app.get("/admin/api/chatlog/users")
async def api_chatlog_users(token: str = "", group_id: int = -1):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    g, _ = _log_scope(group_id, -1)
    return JSONResponse(DB.log_users(g=g))


@app.post("/admin/api/chatlog/clear")
async def api_chatlog_clear(token: str = "", group_id: int = -1, user_id: int = -1):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    g, u = _log_scope(group_id, user_id)
    if g is None and u is None:
        return JSONResponse({"error": "拒绝清空全部：至少指定群或用户"}, status_code=400)
    n = DB.log_clear(g=g, uid=u)
    log("block", "聊天记录", f"控制台清空 群={g} 用户={u} 共 {n} 条")
    return JSONResponse({"ok": True, "deleted": n})


@app.get("/admin/api/blacklist")
async def api_bl(token: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    return JSONResponse(DB.bl_list_all())


@app.post("/admin/api/blacklist/remove")
async def api_bl_rm(token: str = "", user_id: int = 0, group_id: int = 0):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    DB.bl_remove(user_id, group_id); return JSONResponse({"ok": True})


@app.post("/admin/api/blacklist/add")
async def api_bl_add(token: str = "", user_id: int = 0, group_id: int = 0):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    if group_id < 0:
        group_id = 0     # 负数 = 幽灵群，那条黑名单不会生效
    DB.bl_add(user_id, group_id, "", "控制台手动拉黑"); return JSONResponse({"ok": True})


@app.get("/admin/api/pending")
async def api_pending(token: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    _prune_pending_verify()
    return JSONResponse([{"user_id": (key[1] if isinstance(key, tuple) else key),
                          "group_id": i["group_id"],
                          "nickname": i.get("nickname", ""),
                          "deadline": i["deadline"]}
                         for key, i in list(pending_verify.items())])


@app.get("/admin/api/config")
async def api_cfg(token: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    safe = dict(CFG)
    for k in ("deepseek_api_key", "vision_api_key", "ollama_api_key", "admin_token", "voice_api_key"):
        if safe.get(k):
            safe[k] = ""
            safe[k + "_set"] = True
    return JSONResponse(safe)


def reload_config():
    global CFG, SUPERUSERS, ADMIN_TOKEN, MIN_QQ_LEVEL
    global MAX_CTX, CHECK_EVERY, MIN_IMPORTANCE, INJECT_COUNT
    data = load_config_file()
    for k, v in DEFAULT_CONFIG.items():
        data.setdefault(k, v)
    CFG = data
    normalize_config()
    SUPERUSERS = set(str(x).strip() for x in cfg_list("superusers"))
    ADMIN_TOKEN = cfg_str("admin_token", "change-me-please")
    MIN_QQ_LEVEL = cfg_int("min_qq_level", 25)
    MAX_CTX = max(2, cfg_int("ai_context_size", 4) * 2)
    CHECK_EVERY = cfg_int("memory_check_every", 4)
    MIN_IMPORTANCE = cfg_int("memory_min_importance", 3)
    INJECT_COUNT = cfg_int("memory_inject_count", 1)
    AI.reload()
    log("success", "配置", f"已热重载（AI 后端：{AI.backend} · {AI.model}）")


@app.post("/admin/api/config")
async def api_cfg_set(token: str = "", request: Request = None):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid body"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid body"}, status_code=400)

    secret_keys = {"deepseek_api_key", "vision_api_key", "ollama_api_key", "admin_token", "voice_api_key"}
    changed = []
    for k, v in body.items():
        if k not in EDITABLE_KEYS and k not in _PLUGIN_KEYS:
            continue
        if k in secret_keys and isinstance(v, str) and not v.strip():
            continue
        if CFG.get(k) != v:
            CFG[k] = v
            changed.append(k)
    normalize_config()
    ok, err = save_config()
    if not ok:
        return JSONResponse({"error": f"写入配置失败: {err}"}, status_code=500)
    reload_config()
    if not changed:
        return JSONResponse({"ok": True, "changed": 0})
    log("success", "配置", f"更新了 {len(changed)} 项：{', '.join(changed[:8])}"
                          + (" 等" if len(changed) > 8 else ""))
    return JSONResponse({"ok": True, "changed": len(changed)})


@app.get("/admin/api/version")
async def api_version(token: str = "", force: int = 0):
    """当前版本 + 远程最新版本。force=1 时绕过缓存重新查。"""
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    info = await fetch_latest_release(force=bool(force))
    latest = (info or {}).get("latest") or ""
    # 只有本地版本读得到、远程版本也拿到了，才谈得上"要不要更新"
    has = bool(latest and APP_VERSION and
               _ver_tuple(latest) > _ver_tuple(APP_VERSION))
    return JSONResponse({
        "current": APP_VERSION,
        "latest": latest,
        "has_update": has,
        "name": (info or {}).get("name") or "",
        "url": (info or {}).get("url")
               or f"https://github.com/{UPDATE_REPO}/releases/latest",
        "notes": (info or {}).get("notes") or "",
        "published": (info or {}).get("published") or "",
        "assets": (info or {}).get("assets") or [],
        "ok": info is not None,
        "error": _VER_CACHE.get("error") or "",
        # 下载源列表交给前端渲染下拉（顺序就是推荐顺序）
        "sources": [{"k": k, "label": lb} for k, lb, _p in UPDATE_SOURCES],
        # 验签能力（cryptography 有没有被打进 exe）和公钥指纹。
        # 暴露出来是为了让 _build_guard.py 能在发布前就发现"打不进去"。
        "sig_ready": _sig_ready(),
        "sig_key": RELEASE_KEY_FP,
    })


# ==== 更新包下载 ====
# 一个下载状态就够（同一时间只会下一份）
_UPDATE_DL = {
    "busy": False, "name": "", "src": "", "got": 0, "total": 0,
    "saved": "", "error": "", "done": False, "verify": "", "speed": 0.0,
    "cancelled": False, "task": None, "sha": "", "sha_src": "",
}
def _update_snapshot():
    r"""_UPDATE_DL 的可序列化快照。

    task 是个 asyncio.Task，JSONResponse 序列化不了它 —— 直接 dict()
    带出去会在下载中抛 TypeError 返回 500（进度条永远 0%），busy 时也会
    把 409 变成 500。抽出来是为了以后往 _UPDATE_DL 加字段时不会漏改一处。
    """
    st = dict(_UPDATE_DL)
    st.pop("task", None)
    return st


_UPDATE_SUMS = {"ts": 0.0, "map": None, "tag": "", "state": "none"}
# 线路测速结果缓存：免得用户反复开弹窗就把未登录配额（60 次/小时）烧光
_PING_CACHE = {"ts": 0.0, "tag": "", "res": {}}
_PING_TTL = 120


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


async def _fetch_asset_bytes(assets, aname):
    r"""按名字从 Release 资产里取一个文件的字节。只认本仓库 api.github.com。"""
    hit = next((a for a in (assets or []) if a.get("name") == aname), None)
    url = (hit or {}).get("api_url")
    if not url or not url.startswith(
            f"https://api.github.com/repos/{UPDATE_REPO}/"):
        if url:
            log("warn", "更新", f"{aname} 的地址不像本仓库的，拒绝：{url[:80]}")
        return None
    try:
        cli = get_http()
        h = {"Accept": "application/octet-stream", "User-Agent": _ua()}
        if UPDATE_TOKEN:
            h["Authorization"] = "Bearer " + UPDATE_TOKEN
        r = await cli.get(url, headers=h, timeout=60.0, follow_redirects=True)
        if r.status_code != 200:
            raise RuntimeError("HTTP %s" % r.status_code)
        return r.content
    except Exception as e:
        log("warn", "更新", f"取 {aname} 失败：{type(e).__name__}: {e}")
        return None


async def _update_expected_sha(name, tag):
    r"""取某个文件的期望哈希，并返回它是否被**有效签名**保护。

    返回 (sha 或 None, 状态)，状态是：
        "signed"   签名验过了 —— 这个哈希可以信
        "unsigned" 没有签名文件（老版本）—— 只能算"GitHub 说的"
        "bad"      有签名但验不过 —— 发布很可能被篡改了
        "none"     什么都没取到

    为什么要签名：哈希和安装包是同一个账号发的。账号被拿走的人可以
    两个一起换，哈希照样对得上。签名用的是**不在 GitHub 上的私钥**，
    他签不出来 —— 这才真正堵住"所有用户被投毒"。
    """
    now = time.time()
    if (_UPDATE_SUMS["map"] is not None and _UPDATE_SUMS["tag"] == tag
            and now - _UPDATE_SUMS["ts"] < 3600):
        return _UPDATE_SUMS["map"].get(name), _UPDATE_SUMS["state"]
    key = "SHA256SUMS.txt"
    info = await fetch_latest_release()
    assets = ((info or {}).get("assets") or []
              if (info and info.get("tag") == tag) else [])
    raw = await _fetch_asset_bytes(assets, key)
    if raw is None:
        _UPDATE_SUMS.update({"map": {}, "tag": tag, "ts": now, "state": "none"})
        return None, "none"

    # ---- 验签 ----
    sig_raw = await _fetch_asset_bytes(assets, key + ".sig")
    state = "unsigned"
    if sig_raw is not None:
        sig = sig_raw.strip()
        try:
            sig = bytes.fromhex(sig.decode("ascii", "ignore").strip())
        except Exception:
            sig = b""
        if len(sig) == 64 and _verify_release_sig(raw, sig):
            state = "signed"
            log("info", "更新",
                f"发布签名验证通过（密钥指纹 {RELEASE_KEY_FP}）")
        else:
            state = "bad"
            log("error", "更新",
                f"⚠ 发布签名验证失败！SHA256SUMS.txt 可能被篡改"
                f"（密钥指纹 {RELEASE_KEY_FP}）")

    m = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            # 格式： <sha256>  <文件名>   （sha256sum 的输出）
            m[parts[-1].lstrip("*")] = parts[0].lower()
    _UPDATE_SUMS.update({"map": m, "tag": tag, "ts": now, "state": state})
    if state == "signed":
        log("info", "更新", f"取到 {len(m)} 个文件的校验和（已签名）")
    return m.get(name), state


def _update_pick_source(d, src):
    r"""给定 release info 和源 id，返回要请求的 URL 和源信息。

    直连走 api.github.com 的 assets 接口（实测国内可达）；
    加速节点走 <前缀> + browser_download_url。
    """
    label, prefix = UPDATE_SRC_MAP.get(src, UPDATE_SRC_MAP["direct"])
    if src == "direct" or not prefix:
        return "direct", label, ""
    dl = ""
    for a in (d.get("assets") or []):
        if a.get("name") == d.get("_want"):
            dl = a.get("url") or ""
    if not dl:
        # 兜底：用 tag + 文件名拼
        dl = (f"https://github.com/{UPDATE_REPO}/releases/download/"
              f"{d.get('tag')}/{d.get('_want')}")
    return src, label, prefix + dl


async def _update_one_attempt(url, part, st):
    r"""单次下载尝试。失败抛异常，成功返回 None。

    单独拆出来是因为要重试 —— 公益反代间歇性抽风（同址时而 206 时而
    ConnectError），一次失败就放弃的话用户看到的就是"点了没反应"。
    """
    cli = get_http()
    h = {"Accept": "application/octet-stream",
         "User-Agent": _ua()}
    if UPDATE_TOKEN:
        h["Authorization"] = "Bearer " + UPDATE_TOKEN
    # 自己计时。之前用的是整轮下载（含前面失败的尝试）的开始时间，
    # 而 st["got"] 每次重试都归零 —— 于是重试成功时速度被明显低估
    # （用户看到 1.2 MB/s，实际可能是 5 MB/s）。
    t0 = time.time()
    async with cli.stream("GET", url, headers=h,
                          timeout=httpx.Timeout(900.0, connect=25.0),
                          follow_redirects=True) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        # 这两行原先缩进错了，落在上面那个块里 —— 而那块第一行就 raise，
        # 所以 st["total"] 永远不从响应头更新，只能用 Release API 的 size
        # 初始化。资产换过 / CDN 不同步时分母就是错的。
        # 只在拿到值时才更新：反代丢了 Content-Length 时若覆盖成 0，
        # 前端百分比就没分母了。
        _cl = int(r.headers.get("Content-Length") or 0)
        if _cl:
            st["total"] = _cl
        with open(part, "wb") as f:
            async for chunk in r.aiter_bytes(1 << 16):
                f.write(chunk)
                st["got"] += len(chunk)
                dt = time.time() - t0
                if dt > 0.5:
                    st["speed"] = st["got"] / dt


async def _update_fetch_task(name, src, url, expect_sha, fallback_url="",
                             sums_state="none"):
    r"""后台把包下到 data/updates/，全程更新 _UPDATE_DL 供前端轮询。

    带重试和回退：加速节点是公益反代，实测会间歇性连不上。
    每个源试 3 次，全挂了就自动回退官方直连 —— 不让用户对着"没反应"发愣。
    """
    st = _UPDATE_DL
    os.makedirs(UPDATE_DIR, exist_ok=True)
    dst = os.path.join(UPDATE_DIR, name)
    part = dst + ".part"
    plan = [(st["src"], url)]
    if fallback_url and fallback_url != url:
        plan.append(("直连（加速节点失败后自动回退）", fallback_url))
    try:
        t0 = time.time()
        last = None
        for label, u in plan:
            # 进循环就更新 —— 以前只在成功时更新，全失败的话前端显示的
            # 还是 api_update_fetch 写进去的初始值，跟实际试过的源对不上
            #（日志里是准的，只有前端不准）。
            st["src"] = label
            for attempt in range(1, 4):
                st["got"] = 0
                st["speed"] = 0.0
                log("info", "更新",
                    f"下载 {name}：源={label}，第 {attempt}/3 次")
                try:
                    await _update_one_attempt(u, part, st)
                    last = None
                    break
                except Exception as e:
                    last = e
                    log("warn", "更新",
                        f"{label} 第 {attempt} 次失败：{type(e).__name__}: {e}")
                    try:
                        if os.path.exists(part):
                            os.remove(part)
                    except Exception:
                        pass
                    if attempt < 3:
                        await asyncio.sleep(1.5 * attempt)
            if last is None:
                break
        if last is not None:
            st["error"] = (f"{type(last).__name__}: {last}"
                           "（加速节点和直连都试过了）")
            log("error", "更新",
                f"下载 {name} 彻底失败：{type(last).__name__}: {last}")
            return
        if not os.path.exists(part) or os.path.getsize(part) == 0:
            st["error"] = "下载到 0 字节"
            return
        # ---- 校验 ----
        got_sha = _sha256_file(part)
        if sums_state == "bad":
            # 有签名但验不过 —— 这是"发布被篡改"的强信号，什么都不许装
            os.remove(part)
            st["error"] = ("发布签名验证失败：这个版本的校验文件签名不对，"
                           "可能已被篡改。已拒绝安装，请联系作者。")
            return
        if expect_sha:
            if got_sha.lower() != expect_sha.lower():
                os.remove(part)
                st["error"] = ("校验失败：下载到的文件跟官方发布的不一致"
                               "（可能被篡改），文件已删除")
                log("error", "更新",
                    f"{name} SHA-256 不匹配！期望 {expect_sha[:16]}… "
                    f"实得 {got_sha[:16]}…")
                return
            if sums_state == "signed":
                st["verify"] = "SHA-256 校验通过（Ed25519 签名已验）"
                st["sha_src"] = (f"发布签名（密钥指纹 {RELEASE_KEY_FP}），"
                                 f"签名文件取自 api.github.com")
            else:
                st["verify"] = "SHA-256 校验通过（未签名）"
                st["sha_src"] = ("GitHub API（api.github.com）—— 这个版本没有"
                                 "签名文件，无法排除发布账号被冒用")
            st["sha"] = got_sha.lower()
        elif src != "direct":
            # 走镜像又没有校验文件 -> 不能用（fail closed）
            os.remove(part)
            st["error"] = "这个版本没有发布校验文件，加速节点不可用，请改用直连"
            return
        else:
            st["verify"] = f"SHA-256 {got_sha[:16]}…（直连，未比对）"
            st["sha"] = got_sha.lower()
            st["sha_src"] = "本地计算（直连没比对）"
        os.replace(part, dst)
        st["saved"] = dst
        st["done"] = True
        log("success", "更新",
            f"{name} 已保存到 {dst}"
            f"（{st['got']/1024/1024:.1f} MB，{time.time()-t0:.0f}s）")
    except asyncio.CancelledError:
        # CancelledError 是 BaseException，不走 except Exception —— 得单独接
        st["error"] = "已取消"
        st["cancelled"] = True
        log("info", "更新", f"{name} 下载已取消")
        try:
            if os.path.exists(part):
                os.remove(part)
        except Exception:
            pass
        raise
    except Exception as e:
        st["error"] = f"{type(e).__name__}: {e}"
        # 带上堆栈：httpx 的连接错误经常是空消息（`ConnectError: `），
        # 只记 type+str 等于什么都没记
        log("error", "更新",
            f"下载 {name} 失败：{type(e).__name__}: {e}\n"
            + traceback.format_exc())
        try:
            if os.path.exists(part):
                os.remove(part)
        except Exception:
            pass
    finally:
        st["busy"] = False
        st["task"] = None


@app.post("/admin/api/update/fetch")
async def api_update_fetch(token: str = "", name: str = "", src: str = "direct"):
    r"""开始把更新包下到 data/updates/。立刻返回，进度靠 progress 接口轮询。

    为什么不在浏览器里下：一是要停在"这个程序里"，二是页面里的
    blob 下载在 pywebview 窗口里不一定可用。后端落盘最稳。
    """
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    if _UPDATE_DL["busy"]:
        # 跟 api_update_progress 同一个坑：busy 为真时 task 必然是个
        # asyncio.Task，dict() 浅拷贝带进去 -> JSONResponse 序列化抛
        # TypeError -> 500。用户重复点「下载」本该看到「已有下载在进行」，
        # 实际看到的是 500。
        return JSONResponse({"error": "已经有一个下载在进行",
                             "state": _update_snapshot()}, status_code=409)
    want = str(name or "").strip()
    info = await fetch_latest_release()
    if not info:
        return JSONResponse({"error": "拿不到 Release 信息"
                             + (f"（{_VER_CACHE.get('error') or ''}）")},
                            status_code=502)
    hit = next((a for a in (info.get("assets") or [])
                if a.get("name") == want), None)
    if hit is None:
        return JSONResponse({"error": f"这次 Release 里没有 {want}"}, status_code=404)
    if src not in UPDATE_SRC_MAP:
        src = "direct"
    info["_want"] = want
    src, label, url = _update_pick_source(info, src)

    # 取校验和 + 签名状态。**直连也要取** —— 签名有效时直连下完同样比对，
    # 否则"账号被拿走 -> 投毒直连路径"照样得手。
    expect, sums_state = await _update_expected_sha(want, info.get("tag") or "")
    if sums_state == "bad":
        # 有签名但验不过：这是发布被篡改的强信号，任何来源都不许装
        log("error", "更新", f"{want} 的发布签名验证失败，拒绝下载")
        return JSONResponse(
            {"error": "发布签名验证失败：这个版本的校验文件签名不对，"
                      "可能已被篡改。为安全起见拒绝了下载，请联系作者。"},
            status_code=400)
    if src != "direct" and not expect:
        # 走镜像必须拿到校验和，拿不到就不许用（fail closed）
        return JSONResponse(
            {"error": "这个版本没有可用的校验和"
                      "（缺 SHA256SUMS.txt，或里面没列这个文件）。"
                      "用加速节点无法验证文件是否被篡改，请改用直连"},
            status_code=400)
    if src == "direct":
        url = (hit or {}).get("api_url") or ""
        if not url.startswith(f"https://api.github.com/repos/{UPDATE_REPO}/"):
            return JSONResponse({"error": "没有可用的下载地址"}, status_code=400)

    # 回退地址：官方直连（镜像挂了也能下完）
    # 用 hit（上面已经查过的那条），别再引用只在某个分支里才存在的变量 ——
    # 之前这里写的是只在该 if 里定义的 hit2，走镜像时直接 NameError -> HTTP 500
    fb = (hit or {}).get("api_url") or ""
    if not fb.startswith(f"https://api.github.com/repos/{UPDATE_REPO}/"):
        fb = ""
    _UPDATE_DL.update({"busy": True, "name": want, "src": label, "got": 0,
                       "total": int(hit.get("size") or 0), "saved": "",
                       "error": "", "done": False, "verify": "", "speed": 0.0,
                       "cancelled": False, "task": None, "sha": "",
                       "sha_src": ""})
    _UPDATE_DL["task"] = asyncio.create_task(
        _update_fetch_task(want, src, url, expect, fb, _UPDATE_SUMS["state"]))
    return JSONResponse({"ok": True, "src": label, "name": want})


@app.get("/admin/api/update/ping")
async def api_update_ping(token: str = "", src: str = "direct"):
    r"""测一条线路的延迟 —— 就为了用户在卡片上看到"0.3s"还是">1s"。

    只取前 64KB（Range），不下整个包。直连要打一次 api.github.com，
    所以结果缓存 2 分钟，免得反复开弹窗把配额烧光。
    """
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    if src not in UPDATE_SRC_MAP:
        src = "direct"
    info = await fetch_latest_release()
    tag = (info or {}).get("tag") or ""
    # 三段 tag（v1.1）会让版本比较算错：_ver_tuple 补零后 "1.1" -> (1,1,0,0)，
    # 比 "1.0.2.1" -> (1,0,2,1) 大，看着像有新版，但语义上根本不是一回事。
    # 靠"发布时永远打四段"这个约定规避 —— 这里加一句警告，真出现时能看见。
    try:
        _segs = [x for x in re.sub(r"^[vV]", "", tag).split(".") if x != ""]
        # 只对同一个 tag 警告一次：弹窗会 ping 三路，每路都调这个函数，
        # 不记标记的话用户每开一次弹窗就刷三条日志。
        if tag and len(_segs) < 4 and _PING_CACHE.get("tag_warned") != tag:
            _PING_CACHE["tag_warned"] = tag
            log("warn", "更新",
                f"Release tag {tag!r} 不是四段，版本比较可能不准（约定见 README）")
    except Exception:
        pass
    now = time.time()
    if (_PING_CACHE["tag"] == tag and _PING_CACHE["res"]
            and now - _PING_CACHE["ts"] < _PING_TTL
            and src in _PING_CACHE["res"]):
        return JSONResponse(_PING_CACHE["res"][src])
    if not info or not (info.get("assets") or []):
        return JSONResponse({"ok": False, "error": "拿不到 Release 信息"})
    # 用最小的那个附件来测（少下点）
    a = min(info["assets"], key=lambda x: int(x.get("size") or 0))
    info["_want"] = a["name"]
    _s, _label, url = _update_pick_source(info, src)
    if src == "direct" or not url:
        url = a.get("api_url") or ""
    if not url:
        return JSONResponse({"ok": False, "error": "没有可用地址"})
    h = {"Accept": "application/octet-stream",
         "Range": "bytes=0-65535",
         "User-Agent": _ua()}
    if UPDATE_TOKEN:
        h["Authorization"] = "Bearer " + UPDATE_TOKEN
    t0 = time.time()
    try:
        cli = get_http()
        r = await cli.get(url, headers=h, timeout=15.0, follow_redirects=True)
        ms = int((time.time() - t0) * 1000)
        if r.status_code not in (200, 206):
            out = {"ok": False, "ms": ms, "error": f"HTTP {r.status_code}"}
        else:
            out = {"ok": True, "ms": ms, "bytes": len(r.content)}
    except Exception as e:
        out = {"ok": False, "ms": int((time.time() - t0) * 1000),
               "error": f"{type(e).__name__}: {e}"}
    if _PING_CACHE["tag"] != tag:
        _PING_CACHE.update({"ts": now, "tag": tag, "res": {}})
    _PING_CACHE["res"][src] = out
    return JSONResponse(out)


@app.post("/admin/api/update/cancel")
async def api_update_cancel(token: str = ""):
    """取消正在进行的下载。"""
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    t = _UPDATE_DL.get("task")
    if t is not None and not t.done():
        _UPDATE_DL["cancelled"] = True
        t.cancel()
        log("info", "更新", "用户取消了下载")
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "没有正在进行的下载"})


@app.get("/admin/api/update/progress")
async def api_update_progress(token: str = ""):
    """下载进度。前端每 300ms 拉一次，画进度条。"""
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    st = _update_snapshot()
    st["pct"] = (round(st["got"] * 100.0 / st["total"], 2)
                 if st["total"] else 0)
    st["dir"] = UPDATE_DIR
    return JSONResponse(st)


@app.post("/admin/api/update/reveal")
async def api_update_reveal(token: str = ""):
    """在资源管理器里定位到刚下好的文件。"""
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    path = _UPDATE_DL.get("saved") or UPDATE_DIR
    try:
        if os.path.exists(path):
            # /select 会打开文件夹并选中该文件
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            os.makedirs(UPDATE_DIR, exist_ok=True)
            os.startfile(UPDATE_DIR)
        return JSONResponse({"ok": True, "path": path})
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.get("/admin/api/update/download")
async def api_update_download(token: str = "", name: str = "", src: str = "direct"):
    r"""把更新包从 GitHub 取回来转给浏览器 —— 就地下载，不用跳转。

    为什么不让浏览器直接点 browser_download_url（真实问题）：
        那个地址第一跳是 github.com，实测国内直连不通（TLS 握手过不去）。
        用户点「下载」只会一直转圈，什么也拿不到。
        而 api.github.com 和 release-assets.githubusercontent.com 是通的，
        所以这里由服务端经 assets 接口取回文件，再转给浏览器 ——
        用户实际是从 127.0.0.1 拿，全程不碰那个不通的域名。

    安全：只接受**这次 Release 里真实存在的文件名**，绝不接受任意 URL。
          否则这就是个"让服务器去请求任意地址"的洞（SSRF）。
    """
    if not _auth(token):
        return JSONResponse({"error": "invalid"}, status_code=401)
    want = str(name or "").strip()
    if not want:
        return JSONResponse({"error": "缺少 name"}, status_code=400)
    info = await fetch_latest_release()
    assets = (info or {}).get("assets") or []
    hit = next((a for a in assets if a.get("name") == want), None)
    if hit is None:
        return JSONResponse(
            {"error": f"这次 Release 里没有 {want}"}, status_code=404)
    api_url = str(hit.get("api_url") or "")
    # 地址必须是 api.github.com 下面这个仓库的。
    # 注意：token 在函数开头已经校验过了，这里不用再检一遍。
    if not api_url.startswith(f"https://api.github.com/repos/{UPDATE_REPO}/"):
        return JSONResponse({"error": "这个文件没有可用的下载地址"},
                            status_code=400)
    log("info", "更新", f"开始取 {want}（{hit.get('size', 0)/1024/1024:.1f} MB）")
    t0 = time.time()
    try:
        cli = get_http()
        _h2 = {"Accept": "application/octet-stream",
               "User-Agent": _ua()}
        if UPDATE_TOKEN:
            _h2["Authorization"] = "Bearer " + UPDATE_TOKEN
        r = await cli.get(api_url, headers=_h2,
                          timeout=900.0, follow_redirects=True)
    except Exception as e:
        log("error", "更新", f"取 {want} 失败：{type(e).__name__}: {e}")
        return JSONResponse(
            {"error": f"下载失败：{type(e).__name__}: {e}"}, status_code=502)
    if r.status_code != 200:
        log("error", "更新", f"取 {want} 失败：HTTP {r.status_code}")
        return JSONResponse(
            {"error": f"下载失败：GitHub 返回 HTTP {r.status_code}"},
            status_code=502)
    data = r.content
    if not data:
        return JSONResponse({"error": "下载到 0 字节"}, status_code=502)
    log("success", "更新",
        f"{want} 已转给浏览器（{len(data)/1024/1024:.1f} MB，"
        f"耗时 {time.time()-t0:.0f}s）")
    # 文件名都是 ASCII，不用管 RFC 5987 那套编码
    return Response(
        content=data, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{want}"',
                 "Content-Length": str(len(data))})


@app.get("/admin/api/ai/models")
async def api_ai_models(token: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    models = []
    headers = {"Authorization": f"Bearer {AI.api_key}"}
    try:
        async with httpx.AsyncClient(timeout=8.0, mounts=_http_mounts()) as client:
            r = await client.get(AI.models_endpoint(), headers=headers)
            r.raise_for_status()
            data = r.json()
            for m in data.get("data", []):
                name = (m.get("id") or "").strip()
                if name:
                    models.append(name)
    except Exception as e:
        return JSONResponse({"models": [], "backend": AI.backend,
                             "error": f"{type(e).__name__}: {e}"})
    return JSONResponse({"models": models, "backend": AI.backend})


@app.post("/admin/api/ai/test")
async def api_ai_test(token: str = "", text: str = ""):
    if not _auth(token): return JSONResponse({"error": "invalid"}, status_code=401)
    if not text: return JSONResponse({"error": "empty"})
    try:
        reply = await asyncio.wait_for(
            AI.chat(text, speaker={"name": "管理员", "qq": "console"}, mode="private"),
            timeout=AI_HARD_TIMEOUT
        )
    except asyncio.TimeoutError:
        return JSONResponse({"reply": "（超时）"})
    return JSONResponse({"reply": reply or "(无回复)"})


ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nekolyra 控制台</title>
<link rel="icon" type="image/png" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAYAAABccqhmAABp1UlEQVR4nO1dB3gc1bX+Z2b7rnZVbMu922AMBtM7NjXEtIRAgJDAgwAhEEhCD4QSIPSWUB6QQhrw7ITQCSXY9G4bg3tvuKivtpeZ9/1nZuS1Lcm2irWS5udbLG3TzPnPuffcc889R0ERwzAMdQagVk2bZpx++un5wtfWGkYg1pgeZmgYizzGQdFHKlAG6wYGGHmjvwG4FAV9uu7qHfQUGAaqFSCnaMp6VcE6A8YaGOoyaJiv5LEoVOJdOUhREoWfmTp1qtb3tNOUSYCuKIqOIoWCIoNhGMqMGTO0SZMm5RVFMezn5zU0VHg8gT31vHGIbhj7G4YxHoY+wOP1+VwuRe5EzwO6biCfy4Ef5L8OHLQXmsslhsJ/VVWBqlFRgVzOQCadSkFR1ymKMldVlE9VTfkgk0l8uVskUrMtnS4GKMU02/N6FEVpmumXplJjjbw62YBxYj6vH+Tz+8o9LiBvAOm0jlw2SyM37BHW4Oetf2F9WVfek4OeAcMwxGgVcQbMf63nVc3lUlxuN7xeFZoCZHJAKpmq1TT1IwXKS4qmTx/l8y0q+C4ZPorFK+hyA5lqGNppBQJZFDX6GmrmFEXB6YauHx4M+Ty6DiSTWRo838OHIv8ZYuNdfg8OevngoMCQ/8yBQXW53arf74aqAvFYKqOo6ruGgamK7nl+bFipsj6nTgOU0wsmvK6A0pWGPxcwbrEMf348s49m6BfoUL4bDHr65vNAIp6Cns/nzXkdqmPsDrqRx6DTX1A1TQsEfdA0IB7PVKkwnssr6pPjgp4v+N6bDEMd34UDwU4fALYc+RY1Jo+Bpv5czxvHh0JeJRbPIpfJcK3El1XO8Tv7Gh046DBYgwH/cXk8WijoRiyWNlRNeQ15/cGxJf43m/OEdxZ2mnHZgZDJkydLZG5hY/JoaNrVbpfrGM2loLEhwYvJGYDmzPQOeqpnoJghLFdJJIB8zkA2l3sT+fzdu5T43+J7pk+f7tqZwcKdMgBwdLNn/Lk16fEuL25xudyn0vBj0QRHPI58DI44cNArYBgG7UEJhQMqB4JcLvuvXBo3ja/wzt3SZrrxAGAo0w1okxUl9+GHq/x99qj8laIqv/T63YFovWP4DhwY1kAQLg2o6WQ2YejG/dVfbfjtwQcPTU43DNdkBXmg87wBpZO39TizG/Pqk8e4Pa57A37XhIYGM7CnqLKb6sCBAwCGrucZMIxEfEgkc3OymdyVu5X63zRXDbLb1SmxARpph4PrGF4w1/zzG5J3eTzuN1TNNaGuNpHTdd1wjN+Bg81Bm6Bt0EZoK7QZ2g5tiLZEm0J38ABMt0XJfbEhOToUVJ8KBT2H1NUndUPXeZOdMuA4cNCTYOi6rqgqykr9aiye+SAW18/dp9K/xLatohwALFeFe/X5r2rjJ3p97qfcHnd5rCGeU1TFVQQ5Rw4cdCMYMHQjF4oEXdlMtjadyp67R3nwJSuTkOcLOiQu0CFWWbhOmRtNXO3zeu/KZnLIcj/fWes7cNCu2IDb49HcHhdS6fQ148OBuwvja+jqAcC6GFq/Prch8XAk7L+kvj4hiQ+KojguvwMH7YRhGJzxUVoaUBuiyUfGRwKXFtpdlw0AnPntUWhuQ/KZsrDvjNr6RNYwDAYBHZ/fgYOOTCJSlFx5acBdF009Oz7iP3NLG2wL2mykthvy5fr1AV+o9JlgyHdiQ10iCwXutn6nAwcOtgED2UhZwB2PpV5KxerP3LN//0R7tgnVdqz5+VcNtz/yf5GQ78T6urhj/A4cdDYUuGlrtDnanj372za541/Xjmj/1w3xZ8LhwBm8IEVRnJnfgYOdBMMwsqVlQXc0mnh290jwzLbuDrTFA2BiQv6rusTDZeHAGQ2O8TtwsNPBCZe2RxukLVqFdHY4u3aHBgAruJebVRW9uqzUfwkDfnBmfgcOugaK4qYN0hZpk7RN2ugOfcX2vpEuBkeZWRsbpoQjoZdTyXQur+uaYlXrcODAwc6HAcPQVDXv83td0YbYCRP7RV6xbXV7Pq9sb8SfUcZZdcnhfrc6CwbCmQwnf2ef34GDYsgT8HjctOZoMqtPnFjmX2HbbLuXAHaW3+eG4XYZ+Wc9Xk9pJp3h5qOT5OPAQRGAtkibpG3SRmmrfHp7dga2acQzrKCfqyZ2W1lZ8IDGhljOSe914KC4QJukbdJGaau0WdruNj+3Xev+qsZJwZDv7XQqm7e2G5x1vwMHxQd65nmvz63FY6kjJ/YtmbGteECLHgDdh2kAlhuGz+VSH6OjoefzUru/0y7fgQMH7YFlo4pCm6Xt0oZbWwq0tgRQWZOsoTp2faQ0sGsynqLr76z7HTgoYtBGaau0WdquVVewRbtVWs3zj6bHeBTMYUXjXC7n1OV34KCbHBxyuVy6oiCXMTBhz7B3cUvnBVoaGcwTRtnc3f6g15vNypaf4/o7cNANQFulzdJ2acNWenCz9rvVk3bQYE5t4gh/wDMjmUi3KcXQgQMHXY68P+DVkonMpAnlgXeaCwg25wGYp4t0/WZV06zGJg4cOOhuoO2KDev6zfZTrXoAdjOCWXXJyUG/++1EPMXTRU7gz4GDbpwlGAj61Hgye+TEMv/0LRuObGbc7E3GfxU9d63mki7GzvTvwEG3hmHQlmnThTa+1QAwdepUqT8+sza2p8frOSoWTeqK4jTvcOCgO4M2TFumTdO2aeO09abX7R/sAMHs6sbHS8tDFzbUxnOQct69F4X+j6aYQ6fjEhU35OAKo18FRPX6/SvdyEXKg6762tgTe/UpuagwGKgUFhb8elV9eS7oXuJyucpy2aw8iV4GMwJqKo1LVaAqgK4D8ZwOlwq4FMUZBIoUVNacYSCnA0GXCqat6QaQ03lo1nyD0lvzAtxuJZfL1bni2dG7Dy2ttW1eZnjr0EAuF3B9p7Q0UFZfG+t19fztCcNtGX06b2BjMosNiRxqkjlsSGZxQGUII8IeZPIsed7FF+xgM9DAPZqCZfVpzKxKoDLgRoXPhcqAC2VeF7wuRQaDLP/Xy/LZFVYKyWTypeWhsnoj/h0Af7RtXgaASYCVIaT/IJfjgQL0AmeXN2nODHR0PCoVxEB1ModVsQy+iWfQmMnLbKKpVBygJpXFyLDH+nxPl093g2nStamcDN7kb3VjRry2Eo+GgUEPhoY8qPBpLGgpA4EhvSvsz/ZsPnmftG3aOAcA2+YVu3DA7LrkCMXQFwGKyzBkmOzRg6Rp+BDDz+gGVscyWN6QQVUyJ8pBo9cUs94RBcFZv3/AjcmDS8TN7NHC6aYgJ/9d3YiadA5uLtUYtzEYDzCQ103vrq/fhRERD4aEPE3c27rQw2EoCn1bI2co6ti9yvzLafsuaydAN3L6seHygCtaF8vxMBF6KOxxnu4iDX1BXQpLGtKoS+dFgbjup7vIN0rQz/qXgwE9glRel886G6TFBap2ImcgltWhQjGnN4tsxm1cLpPL9Yks1iWyKPOmMDrixciIV/jkQED04HFAMfR8LlwWcjXUJo4F8DjF5po2bZp15/rJDHb1ZBlQAWjgxPJoBvNqk6hN5yXCTyUoNPotwY8l87ooWF/NhSwcL6BYQLro1kczOaR1fatArR3YJegFUMMbMnl8ujEhg/9u5X4MKzGXdgwY9mBvQBEbV/STOQDQ9uVWZ9XVlSp593KX212azUq5rx4lAns9QyNnQG9WdRLr4lkxahkQLMNvDZQIlwH7VwaxS5kX6VyPVpRuBQ7Y9Nrm1qTwxcaE/LwtD81e+ud0Q4KDA4JuTOzjR4XfJTz3xDUwdwPcbo+Sy2brDS07YmJZWb2ZCJRzTQyEAjR+pv72qPumItB95wwxpzqJN1c3Yn08K4OBzBTbY/x2mMiABAkd97/IwK1aAxK/sWM221Ji29NzMQCsKaIT1A3qCHWFOtPTeDZPCWZ02jptns/ZHUYP9XgVkSN64NZQIqdjxppGzK5KNrmB2zR8K0LM93F9yMgyA4MSUOqJ00M3hiT+MJgHQ5Zz5Gqz4F4rlmxYL8vSABAdoa5QZ3pirIc2LrauKIfydyvYZxyQN48H9Bi1Jm90BVc3ZvHJhjiSOR0+ay+4NU4NPS8nqHRVQyaTQ9CjYZDfLS4i95WDblW2C3uMoHoIyOnBA0ISo+FWIJd49AjimTzcHhdUQ4eez6Ol7HbD+pc6wiDhG6uiOKAyiCElbhlQehDfimnrxgHyyyLD8MZq4gt8ft/wVDLZY9b/HNEZ4Z+1MSGzgGbN+i3B0HXJB/CGQkjEYvBnk5g4YgDKNR0lXldTeimNv4dNCj0GVtPKprTtxnQOtXkVs5avQ9LtRyAUQjoWM/f/W6lupyiWR2EAE/sFsGuZrymBqCfEAXx+v5JKplaEKoK7ulKN6RGKggHZTEYMoLuDNHF/d1ZVQoJCdONa8wKl3oFhwBMMIp/NYtkH72LWv/8JTzqOo/70MCLhCiSSaaj0/wtSRrq/pHoWbHolFZgDdV5HJOgDamrw8o3XIuMNYuJ3voch+x4Aze1GJh4XS29O5+24EUPkn29IiPc4sW9AlhXdnXfer2nrGCC2/0VV/CR/wPtCOpXUWVIQPcD4mQo6rya1zWgwZ32X1wtVc2Hlpx9hzgv/woYF8+Byu5FIpbHX/vvgrscfQD5P95HiUZp2Dhg9dlAcoFHSw7Mj+iSdA7amqbjmol9g9qdfIODzIpfNonLX3TDh5FMxbP+DoOdzyKXT2/QGuOOzW4UPe/eQQYCa7/X51WQifbIKGONcLlb+6d6e7Y4Yv+kRGND8ATRuWI+37rkNb951KzYuWgB3IADV40GkNIKZn3yOh+98CKFQQL4v5GGKCWQrsacFh7r1ASDdQLX0qYVwJFyFAsIdOSSX5JTckmNyTc4bN6wXHZAlwTa2GKlT1C3qWHennrZOmxfbVxRjlPksur/bvzGBebXbNv4cs/kCAVTN+hSvPXAPcok4vMEQ8roOn9eDeCIpQcBISRD//tNTGDp8CPY76bv4dNlGSRfeGM/h0v36Y3DEg4yTD9Bl4Gzvc6lYVZ/EY19swMCQG8MjHuw/sh8+m/q0cFdWWYlsLg9dzyMY8CPJgV9VseLjD7Bmzmwc/4ur0Hfi/sgkEnBparNmUDgIsLxmt18OWDdJ21cVwxiqd+ObEXI0M+A3l8bfytaNHIiQPH8FB1YGsOr1F5GMNsIXjkjwqK6uAT8+9yxceen5aKjaAFekAiO/dx7ezvXHA++txH+XR7E2mkEsk8c3sYxzNLgIwNDM6mgGiayONdGMcESuyBm5I4fkkpySW3JMrsl5MtooOkBdsJcQLYXBbD2jjlHXWtOzYgdvUWzeMIa6oKgD8jndPgLfLff5udU3qzph7ttuI5Mv7FYxaVg5MtUbsGjRUvhLQtBzWdkeCvh9mDLpQJSNHI3n1uagjZ0IX3kf6JkM9GwaIe4G8Hv0PFZH0wBKdvIdO9gKBrC2MWOmc7sUuDUXdCOPwMBhCA0fg8iBR2PAoln4/rlno27ZEtz7oE+SXYxcVrinDvjqN+L4UZWYsbIW0WzL+//iaWqK6FrIrWFwyN0tj4bT1mnztH3VgNGXwRA+aVe86S4PjtqNWR2fboyDsX5lG8bfz+/CpIFBVEZ8+OTjz9FQWweXpkFRXUjU12P0qOH4JFeKH7+6GL59j4TbH0C2sQF6hsZuHheW2IECrGvMNm0NdbUceuuDvDJC/w0HADnObW7TCleZtHBHDsklOSW35Jhck3OXpokOUBeoE9QN6khrRm1mGSqic9Q92V4uAlns4EMxbd7oq8JQ3KbQuh941Z9aST50BZu7C0bt7aO8RwwqkTVjMp3HJ+9+CEUz+5xmYw0o3XUCPCdfhD/Nr0UinYORjMOwE0cKtME+UFQVzyKayTcpgIOdC9mqUxU0pPOoTeaEk83UmFt8qiYckktySm7JMbkm56Apa5roAnWCukEdoa5QZ6zkwM3/rrXsoM5R97or92LzhuJmWLtPnorejZIAbNd/fq15qKel9Zhs4Vgz/2EDQ1zzQHV7sG71Wsz7aq4E/HLJOPoe9R0Mu+A6uPsNRkDR4WYVCW4NNbdHbHkeNH4OAnZasYOdC4qcsl8fyyDOI8CtuX+qKpwKt/0GC9fkPJeMiw5QF6gT1A3qCHWFOiMZgErL8QDqHnWwu6UM09Zp87T9brfvTzlztK9J5TG/zkz0aW5L3t4eCns0SRE1q8Do8Pu9mPXZF6jduBFujxsDv38xKk84GwbLpWRT0LF9J8n43QwIqrJwctAVIA/kgFl7LS8ATZBT4TabEq7J+cDvXyw6QF2gTlA3qCPUFeoMdUcCg818n25PQnUp0UXxQND90O0GABtfViVajdrq1gxxyIAQ/C5VlITE8jMfvf0OXG4PBv/oCpTuNwk5cQelter2/XHGAvJ5rIsxo0p1uid1BazElbXRNBQjz9yW7fucxTE5L91vkugAdYE6kbN0hLpCnaHuUIda+mZ7V4m62F3RrQYAcf1VBasaM3Jgw92S62/N0MzcKvdpsp6j7+Dx+bB22XIsmLsAYy66HoHRuyPX2NDiAZEtQTeTCgKXB4HSMiytTaE+loDH7XIGgZ0IBmLdbhcaEmmsjuXhj5QKJ+SmxaXAFiDnucYG0QHqAnWCukEdoa5QZ6g71KGWvAA5RcilQCIrOilJQt3MDehWAwBtj5H3+XU8s22tB5p5D5M0xpSa5Z6YxmmW9jbg9bgw/+sFCE05B5HdJiIXi1qBwNZBxeKfa8zoiGVyyFavR/Sj1/HF4/fiodvvhcttH6rshJt2sDksGVPmv7v9Hsx54h5EP3xdOCE35Mg+FLQtKJomOkBdoE5QN6gj1BWpDJ0zRIeoS5L40+woYE4M1EnqZveJpJlwGd0lccEKvCysT0n9vuYCL/a5cK7dJlQEzJnfOhnGfO9cKoUF/qEI7+bZLuO3i0sw8celGDhh3ABk536Kh+66GSWMn6oq3p6bwuChQ3DBFRejviYKSavuXHH0WphZnHmUVoTx5P2P4e2X/4OSoA/rl86RLbnLb74Z7vH74/UF65AzzNRt8f2MbQ8C4d32wQJfBkelUsKrfMTaQaIusTx8PMvycVuXG+P6nzq5LJrGLqU+CR5ahaaKHt3CA7D3fDkKL21Itzj725VhJvTxbxYc5L9Bj4o3l9ZjQUKDD021z1sEic7lDcQyOg4fEsZDx4zElRMrcN0ZR+P4YyahPq1D8wUQLgni6Sefwhsvvo5IeRjZXK5zhOBAZEsZU9ZPP/EUSkqCcPkCwgU5ITfkiFyRM3JHDsllazCYGYic6AZ1hLpSqDvUJepUi7WyLS+Auml7Ct3B+Anli+qY0V22/VjI8+P1seZnfy4P8gYGhTw4dGBIfuZzJI0BnSV1STz66XqpE7+tw7xUmFg2jz5+Ny6eWInJwyLyfJqJH8wXTydxyunnY+7KdSjp2x+Jjd/A5XLhnj88jNHjxiIei0PbjqWFg+0Ht62CoSCWzF+Eq358qXgCwf6D0bBuLXYfPgDPT/0jFK9fTm563ea8Nn1lAx6btQHVyaxk7rGaU+swpA/ET/fvj9Flftnrl5ncWuu//00Ma2OZZmNPdrLZgf27V/OYbuEB2DP7ckZ8+XszPJIQrvtYvMF23wizRJSO5+bVyvPbWhvS+BvSOezRN4DfHzNCjJ+DCQ/9ULFYMXxRQsGEi67B8EtvxaAfXAa314tkIoHfXnMj6mvr4PF6oFsllh20H5QlZUrZ/vaam5CMm4e3Bv/olxhx6W+EC3JCbsgRuSJn5I4ckktyui1PQLXce+oKdYa6I7BmdOqWvKe5ccRcbYqOdqeuGtzDQjE/GPElsbWprBzDbW6/lbJmAGZQ0I0+Pk1O+zGhg8Ecv0vBjOUNWNWQhq+FnAEbTPCpT+dw7IhS3DN5OCqDbgkE8W8yz3z2hjiufWclLntjKT5L+BDu1x9aSSm0SAX8Xg9WrViFe399OzRVk5FV+qsUgQy784MypCwpU8p21YqVkrxDmav+EEr6DRAuyAm5IUfkipyRO3JILskpuSXHrZ4u1BTRFeoMdcc6NCM6Rd2ijkmwr6X8lGROdFVaSxSB/Lb1KH4PgDM7gNWxrNmRp6X0TEXB2FJv0+DA+zMzxbJ4Z2UUfqnltw3jT+Vw3IhSXH/wYCFTZn2XWVT0/k+/wZXTV+DTb2IIuFX4lLwEFTWvH97KQchl0nLu/MMZ7+LJBx5BKFwiR1AdtA+UIWVJmVK2kbJS5DNp+AYOg+rxIp9OCRfkhNyQI3JFzsgdOSSX5JTckuNtDQJ+tyo6Q90pzPTkP9SxLQOBm+UFGOwylTUNq+gX191gCWAH/5h2KS5ZM5F/7tP28bukaKe9Z8u3UQHeXxVF1Gr+YbS25s/ksU//EK4+YJB0A2bwiDMJj5r+/K3l+PfiWoklBD2aVVjU3CJgxNhdORTZbFZG/EhpKaY99Q+8PO15lJZFkHeCgm0GZUcZUpaUKWXL5QDX/+5+Q6xtGhqj6dmRG3JErsgZuSOH5JKckltyTK5bWg4Y1rKROkPdkdoSBXpGHaOuNZsbIKXEILra4rZhkaGoBwA7774+nZegXEsjL58bFnI3BWzsPPF1sSw++ybe6uwvff90XYj91UGD5XMkl4pDV/DKt1dgaX0K5T5XwWkzE6qqIplMYo9DD8fwEcORSLAAqYJAKIhH7noAsz+fjVA4LAEsBzsGyoyyowwpS8qUsmWsZdCwYRi5935IJ5OQdncWyA05IlfkjNyRQxkEdEO4Jcfkmpwr2/ACqDvrbC+gKc5k6lpLemgHkKmz3eGgmFoERxNbflju/4YE3f/mAyt2lJ9rvay1RJCtG5eCz7+JNc3+LYFKlcoZuGyfAegbMA+A8LMsLnHV9JXSEThk5YQ382Ho2Sxc5f1w2+2/NrcOc3nZEcjn8rjz2puxYd16eH0+qTbU5fLsJg/KijKj7CjDvCVTypbGeMutN8BV1kfO9Dd3BoBckTNyRw7JJTklt+SYXJNzpZUp2vYCqEP8LOmXnSbDjCtQ55qdVGQZYOqsxIGsCalYH0XtAVCYzOWpSeebXVPZa66+PhcCBYRwlOYJsa82tF4khBFduoNHDAnjsKHcwzfXi4mMjls+WI2qZBYBlybJRc2B5Aa8LixbV4Nx++6Fu26+CvUNUXnN5/dh/TfrcPcNt8rSgN6CBIUctIpCWVF2lCFlSdQ1REXGY/eZiFUba+H3uFrklpyRO3JILskpuSXH5JqcxzL5FneF+L3UHepQ4WlDqgJ1jTrXbEzKmrSos92hgUxRDwAUOrvx8sx3s+6/9UT/gFm3n7+bGYOsE5c2gzitHNWUnHJNwdnj+2521vuJLzdgQU1S+sq3tHcslWgVBpl0VKd1vLuoGqeffjJ+eNapqKmtk9mlJFKCmR9/hsfufhDBUEiqEDtoHZQRZUWZUXaUIWVJmf7orFPxvdNOxjsLN6AuY0iUX1q4t/Bd5I4ckktyWlgzgpy7RTdaHtz5OnWIukSdMisOm9xT5wp1sOlzTVvJZifp7T2b0FUo2gHAFiRzu7kn21JxBrqEZdb6vHDvf15VwlwStDb7Z3UcPjiMMeU+iRbT1ftqYwIvLalFqRVQbP6zpmfCveXhER9+uf9A7DsghGxOx29vuhIH7DsRDdFGucDSslK88Mw/8a+/PYNIeQQ5JyjYIigbyoiyoswoO8qQsqRMKVvK+JDBEVy5/0AMj3iFA3LRkqGRQ3JJTsktOSbX5PzwwWGznXgLXoBsLxuG6FJhTgDVgjpnxwaa0w/qLHW3pbhVsaBoBwB7pGXLZ3GltoDZqcdAyK3Kgz8rBV7Dsrp0iwTZf4Cknji6vOn7OOr/Y16VdfyzZePn+pEtpK7Yf6AkmnA2GVbqk+/w+zx4+P5bUFFWinQmIzOMbGPd/wg+fe9jhEu5M9C1QUFeU3OPrgRlQtlQRpQVZcZrogwpS8qUsqWMR5X78IPxfUX25IBckJOWZ1tDOCW3vE37bSeOLrcMuwUvwJpgqEv2bN6S3m11P4apu618fVGgeAcAS9jRjOU2byllaySOeLTNorRc51UncqhKNFMmyv6oDBIGRpT6sFsfpo8CbraXrk7g8/UxIba5yd8+IcYW0vcdOQInjaECKZJ1xu/g2jWbzWP40EF46J6bkU6lm9a0jFbfc8OtWLNiFfxBf6dlCoox67p8PyPpfPDnwuUHA2rNPZq+o4XPd9Ygwe+nTCgbyoiysuMAlCFlSZlStnyesqbMKXtyQC7IiX3yc6vvNyCckltyTK75Hbv18YsOpFqp/CPl3xI50Slbn+yBgbrXbNaf9Tt1V34s4mVA8Q4A1iiaYFYff2lG9/hU2GrWwV/4O0naEM8iIduGzX8vP5HO6di3MijrPLu4539XNJhnCJphzNwHhiSc3HnEMIzkEVHZmjCXKrYC8QxAJpvHkUcciGuv+GlTGWqv14uaqhrcdd0tyKQz8r72GhQ/zUw121ClYKmmwev3I1RSgkhZRB7BkhB8Ab+spfmexmgjGuobELUe/JnPSYMMHowJ+OUz9uf5XfxO+5o3DQzt7yZjXzNlQtlQRpSVXaadMqQsKVP7fIX0erQETg7IBTkhN1Lstpm/Q07JLTkmyLlbU0QHqAuWFm0F6hB1iTrVlIVqeRHUvWbv33qdutuc91pMKOrjwHSveCCjxXPYoEFqZplne4xQgI0xJuW0/t2cKfboF5SfqQiprIGZG+Pwym7C1h+mQjZmcrhsn8EYbhk/FaI5sNosZ6vLLj4HX89biOdffgMV5aUIhUOYO3sOfv/b+3D1b29ELNq4XefWN7ttKz1WWmGLsXvhZi07eifpDBobGlC1fiOq1m/Ahm/Wo2rDBtRsrEassRGN9VFZZ8cbY2ZuglKQwKJpYvT0BEpKw2L0Ff36oG9lJSoH9kff/pXo278fSiIReL0ekW82mxHDZWKOVGVWm++11xoo60AwgLt/9RuRTaS81Oy+VFuP75x0nMiQsqRMm5W1lbFJTi7aqz/u/HgNIl7XVsFb/h1yS47JNTknqAPqgmq0Bn4Vdco+E0Dolu61lPFnVyy2r6NY7WyT31dksMstyakq1nJr5j20P7/dzcVO/zOA6mSuVbdL1nAeDcPCXvmd0eGlddw1yLR40pBk7lrhxzHDI2CbwJaMv+na6KrqOu65/VdYvHQFlixdgWDQj3BZKV7/90sYNmoEzrrgR6ivbYAmbZq2bfS8OZfHA5/PB1VV5NThiiXLsGLxUiyetxDLFi0Wo+ehmVQyhXw+Zw6UsgQxe+VRMPLvloZqGKjeWCV/gyfq7GUA5ahpLtmKKy0vk8Fg5NgxGLPbLhg+ZhQGDR0iKdD0BlKpFHKZjDnfbsdgwHV/aXkETz/5V5EJZcPriMWTGD9urMiOMuT1twZyQU7IzfOLa7CMUfstukPZ23rkeFU0jbEV5tYidYC60OpJQcXSKfst1kBA3WspOE2dpe5yq7ClpWgxoGgHAMLMvGvxCLbMnoXbfHaAhpVhWrJP++BQv6Bb1o1UHNrDioYUUswf11zIbzHcyJIhn8dRw0rh4pJhO86YU/m5Fx0Jh/Dwfb/BKWdcIC2q2H6qJBLGnx56DENGDMMhRx2BaF09tII1eNP9W+tut9sNX4lfrr16QxU+e+9DfPnZTMyf87WsmxOxOHRDN9fybrf8y6OztgGKo24tkVrTRJfbKqdtCcpeCvEaeC01VdXY8M062Z5TFVWy8wYPH4pxE3bHnvvtjd323AN9KvvK30klkpIezWtozoCZ5kuDf++td0QWlAnr+mXzOnw+r8iMsmPUv7Xc/SZZWVu65Gh+zTr4Xc3zmMrlhWsOAHkdogOM6G+0Kzw3893889SpwoCfvU1onyBsqYhNsfeQbTmToqu3AGUE1Vss/Gkf9nFb61p73cX3J7PmsqHZW7O28Eo9mtSB52DAv7U+nm1RFMzh4z7w7n0D1ldsn5vLHnR0X8ePG417b78eF152HcoiYWk7zWq0D9z0WwwaMliMKJlImi3IqTRMU1UU+P0BuD0uceE/efcDqV8/5/OZ2PjNepkZPR43PF4vQtwrl2rGmyL69neYD9OwzVIIrXRPKpC/DBtb7BBwIPJ4uNzgdxjSMXnZwsVY8NU8vPjMP9FvYH9M2HdvHHD4wZiwz0RZQmQzOaSSyaZgqARv8zoCwSBWLFoqMqAs7IEnFovjid/dITKj7La3roLNCTmSPftWIvvkmsgb3M1RRRck5beZsyaG7QFmLV1syu4zRPdcUm16ax0t9GB9rEbVypZ0V6KoPQA74orWSr5vYej0WqWeewvibtrG8ZhtvuwP8whn88Ejq8yYV0NlwG0ONDvApB0UPPmEozHn6wV46LE/oW+fCigeNxqjUdxx3U24+8mH5bw719Ocvbn+5gy58Ou5mPGft8T4v1m9FobB2dEna3UaoXQqoqvOQKnMtJyRuCQy3fhsNodsLiuutqQiW4Ysg8KWsrZmevt1Dl5cmrhd9Cg0WTaYSzFzcLHfy+CgP2B22K2vqcUbL7yMN198FQOHDMIBhx+CSd86GruM3008nEQ8jlw2J/fK5QvvnTLwBwNy3VXVNbj84vNEVplW1v3N8mpxSY7IlRkEbv7oOLm2b1qRjsJmzKClpSafp04VbtyYHmjrumDvTBUzinoAaCvaMtK2Fq2VbR9NkZqEbQGNiXns1199CeYuWIQZ730sngBd6MXzFuCh39yJG+69HX6/H4l4Au+8/hZee+5FfD1zthw28klUP2TO3tba3DZ4aJr8zv1yOyBHwwkGA6ioKENlvz7o17cCfcrL5V96DX0qyuHzmYG8pm3RVAbVNbXIZLLYWFWD6tpa+XfDxmrUc6egobHpu2nAXo/Hcu03DQhcfpR4vU3xBCb0vPrP57H73nvh+O+ehP0PO9iKF+i45/rfyL3zeC9bK9TVN+DoyYeKjCgryqwtIEeyLLRsvDm0JTKvoGeiqAeANg2e0hJqx5WnpaCevc3LPeZkzkCIcUN9xzTC3H5TxdAeuvsmnHj6+diwoVrWujziOv0/b2LoyBEYPnoEnvnDX7Bs0RL5HN3kiNcrBrPJ6E2jo6EmJdCnIxDwY8igARi3y2gJnu06dhSGDx2M/pV9UVravgam9fWNWL+hCitWrcGCRUsxd/4izF+4BGvWrkcikRTPwO/3ycAiPfkK4hbczuPvMz/+VB4jx47GmT8+ByuWLJd7Li0rk+BmMpXG0CEDRTbmjEpZ7aDJWSdbyBG5asabb8K2ArjNQXRK1pU7ppVF7gAU5zag7G8XuFgtyZwBFm7Fe2xDVVVkkgkk6+ugukqabRaxKVc7Jy/betbH13I4xK2piGfyWBfPoG/QfN+OqhBn61xOR2W/Cvz+nltw6tk/ka04GnQ4EsH//flv4h7zd+bCy/1ZyTh8jg8afSIRkz8+oLIvJh9+MA4+cG/ss9ceGDN6BIIB39Yy0jcFEwuC2M2i8HV7sOEAwseuu4zEt445XF6PJ1JYvGQ5vpj9FT78eCZmzv4K6zZUyRdwMOJgYOcLEPb90PBvv/pGKenNe7YHtryeF5lQNlmr7uKOwr729XG2CsvDp5lZes29j1zLfSqmitjlwlra01dVTXQqk4rA4w80JVXlthHks5eo9jZ1MaIoPQBz9DbPbzPI0lxxBf5Ogs3yTKpUjuF+8rzPPseaxWtRMfFg5JM0FrXZog0NmbxUjeGxTqJ/yLNVVpS5R88ZRUdNMivlpib0C5hR9TY4hVTsZCqDA/bbEw/dfTMuu/ImhEJBs+adxyMzJmFnCTJ+wJ9j8bis5wdU9sO3j52E44+dhP322RP9+pppzDY4wDAuYBuw/djWNlpryOcLgoGWTDjQ7DVhnDzO/9Hp2FhVi8+++BKvvTED7334GdZt2CiNO4KBgJW5Zw4E/oBf/rW/z6ynkMLv7r1FZELZcGnRFticzNwQQ3Uii34BT9Ne/2Y1HCyuRb6KWe2JutBcsRnzWnUx+jWzZmJJWQJ7H34oYo0xM9eDg5exjSC17CwUZwCwaAcAgXXAw6zks3VQzw7mkQRF0cSVdLlVfPLOe4jHg+jr0mSE3mq7267jnmJ6ZxbDSk2jGxHxSREISbCxWog1MvVUAcaW+XHBnpU4bEhYZtQdTd4pVHrms6/fUI1Pv5htJdRYMfeCaDsNnwk7XHtzYDhwv4k45YTjcMyRh6J/ZZ/NDN7O3rMNvaNTO+3vbmlQ4GsciKZ8a7I8eG9vvv0+nn/5dXz2xRxkMhkZ5KRGwhaFUfh5yoCyOOTAfeTeeE/N/c1tgZyQG9b+82gq3l7ZgCV1KXmNvHIiIbf8mVzLZ1SIDlAXWurtJzUpXBri1RvwyTvLsP9Rh4uuKS5uJ5sDbnPeg61HLZawLxIU7QAghqookrwRt/J6jGaCOUzQIehWNtTFMPOjT6CM3LspcaY5kJhoOicVXDkAsGTUkLAHg0q8Uj6cYDbZ0YMjOGZ4KfapDMr+P2HlxuwQuE53S6lqBVOfewV33PcY1qxdh7LSyGbpwDR8GkltXT1KI2F87ztTcPYZp+DA/fbayujtZUFXYEsD5YBgxyhoxD888xR5fPzZbPz92efxxn/flXuKhEua7pGQwKHLhT/9dSre+O97uO6Ki3H6d6fIazu6FLCvZkDIgzN364NTd6nAZ+tieHNFPb5YH5ODOXzX6DKfcE3OXVJqPo1EJo9wM9mDJswkLCVWj5lzZoqO2Z2gzEw/c5La8lrsxCNz4CleFP0A4Nc01LL5YzMHLvgeFmugVXI76uuZc7Bm5Rr06Tscej4njTtb+/6vNsYxaWhY8sd9bgX79w9JFZhTdymXxhJUJnmvbh4+sVuE7Qio7G63JjPjr2+9Dy+++hYCfj/6lJdJVJ1gxJszCSPh4ZIQzjnrVPz43DOwy5gRmxlYVxp9a5BtQ2vLrvBaOXDxsXDxcvzhqWclJZr3yIHAbMJpDmaURX19FD+78mbxHm799RUykOxIHoANJvdIUpCi4JDBJfJgbcB3Vzfg34tqhWM79dulmTrQ+lazKrqExlrRraULFmH3vScgn0yK7m2WhVoAjiXUXTksVqQ5AEU9AJjBF+ZbW91ZtxSylfjDM9dmnrcLsz7+VDLJslXrkI83QnF7mo8gKobkcX+wthH/s0el2QlGB84a3xc/3L2vuImEfWrXXIq0IWdfouEa3pz+Aa678S6stmZ9u7Clvd8ebYzJnvv3Tz0Bl1z4oybD5yxotrdXu02jkcLBwPRWIPdzz+3XyaD2yBN/lYFAjv+WhGQQkCKfbhfKvBEZIGd9ORd3/OYaHDP5kB1eEvBdtkvOwYjPcMbn8eHvjKkQXSLXTBWOpXXRAeqC7EU2mwaoii5Rp6hb1LG99t9bjNruQ9hcsoF5VsBaAuzgrtHORPFNJ1vM8Kzo0tJpQDuYl4Mq++eff/ARvIEgMrUbka5aB9XFAUDf6gRZY9osMsIAIOvGyddbR0b5XNPxXiuKu6OggWt0/1wq7nnoSZx74RWorqlDOasEF5za4881dfXYf9+9MO2vj+J399wkxsKZL8dqN1pxzvjbC3PgYg6EIffEe+M98l55z7x3yqDwlCFlRFlRZpQdZUhZtuX4tN0t2D4+zNOC5NieE6qTZvovU8A5GBCbVRgydNEh6hJ1irpFHaOuUeeagodbwpqcqLtNHkKRoqiLgjKXm40+mxOyUXBCL+tyY/nCRXIwxhvwI5dKIrFsPhSXWSqM72NKJhtDEIcOLsGthw3F48eNksMgdsFHThjmmm7T8d4dBZWYShuNxvDjS67FXfc/JkEwBruY4MKZjBHkhoao7J/ffet1eO7p/5UoOPPeTcPXzCSfHgLeixnYNOQeea+8Z947ZUBZUCaUDWVEWVFmlB1lSFlSpm2trmwP/ja/duHYYREvnjx+FG46ZAj2HxiS56gjkh5uDQTUIeoSdYq6RR2jrlHnqHstnQWgzlJ3qcNdbUetPVzFGqK0D1MEXYrMykzw2NIm5GBPXkfMUDHrw48ln97r90HRXIgtmI0+R5wgAUQgh6FhH44YGsaRQyNydJSgc1B42Ki9Jmev91esXIsLLr0WX341X9J+7TJgkhGYz8t699vHTcatv/4lhgwe0BRVb2v2W3eBOaiZ+RA09nPPPhVHTToYv771frz6+nSEwyEZCLgsICi7F195CytXrcWTD9+J4cPMoiBtXQ4V8mvn9Hs1FZOGheXBXQPuHry7Ooo1jWxCqyCYzYguUad4/dQx6lrJLuNF91x2J+ECcJCRHhJSTbh41/+E8snGaHGOANYIxWDOh+vj0mxhy9NaMgAYQH+/hudvuBqLFy6VY6uSqGEYGHL+tTho3wk4dkgQ+w8oaXZt31HgzOXxaPhi9lxceOm1khhTGi5p6hbMaDcPunCvn9Hu8885XZ5vj0J3d9gDJvHHv0yV3ZF0Oi2zvz1oul0u1EcbJfHpiYfvxD57jUeG7dq3cYR6e2FY8RrO9zYN8YyOj9dG8fqaBD6fOQer/3iH5JMw0YzHrMfsMgqn3HY31ifzcoBoK53UDQwIunFw/2BRBwAJq4F68T5opBU+s+hH81V9Vayrj2NjXVR+puHToKLRRpzoq8HthwzEEUMj5sm/dq7tWwJndRr/J599iTPP/RmqamoRKQkh02T8mlS1HT1qOP719GNi/LkCd7+3wl4WUBaUCWVDGVFWtoFThpQlZUrZUsaUtb2D0l4o1hJRTu9ZsYKgW8VRI0px92EDcYKvxgzSkidL36hr1Dn+3FxVJ+oqdbYpB6CIH0Xvc/IkHmuwNzf7s+hELptF6eAhGHHgocimUxK1lRpzgQCOO/wA+Uw6mxdXvz1r+1aN363h1ddn4AfnXy4Ze9zm48xPxaJbz6DWSd8+Gs8/+wT23H1XmcHMLb1inht2DuQEo6Q550U2lBFlRZlRdpQhZSkyzeZExpQ1Zd5Rg4ANe7eHLjx1hrpz/OEHyLFsyXPg8e50CiMPPgxlg4eK7knhky29VlURnW2pn0QxoagHADPbzwymlEgF1k0XTDeLKbohvxfzX/431sz+QlI2+ToPl+y262iMHTNSkjjcDDB1wvXZxv/W9A9x4c+uk6o4zIOn+0rl5ezAZha/uOR8/OGRO80CF1kW7ui9s35LoEwoG8qIsqLMKDs7NkKZUraUMWVNmXfGIEBQV6gz/FtjxowUXaJOcfLw+P1YO2c2Frz2AgI+r+gg3XyCukcdpa5KALCFYjbFhKIeAApH1AEBt1lggfXwDB7o0HDI0HKMTFbhrQfvQfWK5fJePZ+Xs+ZHHnGwRI7Z1rmz1q9UQLqkP/3FDZKy63Ez3ZVbgGYgK5FM4o6br8avrvqpuLkM9rXloEtvAWVDGVFWlBllRxlSlua2qS4ypqwpc1kOuDdlFnY08gzyuVTRJeqUnqNXoGDj4kV44747MSq1EQcNKUMF80gMs4ktdZS62npJ+uJBUW8D2g+maA4MulHiUTEq4sVhA4M4uDKAMeVBzJ81G/5wBH0HDkBJOCwNJSsHVMpJObnBTmjRaqb2api/cCnOuegKOaXHRBYONqYS5+V8/v8++Fuc96PTpLgFM8p2+IhrL4SZ9KOKzCg7ypCyNPMFzAGdsqbMKXtyQC7ISUdDtfiiLlGnqFvUsb6DBsJfEsGCWV9ilz4hHNI/iEMHBjEi7JE8A+oqdbar7WZ7HsrHG4p3F2BLsKwSCzGax4DNyG0yERflEOMyrAwsl4YDhvTrFPeLa0HOCkztPeWMC/HNug1yBNZ2+zlbsTrvYw/ejhOOn4xMJrdZzX0H2w/T7Xfh5dem4+KfXy/5AbaMKVPWIxg4oFLiBvZBos5InDIAfLJ6IxI5q0elHFE3A7j+QLDp5KpinQ9oS72BrkK3GgDsrEr7Z0KVBBKTJXNb0MCggAdD/O4WC4q2FWbE15DI9Ok/vASfzZxjbvXxzL5Vm5DG/+gDtznG3wmDAN1+DgJ2OTSu07lFuN/eEzD1b4/AIwPtjp8kbA1knPa8OpnF2kTGrEHJF6w6FVxy2u8jzHIt3QfdakEqLssWCR1cl7GQBhWFDz2XA0+dcy1GLyHbgY+MVUL4+lvuxUefzpSyXgxCyTk/njBsjOHBu290jL8DwZmeXhS9KcqWMpajz1YQlhyQC3JCbshRR3Ke40M3RKeoW7aeUeeoezZsvexOxk90f9+0oHw1wW2clfG0PDryb7BIJ+vX/fOJZ/D3p/8l9fbsJB96Idy7vvXXV+K7Jx3nuP2dNAhQtiw+8utb70VFeZks/cgBuSAn5cNH4HvnnImGKqvMegdbo2YtL7qPg9/DlgDbi46+Ibp5LGvFzjXX/+RyUUi7zRZ/rq6uxUXn/wC33fjLXp3Zt7MyB2/4zf14/I//QJ8+5TIb21zw59v/9yGM32sC4rGYDMwdCQU9D91qCbC9UDrwIdlfbpe003r0t/fKYGA3rmTeOqv2TD7iINx8/c87LQjlwARlSxlT1pQ5ZU8O7AIp5IYckStyRu46VBd6ILrFNmBXPiSrMBTC3x57EksWLIQ/aNbwo8IlUikMHjQAv7/3lk3uobPV12mwZUtZU+aUPTkQ45cOw0HhiFyRMymGWgQ6ZBTxw5mutuX6l5Tgk3fexytTn0O4tFRiAYR038nrePDum6SaLQ8DObP/zvIC8iJzyp4c2Pn40m6stFS4Imfkzo7SO2gezgDQClilh91s/vzQo2Yij/U81/2scfezn5yLQw7ce7PW1W3F5m23zXLZjHLbR2O7I+xqP03lvwvamLcHdrclyp4ckAs718KsiKwKZ+RuW41XezucAaAFsGQVZ5AXnp6KpfPp+psHQuwSXgcfsA9+cel57epi0/S3rApCDHDxwUQjPpjm6rbKljMvvbvAvla3dQ/2/dj3x3tt78Bmd1siB+RCTuw1LQUCwhm5I4fk0kHzUD5a3/N2AdoLzlBsurlu1Rpced5FTQ077DLYLHX94tQ/YcLuY63utWq7MwsTiRTeePs9fPTJTKxdt0GCW6NGDMWkww7CYYfsK+9t79/aGaBh24PWex98jhnvfYSly1eJJzBoQCUOOmBvHHvkYQgEfO0Omtp/a87Xi3DS6efJGQGbI5Gr24V7//Q4BgwdjEyah3l6aiiv7XAGgGbAdWMoEsZ9N/wGb77wCsKlZi0/2fKrqcXlPz0PN1x9Sbu3/NgRx+3S8J8338Xt9zyMRUuWm+fT7Rp5bCfudskMd9uvr5DuPMW8zWhv0y1YuAw33HofPvzkCxk86YaLUdL9BzB29Ahcf9Wl0mmILdM1VWv337zt7kfw0KN/kr6HkpbNmhD1DTjm5Cm44rYbEWuIdvi2YE+AMwBsAbuWwII5X+O6i37WNKsw1ZeHUgb074fXn/8bgkF/u9JObcX901+n4bqb7pLGoPxOHnLhV7Irr3T/NQypmReJhPGXJ+7DAfvuWZSegD0bf/L5lzjnwiuarlmy9uRMf1ZSZ3mkNx5PStPTO265Rg78tGdQs9Oz+Z3HnfJDrFu/UboL6QXe2h2P/x67TtgdyUTCCdRuAWcbcMutERq7puGFZ6Yim840NRpVrDZWV11+EcJhbgWaCtZmY3Fr+MfUF3HNjXeirKxUDKC2rkHKYXHQqa6tk5mMA0VpaQSpVBo//PEvMOfrhWJobamS21nQm1zxhXKNvFZeM6+d98B74T3x3niPvFfeM++dMpDTfG28H9PdN4QTckOOCjkjh+TS9qq6etvNKLKH8qETA9hq9p//5Ve44eLLpcOtnWQSiyfk0Amr2VLh2rp2tUuGL1m6Csd/9xxRYDHySBg3XnsZDjt4P6TSGTz34n9w/+//2FQ8hP82RGPYY7dd8ML/PWkmunTwwZf2zMCs1nPy9y/AV/MWSlEPBugI/vvLn52P7570Lfi8Hukd+Js7f4f6hmhTOfDXnvsLRo8aKrUA2iNXVhf67lk/kUNaIStoS/lks1nc9thDGLfnHo4XsAWKy4/savCIp0vDq9OeQzpVMJNYRsqmHVSy9m5j8fvu+/2TaIzFxZB5lv3RB2/FKSceK3ntgwZW4mc/OUcMx65HR0PiycMvZn2FZ6a9JGfji8ELMAc0Va6J18ZrlJ0RrsEbY3IPvBfeE++N98h7NWsquEQGlEV7BzJzoFaEo6bj4ZYXQC7JqWwJtpO7ngZnAChQIJ/fj+ULF+Oz9z9EgJlkVtpvYyyGg/bfW0pY2xV/2hPxn7dgKV5/6x3pEsRZfZ+Ju0sDULrLm/IBdJzxvRPRt6JcZld7EAoGA3jq71ORSKbF8No7GLUH5pl4Va6F18Rrs42P18xr5z3wXuz9f94j75X3zHunDCgLyoSyaeugZlcMIkfkipzZ6cHkkpySW3LclTIrNjgDgAVp0e3zYvorr6Ox3mxUQZjRax0X//iHMsO0Zz/eblP92hvTZUlhz+IBv9mttrDLr9Sf87jlwRbV9uf9Pi+WLFuBL2Z+ZV5PFyoz/zavgdfCa+K12dcjbbWt67fbmxW2+OI9294DZUGZ2N/Z5uuRpZkiXJEz+2+RS3JKbslxMXhOxQK1OEIRXfswe/i5ULNhI95/6234Av6mfP94PIG9JuyGI484UKq8tmcLzo7cf/L5bIkvcMYKBvzSS4B7/3a7MPtvv/fBZxLVNgcB0zAksp3N4cNPv5Dfu9oDIHgtvCbb4CSPwuOWa+c92Ln6dhsw3ivvmfduLgXcIhOiPbsbZi1GQ7giZ+TO/tvklNySY3Jtxy7Qyx+OB0Ax6Hn4A35pLb5+zTeSBGRv/aXSaZx52skShGtP3TnTXVYQiyWxavVaeNyscGsuJxobY7jkF7+WhBn+zsf7H32OG2+7Hz4fZ8pNRm6Xolq4aFmn1TzcXth/m9diB/Rs8Jp57bwH3ot9X7xH3ivvmb+L5+V2i0woG8qoPYOaWchTE87InV2piZySW3JMrsm5A8DVdfNHEUHW1zref3O6VVvQ3OJLZ7IYNLC/tPGiTrZndjKVWkFNbT0aGhpFSe0Owlw7fz7zK5x42o+x1x7jkEylMOvLefK5wtlfvsfaEdhQVS3X1JUHkMzsSMi1yP0UvGZ7Vdz2O/v8X2DinrvB7/Nh9lfzpWkL79l2xWWHo6FRZBMKmWv0tgYFzXLsEM7uf/gPVl9BM/jH7yTHk6d8Szg3OkgO3Rm93gOQ2cHnxaplKzBv9pwm95+KFIvHJW21T0VpU2PP9oJbUqxiU/hd/Hs0CNYTnPHex/jsizkF6/+t1dQcnDJFEdDmNfBampONvRTgg/fEe+M9Fho/IUHDXE5k017YDUbJGbkjh/YZAXJLjsk1OTeKQYBdDGcA0HV4fT5xDRsKTpUxGEXX9DsnHmdKqoNcbW5Fme7y5s/bAbGSkpAYiO0dNAfJqCuiSsO8lpZsqdDL4b01t30p3hWXCB11cs/iitzJUsu6OHJLjsk1OTecYKAzAMjsk8niy0+/kDpyduIPM8p2GTsS++w9Qdaz7U29tWfI8rJShEtCm+1V25AaA9bR2Ra/x0ojZudcXlJXRrTNYKXZxVfuZxvvlQIdW4wU9vYmZULZ2M+1B+aMbwh35JBc2oe5yDG5JueKczio+JuDduZD2oa5Pdi4bj2WzFsAr+UWMnDEdThP4pmdZ9pvZKaiGwiXBKWSTSbbRgXk9+Ry0kSzWHYBeC1SKKUN92PuamRFJpQNZdQRhml2EdKEQ3JpBwPJMbkm5263R3Sgq/WwKx+9eglgr/8XfT0PdTU1cFmpv3QZvR43Jh92kPnGDpopmO/Or9p34h5ySKWtEXy6y0ymKRbwWtq6PUoZUBaUCcXRYQVQ7K4+hx0kXNoHq8gxuSbnHicO0LsHAC4+VUXF3JmzzZmgIMDGGWmvPXczI+0dNADYM9u3jpkEn3dT0syOfD6dTmPokEE4YL+9imYXgNfCa+K17ejsTRlQFpQJ0VFuuTnjQzgkl4WBSnJNzlXFbCffm9GrTwMqqoZ4PI4l8xaas7+VgMPTbHtOGI9Q0ExUaVEpqTz2Yztekyo2eR177zUehx96wKa+89sJyWiLxXHW6SeLu9xROxPtjbjzWnhNvDY7g3J7YJ8XoCwoE+mtWDig7aB8t7w2ckcOySU5lQFLCoW4hfN4PC460NV62JWPXusByD61x42qDRuxbs1aeLzmuX+C+eMHWS72VrO0rXSUnKZsehRKsrnXrM/xH9rsFT+7QIzFPrG2PcYSSySkmMa5Z3+vXScSOxJmpp0h18Rr4zVuz6DWVLVH00QWVvpFu+VbCJs7ctnUwkuSgjzCeRWzAlvYau0tKJ69pC4aANYsX4HGaBSBYFBmBzkS7Pdhwu7j5H2bGafsV1m/J9Iwquth1DYA0TiQysCwKgYr3KLzeYBwEEp5BEqfUiDglde0PHMB8th7r91w5eUX4pY7HkRlv76tFss0TwPmJL/9zluuleO2xVIURGZaXZdr4rWddd5lcq3ccmupbTc/w3vasLEKN133c5GFFAXh/bRTvshbI2wBd+SSnNo7JqwNQM7XLF+BIcOHtmnp0lPQqwcAKsLKxUuRZ80/zkjW2XEa5KiRw+R9sv63fSUeBaZSLlwJY30NkEybgwLfYz+s726a8fmc3wulfwWUXYaJsrIEFhX+8p+ei3giiXsfelxOxUn1IallLwuUpsMzsVhcvvd/f/dbHH7ofshmzZyBYgENl9fEa+M1Xnz59ZLwwwIgm/IZzG7OPJ7LoB9Lq115+UUiAzF+5gBQ1B0gXxGfvil2Qy7JaVV1jZw74PPkfOXipTjkaGZ5Oh5Ar4N0Gs7rWL1ipbk2tIyd6b/DhgxCJGxtSfGd9GjTWeizF8JYupab2lyQAx739v2xbA7G8m9grFwPZdQgqBN3geZ2y/r5V1dejAH9++K+3z2JDRur4Xa5rDRhfsyc9cftOhq33XiFFAsp1pqAHJB4bSd8azIif34IN/zmPsxfsASqpso90RZ5v7ynyn59cMM1P8P/nP09s3YAZZnNQp/VQfLdaxfA64aSN7deySU5XfPNeikXJlyrqnAvpwbRe9FrPQAz2JfChjXfmAlAXItLqelNe+xUDs2tyUyUn/4FUF0H+OhqWoUltnfmkLO9pjJzdsvXRKFN3geqzyuVcWkIxx11OP790hv4+LOZ2LChWgxq2NDBmHz4QTjx+KOkiq458xef8dvgtfEaOVC99txTeOm1/2L6ux9h5ao1EpCrrOyDA/fbG9858VgMHNDPrArM+0l1jnz5XbolM3L6zgefmGcAmHXpcgn31IFiiKV0FXrlAGA39eQ6sJYHWaS8ltXe2TCkHLe8j4qVzSE/YybAtSjP7bd1n9pWZn5HbYN8p3bUflBd5nKABnHJhWfLwyyNRfd/08dpLMXk9rcEXiOvlQPW90+dIg/eumRT2ut7ORNBT0btdPka3OrjMmDEUPOQkfV2cl5bVY1YNIqScLipyWhvQ6/dBpRAUENUtoLso6yMGtNdHTHcHABUtwp9wQpgYy3g9bRdOQvB7+B3baw1v1tVZA1No6dRmLXyzT1szqZ8rj218lqCmXZsPjp6DawW3A/vwcxX4JbhpvuRgB8LmnSyfMkhQU7JrZ0QRM7j8bjogBQM7a12gN4aAHRpqK+pRTqZFNff3IYy4PV6ZI0qyORgrFhnupcdmXPP7+L2E787q4tbakfG7eQagjMkn+vImckuOcbZ2OUyH9KppwNadhXCvh/ba7GTlpruR7wrvfPlmzF3DsgpuTWPGlu1ApNJ0QHqQm8NBKq9tvinqqLeKr1tG5hU6AkGUGEfSkmkgWRKZqoOB7+T351IbtrH3gmwW5ClUhksW75GHvzZbtm1U2Dv8/PeO1m+wiEgnJo1C/WCJKac6IB4I710AOiVMQCBoqChvr7gNB1nwZyUkw4Gg+ZTlpuKzurLx+/eSQEou8gGW5A9+Mif8Mob01FdVSOv9elbgSnHTsbPLzlPqvi0pyDHDmEnypeckttYjIlKZsBQ13XRgY4669Ed4ZKFQC+DbC1DQTzaKAshoV8SWvJyZp3po9RJJegDQkGgLmpuS3XULCF7YnkgHAb4NzgGdbISSm6BYeCX196Gf0x9Xlpo2efvv/lmA+6471GsWv0NHnngN6ISsv3ZWeC98p53gnzJIbkkp+SW9QgVxWPenQHRAd5rU4nAXobeuQQgGGTLZC3rt54y2JLLZSmomfWnjh0mkeoONVBrd0G+m7NUJ7ufZsqtipWr1+HVN6dj6OCB8HhcZittKTvmkuf4Gt/D93Z6nQHzlFXny5fLGhnNFeF2M1Erlg70QsPv1QOAvd3XUFsrrq5RUGijorzMLLRBTWEi0MiBUMYO3bRWbY+iSn1sa206dqh8t62cnQm7fVb/fhUYt8torN9YBUUxA3JmUE6V5/ga39Oetmc7cFFy750u37y5u0NOya1duEQ4VxTRgcLtwd6GXjkA2NimklsnVNQD94AyZhiQzHCU2CwtdTv/kPngZ5MZ+S5+pzkddb7qmQMAZG/+kftvxRGHHiBFMurqG+TBn/kcX+N7+N6dsye+E+S7je9RevH6v9dWBS7cB902zLMA6kG7w+hXBv3rpUA0ZgaXuMW1rSAerYnrUf4bDkHdfZSkq8oaeCfOO9yH55786JFDpb/hl18twOKlK+S1MaOGY889dpWfd/45g66Vr7HD+tCz0Ht3AXYUnJhGDYI2pBLGim9grN4AozYKZDItR7Hpjno8UPqWQRlSCWX4QFbQlO/amT6n2XxD3SyZiAZvG30h+D5Z/eTN2gg77yK7r3y7M1y9c9wzx3u7CtB2uYQSJJAoobm+5Lo1loLR0CjHVQ2eXLOSTqiEit9rHleNlAAh36Y/W3BctbNhn8SjUSeTGayWcw9WPwJmAVoRMR6CUiT12GyDNnzYYMkLoDdgpiTvhOvdCfJttnS53rt9gF7pAdgBoHBZpCkAxOfo+tbWMTfAdJm3gn00WJRGEcVTqHyD+rY+4cix+IJjrTsB5v2Ycf7X33oPd97/KFau+qapIYndg5Cwq/XayTG7jh2F66+6FIcctI/1+k655E6Tr9nTEcKtNFQtCASHyyJNgeDeiN4bBFQgteELmbfrATa9ocXPUtEsxeOM0+rD+qqdGGwyBzUDS5auxCW/vAk/vuQaLF22qsn42bGHwb/999kTxx8zSc7ms36+fUhqztwFOOu8y3HV9Xdgxaq1pre0MzPlOly+5mtbNTAxLB3oxcuFXjkA2Ekg/kCgiXw5ICI1AhNI8HyAdTZgm19UWKyi2Qe6rGvv0hWr8Y//+7c8Fwj4mwqM1NU1oLQ0jF9deQmuv/oSOShTVWNuifJREgqKV/DEn5+RQaTLuhB3gHzt3H9ySm7JcROviqUDdjJYL0SvHAAIKnRE9vxNl5CQVtWxhDy68+6QWXzUwHFHHYoXp/4R48eNRSaTlTZZVP4D998b0/76KHbdZST6VJThn39/TNpo0ejjiYQU0OQy4KVpf8TRkw+W7yqG8mNtBbm0eW06nGSdWoyUl3Vpi/WuRu/cBrTSfsPlZdC0Ta2ipfBmPCEzZP/Kip2XE98JYGAvlzNw+CH74fCDn8K8hUvF1R/Qvx/GjDLLnfF1Tn0jhg3C3//4AJYuXy0tvUsjYUkKYgyB72k2HtJNYDdlJafkVhqFWksaTXOJDlAXqBO90RZ6ZRDQjHbnUVpeZnYDsotFqmbdfXa7HbfryG6vEPYZfM50u+06imUx5Hmex7dboBE0co5zo0YMkQdBu7E/251hc0hOya3bzeWNtIUCuacOUBe660DfXnRvdtuBfC6PktIIgqFNffpUqyjoipWr5T094Yz4JiNnMQ5d/i3cATDfY679ORDY7zF3Qrq/etgcklNyK1ueVj/CYCgkOkBd6K3olacBefqLpIdKSlBaUY66mlqJfosoFAXLVqyy3tdzsD3GbLr6PemuN92NcFrg5pP/0opy0QH+LKcfe6EtdP8hvo2g2+fz+VE5aAByuaxUiKHfyzXi4iXL5T291S3sSbA5JKey/mdcR4q/ZoV76oDdNKQ3otcOAGaijIaBw4aZ3XmsnQGP24MVq9ciHk+ZSSM9YBnQW2EG+lThkpySW3IsXOu6cC/1INF70WsHALs11bDRI5uUQNpGedxYv36juIzblQvgoGhh5wCQS3JKbiUN2hr8h40eud2t2Xoqem1VYGpGJpvB4JHDEQxxHZhrWiuzW89XcxfK7715j7i7w+aOXJJTOw5Crsn54JHDRQfs2EBvfPRqD4DVYPr2749+A/tLhNieCfjvx5/NMn/u4ut00HbY3JHLQm7JNTnv27+/6ECv9gDQi8HgTyAUwshdd0GOeeLS6VaHz+fFzNlfSbVcO3DkoJvBCuiSQ3JJTsXdZwAwkxHOA6FQrw4AorcPACYM7DZxz80aT/o8XimQSddRCs04A0C3Azkjd+SQXJLTTWcAFJNzOAN7rx4ApFNtOoOxe4xHOFJaEAdQpEzWO+9/Ir87gcDuB5szckgu7XRmckyuyXkmbXp9vRm9+u5lPZhOo3LQQIzYZTTSVqNIBo+8Xi/efudDSZtllVwH3QvkjNyRQ3JpnpBUhWNyTc6z6XSvXv8TvV6zZe/f68Xu++0js4O9PRjw+zB3/iJ8NXeBVVBiZ1XFcNBekCtyRu7IIbm0t/vIMbkm57qztOu924D2Q5YBmTT2OugABLgdaAWFePyVZ8iff/kN+d1ZBnQf2FyRO3JoH2Umt+SYXJNzxToK3psfvd4DkCpAyRSGjxmNMbuPk4aR0t1WN/sEvvbGDEQb403VdBwUN8yqRppwRu6kH6B4BGYzUHJMrsm50svdf6LXDwBNSuN246AjJ5keABNDuBvg9WL5ytV48+33zd0AZxlQ9CBH5IqckTtyKAO3dQKQHJNrZzA34fhA1A1rdtjn0INR3q8fclZSkF0/7+mpz5udwnp5xLg7gByRK3JG7pqKnWazwi05tlvCo6v97yJ4OBptHQ/OpjPoO2AA9j/iUKTiiaakoFAwiE8+m4VPPp0lFXLs9tIOig/khhyRK3JG7uzkH3JKbskxue7U5qfdCM4AYMEsiJHFpCnHwxvwN0X9pTitbuDRJ/9mva/ryHLQOmxuyBU5s6kil+SU3MrRb4fEJjh+kOULsTFGKpHAmPHjMGH/fZGMx5uCgeGSEKa/+xE++nQ2XC7zOQfFBXJCbsgRuSJndvCPXJJTckuOyXWX+94ojkev3wZsTizHfe+UzaoFm16Ajkee+GvT7w6KCzYn5EgCgdbv5JBcktOuNzcU3cNZAhTAni32PGB/7HnAfpt5AZGSEN6e8QH+O+Mj0wtwYgFFA3JBTsgNOYpsMfuTS3Jq8+lgExxpbAGOinQRv33GaZsFivg8++rdcd8jSKUz0rTW2UrqepjVjSGckBvpfVjwOjkULtXeWfZ7W3AGgC0FwgzAWBx7Hrg/9j38EMQbY1A1rWlHYPacefjjX6ZKqSknPbjrQQ7IBTkhN3bkn5yRO3JILsmpM/tvDWcAaAGGoeM7554tveNsQ2ciCZtm/O6xP2H5irWSceYMAl0Hyp4ckAtyQm7sVG6J/Pt8wiG5dNA8nAGgOaHI2jGBXSbsgWNOPQnxaNSsG2g1z4xGY7jp9vudmoFFUvOPXJATKe0uhUA14YzckUNy6cz+zcMZAFoSjBVAOuVHP8CAIYOlq4zdUIIzzWtvzsAf/zINbreGXC+vKtMVoMwpe3JALuzZX852pNPCGblzAn+tw9kGbGF7BFbtuNI+FTjj4gvMs+P2qTLuCoRLJOg0d/4SeNyakxuwE0H5U+aUPTkgF3ZuBjkiV+SM3JHD3lz003C2AdsOBpJiDVEc+q1j5RGrb9i0FNA0iTxfduVN0nSSuSXOrsBOivqz2288IbInB+TCdv3JURNfDVHh0EHLcJYA24DUC0ilcPbPfoJ+gwaaSwErN6AkFMRX8xbiquvvaNoVcLaaOg9GQdSfMqfsyYEk/vBAVzotHJErctbby31tDxwJbQNcU2YyGVRUVuJ/rrwcebqUFnK5HCrKyzDt36/goUefkjVpPm/WFXTQ8aBsKWPKmjKn7MlB0+vZrHBErsiZk/O/bTjHgbdjoaSp5lLggElH4OQf/QCNdfXQXK7NBoE77nsUz734Bjxu12ZK6aBjQJlStpQxZV1o/OSCnJAbckSuyFmXL7CN4n84HsB2QtaX0ShOv/B87HfEYWhsMOMBBNefdEV/fvUtePeDz+DxOINAhxu/xyWypYwpazveQg7IBTkhN+TI5sXBtuEMADsCw5A16MU3XINBw4YimUhAtRqIctuQSSkXXHotPvnsS2sQcLYH2wvKkLKkTClbylgOalHmmiockAtyIklZTtm2HYKzDbgDHhOTzjPpNErKynD57bcgEAxKaykpJa7rcLvdSKcz+NGFv7QGAc1ZDrR75tdElpQpZUsZm1V/VZE9OSAX5ITckKMi8KzRXR6OB7CD4LZSIh7HyF3H4me33iitpewEFEajfT4P0pnCQcBZDrTd+F2bjD+TEdmaNf/MhCzKnhyQC3LibPntOJwBoA0w151RTDz4IPz0xl9JvTlzf9osGebzeJDJZPGD8y/HK6/PEEWmwjp5AtsGZURZUWaUHWVIWVKmlC1lzPdQ5pQ9OSAXzrq/bXAGgPYMAvX1mHTit/E/V1yOeGPjptJieV160eu6gZ9c9iv8+W//lO0rwjk81DJs2VBWlBllRxlSlpSpva1HWVPmlD05cIy/7VDeWl1nhlMdtAmcrUoiEbz89LP424OPwOv3mQVF83lRTCp1tDGGC849A7dc/wtJYslmzdccbC5HM49Cx023P4Ann3pWynpJQZZ8Xtx7Q9elnv8Pf34JTjjrjM12Yhy0Dc4A0AGgsYfLyjDjldfw6C23S915nkyz21FRiWtq6jD5iIPwwJ2/xqCBlTII8PnenqxCd94MoGpY+80G/OLaWzH9nY9QUVFmZlZaOyyMCYjbf9P1UtwzWlfnrPk7AM4SoCOEqGloqKvDEd/+Fi6/7WZRWEak7XMDnMH69CnHex9+hhNOOx+vvj5DFL63lxm3y3hTFpQJZUMZUVZ2zIQypCwpU8qWMqasnYBfx0B501kCdBik9XRZGeZ9MRMP3XAL6qqqEeRJNTtjTdMkX122Cn9wKq6/6hJJasnmWMSSnkLv8Aa4rud/bpeKxlgct9/zCP76j3/B6/VIJ9+m/owuF+LRRpT17YPLb7sJu+2zt8z8dhamg/bDGQA6GDT2UDiMdatX4+GbbsOiOV8hXFpa4M7SyBXU1Tdg7OiR+PU1P8NxRx8mn+3py4JCd594/a33cOtdv8eiJctQVhqRnWkODvayKVpfj7ET9sClt9yAAUOGmFl+jvF3KJwBoBPAGczn9yObyeDP9z6I6S++jEAoJAUrdcvlZ0ZbIpFENpfHyd8+Glf94iKMHD6kRw4EWxr+shWrcc8Dj+OFV9+C26UhEPA3ZU0yuy+fyyMRi2HySSfgf678OdweD1Ls8usE/DoczgDQSTCPrWrwBfx4fdpzeOaRx5FOpRDg8VVb2a3jqg0NUZSVleKcs07Fj8/9PvpUlFnfgR4B+1RudU0d/vDU/+EvT/8LdXX1iETCm23/cYBk8U7W8jvzkotw3GnfRSqRNHcBnKO9nQLlzVW1zjZgJ858clAoEsGSufPwp3sfxPxZX8ogYNevIzhQZLM5RBsbMXrkMOy79wRcdvH/yM9sccXkl+4InUE8VcGSZSvxu8f+jM9nzpGfwyUl0rjTXutLZl8uh3gshnET98J5V/4co8fvJtt8fK2neELFCGcA2AmgovsDAYlm/2fac3jjn/8WF9fsUGsOAlRydrbNZLPYWFWDV557CocfvK8ECLtrV2Km7TLQ9+6Hn2PKd89Fv74V8Ljd8nxTViQz+3RdBsVvnXYqjv3ed+DxeuWQj+Pydz66p2Z1M1CRafCMAxx50hT0HzyoqQW5DUlvlWQYN8rLSuHuQcEu3ovck5sZfZunREvbtVwOZX364IAjj0CwpEQKeXbXQa+7wTkN2IknrTYFwAzZHnznldfwy9PPxsI5X8ksx5nPBscC7hCYg0JPXJWZS5ktg5uUD4N8KxcvwVVnnYsX//40guGwCIRLCPOTzsPoJBn0nGmmCGEL2R8K4ulH/hf//vNf4QsEZIfADnzZBsFMN5ax4vOxWLxHHSPmvTQ0NEriD1t0cRlAb4DLH3s54PH5ZEB86r6HsHrZcpx/9RX8YFM2pYPOgTMAdCKovHRp//H7R8X4I2VlTVti9l43jZ0BwLKyCIYNHYSB/Svl6Ct3BTgBdmfl57XzHngvxx59OHw+HzZsrMKatetQU1svHkFJSaip0ArfTxm99a8XkMvmcPGvr5OdEwedB+UNZxeg0wJ/4dIIXp/2bzzx27tkCcAZjm6tS1ORyeSQSCRw4P5743unHI9DDtoXgwb0l2OwNnraNiDBU33r12/Ep198iX8+/xpmvPex1Prj4FCYAVhfXYPTLjwfZ1xyoZT6dlJ/OwfOANAJ4GxGN3/l4sX4zcWXy3qWs51p/Bri8YQ0s7j+6ktxxvdOlPU/wdkynzcXDj1t37tpr1/jkmfT86+98Q5u/u2DWL3mG4SZNm0NAtKkNR7HVffegb0POViOADuDQMejZ2lZUbm+Bp599EmkrQw2aSaiqmL8I4YPwT//8RjOPO1ECRAy84+GTxthILCnGT/BezJr+ZmDHO85l9Nx/LFH4Plnn8B+++yJaLRxs60//kwZMj/ASQHuHJCRaktBe2LouUtmOm73ff7u+5jzyacIlJTIcWHpWZfJoqK8FH978gHsOnYk0pmcuf+vaVbCC3oF7HuWU5OZPAb074u/PHEfxo4ZKenRdo1FBkyXL1yId15+VWRKOTpoP2jrMtAaRjX9MW5Id8DXOrCh63m8++p/zABeQaJPOpPGvb+9AcOGDpQYQE/a628reCYik2XD1RI8fN9vNmu5ToNnWvD7/3lT8iikKEhXX3BPAXVTUbKqbhhV1tpKVNV5tF0GXON7fF6sWbYCC2fPgTcQaDoT0BBtxEnHH42jJx8s7i9TgR2YYFyEg8Duu43BuT84TWRlL5u4Pbhy8VIs+nouPNb2qaOjaG9+ikGbF9sHjHWq5mJKljO4thOM8rs9Xiz+et5mDSrsZqLnnn26Uxi0BdjFPn941nel1bfdcp2eUzaTxoLZc8xB01mpth8KBwBOQMY6hpxWSdDJMf8Ow7IFCzZT7GQqhTGjR2DvvcbDMMz1r4PNYa77gRHDBmHinuMlFiDpwFIVyIUVCxYhl9s8fdpBG2FYCWi0fQPGUtP4uV511gDtkQGr+jDHv3rdeqjSm86wutZmMG6X0VZ58B6yud8JYFYgscf4XZG1zkpIWTCXhtqNVUgnklAUa7JyHmj7GsC0ddq+akCZz5HVOpfhoL2txNNpRKV5qLmGpVC5buXWH8FSWA5akJ/176jhQ03jt37nepVLqmTcPCHobFi1GwptnravKi7XomwqlVbMzWdHO9sLo/na/z6vt91f3Vvg83m3mo0YXyk8POWgzTBo62LzLtciVR9YstyAsc7l9sDQHfvvmD1uBqs2fz6eSLb7u3sL4onEVjMRvQDF2QZsN2jjYusw1tH21W8rStqAMtfNGYrZqs7yqu3bgLoOl9eLSHlZU79Au7T10mUrhIDuWt1nZ8A2+sVLVzQtn+x8gFBpBP5g0EwVtpYHzgNtWf7rtHXaPG1fck4NBZ9oLumq6rgA7VFgbve53Og3cIAkA9ln2lnuev7CJUgmM5IL76xhmwej/tzlm/P1fHg8HpGdHJXO5tCnsp8kBTnLgPaBNi62ruAT/i4DgJJX3s+mM02/O2gPdIzcbZzsCIjADQN+rxdLl6/CR5/OlAQspz9g8zsAjEItWLQMs+fMRzBg1UzgIKrnMXLcODkP4Aye7YZKW6fNyy/8nxeYlYjH6l1ut+qcCWj/LgALWoa5DCgo6kHD/8Nfnm0/fT0U4vIrisgokTTPA8jz1snKXfeasFUZNQc7Bto2bZy2Tpvnc+rUqVO1ySPK6gHlI58/wHnLCbW2EVTOTCqNAcOGYPw+eyOVSEjwirMbK+H+d8YHmPrcq1Ifn0VAHGyqGORxa3j/oy/wf/96SY5K26XA2Q9g5Lhdxaviz1JI1UGbQNumjdPWafO0fbXvaafZp9FfoHANJyW4fZAqtwYOP+H4pmQggu5sKBTE9bfcg9lzFsDrdckg0Ntd2iyN3+PC6jXrcdmVN0m6rz3HUx856x8+5fitaig62HHQts0B1HiBv9P21RnWjK8a6huxaENOUVRJEm7D9zsoKGQx4cD9sd/kI5rOBNjnAWj051z0S7z34ecyCPD8P2c7u3VYT4ddEs0u/OH1uDB3/mKcdd5lqKquhc9rBv9Ejo2N0ifg4GOPlp+dgiDtgkHbpo3T1vkEbV8GW8MwVEVR9FdXVL8dKAlPSjY2slulk7DeJnDrj4eCPKhevwG3XnSpLAU0t9s8GWilBnO5cOlFP8KF552FklBgE0sM0/bQcUC6IhZ48JlMFn979t+496EnpTZiMBjYbPuUMZRrf38/xuy+O5KJuOlROXNT22Ag7y8pURON0RnfHt7nSNvmhY4Zm6L//3C5NIUK7KCtMINZmVQKA4cOwblX/QLpVFqsWsqC6bpsC/Lc+10P/C+OP+Uc3P3A4/jk8y9RVV0nZwV6YrqwzPyGgdq6KGZ9OQ8PP/5XTDn1PPzqpnukGjL7A9rGTzc11hDF9y/5CcZN3BPJeMwKCvY8uews0KZp27TxQpu3PQBFURTjP6tWlRtGcInmcpXlsll5cqddYQ+EJLBEInj16Wfx1/t/j2BJqKnaDcGlAU8KJpMpSRWuqCiTvoA9Wey1dfWorq5FQu7Zg4BVM8HeBWAMhS3ATz73hzjr0oulHFhPLJHWBdF/JZ/L1SlKfPS3hg6ttW2+SdNYJUhRlPwry6sfj5SVXxitr80pUJyqFe2EBP8iYbwx9V/4+0MPy3NsGMouwbbS24MCo+HsktsTPQAbLs0lfQHN+oBmPMAeDOkJcBflO+f9CKdecL50COrJg+HOggEjFy4tdzXU1T4xZUSfi2xb52tNBj5t2jT5Vzfyj6YS8fPpiO20K+zBkNr/9Q047vRTMWDYUDx17wNYu2yF2SCUcQGrOQaXCIyA0zh68sFMu2GquPuqKobPn6P1Daio7IeLbrgOBx17FOINUTN5wkG7QVtOJeJ52nahrZuvFcAODLyyvOr1ULj02ES0gYsyJxjYQcsBFrbkrsAr/3hWCl3W19TC5XbB4/HK8eFeo/DWAJDNZJDLZBEMl+CgY47CSeecjT79+yMejToR/46CYeQD4YgWi9a/MWVE3+NsG7df3szFn2YNCBpwp57PHWtw56AHz0Y7E9zC4vYg97PPvPRiTD75RHw24x3MfP9DfLNypcx4NIqevhUoSx5Nk8Fw0Ijh2PPAA3DAkZMweNRIyaJ0jL9jQRvW8zmx6UIbt7GVdTd5AcuqpwfD4UmJxqjjBXS0C6zrMhB4/T7ZIahetw7rVq1GzYaNUkykZzYINbdH2fiTrn7/IUPk0JQ/yLbpGaSTKekb6Kz5O3j2Lwlr8Wh0xpSRfSZvOfsTWwX57BFCgX6zns/PcByAjoVsc0lCUFZmPK6D+wwYgP5Dh27VObcnggFODoDM8MtmsmisbxAZqJoTcuqU3F/GWqDf3Nzsb71la0w1DO10c0fg+ZJI6cmN9XV5xczCcNCJgbEemwG0JaQJijPbdyYMXc+XlJZpjQ31L0wZ0ecU26a3fF+z23xzza1D5dVlVVenkolvaW63S8/lrI1aBx0NxxgcdCi4z+d2I5VMpKHrV9OWb25hTdms33WLuU5Qp4zqtyidSt4TCkc03TCcvkwOHHQD0FZps7Rd2jBt2bLprdDijM5RY9o0qH33gzul1s3y+Hy7puJx3Soe6sCBgyKEoeu6LxhUM6nUAp9eNrHqM2RPOw06s/6ae3+LxiwfOA2YPEJJ5fT8xXKaSNM4ivSShaoDB90OTTZKm6Xt0oZbMn6i1dmcQYPp0w3XiSP6zojHYveEy8pceV23Otg7D0cGjg4YRSQD2iZtlLZKm6XtNhf42+4BgJg0CXnGFNbVrbqhobbuk2A44mKEcVufc+DAwc4DbZK2SRulrdJmabvb+tw2BwC6D4wgXrTvvlnVUM7IZtL1bq/XzOpw4MBB18MwWOpboW3SRmmrtNnWXH8b2xXQYwSR+4jHjyhbkUzGzna5Paqiqr2jhI0DB8UMRutVVadN0jZpo7TVlqL+W2KH9vWnG4ZrsqLkXlqy4erSPv3uitbVsqmgu80X78CBg/YiGy4rd9dXb7zmxNGVd9s2ur0f3uHEHvsPvLy85uGyivJL6mpqsgoUZxBw4GAnw4CRLauocNfV1D5ywoiKS3fU+Ns0AEh+AKBKqvCK2mdCkcgZjXV1WUVxBgEHDnYWDMPIlpSVuWMNDc9OGV5+Jt3+01jkczvW/YVoU2ovBwEzPsjaAXUvR/qUTqmrqnEGAQcOdpLxl/WtcDdU178yZUTZCTzlZ1b93THjJ9qU1WftDMhAUNOQ+n5jfeNLJaVlbrokbfk+Bw4cbB9oY7Q12hxtz8rzbzXZpzW063CPXViQP7+you6ZcHnpGQ01tVnWIHQKijpw0HGQ03mKkotUlLujtfXPThleduaWNtgWtCuvn3/4JsNgP0GVF9RQV/9ISVm5m88bTp6AAwcdAtoSbYq2RRujrdHmaHvtMX6iQ473Wm6Iwr3HV1bUXe31+e7KZlnvLePUEXDgoJ0Zfi6PR3O7PUinUtdMGV52Nw1/exN9toWOO99vGMpUa3fgxaW1J3r97qfcbk95rLEhp5rtxhw4cLAD0A09FyqJuLLZTG06mT33pFHlL0lhD7bz6wDjJzruaK+iGPbhIV5oKp44IJNOfVBa0cfFcYzHFDvsbzlw0INhiK0YOm2HNkRbok01He7pIOMnOvxs/+TJSu6m6dNdJ4+pXPLR4pmTYtHo3W6vX/UGgypHNAYHuvrUlPNwZFCUOmAYnClztBXaDG2HNkRbok3RtjraXjutxFfhOuWVldXHuNy+e72BwIRYfR0LFTqxAQcOtljrq5qmhUrLkE4k5uSyqSunDOvzZmF8DZ2ATqvuwwtmrTu6LbyRhhVzDkw0Nt7m8rgTodIyTXYJnDJjDno7DIPH7XXaBG2DNkJboc3QdmhDnWX8xE4p8llYkfSFlTXjPZrrFk1znaq5XIg3RtmjnAVHnarDDnqX4QNKsCSssg16Pp/7Vyafu+nkYRVz+XJLVXw7Gjuvyq9hKNNnzNAmT54s65iXV9Yfranq1ZpLO4YDQSIa5TqIr7FxoVN92EGPg2GGv/IK4AqEwxDDz+XfzOv63ScMK32L75nOtf6kSR0a6GsNO93QGBsYPw3K6aebo9srK+uPUTX153o+f3wgVKKwF3wum8lLSzKFSxRnMHDQnWGwF4rO/7ncHs0fDCERazRUTXtNz+sPThlW+ibfNXWqoc09DUZnuvvNoctm2i1v+PWV9fvkFeUCwzC+GwiF+nJ0TCXi0PW8DAYGwKwnxzNw0D3SdmEavapqmi8QhHi5sViVoijPaYbx5HHDSr9obkLc2ehyg+JAcNppslsgA8Gr30T7qnmcohvG6YahH+4PlXhYgjCdTCKfzeoGjzxKx2MOBpSzMyg46EoYXMpLayf2dlLYB9btVr1+P40fyVhjRlHUd1VFmapreP7bA8NV5qcMdVoXGn7RDAA2RCCAUhj4eGVNw1jVUCbrhn6ins8f5PUHytlUk95BJpOW/nJ6PmcoMAcPMiHDgmH1N3Q8Bgcdo5uGqU9mETwJWpsn81RVcykut9tq8e6Sfo/pZKJW1bSPVEV9SVeM6VMGR9icQ2Cd22+a8LoaRTMA2KD3NGPGDG3SpEn5wlznN9c0VGQMdU8Y+UMMw9gfujHeUIwBbrfXR8ELK7ouzRDZZpsU5fMdnjfhoBdC00z90jRNWpuzmanoVy6HbDadUgxlHVRlrqIon0LRPvAo+pfHDI7UbEuniwFFl6NvCShnr48mAWrVtGmGJdC3rQdeXGsEVKVxWC6bGZvLZMZBVUcCxmDD0AfoeaM/FMOlQOnT1ffjoPsjl89Ww1Byup5fr+TUdYCyBrq+DCrmKx73It0oWXnSICVR+JmpU6dqfU87TZlhVunhbF+Us9H/A8r48PA9o+5yAAAAAElFTkSuQmCC">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
color-scheme:light dark;
/* ===== 主题：卡提希娅 =====
   依据官方 KV 实测：深蓝紫底(H216-230) + 亮青蓝(H202-217) + 银白 + 淡紫(H240-249)。
   金色来自立绘（发色/饰件），在官方 KV 里已不是主角，所以只用做细线。 */
--azure:#246499;--azure-strong:#1e5380;--azure-bright:#3d94d6;--azure-deep:#205588;
--azure-soft:rgba(28,94,155,.08);--azure-line:rgba(28,94,155,.26);
--gold:#8a6d1f;--gold-bright:#b8860b;--gold-pale:#f8e8c8;
--gold-soft:rgba(138,109,31,.10);--gold-line:rgba(138,109,31,.26);
--blue:var(--azure);--blue-soft:var(--azure-soft);
/* 面：按官方 KV 的蓝紫调偏（H225 系） */
--bg:#fff;--bg-2:#f9fafc;--bg-3:#f2f4f9;--bg-4:#e9ecf5;--hover:#f2f4f9;
--line:#e9ecf5;--line-2:#dde1ee;--line-3:#cfd5e6;
--text:#0a0e1a;--text-2:#4e556b;--text-3:#7b8299;--text-4:#c3c9d9;
/* 状态色：当文字也达标，且色相离主色 ≥25° */
--green:#15803d;--green-soft:rgba(21,128,61,.10);
--red:#dc2626;--red-soft:rgba(220,38,38,.10);
--amber:#b45309;--amber-soft:rgba(180,83,9,.10);
--purple:#6d4fc4;--purple-soft:rgba(109,79,196,.10);
/* 形 */
--radius:11px;--radius-s:8px;--radius-xs:6px;
--shadow-xs:0 1px 2px rgba(10,14,26,.04);
--shadow-s:0 1px 2px rgba(10,14,26,.04);
--shadow-m:0 1px 3px rgba(10,14,26,.05),0 1px 2px rgba(10,14,26,.03);
--shadow-l:0 4px 14px rgba(10,14,26,.06);
--shadow-xl:0 10px 28px rgba(10,14,26,.08);
/* 动效：飘带是飘的，不是弹的 */
--ease:cubic-bezier(.22,.61,.36,1);
--ease-in:cubic-bezier(.4,0,1,1);
/* 其余 */
--side-bg-1:#fafbfd;--side-bg-2:#f5f7fb;
--card-bg:#fff;
--log-bg-1:#f9fafb;--log-bg-2:#f6f7f9;
--btn-primary-bg:var(--azure);
--btn-primary-bg-h:var(--azure-strong);
--btn-primary-border:var(--azure);
--btn-primary-border-h:var(--azure-strong);
--btn-primary-shadow:inset 0 1px 0 rgba(255,255,255,.18);
--btn-primary-shadow-h:inset 0 1px 0 rgba(255,255,255,.24);
--scrollbar:rgba(10,14,26,.14);
--scrollbar-h:rgba(10,14,26,.28);
--nav-hover:rgba(28,94,155,.055);
--log-line-hover:rgba(10,14,26,.03);
--api-tab-hover:rgba(255,255,255,.6);
--brand-border:var(--gold-line);
--toast-border:transparent;
--selection:rgba(28,94,155,.18);
--on-accent:#fff;
    /* 常暗元素（.toast）上的字，永远白，不跟主题翻 */
    --on-dark:#fff;
/* --aero 系是品牌主色的别名。之前只有 var(--aero) 的引用、没有定义，
   未定义的自定义属性会让**整条声明**在计算时失效 ——
   侧栏选中项的主色文字、标题下划线、竖条发光全都因此没了。
   这里指向 --azure 系，深色模式下 --azure 被覆盖时会自动跟着变。 */
--aero:var(--azure);
--aero-soft:var(--azure-soft);
--aero-line:var(--azure-line);
/* 常暗元素（toast）：不跟随主题，是刻意的 */
--dark-1:#1c1c1e;--dark-2:#09090b;
/* 毛玻璃（只在开了壁纸时被注入的规则用到）*/
--glass-2:rgba(255,255,255,.86);
--glass-line:rgba(255,255,255,.62);
--glass-hi:inset 0 1px 0 rgba(255,255,255,.75);
--glass-shadow:0 8px 32px rgba(8,14,32,.14);
--panel-blur:18px;
}
@media (prefers-color-scheme: dark){
:root{
/* 底 = 官方 KV 的深蓝紫族（H225），不是纯黑也不是紫黑 */
--bg:#0d1428;--bg-2:#141d38;--bg-3:#1c2748;--bg-4:#243158;--hover:#1c2748;
--line:#1c2748;--line-2:#28345c;--line-3:#364268;
/* 正文取她的银白发 */
--text:#d0e0f0;--text-2:#9aa8c8;--text-3:#7a88a8;--text-4:#5a6684;
--azure:#6db8ee;--azure-strong:#8ecdf2;--azure-bright:#b0d8f0;--azure-deep:#4da6e8;
--azure-soft:rgba(109,184,238,.14);--azure-line:rgba(109,184,238,.34);
--gold:#d4b46a;--gold-bright:#e8cf8a;--gold-pale:#3a3222;
--gold-soft:rgba(212,180,106,.12);--gold-line:rgba(212,180,106,.28);
--blue:var(--azure);--blue-soft:var(--azure-soft);
--green:#4ade80;--green-soft:rgba(74,222,128,.15);
--red:#ff7b7b;--red-soft:rgba(255,123,123,.15);
--amber:#ffc043;--amber-soft:rgba(255,192,67,.15);
--purple:#a99ae8;--purple-soft:rgba(169,154,232,.15);
--shadow-xs:0 1px 2px rgba(0,0,0,.32);
--shadow-s:0 1px 2px rgba(0,0,0,.32);
--shadow-m:0 1px 3px rgba(0,0,0,.38),0 1px 2px rgba(0,0,0,.26);
--shadow-l:0 4px 14px rgba(0,0,0,.42);
--shadow-xl:0 10px 28px rgba(0,0,0,.52);
--side-bg-1:#111932;--side-bg-2:#0d1428;
--card-bg:#141d38;
--log-bg-1:#111932;--log-bg-2:#0d1428;
--btn-primary-bg:var(--azure);
--btn-primary-bg-h:var(--azure-strong);
--btn-primary-border:var(--azure);
--btn-primary-border-h:var(--azure-strong);
--btn-primary-shadow:inset 0 1px 0 rgba(255,255,255,.12);
--btn-primary-shadow-h:inset 0 1px 0 rgba(255,255,255,.16);
--scrollbar:rgba(208,224,240,.20);
--scrollbar-h:rgba(208,224,240,.32);
--nav-hover:rgba(109,184,238,.10);
--log-line-hover:rgba(255,255,255,.04);
--api-tab-hover:rgba(255,255,255,.08);
--brand-border:var(--gold-line);
--toast-border:rgba(255,255,255,.12);
--selection:rgba(109,184,238,.28);
/* 深色下主色翻亮（azure #6db8ee / green #4ade80 / red #ff7b7b），
   白字压在上面只有 1.7-2.2:1，必须翻成深色。
   .toast 除外 —— 它底永远是暗的，用 --on-dark。 */
--on-accent:#0b0a0d;
--on-dark:#fff;
--dark-1:#1c1c1e;--dark-2:#09090b;
--glass-2:rgba(18,26,50,.68);
--glass-line:rgba(190,214,240,.11);
--glass-hi:inset 0 1px 0 rgba(190,214,240,.10);
--glass-shadow:0 8px 32px rgba(0,0,0,.46);
--panel-blur:18px;
}
}
html,body{height:100%;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}
body{background:var(--bg);color:var(--text);font-family:"Segoe UI Variable Text","Segoe UI",-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","HarmonyOS Sans SC","Noto Sans SC","Microsoft YaHei UI","Microsoft YaHei",Roboto,sans-serif;font-size:14px;line-height:1.6;overflow:hidden;font-feature-settings:"tnum","cv02","cv03","cv04","cv11"}
button{font-family:inherit;cursor:pointer;border:none;background:none;color:inherit;font-size:inherit;padding:0;letter-spacing:inherit}
input,select,textarea{font-family:inherit;font-size:inherit;color:inherit;letter-spacing:inherit}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--scrollbar);border-radius:6px;border:2px solid transparent;background-clip:padding-box}
::-webkit-scrollbar-thumb:hover{background:var(--scrollbar-h);background-clip:padding-box;border:2px solid transparent}
::selection{background:var(--selection)}
.app{display:flex;height:100vh;background:var(--bg)}
.side{width:236px;flex-shrink:0;background:linear-gradient(180deg,var(--side-bg-1) 0%,var(--side-bg-2) 100%);border-right:1px solid var(--line);display:flex;flex-direction:column;padding:22px 14px 16px}
.brand{display:flex;align-items:center;gap:12px;padding:0 8px 24px;margin-bottom:4px;position:relative}
.brand::after{content:"";position:absolute;left:15px;right:15px;bottom:0;height:1px;background:linear-gradient(90deg,transparent,var(--gold-line) 22%,var(--gold-line) 78%,transparent)}
.brand-mark{width:36px;height:36px;border-radius:11px;background:linear-gradient(135deg,var(--azure) 0%,var(--azure-strong) 100%);border:1px solid var(--brand-border);display:flex;align-items:center;justify-content:center;flex-shrink:0;box-shadow:var(--shadow-m)}
.brand-mark svg{width:18px;height:18px;color:var(--on-accent)}
.brand-info{flex:1;min-width:0}
.brand-title{font-size:15px;font-weight:600;letter-spacing:-.015em;color:var(--text);line-height:1.2}
.brand-sub{font-size:10px;color:var(--text-3);letter-spacing:.14em;font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;margin-top:3px;font-weight:500}
.nav{flex:1;display:flex;flex-direction:column;gap:1px;overflow-y:auto;padding-right:2px;position:relative}
.nav::before{content:"";position:absolute;left:50%;top:-2.5px;width:5px;height:5px;margin-left:-2.5px;background:var(--gold-bright);transform:rotate(45deg)}
.nav-section{font-size:10px;font-weight:600;color:var(--text-3);letter-spacing:.14em;text-transform:uppercase;padding:18px 10px 8px;font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
.nav-section:first-child{padding-top:6px}
.nav-item{display:flex;align-items:center;gap:11px;padding:8px 10px;border-radius:9px;color:var(--text-2);font-size:13px;font-weight:450;transition:all .14s ease;width:100%;text-align:left;position:relative}
.nav-item:hover{background:var(--nav-hover);color:var(--text)}
.nav-item.on{background:var(--card-bg);color:var(--text);font-weight:550;box-shadow:0 0 0 1px var(--line)}
.nav-item.on::before{content:"";position:absolute;left:-14px;top:50%;transform:translateY(-50%);width:2px;height:20px;background:var(--azure);border-radius:0 3px 3px 0}
.nav-item .ico{width:16px;height:16px;flex-shrink:0;opacity:.7;transition:opacity .14s}
.nav-item:hover .ico,.nav-item.on .ico{opacity:1}
.nav-item .badge{margin-left:auto;font-size:10px;padding:1px 7px;border-radius:10px;font-weight:600;min-width:20px;text-align:center;line-height:1.6;background:var(--aero-soft);color:var(--aero);border:1px solid var(--aero-line)}
/* 只有"待你处理"的才用红色 —— 记忆条数/知识库条数不是错误 */
.nav-item .badge.alert{background:var(--red);color:var(--on-accent);border-color:var(--red);box-shadow:0 1px 3px rgba(220,38,38,.34)}
.nav-item .badge:empty{display:none}
.side-foot{padding:14px 10px 2px;border-top:1px solid var(--line);margin-top:14px;font-size:11px;color:var(--text-3);font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;display:flex;align-items:center;gap:8px;font-weight:500;letter-spacing:.05em}
.dot-live{width:6px;height:6px;border-radius:50%;background:var(--green);animation:pulse 2.4s infinite;box-shadow:0 0 0 3px var(--green-soft)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.main{flex:1;display:flex;flex-direction:column;min-width:0;overflow:hidden}
.topbar{height:72px;flex-shrink:0;border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 32px;gap:22px;background:var(--card-bg)}
.topbar h1{font-size:19px;font-weight:600;color:var(--text);letter-spacing:-.015em}
.topbar .spacer{flex:1}
.top-metrics{display:flex;gap:8px;align-items:center}
.metric{display:flex;flex-direction:column;align-items:flex-end;padding:5px 12px;border-radius:9px;border:1px solid var(--line);background:var(--bg-2);min-width:66px;transition:all .15s ease}
.metric:hover{border-color:var(--line-2)}
.metric .metric-l{font-size:10px;color:var(--text-3);letter-spacing:.06em;text-transform:uppercase;font-weight:500;line-height:1}
.metric .val{color:var(--text);font-weight:600;font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;font-size:13px;line-height:1.4;margin-top:3px}
.clock{font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;font-size:12px;color:var(--text-2);padding:6px 12px;border:1px solid var(--line);border-radius:9px;background:var(--bg-2);font-weight:500;letter-spacing:.02em}
.content{flex:1;overflow-y:auto;padding:30px 32px 50px}
.page{display:none}
.page.on{display:block}
/* 淡入只能加在 .card 的**子元素**上，不能加在 .page 或 .card 上：
   .page 是 .card 的祖先 —— 祖先带 transform 会重建合成层、
   祖先 opacity<1 会让它变成"组"，两种情况后代的 backdrop-filter
   都没有可糊的背景，面板会闪一下露出清晰壁纸。
   动画落在子元素上，玻璃层本身始终不动。*/
.page.on>.card>*{animation:fadeIn .22s cubic-bezier(.16,1,.3,1)}
/* 切页动画只能动 opacity。以前带 transform:translateY(6px) ——
   .page 是 .card 的祖先，祖先做 transform 动画时浏览器要重建合成层，
   后代 .card 上的 backdrop-filter 在动画期间失效，面板会闪一下
   （露出没糊的壁纸）。去掉 transform 就没这个问题了。*/
.card{transform:translateZ(0)}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.card{background:var(--card-bg);border:1px solid var(--line);border-radius:var(--radius);padding:26px;margin-bottom:18px;transition:border-color .16s var(--ease)}
.card:hover{border-color:var(--azure-line)}
.card-h{display:flex;align-items:center;gap:10px;padding-bottom:18px;margin-bottom:22px;border-bottom:1px solid var(--line)}
.card-h h3{font-size:13.5px;font-weight:600;color:var(--text);letter-spacing:-.005em}
.card-h .en{font-size:10px;color:var(--text-4);letter-spacing:.14em;text-transform:uppercase;font-weight:500;font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
.card-h .right{margin-left:auto;display:flex;gap:8px;align-items:center}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:12px}
.stat-box{background:var(--card-bg);border:1px solid var(--line);border-radius:var(--radius);padding:14px 24px;transition:all .2s cubic-bezier(.16,1,.3,1);box-shadow:var(--shadow-s);position:relative;overflow:hidden}
.stat-box::after{content:"";position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(127,127,127,.15),transparent)}
.stat-box:hover{border-color:var(--line-3)}
.stat-box .v{font-size:36px;font-weight:600;letter-spacing:-.04em;line-height:1.05;background:linear-gradient(180deg,var(--text) 0%,var(--text-2) 100%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;font-variant-numeric:tabular-nums;display:inline-block}
.stat-box .l{font-size:11px;color:var(--text-3);margin-top:10px;letter-spacing:.07em;text-transform:uppercase;font-weight:500}
.stat-box .l{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{text-align:left;padding:0 12px 12px;color:var(--text-3);font-weight:500;font-size:10px;letter-spacing:.11em;text-transform:uppercase;border-bottom:1px solid var(--line);font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
.tbl td{padding:13px 12px;border-bottom:1px solid var(--line);color:var(--text-2);vertical-align:middle}
.tbl tr:last-child td{border-bottom:none}
.tbl tbody tr{transition:background .12s ease}
.tbl tbody tr:hover td{background:var(--bg-2)}
.tbl td.mono{font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;color:var(--text);font-size:12px}
.tbl td.strong{color:var(--text);font-weight:500}
.tbl td.rank{font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;font-weight:600;color:var(--text);width:44px;font-size:13px}
.tbl td.points{font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;font-weight:600;color:var(--amber);text-align:right}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 15px;border-radius:8px;font-size:12.5px;font-weight:500;border:1px solid var(--line-2);background:var(--card-bg);color:var(--text-2);transition:all .16s cubic-bezier(.16,1,.3,1);white-space:nowrap;letter-spacing:-.005em;box-shadow:var(--shadow-xs)}
.btn:hover{color:var(--text);border-color:var(--line-3);background:var(--bg-2);box-shadow:var(--shadow-s)}
.btn:active{transform:scale(.97);box-shadow:inset 0 1px 2px rgba(0,0,0,.06)}
.btn.primary{background:var(--btn-primary-bg);color:var(--on-accent);border-color:var(--btn-primary-border);box-shadow:var(--btn-primary-shadow)}
.btn.primary:hover{background:var(--btn-primary-bg-h);border-color:var(--btn-primary-border-h);box-shadow:var(--btn-primary-shadow-h)}
.btn.green{background:var(--green);color:var(--on-accent);border-color:var(--green);box-shadow:0 1px 2px rgba(16,185,129,.2)}
.btn.green:hover{filter:brightness(1.08)}
.btn.danger{color:var(--red);border-color:rgba(239,68,68,.32);background:var(--card-bg)}
.btn.danger:hover{background:var(--red-soft);border-color:rgba(239,68,68,.5);color:var(--red)}
.win-new{border-style:dashed;opacity:.7}
.win-new:hover{border-color:var(--line-3);opacity:1}
.win-new .win-tile-name{color:var(--text-3)}
/* 左侧文件夹树 */
.kb-tree-row{display:flex;align-items:center;gap:6px;padding:5px 8px;border-radius:6px;cursor:pointer;color:var(--text-2)}
.kb-tree-row:hover{background:var(--card-bg);color:var(--text)}
.kb-tree-row.on{background:var(--btn-primary-bg);color:var(--on-accent)}
.kb-tree-act{padding:2px 7px!important;font-size:11px!important;line-height:1.5}
.kb-tree-row.on .kb-tree-act{color:var(--text)!important;background:var(--card-bg);border-color:transparent}
.btn.ghost{border-color:transparent;color:var(--text-3);box-shadow:none;background:transparent}
.btn.ghost:hover{color:var(--text);background:var(--hover);box-shadow:none}
.btn.sm{padding:5px 11px;font-size:11.5px;border-radius:7px}
.logs{background:linear-gradient(180deg,var(--log-bg-1) 0%,var(--log-bg-2) 100%);border:1px solid var(--line);border-radius:var(--radius-s);overflow-y:auto;padding:10px 6px;font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;font-size:12px;line-height:1.9;height:calc(100vh - 420px);min-height:340px}
.log-line{display:flex;gap:10px;padding:3px 12px;align-items:baseline;border-radius:6px;transition:background .1s}
.log-line:hover{background:var(--log-line-hover)}
.log-t{color:var(--text-4);flex-shrink:0;font-size:11px;font-variant-numeric:tabular-nums;letter-spacing:.01em}
.log-tag{flex-shrink:0;padding:0 8px;border-radius:5px;font-size:10px;font-weight:600;height:18px;line-height:18px;background:var(--bg-4);color:var(--text-2);letter-spacing:.05em;font-family:inherit;text-align:center;min-width:44px}
.log-tag.ok{background:var(--green-soft);color:var(--green)}
.log-tag.warn{background:var(--amber-soft);color:var(--amber)}
.log-tag.err{background:var(--red-soft);color:var(--red)}
.log-tag.msg{background:var(--blue-soft);color:var(--blue)}
.log-tag.reply{background:var(--purple-soft);color:var(--purple)}
.log-tag.mem{background:var(--amber-soft);color:var(--amber)}
.log-tag.join,.log-tag.leave{background:var(--green-soft);color:var(--green)}
.log-tag.verify{background:var(--blue-soft);color:var(--blue)}
.log-tag.block{background:var(--red-soft);color:var(--red)}
.log-tag.info{background:var(--bg-4);color:var(--text-2)}
.log-tag.req{background:var(--blue-soft);color:var(--blue)}
.log-msg{flex:1;color:var(--text-2);word-break:break-all;white-space:pre-wrap;font-size:12px;line-height:1.75}
.log-line.ok .log-msg{color:var(--green)}
.log-line.err .log-msg{color:var(--red)}
.log-line.warn .log-msg{color:var(--amber)}
.group-block{border:1px solid var(--line);border-radius:var(--radius-s);overflow:hidden;margin-bottom:14px;background:var(--card-bg);box-shadow:var(--shadow-xs);transition:all .15s}
.group-block:hover{box-shadow:var(--shadow-s)}
.group-head{padding:14px 18px;background:var(--bg-2);display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);font-size:13px}
.gid{font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;color:var(--text);font-weight:600;font-size:12.5px;letter-spacing:.01em}
/* === 记忆/记录页组件 === */
.page-desc{font-size:12.5px;color:var(--text-2);line-height:1.9;margin-bottom:16px}
.page-desc code{background:var(--bg-3);padding:1px 6px;border-radius:5px;font-size:11.5px}
.sub-panel{background:var(--bg-2);border:1px solid var(--line);border-radius:var(--radius-s);padding:16px;margin-bottom:18px}
.sub-panel-h{font-size:12.5px;font-weight:600;color:var(--text);margin-bottom:13px;display:flex;align-items:center;gap:8px}
.grid-f{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:13px}
.fld{display:flex;flex-direction:column;gap:6px;min-width:0}
.fld>label{font-size:11.5px;color:var(--text-3);font-weight:500}
.inp,.sel{width:100%;background:var(--card-bg);border:1px solid var(--line-2);border-radius:8px;padding:9px 12px;font-size:13px;color:var(--text);outline:none;box-shadow:var(--shadow-xs);font-family:inherit;transition:border-color .14s,box-shadow .14s}
.inp:focus,.sel:focus{border-color:var(--blue);box-shadow:0 0 0 3px var(--blue-soft)}
.inp::placeholder{color:var(--text-4)}
textarea.inp{resize:vertical;line-height:1.6}
.seg{display:inline-flex;background:var(--bg-3);border-radius:9px;padding:3px;gap:2px}
.seg button{padding:6px 15px;font-size:12.5px;border:none;border-radius:7px;background:transparent;color:var(--text-2);cursor:pointer;font-weight:500;font-family:inherit;transition:all .14s}
.seg button:hover{color:var(--text)}
.seg button.on{background:var(--card-bg);color:var(--text);font-weight:600;box-shadow:var(--shadow-xs)}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:18px}
.toolbar .grow{flex:1;min-width:180px}
.bar-stat{font-size:12px;color:var(--text-3);margin-bottom:12px}
.bar-stat b{color:var(--text-2);font-weight:600}
/* ========== 插件页 ========== */
.inline-code{font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;font-size:11.5px;
  background:var(--bg-3);border:1px solid var(--line);border-radius:4px;
  padding:1px 5px;color:var(--text-2);white-space:nowrap}
.plug-empty{padding:26px 20px;text-align:center;border:1px dashed var(--line-2);
  border-radius:var(--radius-s);background:var(--bg-2)}
.plug-card{border:1px solid var(--line);border-radius:var(--radius-s);
  background:var(--bg-2);padding:13px 15px;margin-bottom:10px}
.plug-card.bad{border-color:var(--red);background:var(--red-soft)}
.plug-head{display:flex;align-items:flex-start;gap:10px;justify-content:space-between}
.plug-title{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0}
.plug-title b{font-size:14px;color:var(--text);font-weight:600}
.plug-ver{font-size:11px;color:var(--text-3);
  font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
.plug-badge{font-size:10.5px;padding:2px 7px;border-radius:20px;font-weight:600;flex-shrink:0}

/* 插件页面的导航项：只用来让 setPage 找得到，不显示在侧边栏 */
.nav-item.plug-nav-hidden{display:none}
/* 插件页面顶部的返回条 */
.plug-page-bar{display:flex;align-items:center;gap:10px;margin-bottom:16px;
  padding-bottom:12px;border-bottom:1px solid var(--line)}
.plug-page-bar .ppb-title{font-size:12.5px;color:var(--text-3)}

/* 插件配置小窗 */
.modal-mask{position:fixed;inset:0;background:rgba(9,9,11,.42);
  -webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px);
  display:flex;align-items:center;justify-content:center;z-index:200;padding:24px;
  animation:fadeIn .14s ease}
.modal-mask[hidden]{display:none}
.modal{background:var(--card-bg);border:1px solid var(--line-2);
  border-radius:var(--radius);box-shadow:var(--shadow-xl);
  width:min(660px,100%);max-height:86vh;display:flex;flex-direction:column;
  animation:popIn .18s cubic-bezier(.16,1,.3,1)}
.modal-h{display:flex;align-items:center;gap:10px;padding:18px 22px 15px;
  border-bottom:1px solid var(--line);position:relative}
.modal-h::before{content:"";position:absolute;left:0;top:18px;bottom:15px;width:2px;
  background:var(--azure);border-radius:0 3px 3px 0}
.modal-h h3{font-size:15px;font-weight:600;letter-spacing:-.01em}
.modal-h .en{font-size:10.5px;color:var(--text-3);letter-spacing:.08em;text-transform:uppercase}
.modal-x{margin-left:auto;width:30px;height:30px;border-radius:8px;
  border:1px solid var(--line-2);background:var(--bg-2);cursor:pointer;
  color:var(--text-2);font-size:14px;line-height:1;display:flex;
  align-items:center;justify-content:center;font-family:inherit}
.modal-x:hover{background:var(--bg-3);color:var(--text)}
.modal-b{padding:22px;overflow-y:auto;flex:1}
.modal-b .cfg-group-title{margin-bottom:16px}
.modal-b .cfg-group-title:not(:first-child){margin-top:22px}
.modal-f{display:flex;justify-content:flex-end;gap:10px;padding:15px 22px;
  border-top:1px solid var(--line);background:var(--bg-2);
  border-radius:0 0 var(--radius) var(--radius)}
/* 品牌区后面的版本号 */
.brand-ver{margin-left:7px;font-size:10.5px;font-weight:500;color:var(--text-3);
  font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;letter-spacing:0;
  vertical-align:1px;white-space:nowrap}
.brand-ver .ver-cur{cursor:pointer;border-bottom:1px dashed transparent}
.brand-ver .ver-cur:hover{color:var(--text-2);border-bottom-color:var(--text-4)}
/* 有新版本时的小标签 */
.ver-new{margin-left:6px;padding:1px 7px;border-radius:20px;font-size:10px;
  font-weight:600;border:none;cursor:pointer;font-family:inherit;
  background:var(--green-soft);color:var(--green);
  animation:verPulse 2.2s ease-in-out infinite}
.ver-new:hover{background:var(--green);color:var(--on-accent);animation:none}
@keyframes verPulse{0%,100%{opacity:1}50%{opacity:.55}}
/* 更新说明里的正文排版 */
.upd-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:4px}
.upd-cur{font-size:12.5px;color:var(--text-3);
  font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
.upd-arrow{color:var(--text-4)}
.upd-same{font-size:12px;color:var(--text-3);padding:1px 8px;border-radius:20px;
  background:var(--green-soft);color:var(--green);font-weight:500}
.upd-label{font-size:12px;color:var(--text-3);margin:2px 0 7px}
.upd-new{font-size:13px;font-weight:600;color:var(--green);
  font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
.upd-time{font-size:11.5px;color:var(--text-3);margin-bottom:14px}
.upd-notes{font-size:13px;line-height:1.72;color:var(--text-2);
  background:var(--bg-2);border:1px solid var(--line);
  border-radius:var(--radius-s);padding:14px 16px;max-height:44vh;overflow-y:auto;
  white-space:pre-wrap;word-break:break-word}
.upd-notes code{font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  font-size:12px;background:var(--bg-4);padding:1px 5px;border-radius:4px}
.upd-notes b{color:var(--text)}
/* markdown 渲染出来的元素 */
.upd-notes .md-h1{font-size:14px;display:block;margin:2px 0 6px}
.upd-notes .md-h2{font-size:13.5px;display:block;margin:12px 0 6px}
.upd-notes .md-h3{font-size:12.5px;display:block;margin:12px 0 5px;
  color:var(--text-3);letter-spacing:.02em}
.upd-notes a{color:var(--blue);text-decoration:none;border-bottom:1px solid transparent}
.upd-notes a:hover{border-bottom-color:var(--blue)}
.upd-notes .md-li{display:block;position:relative;padding-left:14px;margin:3px 0}
.upd-notes .md-li::before{content:"·";position:absolute;left:4px;
  color:var(--text-4);font-weight:700}
.upd-notes .md-quote{display:block;padding:2px 0 2px 10px;margin:6px 0;
  border-left:2px solid var(--line-3);color:var(--text-3)}
.upd-notes .md-pre{background:var(--bg-4);border:1px solid var(--line);
  border-radius:var(--radius-xs);padding:10px 12px;margin:8px 0;overflow-x:auto;
  font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;font-size:11.5px;
  line-height:1.6;white-space:pre;color:var(--text-2)}
.upd-notes hr{border:none;border-top:1px solid var(--line);margin:14px 0}
.upd-notes .md-tb{border-collapse:collapse;margin:9px 0;font-size:12px;
  width:100%;table-layout:auto}
.upd-notes .md-tb th,.upd-notes .md-tb td{border:1px solid var(--line);
  padding:6px 9px;text-align:left;vertical-align:top}
.upd-notes .md-tb th{background:var(--bg-4);color:var(--text);font-weight:600}
.upd-notes .md-tb td{color:var(--text-2)}
/* 选择下载线路 */
.upd-srcrow{margin:14px 0 10px}
.upd-srclb{font-size:12.5px;color:var(--text-2);margin-bottom:9px;font-weight:500}
.upd-lines{display:grid;grid-template-columns:1fr 1fr;gap:9px}
.upd-line{display:flex;align-items:center;gap:9px;padding:11px 13px;
  border:1px solid var(--line-2);border-radius:var(--radius-s);
  background:var(--bg-2);cursor:pointer;font-family:inherit;font-size:12.5px;
  color:var(--text-2);text-align:left;transition:border-color .15s,background .15s}
.upd-line:hover{border-color:var(--line-3)}
.upd-line.on{border-color:var(--blue);background:var(--blue-soft);color:var(--text)}
.upd-line .rd{width:15px;height:15px;border-radius:50%;flex-shrink:0;
  border:1.5px solid var(--line-3);position:relative;transition:border-color .15s}
.upd-line.on .rd{border-color:var(--blue)}
.upd-line.on .rd::after{content:"";position:absolute;inset:3px;border-radius:50%;
  background:var(--blue)}
.upd-line .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.upd-line .ms{flex-shrink:0;padding:3px 9px;border-radius:5px;font-size:11px;
  font-weight:700;font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  background:var(--green-soft);color:var(--green);min-width:52px;text-align:center}
.upd-line .ms.mid{background:var(--amber-soft);color:var(--amber)}
.upd-line .ms.bad{background:var(--red-soft);color:var(--red)}
.upd-line .ms.wait{background:var(--bg-4);color:var(--text-2);font-weight:500}
.upd-srchint{font-size:11px;color:var(--text-4);margin-top:9px}
/* 进度条 */
.upd-prog{margin-top:16px}
.upd-bar{position:relative;height:26px;border-radius:6px;background:var(--bg-4);
  overflow:hidden}
.upd-bar>i{display:block;height:100%;width:0;
  background:linear-gradient(90deg,var(--azure),var(--azure-bright));
  transition:width .25s var(--ease)}
.upd-bar>b{position:absolute;left:5px;top:50%;transform:translateY(-50%);
  font-size:11px;font-weight:700;color:var(--on-accent);
  font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  background:rgba(10,132,255,.92);padding:2px 7px;border-radius:4px;
  white-space:nowrap}
.upd-pgtx{display:flex;justify-content:space-between;gap:10px;margin-top:8px;
  font-size:11.5px;color:var(--text-3);
  font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
.upd-cancelrow{margin-top:11px;display:flex;justify-content:flex-end}
.upd-pgerr,.upd-pgok{margin-top:9px;font-size:12px;line-height:1.7;
  border-radius:var(--radius-xs);padding:9px 11px;word-break:break-all}
.upd-pgerr{color:var(--amber);background:var(--amber-soft)}
.upd-pgok{color:var(--text-2);background:var(--green-soft)}
.upd-pgok code{font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  font-size:11px;background:var(--bg-2);padding:1px 5px;border-radius:4px}
.upd-vf{color:var(--green);font-size:11px}
.upd-sha{margin-top:7px;font-size:11px;color:var(--text-3);word-break:break-all}
.upd-sha code{font-size:10.5px;background:var(--bg-2);padding:1px 5px;
  border-radius:4px;color:var(--text-2)}
.upd-shasrc{display:block;margin-top:3px;color:var(--text-4)}
.upd-files{margin-top:16px}
.upd-files .upd-ft{font-size:12px;color:var(--text-3);margin-bottom:8px}
.upd-file{display:flex;align-items:center;gap:10px;padding:9px 12px;
  border:1px solid var(--line);border-radius:var(--radius-xs);
  margin-bottom:7px;font-size:12.5px}
.upd-file .nm{font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  color:var(--text);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.upd-file .sz{color:var(--text-3);font-size:11.5px;flex-shrink:0}
.upd-dl{flex-shrink:0;padding:3px 12px;border-radius:var(--radius-xs);
  border:1px solid var(--line-3);background:var(--bg-2);color:var(--blue);
  font-size:12px;font-weight:600;cursor:pointer;font-family:inherit}
.upd-dl:hover{background:var(--blue-soft);border-color:var(--blue)}
.upd-dl:disabled{opacity:.6;cursor:default}
.upd-err{font-size:12.5px;color:var(--amber);margin-top:10px}
@keyframes popIn{from{opacity:0;transform:translateY(8px) scale(.985)}to{opacity:1;transform:none}}
.plug-badge.ok{background:var(--green-soft);color:var(--green)}
.plug-badge.off{background:var(--bg-4);color:var(--text-3)}
.plug-badge.err{background:var(--red-soft);color:var(--red)}
.plug-tag{font-size:10.5px;padding:2px 7px;border-radius:20px;
  background:var(--blue-soft);color:var(--blue)}
.plug-actions{flex-shrink:0}
.plug-desc{font-size:12.5px;color:var(--text-2);line-height:1.75;margin-top:7px;
  word-break:break-word}
.plug-meta{font-size:11.5px;color:var(--text-3);margin-top:6px;line-height:1.9;
  word-break:break-word}
.plug-meta .inline-code{margin:0 1px}
.plug-err{margin-top:9px;padding:8px 11px;border-radius:var(--radius-xs);
  background:var(--red-soft);color:var(--red);font-size:11.5px;line-height:1.7;
  font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;word-break:break-word}
/* 插件安装框 + 市场 */
.plug-install{margin-top:16px;padding:14px 15px;border:1px dashed var(--line-2);
  border-radius:var(--radius-s);background:var(--bg-2)}
.plug-install-h{font-size:12.5px;font-weight:600;color:var(--text-2);margin-bottom:9px}
.plug-install-row{display:flex;gap:9px}
.plug-install-row .inp{flex:1;min-width:0}
.plug-install-tip{font-size:11.5px;color:var(--text-3);line-height:1.85;margin-top:8px}
.plug-install-tip a{color:var(--blue);text-decoration:none}
.plug-badge.warn{background:var(--amber-soft);color:var(--amber)}
.plug-meta a{color:var(--blue);text-decoration:none}
.plug-meta a:hover{text-decoration:underline}
#plugSeg{margin-bottom:2px}
/* 原始发言（记忆页里的参考区） */
.raw-wrap{margin:10px 0 4px;border:1px solid var(--line);border-radius:var(--radius-s);background:var(--bg-2);padding:10px 13px;max-height:330px;overflow-y:auto}
.raw-row{display:flex;gap:10px;font-size:12px;line-height:1.75;padding:1px 0}
.raw-row.bot .raw-n{color:var(--blue)}
.raw-t{color:var(--text-4);flex-shrink:0;font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;font-size:11px;padding-top:1px}
.raw-n{color:var(--text-2);flex-shrink:0;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
.raw-x{color:var(--text);word-break:break-word;flex:1;padding-right:4px}
.raw-w{color:var(--text-4);font-size:10.5px;flex-shrink:0;padding-top:1px;margin-left:12px;padding-left:10px;border-left:1px solid var(--line-2)}
/* 聊天记录气泡 */
.cl-wrap{max-height:620px;overflow-y:auto;padding:6px 4px}
.cl-row{display:flex;margin-bottom:11px}
.cl-row.mine{justify-content:flex-end}
.cl-bubble{max-width:74%;border:1px solid var(--line);background:var(--card-bg);border-radius:var(--radius-s);padding:9px 13px;box-shadow:var(--shadow-xs)}
.cl-row.mine .cl-bubble{background:var(--blue-soft);border-color:rgba(0,102,255,.2)}
.cl-head{display:flex;gap:9px;align-items:baseline;font-size:11px;color:var(--text-4);margin-bottom:3px}
.cl-row.mine .cl-head{flex-direction:row-reverse}
.cl-name{font-weight:600;color:var(--text-2);font-size:11.5px}
.cl-body{font-size:13px;line-height:1.65;color:var(--text);white-space:pre-wrap;word-break:break-word}
.cl-day{text-align:center;font-size:11px;color:var(--text-4);margin:14px 0 10px}
.cl-day span{background:var(--bg-3);padding:3px 12px;border-radius:20px}
.mem-row{padding:14px 18px;border-top:1px solid var(--line);display:flex;gap:14px;align-items:flex-start;font-size:13px}
.mem-row:first-child{border-top:none}
.mem-row:hover{background:var(--bg-2)}
.mem-tag{font-size:10px;padding:3px 8px;border-radius:5px;background:var(--bg-4);color:var(--text-2);flex-shrink:0;font-weight:600;font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;line-height:1.5}
.mem-time{font-size:11px;color:var(--text-4);flex-shrink:0;width:100px;font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
.mem-txt{flex:1;color:var(--text);word-break:break-word;line-height:1.6}
.req-card{border:1px solid var(--line);border-radius:var(--radius-s);padding:18px 20px;margin-bottom:14px;background:var(--card-bg);box-shadow:var(--shadow-xs);transition:all .18s cubic-bezier(.16,1,.3,1)}
.req-card:hover{border-color:var(--line-2);box-shadow:var(--shadow-m);transform:translateY(-1px)}
.req-head{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.req-avatar{width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,var(--azure) 0%,var(--azure-strong) 100%);border:1px solid var(--brand-border);display:flex;align-items:center;justify-content:center;color:var(--on-accent);font-weight:600;font-size:14px;flex-shrink:0;box-shadow:var(--shadow-s)}
.req-name{font-size:14px;font-weight:600;color:var(--text)}
.req-qq{font-size:11.5px;color:var(--text-3);font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
.req-body{margin-bottom:14px}
.req-row{display:flex;gap:8px;font-size:12.5px;line-height:1.8}
.req-row .lbl{color:var(--text-3);flex-shrink:0;width:70px}
.req-row .val{color:var(--text-2);flex:1;word-break:break-word}
.req-actions{display:flex;gap:8px;justify-content:flex-end;border-top:1px solid var(--line);padding-top:14px}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:680px){.form-grid{grid-template-columns:1fr}}
.form-row{display:flex;flex-direction:column;gap:7px}
.form-row.full{grid-column:1/-1}
.form-row label{font-size:11px;color:var(--text-3);letter-spacing:.04em;font-weight:500}
.form-row input,.form-row select,.form-row textarea{width:100%;background:var(--card-bg);border:1px solid var(--line-2);border-radius:8px;padding:10px 13px;font-size:13px;color:var(--text);outline:none;transition:all .16s cubic-bezier(.16,1,.3,1);font-family:inherit;box-shadow:var(--shadow-xs)}
.form-row input:hover,.form-row textarea:hover,.form-row select:hover{border-color:var(--line-3)}
.form-row input:focus,.form-row textarea:focus,.form-row select:focus{border-color:var(--text);box-shadow:0 0 0 3.5px rgba(127,127,127,.15),var(--shadow-xs)}
.form-row textarea{min-height:76px;resize:vertical;line-height:1.6;font-family:inherit}
.form-row select{appearance:none;background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23a1a1aa' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>");background-repeat:no-repeat;background-position:right 13px center;padding-right:36px;cursor:pointer}
.cfg-group{margin-bottom:26px;padding-bottom:24px;border-bottom:1px solid var(--line)}
.cfg-group:last-child{margin-bottom:0;padding-bottom:0;border-bottom:none}
.cfg-group-title{font-size:12px;font-weight:600;color:var(--text);margin-bottom:18px;letter-spacing:.015em;display:flex;align-items:center;gap:10px;text-transform:uppercase}
.cfg-group-title::before{content:"";width:3px;height:13px;background:linear-gradient(180deg,var(--text),var(--text-2));border-radius:2px}
.empty{padding:70px 20px;text-align:center;color:var(--text-3);font-size:13px}
.empty .big{font-size:32px;margin-bottom:14px;opacity:.35}
.empty svg{width:42px;height:42px;margin-bottom:16px;color:var(--text-4);opacity:.7}
.empty .msg{color:var(--text-3);font-size:13.5px;letter-spacing:.005em}
.fab-bar{position:fixed;right:32px;bottom:32px;display:none;gap:8px;z-index:50;background:var(--card-bg);border:1px solid var(--line);padding:8px;border-radius:12px;box-shadow:var(--shadow-xl);transition:opacity .2s ease,transform .2s ease;opacity:0;transform:translateY(12px);pointer-events:none}
.fab-bar.show{display:flex;opacity:1;transform:translateY(0);pointer-events:auto}
@media (max-width:900px){.fab-bar{right:16px;bottom:16px;padding:6px;border-radius:10px}}
.toast{position:fixed;left:50%;bottom:36px;transform:translateX(-50%) translateY(24px);background:linear-gradient(180deg,var(--dark-1),var(--dark-2));color:var(--on-dark);padding:12px 22px;border-radius:10px;font-size:13px;font-weight:500;z-index:100;opacity:0;pointer-events:none;transition:all .28s cubic-bezier(.16,1,.3,1);box-shadow:var(--shadow-xl);letter-spacing:-.005em;border:1px solid var(--toast-border)}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.toast.err{background:var(--red)}
.api-tabs{display:flex;gap:4px;padding:5px;background:var(--bg-3);border-radius:var(--radius-s);margin-bottom:24px;flex-wrap:wrap}
.api-tab{padding:7px 15px;border-radius:7px;font-size:12.5px;color:var(--text-2);transition:all .15s ease;font-weight:500}
.api-tab:hover{color:var(--text);background:var(--api-tab-hover)}
.api-tab.on{background:var(--card-bg);color:var(--text);font-weight:550;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.api-panel{display:none}
.api-panel.on{display:block}
.api-result{margin-top:16px;padding:16px 18px;background:var(--bg-2);border:1px solid var(--line);border-radius:var(--radius-s);font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;font-size:12px;line-height:1.75;color:var(--text-2);max-height:360px;overflow:auto;white-space:pre-wrap;word-break:break-all}
.api-doc{margin-top:26px;border-top:1px solid var(--line);padding-top:24px}
.api-doc code{font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;background:var(--bg-3);padding:2px 7px;border-radius:5px;font-size:12px;color:var(--text)}
.api-doc h4{font-size:12px;font-weight:600;color:var(--text);margin:18px 0 10px;letter-spacing:.02em}
.api-doc table{width:100%;border-collapse:collapse;font-size:12.5px;margin-bottom:14px}
.api-doc td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
.api-doc td:first-child{font-family:"Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;color:var(--text);width:42%;word-break:break-all;font-size:12px}
.api-doc td:last-child{color:var(--text-2)}
@media (max-width:900px){
.side{width:70px;padding:16px 8px 10px}
.brand{justify-content:center;padding:0 0 16px}
.brand-info{display:none}
.brand-mark{width:32px;height:32px}
.nav-section{display:none}
.nav-item{justify-content:center;padding:12px 6px;gap:0;font-size:0}
.nav-item::before{display:none}
.nav-item .ico{width:20px;height:20px}
.nav-item .badge{position:absolute;top:3px;right:5px;min-width:16px;padding:0 5px;font-size:9px;line-height:14px}
.side-foot{display:none}
.topbar{padding:0 16px;height:60px;gap:10px}
.topbar h1{font-size:16px}
.top-metrics .hide-mobile{display:none}
.clock{display:none}
.content{padding:16px}
.card{padding:18px;border-radius:12px}
.stat-grid{grid-template-columns:1fr 1fr;gap:10px}
.stat-box{padding:16px;border-radius:12px}
.stat-box .v{font-size:26px}
.logs{font-size:11px}
.log-tag{min-width:36px;padding:0 6px;font-size:9px}
}
/*__WALLPAPER__*/
/* ---------- 自定义壁纸控件 ---------- */
.wall-box{display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap}
.wall-prev{width:196px;height:112px;flex-shrink:0;border-radius:var(--radius-s);
  border:1px solid var(--line-2);background:var(--bg-3) center/cover no-repeat;
  box-shadow:var(--shadow-s);position:relative;overflow:hidden}
.wall-prev::after{content:"";position:absolute;inset:0;
  background:linear-gradient(180deg,transparent 55%,rgba(0,0,0,.28));
  pointer-events:none}
.wall-prev.off{filter:grayscale(1);opacity:.45}
.wall-prev.off::before{content:"未启用";position:absolute;left:0;right:0;top:50%;
  transform:translateY(-50%);text-align:center;font-size:11px;font-weight:600;
  letter-spacing:.08em;color:#fff;text-shadow:0 1px 3px rgba(0,0,0,.6);z-index:2}
.wall-act{display:flex;flex-direction:column;gap:8px;min-width:220px;flex:1}
.wall-act .btn{align-self:flex-start}
.wall-tip{font-size:11.5px;color:var(--text-3);line-height:1.7}

/* ---- 概览页：正好占满，不留"下面啥也没有"的下滑 ----
   以前 .logs 是 height:calc(100vh - 280px)，那个 280 是手算的
   （顶栏 + 统计条 + padding + 卡片内边距），只在某个窗口尺寸下才对，
   换个高度就多出一截滚动。
   改 flex：页面撑满 .content，日志卡片吃掉剩余空间，日志列表自己在
   内部滚 —— 任何窗口高度都正好，一个像素不多。*/
#page-overview.on{display:flex!important;flex-direction:column;
                 height:100%!important;overflow:hidden!important}
#page-overview>.stat-grid{flex:0 0 auto}
#page-overview>.card{flex:1 1 auto;min-height:0;display:flex;
                     flex-direction:column;overflow:hidden}
#page-overview .logs{flex:1 1 auto;min-height:0;height:auto;
                     max-height:none;overflow-y:auto}
</style>
</head>
<body>
<div class="app">
<aside class="side">
  <div class="brand">
    <div class="brand-mark">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
    </div>
    <div class="brand-info">
      <div class="brand-title">Nekolyra<span class="brand-ver" id="brandVer"></span></div>
      <div class="brand-sub">CONSOLE</div>
    </div>
  </div>
  <nav class="nav">
    <div class="nav-section">监控</div>
    <button class="nav-item on" data-page="overview" data-title="概览"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg><span>概览</span></button>
    <button class="nav-item" data-page="join" data-title="入群管理"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="20" y1="8" x2="20" y2="14"/><line x1="23" y1="11" x2="17" y2="11"/></svg><span>入群管理</span><span class="badge" id="badge-join"></span></button>

    <div class="nav-section">群管</div>
    <button class="nav-item" data-page="risk" data-title="风控"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l9 4v6c0 5-3.8 9.3-9 10-5.2-.7-9-5-9-10V6z"/><path d="M9 12l2 2 4-4"/></svg><span>风控</span></button>
    <button class="nav-item" data-page="mem" data-title="长期记忆"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a4 4 0 0 0-4 4v1a3 3 0 0 0-3 3 3 3 0 0 0 1 2.2A3 3 0 0 0 6 16a3 3 0 0 0 3 3h1v2h4v-2h1a3 3 0 0 0 3-3 3 3 0 0 0-1-2.8A3 3 0 0 0 19 11a3 3 0 0 0-3-3V7a4 4 0 0 0-4-4z"/></svg><span>长期记忆</span><span class="badge" id="badge-mem"></span></button>
    <button class="nav-item" data-page="kb" data-title="知识库"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H19v3H6.5A2.5 2.5 0 0 1 4 20.5z"/><line x1="8" y1="7.5" x2="15" y2="7.5"/><line x1="8" y1="11" x2="13" y2="11"/></svg><span>知识库</span><span class="badge" id="badge-kb"></span></button>

    <div class="nav-section">配置</div>
    <button class="nav-item" data-page="persona" data-title="人格设定"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21v-1a8 8 0 0 1 16 0v1"/></svg><span>人格设定</span></button>
    <button class="nav-item" data-page="voice" data-title="语音合成"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg><span>语音合成</span></button>
    <button class="nav-item" data-page="aichat" data-title="AI 对话"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.1A8.4 8.4 0 0 1 4 11.5a8.5 8.5 0 0 1 17 0z"/><path d="M8 10h8"/><path d="M8 14h5"/></svg><span>AI 对话</span></button>
    <button class="nav-item" data-page="cfg" data-title="系统配置"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg><span>系统配置</span></button>
    <button class="nav-item" data-page="appear" data-title="外观"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="13.5" cy="6.5" r="1.5"/><circle cx="17.5" cy="10.5" r="1.5"/><circle cx="8.5" cy="7.5" r="1.5"/><circle cx="6.5" cy="12.5" r="1.5"/><path d="M12 2a10 10 0 1 0 0 20c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.3-.3-.4-.5-.8-.5-1.2 0-1.1.9-2 2-2h1.5a5.5 5.5 0 0 0 5.5-5.5C22 4.9 17.5 2 12 2z"/></svg><span>外观</span></button>
    <button class="nav-item" data-page="debug" data-title="调试"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/></svg><span>调试</span></button>
  <div class="nav-section">扩展</div>
<button class="nav-item" data-page="plugins" data-title="插件"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h6v3a2 2 0 0 0 2 2h3v6h-3a2 2 0 0 0-2 2v3H9v-3a2 2 0 0 0-2-2H4V8h3a2 2 0 0 0 2-2V3z"/></svg><span>插件</span></button>
</nav>
  <div class="side-foot"><span class="dot-live"></span><span>RUNNING</span></div>
</aside>

<main class="main">
  <div class="topbar">
    <h1 id="pageTitle">概览</h1>
    <div class="spacer"></div>
    <div class="top-metrics">
      <div class="metric hide-mobile"><div class="metric-l">模型</div><div class="val" id="mModel">-</div></div>
      <div class="metric"><div class="metric-l">记忆</div><div class="val" id="mMem">0</div></div>
      <div class="metric"><div class="metric-l">黑名单</div><div class="val" id="mBl">0</div></div>
      <div class="metric hide-mobile"><div class="metric-l">运行</div><div class="val" id="mUp">0s</div></div>
    </div>
    <div class="clock" id="clock">--:--:--</div>
  </div>
  <div class="content">
    <div class="page on" id="page-overview">
      <div class="stat-grid">
        <div class="stat-box" title="库里一共存了多少条长期记忆"><div class="v" id="s1">0</div><div class="l">长期记忆</div></div>
        <div class="stat-box" title="黑名单里一共多少人"><div class="v" id="s2">0</div><div class="l">黑名单</div></div>
        <div class="stat-box" title="本次启动以来，记忆分析写入的条数（重启归零）"><div class="v" id="s3">0</div><div class="l">本次已存</div></div>
        <div class="stat-box" title="本次启动以来，因重要度不够或重复而跳过的条数（重启归零）"><div class="v" id="s4">0</div><div class="l">本次跳过</div></div>
        <div class="stat-box" title="最近说过话、还在上下文缓存里的群数。刚启动时是 0，随聊天增长；不是群总数"><div class="v" id="s5">0</div><div class="l">活跃群组</div></div>
        <div class="stat-box" title="本次启动到现在"><div class="v" id="s6">0s</div><div class="l">运行时长</div></div>
      </div>
      <div class="card">
        <div class="card-h"><h3>实时日志</h3><span class="en">Live</span>
          <div class="right"><button class="btn ghost sm" onclick="clearLogs()">清空视图</button></div>
        </div>
        <div class="logs" id="bigLogs"></div>
      </div>
    </div>

    <div class="page" id="page-join">
      <div class="card">
        <div class="card-h"><h3>待审批入群申请</h3><span class="en">Pending Requests</span>
          <div class="right">
            <span style="font-size:11px;color:var(--text-3)">等级查不到时转人工</span>
            <button class="btn ghost sm" onclick="loadRequests()">刷新</button>
          </div>
        </div>
        <div id="reqList"><div class="empty"><div class="big">…</div><div class="msg">加载中</div></div></div>
      </div>
      <div class="card">
        <div class="card-h"><h3>待验证成员</h3><span class="en">Verifying</span>
          <div class="right">
            <span style="font-size:11px;color:var(--text-3)">新成员需在时限内回答数学题</span>
            <button class="btn ghost sm" onclick="loadVF()">刷新</button>
          </div>
        </div>
        <div id="vfList"><div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path d="M9 12l2 2 4-4"/></svg><div class="msg">当前无待验证成员</div></div></div>
      </div>
    </div>

    <div class="page" id="page-risk">
      <div class="card">
        <div class="card-h"><h3>黑名单</h3><span class="en">Blacklist</span>
          <div class="right"><button class="btn ghost sm" onclick="loadBL()">刷新</button></div>
        </div>
        <div style="display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap">
          <input id="blQQ" placeholder="QQ 号" style="flex:1;min-width:120px;background:var(--card-bg);border:1px solid var(--line-2);border-radius:8px;padding:10px 13px;font-size:13px;outline:none;box-shadow:var(--shadow-xs);color:var(--text)">
          <input id="blGID" placeholder="群号" style="flex:1;min-width:120px;background:var(--card-bg);border:1px solid var(--line-2);border-radius:8px;padding:10px 13px;font-size:13px;outline:none;box-shadow:var(--shadow-xs);color:var(--text)">
          <button class="btn primary" onclick="addBL()">手动拉黑</button>
        </div>
        <div id="blList"><div class="empty"><div class="big">…</div><div class="msg">加载中</div></div></div>
      </div>
    </div>

    <div class="page" id="page-mem">
      <div class="card">
        <div class="card-h"><h3>AI 长期记忆</h3><span class="en">Memory</span>
          <div class="right">
            <button class="btn sm" onclick="loadMem()">刷新</button>
            <button class="btn primary sm" onclick="toggleMemAdd()">新增记忆</button>
          </div>
        </div>
        <div class="page-desc">
          诗把记住的东西分成两层，<b>互不影响</b>：<br>
          · <b>群级记忆</b>——整个群共享（群规、话题、共识）<br>
          · <b>用户级记忆</b>——只属于某个人，按群隔离（张三在 A 群的偏好，B 群看不到）<br>
          每条记忆旁边可以展开<b>原始发言</b>，看看它是从哪句话得出来的。<br>
          删除分三层：删单条 · 清空某人在本群 · 清空某人全部（跨群）。
        </div>

        <div id="memAddBox" class="sub-panel" style="display:none">
          <div class="sub-panel-h">新增一条记忆</div>
          <div class="grid-f">
            <div class="fld"><label>归属</label>
              <select id="maScope" class="sel" onchange="memScopeChanged()">
                <option value="group">群级（整个群共享）</option>
                <option value="user">用户级（只对某个人）</option>
              </select></div>
            <div class="fld"><label>群号（0 = 私聊）</label>
              <input id="maGroup" class="inp" placeholder="例如 100000000" value="0"></div>
            <div class="fld" id="maUserWrap" style="display:none"><label>用户 QQ</label>
              <input id="maUser" class="inp" placeholder="例如 10001"></div>
            <div class="fld"><label>类别</label>
              <select id="maCat" class="sel">
                <option>偏好</option><option>近况</option><option>话题</option>
                <option>规则</option><option>成员</option><option selected>其他</option>
              </select></div>
            <div class="fld"><label>重要度 1-5</label>
              <input id="maImp" class="inp" type="number" min="1" max="5" value="4"></div>
          </div>
          <div class="fld" style="margin-top:13px">
            <label>要记住的内容（一句话）</label>
            <textarea id="maText" class="inp" rows="2"
              placeholder="例如：张三喜欢冰系角色，尤其卡提希娅"></textarea>
          </div>
          <div style="display:flex;gap:9px;margin-top:14px">
            <button class="btn primary sm" onclick="submitMemAdd()">保存</button>
            <button class="btn sm" onclick="toggleMemAdd()">取消</button>
          </div>
        </div>

        <div class="toolbar">
          <div class="seg" id="memViewSeg">
            <button class="on" data-v="group" onclick="setMemView('group')">按群看</button>
            <button data-v="user" onclick="setMemView('user')">按人看</button>
          </div>
          <select id="memUserSel" class="sel" style="width:auto;min-width:230px;display:none"
                  onchange="loadMem()"></select>
          <span id="memViewHint" class="bar-stat" style="margin:0 0 0 auto"></span>
        </div>

        <div id="memList"><div class="empty"><div class="msg">加载中…</div></div></div>
      </div>
    </div>

    <div class="page" id="page-kb">
      <div class="card">
        <div class="card-h"><h3>知识库</h3><span class="en">Knowledge Base</span>
          <div class="right">
            <button class="btn sm" onclick="loadKB()">刷新</button>
            <button class="btn sm" onclick="kbReclassify()">重新分类</button>
            <button class="btn sm" onclick="kbExport()">导出</button>
            <button class="btn primary sm" onclick="kbAdd()">新增条目</button>
          </div>
        </div>
        <div style="font-size:12.5px;color:var(--text-2);line-height:1.9;margin-bottom:16px">
          群里问问题时，诗会先在这里找答案，命中就把内容作为依据回答（命中日志会显示在日志页）。<br>
          群友也能自己教它：群里发 <code>补充知识 问题 | 答案</code>。<br>
          条目里 <b>群号 0 = 全局知识</b>（所有群通用）；填具体群号则该群专属。<br>
          新增条目会<b>自动分类</b>（按问题内容判断）；分类不准时点「重新分类」批量修正。
        </div>
        <div style="display:flex;gap:16px;align-items:flex-start">
          <div style="width:216px;flex:0 0 216px;background:var(--bg-2);border:1px solid var(--line);border-radius:10px;padding:10px">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px">
              <b style="font-size:12.5px;color:var(--text)">文件夹</b>
              <button class="btn sm" id="kbNewSubBtn" style="margin-left:auto;padding:4px 8px"
                onclick="kbNewFolderHere()" title="在当前文件夹里创建子文件夹">+ 子文件夹</button>
              <button class="btn sm" id="kbNewRootBtn" style="padding:4px 8px" onclick="kbNewFolderRoot()"
                title="在顶层创建文件夹">+ 顶层</button>
            </div>
            <div id="kbSubHint" style="font-size:11px;color:var(--text-4);margin-bottom:8px;line-height:1.6"></div>
            <div id="kbFolderTree" style="font-size:12.5px;max-height:560px;overflow:auto"></div>
          </div>
          <div style="flex:1;min-width:0">
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
          <input id="kbQuery" placeholder="搜索问题/答案，或输入一句话测试匹配效果（会显示匹配分数）"
                 style="flex:1;min-width:200px;background:var(--card-bg);border:1px solid var(--line-2);border-radius:8px;padding:10px 13px;font-size:13px;outline:none;box-shadow:var(--shadow-xs);color:var(--text)">
          <select id="kbGroupFilter" style="background:var(--card-bg);border:1px solid var(--line-2);border-radius:8px;padding:10px 13px;font-size:13px;color:var(--text)">
            <option value="-1">全部群</option>
            <option value="0">仅全局</option>
          </select>
          <button class="btn" onclick="loadKB()">查询</button>
          <button class="btn" onclick="kbMatchTest()">测匹配</button>
        </div>
        <div id="kbPathInfo" style="font-size:12.5px;color:var(--text-3);margin-bottom:10px">全部条目</div>
        <div id="kbToolbar" style="display:none;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px;padding:10px 13px;background:var(--bg-2);border:1px solid var(--line);border-radius:8px">
          <label style="font-size:12.5px;color:var(--text-2);display:flex;align-items:center;gap:6px;cursor:pointer">
            <input type="checkbox" id="kbSelAll" onchange="kbToggleAll(this.checked)">全选
          </label>
          <span id="kbSelInfo" style="font-size:12.5px;color:var(--text-3)">已选 0 条</span>
          <div style="margin-left:auto;display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn sm" onclick="kbMoveSelected()">移动到…</button>
            <button class="btn danger sm" onclick="kbDeleteSelected()">删除选中</button>
            <button class="btn danger sm" id="kbClearCatBtn" onclick="kbClearCategory()">清空此分类</button>
          </div>
        </div>
        <div id="kbTestRes" style="display:none;margin-bottom:16px"></div>
        <div id="kbList"><div class="empty"><div class="big">…</div><div class="msg">加载中</div></div></div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-h"><h3>批量导入</h3><span class="en">Import</span></div>
        <div style="font-size:12px;color:var(--text-3);margin-bottom:10px">
          每行一条，格式 <code>问题|答案</code>；以 # 开头的行会被忽略。
        </div>
        <div class="form-grid">
          <div class="form-row"><label>导入到群号（0 = 全局）</label>
            <input id="kbImportGroup" type="number" value="0" step="1" min="0">
          </div>
        </div>
        <textarea id="kbImportText" style="min-height:160px;width:100%;background:var(--card-bg);border:1px solid var(--line-2);border-radius:8px;padding:11px 13px;font-size:12.5px;font-family:ui-monospace,Menlo,monospace;color:var(--text);outline:none"
          placeholder="# 每行：问题|答案&#10;鸣潮3.5岁主是谁|磐古&#10;本群群规|不许发广告"></textarea>
        <div style="margin-top:12px;display:flex;gap:8px">
          <button class="btn primary" onclick="kbImport()">开始导入</button>
          <button class="btn" onclick="document.getElementById('kbImportText').value='';">清空</button>
        </div>
        <div id="kbImportRes" class="api-result" style="margin-top:12px;display:none"></div>
      </div>
    </div>

    <div class="page" id="page-persona">
      <div class="card">
        <div class="card-h"><h3>人格设定</h3><span class="en">Persona</span>
          <div class="right">
            <button class="btn sm" onclick="loadPersona()">重置</button>
            <button class="btn primary sm" onclick="savePersona()">保存</button>
          </div>
        </div>
        <div id="personaForm"><div class="empty"><div class="big">…</div><div class="msg">加载中</div></div></div>
      </div>
    </div>

    <div class="page" id="page-voice">
      <div class="card">
        <div class="card-h"><h3>语音合成配置</h3><span class="en">Voice · IndexTTS</span>
          <div class="right">
            <button class="btn sm" onclick="loadVoice()">重置</button>
            <button class="btn primary sm" onclick="saveVoice()">保存</button>
          </div>
        </div>
        <div style="font-size:12.5px;color:var(--text-2);line-height:1.9;margin-bottom:18px">
          语音功能依赖本地运行的 IndexTTS（OpenAI 兼容接口）。<br>
          启动 IndexTTS 整合包时，会给出一个 API 地址（默认 <code>http://127.0.0.1:7861/v1/audio/speech</code>）和 API Key。<br>
          音色名称是你在 IndexTTS 里给某个动漫角色参考音频起的名字。
        </div>
        <div id="voiceForm"><div class="empty"><div class="big">…</div><div class="msg">加载中</div></div></div>
        <div style="margin-top:22px;padding-top:18px;border-top:1px solid var(--line)">
          <div style="font-size:12px;color:var(--text-3);margin-bottom:10px">测试一段文本，看是否能生成语音（不会发送到群里）：</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <input id="voiceTestText" placeholder="例如：你好呀，今天也要开心哦" style="flex:1;min-width:200px;background:var(--card-bg);border:1px solid var(--line-2);border-radius:8px;padding:10px 13px;font-size:13px;outline:none;box-shadow:var(--shadow-xs);color:var(--text)">
            <button class="btn" onclick="testVoice()">生成测试</button>
          </div>
          <div class="api-result" id="voiceTestRes" style="margin-top:12px">等待测试...</div>
        </div>
        <div style="margin-top:22px;padding-top:18px;border-top:1px solid var(--line)">
          <div style="font-size:12px;color:var(--text-3);margin-bottom:10px">把指定文本作为语音发到群里（会真的发出去）：</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
            <input id="voiceSendGid" placeholder="群号" style="width:180px;background:var(--card-bg);border:1px solid var(--line-2);border-radius:8px;padding:10px 13px;font-size:13px;outline:none;box-shadow:var(--shadow-xs);color:var(--text)">
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <input id="voiceSendText" placeholder="要念的文本，例如：早上好呀～" style="flex:1;min-width:200px;background:var(--card-bg);border:1px solid var(--line-2);border-radius:8px;padding:10px 13px;font-size:13px;outline:none;box-shadow:var(--shadow-xs);color:var(--text)">
            <button class="btn primary" onclick="sendVoice()">发送语音</button>
          </div>
          <div class="api-result" id="voiceSendRes" style="margin-top:12px">等待操作...</div>
        </div>
      </div>
    </div>

    <div class="page" id="page-cfg">
      <div class="card">
        <div class="card-h"><h3>系统配置</h3><span class="en">Config</span>
          <div class="right">
            <button class="btn sm" onclick="loadConfig()">重置</button>
            <button class="btn primary sm" onclick="saveConfig()">保存</button>
          </div>
        </div>
        <div id="cfgForm"><div class="empty"><div class="big">…</div><div class="msg">加载中</div></div></div>
      </div>
    </div>

    <div class="page" id="page-appear">
      <div class="card">
        <div class="card-h"><h3>外观</h3><span class="en">Appearance</span>
          <div class="right">
            <button class="btn sm" onclick="loadAppear()">重置</button>
            <button class="btn primary sm" onclick="saveConfig('appear')">保存</button>
          </div>
        </div>
        <div id="appearForm"><div class="empty"><div class="big">…</div><div class="msg">加载中</div></div></div>
      </div>
    </div>

    <div class="page" id="page-aichat">
      <div class="card" style="display:flex;flex-direction:column;min-height:70vh">
        <div class="card-h"><h3>AI 对话</h3><span class="en">Chat</span>
          <div class="right">
            <span style="font-size:11.5px;color:var(--text-3)">
              只在本机跟 AI 聊，不会发到任何群</span>
          </div>
        </div>
        <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px">
          <div class="form-row" style="min-width:160px;margin:0"><label>测试身份（QQ号）</label>
            <input id="aichat-uid" value="10001"></div>
          <div class="form-row" style="min-width:160px;margin:0"><label>昵称（可选）</label>
            <input id="aichat-nick" placeholder="留空就用 QQ 号"></div>
          <label style="display:flex;align-items:center;gap:6px;font-size:12.5px;
                        color:var(--text-2);cursor:pointer;padding-bottom:9px">
            <input type="checkbox" id="aichat-dry" checked> 不写入记忆和聊天记录</label>
          <button class="btn sm" style="margin-bottom:4px" onclick="aiChatClear()">清空对话</button>
        </div>
        <div id="aichat-log" style="flex:1;min-height:320px;max-height:56vh;overflow:auto;
             border:1px solid var(--line);border-radius:var(--radius-sm);padding:14px;
             background:var(--bg-2);line-height:1.9"></div>
        <div style="display:flex;gap:10px;margin-top:12px;align-items:flex-end">
          <textarea id="aichat-text" class="inp" placeholder="说点什么…（Ctrl+Enter 或点发送）"
            style="flex:1;min-height:64px;resize:vertical"></textarea>
          <button class="btn primary" id="aichat-send" onclick="aiChatSend()"
            style="height:40px">发送</button>
        </div>
      </div>
    </div>

    <div class="page" id="page-debug">
      <div class="card">
        <div class="card-h"><h3>接口测试</h3><span class="en">Test HTTP API</span>
          <div class="right"><span style="font-size:11px;color:var(--text-3)">所有请求自动带 token</span></div>
        </div>
        <div class="api-tabs" id="apiTabs">
          <button class="api-tab on" data-api="vision">识图测试</button>
          <button class="api-tab" data-api="send">发消息到群</button>
          <button class="api-tab" data-api="nickname">查昵称</button>
          <button class="api-tab" data-api="backup">备份</button>
        </div>




        <div class="api-panel" id="api-vision">
          <div style="font-size:12.5px;color:var(--text-2);line-height:1.9;margin-bottom:16px">
            贴一张图片的公开 URL，看看视觉模型能不能正确描述。<br>
            用的是配置里「视觉识图」那组的 API Key 和模型。
          </div>
          <div class="form-row"><label>图片 URL</label><input id="vis-url" placeholder="https://..."></div>
          <div style="margin-top:16px"><button class="btn primary" onclick="runVision()">识图测试</button></div>
          <div class="api-result" id="vis-res">等待请求...</div>
        </div>

        <div class="api-panel" id="api-send">
          <div class="form-grid">
            <div class="form-row"><label>群号</label><input id="send-gid" placeholder="例如 100000000"></div>
            <div class="form-row"><label>@某人（可选，QQ号）</label><input id="send-at" placeholder="可留空"></div>
            <div class="form-row full"><label>消息内容</label><textarea id="send-msg" placeholder="输入要发送的消息..."></textarea></div>
          </div>
          <div style="margin-top:16px"><button class="btn primary" onclick="runSend()">发送到群</button></div>
          <div class="api-result" id="send-res">等待请求...</div>
        </div>

        <div class="api-panel" id="api-nickname">
          <div class="form-grid">
            <div class="form-row"><label>群号</label><input id="nick-gid" placeholder="例如 100000000"></div>
            <div class="form-row"><label>QQ号</label><input id="nick-uid" placeholder="例如 10001"></div>
          </div>
          <div style="margin-top:16px"><button class="btn primary" onclick="runNick()">查昵称</button></div>
          <div class="api-result" id="nick-res">等待请求...</div>
        </div>

        
        <div class="api-panel" id="api-backup">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:16px">
            <button class="btn primary" onclick="runBackupNow()">立即备份</button>
            <button class="btn" onclick="loadBackups()">刷新列表</button>
            <span style="font-size:11.5px;color:var(--text-3)">每天凌晨自动备份一次，保留最近 7 天</span>
          </div>
          <div id="backupList"><div class="empty"><div class="big">…</div><div class="msg">加载中</div></div></div>
        </div>

        <div class="api-doc">
          <h4>群内普通命令（@机器人 触发）</h4>
          <table>
            <tr><td>@机器人 + 发图</td><td>机器人看图后回复（需开启视觉识图）</td></tr>
            <tr><td>消息里带「语音 / 说话 / 念出来」等</td><td>机器人以语音回复（需开启语音合成）</td></tr>
          </table>
          <h4>超管命令（@机器人 触发，仅超管可用）</h4>
          <table>
            <tr><td>踢 @某人 / QQ号 / 昵称</td><td>拉黑并踢出群</td></tr>
            <tr><td>拉黑 @某人 / QQ号 / 昵称</td><td>只拉黑，不踢出</td></tr>
            <tr><td>解除 @某人 / QQ号 / 昵称</td><td>从黑名单移除</td></tr>
            <tr><td>清空记忆</td><td>清空本群 AI 长期记忆</td></tr>
            <tr><td>清空黑名单</td><td>清空本群黑名单</td></tr>
            <tr><td>状态</td><td>查看 bot 运行状态</td></tr>
            <tr><td>AI开 / AI关</td><td>切换 AI 对话</td></tr>
            <tr><td>语音开 / 语音关</td><td>切换语音合成</td></tr>
          </table>
          <h4>入群审批逻辑</h4>
          <table>
            <tr><td>黑名单</td><td>自动拒绝</td></tr>
            <tr><td>等级 ≥ 25</td><td>自动通过</td></tr>
            <tr><td>等级 &lt; 25（且有等级）</td><td>自动拒绝</td></tr>
            <tr><td>等级查不到（含 0 级）</td><td>转人工，私聊超管 + 管理界面「入群管理」页</td></tr>
            <tr><td>60 秒内重复申请</td><td>静默拒绝（防刷屏）</td></tr>
          </table>
        </div>
      </div>
    </div>

    <div class="page" id="page-plugins">
        <div class="card">
          <div class="card-h"><h3>插件</h3><span class="en">Plugins</span>
            <div class="right">
              <button class="btn sm" onclick="loadPlugins()">刷新</button>
              <button class="btn primary sm" onclick="reloadPlugins()">重新加载</button>
            </div>
          </div>
          <div class="page-desc">
            插件放在 <code id="pluginDir" class="inline-code">data/plugins/</code> 下面，
            一个插件一个文件夹，里面放 <code class="inline-code">metadata.json</code>
            和 <code class="inline-code">main.py</code>。<br>
            <b>启用 / 停用立刻生效</b>（命令和消息钩子马上响应或不响应）；
            改完插件代码点右上角<b>重新加载</b>即可，不用重启程序。<br>
            <span style="color:var(--text-3)">
              注意：插件是用 Python 写的，装插件等于让它的代码在你电脑上运行，只装你信得过的。
            </span>
          </div>
          <div class="seg" id="plugSeg" style="margin:14px 0 4px">
            <button class="on" data-tab="installed">已安装</button>
            <button data-tab="market">插件市场</button>
          </div>

          <div id="plugPaneInstalled">
            <div class="toolbar"><span class="bar-stat" id="pluginStat">加载中…</span></div>
            <div id="pluginList"></div>
            <div class="plug-install">
              <div class="plug-install-h">从 GitHub 地址安装</div>
              <div class="plug-install-row">
                <input id="plugUrl" class="inp" placeholder="https://github.com/用户名/仓库名">
                <button class="btn primary" onclick="installFromUrl()">安装</button>
              </div>
              <div class="plug-install-tip">
                仓库根目录要有 <code class="inline-code">metadata.json</code> 和入口文件。
                插件放在子目录里也行，装完我会自己找。不确定怎么写？
                看 <a href="https://github.com/Mario9800/nekoe-plugins" target="_blank">插件模板</a>。
              </div>
            </div>
          </div>

          <div id="plugPaneMarket" style="display:none">
            <div class="toolbar">
              <span class="bar-stat" id="marketStat">还没拉取</span>
              <span style="font-size:11.5px;color:var(--text-3)">装好之后去「已安装」页停用 / 配置 / 卸载</span>
              <button class="btn sm" onclick="loadMarket(1)">刷新市场</button>
            </div>
            <div id="marketList"></div>
          </div>
        </div>
      </div>
    </div>
</main>
</div>
<div class="toast" id="toast"></div>
<div class="fab-bar" id="fabBar">
  <button class="btn sm" onclick="fabReset()">重置</button>
  <button class="btn primary sm" onclick="fabSave()">保存</button>
</div>
<script>
const TOKEN = new URLSearchParams(location.search).get("token") || "";
let lastSeq = 0;
const MAX_LINES = 500;
const TAG_MAP = {info:"info",success:"ok",warn:"warn",error:"err",msg:"msg",reply:"reply",mem:"mem",join:"join",leave:"leave",verify:"verify",block:"block",req:"req"};
const TAG_LABEL = {info:"INFO",success:"OK",warn:"WARN",error:"ERR",msg:"MSG",reply:"REPLY",mem:"MEM",join:"JOIN",leave:"LEAVE",verify:"VERIFY",block:"BLOCK",req:"REQ"};
function toast(m,t){const el=document.getElementById("toast");el.textContent=m;el.className="toast show "+(t||"");clearTimeout(el._t);el._t=setTimeout(()=>el.classList.remove("show"),2200);}
function api(p,o){const s=p.includes("?")?"&":"?";return fetch(p+s+"token="+encodeURIComponent(TOKEN),o).then(async r=>{try{return await r.json();}catch(e){return {error:"HTTP "+r.status+"（响应不是 JSON）"};}}).catch(e=>({error:"网络错误："+e.message}));}
function post(p,body){const s=p.includes("?")?"&":"?";return fetch(p+s+"token="+encodeURIComponent(TOKEN),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})}).then(async r=>{try{return await r.json();}catch(e){return {error:"HTTP "+r.status+"（响应不是 JSON）"};}}).catch(e=>({error:"网络错误："+e.message}));}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
// 拼进 onclick="fn('...')" 里的字符串必须用这个，不能用 esc。
// esc 只做 HTML 转义：' -> &#39;，而 HTML 属性解析这一层会把它
// **解回 '** —— 正好闭合 JS 字符串，轻则 SyntaxError，重则把插件名
// 当 JS 执行（插件作者可控，但打开管理页的所有人都中招）。
// 这里把引号/尖括号/& 全变成 \xNN：HTML 层看不到特殊字符，
// JS 层再还原成原字符。
function jsq(v){
  return String(v==null?"":v)
    .replace(/\\/g,"\\\\")
    .replace(/'/g,"\\x27")
    .replace(/"/g,"\\x22")
    .replace(/</g,"\\x3c")
    .replace(/>/g,"\\x3e")
    .replace(/&/g,"\\x26")
    .replace(/\r/g,"\\r")
    .replace(/\n/g,"\\n");
}
function fmtUp(sec){sec=Math.floor(sec);const d=Math.floor(sec/86400),h=Math.floor(sec%86400/3600),m=Math.floor(sec%3600/60),s=sec%60;if(d)return d+"d "+h+"h";if(h)return h+"h "+m+"m";if(m)return m+"m "+s+"s";return s+"s";}
function fmtT(iso){if(!iso)return "";const d=new Date(iso),p=n=>String(n).padStart(2,"0");return `${d.getMonth()+1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;}
function fmtAgo(ts){if(!ts)return "";const s=Math.floor(Date.now()/1000)-ts;if(s<60)return s+"秒前";if(s<3600)return Math.floor(s/60)+"分钟前";if(s<86400)return Math.floor(s/3600)+"小时前";return Math.floor(s/86400)+"天前";}
function renderLog(i){const tag=TAG_MAP[i.level]||"info",lbl=TAG_LABEL[i.level]||"INFO";return `<div class="log-line ${tag}"><span class="log-t">${i.time}</span><span class="log-tag ${tag}">${lbl}</span><span class="log-msg">${esc(i.msg)}</span></div>`;}
function appendLogs(el,items){if(!items.length)return;const at=el.scrollHeight-el.scrollTop-el.clientHeight<60;el.insertAdjacentHTML("beforeend",items.map(renderLog).join(""));while(el.children.length>MAX_LINES)el.removeChild(el.firstChild);if(at)el.scrollTop=el.scrollHeight;}
async function pollLogs(){try{const items=await api("/admin/api/logs?since="+lastSeq);if(items&&items.length){lastSeq=items[items.length-1].seq;appendLogs(document.getElementById("bigLogs"),items);}}catch(e){}}
async function pollStats(){try{const s=await api("/admin/api/stats");document.getElementById("mMem").textContent=s.memory_total;document.getElementById("mBl").textContent=s.blacklist_total;document.getElementById("mUp").textContent=fmtUp(s.uptime);document.getElementById("mModel").textContent=s.model;document.getElementById("s1").textContent=s.memory_total;document.getElementById("s2").textContent=s.blacklist_total;document.getElementById("s3").textContent=s.mem_saved;document.getElementById("s4").textContent=s.mem_skipped;document.getElementById("s5").textContent=s.active_groups;document.getElementById("s6").textContent=fmtUp(s.uptime);document.getElementById("badge-mem").textContent=s.memory_total||"";const joinCount=(s.pending_count||0)+(s.request_count||0);document.getElementById("badge-join").className="badge alert";document.getElementById("badge-join").textContent=joinCount||"";}catch(e){}}
function updateFab(){
  const cur=document.querySelector(".page.on");
  const id=cur?cur.id:"";
  const bar=document.getElementById("fabBar");
  if(id==="page-cfg"||id==="page-appear"||id==="page-persona"
     ||id==="page-risk"||id==="page-voice"){
    bar.classList.add("show");
  }else{
    bar.classList.remove("show");
  }
}
function fabSave(){
  const id=document.querySelector(".page.on").id;
  if(id==="page-cfg")saveConfig();
  else if(id==="page-appear")saveConfig("appear");
  else if(id==="page-persona")savePersona();
  else if(id==="page-voice")saveVoice();
}
function fabReset(){
  const id=document.querySelector(".page.on").id;
  if(id==="page-cfg")loadConfig();if(id==="page-appear")loadAppear();
  else if(id==="page-persona")loadPersona();
  else if(id==="page-voice")loadVoice();
}
function setPage(n,t){
  /* 同一页再点一次：直接返回。
     以前不管点哪都会把 .on 摘了再挂上，浏览器就当成一次新的
     DOM 变化 —— 挂在上面的 fadeIn 动画从头再跑一遍，内容从
     opacity:0 起来，看着就是"点一下白一下"。*/
  if(document.getElementById("page-"+n)?.classList.contains("on"))return;document.querySelectorAll(".nav-item").forEach(b=>b.classList.toggle("on",b.dataset.page===n));document.querySelectorAll(".page").forEach(p=>p.classList.toggle("on",p.id==="page-"+n));document.getElementById("pageTitle").textContent=t;if(n==="mem")loadMem();if(n==="kb")loadKB();if(n==="risk")loadBL();if(n==="join"){loadRequests();loadVF();}if(n==="persona")loadPersona();if(n==="voice")loadVoice();if(n==="aichat")loadAiChat();if(n==="cfg")loadConfig();if(n==="appear")loadAppear();if(n==="debug")loadBackups();if(n==="plugins"){loadPlugins();setPlugTab("installed");}updateFab();}
document.querySelectorAll(".nav-item").forEach(b=>{b.onclick=()=>setPage(b.dataset.page,b.dataset.title);});

document.querySelectorAll(".api-tab").forEach(b=>{b.onclick=()=>{
  document.querySelectorAll(".api-tab").forEach(x=>x.classList.remove("on"));
  document.querySelectorAll(".api-panel").forEach(x=>x.classList.remove("on"));
  b.classList.add("on");
  document.getElementById("api-"+b.dataset.api).classList.add("on");
};});

function showRes(id, data){document.getElementById(id).textContent = JSON.stringify(data, null, 2);}

async function runChat(){const gid=document.getElementById("chat-gid").value.trim();const uid=document.getElementById("chat-uid").value.trim();const nick=document.getElementById("chat-nick").value.trim();const text=document.getElementById("chat-text").value.trim();const mode=document.getElementById("chat-mode").value;if(!gid||!uid||!text){toast("请填群号、QQ号和消息","err");return;}showRes("chat-res", await post("/api/chat", {group_id:parseInt(gid),user_id:parseInt(uid),text:text,nickname:nick,mode:mode}));}
async function runVision(){const url=document.getElementById("vis-url").value.trim();if(!url){toast("请填图片 URL","err");return;}showRes("vis-res",await api(`/api/vision/test?url=${encodeURIComponent(url)}`));}
async function runSend(){const gid=document.getElementById("send-gid").value.trim();const at=document.getElementById("send-at").value.trim();const msg=document.getElementById("send-msg").value.trim();if(!gid||!msg){toast("请填群号和消息","err");return;}const body={group_id:parseInt(gid),message:msg};if(at) body.at_user_id=parseInt(at);showRes("send-res", await post("/api/send", body));}
async function runNick(){const gid=document.getElementById("nick-gid").value.trim();const uid=document.getElementById("nick-uid").value.trim();if(!gid||!uid){toast("请填群号和QQ号","err");return;}showRes("nick-res",await api(`/api/nickname?group_id=${gid}&user_id=${uid}`));}


async function runBackupNow(){const r=await api("/api/backup/now",{method:"POST"});if(r.error){toast("备份失败："+r.error,"err");}else{toast(`备份完成（${r.size_kb}KB）`);loadBackups();}}
async function loadBackups(){
  try{
    const data=await api("/api/backup/list");
    const el=document.getElementById("backupList");
    if(!data.items||!data.items.length){el.innerHTML='<div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg><div class="msg">还没有备份</div></div>';return;}
    let h='<table class="tbl"><thead><tr><th>日期</th><th>大小</th><th>生成时间</th><th></th></tr></thead><tbody>';
    for(const it of data.items){
      h+=`<tr><td class="mono strong">${it.date}</td><td class="mono">${it.size_kb} KB</td><td class="mono" style="color:var(--text-4)">${it.mtime}</td><td style="text-align:right"><span style="font-size:11px;color:var(--text-3)">${esc(it.file)}</span></td></tr>`;
    }
    h+='</tbody></table>';
    el.innerHTML=h;
  }catch(e){
    document.getElementById("backupList").innerHTML='<div class="empty"><div class="msg">加载失败</div></div>';
  }
}

async function loadRequests(){
  try{
    const data=await api("/admin/api/requests");
    const el=document.getElementById("reqList");
    if(!data||!data.length){el.innerHTML='<div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path d="M9 12l2 2 4-4"/></svg><div class="msg">当前没有待审批的入群申请</div></div>';return;}
    let h='';
    for(const it of data){
      const initial=(it.nickname||"?").charAt(0).toUpperCase();
      const ago=fmtAgo(it.created_at);
      h+=`<div class="req-card" data-flag="${esc(it.flag)}">
        <div class="req-head">
          <div class="req-avatar">${esc(initial)}</div>
          <div>
            <div class="req-name">${esc(it.nickname||"未知")}</div>
            <div class="req-qq">QQ ${it.user_id} · ${ago}</div>
          </div>
        </div>
        <div class="req-body">
          <div class="req-row"><span class="lbl">群号</span><span class="val">${it.group_id}</span></div>
          <div class="req-row"><span class="lbl">验证信息</span><span class="val">${esc(it.comment||"（无）")}</span></div>
          <div class="req-row"><span class="lbl">等级</span><span class="val" style="color:var(--amber)">查不到（可能 0 级或隐私保护）</span></div>
        </div>
        <div class="req-actions">
          <button class="btn danger sm" onclick="rejectReq('${jsq(it.flag)}')">拒绝</button>
          <button class="btn green sm" onclick="approveReq('${jsq(it.flag)}')">通过</button>
        </div>
      </div>`;
    }
    el.innerHTML=h;
  }catch(e){
    document.getElementById("reqList").innerHTML='<div class="empty"><div class="msg">加载失败</div></div>';
  }
}
async function approveReq(flag){
  if(!confirm("确定通过这个入群申请？")) return;
  const r=await api("/admin/api/requests/approve?flag="+encodeURIComponent(flag),{method:"POST"});
  if(r.error){toast("通过失败："+r.error,"err");}
  else{toast("已通过");loadRequests();pollStats();}
}
async function rejectReq(flag){
  const reason=prompt("拒绝理由（可留空，直接确定使用默认理由）：","");
  if(reason===null) return;
  const r=await api("/admin/api/requests/reject?flag="+encodeURIComponent(flag)+"&reason="+encodeURIComponent(reason||""),{method:"POST"});
  if(r.error){toast("拒绝失败："+r.error,"err");}
  else{toast("已拒绝");loadRequests();pollStats();}
}

function listToText(arr){return (arr||[]).join("\n");}
function textToList(s){return s.split("\n").map(x=>x.trim()).filter(x=>x);}





// ==================== AI 对话（独立页，只私聊） ====================
// 这个页只调用 /api/chat 的 private 分支 —— 它读 get_private_context(uid)、
// 不碰任何群上下文，也不会往群里发消息。
function aiChatBubble(who, text, cls){
  var box=document.getElementById("aichat-log");
  if(!box)return;
  var d=document.createElement("div");
  d.style.cssText="margin:8px 0;display:flex;"
    +(who==="me"?"justify-content:flex-end":"justify-content:flex-start");
  var b=document.createElement("div");
  b.style.cssText="max-width:76%;padding:9px 13px;border-radius:12px;"
    +"white-space:pre-wrap;word-break:break-word;font-size:13px;"
    +(who==="me"
      ? "background:var(--btn-primary-bg);color:#fff"
      : (cls==="err"?"background:var(--red-soft);color:var(--red)"
                    : "background:var(--card-bg);color:var(--text);border:1px solid var(--line)"));
  b.textContent=text;
  d.appendChild(b);
  box.appendChild(d);
  box.scrollTop=box.scrollHeight;
}
function aiChatClear(){
  document.getElementById("aichat-log").innerHTML=
    '<div style="color:var(--text-3);font-size:12.5px">'+
    '在这里直接跟 AI 对话，用来调人格 / 试知识库命中，不用去 QQ。'+
    '默认不写入记忆和聊天记录。</div>';
}
function loadAiChat(){
  var box=document.getElementById("aichat-log");
  if(box && !box.textContent.trim())aiChatClear();
}
async function aiChatSend(){
  var t=document.getElementById("aichat-text");
  var btn=document.getElementById("aichat-send");
  var text=(t.value||"").trim();
  if(!text){toast("先说点什么","err");return;}
  var uid=parseInt(document.getElementById("aichat-uid").value||"10001",10);
  if(isNaN(uid)||uid<=0){toast("测试身份要填正整数 QQ 号","err");return;}
  var nick=(document.getElementById("aichat-nick").value||"").trim();
  var dry=document.getElementById("aichat-dry").checked;
  aiChatBubble("me", text);
  t.value="";
  btn.disabled=true;btn.textContent="思考中…";
  try{
    var r=await post("/api/chat",{group_id:0,user_id:uid,text:text,
      nickname:nick,mode:"private",dry_run:dry});
    if(!r||!r.ok){aiChatBubble("ai","失败："+((r&&r.error)||"无响应"),"err");}
    else{aiChatBubble("ai",r.reply||"(空)");}
  }catch(e){
    aiChatBubble("ai","异常："+(e&&e.message||e),"err");
  }finally{
    btn.disabled=false;btn.textContent="发送";
  }
}
document.addEventListener("keydown",function(e){
  var t=document.getElementById("aichat-text");
  if(t&&document.activeElement===t&&e.ctrlKey&&e.key==="Enter"){e.preventDefault();aiChatSend();}
});

async function loadVoice(){
  const cfg = await api("/admin/api/config");
  const el = document.getElementById("voiceForm");
  const apiKeyPlaceholder = cfg.voice_api_key_set ? "留空 = 不修改（当前已配置）" : "未配置";
  el.innerHTML = `
    <div class="form-grid">
      <div class="form-row"><label>开启语音合成</label>
        <select id="vf-enabled">
          <option value="true"${cfg.voice_enabled?" selected":""}>开启</option>
          <option value="false"${!cfg.voice_enabled?" selected":""}>关闭</option>
        </select>
      </div>
      <div class="form-row"><label>音色名称</label>
        <input id="vf-name" value="${esc(cfg.voice_name||"")}" placeholder="你在 IndexTTS 里保存的音色名">
      </div>
      <div class="form-row full"><label>API 地址</label>
        <input id="vf-url" value="${esc(cfg.voice_api_url||"")}" placeholder="http://127.0.0.1:7861/v1/audio/speech">
      </div>
      <div class="form-row full"><label>API Key</label>
        <input id="vf-key" value="" placeholder="${esc(apiKeyPlaceholder)}">
      </div>
      <div class="form-row"><label>语速（0.5 - 2.0）</label>
        <input id="vf-speed" type="number" step="0.1" value="${cfg.voice_speed||1.0}">
      </div>
      <div class="form-row"><label>超时（秒）</label>
        <input id="vf-timeout" type="number" value="${cfg.voice_timeout||90}">
      </div>
      <div class="form-row"><label>随机发语音概率（0-1）</label>
        <input id="vf-rand" type="number" step="0.01" value="${cfg.voice_random_chance||0}">
      </div>
      <div class="form-row"><label>语音最大字数</label>
        <input id="vf-maxlen" type="number" value="${cfg.voice_max_chars||200}">
      </div>
      <div class="form-row full"><label>触发关键词（每行一个；消息里带任意一个就发语音）</label>
        <textarea id="vf-kw" style="min-height:120px">${esc((cfg.voice_trigger_keywords||[]).join("\n"))}</textarea>
      </div>
      <div class="form-row"><label>情感模式</label>
        <select id="vf-emode">
          <option value="vector"${(cfg.voice_emotion_mode||"vector")==="vector"?" selected":""}>八维情感向量（推荐）</option>
          <option value="speaker"${cfg.voice_emotion_mode==="speaker"?" selected":""}>跟随音色参考（不额外控制情感）</option>
        </select>
      </div>
      <div class="form-row"><label>情感强度（0-1，服务端默认 0.65）</label>
        <input id="vf-estr" type="number" step="0.05" min="0" max="1" value="${cfg.voice_emotion_strength!=null?cfg.voice_emotion_strength:0.65}">
      </div>
      <div class="form-row"><label>音频后处理（去刺音）</label>
        <select id="vf-pp">
          <option value="off"${(cfg.voice_postprocess_preset||"off")==="off"?" selected":""}>关闭</option>
          <option value="deharsh"${cfg.voice_postprocess_preset==="deharsh"?" selected":""}>去刺耳 deharsh（推荐）</option>
          <option value="warm"${cfg.voice_postprocess_preset==="warm"?" selected":""}>温暖 warm</option>
          <option value="voice_clarity"${cfg.voice_postprocess_preset==="voice_clarity"?" selected":""}>提升清晰度</option>
          <option value="normalize"${cfg.voice_postprocess_preset==="normalize"?" selected":""}>统一响度</option>
        </select>
      </div>
      <div class="form-row"><label>后处理强度（0-1）</label>
        <input id="vf-pps" type="number" step="0.1" min="0" max="1" value="${cfg.voice_postprocess_strength!=null?cfg.voice_postprocess_strength:1.0}">
      </div>
      <div class="form-row full"><label>情感向量（8 个数字，逗号或换行分隔；顺序：喜,怒,哀,惧,厌恶,低落,惊喜,平静）</label>
        <textarea id="vf-evec" style="min-height:64px">${esc((cfg.voice_emotion_vector||[]).join(", "))}</textarea>
        <div style="font-size:11.5px;color:var(--text-3);margin-top:6px;line-height:1.7">
          这是"没指定情绪"时用的默认向量。群里说「开心点」「带感情哟」时会自动换成对应情绪的向量，不受这里影响。<br>
          全填 0 等于没有情感，会退回"跟随音色参考"。例：平静 0.4 → <code>0.1,0,0,0,0,0,0,0.4</code>
        </div>
      </div>
    </div>`;
}
async function saveVoice(){
  // 解析情感向量：允许逗号/空格/换行分隔，夹到 0-1，补齐/截断到 8 个
  const rawVec = (document.getElementById("vf-evec").value||"")
    .split(/[,，\s]+/).map(x=>x.trim()).filter(x=>x!=="")
    .map(x=>parseFloat(x)).filter(x=>!isNaN(x)).map(x=>Math.max(0,Math.min(1,x)));
  while(rawVec.length<8)rawVec.push(0);
  const emoVec = rawVec.slice(0,8);
  const emoMode = document.getElementById("vf-emode").value;
  if(emoMode==="vector" && emoVec.every(x=>x===0)){
    if(!confirm("情感向量全是 0，等于没有情感（会退回「跟随音色参考」）。仍要保存吗？"))return;
  }
  const data = {
    voice_enabled: document.getElementById("vf-enabled").value === "true",
    voice_name: document.getElementById("vf-name").value.trim(),
    voice_api_url: document.getElementById("vf-url").value.trim(),
    voice_speed: parseFloat(document.getElementById("vf-speed").value)||1.0,
    voice_timeout: parseInt(document.getElementById("vf-timeout").value)||90,
    voice_random_chance: parseFloat(document.getElementById("vf-rand").value)||0,
    voice_max_chars: parseInt(document.getElementById("vf-maxlen").value)||200,
    voice_trigger_keywords: textToList(document.getElementById("vf-kw").value),
    voice_emotion_mode: emoMode,
    voice_emotion_vector: emoVec,
    voice_emotion_strength: Math.max(0,Math.min(1,
      parseFloat(document.getElementById("vf-estr").value)||0.65)),
    voice_postprocess_preset: (document.getElementById("vf-pp")||{}).value||"off",
    voice_postprocess_strength: Math.max(0,Math.min(1,
      parseFloat((document.getElementById("vf-pps")||{}).value)||1.0)),
  };
  const keyInput = document.getElementById("vf-key").value.trim();
  if(keyInput){ data.voice_api_key = keyInput; }
  const r = await post("/admin/api/config", data);
  if(r.error){toast("保存失败："+r.error,"err");return;}
  toast("语音配置已保存，立即生效");
}
async function testVoice(){
  const text = document.getElementById("voiceTestText").value.trim();
  if(!text){toast("请输入测试文本","err");return;}
  document.getElementById("voiceTestRes").textContent = "生成中，请稍候…";
  const r = await api("/api/voice/test?text=" + encodeURIComponent(text));
  document.getElementById("voiceTestRes").textContent = JSON.stringify(r, null, 2);
  if(r.ok) toast(`语音生成成功（${r.size_kb}KB）`);
  else toast("生成失败："+(r.error||"未知"),"err");
}
async function sendVoice(){
  const gid = document.getElementById("voiceSendGid").value.trim();
  const text = document.getElementById("voiceSendText").value.trim();
  if(!gid||!text){toast("请填群号和文本","err");return;}
  document.getElementById("voiceSendRes").textContent = "发送中，请稍候…";
  const r = await post("/api/voice/send", {group_id:parseInt(gid), text:text});
  document.getElementById("voiceSendRes").textContent = JSON.stringify(r, null, 2);
  if(r.ok) toast("语音已发送");
  else toast("发送失败："+(r.error||"未知"),"err");
}

// ---------- 原始发言（收在记忆页里，当参考用）----------
function renderRaw(items, showWhere){
  const arr=items.slice().reverse();
  let h='<div class="raw-wrap">';
  for(const m of arr){
    const nm=m.is_bot?"诗":((m.nickname&&m.nickname.trim())?m.nickname:("QQ"+m.user_id));
    const t=(m.created_at||"").replace("T"," ").slice(5,16);
    const where=m.group_id===0?"私聊":("群"+m.group_id);
    h+=`<div class="raw-row${m.is_bot?" bot":""}">`
      +`<span class="raw-t">${t}</span>`
      +`<span class="raw-n">${esc(nm)}</span>`
      +`<span class="raw-x">${esc(m.text)}</span>`
      + (showWhere?`<span class="raw-w">${where}</span>`:"")
      +`</div>`;
  }
  return h+'</div>';
}
async function toggleRaw(key, gid, uid, showWhere){
  const box=document.getElementById("raw_"+key);
  if(!box)return;
  box.dataset.where = showWhere?"1":"";
  if(box.dataset.open==="1"){
    box.dataset.open="0"; box.innerHTML=""; box.style.display="none"; return;
  }
  box.dataset.open="1"; box.style.display="block";
  box.innerHTML='<div class="hint" style="padding:10px">加载中…</div>';
  let q="/admin/api/chatlog?limit=120";
  if(gid!==null&&gid!==undefined&&gid!=="") q+="&group_id="+gid;
  if(uid) q+="&user_id="+uid;
  const r=await api(q);
  if(!r||!r.items){ box.innerHTML='<div class="hint" style="padding:10px">读取失败</div>'; return; }
  if(!r.items.length){ box.innerHTML='<div class="hint" style="padding:10px">还没有记录</div>'; return; }
  box.innerHTML = renderRaw(r.items, !!box.dataset.where)
    + (r.total>r.items.length
       ? `<div class="hint" style="text-align:center;margin-top:8px">只显示最近 ${r.items.length} / 共 ${r.total} 条</div>`
       : "");
}
let _memView="group";
function setMemView(v){
  _memView=v;
  document.querySelectorAll("#memViewSeg button").forEach(b=>b.classList.toggle("on",b.dataset.v===v));
  document.getElementById("memUserSel").style.display=(v==="user"?"":"none");
  loadMem();
}
async function loadMem(){
  if(_memView==="user"){ await loadMemByUser(); } else { await loadMemByGroup(); }
}
async function loadMemByGroup(){
  const data=await api("/admin/api/memories?view=group");
  const el=document.getElementById("memList");
  if(!Array.isArray(data)||!data.length){
    el.innerHTML='<div class="empty"><div class="msg">还没有任何记忆</div></div>'; return;
  }
  let h="";
  for(const g of data){
    const gid=g.group_id;
    const gname = gid===0 ? "私聊" : ("群 "+gid);
    const ucnt=g.user_count||0;
    const grk="g"+gid;
    h+=`<div class="group-block"><div class="group-head">`
      +`<span><b>${gname}</b> · ${g.count} 条记忆`
      +(ucnt?` <span style="color:var(--text-3)">（用户专属 ${ucnt}）</span>`:"")+`</span>`
      +`<span style="display:flex;gap:8px">`
      +`<button class="btn sm" onclick="toggleRaw('${grk}',${gid},0)">看原始发言</button>`
      +`<button class="btn danger sm" onclick="clearMem(${gid})">清空本群</button>`
      +`</span></div><div id="raw_${grk}" style="display:none"></div>`;
    const gmem=g.memories.filter(m=>(m.scope||"group")==="group");
    const umem=g.memories.filter(m=>(m.scope||"group")==="user");
    for(const m of gmem){
      h+=`<div class="mem-row"><span class="mem-tag">群级 · ${esc(m.category||"其他")} · ${m.importance}星</span>`
        +`<span class="mem-txt">${esc(m.content)}</span>`
        +`<button class="btn sm" onclick="delMem(${m.id})">删</button></div>`;
    }
    const byUser={};
    for(const m of umem){ (byUser[m.user_id]=byUser[m.user_id]||[]).push(m); }
    for(const uid of Object.keys(byUser)){
      const rk="g"+gid+"u"+uid;
      h+=`<div class="group-head" style="background:var(--card-bg)"><span>`
        +`<b>用户 ${uid}</b> · ${byUser[uid].length} 条</span><span style="display:flex;gap:8px">`
        +`<button class="btn sm" onclick="toggleRaw('${rk}',${gid},${uid})">看原始发言</button>`
        +`<button class="btn sm" onclick="clearMemUser(${gid},${uid})">清空此人在本群</button>`
        +`</span></div><div id="raw_${rk}" style="display:none"></div>`;
      for(const m of byUser[uid]){
        h+=`<div class="mem-row"><span class="mem-tag">用户 · ${esc(m.category||"其他")} · ${m.importance}星</span>`
          +`<span class="mem-txt">${esc(m.content)}</span>`
          +`<button class="btn sm" onclick="delMem(${m.id})">删</button></div>`;
      }
    }
    h+=`</div>`;
  }
  el.innerHTML=h;
  document.getElementById("memViewHint").innerHTML =
    `共 <b>${data.length}</b> 个会话有记忆`;
}
async function loadMemByUser(){
  const sel=document.getElementById("memUserSel");
  if(!sel.options.length){
    const r=await api("/admin/api/memories?view=user");
    const us=(r&&r.users)||[];
    if(!us.length){
      document.getElementById("memList").innerHTML='<div class="empty"><div class="msg">还没有任何用户专属记忆</div></div>';
      document.getElementById("memViewHint").textContent=""; return;
    }
    sel.innerHTML=us.map(u=>`<option value="${u.user_id}">QQ${u.user_id} · ${u.cnt} 条 · ${u.gcnt} 个群</option>`).join("");
  }
  const uid=sel.value;
  if(!uid)return;
  const r=await api("/admin/api/memories?view=user&user_id="+uid);
  const el=document.getElementById("memList");
  const ms=(r&&r.memories)||[];
  if(!ms.length){ el.innerHTML='<div class="empty"><div class="msg">这个人没有专属记忆</div></div>'; return; }
  const gl=(r.groups||[]).map(g=>(g.group_id===0?"私聊":"群"+g.group_id)+" "+g.cnt+"条").join(" · ");
  let h=`<div class="group-block"><div class="group-head">`
    +`<span><b>用户 ${uid}</b> · 共 ${ms.length} 条 <span style="color:var(--text-3)">（${gl}）</span></span>`
    +`<span style="display:flex;gap:8px">`
    +`<button class="btn sm" onclick="toggleRaw('uall${uid}',-1,${uid},1)">看他全部发言</button>`
    +`<button class="btn danger sm" onclick="clearMemUserAll(${uid})">清空此人全部</button>`
    +`</span></div><div id="raw_uall${uid}" style="display:none"></div>`;
  let curG=null;
  for(const m of ms){
    if(m.group_id!==curG){ curG=m.group_id;
      const urk="u"+uid+"g"+curG;
      h+=`<div class="group-head" style="background:var(--card-bg)"><span>${curG===0?"私聊":"群 "+curG}</span>`
        +`<button class="btn sm" onclick="toggleRaw('${urk}',${curG},${uid})">看这里的发言</button></div>`
        +`<div id="raw_${urk}" style="display:none"></div>`; }
    h+=`<div class="mem-row"><span class="mem-tag">${esc(m.category||"其他")} · ${m.importance}星</span>`
      +`<span class="mem-txt">${esc(m.content)}</span>`
      +`<button class="btn sm" onclick="delMem(${m.id})">删</button></div>`;
  }
  el.innerHTML=h+`</div>`;
  document.getElementById("memViewHint").innerHTML=
    `这个人在 <b>${(r.groups||[]).length}</b> 个会话里被记住`;
}
async function clearMemUserAll(uid){
  if(!confirm("清空这个人在【所有群】的专属记忆？群级记忆不受影响。"))return;
  const r=await api("/admin/api/memories/clear_user_all?user_id="+uid,{method:"POST"});
  if(r&&r.ok){ toast("已删除 "+r.deleted+" 条（涉及 "+r.groups+" 个会话）");
    document.getElementById("memUserSel").innerHTML=""; loadMem(); pollStats(); }
  else { toast("失败："+((r&&r.error)||"未知")); }
}
async function clearMemUser(gid,uid){
  if(!confirm("清空该用户的专属记忆？群级记忆不受影响。"))return;
  await api("/admin/api/memories/clear_user?group_id="+gid+"&user_id="+uid,{method:"POST"});
  toast("已清空");loadMem();pollStats();
}
function toggleMemAdd(){
  const b=document.getElementById("memAddBox");
  b.style.display = (b.style.display==="none" ? "block" : "none");
}
function memScopeChanged(){
  const s=document.getElementById("maScope").value;
  document.getElementById("maUserWrap").style.display = (s==="user" ? "" : "none");
}
async function submitMemAdd(){
  const scope=document.getElementById("maScope").value;
  const gid=parseInt(document.getElementById("maGroup").value||"0",10);
  const uid=parseInt(document.getElementById("maUser").value||"0",10);
  const content=document.getElementById("maText").value.trim();
  const cat=document.getElementById("maCat").value;
  const imp=parseInt(document.getElementById("maImp").value||"3",10);
  if(!content){toast("请填写要记住的内容");return;}
  if(scope==="user" && !uid){toast("用户级记忆必须填 QQ 号");return;}
  const r=await api("/admin/api/memories/add",{method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({group_id:isNaN(gid)?0:gid, user_id:isNaN(uid)?0:uid,
      scope:scope, content:content, category:cat, importance:imp})});
  if(r && r.ok){toast("已添加");document.getElementById("maText").value="";loadMem();pollStats();}
  else {toast("添加失败："+((r&&r.error)||"未知错误"));}
}

async function delMem(id){await api("/admin/api/memories/delete?id="+id,{method:"POST"});toast("已删除");loadMem();pollStats();}
async function clearMem(gid){if(!confirm("清空该群所有记忆？"))return;await api("/admin/api/memories/clear?group_id="+gid,{method:"POST"});toast("已清空");loadMem();pollStats();}

// ---------------- 知识库 ----------------
const kbState={game:"",cat:"",folder:null,folders:[],rootCount:0,sel:new Set(),collapsed:new Set()};
const KB_FOLDER_NAME="📁 ";

// 把扁平路径列表变成树：a/b/c -> {a:{b:{c:{}}}}
function kbBuildTree(paths){
  const root={};
  for(const p of paths){
    const parts=String(p).split("/").filter(Boolean);
    let node=root;
    for(const seg of parts){
      node[seg]=node[seg]||{__children:{}};
      node=node[seg].__children;
    }
  }
  return root;
}
function kbCountUnder(prefix,folders){
  // 该路径自身 + 所有子路径的条目数
  let n=0;
  for(const f of folders){
    if(f.path===prefix||f.path.startsWith(prefix+"/")) n+=f.count;
  }
  return n;
}
// 折叠状态：记录"被收起"的路径（默认全部展开）
function kbToggleCollapse(enc){
  const p=kbDec(enc);
  if(kbState.collapsed.has(p))kbState.collapsed.delete(p);
  else kbState.collapsed.add(p);
  kbRenderTree();
}
function kbCollapseAll(){
  const parents=new Set();
  for(const f of (kbState.folders||[])){
    const parts=f.path.split("/");
    let acc="";
    for(let i=0;i<parts.length-1;i++){
      acc=acc?acc+"/"+parts[i]:parts[i];
      parents.add(acc);
    }
  }
  kbState.collapsed=new Set(parents);
  kbRenderTree();
}
function kbExpandAll(){kbState.collapsed.clear();kbRenderTree();}
// 「全部群」视图下列出的是所有群的文件夹合并结果，但增删改只能落到
// 某一个群（或共享域 group_id=0）。在这个视图里操作会语义错位：
//   删   -> 后端按 g=0 删，而那个 path 属于群 A，实际什么都没删，
//           前端却照样弹"已删除"
//   改名 -> 用 group_id=-1 把**所有群**里同名文件夹的条目一起搬走
//   归档 -> 把所有群未归档的条目塞进共享域的同名文件夹
// 所以这个视图下直接把这些操作禁掉，提示切到具体群。
function kbAllGroups(){
  const v=document.getElementById("kbGroupFilter");
  if(!v)return true;
  const n=parseInt(v.value);
  return isNaN(n)||n<0;
}
function kbNeedGroup(){
  toast("先在左上角切到一个具体的群，再操作文件夹","err");
}
function kbFolderRow(path,label,depth,count,active,hasKids){
  const pad=8+depth*14;
  const collapsed=kbState.collapsed.has(path);
  // 有子级才显示箭头；点箭头只折叠，不切换当前文件夹
  const arrow=hasKids
    ?`<span onclick="event.stopPropagation();kbToggleCollapse('${kbEnc(path)}')"
        style="width:12px;flex:0 0 12px;text-align:center;opacity:.7;cursor:pointer;font-size:10px"
        title="${collapsed?"展开":"收起"}">${collapsed?"\u25b6":"\u25bc"}</span>`
    :`<span style="width:12px;flex:0 0 12px"></span>`;
  const _all=kbAllGroups();
  const _dis=_all?' disabled title="「全部群」视图下不能改文件夹，先切到具体群"':'';
  const actions=active?`<span style="margin-left:auto;display:flex;gap:4px;flex:0 0 auto">
      <button class="btn sm kb-tree-act"${_dis} style="padding:2px 6px;font-size:11px" onclick="event.stopPropagation();kbRenameFolder('${kbEnc(path)}')">改名</button>
      <button class="btn sm kb-tree-act"${_dis} style="padding:2px 6px;font-size:11px" onclick="event.stopPropagation();kbDeleteFolder('${kbEnc(path)}')">删</button>
    </span>`:"";
  return `<div class="kb-tree-row${active?" on":""}" onclick="kbPickFolder('${kbEnc(path)}')" style="padding-left:${pad}px">
      ${arrow}
      <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${KB_FOLDER_NAME}${esc(label)}</span>
      ${actions||`<span style="margin-left:auto;opacity:.6;font-size:11px;flex:0 0 auto">${count}</span>`}
    </div>`;
}
function kbRenderTree(){
  const el=document.getElementById("kbFolderTree");
  const total=kbState.folders.reduce((a,b)=>a+b.count,0)+kbState.rootCount;
  let h=`<div class="kb-tree-row${kbState.folder===""?" on":""}" onclick="kbPickFolder('')">
      <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">🗂 根目录（未归档）</span>
      <span style="margin-left:auto;opacity:.6;font-size:11px">${kbState.rootCount}</span></div>`;
  h+=`<div class="kb-tree-row${kbState.folder===null?" on":""}" onclick="kbPickFolder('__all__')">
      <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📚 全部条目</span>
      <span style="margin-left:auto;opacity:.6;font-size:11px">${total}</span></div>`;

  // 先算出哪些路径有子级，用于决定是否显示折叠箭头
  const hasKids=new Set();
  for(const f of (kbState.folders||[])){
    const parts=f.path.split("/");
    if(parts.length>1){
      let acc="";
      for(let i=0;i<parts.length-1;i++){
        acc=acc?acc+"/"+parts[i]:parts[i];
        hasKids.add(acc);
      }
    }
  }
  function walk(node,depth,prefix,ancestorCollapsed){
    for(const name of Object.keys(node).sort()){
      const path=prefix?prefix+"/"+name:name;
      if(ancestorCollapsed)continue;      // 祖先被收起 -> 整支不显示
      const cnt=kbCountUnder(path,kbState.folders);
      h+=kbFolderRow(path,name,depth,cnt,kbState.folder===path,hasKids.has(path));
      walk(node[name].__children,depth+1,path,
           ancestorCollapsed||kbState.collapsed.has(path));
    }
  }
  walk(kbBuildTree(kbState.folders.map(f=>f.path)),0,"",false);

  // 未归档条目按分类列成"虚拟文件夹"，方便查看和一键归档
  const cats={};
  for(const it of kbState.catItems||[]){
    const c=it.category||"其他";
    cats[c]=(cats[c]||0)+1;
  }
  const catKeys=Object.keys(cats);
  if(catKeys.length){
    h+=`<div style="margin:10px 0 6px;font-size:11px;color:var(--text-4);letter-spacing:.06em">未归档（按分类）</div>`;
    for(const c of catKeys.sort()){
      const path="%"+c;
      const active=(kbState.folder===path);
      h+=`<div class="kb-tree-row${active?" on":""}" onclick="kbPickFolder('${kbEnc(path)}')">
          <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${KB_FOLDER_NAME}${esc(c)}</span>
          <span style="margin-left:auto;opacity:.6;font-size:11px">${cats[c]}</span></div>`;
    }
    h+=`<button class="btn sm" style="width:100%;margin-top:8px"${kbAllGroups()?' disabled title="「全部群」视图下不能归档，先切到具体群"':''} onclick="kbAutoFile()">一键归档全部（按分类）</button>`;
  }
  // 折叠/展开全部（只有存在多级文件夹时才有意义）
  if(hasKids.size){
    h+=`<div style="display:flex;gap:6px;margin-top:8px">
        <button class="btn sm" style="flex:1" onclick="kbCollapseAll()">全部收起</button>
        <button class="btn sm" style="flex:1" onclick="kbExpandAll()">全部展开</button>
      </div>`;
  }
  el.innerHTML=h;
  kbUpdateSubHint();
}
// 提示「+ 子文件夹」会建在哪，避免建错层级
function kbUpdateSubHint(){
  const el=document.getElementById("kbSubHint");
  const btn=document.getElementById("kbNewSubBtn");
  const btnRoot=document.getElementById("kbNewRootBtn");
  if(!el)return;
  // 「全部群」视图下不能建文件夹（会落到共享域），两个按钮都禁掉。
  // 这里以前只看 kbState.folder，没看群筛选器。
  if(kbAllGroups()){
    el.textContent="当前：全部群 → 先在左上角切到一个具体的群，才能建文件夹";
    if(btn)btn.disabled=true;
    if(btnRoot)btnRoot.disabled=true;
    return;
  }
  const f=kbState.folder;
  if(btnRoot)btnRoot.disabled=false;
  if(f===null){
    el.textContent="当前：全部条目 → 先进入一个文件夹，才能在它里面建子文件夹";
    if(btn)btn.disabled=true;
  }else if(f===""){
    el.textContent="当前：根目录 → 「+ 子文件夹」会建在顶层";
    if(btn)btn.disabled=false;
  }else if(f.startsWith("%")){
    el.textContent="当前：未归档（按分类）→ 这里不能建文件夹";
    if(btn)btn.disabled=true;
  }else{
    el.textContent="当前："+f+" → 「+ 子文件夹」会建在 "+f+" 里面";
    if(btn)btn.disabled=false;
  }
}
async function kbAutoFile(){
  if(kbAllGroups()){kbNeedGroup();return;}
  if(!confirm("把所有未归档的条目，按分类自动放进同名文件夹？\n（已归档的条目不受影响）"))return;
  const g=document.getElementById("kbGroupFilter").value;
  // /kb/move 里那个 group_id 是给 DB.kb_folder_create() 用的：文件夹
  // 定义按群隔离。写死 0 的话，在 A 群归档会在**共享域**建这些分类
  // 文件夹，B 群的文件夹树里也会冒出来。条目本身靠 ids 移动，不受影响。
  const _gn=parseInt(g);
  const gid=(isNaN(_gn)||_gn<0)?0:_gn;
  const d=await api(`/admin/api/kb?group_id=${g}&folder=`);
  const items=d.items||[];
  if(!items.length){toast("没有未归档的条目");return;}
  const byCat={};
  for(const it of items){
    const c=it.category||"其他";
    (byCat[c]=byCat[c]||[]).push(it.id);
  }
  let moved=0;
  for(const c of Object.keys(byCat)){
    const r=await post("/admin/api/kb/move",
      {ids:byCat[c],folder:c,group_id:gid});
    if(!r.error)moved+=(r.moved||0);
  }
  toast(`已归档 ${moved} 条到 ${Object.keys(byCat).length} 个文件夹`);
  loadKB();
}
function kbPickFolder(enc){
  const p=kbDec(enc);
  kbState.folder=(p==="__all__")?null:p;
  kbState.sel.clear();
  loadKB();
}
async function kbLoadFolders(){
  const g=document.getElementById("kbGroupFilter").value;
  const d=await api(`/admin/api/kb/folders?group_id=${g}`);
  kbState.folders=d.folders||[];
  kbState.rootCount=d.root_count||0;
}
// 在顶层创建文件夹
async function kbNewFolderRoot(){
  // 跟 kbAutoFile 对齐：「全部群」视图下 kbCreateFolder 会把
  // group_id=-1 折算成 0（共享域），新文件夹所有群都能看到。
  if(kbAllGroups()){kbNeedGroup();return;}
  const name=prompt("在顶层新建文件夹，名称：\n（用 / 可以一次建多层，例如：鸣潮/剧情）","");
  if(!name)return;
  await kbCreateFolder(name.trim(), "");
}
// 在当前文件夹里创建子文件夹（在「鸣潮」里建就自动带上「鸣潮/」）
async function kbNewFolderHere(){
  if(kbAllGroups()){kbNeedGroup();return;}
  const base=kbCurrentFolderPath();
  const name=prompt("在「"+(base||"顶层")+"」里新建子文件夹，名称：","");
  if(!name)return;
  await kbCreateFolder(name.trim(), base);
}
// 当前所在文件夹的路径；虚拟分类视图 / 全部条目 时返回 ""
function kbCurrentFolderPath(){
  const f=kbState.folder;
  if(!f||f===""||f.startsWith("%"))return "";
  return f;
}
async function kbCreateFolder(name,base){
  if(!name)return;
  // 用户输入里如果带了 /，也当成相对路径拼到 base 后面
  const full=(base?(base+"/"+name):name).replace(/\/+/g,"/").replace(/^\/|\/$/g,"");
  const g=parseInt(document.getElementById("kbGroupFilter").value);
  const r=await post("/admin/api/kb/folder/create",
    {path:full,group_id:(isNaN(g)||g<0)?0:g});
  if(r.error){toast("创建失败："+r.error,"err");return;}
  toast("已创建 "+full);
  kbState.folder=full;      // 创建后直接进入它
  loadKB();
}
async function kbRenameFolder(enc){
  if(kbAllGroups()){kbNeedGroup();return;}
  const old=kbDec(enc);
  const name=prompt("重命名为（用 / 可改层级）：",old);
  if(!name||name===old)return;
  // 以前这里写死 group_id:0 —— 在 A 群里改名，新文件夹却建到了共享域。
  const _g=parseInt(document.getElementById("kbGroupFilter").value);
  const r1=await post("/admin/api/kb/folder/create",
    {path:name,group_id:(isNaN(_g)||_g<0)?0:_g});
  if(r1.error){toast("失败："+r1.error,"err");return;}
  // 把旧文件夹里的条目搬过去，再删旧文件夹
  // group_id=-1 会把**所有群**里 folder==old 的条目都捞出来，
  // 而 kb_set_folders_bulk 只按 id 更新、不看 group —— 群 A 和群 B
  // 都有「剧情」时，改 A 的会把 B 的一起改。这里限定当前群。
  const items=await api(`/admin/api/kb?group_id=${(isNaN(_g)||_g<0)?0:_g}&folder=${encodeURIComponent(old)}`);
  const ids=(items.items||[]).map(x=>x.id);
  if(ids.length)await post("/admin/api/kb/move",
    {ids:ids,folder:name,group_id:(isNaN(_g)||_g<0)?0:_g});
  const g=parseInt(document.getElementById("kbGroupFilter").value);
  await post("/admin/api/kb/folder/delete",
    {path:old,group_id:(isNaN(g)||g<0)?0:g});
  toast(`已重命名为 ${name}（搬了 ${ids.length} 条）`);
  kbState.folder=name;
  loadKB();
}
async function kbDeleteFolder(enc){
  if(kbAllGroups()){kbNeedGroup();return;}
  const p=kbDec(enc);
  if(!confirm(`删除文件夹「${p}」？\n里面的条目会移回根目录，不会删除条目。`))return;
  // 必须带上当前群：kb_folders 是按 group_id 隔离的，
  // 不传的话后端会把所有群的同名文件夹一起删掉。
  const g=parseInt(document.getElementById("kbGroupFilter").value);
  const r=await post("/admin/api/kb/folder/delete",
    {path:p,group_id:(isNaN(g)||g<0)?0:g});
  if(r.error){toast("失败："+r.error,"err");return;}
  toast("已删除文件夹（条目已移回根目录）");
  if(kbState.folder===p)kbState.folder=null;
  loadKB();
}
async function kbMoveSelected(){
  if(!kbState.sel.size){toast("先勾选要移动的条目","err");return;}
  const names=kbState.folders.map(f=>f.path);
  const hint=names.length?("\n已有文件夹：\n  "+names.slice(0,12).join("\n  ")+
    (names.length>12?"\n  …":"")):"";
  const target=prompt(`把选中的 ${kbState.sel.size} 条移动到哪个文件夹？\n`
    +`填名称即可（不存在会自动创建），留空 = 移回根目录${hint}`,"");
  if(target===null)return;
  // 同上：用户输入一个不存在的文件夹名时，文件夹会按这里的 group_id
  // 建到对应群里，而不是共享域。
  const _mn=parseInt(document.getElementById("kbGroupFilter").value);
  const _mid=(isNaN(_mn)||_mn<0)?0:_mn;
  const r=await post("/admin/api/kb/move",
    {ids:[...kbState.sel],folder:target.trim(),group_id:_mid});
  if(r.error){toast("移动失败："+r.error,"err");return;}
  toast(`已移动 ${r.moved} 条 -> ${r.folder||"根目录"}`);
  kbState.sel.clear();
  loadKB();
}
function kbChip(label,count,active,onclick){
  const style=active
    ?"background:var(--btn-primary-bg);color:#fff;border-color:var(--btn-primary-border)"
    :"background:var(--card-bg);color:var(--text-2);border-color:var(--line-2)";
  return `<button class="btn sm" style="${style}" onclick="${onclick}">${esc(label)}`
    +(count!==undefined&&count!==null?` <span style="opacity:.7;font-weight:400">${count}</span>`:"")
    +`</button>`;
}
// 参数走 URL 编码，避免分类名里的引号/特殊字符破坏 onclick
function kbEnc(v){return encodeURIComponent(v);}
function kbDec(v){return decodeURIComponent(v);}
// 分类/游戏筛选条已取消，导航统一走左侧文件夹树
function kbRenderChips(meta){ return; }
function kbUpdateToolbar(){
  const bar=document.getElementById("kbToolbar");
  bar.style.display="flex";
  document.getElementById("kbSelInfo").textContent="已选 "+kbState.sel.size+" 条";
  const all=document.getElementById("kbSelAll");
  const boxes=document.querySelectorAll(".kb-cb");
  all.checked=boxes.length>0&&[...boxes].every(b=>b.checked);
  const btn=document.getElementById("kbClearCatBtn");
  if(btn){
    if(kbState.folder===null){
      btn.textContent="清空全部条目";
    }else{
      btn.textContent="清空「"+(kbState.folder||"根目录")+"」";
    }
  }
}
function kbToggleOne(id,checked){
  if(checked)kbState.sel.add(id);else kbState.sel.delete(id);
  kbUpdateToolbar();
}
function kbToggleAll(checked){
  document.querySelectorAll(".kb-cb").forEach(b=>{
    b.checked=checked;
    const id=parseInt(b.dataset.id);
    if(checked)kbState.sel.add(id);else kbState.sel.delete(id);
  });
  kbUpdateToolbar();
}
// —— 资源管理器风格：文件夹磁贴 + 条目磁贴，双击打开 ——
function kbRow(it){
  const sc=it.score!==undefined?`<span style="color:var(--amber);font-weight:600"> ${(it.score*100).toFixed(0)}%</span>`:"";
  const tags=(it.tags_list||[]).filter(t=>t!==it.category);
  const tagHtml=tags.length?`<div style="font-size:11px;color:var(--text-4);margin-top:3px">${tags.slice(0,8).map(esc).join(" · ")}</div>`:"";
  const fol=it.folder?`<div style="font-size:11px;color:var(--text-4);margin-top:3px">${KB_FOLDER_NAME}${esc(it.folder)}</div>`:"";
  return `<tr>
    <td><input type="checkbox" class="kb-cb" data-id="${it.id}" ${kbState.sel.has(it.id)?"checked":""}
        onchange="kbToggleOne(${it.id},this.checked)"></td>
    <td class="mono">#${it.id}</td>
    <td><span class="mem-tag">${esc(it.category||"其他")}</span>${fol}</td>
    <td class="strong">${esc(it.question)}${sc}${tagHtml}</td>
    <td style="max-width:340px;white-space:pre-wrap;word-break:break-word">${esc(it.answer)}</td>
    <td class="mono">${it.hits}</td>
    <td>
      <button class="btn sm" onclick="kbEdit(${it.id})">编辑</button>
      <button class="btn danger sm" onclick="kbDelete(${it.id})">删除</button>
    </td></tr>`;
}
function kbRender(items){
  kbState.lastItems=items||[];
  const el=document.getElementById("kbList");
  // 在当前路径下找直接子文件夹，做成可点击的导航行（否则父文件夹看着像空的）
  const cur=kbState.folder;
  let childFolders=[];
  if(cur===null){
    childFolders=(kbState.folders||[]).filter(f=>f.path.indexOf("/")<0);
  }else if(cur!==""&&!cur.startsWith("%")){
    const pref=cur+"/";
    childFolders=(kbState.folders||[]).filter(f=>f.path.startsWith(pref)
      && f.path.slice(pref.length).indexOf("/")<0);
  }
  let navHtml="";
  if(childFolders.length){
    navHtml='<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">'
      +'<span style="font-size:12px;color:var(--text-3);align-self:center">子文件夹：</span>';
    for(const f of childFolders){
      const nm=f.path.split("/").pop();
      navHtml+=`<button class="btn sm" onclick="kbPickFolder('${kbEnc(f.path)}')">`
        +`${KB_FOLDER_NAME}${esc(nm)} <span style="opacity:.6;font-weight:400">`
        +`${kbCountUnder(f.path,kbState.folders)}</span></button>`;
    }
    navHtml+='</div>';
  }
  const head='<table class="tbl"><thead><tr>'
    +'<th style="width:34px"></th><th style="width:52px">编号</th><th style="width:112px">分类</th>'
    +'<th>问题</th><th>答案</th><th style="width:56px">命中</th><th style="width:140px"></th>'
    +'</tr></thead><tbody>';
  if(!items.length){
    const msg=childFolders.length?"这个文件夹里没有直接存放的条目，请点上面的子文件夹"
      :"这个文件夹是空的";
    el.innerHTML=navHtml+head+`<tr><td colspan="7" style="text-align:center;color:var(--text-3);padding:34px 0">${msg}</td></tr></tbody></table>`;
    kbUpdateToolbar();return;
  }
  // 看"全部条目"时按文件夹分组显示，方便一眼看清归属
  if(kbState.folder===null){
    const groups={};
    for(const it of items){
      const f=it.folder||"（未归档）";
      (groups[f]=groups[f]||[]).push(it);
    }
    let h="";
    for(const f of Object.keys(groups)){
      h+=`<tr><td colspan="7" style="background:var(--bg-2);font-weight:600;color:var(--text)">`
        +`${KB_FOLDER_NAME}${esc(f)} <span style="color:var(--text-3);font-weight:400">· ${groups[f].length} 条</span></td></tr>`;
      h+=groups[f].map(kbRow).join("");
    }
    el.innerHTML=navHtml+head+h+"</tbody></table>";
  }else{
    el.innerHTML=navHtml+head+items.map(kbRow).join("")+"</tbody></table>";
  }
  kbUpdateToolbar();
}
function kbBreadcrumb(){return "";}
async function loadKB(){
  const q=document.getElementById("kbQuery").value.trim();
  const g=document.getElementById("kbGroupFilter").value;
  const el=document.getElementById("kbList");
  el.innerHTML='<div class="empty"><div class="big">…</div><div class="msg">加载中</div></div>';
  // 虚拟分类文件夹（%开头）不走后端 folder 过滤，取全量后在前端筛
  const virtualCat=(kbState.folder||"").startsWith("%")?kbState.folder.slice(1):"";
  // folder=null -> 不传该参数（= 不过滤，看全部）；folder="" -> 只看根目录
  const folderQS=kbState.folder===null ? ""
    : ("&folder="+encodeURIComponent(virtualCat?"":kbState.folder));
  const data=await api(`/admin/api/kb?group_id=${g}&q=${encodeURIComponent(q)}`
    +`&category=${encodeURIComponent(kbState.cat)}&game=${encodeURIComponent(kbState.game)}`
    +folderQS);
  if(data.error){el.innerHTML=`<div class="empty"><div class="msg">${esc(data.error)}</div></div>`;return;}
  // 填充群筛选下拉
  const sel=document.getElementById("kbGroupFilter");
  const cur=sel.value;
  let opts='<option value="-1">全部群</option><option value="0">仅全局</option>';
  for(const g2 of (data.groups||[])){
    if(g2.group_id===0)continue;
    opts+=`<option value="${g2.group_id}">群 ${g2.group_id}（${g2.cnt}）</option>`;
  }
  sel.innerHTML=opts; sel.value=cur;
  kbRenderChips(data);
  // 文件夹树：需要全量数据来统计"未归档（按分类）"
  const allForTree=await api(`/admin/api/kb?group_id=${g}&folder=`);
  kbState.catItems=((allForTree.items)||[]);
  await kbLoadFolders();
  kbRenderTree();
  let items=data.items||[];
  if(virtualCat)items=items.filter(x=>(x.category||"其他")===virtualCat);
  const badge=document.getElementById("badge-kb");
  let shown=items.length;
  if(badge)badge.textContent=shown||"";
  kbRender(items);
  document.getElementById("kbPathInfo").textContent=kbFolderLabel();
}
function kbFolderLabel(){
  if(kbState.folder===null)return "全部条目";
  if(kbState.folder==="")return "根目录（未归档）";
  if(kbState.folder.startsWith("%"))return "未归档 · "+kbState.folder.slice(1);
  return kbState.folder;
}
async function kbDeleteSelected(){
  if(!kbState.sel.size){toast("先勾选要删除的条目","err");return;}
  if(!confirm(`删除选中的 ${kbState.sel.size} 条？不可恢复。`))return;
  const r=await post("/admin/api/kb/delete",{ids:[...kbState.sel]});
  if(r.error){toast("删除失败："+r.error,"err");return;}
  toast("已删除 "+r.deleted+" 条");
  kbState.sel.clear();
  loadKB();pollStats();
}
async function kbClearCategory(){
  const cur=kbState.folder;
  const label=(cur===null)?"全部条目"
    :(String(cur).startsWith("%")?"未归档 · "+String(cur).slice(1)
      :(cur||"根目录"));
  if(!confirm(`清空「${label}」下的所有条目？不可恢复。`))return;
  // cur===null 是「全部条目」视图 —— 这时必须传 all:true。
  // 以前传的是 folder:""，后端把 "" 当"根目录"，结果只清了未归档的，
  // 用户看到「清空全部条目」却只清了一部分，而且不可恢复。
  const body={category:"",game:"",
    group_id:parseInt(document.getElementById("kbGroupFilter").value)};
  if(cur===null){
    body.all=true;
  }else if(String(cur).startsWith("%")){
    // "%xxx" 是「未归档 · xxx」的虚拟文件夹，不是真实 folder 值。
    // 直接传下去跟真实 folder 字段永远对不上 -> 删 0 条。
    // 正确拆法：folder="" (未归档) + category=xxx
    body.folder="";
    body.category=String(cur).slice(1);
  }else{
    body.folder=cur;
  }
  const r=await post("/admin/api/kb/clear_category",body);
  if(r.error){toast("清空失败："+r.error,"err");return;}
  toast("已清空 "+r.deleted+" 条（"+r.label+"）");
  kbState.sel.clear();
  loadKB();pollStats();
}
async function kbReclassify(){
  if(!confirm("按问题内容重新打标签（游戏 + 分类）？\n原有分类会被覆盖，具体名词标签保留。"))return;
  const r=await post("/admin/api/kb/reclassify",{group_id:-1});
  if(r.error){toast("失败："+r.error,"err");return;}
  const gs=Object.entries(r.games||{}).map(([k,v])=>k+" "+v).join("，");
  toast(`重新分类完成：${r.total} 条，改动 ${r.changed} 条`);
  alert(`重新分类完成\n\n总计 ${r.total} 条，改动 ${r.changed} 条\n\n游戏分布：${gs}`);
  loadKB();pollStats();
}
async function kbMatchTest(){
  const text=document.getElementById("kbQuery").value.trim();
  if(!text){toast("先在搜索框输入一句话","err");return;}
  const g=document.getElementById("kbGroupFilter").value;
  const r=await api(`/admin/api/kb/search?group_id=${g}&text=${encodeURIComponent(text)}`);
  const box=document.getElementById("kbTestRes");
  box.style.display="block";
  if(r.error){box.innerHTML=`<div class="api-result">${esc(r.error)}</div>`;return;}
  let h=`<div style="font-size:12.5px;color:var(--text-2);margin-bottom:8px">阈值 <b>${r.threshold}</b>　→　${(r.would_hit||[]).length?"会命中并注入 AI":"不命中，交给 AI 自由回答"}</div>`;
  if((r.would_hit||[]).length){
    h+='<div class="api-result">'+r.would_hit.map(x=>`#${x.id} [${(x.score*100).toFixed(0)}%] ${esc(x.question)}`).join("\n")+'</div>';
  }
  if((r.near_miss||[]).length){
    h+=`<div style="font-size:11.5px;color:var(--text-3);margin:8px 0 4px">接近但未达阈值（想放宽就调低「匹配阈值」）：</div>`;
    h+='<div class="api-result">'+r.near_miss.map(x=>`#${x.id} [${(x.score*100).toFixed(0)}%] ${esc(x.question)}`).join("\n")+'</div>';
  }
  box.innerHTML=h;
}
async function kbAdd(){
  const g=prompt("这条知识属于哪个群？\n填 0 = 全局（所有群通用）\n填群号 = 该群专属","0");
  if(g===null)return;
  const q=prompt("问题（群里会怎么问？）","");
  if(!q)return;
  const a=prompt("答案","");
  if(!a)return;
  // 让用户选文件夹；默认落在当前浏览的文件夹
  const names=(kbState.folders||[]).map(f=>f.path);
  const curFolder=(kbState.folder&&!kbState.folder.startsWith("%")&&kbState.folder!=="")?kbState.folder:"";
  const hint=names.length?("\n已有："+names.slice(0,10).join("、")+(names.length>10?"…":"")):"";
  const f=prompt("放进哪个文件夹？（留空 = 根目录，填名称会自动创建）"+hint,curFolder);
  if(f===null)return;
  const r=await post("/admin/api/kb/add",
    {group_id:parseInt(g)||0,question:q,answer:a,folder:f.trim()});
  if(r.error){toast("添加失败："+r.error,"err");return;}
  toast("已新增 #"+r.id+(r.folder?("（"+r.folder+"）"):""));
  loadKB();pollStats();
}
async function kbEdit(id){
  const data=await api(`/admin/api/kb?group_id=-1`);
  const it=(data.items||[]).find(x=>x.id===id);
  if(!it){toast("条目不存在","err");return;}
  const q=prompt("修改问题：",it.question);
  if(q===null)return;
  const a=prompt("修改答案：",it.answer);
  if(a===null)return;
  const g=prompt("所属群号（0 = 全局）：",String(it.group_id));
  if(g===null)return;
  const t=prompt("标签（逗号分隔，可留空）：",it.tags||"");
  if(t===null)return;
  const r=await post("/admin/api/kb/update",{id:id,question:q,answer:a,group_id:parseInt(g)||0,tags:t});
  if(r.error){toast("更新失败："+r.error,"err");return;}
  // 单独问文件夹，允许改归属（留空 = 根目录）
  const f=prompt("所属文件夹（留空 = 根目录；填名称会自动创建）：",it.folder||"");
  if(f!==null&&f.trim()!==(it.folder||"")){
    const r2=await post("/admin/api/kb/move",{ids:[id],folder:f.trim(),group_id:parseInt(g)||0});
    if(r2.error)toast("文件夹未改："+r2.error,"err");
  }
  toast("已更新 #"+id);loadKB();
}
async function kbDelete(id){
  if(!confirm("删除 #"+id+" ？"))return;
  const r=await post("/admin/api/kb/delete",{ids:[id]});
  if(r.error){toast("删除失败："+r.error,"err");return;}
  toast("已删除");loadKB();pollStats();
}
async function kbExport(){
  const g=document.getElementById("kbGroupFilter").value;
  const r=await api(`/admin/api/kb/export?group_id=${g}`);
  if(r.error){toast("导出失败："+r.error,"err");return;}
  const blob=new Blob([r.text],{type:"text/plain;charset=utf-8"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob);
  a.download=`知识库-${g==="-1"?"全部":("群"+g)}-${r.count}条.txt`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast("已导出 "+r.count+" 条");
}
async function kbImport(){
  // parseInt("-1")||0 是 -1（-1 是 truthy，|| 拦不住）。
  // 负数会变成"幽灵群"：kb_list 的过滤是 group_id IN (0,?)，-1 的条目
  // 在任何具体群里都查不到，聊天也用不上 —— 等于白导入。
  const _ig=parseInt(document.getElementById("kbImportGroup").value);
  const g=(isNaN(_ig)||_ig<0)?0:_ig;
  const text=document.getElementById("kbImportText").value;
  if(!text.trim()){toast("请先粘贴内容","err");return;}
  const box=document.getElementById("kbImportRes");
  box.style.display="block";
  box.textContent="导入中…";
  const r=await post("/admin/api/kb/import",{group_id:g,text:text});
  if(r.error){box.textContent="失败："+r.error;toast("导入失败","err");return;}
  box.textContent=`成功 ${r.added} 条`+(r.failed_count?`，失败 ${r.failed_count} 条：\n`+r.failed.join("\n"):"");
  toast("已导入 "+r.added+" 条");
  document.getElementById("kbImportText").value="";
  loadKB();pollStats();
}
async function loadBL(){const data=await api("/admin/api/blacklist");const el=document.getElementById("blList");if(!data.length){el.innerHTML='<div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path d="M5.5 5.5l13 13"/></svg><div class="msg">暂无黑名单记录</div></div>';return;}let h='<table class="tbl"><thead><tr><th>QQ</th><th>群号</th><th>昵称</th><th>原因</th><th>时间</th><th></th></tr></thead><tbody>';for(const e of data){h+=`<tr><td class="mono">${e.user_id}</td><td class="mono">${e.group_id}</td><td class="strong">${esc(e.nickname||"-")}</td><td>${esc(e.reason||"")}</td><td class="mono" style="color:var(--text-4)">${fmtT(e.added_at)}</td><td><button class="btn danger sm" onclick="delBL(${e.user_id},${e.group_id})">解除</button></td></tr>`;}h+="</tbody></table>";el.innerHTML=h;}
async function delBL(u,g){await api(`/admin/api/blacklist/remove?user_id=${u}&group_id=${g}`,{method:"POST"});toast("已解除");loadBL();pollStats();}
async function addBL(){const u=document.getElementById("blQQ").value.trim();const g=document.getElementById("blGID").value.trim();if(!u||!g){toast("请填写 QQ 和群号","err");return;}await api(`/admin/api/blacklist/add?user_id=${u}&group_id=${g}`,{method:"POST"});toast("已拉黑");document.getElementById("blQQ").value="";loadBL();pollStats();}
async function loadVF(){const data=await api("/admin/api/pending");const el=document.getElementById("vfList");if(!data.length){el.innerHTML='<div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path d="M9 12l2 2 4-4"/></svg><div class="msg">当前无待验证成员</div></div>';return;}const now=Date.now()/1000;let h='<table class="tbl"><thead><tr><th>QQ</th><th>群号</th><th>昵称</th><th>剩余</th></tr></thead><tbody>';for(const e of data){const left=Math.max(0,Math.round(e.deadline-now));h+=`<tr><td class="mono">${e.user_id}</td><td class="mono">${e.group_id}</td><td class="strong">${esc(e.nickname||"")}</td><td style="color:var(--amber);font-weight:600">${left}s</td></tr>`;}h+="</tbody></table>";el.innerHTML=h;}
async function loadPersona(){const cfg=await api("/admin/api/config");const el=document.getElementById("personaForm");el.innerHTML=`<div class="form-grid"><div class="form-row full"><label>人格名字</label><input id="p-name" value="${esc(cfg.persona_name||"")}"></div><div class="form-row full"><label>人格设定</label><textarea id="p-identity">${esc(cfg.persona_identity||"")}</textarea></div><div class="form-row full"><label>行为准则</label><textarea id="p-behavior">${esc(cfg.persona_behavior||"")}</textarea></div><div class="form-row full"><label>表达风格</label><textarea id="p-speech">${esc(cfg.persona_speech||"")}</textarea></div></div>`;}
async function savePersona(){
  const data={persona_name:document.getElementById("p-name").value,persona_identity:document.getElementById("p-identity").value,persona_behavior:document.getElementById("p-behavior").value,persona_speech:document.getElementById("p-speech").value};
  try{
    const r=await post("/admin/api/config",data);
    if(!r||r.error){toast("人格保存失败："+((r&&r.error)||"无响应"),"err");return;}
    toast("人格已保存（改了 "+(r.changed||0)+" 项），立即生效");
  }catch(e){toast("人格保存异常："+(e&&e.message||e),"err");}
}

// 每个插件一个独立的配置小窗。传插件名（ctx.name）。
async function configFor(name){
  let schema=[], cfg={};
  try{ schema=await api("/admin/api/plugins/schema")||[]; }catch(e){ schema=[]; }
  try{ cfg=await api("/admin/api/config")||{}; }catch(e){ cfg={}; }
  const groups=schema.filter(function(g){return g.plugin===name;});
  if(!groups.length){ toast("这个插件没有可配置的项","err"); return; }
  const mask=document.getElementById("plugCfgMask");
  const body=document.getElementById("plugCfgBody");
  const ttl=document.getElementById("plugCfgTitle");
  if(!mask||!body){ toast("界面缺少配置小窗","err"); return; }
  if(ttl)ttl.textContent=(groups[0].plugin_title||name)+" · 配置";
  let h="";
  for(let gi=0;gi<groups.length;gi++){
    const g=groups[gi];
    if(groups.length>1)h+='<div class="cfg-group-title">'+esc(g.title)+'</div>';
    h+='<div class="form-grid">';
    for(const f of g.fields){
      const k=f[0], label=f[1], type=f[2];
      const v=cfg[k];
      h+='<div class="form-row'+((type==="text")?" full":"")+'">';
      h+='<label>'+esc(label)+'</label>';
      if(type==="bool"){
        h+='<select id="pcfg-'+k+'">'
          +'<option value="true"'+(v?" selected":"")+'>开启</option>'
          +'<option value="false"'+(!v?" selected":"")+'>关闭</option></select>';
      }else if(type==="number"){
        h+='<input id="pcfg-'+k+'" type="number" step="any" value="'+(v===undefined||v===null?"":esc(v))+'">';
      }else{
        h+='<input id="pcfg-'+k+'" value="'+esc(v===undefined||v===null?"":v)+'">';
      }
      h+='</div>';
    }
    h+='</div>';
  }
  body.innerHTML=h;
  mask.hidden=false;
  document.addEventListener("keydown",_plugCfgKey);
}

function _plugCfgKey(e){ if(e.key==="Escape")closePlugCfg(); }

function closePlugCfg(){
  const mask=document.getElementById("plugCfgMask");
  if(mask)mask.hidden=true;
  document.removeEventListener("keydown",_plugCfgKey);
}

async function savePlugCfg(){
  const cfg={};
  let n=0;
  document.querySelectorAll("#plugCfgBody [id^='pcfg-']").forEach(function(el){
    const k=el.id.slice(5);
    if(!k)return;
    let v=el.value;
    if(el.tagName==="SELECT"){
      v=(el.value==="true");
    }else if(el.type==="number"){
      v=parseFloat(el.value);if(isNaN(v))v=0;
    }
    cfg[k]=v; n++;
  });
  if(!n){ toast("没有可保存的项","err"); return; }
  const r=await post("/admin/api/config",cfg);
  if(!r||r.error){ toast("保存失败："+((r&&r.error)||"无响应"),"err"); return; }
  toast("已保存 "+((r.changed===undefined)?n:r.changed)+" 项，立即生效");
  closePlugCfg();
}

// ---------- 壁纸 ----------
async function loadWallState(){
  const prev=document.getElementById("wallPrev");
  const tip=document.getElementById("wallTip");
  if(!prev)return;
  try{
    const s=await api("/admin/api/wallpaper/state");
      // --wall 可能是 url("data:...")，也可能是渐变。以前一律套一层
      // url(...)，遇到渐变就拼成 url(linear-gradient(...)) —— 无效 CSS，
      // 浏览器整条丢弃，预览图还停在上一个值。
      const _wv=(getComputedStyle(document.documentElement)
        .getPropertyValue("--wall")||"").trim();
      prev.style.backgroundImage=_wv.startsWith("url(")?_wv:"";
    prev.className="wall-prev"+(s.on?"":" off");
    if(tip)tip.textContent=s.custom
      ? ("当前：自定义壁纸（"+s.name+"，"+(s.size/1024).toFixed(0)+" KB）")
      : "当前：内置默认壁纸。选了图片就会换成你自己的。";
  }catch(e){
    if(tip)tip.textContent="读不到壁纸状态："+e.message;
  }
}
function shrinkImage(file,maxW){
  // 先在浏览器里缩小再上传 —— 一张 4MB 的图直接 base64 会变成 5MB 的请求体
  return new Promise(function(res,rej){
    const fr=new FileReader();
    fr.onerror=function(){rej(new Error("读文件失败"))};
    fr.onload=function(){
      const img=new Image();
      img.onerror=function(){rej(new Error("这不是张能解码的图片"))};
      img.onload=function(){
        let w=img.width,h=img.height;
        if(w>maxW){h=Math.round(h*maxW/w);w=maxW}
        const cv=document.createElement("canvas");
        cv.width=w;cv.height=h;
        const cx=cv.getContext("2d");
        cx.fillStyle="#0b1120";cx.fillRect(0,0,w,h);   // PNG 透明处垫个底色
        cx.drawImage(img,0,0,w,h);
        const out=cv.toDataURL("image/jpeg",0.86);
        res({data:out.split(",",2)[1],ext:".jpg",w:w,h:h});
      };
      img.src=fr.result;
    };
    fr.readAsDataURL(file);
  });
}
async function pickWall(input){
  const f=input.files&&input.files[0];
  if(!f)return;
  if(f.size>24*1024*1024){toast("图太大了（>24MB），先压一下","err");input.value="";return}
  const tip=document.getElementById("wallTip");
  if(tip)tip.textContent="处理中…";
  try{
    const s=await shrinkImage(f,2560);
    const r=await api("/admin/api/wallpaper",{method:"POST",
      body:JSON.stringify({data:s.data,ext:s.ext})});
    // 立刻套上去 —— 不然用户以为没生效
    window.__wallCustomUri="url(data:image/jpeg;base64,"+s.data+")";
    document.documentElement.style.setProperty("--wall",window.__wallCustomUri);
    // 关键：把 preset 也切成 custom，否则保存后读到的还是旧预设，
    // 用户上传的图下次打开就丢了
    applyAppearance({wallpaper_preset:"custom"});
    if(tip)tip.textContent="已换成你的壁纸（"+s.w+"×"+s.h+"，"+
      ((r.size||0)/1024).toFixed(0)+" KB）。点「保存」把开关也存下来。";
    toast("壁纸已应用");
  }catch(e){
    if(tip)tip.textContent="换壁纸失败："+e.message;
    toast("换壁纸失败："+e.message,"err");
  }
  input.value="";
}
async function resetWall(){
  try{
    await api("/admin/api/wallpaper/reset",{method:"POST"});
    toast("已恢复默认壁纸，正在刷新…");
    setTimeout(function(){location.reload()},600);
  }catch(e){toast("恢复失败："+e.message,"err")}
}
function _hex2rgb(h){
  h=(h||"#000000").replace("#","");
  if(h.length===3)h=h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
  return [parseInt(h.slice(0,2),16)||0,parseInt(h.slice(2,4),16)||0,
          parseInt(h.slice(4,6),16)||0];
}
function _sw(on,id,key){
  return `<div class="sw${on?" on":""}" id="${id}" data-key="${key}"`
    +` data-val="${on?"true":"false"}"></div>`;
}
function _sld(id,val,min,max,step,unit,label,hint){
  return `<div class="set-row"><div class="set-t"><b>${label}</b>`
    +`<span>${hint||""}</span></div><div class="set-c">`
    +`<div class="sld"><input type="range" id="${id}" min="${min}" max="${max}"`
    +` step="${step}" value="${val}" data-key="${id}"`
    +` data-unit="${unit}">`
    +`<div class="v" id="${id}-v">${val}${unit}</div></div></div></div>`;
}
// 色板 id -> 配置键。四个都要认，少一个就会串到别的键上。
// 色板 id -> 配置键。
// 注意：sws-side-dark / sws-face-dark 目前**没有界面入口**
// （「深色模式」那一节按用户要求去掉了）。留着是为了以后恢复 UI 时
// 直接接回来；对应的配置键 ui_side_color_dark / ui_face_color_dark
// 仍在生效 —— 深色模式那段 @media 要读它们。
function _swKeyOf(id){
  if(id==="sws-side")return "ui_side_color";
  if(id==="sws-side-dark")return "ui_side_color_dark";
  if(id==="sws-face-dark")return "ui_face_color_dark";
  return "ui_face_color";
}
function _sws(id,cur,colors,label,hint){
  let s=`<div class="set-row"><div class="set-t"><b>${label}</b>`
    +`<span>${hint||""}</span></div><div class="set-c"><div class="sws" id="${id}">`;
  for(const c of colors){
    s+=`<i class="${c.toLowerCase()===String(cur).toLowerCase()?"sel":""}"`
      +` style="background:${c}" data-color="${c}" data-preset-dot="1"`
      +` title="${c}"></i>`;
  }
  s+=`<span class="cus" data-custom="${id}">自定义</span>`;
  const key=_swKeyOf(id);
  s+=`<input type="hidden" id="${id}-v" data-key="${key}" value="${cur}">`;
  s+=`</div></div></div>`;
  return s;
}
// 色板预设值。这两个常量曾被误删过（清理旧函数时连带删掉了），
// 结果 renderAppearance 报 "SIDE_COLORS is not defined"，整页渲染不出来。
// 以前末尾还有个 #101010 —— 跟 #12161d 肉眼分不出来，两颗一样的黑。
// 参考图那种色板是"颗颗能分清"，重复的色块只会让人以为点错了。
const SIDE_COLORS = ["#0a1022", "#12161d", "#0d2b3a", "#2a1730",
                     "#3a1a12", "#ffffff"];
// 同上：末尾的 #101010 跟 #0a0a0d 分不出来，删掉。
const FACE_COLORS = ["#ffffff", "#0a0a0d", "#1ec8d8", "#f0a0c0",
                     "#f0a860", "#f06060"];
// 深色模式下的基底。默认是深蓝紫，跟主题的 --bg-2 一致。
function renderAppearance(cfg){
  const g=function(k,d){return cfg[k]===undefined?d:cfg[k];};
  let h=`<div class="set-row full"><div class="set-t"><b>使用壁纸背景</b>`
    +`<span>关掉就退回纯色底</span></div>`
    +`<div class="set-c">${_sw(!!g("wallpaper_enabled",true),"sw-wall","wallpaper_enabled")}</div></div>`;
  h+=`<div class="set-row full" style="display:block;padding-bottom:6px">`
    +`<div class="set-t" style="margin-bottom:14px"><b>背景</b>`
    +`<span>预设是纯 CSS 渐变 —— 不占体积、任意分辨率都不糊；`
    +`也可以换成自己的图片</span></div>`
    +`<div id="wallSection">${renderWallSection()}</div></div>`;
  h+=`<div class="set-row"><div class="set-t"><b>自动轮播</b>`
    +`<span>页面开着时按间隔自动切换预设壁纸</span></div>`
    +`<div class="set-c">${_sw(!!g("wallpaper_rotate",false),"sw-rotate","wallpaper_rotate")}</div></div>`;
  h+=_sld("wallpaper_rotate_sec",g("wallpaper_rotate_sec",15),5,120,5,"s","轮播间隔","");
  h+=_sld("wallpaper_dim",g("wallpaper_dim",40),0,88,1,"", "壁纸压暗",
          "压得越深，上面的字越清楚；0 就是原图");
  h+=_sld("wallpaper_blur",g("wallpaper_blur",0),0,40,1,"px","壁纸模糊","给底图加虚化");
  h+=`<div class="set-row full" style="padding-top:22px;border-bottom:0">`
    +`<div class="set-t"><b style="font-size:12px;letter-spacing:.14em;`
    +`color:var(--text-3);text-transform:uppercase">窗口与侧栏</b></div>`
    +`<div class="set-c"></div></div>`;
  h+=`<div class="set-row"><div class="set-t"><b>侧栏液态玻璃</b>`
    +`<span>侧栏做毛玻璃，壁纸在上面透出来</span></div>`
    +`<div class="set-c">${_sw(!!g("ui_side_on",true),"sw-side","ui_side_on")}</div></div>`;
  h+=_sld("ui_side_blur",g("ui_side_blur",18),0,40,1,"px","侧栏模糊","");
  h+=_sld("ui_side_alpha",g("ui_side_alpha",72),0,100,1,"%","侧栏不透明度",
          "越低越透，壁纸越明显；太高会看不清字");
  h+=_sws("sws-side",g("ui_side_color","#0a1022"),SIDE_COLORS,"侧栏玻璃颜色",
          "侧栏那层玻璃本身是什么颜色");
  // 「深色模式」小节已去掉（用户要求）。
  // ui_face_color_dark / ui_side_color_dark 两个配置键保留：
  // 深色模式那段 @media 还要读它们，只是不再给界面调。
  h+=_sld("ui_face_blur",g("ui_face_blur",24),0,40,1,"px","内容面模糊","");
  h+=_sld("ui_face_alpha",g("ui_face_alpha",52),0,100,1,"%","内容面不透明度",
          "正文所在的卡片，建议 40 以上");
  h+=_sws("sws-face",g("ui_face_color","#ffffff"),FACE_COLORS,"内容面底色","");
    return h;
}
// ---------------- 取色器 ----------------
// 用一个隐藏的 div 浮层，点色板「自定义」时贴到那一行下面。
// HSV 转换：方块给 S/V，色相条给 H。
let _cp = null, _cpTarget = null, _cpDrag = null;

function _hsv2rgb(h, s, v){
  h=((h%360)+360)%360; s=Math.max(0,Math.min(1,s)); v=Math.max(0,Math.min(1,v));
  const c=v*s, x=c*(1-Math.abs((h/60)%2-1)), m=v-c;
  let r=0,g=0,b=0;
  if(h<60){r=c;g=x;} else if(h<120){r=x;g=c;}
  else if(h<180){g=c;b=x;} else if(h<240){g=x;b=c;}
  else if(h<300){r=x;b=c;} else {r=c;b=x;}
  const to=function(n){return ("0"+Math.round((n+m)*255).toString(16)).slice(-2)};
  return "#"+to(r)+to(g)+to(b);
}
function _rgb2hsv(hex){
  const c=_hex2rgb(hex), r=c[0]/255, g=c[1]/255, b=c[2]/255;
  const mx=Math.max(r,g,b), mn=Math.min(r,g,b), d=mx-mn;
  let h=0;
  if(d){
    if(mx===r)h=60*(((g-b)/d)%6);
    else if(mx===g)h=60*((b-r)/d+2);
    else h=60*((r-g)/d+4);
  }
  if(h<0)h+=360;
  return {h:h, s:mx?d/mx:0, v:mx};
}
function closePicker(){
  if(_cp)_cp.hidden=true;
  _cpTarget=null; _cpDrag=null;
}
function openPicker(anchor, key, cur){
  if(!_cp){
    _cp=document.createElement("div");
    _cp.className="cp"; _cp.hidden=true;
    _cp.innerHTML=
      '<div class="cp-sv"><i></i></div>'
      +'<div class="cp-hue"><i></i></div>'
      +'<div class="cp-foot"><span class="cp-prev"></span>'
      +'<input class="cp-hex" spellcheck="false" maxlength="7"></div>'
      +'<div class="cp-dots"></div>'
      +'<button class="btn sm cp-reset" type="button">恢复默认</button>';
    document.body.appendChild(_cp);
    const sv=_cp.querySelector(".cp-sv"), hue=_cp.querySelector(".cp-hue");
    const hex=_cp.querySelector(".cp-hex"), prev=_cp.querySelector(".cp-prev");
    const dots=_cp.querySelector(".cp-dots");
    ["#0a1022","#141d38","#0d2b3a","#ffffff","#0a0a0d","#1ec8d8",
     "#f0a0c0","#f0a860","#f06060","#8a6d1f"].forEach(function(c){
      const d=document.createElement("i");
      d.style.background=c; d.dataset.cpDot=c; d.title=c;
      dots.appendChild(d);
    });
    const read=function(){
      const st=_cp._st;
      return _hsv2rgb(st.h,st.s,st.v);
    };
    const paint=function(){
      const st=_cp._st;
      _cp.style.setProperty("--cp-h",_hsv2rgb(st.h,1,1));
      const c=read();
      sv.querySelector("i").style.left=(st.s*100)+"%";
      sv.querySelector("i").style.top=((1-st.v)*100)+"%";
      hue.querySelector("i").style.left=(st.h/360*100)+"%";
      prev.style.background=c;
      if(document.activeElement!==hex)hex.value=c;
    };
    _cp._paint=paint;
    const fromEvent=function(el,ev,cb){
      const r=el.getBoundingClientRect();
      const x=Math.max(0,Math.min(1,(ev.clientX-r.left)/r.width));
      const y=Math.max(0,Math.min(1,(ev.clientY-r.top)/r.height));
      cb(x,y);
    };
    const start=function(which){
      return function(ev){
        if(ev.button!==undefined&&ev.button!==0)return;
        ev.preventDefault();
        _cpDrag=which; _cp.setPointerCapture&&_cp.setPointerCapture(ev.pointerId);
        move(ev);
      };
    };
    const move=function(ev){
      if(!_cpDrag)return;
      if(_cpDrag==="sv")fromEvent(sv,ev,function(x,y){
        _cp._st.s=x; _cp._st.v=1-y; paint(); commit();});
      else fromEvent(hue,ev,function(x){
        _cp._st.h=x*360; paint(); commit();});
    };
    sv.addEventListener("pointerdown",start("sv"));
    hue.addEventListener("pointerdown",start("hue"));
    _cp.addEventListener("pointermove",move);
    _cp.addEventListener("pointerup",function(){_cpDrag=null});
    _cp.addEventListener("pointercancel",function(){_cpDrag=null});
    hex.addEventListener("input",function(){
      const v=hex.value.trim();
      if(!/^#?[0-9a-fA-F]{6}$/.test(v))return;
      const c=v[0]==="#"?v:"#"+v;
      const hsv=_rgb2hsv(c);
      _cp._st=hsv; _cp._paint();
    });
    hex.addEventListener("change",function(){
      const v=hex.value.trim();
      if(!/^#?[0-9a-fA-F]{6}$/.test(v)){_cp._paint();return;}
      commit(true);
    });
    dots.addEventListener("click",function(e){
      const d=e.target.closest("[data-cp-dot]");
      if(!d)return;
      _cp._st=_rgb2hsv(d.dataset.cpDot); _cp._paint(); commit(true);
    });
    _cp.querySelector(".cp-reset").addEventListener("click",function(){
      const d=_cp._default||"#0a1022";
      _cp._st=_rgb2hsv(d); _cp._paint(); commit(true);
    });
    document.addEventListener("pointerdown",function(e){
      if(_cp&&!_cp.hidden&&!_cp.contains(e.target)
         &&!(e.target.closest&&e.target.closest("[data-custom]")))closePicker();
    });
    document.addEventListener("keydown",function(e){
      if(e.key==="Escape")closePicker();
    });
  }
  _cpTarget=key;
  _cp._default=cur;
  _cp._st=_rgb2hsv(cur);
  _cp.hidden=false;
  _cp._paint();
  // 贴到锚点下方；右边放不下就左移
  const r=anchor.getBoundingClientRect();
  // fixed 的坐标就是视口坐标，不要再加 scrollY/scrollX
  let top=r.bottom+8;
  let left=r.left;
  const w=_cp.offsetWidth||236, hgt=_cp.offsetHeight||300;
  const vw=document.documentElement.clientWidth;
  const vh=document.documentElement.clientHeight;
  if(left+w>vw-12)left=vw-w-12;
  if(top+hgt>vh-12)top=Math.max(8,r.top-hgt-8);   // 下面放不下就翻到上面
  _cp.style.top=top+"px";
  _cp.style.left=Math.max(8,left)+"px";
  function commit(force){
    const c=_hsv2rgb(_cp._st.h,_cp._st.s,_cp._st.v);
    _cp.querySelector(".cp-hex").value=c;
    if(_cpTarget)applyAppearance({[_cpTarget]:c});
    const box=document.querySelector('[data-custom="'
      +(_cpTarget==="ui_side_color"?"sws-side"
        :_cpTarget==="ui_side_color_dark"?"sws-side-dark"
        :_cpTarget==="ui_face_color_dark"?"sws-face-dark":"sws-face")+'"]');
    if(box){
      const row=box.parentElement;
      // 预设点：当前色正好是预设之一就选中它，并撤掉自定义点。
      const preset=row.querySelector('i[data-preset-dot][data-color="'+c+'"]');
      const cusDot=row.querySelector("i[data-cp-custom]");
      row.querySelectorAll("i").forEach(function(x){x.classList.remove("sel")});
      if(preset){
        preset.classList.add("sel");
        if(cusDot)cusDot.remove();
      }else{
        // 只维护**一个**自定义点。以前这里是"没有就 insert" ——
        // 拖动时每帧颜色都不同，于是每帧插一个点，拖一下色板被撑爆。
        let el=cusDot;
        if(!el){
          el=document.createElement("i");
          el.dataset.cpCustom="1";
          row.insertBefore(el,box);
        }
        el.style.background=c; el.dataset.color=c; el.title=c;
        el.classList.add("sel");
      }
      const hid=document.getElementById(row.id+"-v");
      if(hid)hid.value=c;
    }
  }
}


// ---------------- 预设壁纸画廊 ----------------
// 这几个是页面级的：配置快照、预设列表、当前标签页、轮播定时器。
let _wallPresets = null, _wallTab = "preset", _rotateTimer = null;
let cfgCache = {};

// 自定义图片的 data URI。优先用上传时缓存的；没有就从当前 --wall
    // 里读回来 —— 之前这里读一个从未赋值的变量，点「我的图片」会把
    // --wall 设成字面量 "null"，背景直接没了。
function _customWallUri(){
  // ① 本次会话里上传过，直接用缓存
  if(window.__wallCustomUri)return window.__wallCustomUri;
  // ② 问服务器要原图地址 —— 这是唯一可靠的办法。
  //
  //    以前这里还有一条"如果当前 --wall 以 url( 开头，就当成自定义图"，
  //    那个判断在内置壁纸是渐变时成立；后来默认预设也换成了内嵌图片，
  //    它的值同样是 url(...)，于是"官方 KV"被误认成"我的图片" ——
  //    点回去等于又把 KV 设了一遍，看着就是"切不回自定义图"。
  //    判断依据从根本上就不该是"--wall 长什么样"。
  return "url('/admin/api/wallpaper/raw?token="
    + encodeURIComponent(TOKEN) + "')";
}
function _wallCurrent(){
  // 优先用运行时改过的值（还没保存的点选），否则用服务端配置
  return (window.__appearPatch && window.__appearPatch.wallpaper_preset)
    || (cfgCache && cfgCache.wallpaper_preset) || "kv";
}
function renderWallSection(){
  const sel=_wallCurrent();
  let h='<div class="set-row" style="display:block;padding-bottom:6px">';
  h+='<div class="wall-tabs">'
    +'<div class="wall-tab'+(_wallTab==="preset"?" on":"")
    +'" data-walltab="preset">预设背景</div>'
    +'<div class="wall-tab'+(_wallTab==="image"?" on":"")
    +'" data-walltab="image">图片背景</div></div>';
  if(_wallTab==="preset"){
    // 防御：接口没回来 / 返回结构不对时不能炸，画个占位就行
    const ps=(_wallPresets&&Array.isArray(_wallPresets.presets))
      ?_wallPresets.presets:[];
    if(!ps.length){
      h+='<div class="wall-empty">正在读预设…</div>';
    }else{
      h+='<div class="wall-grid">';
      for(const p of ps){
        h+='<div class="wall-thumb'+(p.id===sel?" on":"")+'"'
          +' data-preset="'+p.id+'" style="background-image:'+p.css+'">'
          +'<b>'+p.name+'</b></div>';
      }
      if(_wallPresets.custom){
          // 以前这里是写死的 background:#111 —— 缩略图一直是个黑块。
          // 现在指向后端的原图接口，真把用户那张图显示出来。
          h+='<div class="wall-thumb'+(sel==="custom"?" on":"")+'"'
            +' data-preset="custom" id="wallThumbCustom">'
            +'<img src="/admin/api/wallpaper/raw?token='
            +encodeURIComponent(TOKEN)+'" alt="">'
            +'<b>我的图片</b></div>';
        }
        h+='</div>';
    }
  }else{
    h+='<div class="wall-box"><div class="wall-prev" id="wallPrev"></div>'
      +'<div class="wall-act">'
      +'<input type="file" id="wallFile" accept="image/*" style="display:none"'
      +' onchange="pickWall(this)">'
      +'<button class="btn sm" type="button"'
      +' onclick="document.getElementById(\'wallFile\').click()">选择图片</button>'
      +'<button class="btn sm" type="button" onclick="resetWall()">恢复默认</button>'
      +'<div class="wall-tip" id="wallTip">jpg / png / webp，'
      +'太大会自动缩到 2560px 再上传</div></div></div>';
  }
  h+='</div>';
  return h;
}
async function loadWallPresets(){
  try{
    _wallPresets=await api("/admin/api/wallpaper/presets");
  }catch(e){ _wallPresets={presets:[],current:"kv",rotate:false,rotate_sec:15}; }
  if(_wallPresets&&_wallPresets.rotate)startRotate();
  return _wallPresets;
}
function startRotate(){
  stopRotate();
  const sec=Math.max(3,(_wallPresets&&_wallPresets.rotate_sec)||15);
  _rotateTimer=setInterval(function(){
    if(!_wallPresets||!_wallPresets.presets.length)return;
    const ids=_wallPresets.presets.map(function(p){return p.id});
    const i=ids.indexOf(_wallCurrent());
    const next=ids[(i+1)%ids.length];
    applyAppearance({wallpaper_preset:next});
    const grid=document.querySelector(".wall-grid");
    if(grid){
      grid.querySelectorAll(".wall-thumb").forEach(function(x){
        x.classList.toggle("on",x.dataset.preset===next)});
    }
  },sec*1000);
}
function stopRotate(){
  if(_rotateTimer){clearInterval(_rotateTimer);_rotateTimer=null;}
}
document.addEventListener("click",function(e){
  const tab=e.target.closest&&e.target.closest("[data-walltab]");
  if(tab){
    _wallTab=tab.dataset.walltab;
    const box=document.getElementById("wallSection");
    if(box){box.innerHTML=renderWallSection();if(_wallTab==="image")loadWallState();}
    return;
  }
  const th=e.target.closest&&e.target.closest("[data-preset]");
  if(th){
    const grid=th.parentElement;
    grid.querySelectorAll(".wall-thumb").forEach(function(x){
      x.classList.remove("on")});
    th.classList.add("on");
    applyAppearance({wallpaper_preset:th.dataset.preset});
    return;
  }
});

function applyAppearance(patch){
  const R=document.documentElement;
  for(const k in patch){
    const v=patch[k];
    if(k==="wallpaper_preset"){
      // 预设直接换 --wall；custom 时用当前图片的 data URI
      let v2=null;
      if(_wallPresets){
        const hit=_wallPresets.presets.filter(function(p){return p.id===v})[0];
        if(hit)v2=hit.css;
      }
      if(v==="custom")v2=_customWallUri();
      if(v2)R.style.setProperty("--wall",v2);
    }else if(k==="wallpaper_rotate"){
      if(v)startRotate(); else stopRotate();
    }else if(k==="wallpaper_rotate_sec"){
      if(_wallPresets)_wallPresets.rotate_sec=v;
      if(_wallPresets&&_wallPresets.rotate)startRotate();
    }else if(k==="wallpaper_enabled"){
      document.body.classList.toggle("no-wall",!v);
    }else if(k==="wallpaper_dim"){
      // 亮色蒙版用 --wall-dim，深色蒙版用 -1/-2/-4。
      // 只改 --wall-dim 的话深色用户拖滑杆没反应。
      const d=v/100;
      R.style.setProperty("--wall-dim",Math.min(.72,.30+d*.45).toFixed(3));
      R.style.setProperty("--wall-dim-1",(d*.78).toFixed(3));
      R.style.setProperty("--wall-dim-2",d.toFixed(3));
      R.style.setProperty("--wall-dim-4",Math.min(.92,d*1.12).toFixed(3));
    }else if(k==="wallpaper_blur"){
      R.style.setProperty("--wall-blur",v+"px");
    }else if(k==="ui_side_blur"){
      R.style.setProperty("--side-blur",v+"px");
    }else if(k==="ui_face_blur"){
      R.style.setProperty("--panel-blur",v+"px");
    }else if(k==="ui_side_on"){
      // 关掉侧栏玻璃：给它一个近乎不透明的底
      document.body.classList.toggle("no-side-glass",!v);
      if(!v)R.style.setProperty("--side-a",".985");
      else{
        const a=+(document.getElementById("ui_side_alpha")||{}).value||72;
        R.style.setProperty("--side-a",(a/100).toFixed(3));
      }
    }else if(k==="ui_face_on"){
      document.body.classList.toggle("no-face-glass",!v);
      if(!v)R.style.setProperty("--face-a",".985");
      else{
        const a=+(document.getElementById("ui_face_alpha")||{}).value||52;
        R.style.setProperty("--face-a",(a/100).toFixed(3));
      }
    }else if(k==="ui_side_alpha"){
      R.style.setProperty("--side-a",(v/100).toFixed(3));
    }else if(k==="ui_face_alpha"){
      R.style.setProperty("--face-a",(v/100).toFixed(3));
    }else if(k==="ui_side_color"){
      // 写进 light 基底。不要写 --side-rgb —— 那是"当前生效"的别名，
      // 写它会以 inline 盖掉 @media，深色下反而被浅色值顶掉。
      R.style.setProperty("--side-rgb-light",_hex2rgb(v).join(","));
      const el=document.getElementById("ui_side_alpha");
      R.style.setProperty("--side-a",((el?+el.value:72)/100).toFixed(3));
    }else if(k==="ui_face_color_dark"){
      R.style.setProperty("--face-rgb-dark",_hex2rgb(v).join(","));
    }else if(k==="ui_side_color_dark"){
      R.style.setProperty("--side-rgb-dark",_hex2rgb(v).join(","));
    }else if(k==="ui_face_color"){
      R.style.setProperty("--face-rgb-light",_hex2rgb(v).join(","));
      const el=document.getElementById("ui_face_alpha");
      R.style.setProperty("--face-a",((el?+el.value:52)/100).toFixed(3));
    }
    window.__appearPatch=Object.assign(window.__appearPatch||{},patch);
  }
}
document.addEventListener("click",function(e){
  const t=e.target;
  if(t&&t.classList&&t.classList.contains("sw")){
    const on=!t.classList.contains("on");
    t.classList.toggle("on",on);
    t.dataset.val=on?"true":"false";
    applyAppearance({[t.dataset.key]:on});
    return;
  }
  if(t&&t.dataset&&t.dataset.color){
    const box=t.parentElement;
    Array.prototype.forEach.call(box.querySelectorAll("i"),function(x){
      x.classList.remove("sel")});
    t.classList.add("sel");
    const id=_swKeyOf(box.id);
    const hid=document.getElementById(box.id+"-v");
    if(hid)hid.value=t.dataset.color;
    applyAppearance({[id]:t.dataset.color});
    return;
  }
  if(t&&t.dataset&&t.dataset.custom){
    // 别用三元 —— 之前只写了 sws-side / sws-face 两个分支，
    // 深色那两项落进 else，颜色被存成了浅色的 key。
    const id=_swKeyOf(t.dataset.custom);
    const cur=window.__appearPatch&&window.__appearPatch[id];
    // 回落链：运行时改过的 -> 服务端配置 -> 侧栏默认
    // 之前直接 || "#0a1022"，face 系（默认 #ffffff）打开就是深蓝黑
    openPicker(t, id,
      cur || (cfgCache && cfgCache[id]) || "#0a1022");
    return;
  }
});
document.addEventListener("input",function(e){
  const el=e.target;
  if(!el||el.type!=="range")return;
  const id=el.id, v=el.value;
  const box=document.getElementById(id+"-v");
  // 单位读控件自带的 data-unit，别靠 id 猜 ——
  // wallpaper_rotate_sec 以前会落进 "px"，一拖就从 15s 变 20px。
  const unit=(el.dataset&&el.dataset.unit!==undefined)?el.dataset.unit:"";
  if(box)box.textContent=v+unit;
  const num=(id==="wallpaper_dim"||id.indexOf("alpha")>=0)?+v:+v;
  applyAppearance({[id]:num});
});

async function loadAppear(){
  // 外观从「系统配置」里拆出来单独一页（跟系统配置同级）。
  // 控件还是 renderAppearance 那套（开关 / 滑杆 / 色板 / 画廊），
  // 只是渲染目标换成了 #appearForm。
  const cfg=await api("/admin/api/config");
  cfgCache=cfg||{};          // 画廊要读当前预设
  window.__appearPatch={};   // 每次重载都清空，免得残留上一轮的值
  const el=document.getElementById("appearForm");
  if(!el)return;
  el.innerHTML=renderAppearance(cfg||{});
  loadWallPresets().then(function(){
    const box=document.getElementById("wallSection");
    if(box)box.innerHTML=renderWallSection();
    if(_wallTab==="image")loadWallState();
  });
  setTimeout(loadWallState,0);
}
async function loadConfig(){
  const cfg=await api("/admin/api/config");
  cfgCache=cfg||{};          // 插件配置小窗要读当前值，存一份快照
  window.__appearPatch={};   // 每次重载都清空，免得残留上一轮的值
  // 这里原本还有一段 loadWallPresets().then(...) 给 wallSection 补画一次
  // 画廊 —— 外观拆到 page-appear 之后，cfgForm 里已经没有 #wallSection 了，
  // getElementById 永远返回 null（被 if(box) 挡住，不报错），
  // 但每次进系统配置页都白跑一次预设请求。那段已经挪到 loadAppear。
  // 插件配置不在这里 —— 每个插件有自己的配置小窗，
  // 从插件卡片的「配置」按钮打开（见 configFor）。
  const el=document.getElementById("cfgForm");
  const groups=[
    {title:"AI 后端",fields:[["ai_backend","后端选择","backend"],["ollama_host","Ollama 地址","text"],["ai_model","Ollama 模型名","text"],["deepseek_api_host","云端 API 地址","text"],["deepseek_model","云端模型名","text"],["deepseek_api_key","云端 API Key","text"],["ai_min_gap","最小请求间隔（秒）","number"],["ai_max_gap","最大请求间隔（秒）","number"],["ai_context_size","短期上下文轮数","number"]]},
    {title:"视觉识图",fields:[["vision_enabled","开启识图","bool"],["vision_api_host","接口地址","text"],["vision_api_key","API Key","text"],["vision_model","模型名","text"],["vision_timeout","超时（秒）","number"],["vision_max_image_kb","图片大小上限（KB）","number"]]},
    {title:"回复行为",fields:[["talk_value","主动发言概率 0-1","number"],["reply_to_name","提及名字也回复","bool"],["split_reply","长回复拆成多条","bool"],["split_max_length","拆分单条最大字数","number"],["split_max_parts","最多拆几条","number"],["split_delay_seconds","多条间隔（秒）","number"],["enable_private_chat","私聊对话","bool"]]},
    {title:"长期记忆",fields:[["memory_check_every","分析频率（每N轮）","number"],["memory_min_importance","最低重要度 1-5","number"],["memory_inject_count","注入群记忆条数","number"],["memory_user_enabled","记住每个人的事","bool"],["memory_user_inject_count","注入个人记忆条数","number"],["chatlog_enabled","记录聊天记录","bool"],["chatlog_keep_days","聊天记录保留天数","number"]]},
    {title:"入群与验证",fields:[["verify_enabled","开启入群验证","bool"],["verify_timeout","验证超时（秒）","number"],["min_qq_level","最低 QQ 等级","number"]]},
    {title:"欢迎与拒绝",fields:[["welcome_msg","入群欢迎语","text"],["farewell_msg","退群提示语","text"],["reject_msg","拒绝入群理由","text"]]},
    {title:"备份",fields:[["backup_enabled","自动备份开关","bool"],["backup_hour","备份时间（0-23 点）","number"],["backup_keep_days","保留天数","number"]]},
    {title:"安全",fields:[["admin_token","管理密码","text"]]}
  ];
  let h="";
  for(const g of groups){
    h+=`<div class="cfg-group"><div class="cfg-group-title">${esc(g.title)}</div><div class="form-grid">`;
    for(const [k,label,type] of g.fields){
      const v=cfg[k];
      const full=(type==="text")?" full":"";
      h+=`<div class="form-row${full}"><label>${label}</label>`;
      if(type==="bool"){
        h+=`<select id="cfg-${k}"><option value="true"${v?" selected":""}>开启</option><option value="false"${!v?" selected":""}>关闭</option></select>`;
      }else if(type==="number"){
        h+=`<input id="cfg-${k}" type="number" step="any" value="${v}">`;
      }else if(type==="backend"){
        h+=`<select id="cfg-${k}"><option value="ollama"${v==="ollama"?" selected":""}>本地 Ollama</option><option value="deepseek"${v==="deepseek"?" selected":""}>云端 API（智谱 / DeepSeek / 兼容 OpenAI）</option></select>`;
      }else{
        const isKey=k.endsWith("_api_key")||k==="admin_token";
        const ph=(isKey&&cfg[k+"_set"])?"留空 = 不修改（当前已配置）":"";
        h+=`<input id="cfg-${k}" value="${esc(v)}" placeholder="${ph}">`;
      }
      h+=`</div>`;
    }
    if(g.title.startsWith("AI 后端")){
      h+=`<div class="form-row full" style="margin-top:4px"><label>说明</label><div style="font-size:12px;color:var(--text-3);line-height:1.9">选「本地 Ollama」时用上面的 Ollama 地址 + 模型名，key 随便填非空即可（默认 ollama）。<br>选「云端 API」时用下面的地址 + 模型名 + API Key，支持任何兼容 OpenAI 协议的服务。<br>智谱：地址 <code>https://open.bigmodel.cn/api/paas/v4</code>，模型 <code>glm-4.7-flash</code>（免费）。<br>DeepSeek 官方：地址 <code>https://api.deepseek.com</code>，模型 <code>deepseek-chat</code>。<br>改完点保存立即生效，不用重启。</div></div>`;
    }
    if(g.title.startsWith("视觉识图")){
      h+=`<div class="form-row full" style="margin-top:4px"><label>提示</label><div style="font-size:12px;color:var(--text-3);line-height:1.8">智谱视觉模型用 <code>https://open.bigmodel.cn/api/paas/v4</code>，模型名 <code>glm-4v-flash</code>（免费）。<br>API Key 和上面的对话模型共用同一个。<br>出于安全考虑，已保存的 Key 不会再回显到页面上；留空即表示保持原值。</div></div>`;
    }
    h+=`</div></div>`;
  }
  el.innerHTML=h;
}
async function saveConfig(scope){
  // 遍历表单里所有 cfg-* 输入框，而不是写死一个 keys 数组 ——
  // 否则插件声明的配置项永远存不进去。
  // 不再维护"哪些键是浮点"的白名单 —— parseFloat 对整数和小数都对
  // （parseFloat("3")===3，parseFloat("0.7")===0.7），
  // 服务端 cfg_int() 里也是 int(float(v))，整数键不会因此出问题。
  const cfg={};
  let cnt=0;
  // 外观页和系统配置页各存各的：从外观页点保存时只收 #appearForm 里的
  // 控件，免得把另一页还没保存的编辑也一起提交了。
  const root=document.getElementById(scope==="appear"?"appearForm":"cfgForm");
  if(!root){toast("表单还没加载完","err");return;}
  root.querySelectorAll("[id^='cfg-']").forEach(function(el){
    const k=el.id.slice(4);
    if(!k)return;
    let v=el.value;
    if(el.tagName==="SELECT"&&(el.value==="true"||el.value==="false")){
      v=(el.value==="true");
    }else if(el.type==="number"){
      v=parseFloat(v);if(isNaN(v))v=0;
    }
    cfg[k]=v;
    cnt++;
  });
  // 外观那个面板用的是自定义控件（开关 / 滑杆 / 色板），
  // 它们的 id 没有 cfg- 前缀，所以额外收一遍 data-key。
  root.querySelectorAll("[data-key]").forEach(function(el){
    const k=el.dataset.key;
    if(!k)return;
    let v=el.dataset.val;
    if(v===undefined){
      if(el.type==="range")v=parseFloat(el.value);
      else if(el.tagName==="SELECT")v=(el.value==="true");
      else v=el.value;
    }else{
      v=(v==="true")?true:(v==="false")?false:(isNaN(+v)?v:+v);
    }
    cfg[k]=v;
    cnt++;
  });
  // 运行时即时改动过的（比如点色板换的颜色）也并进来。
  // 只在保存外观时收 —— 那是外观控件独有的状态。
  if(scope==="appear"&&window.__appearPatch){
    for(const k in window.__appearPatch){
      if(!(k in cfg))cnt++;
      cfg[k]=window.__appearPatch[k];
    }
  }
  const keys={length:cnt};
  if(!cnt){toast("没有可保存的配置项","err");return;}
  const newToken=document.getElementById("cfg-admin_token");
  const tokenChanged=!!(newToken&&newToken.value.trim()&&newToken.value.trim()!==TOKEN);
  let r;
  try{ r=await post("/admin/api/config",cfg); }
  catch(e){ toast("保存异常："+(e&&e.message||e),"err"); return; }
  if(!r){toast("保存失败：服务器无响应","err");return;}
  if(r.error){toast("保存失败："+r.error,"err");return;}
  const n=(r.changed===undefined?keys.length:r.changed);
  if(n===0){toast("已保存（数值与原来相同，没有变化）");}
  else{toast("已保存 "+n+" 项，立即生效"+(tokenChanged?"，正在用新密码刷新…":""));}
  if(tokenChanged){
    setTimeout(()=>{location.href="/admin?token="+encodeURIComponent(newToken.value.trim());},900);
  }
}
function clearLogs(){document.getElementById("bigLogs").innerHTML="";}

// ==================== 版本 / 远程更新 ====================
let _verInfo=null;
function _sz(n){
  n=Number(n)||0;
  if(n>=1024*1024)return (n/1024/1024).toFixed(1)+" MB";
  if(n>=1024)return (n/1024).toFixed(0)+" KB";
  return n+" B";
}
// 更新说明是 markdown，这里只做最轻的渲染：标题加粗、**粗**、`代码`。
// 先 esc 再替换 —— 顺序反了就是 XSS。
// 更新说明是 markdown，这里做轻量渲染。
// 规则顺序有讲究：代码块 -> 行内代码 -> 链接 -> 粗体 -> 斜体 -> 标题 ->
// 分隔线 -> 列表 -> 引用。代码和链接先"抽走"成占位符，免得 URL 里的
// _ 或 * 被后面的规则改坏。
function _mdLite(s){
  let h=esc(s||"");
  const keep=[];
  const stash=function(html){keep.push(html);return "\u0000"+(keep.length-1)+"\u0001";};
  // ``` 围栏代码块，整段原样（有的更新说明里直接贴了报错或代码）
  h=h.replace(/```[^\n]*\n([\s\S]*?)```/g,function(_,c){
    return stash('<pre class="md-pre">'+c.replace(/\n$/,"")+'</pre>');});
  // 行内代码
  h=h.replace(/`([^`\n]+)`/g,function(_,c){return stash("<code>"+c+"</code>");});
  // 链接 —— 原来完全没处理，导致每版说明里那行 [README](...) 都是原文
  h=h.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,function(_,t,u){
    return stash('<a href="'+u+'" target="_blank" rel="noopener">'+t+'</a>');});
  // 粗体：非贪婪 + 允许跨行。原来用 [^*\n]+ 匹配不到换行，
  // 而 Release 说明里 "**完整说明见 [README](…) ·\n[更新日志](…)**" 正好跨行
  h=h.replace(/\*\*([\s\S]+?)\*\*/g,"<b>$1</b>");
  h=h.replace(/__([\s\S]+?)__/g,"<b>$1</b>");
  // 斜体（单星号）
  h=h.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g,"$1<i>$2</i>");
  // 标题分三级 —— 原来 ## 和 ### 都渲染成 <b>，层级完全看不出来
  h=h.replace(/^###[ \t]*(.+)$/gm,'<b class="md-h3">$1</b>');
  h=h.replace(/^##[ \t]*(.+)$/gm,'<b class="md-h2">$1</b>');
  h=h.replace(/^#[ \t]*(.+)$/gm,'<b class="md-h1">$1</b>');
  // 分隔线（说明末尾常有 ---）
  h=h.replace(/^[ \t]*([-*_])[ \t]*\1[ \t]*\1[\-*_ \t]*$/gm,"<hr>");
  // 无序列表 —— 原来是原文的 "- xxx"
  h=h.replace(/^[ \t]*[-*][ \t]+(.+)$/gm,'<span class="md-li">$1</span>');
  // 引用
  h=h.replace(/^[ \t]*&gt;[ \t]?(.*)$/gm,'<span class="md-quote">$1</span>');
  // 表格：连续的 | a | b | 行，第二行是 |---|---|
  // 放在链接/粗体之后 —— 单元格里的 markdown 那时已经处理过了
  h=h.replace(/(^\|[^\n]*\|[ \t]*$\n?)+/gm,function(block){
    const rows=block.replace(/\n$/,"").split("\n").map(function(r){
      return r.replace(/^\|/,"").replace(/\|$/,"").split("|")
              .map(function(c){return c.trim();});
    });
    // GitHub 的语义：**第二行必须是分隔行**（|---|---|）才算表格。
    // 只看"连续两行以 | 开头结尾"的话，说明里用 | 画的示意图会被误渲染成表格。
    if(rows.length<2)return block;
    if(!rows[1].every(function(c){return /^:?-{2,}:?$/.test(c);}))return block;
    let t='<table class="md-tb">';
    for(let i=0;i<rows.length;i++){
      const cs=rows[i];
      // 第二行是分隔行 |---|---|，跳过
      if(i===1&&cs.every(function(c){return /^:?-{2,}:?$/.test(c);}))continue;
      const tag=(i===0)?"th":"td";
      t+="<tr>"+cs.map(function(c){
        return "<"+tag+">"+c+"</"+tag+">";
      }).join("")+"</tr>";
    }
    return stash(t+"</table>");
  });
  // 还原占位。要循环 —— 表格是"抽走"的，单元格里可能还嵌着链接的占位符；
  // 一遍 replace 不会去扫替换进来的内容，嵌在里面的就换不回来了。
  let prev=null;
  for(let n=0;n<8&&h!==prev;n++){
    prev=h;
    h=h.replace(/\u0000(\d+)\u0001/g,function(_,i){return keep[+i];});
  }
  return h;
}
async function checkVersion(force, showDlg){
  const el=document.getElementById("brandVer");
  if(!el)return;
  let d=null;
  try{ d=await api("/admin/api/version"+(force?"?force=1":"")); }catch(e){ d=null; }
  if(!d||!d.current){ el.innerHTML=""; return; }
  _verInfo=d;
  // 版本号任何时候都能点开详情 —— 不是只有"有新版本"时才给看。
  // 已经是最新的时候，"刚才这版改了什么"照样是用户想知道的。
  let h='<span class="ver-cur" title="查看版本详情 / 重新检查" '
       +'onclick="checkVersion(1,1)">v'+esc(d.current)+'</span>';
  if(d.has_update){
    h+='<button class="ver-new" onclick="showUpdate()" title="最新 v'+esc(d.latest)+'">有新版本</button>';
  }
  el.innerHTML=h;
  // 深链 ?upd=1 直接把更新窗打开（跟 ?page= / &cfg= 一个套路）
  try{
    const q=new URLSearchParams(location.search);
    // 不再要求 has_update —— 已经是最新的时候，用户照样想看"这版改了什么"
    if(q.get("upd")==="1"&&!window.__updShown){
      window.__updShown=1;
      showUpdate();
    }
  }catch(e){}
  // 点版本号进来的，直接弹详情（这本身就是"查过了"的反馈）
  if(showDlg)showUpdate();
}
function showUpdate(){
  const d=_verInfo;
  const mask=document.getElementById("updMask");
  const body=document.getElementById("updBd");
  if(!d||!mask||!body)return;
  const up=!!d.has_update;
  const ttl=document.getElementById("updTitle");
  if(ttl)ttl.textContent=up?"发现新版本":"版本详情";
  // 有新版：当前 -> 最新；已是最新：只显示当前版本
  let h=up
    ? '<div class="upd-head">'
      +'<span class="upd-cur">v'+esc(d.current)+'</span>'
      +'<span class="upd-arrow">→</span>'
      +'<span class="upd-new">v'+esc(d.latest)+'</span></div>'
    : '<div class="upd-head">'
      +'<span class="upd-new">v'+esc(d.current)+'</span>'
      +'<span class="upd-same">已是最新版本</span></div>';
  if(d.published){
    const t=String(d.published).replace("T"," ").replace("Z","").slice(0,16);
    h+='<div class="upd-time">发布于 '+esc(t)+'（UTC）</div>';
  }
  if(!up&&d.notes)h+='<div class="upd-label">这一版改了什么</div>';
  h+='<div class="upd-notes">'
    +(d.notes?_mdLite(d.notes)
             :(up?"（这次没有写更新说明）":"（这一版没有写更新说明）"))
    +'</div>';
  if(d.assets&&d.assets.length){
    // 选择下载线路：每条线路显示实测延迟，用户照着挑最快的。
    // 光列出来没用 —— 用户不知道哪条通、哪条快。
    const srcs=(d.sources&&d.sources.length)
      ? d.sources : [{k:"direct",label:"直连（GitHub 官方）"}];
    h+='<div class="upd-srcrow"><div class="upd-srclb">选择下载线路</div>'
      +'<div class="upd-lines" id="updLines"></div>'
      +'<div class="upd-srchint">点数字可重新测速 · 加速节点会下载后核对 '
      +'SHA-256（校验值取自 api.github.com，不经过加速节点），不通过自动删除'
      +'</div></div>';
    h+='<div class="upd-files"><div class="upd-ft">下载文件</div>';
    for(const a of d.assets){
      // 就地下载：后端把包下到 data/updates/，全程只跟 127.0.0.1 打交道
      h+='<div class="upd-file"><span class="nm">'+esc(a.name)+'</span>'
        +'<span class="sz">'+_sz(a.size)+'</span>'
        +'<button class="upd-dl" data-name="'+esc(a.name)+'" '
        +'onclick="dlUpdate(this.getAttribute(\'data-name\'),this)">下载</button></div>';
    }
    h+='</div>';
    h+='<div class="upd-prog" id="updProg" hidden>'
      +'<div class="upd-srclb">下载进度</div>'
      +'<div class="upd-bar"><i id="updBar"></i><b id="updPct">0%</b></div>'
      +'<div class="upd-pgtx"><span id="updSpd">速度: 0 B/s</span>'
      +'<span id="updTot">0 / 0</span></div>'
      +'<div class="upd-cancelrow"><button class="btn sm" id="updCancel" '
      +'onclick="cancelDl()">取消下载</button></div>'
      +'<div class="upd-pgerr" id="updErr" hidden></div></div>';
  }
  if(!d.ok){
    h+='<div class="upd-err">检查更新时没能连上 GitHub：'
      +esc(d.error||"未知原因")+'</div>';
    // 查不到也给个重试，别让人只能刷新页面
    h+='<div style="margin-top:12px"><button class="btn sm" '
      +'onclick="checkVersion(1,1)">重试</button></div>';
  }
  // 这次 Release 没有附件时，别显示「下载桌面版」
  const go=document.getElementById("updGo");
  if(go)go.hidden=!((d.assets||[]).length);
  body.innerHTML=h;
  mask.hidden=false;
  document.addEventListener("keydown",_updKey);
  // 画线路卡片并开始测速
  renderLines((d.sources&&d.sources.length)
    ? d.sources : [{k:"direct",label:"直连（GitHub 官方）"}]);
  // 接着显示下载状态：不光是"正在下"，已完成 / 已失败也要显示 ——
  // 不然关掉弹窗再打开，就看不到"文件存哪了"
  api("/admin/api/update/progress").then(function(s){
    if(s&&(s.busy||s.done||s.saved||s.error)){
      const p=document.getElementById("updProg");
      if(p)p.hidden=false;
      if(s.busy){
        if(_dlPoll)clearInterval(_dlPoll);
        _dlPoll=setInterval(pollDl,300);
      }
      pollDl();   // 立刻画一次
    }
  }).catch(function(){});
}
function _updKey(e){ if(e.key==="Escape")closeUpd(); }
function closeUpd(){
  const m=document.getElementById("updMask");
  if(m)m.hidden=true;
  document.removeEventListener("keydown",_updKey);
  // 只停轮询，后端该下还继续下；再打开弹窗会接上
  if(_dlPoll){clearInterval(_dlPoll);_dlPoll=null;}
}
// ==== 下载线路 ====
let _pingGen=0, _userPicked=false, _pingRes={}, _pingSettled=0;
function renderLines(srcs){
  const box=document.getElementById("updLines");
  if(!box)return;
  _userPicked=false;
  _pingRes={};
  let h="";
  for(let i=0;i<srcs.length;i++){
    const s=srcs[i];
    h+='<button type="button" class="upd-line'+(i===0?" on":"")+'" '
      +'data-src="'+esc(s.k)+'" data-i="'+i+'" onclick="pickLine(this)">'
      +'<i class="rd"></i><span class="nm">'+esc(s.label)+'</span>'
      +'<span class="ms wait" data-ms="'+i+'" title="点击重新测速" '
      +'onclick="event.stopPropagation();pingAll()">测速中…</span></button>';
  }
  box.innerHTML=h;
  pingAll();
}
function pickLine(el){
  const box=document.getElementById("updLines");
  if(!box)return;
  for(const b of box.querySelectorAll(".upd-line"))b.classList.remove("on");
  el.classList.add("on");
  _userPicked=true;
}
function currentSrc(){
  const el=document.querySelector("#updLines .upd-line.on");
  return el?el.getAttribute("data-src"):"direct";
}
function _fmtMs(ms){
  if(ms>=1000)return (ms/1000).toFixed(1)+"s";
  return Math.round(ms)+"ms";
}
function pingAll(){
  const box=document.getElementById("updLines");
  if(!box)return;
  const els=box.querySelectorAll(".upd-line");
  const gen=++_pingGen;
  _pingRes={};
  _pingSettled=0;
  for(const e of els){
    const m=e.querySelector(".ms");
    if(m){m.className="ms wait";m.textContent="测速中…";}
  }
  // 所有线路都"有了结果"（成功或不通过）就应该选最快 ——
  // 原来判断的是"成功的数量等于总数"，只要有一条不通就永远不触发
  const settle=function(){
    if(gen!==_pingGen||_userPicked)return;
    if(_pingSettled<els.length)return;
    let best=null,bv=1e9;
    for(const k in _pingRes){if(_pingRes[k]<bv){bv=_pingRes[k];best=k;}}
    if(!best)return;
    for(const e2 of els){
      e2.classList.toggle("on",e2.getAttribute("data-src")===best);
    }
  };
  for(const e of els){
    const s=e.getAttribute("data-src"), i=e.getAttribute("data-i");
    api("/admin/api/update/ping?src="+encodeURIComponent(s)).then(function(r){
      if(gen!==_pingGen)return;
      const m=box.querySelector('[data-ms="'+i+'"]');
      if(!m)return;
      if(!r||!r.ok){
        m.className="ms bad";m.textContent="不通";
        _pingSettled++;settle();return;
      }
      _pingRes[s]=r.ms;
      m.textContent=_fmtMs(r.ms);
      m.className="ms"+(r.ms<600?"":(r.ms<1500?" mid":" bad"));
      _pingSettled++;settle();
    }).catch(function(){
      const m=box.querySelector('[data-ms="'+i+'"]');
      if(m&&gen===_pingGen){m.className="ms bad";m.textContent="不通";}
      if(gen===_pingGen){_pingSettled++;settle();}
    });
  }
}
function cancelDl(){
  api("/admin/api/update/cancel",{method:"POST"}).then(function(r){
    toast(r&&r.ok?"已取消下载":((r&&r.error)||"取消失败"),r&&r.ok?"":"");
  });
}
// 就地下载：后端把包下到 data/updates/，前端轮询进度画进度条。
// 不做页面跳转，也不依赖浏览器的"另存为"。
let _dlPoll=null;
function dlUpdate(name, btn){
  if(!name)return;
  const src=currentSrc();
  const prog=document.getElementById("updProg");
  const err=document.getElementById("updErr");
  const bar=document.getElementById("updBar");
  if(prog)prog.hidden=false;
  if(err){err.hidden=true;err.textContent="";err.className="upd-pgerr";}
  if(bar)bar.style.width="0%";
  const t=document.getElementById("updPct");
  if(t)t.textContent="正在连接…";
  const sp=document.getElementById("updSpd");
  if(sp)sp.textContent="";
  const btns=document.querySelectorAll(".upd-dl");
  for(const b of btns)b.disabled=true;
  api("/admin/api/update/fetch?name="+encodeURIComponent(name)
      +"&src="+encodeURIComponent(src),{method:"POST"}).then(function(r){
    if(!r||!r.ok){
      if(err){err.hidden=false;
        err.textContent=(r&&r.error)||"启动下载失败";}
      if(t)t.textContent="失败";
      for(const b of btns)b.disabled=false;
      return;
    }
    if(_dlPoll)clearInterval(_dlPoll);
    _dlPoll=setInterval(pollDl,300);
    pollDl();
    // 进度区在文件列表下面，弹窗内容一长就落到折叠线以下 ——
    // 用户点了下载却看不到进度条，得自己往下滚。这里主动滚过去。
    if(prog&&prog.scrollIntoView){
      try{prog.scrollIntoView({block:"nearest",behavior:"smooth"});}catch(e){}
    }
  }).catch(function(e){
    if(err){err.hidden=false;err.textContent=String(e);}
    if(t)t.textContent="失败";
    for(const b of btns)b.disabled=false;
  });
}
function pollDl(){
  api("/admin/api/update/progress").then(function(s){
    if(!s||s.error==="invalid")return;
    const bar=document.getElementById("updBar");
    const t=document.getElementById("updPct");
    const sp=document.getElementById("updSpd");
    const err=document.getElementById("updErr");
    if(bar)bar.style.width=Math.min(100,s.pct||0)+"%";
    if(t){
      if(s.error)t.textContent="失败";
      else if(s.done)t.textContent="100%";
      else t.textContent=Math.round(s.pct||0)+"%";
    }
    if(sp)sp.textContent="速度: "+(s.speed?_sz(s.speed)+"/s":"0 B/s");
    const tot=document.getElementById("updTot");
    if(tot)tot.textContent=_sz(s.got)+" / "+_sz(s.total);
    const cb=document.getElementById("updCancel");
    if(cb)cb.hidden=!(s.busy&&!s.done&&!s.error);
    if(s.error){
      if(err){err.hidden=false;err.className="upd-pgerr";
        err.textContent=s.error;}
    }
    if(s.done&&err&&s.saved){
      err.hidden=false;
      err.className="upd-pgok";
      err.innerHTML='已保存到 <code>'+esc(s.saved)+'</code>'
        +(s.verify?' <span class="upd-vf">'+esc(s.verify)+'</span>':"")
        +' <button class="btn sm" onclick="revealDl()">打开文件夹</button>'
        +(s.sha?('<div class="upd-sha">SHA-256 <code>'+esc(s.sha)+'</code>'
                 +(s.sha_src?'<span class="upd-shasrc">校验值来源：'
                             +esc(s.sha_src)+'</span>':"")+'</div>'):"");
    }
    if(s.done||s.error){
      if(_dlPoll){clearInterval(_dlPoll);_dlPoll=null;}
      const btns=document.querySelectorAll(".upd-dl");
      for(const b of btns)b.disabled=false;
    }
  }).catch(function(){});
}
function revealDl(){
  api("/admin/api/update/reveal",{method:"POST"}).then(function(r){
    toast(r&&r.ok?"已打开文件夹":((r&&r.error)||"打不开文件夹"),r&&r.ok?"ok":"");
  });
}
// 底部主按钮：优先下桌面版
function dlDesktop(){
  const as=(_verInfo&&_verInfo.assets)||[];
  const a=as.filter(function(x){return /desktop/i.test(x.name);})[0]||as[0];
  if(a)dlUpdate(a.name,null);
}
function openUpdPage(){
  const u=(_verInfo&&_verInfo.url)||"";
  if(u)window.open(u,"_blank","noopener");
}
function tick(){const d=new Date(),p=n=>String(n).padStart(2,"0");document.getElementById("clock").textContent=`${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;}
setInterval(pollLogs,1000);setInterval(pollStats,2500);setInterval(tick,1000);
// 启动查一次版本（后台缓存 30 分钟，不会反复打 GitHub）
checkVersion(0);
setInterval(()=>{const cur=document.querySelector(".page.on");if(cur&&cur.id==="page-join"){loadVF();loadRequests();}},3000);

// ==================== 插件管理 ====================
async function loadPlugins(){
  const box=document.getElementById("pluginList");
  if(!box)return;
  box.innerHTML='<div class="hint" style="padding:14px">读取中…</div>';
  const d=await api("/admin/api/plugins");
  if(!d||d.error){box.innerHTML='<div class="hint" style="padding:14px">读取失败：'+esc((d&&d.error)||"无响应")+'</div>';return;}
  const dirEl=document.getElementById("pluginDir");
  if(dirEl)dirEl.textContent=d.dir;
  const st=document.getElementById("pluginStat");
  if(st)st.innerHTML='共 <b>'+d.count+'</b> 个插件，<b>'+d.loaded+'</b> 个正常加载'+((d.disabled&&d.disabled.length)?('，<b>'+d.disabled.length+'</b> 个已停用'):'');
  renderPlugins(d.items||[]);
  loadPluginPages();
}

function renderPlugins(items){
  const box=document.getElementById("pluginList");
  if(!items.length){
    box.innerHTML='<div class="plug-empty">'
      +'<div style="font-size:14px;color:var(--text-2);margin-bottom:8px">还没有装任何插件</div>'
      +'<div style="font-size:12px;color:var(--text-3);line-height:2">'
      +'在 <code class="inline-code">data/plugins/</code> 下新建一个文件夹，放两个文件：<br>'
      +'<code class="inline-code">metadata.json</code> 写名称版本，'
      +'<code class="inline-code">main.py</code> 里定义 <code class="inline-code">def setup(ctx)</code><br>'
      +'放好后点右上角「重新加载」。'
      +'</div></div>';
    return;
  }
  let h="";
  for(const p of items){
    const badge=p.error
      ? '<span class="plug-badge err">加载失败</span>'
      : (p.enabled?'<span class="plug-badge ok">运行中</span>':'<span class="plug-badge off">已停用</span>');
    const tags=(p.tags||[]).map(function(t){return '<span class="plug-tag">'+esc(t)+'</span>';}).join("");
    // 管理插件的东西都放在「已安装」页：停用/启用、配置、卸载。
    // 市场只负责"装" —— 插件一多，在市场里翻卸载键根本找不着。
    // 不要在这里 esc —— jsq 已经做完整转义，先 esc 会把 ' 变成 &#39;，
    // jsq 再把它转成 \x26#39;，JS 拿到的就不是原字符串了。
    const _pkey=p.folder||p.name;
    let acts='<button class="btn sm" onclick="togglePlugin(\''+jsq(_pkey)+'\','+(p.enabled?0:1)+')">'
             +(p.enabled?"停用":"启用")+'</button>';
    if(p.config&&p.config.length)
      acts+=' <button class="btn sm" onclick="configFor(\''+jsq(p.name)+'\')">配置</button>';
    // 插件自带的页面入口（页面不再占侧边栏了）
    if(p.pages&&p.pages.length){
      const _pid=jsq(p.pages[0]);
      const _pt=jsq((p.page_titles&&p.page_titles[p.pages[0]])||p.display_name);
      acts+=' <button class="btn sm" onclick="setPage(\''+_pid+'\',\''+_pt+'\')">打开页面</button>';
    }
    acts+=' <button class="btn danger sm" onclick="uninstallPlugin(\''+jsq(_pkey)+'\')">卸载</button>';
    h+='<div class="plug-card'+(p.error?" bad":"")+'">'
      +'<div class="plug-head"><div class="plug-title">'
      +'<b>'+esc(p.display_name)+'</b>'
      +'<span class="plug-ver">v'+esc(p.version)+'</span>'
      +badge+tags+'</div>'
      +'<div class="plug-actions">'+acts+'</div></div>'
      +'<div class="plug-desc">'+esc(p.description||"（没有描述）")+'</div>'
      +(p.author?('<div class="plug-meta">作者：'+esc(p.author)+'</div>'):"")
      +'<div class="plug-meta">目录：<code class="inline-code">'+esc(p.folder||p.name)+'</code>';
    if(p.commands&&p.commands.length){
      h+=' · 命令：'+p.commands.map(function(c){return '<code class="inline-code">'+esc(c)+'</code>';}).join(" ");
    }
    if(p.hooks){h+=' · 消息钩子 '+p.hooks+' 个';}
    h+='</div>';
    if(p.error){h+='<div class="plug-err">'+esc(p.error)+'</div>';}
    h+='</div>';
  }
  box.innerHTML=h;
}

async function togglePlugin(key,enabled){
  const r=await post("/admin/api/plugins/toggle",{key:key,enabled:!!enabled});
  if(!r||r.error){toast("操作失败："+((r&&r.error)||"无响应"),"err");return;}
  toast((enabled?"已启用 ":"已停用 ")+key);
  loadPlugins(); loadPluginPages();
}

async function reloadPlugins(){
  toast("正在重新加载插件…");
  const r=await post("/admin/api/plugins/reload",{});
  if(!r||r.error){toast("重载失败："+((r&&r.error)||"无响应"),"err");return;}
  let m="已重载 "+r.loaded+"/"+r.total+" 个插件";
  if(r.failed&&r.failed.length)m+="，"+r.failed.length+" 个有问题";
  toast(m,(r.failed&&r.failed.length)?"err":"ok");
  loadPlugins(); loadPluginPages();
}

// 插件声明的页面：动态挂到侧边栏和主区域
async function loadPluginPages(){
  let list=[];
  try{list=await api("/admin/api/plugins/pages")||[];}catch(e){list=[];}
  const nav=document.querySelector("nav.nav");
  // 必须挂在 .content 里面！main.main 是 flex 列、.content 有 flex:1，
  // 挂到 main 上会被 .content 顶下去（表现为页面顶部一大块空白）。
  const main=document.querySelector("main.main .content")||document.querySelector("main.main");
  if(!nav||!main)return;
  const want={};
  for(const p of list)want["page-"+p.id]=p;

  // 先摘掉不该再有的：插件被卸载/停用后，它的页面和导航项必须跟着消失，
  // 否则侧边栏一直留着，看着像没删掉（用户报过这个）。
  // 只认自己加的（data-plugin-page=1），内置页面绝不碰。
  document.querySelectorAll(".page").forEach(function(el){
    if(el.dataset&&el.dataset.pluginPage==="1"&&!want[el.id])el.remove();
  });
  document.querySelectorAll(".nav-item").forEach(function(b){
    if(b.dataset&&b.dataset.pluginPage==="1"&&!want["page-"+b.dataset.page])b.remove();
  });
  // 如果被摘掉的正好是当前打开的页，退回概览
  const cur=document.querySelector(".page.on");
  if(cur&&cur.dataset&&cur.dataset.pluginPage==="1"&&!want[cur.id])setPage("overview","概览");

  // 再补新的
  for(const p of list){
    const pid="page-"+p.id;
    if(document.getElementById(pid))continue;
    // 导航项还是要建 —— setPage() 靠 .nav-item[data-page] 来切页。
    // 但加 plug-nav-hidden 让它不出现在侧边栏：
    // 插件的页面从「插件 → 已安装」的卡片上进。
    const b=document.createElement("button");
    b.className="nav-item plug-nav-hidden";
    b.dataset.page=p.id;
    b.dataset.title=p.title;
    b.dataset.pluginPage="1";
    b.innerHTML='<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h6v3a2 2 0 0 0 2 2h3v6h-3a2 2 0 0 0-2 2v3H9v-3a2 2 0 0 0-2-2H4V8h3a2 2 0 0 0 2-2V3z"/></svg><span>'+esc(p.title||p.id)+'</span>';
    b.onclick=function(){setPage(p.id,p.title);};
    nav.appendChild(b);
    const d=document.createElement("div");
    d.className="page";
    d.id=pid;
    d.dataset.pluginPage="1";
    // 顶部插一条返回（侧边栏里不显示它，得给个出口）
    d.innerHTML='<div class="plug-page-bar">'
      +'<button class="btn ghost sm" onclick="setPage(\'plugins\',\'插件\')">← 返回插件</button>'
      +'<span class="ppb-title">'+esc(p.title||p.id)+'</span></div>'
      +(p.html||"");
    main.appendChild(d);
    // innerHTML 插进去的 <script> 不会自动执行，得重新造一个塞进去
    d.querySelectorAll("script").forEach(function(old){
      const s=document.createElement("script");
      if(old.src){s.src=old.src;}else{s.textContent=old.textContent;}
      old.parentNode.replaceChild(s,old);
    });
  }
}

// ---------------- 插件市场 ----------------
let _plugTab="installed";
function setPlugTab(t){
  _plugTab=t;
  document.querySelectorAll("#plugSeg button").forEach(function(b){
    b.classList.toggle("on",b.dataset.tab===t);
  });
  const a=document.getElementById("plugPaneInstalled");
  const b=document.getElementById("plugPaneMarket");
  if(a)a.style.display=(t==="installed")?"":"none";
  if(b)b.style.display=(t==="market")?"":"none";
  if(t==="market")loadMarket(0);
}

async function loadMarket(force){
  const box=document.getElementById("marketList");
  const st=document.getElementById("marketStat");
  if(!box)return;
  if(box.dataset.loaded!=="1"||force){
    box.innerHTML='<div class="hint" style="padding:14px">正在拉取市场列表…</div>';
    if(st)st.textContent="拉取中…";
  }
  const d=await api("/admin/api/plugins/market"+(force?"?refresh=1":""));
  if(!d||(d.error&&!(d.items&&d.items.length))){
    box.innerHTML='<div class="hint" style="padding:14px">读取失败：'+esc((d&&d.error)||"无响应")+'</div>';
    if(st)st.textContent="读取失败";
    return;
  }
  box.dataset.loaded="1";
  if(st){
    let s="市场共 <b>"+(d.count||0)+"</b> 个插件";
    const up=(d.items||[]).filter(function(x){return x.updatable;}).length;
    if(up)s+="，<b>"+up+"</b> 个可更新";
    if(d.error)s+=' <span style="color:var(--amber)">（用缓存）</span>';
    st.innerHTML=s;
  }
  renderMarket(d.items||[]);
}

function renderMarket(items){
  const box=document.getElementById("marketList");
  if(!items.length){
    box.innerHTML='<div class="plug-empty">'
      +'<div style="font-size:14px;color:var(--text-2);margin-bottom:8px">市场里还没有插件</div>'
      +'<div style="font-size:12px;color:var(--text-3);line-height:2">'
      +'收录方式是改 <code class="inline-code">nekoe-plugins</code> 仓库的 '
      +'<code class="inline-code">index.json</code> 然后提 PR。<br>'
      +'也可以直接用左边「已安装」页里的「从 GitHub 地址安装」。'
      +'</div></div>';
    return;
  }
  let h="";
  for(const p of items){
    // 市场只管"装"和"更新"。停用 / 配置 / 卸载全在「已安装」页 ——
    // 插件一多，在市场里翻卸载键根本找不着。
    let btn;
    if(p.updatable){
      btn='<button class="btn primary sm" onclick="updatePlugin(\''+jsq(p.name)+'\')">更新到 v'+esc(p.version)+'</button>';
    }else if(p.installed){
      btn='<button class="btn sm" onclick="setPlugTab(\'installed\')">已安装 · 去管理</button>';
    }else{
      btn='<button class="btn primary sm" onclick="installPlugin(\''+jsq(p.name)+'\')">安装</button>';
    }
    const tags=(p.tags||[]).map(function(t){return '<span class="plug-tag">'+esc(t)+'</span>';}).join("");
    const badge=p.installed
      ? (p.load_error?'<span class="plug-badge err">加载失败</span>'
         : (p.updatable?'<span class="plug-badge warn">可更新</span>'
            :'<span class="plug-badge ok">已安装</span>'))
      : '';
    h+='<div class="plug-card'+(p.load_error?" bad":"")+'">'
      +'<div class="plug-head"><div class="plug-title">'
      +'<b>'+esc(p.display_name||p.name)+'</b>'
      +'<span class="plug-ver">v'+esc(p.version||"")+'</span>'
      +badge+tags+'</div>'
      +'<div class="plug-actions">'+btn+'</div></div>'
      +'<div class="plug-desc">'+esc(p.description||"（没有描述）")+'</div>'
      +'<div class="plug-meta">'+(p.author?('作者：'+esc(p.author)+' · '):'')
      +'<a href="'+esc(p.repo||"#")+'" target="_blank">源码</a>'
      +(p.verified?' · <span style="color:var(--green)">已审核</span>':'')
      +'</div>'
      +(p.load_error?('<div class="plug-err">'+esc(p.load_error)+'</div>'):'')
      +'</div>';
  }
  box.innerHTML=h;
}

async function installPlugin(name){
  toast("正在安装 "+name+" …（要从 GitHub 下载，慢了等十几秒）");
  const r=await post("/admin/api/plugins/install",{name:name});
  if(!r||!r.ok){toast("安装失败："+((r&&r.error)||"无响应"),"err");return;}
  if(r.loaded)toast("已安装 "+(r.display_name||r.name)+" v"+r.version);
  else toast("装上了但没加载成功："+(r.error||"未知"),"err");
  loadPlugins(); loadMarket(1); loadPluginPages();
}

async function installFromUrl(){
  const el=document.getElementById("plugUrl");
  const url=(el&&el.value||"").trim();
  if(!url){toast("先填个 GitHub 地址","err");return;}
  toast("正在下载安装…");
  const r=await post("/admin/api/plugins/install",{url:url});
  if(!r||!r.ok){toast("安装失败："+((r&&r.error)||"无响应"),"err");return;}
  if(el)el.value="";
  toast("已安装 "+(r.display_name||r.name)+" v"+r.version+(r.loaded?"":"（加载有问题）"),
        r.loaded?"ok":"err");
  loadPlugins(); loadMarket(1); loadPluginPages();
}

async function uninstallPlugin(name){
  if(!confirm("确定卸载 "+name+" 吗？\n插件目录会被删掉。"))return;
  const r=await post("/admin/api/plugins/uninstall",{name:name});
  if(!r||!r.ok){toast("卸载失败："+((r&&r.error)||"无响应"),"err");return;}
  toast("已卸载 "+name);
  loadPlugins(); loadMarket(1); loadPluginPages();
}

async function updatePlugin(name){
  toast("正在更新 "+name+" …");
  const r=await post("/admin/api/plugins/update",{name:name});
  if(!r||!r.ok){toast("更新失败："+((r&&r.error)||"无响应"),"err");return;}
  toast("已更新到 v"+(r.version||"?"));
  loadPlugins(); loadMarket(1); loadPluginPages();
}

document.addEventListener("click",function(e){
  const b=(e.target&&e.target.closest)?e.target.closest("#plugSeg button"):null;
  if(b)setPlugTab(b.dataset.tab);
});

tick();pollLogs();pollStats();
// 深链：/admin?token=xxx&page=mem 直接打开某一页
// 必须等插件页注入完再跳 —— 否则 ?page=<插件页> 找不到 nav 项，会掉回概览。
loadPluginPages().then(function(){try{
  var m=/[?&]page=([\w-]+)/.exec(location.search);
  if(!m)return;
  var btn=document.querySelector('.nav-item[data-page="'+m[1]+'"]');
  if(btn)setPage(m[1],btn.dataset.title||"");
  // 再往细里跳：&tab=market 直接打开插件市场的标签页
  var t=/[?&]tab=([\w-]+)/.exec(location.search);
  if(t&&m[1]==="plugins"&&typeof setPlugTab==="function")setPlugTab(t[1]);
  // &cfg=插件名 直接弹出那个插件的配置小窗
  var c=/[?&]cfg=([\w.\-]+)/.exec(location.search);
  if(c&&typeof configFor==="function")configFor(c[1]);
}catch(e){}}).catch(function(){});
</script>

<!-- 插件配置小窗：每个插件一个，从插件卡片的「配置」按钮打开 -->
<div class="modal-mask" id="plugCfgMask" hidden onclick="if(event.target===this)closePlugCfg()">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="plugCfgTitle">
    <div class="modal-h">
      <h3 id="plugCfgTitle">插件配置</h3>
      <span class="en">Plugin Config</span>
      <button class="modal-x" onclick="closePlugCfg()" title="关闭（Esc）">✕</button>
    </div>
    <div class="modal-b" id="plugCfgBody"></div>
    <div class="modal-f">
      <button class="btn" onclick="closePlugCfg()">取消</button>
      <button class="btn primary" onclick="savePlugCfg()">保存</button>
    </div>
  </div>
</div>

<!-- 更新提示：点品牌区的「有新版本」打开 -->
<div class="modal-mask" id="updMask" hidden onclick="if(event.target===this)closeUpd()">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="updTitle">
    <div class="modal-h">
      <h3 id="updTitle">发现新版本</h3>
      <span class="en">Update</span>
      <button class="modal-x" onclick="closeUpd()" title="关闭（Esc）">✕</button>
    </div>
    <div class="modal-b" id="updBd"></div>
    <div class="modal-f">
      <button class="btn" onclick="closeUpd()">关闭</button>
      <button class="btn primary" id="updGo" onclick="dlDesktop()">下载桌面版</button>
    </div>
  </div>
</div>
</body>
</html>"""


import uvicorn
asgi = nonebot.get_asgi()

_shutdown_evt = threading.Event()


def run_server():
    global MAIN_LOOP
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        MAIN_LOOP = loop
        MAIN_LOOP_READY.set()
        config = uvicorn.Config(asgi, host="127.0.0.1", port=PORT,
                                log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        loop.run_until_complete(server.serve())
    except Exception as e:
        log_startup(f"服务器错误: {type(e).__name__}: {e}")
        log("error", "服务", f"管理界面启动失败: {type(e).__name__}: {e}")


def wait_port(timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def shutdown():
    """尽力收尾：停后台任务、关连接、合并 WAL、落盘配置。"""
    if _shutdown_evt.is_set():
        return
    _shutdown_evt.set()
    print(f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] 正在退出…", flush=True)
    loop = MAIN_LOOP
    if loop is not None and loop.is_running():
        async def _cleanup():
            for t in list(_BG_TASKS):
                t.cancel()
            try:
                await close_http()
            except Exception:
                pass
        try:
            asyncio.run_coroutine_threadsafe(_cleanup(), loop).result(timeout=5)
        except Exception:
            pass
    try:
        save_config()
    except Exception:
        pass
    try:
        DB.close()
    except Exception:
        pass
    # 退出时清一次语音临时目录
    try:
        _cleanup_voice_files()
    except Exception:
        pass
    log_startup("进程退出")



# ==================== 首次运行注册向导 ====================
_SETUP_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Nekolyra 初始化</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:"Microsoft YaHei","Segoe UI",sans-serif;background:#f5f6f8;
       color:#1f2329;padding:28px 34px;font-size:14px}
  h1{font-size:19px;margin-bottom:4px}
  .sub{color:#8a9099;font-size:12.5px;margin-bottom:20px}
  .sec{margin-bottom:18px}
  .sec-t{font-size:13px;font-weight:600;color:#3d434d;margin-bottom:9px;
         padding-left:8px;border-left:3px solid #4a7cf7}
  .row{margin-bottom:11px}
  label{display:block;font-size:12.5px;color:#5a6169;margin-bottom:5px}
  label .opt{color:#a8aeb6;font-weight:400}
  input,select{width:100%;padding:9px 11px;border:1px solid #d8dce3;
        border-radius:7px;font-size:13.5px;font-family:inherit;background:#fff;
        transition:border-color .15s}
  input:focus,select:focus{outline:none;border-color:#4a7cf7;
        box-shadow:0 0 0 3px rgba(74,124,247,.1)}
  .hint{font-size:11.5px;color:#a0a6ae;margin-top:4px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:11px}
  .err{background:#fff1f0;border:1px solid #ffccc7;color:#cf1322;
       padding:10px 13px;border-radius:7px;font-size:12.5px;margin-bottom:14px;
       display:none;white-space:pre-wrap}
  .foot{display:flex;justify-content:flex-end;gap:10px;margin-top:22px;
        padding-top:17px;border-top:1px solid #e6e9ee}
  button{padding:10px 26px;border:none;border-radius:7px;font-size:13.5px;
         font-family:inherit;cursor:pointer;font-weight:500}
  .primary{background:#4a7cf7;color:#fff}
  .primary:hover{background:#3a6ae6}
  .primary:disabled{background:#b9c8f5;cursor:not-allowed}
  .ok{text-align:center;padding:40px 20px}
  .ok .big{font-size:44px;margin-bottom:14px}
  .ok h2{font-size:17px;margin-bottom:9px}
  .ok p{color:#6b727b;font-size:13px;line-height:1.8}

</style></head><body>

<div id="form">
  <h1>Nekolyra 初始化</h1>
  <div class="sub">第一次运行，填几项就能用了（以后可以在管理界面里改）</div>

  <div class="err" id="err"></div>

  <div class="sec">
    <div class="sec-t">1. 管理员</div>
    <div class="grid2">
      <div class="row">
        <label>管理员 QQ 号 <span class="opt">必填</span></label>
        <input id="qq" placeholder="例如 10001" autocomplete="off">
        <div class="hint">你的 QQ 号，用于识别管理员身份</div>
      </div>
      <div class="row">
        <label>管理界面密码 <span class="opt">必填</span></label>
        <input id="token" placeholder="自己设一个" autocomplete="off">
        <div class="hint">进管理界面时要输，别用 123456</div>
      </div>
    </div>
  </div>

  <div class="sec">
    <div class="sec-t">2. 机器人账号</div>
    <div class="grid2">
      <div class="row">
        <label>机器人 QQ 号 <span class="opt">可留空</span></label>
        <input id="botqq" placeholder="机器人自己的QQ号" autocomplete="off">
        <div class="hint">就是 NapCat 登录的那个号</div>
      </div>
      <div class="row">
        <label>机器人密钥 <span class="opt">可留空</span></label>
        <input id="botsecret" placeholder="NapCat 的 secret" autocomplete="off">
        <div class="hint">没设 secret 就留空</div>
      </div>
    </div>
  </div>

  <div class="sec">
    <div class="sec-t">3. AI 对话 <span class="opt" style="font-weight:400;color:#a8aeb6">（可留空，之后再填）</span></div>
    <div class="row">
      <label>API Key</label>
      <input id="key" placeholder="智谱 / DeepSeek 的 API Key" autocomplete="off">
      <div class="hint">留空的话机器人不会回复聊天，但其他功能正常</div>
    </div>
    <div class="row">
      <label>接口地址</label>
      <select id="host">
        <option value="https://open.bigmodel.cn/api/paas/v4">智谱 open.bigmodel.cn</option>
        <option value="https://api.deepseek.com/v1">DeepSeek api.deepseek.com</option>
        <option value="http://localhost:11434/v1">本地 Ollama</option>
      </select>
    </div>
    <div class="row">
      <label>模型名</label>
      <input id="model" value="glm-4.7-flash">
    </div>
  </div>

  <div class="sec">
    <div class="sec-t">4. 语音 <span class="opt" style="font-weight:400;color:#a8aeb6">（可选）</span></div>
    <div class="row">
      <label>语音合成服务地址</label>
      <input id="voice" value="http://127.0.0.1:7861/v1/audio/speech">
      <div class="hint">没装 IndexTTS 就别开语音，机器人会只发文字</div>
    </div>
  </div>

  <div class="foot">
    <button class="primary" id="btn" onclick="submit()">完成并启动</button>
  </div>
</div>

<div id="done" style="display:none">
  <div class="ok">
    <div class="big">✅</div>
    <h2>配置已保存</h2>
    <p id="dmsg">正在重新启动…</p>
  </div>
</div>

<script>
async function submit(){
  const g = id => (document.getElementById(id).value || '').trim();
  const qq = g('qq'), token = g('token');
  const err = document.getElementById('err');
  const btn = document.getElementById('btn');

  err.style.display = 'none';
  const problems = [];
  if (!qq) problems.push('· 管理员 QQ 号没填');
  else if (!/^\d{5,12}$/.test(qq)) problems.push('· 管理员 QQ 号应该是 5-12 位数字');
  if (!token) problems.push('· 管理界面密码没填');
  else if (token.length < 4) problems.push('· 密码太短，至少 4 位');
  if (problems.length){
    err.textContent = problems.join('\n');
    err.style.display = 'block';
    return;
  }

  btn.disabled = true; btn.textContent = '保存中…';
  const data = {
    qq: qq, token: token,
    bot_qq: g('botqq'), bot_secret: g('botsecret'),
    api_key: g('key'), api_host: g('host'), api_model: g('model'),
    voice_url: g('voice')
  };
  try {
    const r = await window.pywebview.api.save(data);
    if (r && r.ok){
      document.getElementById('form').style.display = 'none';
      document.getElementById('done').style.display = 'block';
      document.getElementById('dmsg').textContent = r.msg || '正在重新启动…';
    } else {
      err.textContent = (r && r.error) || '保存失败';
      err.style.display = 'block';
      btn.disabled = false; btn.textContent = '完成并启动';
    }
  } catch(e){
    err.textContent = '保存出错：' + e;
    err.style.display = 'block';
    btn.disabled = false; btn.textContent = '完成并启动';
  }
}
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.tagName === 'INPUT') submit();
});
</script>
</body></html>"""


class _SetupApi:
    """注册向导的后端：接收表单，写配置文件，然后重启进程。"""

    def __init__(self):
        self.done = False
        self.window = None      # 由 run_setup_wizard 注入，保存后用它关窗

    def _close_window_later(self, delay=1.2):
        """延迟关窗。

        必须关窗：pywebview.start() 会一直阻塞到窗口关闭，
        不关窗的话 webview.start() 之后的 _restart_process() 永远执行不到
        （真实 bug：界面停在「正在重新启动…」不动）。
        延迟是为了让「配置已保存」先渲染出来给用户看到。
        """
        def _job():
            try:
                time.sleep(delay)
            except Exception:
                pass
            try:
                if self.window is not None:
                    self.window.destroy()
            except Exception as e:
                log_startup(f"关闭向导窗口失败: {type(e).__name__}: {e}")
        try:
            threading.Thread(target=_job, daemon=True, name="wizard-close").start()
        except Exception:
            pass

    def save(self, data):
        try:
            data = data or {}
            qq = str(data.get("qq") or "").strip()
            token = str(data.get("token") or "").strip()
            if not qq or not qq.isdigit():
                return {"ok": False, "error": "管理员 QQ 号填写不正确"}
            if len(token) < 4:
                return {"ok": False, "error": "管理界面密码至少 4 位"}

            # ---- 写 config.json ----
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if not isinstance(cfg, dict):
                    cfg = {}
            except Exception:
                cfg = {}
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            cfg["superusers"] = [int(qq)]
            cfg["admin_token"] = token
            api_key = str(data.get("api_key") or "").strip()
            if api_key:
                cfg["deepseek_api_key"] = api_key
            host = str(data.get("api_host") or "").strip()
            if host:
                cfg["deepseek_api_host"] = host
                cfg["vision_api_host"] = host
            model = str(data.get("api_model") or "").strip()
            if model:
                cfg["deepseek_model"] = model
            vurl = str(data.get("voice_url") or "").strip()
            if vurl:
                cfg["voice_api_url"] = vurl
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=4)

            # ---- 写 .env ----
            bot_qq = str(data.get("bot_qq") or "").strip()
            bot_secret = str(data.get("bot_secret") or "").strip()
            if bot_qq:
                entry = {
                    "id": bot_qq, "secret": bot_secret,
                    "intent": {"c2c_group_at_messages": True, "group_members": True},
                    "use_websocket": True,
                }
                qq_bots_line = "QQ_BOTS='" + json.dumps([entry], ensure_ascii=False) + "'"
            else:
                qq_bots_line = ("# 填上机器人账号后取消下面这行的注释\n"
                                "# QQ_BOTS='[{\"id\":\"机器人QQ\",\"secret\":\"密钥\","
                                "\"intent\":{\"c2c_group_at_messages\":true,"
                                "\"group_members\":true},\"use_websocket\":true}]'")
            env_path = os.path.join(BASE_DIR, ".env")
            lines = []
            replaced = False
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8",
                          errors="replace") as f:
                    _env_txt = f.read()
                for line in _env_txt.splitlines():
                    if line.strip().startswith("QQ_BOTS") or line.strip().startswith("# QQ_BOTS"):
                        if not replaced:
                            lines.append(qq_bots_line)
                            replaced = True
                    else:
                        lines.append(line)
            if not replaced:
                lines.insert(0, "DRIVER=~httpx+~websockets")
                lines.append(qq_bots_line)
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

            log_startup("注册向导：配置已写入，准备重启")
            self.done = True
            # 关窗 —— 否则 webview.start() 不返回，重启代码执行不到
            self._close_window_later()
            msg = "正在重新启动…" if bot_qq else \
                  "正在重新启动…（机器人 QQ 没填，启动后记得补 .env）"
            return {"ok": True, "msg": msg}
        except Exception as e:
            log_startup(f"注册向导保存失败: {type(e).__name__}: {e}")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _config_incomplete() -> bool:
    """判断是否需要走注册向导。"""
    if not os.path.exists(CONFIG_PATH):
        return True
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return True
    if not isinstance(cfg, dict):
        return True
    su = cfg.get("superusers") or []
    if not su:
        return True
    tok = str(cfg.get("admin_token") or "").strip()
    if not tok or tok == "change-me-please":
        return True
    return False


def run_setup_wizard() -> bool:
    """弹注册向导。返回 True 表示用户已完成配置（需要重启进程）。"""
    api = _SetupApi()
    try:
        import webview
    except ImportError:
        return False
    # 注意：pywebview 6.x 的 create_window() 不支持 icon 参数
    # （加了会抛 TypeError 导致窗口起不来，实测踩过）。
    # exe 的图标由 PyInstaller --icon 嵌入，Windows 会自动用在标题栏和任务栏。
    window = webview.create_window("Nekolyra 初始化", html=_SETUP_HTML,
                                   width=720, height=760, resizable=True,
                                   js_api=api)
    api.window = window          # 保存成功后用它关窗
    webview.start()

    if api.done:
        # 配置变了（.env 也改过）—— 必须重启进程才能让改动生效
        log_startup("注册向导完成，正在重新启动")
        _restart_process()
    return False


def _restart_process():
    """重启自身。

    优先 spawn 一个新进程（最可靠：全新进程，没有 pywebview/COM 残留），
    失败再退回 os.execv。都不行就提示用户手动重开。

    为什么不用 os.execv 打头：在 PyInstaller onefile + pywebview 下，
    窗口消息循环和 COM 对象还占着资源，execv 会失败或卡住，
    表现就是界面停在「正在重新启动…」不动（真实问题）。
    """
    # ---- 方案 1：spawn 新进程 ----
    try:
        exe = sys.executable
        args = [exe] + list(sys.argv[1:])
        kwargs = {"cwd": BASE_DIR, "close_fds": True}
        if os.name == "nt":
            # 脱离当前控制台/进程组，避免跟着父进程一起被关掉
            kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        subprocess.Popen(args, **kwargs)
        log_startup("已启动新进程，当前进程退出")
        # 跳过 pywebview 的清理，直接退，避免它卡住
        os._exit(0)
    except Exception as e:
        log_startup(f"spawn 新进程失败: {type(e).__name__}: {e}")

    # ---- 方案 2：execv ----
    try:
        log_startup("改用 os.execv 重启")
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        os.execv(sys.executable, [sys.executable] + list(sys.argv[1:]))
    except Exception as e:
        log_startup(f"os.execv 也失败: {type(e).__name__}: {e}")

    # ---- 方案 3：提示用户手动重开 ----
    show_msg("配置已保存 · 请手动启动",
             "配置已经写好了。\n\n"
             "本程序没能自动重启，请：\n"
             "  1. 关闭这个窗口\n"
             "  2. 重新双击 Nekolyra.exe\n\n"
             "（下次启动会直接进入管理界面）")
    sys.exit(0)


def main():
    """启动管理界面（uvicorn + 桌面窗口）。只有直接运行本文件时才会执行。"""
    # 这里原本有 global MAIN_LOOP —— main() 只读它、从不赋值，
    # 声不声明 global 都一样，删掉。

    # 配置不完整 -> 先弹注册向导（桌面版第一次运行走这里）
    if _config_incomplete():
        log_startup("配置不完整，启动注册向导")
        if run_setup_wizard():
            return
        # 向导不可用（没装 pywebview）或用户直接关了窗口 -> 走原来的提示
        os.environ.pop("ECHO_SETUP_WIZARD", None)
        show_msg("配置未完成",
                 f"请编辑配置文件：\n{CONFIG_PATH}\n\n"
                 f"至少填上 superusers（你的QQ号）和 admin_token（管理密码）。")
        sys.exit(0)

    MAIN_LOOP_READY.clear()
    print_banner()
    server_thread = threading.Thread(target=run_server, daemon=True, name="uvicorn")
    server_thread.start()

    if not wait_port(20.0):
        show_msg("启动失败",
                 f"服务器无法监听端口 {PORT}。\n\n"
                 f"可能端口被占用，或 startup.log 里有错误详情。")
        sys.exit(1)

    # token 必须做 URL 编码再拼进来。密码里只要有一个 # & = + 空格 或中文，
    # 裸拼就会把 URL 弄坏（# 后面全被当锚点、& 之后成了别的参数），
    # 服务端收到的 token 跟真密码不等 —— 注册完跳过去直接 401。
    # 页内那 15 处请求都用了 encodeURIComponent，只有这里漏了。
    from urllib.parse import quote as _q
    _tok_q = _q(str(ADMIN_TOKEN), safe="")
    url = f"http://127.0.0.1:{PORT}/admin?token={_tok_q}"

    window = None
    try:
        import webview
        # 同向导：pywebview 6.x 不支持 icon 参数，图标靠 exe 自身嵌入
        window = webview.create_window("Nekolyra —— QQ 群管 AI 助手", url,
                                       width=1280, height=820, min_size=(900, 600),
                                       background_color="#ffffff")

        def _on_window_closed():
            shutdown()
            os._exit(0)

        try:
            window.events.closed += _on_window_closed
        except Exception:
            pass
        webview.start()
    except ImportError:
        log_startup("未安装 pywebview，改用系统浏览器打开管理界面")
        import webbrowser
        webbrowser.open(url)
    except Exception as e:
        log_startup(f"窗口启动失败: {type(e).__name__}: {e}")
        import webbrowser
        webbrowser.open(url)

    print(f"\n  管理界面已就绪：{url}\n  （关闭本窗口即退出程序）\n", flush=True)
    if window is None:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    shutdown()
    os._exit(0)


if __name__ == "__main__":
    # 向导模式已在文件开头判断过（那一步必须更早，否则配置校验会先退出）
    main()