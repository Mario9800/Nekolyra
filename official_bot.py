# -*- coding: utf-8 -*-
"""QQ 官方机器人 · AI 对话 + 签到/积分 + 今日老婆 + 积分兑换"""
import os, sys, json, time, sqlite3, asyncio, io, base64, random, hashlib
from datetime import datetime, date, timedelta
import httpx
import websockets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DB_PATH = os.path.join(BASE_DIR, "data", "official_bot.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("[警告] 未安装 Pillow。请执行：pip install Pillow")

# 老婆介绍的超时。内外用同一个值 —— 以前外层 wait_for(4.5)
# 内层 httpx timeout=4.0，内层先炸，外层那句"生成介绍超时"
# 是死代码，日志里看到的是 [AI] raw 出错。
AI_INTRO_TIMEOUT = 4.5

# ============================================================
# 凭证
# ============================================================
APP_ID = "YOUR_APP_ID"
APP_SECRET = "YOUR_APP_SECRET"

# 填了占位符就直接退出。否则会带着 "YOUR_APP_ID" 去请求 token，拿到 401，
# 然后整条链路在日志里只留一句"获取 token 失败"，用户对着它发懵。
if (not APP_ID or APP_ID == "YOUR_APP_ID"
        or not APP_SECRET or APP_SECRET == "YOUR_APP_SECRET"):
    print("\n  [!] QQ 官方机器人的 APP_ID / APP_SECRET 还没填。\n"
          "      请编辑 official_bot.py，"
          "或用环境变量 QQ_APP_ID / QQ_APP_SECRET 覆盖。\n", flush=True)
    raise SystemExit(1)


OFFICIAL_INTENTS = (1 << 25) | (1 << 30) | (1 << 24) | (1 << 26)


DEFAULT_CONFIG = {
    "ai_enabled": True,
    "ollama_host": "http://localhost:11434",
    "ai_model": "qwen3:4b",
    "persona_name": "",
    "persona_identity": "一个活泼可爱的 AI 助手",
    "persona_behavior": "简短友好地回答问题",
    "persona_speech": "控制在 100 字以内",
    "sign_enabled": True,
    "sign_base_points": 10,
    "sign_streak_bonus": 2,
    "sign_max_bonus": 20,
    "wife_enabled": True,
    "wife_change_limit": 3,
    "wife_change_cost_base": 1,
    "wife_api_url": "https://api.pearapi.ai/api/today_wife",
    "wife_api_timeout": 8,
    "wife_intro_enabled": True,
    "menu_enabled": True,
    "panel_enabled": True,
    "napcat_api": "http://127.0.0.1:8080",
    "admin_token": "",
    # 积分兑换项（群共享库存，每个群独立）
    # id：唯一标识；name：显示名；cost：需要积分；stock：该群初始库存
    "exchange_items": [
        {"id": "cash5", "name": "5元现金", "cost": 800, "stock": 5},
    ],
}

if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CFG = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        CFG.setdefault(k, v)
else:
    CFG = DEFAULT_CONFIG


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ============================================================
# 缓存
# ============================================================
_MEDIA_CACHE = {}
_MEDIA_CACHE_TTL = 6 * 24 * 3600
_MEDIA_CACHE_MAX = 500


def _cache_get(key):
    item = _MEDIA_CACHE.get(key)
    if not item:
        return None
    file_info, exp = item
    if time.time() > exp:
        _MEDIA_CACHE.pop(key, None)
        return None
    return file_info


def _cache_put(key, file_info):
    if len(_MEDIA_CACHE) >= _MEDIA_CACHE_MAX:
        now = time.time()
        for k in list(_MEDIA_CACHE.keys()):
            if _MEDIA_CACHE[k][1] < now:
                _MEDIA_CACHE.pop(k, None)
    _MEDIA_CACHE[key] = (file_info, time.time() + _MEDIA_CACHE_TTL)


_NICK_CACHE = {}
_NICK_CACHE_TTL = 12 * 3600


def _nick_cache_get(openid):
    item = _NICK_CACHE.get(openid)
    if not item:
        return None
    name, exp = item
    if time.time() > exp:
        _NICK_CACHE.pop(openid, None)
        return None
    return name


def _nick_cache_put(openid, name):
    _NICK_CACHE[openid] = (name, time.time() + _NICK_CACHE_TTL)


# ============================================================
# 数据库
# ============================================================
class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS points(
                user_id TEXT NOT NULL, group_id TEXT NOT NULL,
                points INTEGER DEFAULT 0, sign_count INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0, best_streak INTEGER DEFAULT 0,
                last_sign_date TEXT, nickname TEXT,
                PRIMARY KEY (user_id, group_id))""")
            c.execute("""CREATE TABLE IF NOT EXISTS wife_records(
                user_id TEXT NOT NULL, group_id TEXT NOT NULL,
                wife_id TEXT, wife_nick TEXT,
                date TEXT NOT NULL, changes INTEGER DEFAULT 0,
                introduction TEXT,
                PRIMARY KEY (user_id, group_id, date))""")
            c.execute("""CREATE TABLE IF NOT EXISTS panel_groups(
                group_id TEXT PRIMARY KEY,
                panel_id TEXT,
                added_at TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS user_bindings(
                user_openid TEXT PRIMARY KEY,
                qq INTEGER NOT NULL,
                bound_at TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS group_bindings(
                group_openid TEXT PRIMARY KEY,
                group_id INTEGER NOT NULL,
                bound_at TEXT)""")
            # 兑换库存：每个群每项一条
            c.execute("""CREATE TABLE IF NOT EXISTS exchange_stock(
                group_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                remaining INTEGER DEFAULT 0,
                PRIMARY KEY (group_id, item_id))""")
            # 兑换记录
            c.execute("""CREATE TABLE IF NOT EXISTS exchange_records(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL, group_id TEXT NOT NULL,
                item_id TEXT NOT NULL, item_name TEXT,
                cost INTEGER DEFAULT 0,
                nickname TEXT,
                code TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_ex_rec ON exchange_records(group_id, user_id)")
            try:
                # 别静默删 —— 以前 except: pass，群不见了日志里一个字都没有
                cur = c.execute("DELETE FROM panel_groups "
                                    "WHERE panel_id IS NOT NULL "
                                    "AND panel_id NOT LIKE 'p_%'")
                if cur.rowcount:
                    log(f"[数据库] 清理了 {cur.rowcount} 条 panel_id 格式异常的旧记录（接口换了格式的话这不一定是脏数据）")
            except Exception as e:
                log(f"[数据库] 清理 panel_groups 失败: {type(e).__name__}: {e}")
            c.commit()

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

    def sign_rank(self, g, limit=10):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT user_id, nickname, points, sign_count, streak "
                "FROM points WHERE group_id=? ORDER BY points DESC LIMIT ?",
                (g, limit))]

    def points_spend(self, u, g, amount):
        if amount <= 0:
            info = self.sign_get(u, g)
            return True, (info["points"] if info else 0)
        with self._conn() as c:
            row = c.execute("SELECT points FROM points WHERE user_id=? AND group_id=?",
                            (u, g)).fetchone()
            if not row or row["points"] < amount:
                return False, (row["points"] if row else 0)
            new_points = row["points"] - amount
            c.execute("UPDATE points SET points=? WHERE user_id=? AND group_id=?",
                      (new_points, u, g))
            c.commit()
            return True, new_points

    def points_refund(self, u, g, amount):
        if amount <= 0:
            return
        with self._conn() as c:
            c.execute("UPDATE points SET points=points+? WHERE user_id=? AND group_id=?",
                      (amount, u, g))
            c.commit()

    def wife_get(self, u, g, today):
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM wife_records WHERE user_id=? AND group_id=? AND date=?",
                (u, g, today)).fetchone()
            return dict(row) if row else None

    def wife_save(self, u, g, wife_id, wife_nick, today, changes, introduction=""):
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM wife_records WHERE user_id=? AND group_id=? AND date=?",
                (u, g, today)).fetchone()
            if row:
                c.execute("""UPDATE wife_records SET wife_id=?, wife_nick=?, changes=?,
                             introduction=? WHERE user_id=? AND group_id=? AND date=?""",
                          (str(wife_id), wife_nick, changes, introduction, u, g, today))
            else:
                c.execute("INSERT INTO wife_records (user_id, group_id, wife_id, wife_nick, date, changes, introduction) VALUES(?,?,?,?,?,?,?)",
                          (u, g, str(wife_id), wife_nick, today, changes, introduction))
            c.commit()

    def panel_group_add(self, g, panel_id=None):
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO panel_groups (group_id, panel_id, added_at) VALUES (?, ?, ?)",
                (g, panel_id, datetime.now().isoformat()))
            c.commit()

    def panel_groups_all(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT group_id, panel_id, added_at FROM panel_groups")]

    def panel_group_remove(self, g):
        with self._conn() as c:
            c.execute("DELETE FROM panel_groups WHERE group_id=?", (g,))
            c.commit()

    def user_bind(self, openid, qq):
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO user_bindings (user_openid, qq, bound_at) VALUES (?,?,?)",
                      (openid, int(qq), datetime.now().isoformat()))
            c.commit()

    def user_get_qq(self, openid):
        with self._conn() as c:
            row = c.execute("SELECT qq FROM user_bindings WHERE user_openid=?",
                            (openid,)).fetchone()
            return row["qq"] if row else None

    def group_bind(self, group_openid, group_id):
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO group_bindings (group_openid, group_id, bound_at) VALUES (?,?,?)",
                      (group_openid, int(group_id), datetime.now().isoformat()))
            c.commit()

    def group_get_gid(self, group_openid):
        with self._conn() as c:
            row = c.execute("SELECT group_id FROM group_bindings WHERE group_openid=?",
                            (group_openid,)).fetchone()
            return row["group_id"] if row else None

    # ---------------- 兑换 ----------------
    def exchange_ensure(self, group_id, item_id, initial):
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM exchange_stock WHERE group_id=? AND item_id=?",
                            (group_id, item_id)).fetchone()
            if not row:
                c.execute("INSERT INTO exchange_stock(group_id, item_id, remaining) VALUES(?,?,?)",
                          (group_id, item_id, int(initial)))
                c.commit()

    def exchange_stock_get(self, group_id, item_id):
        with self._conn() as c:
            row = c.execute("SELECT remaining FROM exchange_stock WHERE group_id=? AND item_id=?",
                            (group_id, item_id)).fetchone()
            return row["remaining"] if row else 0

    def exchange_stock_all(self, group_id):
        with self._conn() as c:
            return {r["item_id"]: r["remaining"] for r in c.execute(
                "SELECT item_id, remaining FROM exchange_stock WHERE group_id=?", (group_id,))}

    def exchange_do(self, group_id, user_id, nickname, item_id, item_name, cost, initial_stock):
        """原子：扣库存 + 扣积分 + 写记录。返回 (ok, msg, code)"""
        with self._conn() as c:
            row = c.execute("SELECT remaining FROM exchange_stock WHERE group_id=? AND item_id=?",
                            (group_id, item_id)).fetchone()
            if not row:
                c.execute("INSERT INTO exchange_stock(group_id, item_id, remaining) VALUES(?,?,?)",
                          (group_id, item_id, int(initial_stock)))
                remaining = int(initial_stock)
            else:
                remaining = row["remaining"]
            if remaining <= 0:
                return False, f"「{item_name}」已兑完", None
            prow = c.execute("SELECT points FROM points WHERE user_id=? AND group_id=?",
                             (user_id, group_id)).fetchone()
            have = prow["points"] if prow else 0
            if have < cost:
                return False, f"积分不足，需要 {cost}，你有 {have}", None
            c.execute("UPDATE exchange_stock SET remaining=remaining-1 WHERE group_id=? AND item_id=?",
                      (group_id, item_id))
            c.execute("UPDATE points SET points=points-? WHERE user_id=? AND group_id=?",
                      (cost, user_id, group_id))
            code = "EX" + datetime.now().strftime("%m%d%H%M%S") + str(random.randint(10, 99))
            c.execute("""INSERT INTO exchange_records
                         (user_id, group_id, item_id, item_name, cost, nickname, code, status, created_at)
                         VALUES(?,?,?,?,?,?,?,?,?)""",
                      (user_id, group_id, item_id, item_name, cost, nickname, code,
                       "pending", datetime.now().isoformat()))
            c.commit()
            return True, f"兑换成功，剩余 {remaining-1} 份", code

    def exchange_list_mine(self, user_id, group_id, limit=10):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM exchange_records WHERE user_id=? AND group_id=? "
                "ORDER BY id DESC LIMIT ?", (user_id, group_id, limit))]

    def exchange_redeem(self, group_id, code):
        with self._conn() as c:
            row = c.execute("SELECT * FROM exchange_records WHERE group_id=? AND code=?",
                            (group_id, code)).fetchone()
            if not row:
                return {"error": f"未找到编号 {code}"}
            if row["status"] == "redeemed":
                return {"error": f"{code} 已核销过了"}
            c.execute("UPDATE exchange_records SET status='redeemed' WHERE id=?", (row["id"],))
            c.commit()
            return dict(row)


DB = Database(DB_PATH)


# ============================================================
# 昵称解析
# ============================================================
async def resolve_nickname(group_openid, user_openid):
    if not user_openid:
        return "匿名"
    cached = _nick_cache_get(user_openid)
    if cached:
        return cached
    qq = DB.user_get_qq(user_openid)
    if qq:
        gid = DB.group_get_gid(group_openid)
        if gid:
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    r = await client.get(
                        f"{CFG.get('napcat_api', 'http://127.0.0.1:8080').rstrip('/')}/api/nickname",
                        params={"token": CFG.get("admin_token", ""),
                                "group_id": gid, "user_id": qq})
                    if r.status_code == 200:
                        name = (r.json().get("nickname") or "").strip()
                        if name and name != str(qq):
                            _nick_cache_put(user_openid, name)
                            return name
            except Exception as e:
                log(f"[昵称] 查 NapCat 失败: {type(e).__name__}: {e}")
    fallback = "用户" + user_openid[-6:].upper()
    # 不写缓存：NapCat 重启或接口抖一下就会走到这里，写进去要挂 12 小时，
    # 期间所有人的昵称都是"用户XXXXXX"，明明服务早恢复了。
    return fallback


def short_nick(openid):
    if not openid:
        return "匿名"
    return "用户" + openid[-6:].upper()


# ============================================================
# Token
# ============================================================
class TokenManager:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = None
        self.expires_at = 0
        self._lock = None   # 刷新 token 用，防并发重复请求

    async def get(self):
        r"""拿 token。**失败返回 None，绝不抛。**

        以前这里是直接 raise RuntimeError —— 而 11 个调用方一个 try 都没有，
        所以 APP_ID 填错、secret 过期、或者网络抖一下，异常会一路穿过事件
        循环，把整条消息处理链炸掉，日志里只剩一句"获取 token 失败"。
        """
        now = time.time()
        if self.token and now < self.expires_at - 60:
            return self.token
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            # 双重检查：等锁期间别人可能已经刷过了
            now = time.time()
            if self.token and now < self.expires_at - 60:
                return self.token
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    r = await client.post(
                        "https://bots.qq.com/app/getAppAccessToken",
                        json={"appId": APP_ID, "clientSecret": APP_SECRET})
                if r.status_code != 200:
                    log(f"[Token] 获取失败 HTTP {r.status_code}: {r.text[:200]}")
                    return None
                data = r.json()
                tok = data.get("access_token")
                if not tok:
                    log(f"[Token] 响应里没有 access_token：{str(data)[:200]}")
                    return None
                self.token = tok
                try:
                    self.expires_at = now + int(data.get("expires_in", 7200))
                except (TypeError, ValueError):
                    self.expires_at = now + 7200
                return self.token
            except Exception as e:
                log(f"[Token] 请求异常: {type(e).__name__}: {e}")
                return None





TOKEN_MGR = TokenManager(APP_ID, APP_SECRET)


# ============================================================
# AI
# ============================================================
async def ai_chat(user_msg, mode="group"):
    if not CFG.get("ai_enabled", True):
        return None
    host = CFG.get("ollama_host", "").rstrip("/")
    model = CFG.get("ai_model", "qwen3:4b")
    if not host:
        return None

    name = CFG.get("persona_name", "").strip()
    identity = CFG.get("persona_identity", "").strip()
    behavior = CFG.get("persona_behavior", "").strip()
    speech = CFG.get("persona_speech", "").strip()

    parts = []
    if name: parts.append(f"你的名字是「{name}」。")
    if identity: parts.append(f"【人格设定】{identity}")
    if behavior: parts.append(f"【行为准则】{behavior}")
    if speech: parts.append(f"【表达风格】{speech}")
    if mode == "group":
        parts.append("注意：现在是在 QQ 群里，有人 @ 你。回复要简短自然。")
    else:
        parts.append("注意：现在是跟一个用户私聊。回复要简短自然。")
    sys_p = "\n\n".join(parts)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{host}/api/chat", json=payload)
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        log(f"[AI] 调用出错: {type(e).__name__}: {e}")
        return None


async def _ai_raw(host, payload):
    try:
        async with httpx.AsyncClient(timeout=AI_INTRO_TIMEOUT) as client:
            r = await client.post(f"{host}/api/chat", json=payload)
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        log(f"[AI] raw 出错: {type(e).__name__}: {e}")
        return None


async def _gen_wife_intro(role_name):
    if not CFG.get("wife_intro_enabled", True):
        return ""
    if not CFG.get("ai_enabled", True):
        return ""
    host = CFG.get("ollama_host", "").rstrip("/")
    model = CFG.get("ai_model", "qwen3:4b")
    if not host:
        return ""
    prompt = (
        f"请用一句 15-30 字的中文，温柔浪漫地描述二次元角色「{role_name}」给人的感觉。\n"
        f"直接输出描述文字，不要引号，不要括号，不要换行，不要提到 AI 或模型。"
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    try:
        r = await asyncio.wait_for(_ai_raw(host, payload), timeout=AI_INTRO_TIMEOUT)
    except Exception as e:
        log(f"[老婆] 生成介绍超时: {type(e).__name__}")
        return ""
    if not r:
        return ""
    r = r.strip().strip('"').strip("「」『』").replace("\n", " ")
    return r[:80]


# ============================================================
# 图片处理
# ============================================================
async def fetch_and_convert_image(image_url, max_side=960, quality=72):
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            raw = r.content
    except Exception as e:
        log(f"[图片] 下载失败: {type(e).__name__}: {e}")
        return None

    if not HAS_PIL:
        return raw

    try:
        img = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")
        if max(img.size) > max_side:
            ratio = max_side / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        log(f"[图片] 转换完成：{len(raw)//1024}KB → {len(data)//1024}KB")
        return data
    except Exception as e:
        log(f"[图片] 转换失败: {type(e).__name__}: {e}，使用原图")
        return raw


# ============================================================
# 富媒体上传
# ============================================================
def _api_base(scope, target_id):
    if scope == "group":
        return f"https://api.sgroup.qq.com/v2/groups/{target_id}/"
    return f"https://api.sgroup.qq.com/v2/users/{target_id}/"


async def upload_media(scope, target_id, file_type, file_bytes):
    token = await TOKEN_MGR.get()
    if not token:
        log("[QQ官方] 拿不到 token，跳过本次发送")
        return False
    api = _api_base(scope, target_id) + "files"
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}

    try:
        b64 = base64.b64encode(file_bytes).decode("ascii")
    except Exception as e:
        log(f"[上传] base64 编码失败: {type(e).__name__}: {e}")
        return None

    body = {"file_type": file_type, "file_data": b64, "srv_send_msg": False}
    log(f"[上传] base64 长度 {len(b64)//1024}KB")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(api, headers=headers, json=body)
            if r.status_code >= 400:
                log(f"[上传] 失败 {r.status_code}: {r.text[:500]}")
                return None
            resp = r.json()
            file_info = resp.get("file_info")
            if file_info:
                log(f"[上传] 成功 file_info={file_info[:40]}...")
                return file_info
            log(f"[上传] 响应无 file_info：{resp}")
            return None
    except Exception as e:
        log(f"[上传] 异常: {type(e).__name__}: {e}")
        return None


async def upload_group_image(group_openid, image_url):
    cache_key = f"{group_openid}|{image_url}"

    cached = _cache_get(cache_key)
    if cached:
        log("[缓存] file_info 命中，跳过下载+上传")
        return cached

    jpg_bytes = await fetch_and_convert_image(image_url)
    if not jpg_bytes:
        return None
    file_size = len(jpg_bytes)
    if file_size > 20 * 1024 * 1024:
        log(f"[群图片] 图片太大 {file_size//1024//1024}MB > 20MB")
        return None
    log(f"[群图片] 准备上传 {file_size//1024}KB")

    file_info = await upload_media("group", group_openid, 1, jpg_bytes)

    if file_info:
        _cache_put(cache_key, file_info)
        log(f"[缓存] 已写入，当前 {len(_MEDIA_CACHE)} 条")
    return file_info


# ============================================================
# 发送消息
# ============================================================
async def send_group_msg(group_openid, content, msg_id=None, msg_seq=1):
    token = await TOKEN_MGR.get()
    if not token:
        log("[QQ官方] 拿不到 token，跳过本次发送")
        return False
    url = f"https://api.sgroup.qq.com/v2/groups/{group_openid}/messages"
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    if len(content) > 4000:
        # 别硬切 —— 用户会看到"话说到一半"。优先断在句末标点上。
        cut = content[:4000]
        for sep in ("。", "！", "？", "\n", ".", "!", "?"):
            idx = cut.rfind(sep)
            if idx > 3000:
                cut = cut[:idx + 1]
                break
        content = cut
    body = {"content": content, "msg_type": 0, "msg_seq": msg_seq}
    if msg_id:
        body["msg_id"] = msg_id
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            log(f"[发送群消息] 失败 {r.status_code}: {r.text[:300]}")
            return False
        return True


async def send_group_markdown(group_openid, md_content, msg_id=None, msg_seq=1):
    token = await TOKEN_MGR.get()
    if not token:
        log("[QQ官方] 拿不到 token，跳过本次发送")
        return False
    url = f"https://api.sgroup.qq.com/v2/groups/{group_openid}/messages"
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    body = {"msg_type": 2, "markdown": {"content": md_content[:4000]}, "msg_seq": msg_seq}
    if msg_id:
        body["msg_id"] = msg_id
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            log(f"[发送Markdown] 失败 {r.status_code}: {r.text[:300]}")
            return False
        log("[发送Markdown] 成功")
        return True


async def send_md_fallback(group_openid, md_content, msg_id=None, msg_seq=1):
    ok = await send_group_markdown(group_openid, md_content, msg_id=msg_id, msg_seq=msg_seq)
    if ok:
        return True
    plain = md_content
    for p in ("## ", "### ", "> ", "**", "*"):
        plain = plain.replace(p, "")
    log("[降级] markdown 发失败，改纯文本")
    return await send_group_msg(group_openid, plain, msg_id=msg_id, msg_seq=msg_seq)


async def send_group_media_with_text(group_openid, file_info, content=None, msg_id=None, msg_seq=1):
    token = await TOKEN_MGR.get()
    if not token:
        log("[QQ官方] 拿不到 token，跳过本次发送")
        return False
    url = f"https://api.sgroup.qq.com/v2/groups/{group_openid}/messages"
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    body = {"msg_type": 7, "media": {"file_info": file_info}, "msg_seq": msg_seq}
    if content:
        body["content"] = content[:4000]
    if msg_id:
        body["msg_id"] = msg_id
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            log(f"[发送图文] 失败 {r.status_code}: {r.text[:300]}")
            return False
        log("[发送图文] 成功")
        return True


async def send_c2c_msg(openid, content, msg_id=None, msg_seq=1):
    token = await TOKEN_MGR.get()
    if not token:
        log("[QQ官方] 拿不到 token，跳过本次发送")
        return False
    url = f"https://api.sgroup.qq.com/v2/users/{openid}/messages"
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    body = {"content": content[:4000], "msg_type": 0, "msg_seq": msg_seq}
    if msg_id:
        body["msg_id"] = msg_id
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            log(f"[发送私聊] 失败 {r.status_code}: {r.text[:300]}")
            return False
        return True


async def respond_interaction(interaction_id, code=0):
    if not interaction_id:
        return False
    token = await TOKEN_MGR.get()
    if not token:
        log("[QQ官方] 拿不到 token，跳过本次发送")
        return False
    url = f"https://api.sgroup.qq.com/interactions/{interaction_id}"
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.put(url, headers=headers, json={"code": code})
        if r.status_code < 400:
            log(f"[互动响应] {interaction_id[-8:]} code={code}")
            return True
        else:
            log(f"[互动响应] 失败 {r.status_code}: {r.text[:200]}")
            return False


# ============================================================
# 自定义菜单
# ============================================================
async def set_custom_menu():
    if not CFG.get("menu_enabled", True):
        return
    token = await TOKEN_MGR.get()
    if not token:
        log("[QQ官方] 拿不到 token，跳过本次发送")
        return False
    url = "https://api.sgroup.qq.com/v2/menu"
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    body = {
        "menu": {
            "items": [
                {"name": "签到", "type": "send_message", "send_message": "签到"},
                {"name": "我的积分", "type": "send_message", "send_message": "我的积分"},
                {"name": "签到排行", "type": "send_message", "send_message": "签到排行"},
                {"name": "今日老婆", "type": "send_message", "send_message": "今日老婆"},
                {"name": "积分兑换", "type": "send_message", "send_message": "兑换"},
            ]
        }
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.put(url, headers=headers, json=body)
        if r.status_code < 400:
            log("[菜单] 自定义菜单设置成功")
            return True
        else:
            log(f"[菜单] 设置失败 {r.status_code}: {r.text[:500]}")
            return False


# ============================================================
# 指令面板
# ============================================================
async def create_command_panel(group_openid):
    if not CFG.get("panel_enabled", True):
        return None
    if not group_openid:
        return None
    token = await TOKEN_MGR.get()
    if not token:
        log("[QQ官方] 拿不到 token，跳过本次发送")
        return False
    url = "https://api.sgroup.qq.com/v2/panels"
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    body = {
        "scope": "group",
        "target_type": "specific",
        "group_openids": [group_openid],
        "panel": {
            "remark": "Chroma 群指令面板",
            "items": [
                {"name": "签到", "type": "command", "command": "签到", "desc": "每日签到"},
                {"name": "我的积分", "type": "command", "command": "我的积分", "desc": "查看积分"},
                {"name": "签到排行", "type": "command", "command": "签到排行", "desc": "本群排行"},
                {"name": "今日老婆", "type": "command", "command": "今日老婆", "desc": "抽今日老婆"},
                {"name": "换老婆", "type": "command", "command": "换老婆", "desc": "消耗积分换一个"},
                {"name": "兑换", "type": "command", "command": "兑换", "desc": "积分兑换"},
            ]
        }
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code < 400:
            try:
                resp = r.json()
            except Exception:
                resp = {}
            panel_id = resp.get("panel_id")
            if not panel_id or not str(panel_id).startswith("p_"):
                log(f"[面板] panel_id 格式异常，放弃保存: {panel_id}")
                return None
            log(f"[面板] 群 {group_openid[-8:]} 创建成功 panel_id={panel_id}")
            DB.panel_group_add(group_openid, panel_id)
            return panel_id
        else:
            log(f"[面板] 群 {group_openid[-8:]} 创建失败 {r.status_code}: {r.text[:500]}")
            return None


async def modify_panel_target(panel_id, op, group_openids=None, user_openids=None):
    if not panel_id or not str(panel_id).startswith("p_"):
        return False
    token = await TOKEN_MGR.get()
    if not token:
        log("[QQ官方] 拿不到 token，跳过本次发送")
        return False
    url = f"https://api.sgroup.qq.com/v2/panels/{panel_id}/target"
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    body = {"op": op}
    if group_openids:
        body["group_openids"] = group_openids
    if user_openids:
        body["user_openids"] = user_openids
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.put(url, headers=headers, json=body)
        if r.status_code < 400:
            log(f"[面板] {panel_id[-8:]} 关联修改成功 op={op}")
            return True
        else:
            log(f"[面板] {panel_id[-8:]} 关联修改失败 {r.status_code}: {r.text[:500]}")
            return False


async def refresh_all_panels():
    if not CFG.get("panel_enabled", True):
        return
    groups = DB.panel_groups_all()
    if not groups:
        log("[面板] 尚未记录任何群，跳过")
        return
    log(f"[面板] 为 {len(groups)} 个群检查面板")
    for g in groups:
        try:
            gid = g["group_id"]
            pid = g.get("panel_id")
            if pid and str(pid).startswith("p_"):
                await modify_panel_target(pid, "add", group_openids=[gid])
            else:
                log(f"[面板] 群 {gid[-8:]} panel_id 无效，重建")
                await create_command_panel(gid)
            await asyncio.sleep(0.6)
        except Exception as e:
            log(f"[面板] 群 {g['group_id'][-8:]} 刷新失败: {type(e).__name__}: {e}")


# ============================================================
# 工具
# ============================================================
def extract_text(d):
    content = (d.get("content") or "").strip()
    if content:
        return content
    md = d.get("markdown") or {}
    return (md.get("content") or "").strip()


def normalize_cmd(text, bot_name=""):
    if not text:
        return ""
    s = text.strip()
    if s.startswith("@"):
        for i, ch in enumerate(s):
            if i > 0 and ch in "/！!.。~～-— ":
                s = s[i:].lstrip()
                break
    while s and s[0] in "/！!.。~～-—":
        s = s[1:].lstrip()
    return s


# ============================================================
# 业务逻辑
# ============================================================
async def do_sign(group_id, user_id, nickname):
    if not CFG.get("sign_enabled", True):
        return {"md": "> **签到功能已关闭**"}
    today = date.today()
    today_str = today.isoformat()

    info = DB.sign_get(user_id, group_id)
    if info and info.get("last_sign_date") == today_str:
        md = (f"## 今日已签到\n\n"
              f"> 用户：**{nickname}**\n"
              f"> 当前积分：**{info['points']}** 分\n"
              f"> 连续签到：**{info['streak']}** 天")
        return {"md": md}

    base = int(CFG.get("sign_base_points", 10))
    bonus_per = int(CFG.get("sign_streak_bonus", 2))
    bonus_cap = int(CFG.get("sign_max_bonus", 20))

    new_streak = 1
    if info and info.get("last_sign_date"):
        try:
            last_d = date.fromisoformat(info["last_sign_date"])
            if (today - last_d).days == 1:
                new_streak = (info.get("streak") or 0) + 1
        except Exception:
            pass

    bonus = min((new_streak - 1) * bonus_per, bonus_cap)
    points_added = base + bonus
    total = DB.sign_do(user_id, group_id, nickname, points_added, new_streak, today_str)
    log(f"[签到] {nickname} +{points_added} 分，累计 {total}")

    md = (f"## 签到成功\n\n"
          f"> 用户：**{nickname}**\n"
          f"> 本次得分：**+{points_added}** 分\n"
          + (f"> 连续奖励：**+{bonus}** 分（连续 {new_streak} 天）\n" if bonus > 0 else "")
          + f"> 累计积分：**{total}** 分\n"
          f"> 连续天数：**{new_streak}** 天")
    return {"md": md}


async def do_rank(group_id, limit=10):
    rows = DB.sign_rank(group_id, limit=limit)
    if not rows:
        return {"md": "> **本群还没有人签到过。**"}
    lines = [f"## 本群签到排行\n"]
    for i, r in enumerate(rows):
        prefix = ["🥇", "🥈", "🥉"][i] if i < 3 else f"**{i+1}.**"
        lines.append(f"{prefix} {r['nickname'] or short_nick(r['user_id'])} — **{r['points']}** 分")
    return {"md": "\n".join(lines)}


async def do_me(group_id, user_id):
    info = DB.sign_get(user_id, group_id)
    if not info:
        return {"md": "> **你还没有签到记录，发「签到」开始吧。**"}
    md = (f"## 我的签到数据\n\n"
          f"> 累计积分：**{info['points']}** 分\n"
          f"> 签到次数：**{info['sign_count']}** 次\n"
          f"> 连续天数：**{info['streak']}** 天\n"
          f"> 最长连续：**{info.get('best_streak') or 0}** 天")
    return {"md": md}


def _wife_change_cost(changes):
    base = int(CFG.get("wife_change_cost_base", 1))
    if changes <= 0:
        return 0
    return base * (2 ** (changes - 1))


async def do_wife(group_id, user_id, nickname, change=False):
    if not CFG.get("wife_enabled", True):
        return {"error": "今日老婆功能已关闭"}
    today_str = date.today().isoformat()
    limit = int(CFG.get("wife_change_limit", 3))
    api_url = CFG.get("wife_api_url", "https://api.pearapi.ai/api/today_wife")
    api_timeout = float(CFG.get("wife_api_timeout", 8))

    row = DB.wife_get(user_id, group_id, today_str)
    points_spent = 0

    try:
        if row:
            if (row.get("changes") or 0) >= limit:
                return {"error": f"今天已经换过 {limit} 次了，明天再来吧～"}
            changes = (row.get("changes") or 0) + 1
            cost = _wife_change_cost(changes)
            if cost > 0:
                ok, left = DB.points_spend(user_id, group_id, cost)
                if not ok:
                    return {"error": f"积分不足，本次换老婆需要 {cost} 积分，你只有 {left} 分。先签到攒分吧～"}
                points_left = left
                points_spent = cost
            else:
                info = DB.sign_get(user_id, group_id)
                points_left = info["points"] if info else 0
        else:
            changes = 0
            info = DB.sign_get(user_id, group_id)
            points_left = info["points"] if info else 0

        raw_seed = f"{group_id}|{user_id}|{today_str}|{changes}|{random.randint(1, 10**9)}"
        wife_seed = hashlib.md5(raw_seed.encode()).hexdigest()[:16]
        log(f"[老婆] 请求 pearapi seed={wife_seed} changes={changes} cost={points_spent}")

        try:
            async with httpx.AsyncClient(timeout=api_timeout) as client:
                r = await client.get(api_url, params={"id": wife_seed})
                r.raise_for_status()
                resp = r.json()
        except Exception as e:
            log(f"[老婆] API 请求失败: {type(e).__name__}: {e}")
            if points_spent > 0:
                DB.points_refund(user_id, group_id, points_spent)
            return {"error": f"老婆 API 请求失败：{type(e).__name__}"}

        if resp.get("code") != 200 or not resp.get("data"):
            msg = resp.get("msg", "未知错误")
            log(f"[老婆] API 返回异常: {msg}")
            if points_spent > 0:
                DB.points_refund(user_id, group_id, points_spent)
            return {"error": f"API 返回异常：{msg}"}

        data = resp["data"]
        image_url = data.get("image_url", "")
        role_name = data.get("role_name", "未知")
        log(f"[老婆] pearapi 返回 role={role_name} url={image_url[:80]}")

        if not image_url:
            if points_spent > 0:
                DB.points_refund(user_id, group_id, points_spent)
            return {"error": "API 未返回图片地址"}

        DB.wife_save(user_id, group_id, image_url, role_name, today_str, changes, "")
        log(f"[老婆] {nickname} 抽到 {role_name}（第 {changes+1} 次，消耗 {points_spent} 分）")

        intro_task = asyncio.create_task(_gen_wife_intro(role_name))

        return {
            "nickname": nickname,
            "role_name": role_name,
            "image_url": image_url,
            "intro_task": intro_task,
            "points_spent": points_spent,
            "points_left": points_left,
            "changes": changes,
            "limit": limit,
            "is_change": changes > 0,
            "is_cached": False,
        }

    except asyncio.CancelledError:
        if points_spent > 0:
            DB.points_refund(user_id, group_id, points_spent)
        log(f"[老婆] {nickname} 任务被取消，已退还 {points_spent} 积分")
        raise


# ============================================================
# 兑换业务
# ============================================================
def _exchange_items():
    items = CFG.get("exchange_items", [])
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        try:
            out.append({
                "id": str(it.get("id", "")).strip(),
                "name": str(it.get("name", "")).strip(),
                "cost": int(it.get("cost", 0)),
                "stock": int(it.get("stock", 0)),
            })
        except Exception:
            continue
    return [x for x in out if x["id"] and x["name"] and x["cost"] > 0]


async def do_exchange_list(group_id):
    items = _exchange_items()
    if not items:
        return {"md": "> **管理员还没有配置兑换项。**"}
    stocks = DB.exchange_stock_all(group_id)
    lines = ["## 积分兑换"]
    for i, it in enumerate(items):
        # 首次访问某群，从 config 初始化库存
        if it["id"] not in stocks:
            DB.exchange_ensure(group_id, it["id"], it["stock"])
            stocks[it["id"]] = it["stock"]
        left = stocks.get(it["id"], 0)
        lines.append(f"> **{i+1}. {it['name']}** — {it['cost']} 积分 · 剩余 **{left}** 份")
    lines.append("")
    lines.append("> 发送「兑换 名称」进行兑换，如「兑换 5元」")
    lines.append("> 发送「我的兑换」查看兑换记录")
    return {"md": "\n".join(lines)}


async def do_exchange(group_id, user_id, nickname, query):
    items = _exchange_items()
    if not items:
        return {"error": "管理员还没有配置兑换项"}
    query = query.strip()
    if not query:
        return {"error": "用法：兑换 名称，如「兑换 5元」"}
    # 精确匹配优先，其次包含
    matched = None
    for it in items:
        if query == it["id"] or query == it["name"]:
            matched = it
            break
    if not matched:
        for it in items:
            if query in it["name"] or it["name"] in query or query == it["id"]:
                matched = it
                break
    if not matched:
        names = " / ".join(f"{it['name']}" for it in items)
        return {"error": f"没有找到「{query}」。可用：{names}"}

    DB.exchange_ensure(group_id, matched["id"], matched["stock"])
    ok, msg, code = DB.exchange_do(
        group_id, user_id, nickname,
        matched["id"], matched["name"], matched["cost"], matched["stock"])
    if not ok:
        return {"error": msg}
    log(f"[兑换] {nickname} 兑换 {matched['name']} 消耗 {matched['cost']} 分，编号 {code}")
    info = DB.sign_get(user_id, group_id)
    left = info["points"] if info else 0
    md = (f"## 兑换成功\n\n"
          f"> 兑换项：**{matched['name']}**\n"
          f"> 消耗积分：**{matched['cost']}**\n"
          f"> 剩余积分：**{left}**\n"
          f"> 兑换编号：**{code}**\n\n"
          f"> 找群主/管理员出示编号领取，领取后可由管理员核销。")
    return {"md": md}


async def do_exchange_mine(group_id, user_id):
    rows = DB.exchange_list_mine(user_id, group_id, limit=10)
    if not rows:
        return {"md": "> **你还没有兑换记录。**"}
    lines = ["## 我的兑换"]
    for r in rows:
        st = "已领取" if r["status"] == "redeemed" else "待领取"
        lines.append(f"> **{r['item_name']}** · {r['cost']}分 · {st} · `{r['code']}`")
    return {"md": "\n".join(lines)}


async def do_redeem(group_id, user_id, nickname, code):
    code = code.strip().upper()
    if not code:
        return {"error": "用法：核销 EX123456"}
    r = DB.exchange_redeem(group_id, code)
    if "error" in r:
        return {"error": r["error"]}
    md = (f"## 已核销\n\n"
          f"> 编号：`{r['code']}`\n"
          f"> 兑换项：**{r['item_name']}**\n"
          f"> 兑换人：{r['nickname'] or short_nick(r['user_id'])}\n"
          f"> 时间：{r['created_at'][:19]}")
    log(f"[核销] {nickname} 核销 {code}（{r['item_name']}）")
    return {"md": md}


# ============================================================
# 绑定指令
# ============================================================
async def do_bind_qq(group_openid, user_openid, qq_str):
    try:
        qq = int(qq_str.strip())
        if qq < 10000:
            return "QQ 号格式不对"
    except Exception:
        return "用法：绑定QQ 123456789"
    DB.user_bind(user_openid, qq)
    _NICK_CACHE.pop(user_openid, None)
    log(f"[绑定] user={short_nick(user_openid)} → QQ {qq}")
    name = await resolve_nickname(group_openid, user_openid)
    return f"绑定成功：QQ {qq}\n你的群昵称：{name}"


async def do_bind_group(group_openid, user_openid, gid_str):
    try:
        gid = int(gid_str.strip())
    except Exception:
        return "用法：绑定群 123456789"
    DB.group_bind(group_openid, gid)
    log(f"[绑定] group={group_openid[-8:]} → QQ 群 {gid}")
    return f"本群已绑定到 QQ 群 {gid}"


SIGN_KW = {"签到", "打卡"}
RANK_KW = {"签到排行", "积分排行", "排行榜", "签到榜", "积分榜"}
ME_KW = {"我的积分", "我的签到"}
WIFE_KW = {"今日老婆", "抽老婆", "我要老婆", "我的老婆"}
WIFE_CHANGE_KW = {
    "换老婆", "换个老婆", "换一个老婆", "再换老婆", "再换一个",
    "换lp", "换LP", "换Lp",
}
BIND_KW = {"绑定QQ", "绑定qq", "绑定"}
BIND_GROUP_KW = {"绑定群", "设置群号"}
EXCHANGE_LIST_KW = {"兑换", "兑换列表", "商品列表", "积分兑换"}
EXCHANGE_MINE_KW = {"我的兑换"}
REDEEM_KW = {"核销"}

ALL_CMDS = (SIGN_KW | RANK_KW | ME_KW | WIFE_KW | WIFE_CHANGE_KW
            | BIND_KW | BIND_GROUP_KW
            | EXCHANGE_LIST_KW | EXCHANGE_MINE_KW | REDEEM_KW)


async def try_handle_command(text, group_id, user_id, nickname):
    cmd = text.strip()
    if cmd in SIGN_KW:
        return await do_sign(group_id, user_id, nickname)
    if cmd in RANK_KW:
        return await do_rank(group_id, limit=10)
    if cmd in ME_KW:
        return await do_me(group_id, user_id)
    if cmd in WIFE_KW:
        return await do_wife(group_id, user_id, nickname, change=False)
    if cmd in WIFE_CHANGE_KW:
        return await do_wife(group_id, user_id, nickname, change=True)
    # 兑换相关
    for kw in EXCHANGE_LIST_KW:
        if cmd == kw:
            return await do_exchange_list(group_id)
        if cmd.startswith(kw + " "):
            arg = cmd[len(kw):].strip()
            return await do_exchange(group_id, user_id, nickname, arg)
    if cmd in EXCHANGE_MINE_KW:
        return await do_exchange_mine(group_id, user_id)
    for kw in REDEEM_KW:
        if cmd == kw:
            return {"error": "用法：核销 EX123456"}
        if cmd.startswith(kw + " "):
            arg = cmd[len(kw):].strip()
            return await do_redeem(group_id, user_id, nickname, arg)
    # 绑定
    for kw in BIND_KW:
        if cmd.startswith(kw):
            arg = cmd[len(kw):].strip()
            msg = await do_bind_qq(group_id, user_id, arg)
            return {"md": f"> {msg}"}
    for kw in BIND_GROUP_KW:
        if cmd.startswith(kw):
            arg = cmd[len(kw):].strip()
            msg = await do_bind_group(group_id, user_id, arg)
            return {"md": f"> {msg}"}
    return None


# ============================================================
# 事件处理
# ============================================================
async def handle_event(evt):
    t = evt.get("t")
    d = evt.get("d") or {}
    bot_name = CFG.get("persona_name", "")

    if t == "GROUP_MESSAGE_CREATE":
        group_openid = d.get("group_openid", "")
        author = d.get("author") or {}
        user_openid = author.get("member_openid") or author.get("id") or ""
        text = extract_text(d)
        msg_id = d.get("id", "")

        if not group_openid or not user_openid:
            return

        nickname = await resolve_nickname(group_openid, user_openid)
        cmd = normalize_cmd(text, bot_name)
        if cmd and cmd in ALL_CMDS:
            log(f"[命令] {nickname}: {text!r} → {cmd!r}")
            try:
                result = await try_handle_command(cmd, group_openid, user_openid, nickname)
            except Exception as e:
                log(f"[命令处理] 失败: {type(e).__name__}: {e}")
                await send_group_msg(group_openid, f"处理出错：{type(e).__name__}", msg_id)
                return

            if result and "error" in result:
                await send_group_msg(group_openid, result["error"], msg_id)
            elif result and "image_url" in result:
                await send_wife_result(group_openid, result, msg_id)
            elif result and "md" in result:
                await send_md_fallback(group_openid, result["md"], msg_id)
        return

    if t == "GROUP_AT_MESSAGE_CREATE":
        group_openid = d.get("group_openid", "")
        author = d.get("author") or {}
        user_openid = author.get("member_openid") or author.get("id") or ""
        text = extract_text(d)
        msg_id = d.get("id", "")
        nickname = await resolve_nickname(group_openid, user_openid)
        log(f"[群@] group={group_openid[-8:]} user={nickname}: {text[:50]}")

        if not group_openid:
            return

        cmd = normalize_cmd(text, bot_name)
        if cmd and cmd in ALL_CMDS:
            log(f"[命令] {nickname}: {text!r} → {cmd!r}")
            try:
                result = await try_handle_command(cmd, group_openid, user_openid, nickname)
            except Exception as e:
                log(f"[命令处理] 失败: {type(e).__name__}: {e}")
                await send_group_msg(group_openid, f"处理出错：{type(e).__name__}", msg_id)
                return

            if result and "error" in result:
                await send_group_msg(group_openid, result["error"], msg_id)
                return
            if result and "image_url" in result:
                await send_wife_result(group_openid, result, msg_id)
                return
            if result and "md" in result:
                await send_md_fallback(group_openid, result["md"], msg_id)
                return

        if not text:
            text = "在吗"
        reply = await ai_chat(text, mode="group")
        if not reply:
            reply = "AI 暂时无法回复～"
        await send_group_msg(group_openid, reply, msg_id)
        return

    if t == "C2C_MESSAGE_CREATE":
        author = d.get("author") or {}
        openid = author.get("user_openid") or author.get("id") or ""
        text = extract_text(d)
        msg_id = d.get("id", "")
        log(f"[私聊] user={short_nick(openid)}: {text[:50]}")

        if not text:
            text = "在吗"
        reply = await ai_chat(text, mode="private")
        if not reply:
            reply = "AI 暂时无法回复～"
        await send_c2c_msg(openid, reply, msg_id)
        return

    if t == "C2C_MSG_RECEIVE":
        openid = d.get("openid", "")
        log(f"[单聊开关] 用户 {short_nick(openid)} 允许机器人主动发消息")
        return

    if t == "FRIEND_ADD":
        openid = d.get("openid", "")
        scene = d.get("scene", 0)
        log(f"[加好友] 用户 {short_nick(openid)} 添加了机器人 scene={scene}")
        return

    if t == "INTERACTION_CREATE":
        interaction_id = d.get("id", "")
        itype = d.get("type", 0)
        log(f"[互动] type={itype} id={interaction_id[-8:] if interaction_id else '-'}")
        if itype in (11, 12):
            try:
                await respond_interaction(interaction_id, code=0)
            except Exception as e:
                log(f"[互动响应] 异常：{type(e).__name__}: {e}")
        return

    if t == "READY":
        user = d.get("user") or {}
        log(f"[就绪] 机器人已登录：{user.get('username', '')}")
        await asyncio.sleep(2)
        try:
            await set_custom_menu()
        except Exception as e:
            log(f"[菜单] 异常：{type(e).__name__}: {e}")
        try:
            await refresh_all_panels()
        except Exception as e:
            log(f"[面板] 刷新异常：{type(e).__name__}: {e}")
        return

    if t == "GROUP_ADD_ROBOT":
        group_openid = d.get("group_openid", "")
        log(f"[机器人入群] group={group_openid[-8:]}")
        if group_openid:
            try:
                await create_command_panel(group_openid)
            except Exception as e:
                log(f"[面板] 新群创建异常：{type(e).__name__}: {e}")
        return

    if t == "GROUP_DEL_ROBOT":
        group_openid = d.get("group_openid", "")
        log(f"[机器人退群] group={group_openid[-8:]}")
        if group_openid:
            DB.panel_group_remove(group_openid)
        return

    log(f"[事件] {t}")


# ============================================================
# 老婆结果发送（图文合并）
# ============================================================
async def send_wife_result(group_openid, result, msg_id=None):
    t0 = time.time()
    role_name = result.get("role_name", "未知")
    image_url = result.get("image_url")
    is_change = result.get("is_change", False)
    limit = result.get("limit", 3)
    changes = result.get("changes", 0)
    points_spent = result.get("points_spent", 0)
    points_left = result.get("points_left", 0)
    is_cached = result.get("is_cached", False)

    upload_task = None
    if image_url:
        upload_task = asyncio.create_task(upload_group_image(group_openid, image_url))

    intro = result.get("cached_intro", "")
    if not intro and result.get("intro_task"):
        try:
            intro = await result["intro_task"]
        except Exception as e:
            log(f"[老婆] 介绍异常: {type(e).__name__}: {e}")
            intro = ""

    lines = [f"{'换' if is_change else '抽'}老婆成功！今日老婆：{role_name}"]
    if intro:
        lines.append(intro)
    if points_spent > 0:
        lines.append(f"消耗 {points_spent} 积分，剩余 {points_left} 分")
    lines.append(f"今日剩余换老婆次数：{max(0, limit - changes)}/{limit}")
    text = "\n".join(lines)

    sent = False
    if upload_task:
        file_info = await upload_task
        if file_info:
            sent = await send_group_media_with_text(
                group_openid, file_info, content=text, msg_id=msg_id, msg_seq=1)
            log(f"[老婆] 图文合并 {'成功' if sent else '失败'}")
    if not sent:
        await send_group_msg(group_openid, text, msg_id=msg_id)
        log("[老婆] 纯文本发送（图片降级）")

    elapsed = (time.time() - t0) * 1000
    log(f"[老婆] 总耗时 {elapsed:.0f}ms cached={is_cached}")


# ============================================================
# WebSocket
# ============================================================
async def run_once():
    _tk = await TOKEN_MGR.get()
    if not _tk:
        log("[QQ官方] 拿不到 token，跳过")
        return False
    token = await TOKEN_MGR.get()
    if not token:
        log("[QQ官方] 拿不到 token，跳过本次发送")
        return False
    headers = {"Authorization": f"QQBot {token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get("https://api.sgroup.qq.com/gateway", headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"获取 gateway 失败 HTTP {r.status_code}: {r.text[:300]}")
        ws_url = r.json()["url"]
    log(f"获取 gateway: {ws_url}")

    async with websockets.connect(ws_url, max_size=2**24) as ws:
        hello_raw = await asyncio.wait_for(ws.recv(), timeout=15)
        hello = json.loads(hello_raw)
        if hello.get("op") != 10:
            raise RuntimeError(f"预期 op=10，实际：{hello}")
        heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000.0
        log(f"Hello 收到，心跳间隔 {heartbeat_interval}s")

        identify = {
            "op": 2,
            "d": {
                "token": f"QQBot {_tk}",
                "intents": OFFICIAL_INTENTS,
                "properties": {"$os": "Windows", "$browser": "python", "$device": "pc"},
            },
        }
        await ws.send(json.dumps(identify))
        log(f"已发送 Identify，intents={OFFICIAL_INTENTS}")

        seq_holder = {"seq": None}

        async def heartbeater():
            while True:
                await asyncio.sleep(heartbeat_interval)
                try:
                    await ws.send(json.dumps({"op": 1, "d": seq_holder["seq"]}))
                except Exception:
                    return

        hb_task = asyncio.create_task(heartbeater())

        try:
            async for raw in ws:
                try:
                    evt = json.loads(raw)
                except Exception:
                    continue
                op = evt.get("op")
                if op == 0:
                    if "s" in evt:
                        seq_holder["seq"] = evt["s"]
                    try:
                        await handle_event(evt)
                    except Exception as e:
                        log(f"[事件处理异常] {type(e).__name__}: {e}")
                elif op == 11:
                    pass
                elif op == 7:
                    log("服务端要求重连")
                    break
                elif op == 9:
                    log(f"Identify 被拒绝: {evt}")
                    break
        finally:
            hb_task.cancel()
            try:
                await hb_task
            except Exception:
                pass


async def main():
    log(f"启动 QQ 官方机器人  AppID={APP_ID}")
    log(f"数据库：{DB_PATH}")
    log(f"Pillow：{'已加载' if HAS_PIL else '未安装'}")
    log(f"NapCat API：{CFG.get('napcat_api')}")
    log(f"Persona 名：{CFG.get('persona_name') or '(空)'}")
    log(f"兑换项数：{len(_exchange_items())}")
    backoff = 5
    while True:
        try:
            await run_once()
            backoff = 5
        except Exception as e:
            log(f"[连接断开] {type(e).__name__}: {e}")
        log(f"{backoff} 秒后重连...")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("收到 Ctrl+C，退出")