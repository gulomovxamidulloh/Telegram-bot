import os, io, re, time, html, sqlite3, base64, hashlib, logging, asyncio, threading, hmac, secrets, string
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography.fernet import Fernet
from telegram import (Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup,
                      InputMediaPhoto, InputMediaVideo)
from telegram.constants import ParseMode
from telegram.ext import (ApplicationBuilder, ContextTypes, CommandHandler,
                          MessageHandler, CallbackQueryHandler, filters)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# ---------- Sozlamalar (Render > Environment) ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_PASSWORD = os.environ["BOT_PASSWORD"]
VAULT_PASSWORD = os.environ["VAULT_PASSWORD"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
SECRET_KEY = os.environ["SECRET_KEY"]
TZ_OFFSET = float(os.environ.get("TZ_OFFSET", "9"))  # ixtiyoriy, standart GMT+9
LOCAL = timezone(timedelta(hours=TZ_OFFSET))
OWNER_ID = int(os.environ.get("OWNER_ID", "0") or 0)  # ixtiyoriy: egasining Telegram ID raqami
TRASH_DAYS = 7

DB_PATH = "archive.db"
BACKUP_NAME = "archive_backup.db"
AUTO_LOCK = 600
MAX_FAILS = 5
BLOCK_SECONDS = 300
PAGE_SIZE = 5
ALBUM_SIZE = 10
PASS_TTL = 60

fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode()).digest()))

authorized = {}
vault_open = set()
fails = {}
group_buf = {}
LAST_TOUCH = {}
BLOCKED = set()
bg_tasks = set()
dirty = False

# ---------- Matnlar ----------
from texts import T


def t(uid, key, **kw):
    s = T[key]
    return s.format(**kw) if kw else s


# ---------- Yordamchi funksiyalar ----------
def eq(a, b):
    return hmac.compare_digest(a.encode(), b.encode())

def enc(s):
    return fernet.encrypt(s.encode()).decode()

def dec(s):
    try:
        return fernet.decrypt(s.encode()).decode()
    except Exception:
        return "⚠️ ?"

def now_dt():
    return datetime.now(LOCAL)

def now():
    return now_dt().strftime("%Y-%m-%d %H:%M")

def esc(s):
    return html.escape(s or "")

async def safe_delete(msg):
    try:
        await msg.delete()
    except Exception:
        pass

def spawn(coro):
    task = asyncio.create_task(coro)
    bg_tasks.add(task)
    task.add_done_callback(bg_tasks.discard)

def schedule_delete(msg, delay=PASS_TTL):
    async def _d():
        await asyncio.sleep(delay)
        await safe_delete(msg)
    spawn(_d())

def touch(uid):
    if time.time() - LAST_TOUCH.get(uid, 0) > 60:
        LAST_TOUCH[uid] = time.time()
        try:
            run("UPDATE users SET last_seen=? WHERE user_id=?", (now(), uid), mark=False)
        except Exception:
            pass

def is_owner(uid):
    return OWNER_ID != 0 and uid == OWNER_ID

def is_auth(uid):
    ts = authorized.get(uid)
    if ts is None:
        return False
    if time.time() - ts > AUTO_LOCK:
        authorized.pop(uid, None)
        vault_open.discard(uid)
        return False
    authorized[uid] = time.time()
    touch(uid)
    return True

def blocked_for(uid):
    f = fails.get(uid)
    if f and f[1] > time.time():
        return int(f[1] - time.time())
    return 0

def register_fail(uid):
    f = fails.setdefault(uid, [0, 0])
    f[0] += 1
    if f[0] >= MAX_FAILS:
        f[1] = time.time() + BLOCK_SECONDS
        f[0] = 0
        return True
    return False

def parse_when(s):
    s = s.strip().lower()
    n = now_dt()
    res = None
    m = re.fullmatch(r"(\d{1,6})\s*(m|min|h|d)", s)
    if m:
        v, u = int(m.group(1)), m.group(2)
        if u in ("m", "min"):
            res = n + timedelta(minutes=v)
        elif u == "h":
            res = n + timedelta(hours=v)
        else:
            res = n + timedelta(days=min(v, 3650))
    else:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if h > 23 or mi > 59:
                return None
            res = n.replace(hour=h, minute=mi, second=0, microsecond=0)
            if res <= n:
                res += timedelta(days=1)
        else:
            try:
                res = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL)
            except ValueError:
                return None
    if res is None or res <= n:
        return None
    return res

def gen_password(n=16):
    sym = "!@#$%^&*-_"
    alphabet = string.ascii_letters + string.digits + sym
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(n))
        if (any(c.islower() for c in p) and any(c.isupper() for c in p)
                and any(c.isdigit() for c in p) and any(c in sym for c in p)):
            return p


# ---------- Baza ----------
def run(sql, args=(), mark=True):
    global dirty
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, args).fetchall()
        con.commit()
        if mark and not sql.lstrip().upper().startswith(("SELECT", "PRAGMA")):
            dirty = True
        return rows
    finally:
        con.close()

def init_db():
    global dirty
    run("""CREATE TABLE IF NOT EXISTS items(
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, file_id TEXT,
        content TEXT, caption TEXT, folder_id INTEGER, fav INTEGER DEFAULT 0,
        created TEXT, deleted INTEGER DEFAULT 0, deleted_at TEXT,
        place_id INTEGER, fuid TEXT, fname TEXT)""")
    cols = [r["name"] for r in run("PRAGMA table_info(items)")]
    for col, ddl in (("deleted", "INTEGER DEFAULT 0"), ("deleted_at", "TEXT"),
                     ("place_id", "INTEGER"), ("fuid", "TEXT"), ("fname", "TEXT")):
        if col not in cols:
            run(f"ALTER TABLE items ADD COLUMN {col} {ddl}")
    run("CREATE TABLE IF NOT EXISTS folders(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)")
    run("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY, name TEXT, lang TEXT DEFAULT 'uz', first_seen TEXT,
        username TEXT, last_seen TEXT, blocked INTEGER DEFAULT 0)""")
    ucols = [r["name"] for r in run("PRAGMA table_info(users)")]
    for col, ddl in (("username", "TEXT"), ("last_seen", "TEXT"), ("blocked", "INTEGER DEFAULT 0")):
        if col not in ucols:
            run(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    run("""CREATE TABLE IF NOT EXISTS places(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, lat REAL, lon REAL, created TEXT)""")
    run("""CREATE TABLE IF NOT EXISTS reminders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT,
        due INTEGER, done INTEGER DEFAULT 0)""")
    run("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    BLOCKED.clear()
    BLOCKED.update(r["user_id"] for r in run("SELECT user_id FROM users WHERE blocked=1"))
    dirty = False

def save(kind, file_id=None, content=None, caption=None, folder=None, fuid=None, fname=None, place=None):
    run("INSERT INTO items(kind,file_id,content,caption,folder_id,created,fuid,fname,place_id) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (kind, file_id, content, caption, folder, now(), fuid, fname, place))

def where_for(key):
    if key == "trash":
        return "deleted=1 AND kind!='pass'", ()
    if key == "audio":
        return "deleted=0 AND kind IN ('voice','audio','vnote')", ()
    if key in ("photo", "video", "doc", "text", "pass", "contact"):
        return "deleted=0 AND kind=?", (key,)
    if key == "fav":
        return "deleted=0 AND fav=1 AND kind!='pass'", ()
    if key.startswith("m") and re.fullmatch(r"\d{4}-\d{2}", key[1:]):
        return "deleted=0 AND kind!='pass' AND created LIKE ?", (key[1:] + "%",)
    return "deleted=0 AND folder_id=? AND kind!='pass'", (int(key[1:]),)

def fetch(key, page, size=PAGE_SIZE):
    where, args = where_for(key)
    total = run(f"SELECT COUNT(*) c FROM items WHERE {where}", args)[0]["c"]
    rows = run(f"SELECT * FROM items WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
               args + (size, page * size))
    return rows, total

def counts():
    return {r["kind"]: r["c"] for r in run(
        "SELECT kind, COUNT(*) c FROM items WHERE deleted=0 GROUP BY kind")}

def trash_count():
    return run("SELECT COUNT(*) c FROM items WHERE deleted=1 AND kind!='pass'")[0]["c"]

def places_count():
    return run("SELECT COUNT(*) c FROM places")[0]["c"]

def do_search(term):
    q = term.lower().strip()
    n = now_dt()
    prefix = None
    m = re.search(r"\d{4}-\d{2}(?:-\d{2})?", q)
    if m:
        prefix = m.group(0)
        q = q.replace(prefix, " ")
    elif "bugun" in q:
        prefix = n.strftime("%Y-%m-%d")
        q = q.replace("bugun", " ")
    elif "kecha" in q:
        prefix = (n - timedelta(days=1)).strftime("%Y-%m-%d")
        q = q.replace("kecha", " ")
    elif "shu oy" in q:
        prefix = n.strftime("%Y-%m")
        q = q.replace("shu oy", " ")
    words = q.split()
    if not words and not prefix:
        return []
    res = []
    for it in run("SELECT * FROM items WHERE kind!='pass' AND deleted=0 ORDER BY id DESC"):
        if prefix and not (it["created"] or "").startswith(prefix):
            continue
        if it["kind"] in ("text", "contact"):
            hay = dec(it["content"]).replace("|", " ")
        else:
            hay = (it["caption"] or "") + " " + (it["fname"] or "")
        hay = hay.lower()
        if all(w in hay for w in words):
            res.append(it)
            if len(res) >= 10:
                break
    return res


# ---------- Zaxira nusxa ----------
async def restore_backup(bot):
    if os.path.exists(DB_PATH):
        return
    try:
        chat = await bot.get_chat(CHANNEL_ID)
        pm = chat.pinned_message
        if pm and pm.document and pm.document.file_name == BACKUP_NAME:
            f = await bot.get_file(pm.document.file_id)
            await f.download_to_drive(DB_PATH)
            log.info("Baza zaxiradan tiklandi")
    except Exception:
        log.exception("Tiklashda xato")

async def backup(bot, force=False):
    global dirty
    if not os.path.exists(DB_PATH) or not (dirty or force):
        return False
    dirty = False
    try:
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect("tmp_backup.db")
        src.backup(dst)
        dst.close()
        src.close()
        with open("tmp_backup.db", "rb") as fh:
            msg = await bot.send_document(CHANNEL_ID, document=fh, filename=BACKUP_NAME,
                                          caption="💾 Zaxira nusxa " + now())
        try:
            await bot.pin_chat_message(CHANNEL_ID, msg.message_id, disable_notification=True)
        except Exception:
            log.warning("Pin qilib bo'lmadi (botga admin huquqi bering)")
        return True
    except Exception:
        dirty = True
        log.exception("Zaxira xatosi")
        return False


# ---------- Fon vazifalari: eslatmalar, haftalik hisobot, zaxira ----------
async def fire_reminders(bot):
    rows = run("SELECT * FROM reminders WHERE done=0 AND due<=?", (int(time.time()),))
    for r in rows:
        run("UPDATE reminders SET done=1 WHERE id=?", (r["id"],))
        try:
            await bot.send_message(r["user_id"], t(r["user_id"], "rem_fire", text=dec(r["text"])))
        except Exception:
            log.exception("Eslatma yuborilmadi")

async def weekly_report(bot):
    n = now_dt()
    if n.weekday() != 6 or n.hour < 20:
        return
    key = n.strftime("%G-%V")
    r = run("SELECT v FROM meta WHERE k='weekly'")
    if r and r[0]["v"] == key:
        return
    run("INSERT OR REPLACE INTO meta(k,v) VALUES('weekly',?)", (key,))
    since = (n - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    c = {x["kind"]: x["c"] for x in run(
        "SELECT kind, COUNT(*) c FROM items WHERE created>=? AND deleted=0 GROUP BY kind", (since,))}
    total = run("SELECT COUNT(*) c FROM items WHERE deleted=0")[0]["c"]
    week_n = sum(v for k, v in c.items() if k != "pass")
    for u in run("SELECT user_id FROM users"):
        uid = u["user_id"]
        try:
            await bot.send_message(uid, t(uid, "weekly", n=week_n, photo=c.get("photo", 0),
                                          video=c.get("video", 0), doc=c.get("doc", 0),
                                          text=c.get("text", 0), total=total))
        except Exception:
            pass

async def background(app):
    last_backup = time.time()
    last_purge = 0
    while True:
        await asyncio.sleep(30)
        try:
            await fire_reminders(app.bot)
            await weekly_report(app.bot)
            if time.time() - last_backup >= 300:
                last_backup = time.time()
                await backup(app.bot)
            if time.time() - last_purge >= 3600:
                last_purge = time.time()
                cutoff = (now_dt() - timedelta(days=TRASH_DAYS)).strftime("%Y-%m-%d %H:%M")
                run("DELETE FROM items WHERE deleted=1 AND deleted_at<?", (cutoff,))
        except Exception:
            log.exception("Fon vazifasida xato")

async def post_init(app):
    await restore_backup(app.bot)
    init_db()
    spawn(background(app))


# ---------- Render uchun mini veb-server ----------
class Ping(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *a):
        pass

def start_web():
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=HTTPServer(("0.0.0.0", port), Ping).serve_forever,
                     daemon=True).start()


# ---------- Klaviaturalar ----------
def can_send(uid):
    return (not OWNER_ID) or is_owner(uid)

def kb_main(uid):
    c = counts()
    g = lambda k: c.get(k, 0)
    audio_n = g("voice") + g("audio") + g("vnote")
    rows = [
        [Btn(t(uid, "b_photos", n=g("photo")), callback_data="alb:photo:0"),
         Btn(t(uid, "b_videos", n=g("video")), callback_data="list:video:0")],
        [Btn(t(uid, "b_docs", n=g("doc")), callback_data="list:doc:0"),
         Btn(t(uid, "b_texts", n=g("text")), callback_data="list:text:0")],
        [Btn(t(uid, "b_audio", n=audio_n), callback_data="list:audio:0"),
         Btn(t(uid, "b_places", n=places_count()), callback_data="plc:list:0")],
        [Btn(t(uid, "b_contacts", n=g("contact")), callback_data="list:contact:0"),
         Btn(t(uid, "b_favs"), callback_data="list:fav:0")],
        [Btn(t(uid, "b_folders"), callback_data="folders"), Btn(t(uid, "b_search"), callback_data="search")],
        [Btn(t(uid, "b_remind"), callback_data="rem"), Btn(t(uid, "b_months"), callback_data="months")],
        [Btn(t(uid, "b_vault"), callback_data="vault"),
         Btn(t(uid, "b_trash", n=trash_count()), callback_data="list:trash:0")],
        [Btn(t(uid, "b_stats"), callback_data="stats"), Btn(t(uid, "b_export"), callback_data="export")],
        [Btn(t(uid, "b_backup"), callback_data="backup"), Btn(t(uid, "b_logout"), callback_data="logout")],
    ]
    if is_owner(uid):
        rows.append([Btn(t(uid, "b_users"), callback_data="users")])
    elif OWNER_ID:
        rows.append([Btn(t(uid, "b_contact"), callback_data="contact")])
    return Markup(rows)

def back_kb(uid, target="menu"):
    return Markup([[Btn(t(uid, "b_back"), callback_data=target)]])

def menu_kb(uid):
    return Markup([[Btn(t(uid, "b_menu"), callback_data="menu")]])

def place_kb(uid):
    return Markup([[Btn(t(uid, "b_done"), callback_data="plok")]])

def kb_vault(uid):
    return Markup([
        [Btn(t(uid, "v_list"), callback_data="list:pass:0")],
        [Btn(t(uid, "v_add"), callback_data="vault_add"), Btn(t(uid, "v_gen"), callback_data="vault_gen")],
        [Btn(t(uid, "b_menu"), callback_data="menu")],
    ])

def item_kb(uid, it, fav_view=False):
    i = it["id"]
    if it["deleted"]:
        return Markup([[Btn(t(uid, "b_restore"), callback_data=f"rest:{i}"),
                        Btn(t(uid, "b_purge"), callback_data=f"del:{i}")]])
    if fav_view:
        return Markup([[Btn(t(uid, "b_unfav"), callback_data=f"unfav:{i}")]])
    if it["kind"] == "pass":
        return Markup([[Btn(t(uid, "b_del"), callback_data=f"del:{i}"),
                        Btn(t(uid, "b_edit"), callback_data=f"edit:{i}")]])
    fav = t(uid, "fav_del") if it["fav"] else t(uid, "fav_add")
    rows = [[Btn(fav, callback_data=f"fav:{i}"), Btn(t(uid, "b_del"), callback_data=f"del:{i}")]]
    if it["kind"] in ("contact", "vnote"):
        rows.append([Btn(t(uid, "b_move"), callback_data=f"mv:{i}")])
    else:
        rows.append([Btn(t(uid, "b_edit"), callback_data=f"edit:{i}"),
                     Btn(t(uid, "b_move"), callback_data=f"mv:{i}")])
    return Markup(rows)

def back_target(key):
    if key == "pass":
        return "vault"
    if key[:1] == "m" and key[1:2].isdigit():
        return "months"
    if key[:1] == "d" and key[1:].isdigit():
        return "folders"
    return "menu"

def media_count(fid):
    return run("SELECT COUNT(*) c FROM items WHERE folder_id=? AND deleted=0 AND kind IN ('photo','video')",
               (fid,))[0]["c"]

def send_confirm_view(uid, fid, tid):
    r = run("SELECT name FROM users WHERE user_id=?", (tid,))
    who = r[0]["name"] if r else str(tid)
    kb = Markup([[Btn(t(uid, "send_go"), callback_data=f"fgo:{fid}:{tid}"),
                  Btn(t(uid, "b_no"), callback_data="delno")]])
    return t(uid, "send_confirm", n=media_count(fid), who=who), kb

async def show(update, text, kb=None):
    q = update.callback_query
    if q:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            await q.message.reply_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)

async def send_menu(update, context):
    uid = update.effective_user.id
    context.user_data["state"] = None
    context.user_data.pop("place_id", None)
    text = t(uid, "menu_title")
    fid = context.user_data.get("folder")
    if fid:
        r = run("SELECT name FROM folders WHERE id=?", (fid,))
        if r:
            text += "\n\n" + t(uid, "cur_folder", name=r[0]["name"])
    await show(update, text, kb_main(uid))


# ---------- Elementlarni ko'rsatish ----------
async def send_media(bot, chat_id, rows):
    media = [InputMediaPhoto(r["file_id"]) if r["kind"] == "photo" else InputMediaVideo(r["file_id"])
             for r in rows]
    if len(media) == 1:
        r = rows[0]
        if r["kind"] == "photo":
            await bot.send_photo(chat_id=chat_id, photo=r["file_id"], protect_content=True)
        else:
            await bot.send_video(chat_id=chat_id, video=r["file_id"], protect_content=True)
    elif media:
        await bot.send_media_group(chat_id=chat_id, media=media, protect_content=True)

async def send_item(bot, chat_id, uid, it, fav_view=False):
    kb, k = item_kb(uid, it, fav_view), it["kind"]
    cap = ((it["caption"] + "\n") if it["caption"] else "") + "🕒 " + it["created"]
    if k in ("photo", "video", "doc"):
        fn = {"photo": bot.send_photo, "video": bot.send_video, "doc": bot.send_document}[k]
        await fn(chat_id, it["file_id"], caption=cap, reply_markup=kb, protect_content=True)
    elif k == "voice":
        await bot.send_voice(chat_id, it["file_id"], caption=cap, reply_markup=kb, protect_content=True)
    elif k == "audio":
        await bot.send_audio(chat_id, it["file_id"], caption=cap, reply_markup=kb, protect_content=True)
    elif k == "vnote":
        await bot.send_video_note(chat_id, it["file_id"], reply_markup=kb, protect_content=True)
    elif k == "contact":
        first, last, phone = (dec(it["content"]).split("|") + ["", "", ""])[:3]
        await bot.send_contact(chat_id, phone_number=phone, first_name=first or "?",
                               last_name=last or None, reply_markup=kb, protect_content=True)
    elif k == "pass":
        body = esc(dec(it["content"])[:3500])
        msg = await bot.send_message(
            chat_id, f"🔑 <code>{body}</code>\n\n🕒 {it['created']}\n{esc(t(uid, 'pass_note'))}",
            parse_mode=ParseMode.HTML, reply_markup=kb)
        schedule_delete(msg)
    else:
        await bot.send_message(chat_id, f"📝 {dec(it['content'])[:3800]}\n\n🕒 {it['created']}",
                               reply_markup=kb, protect_content=True)

async def show_list(update, context, key, page):
    q = update.callback_query
    uid = q.from_user.id
    if key == "pass" and uid not in vault_open:
        await q.message.reply_text(t(uid, "vault_first"))
        return
    rows, total = fetch(key, page)
    back = back_target(key)
    if not rows:
        await show(update, t(uid, "empty"), back_kb(uid, back))
        return
    for it in rows:
        await send_item(context.bot, q.message.chat_id, uid, it, fav_view=(key == "fav"))
    nav = []
    if page > 0:
        nav.append(Btn(t(uid, "prev"), callback_data=f"list:{key}:{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(Btn(t(uid, "next"), callback_data=f"list:{key}:{page + 1}"))
    kb = [nav] if nav else []
    if key == "photo":
        kb.append([Btn(t(uid, "b_album"), callback_data="alb:photo:0")])
    kb.append([Btn(t(uid, "b_back"), callback_data=back)])
    a = page * PAGE_SIZE + 1
    await q.message.reply_text(t(uid, "page", a=a, b=a + len(rows) - 1, c=total),
                               reply_markup=Markup(kb))

async def show_album(update, context, key, page):
    q = update.callback_query
    uid = q.from_user.id
    rows, total = fetch(key, page, ALBUM_SIZE)
    if not rows:
        await show(update, t(uid, "empty"), back_kb(uid))
        return
    await send_media(context.bot, q.message.chat_id, rows)
    nav = []
    if page > 0:
        nav.append(Btn(t(uid, "prev"), callback_data=f"alb:{key}:{page - 1}"))
    if (page + 1) * ALBUM_SIZE < total:
        nav.append(Btn(t(uid, "next"), callback_data=f"alb:{key}:{page + 1}"))
    kb = [nav] if nav else []
    kb.append([Btn(t(uid, "b_single"), callback_data=f"list:{key}:0")])
    kb.append([Btn(t(uid, "b_back"), callback_data="menu")])
    a = page * ALBUM_SIZE + 1
    await q.message.reply_text(t(uid, "page", a=a, b=a + len(rows) - 1, c=total),
                               reply_markup=Markup(kb))

async def show_place(update, context, pid, page=0):
    q = update.callback_query
    uid = q.from_user.id
    r = run("SELECT * FROM places WHERE id=?", (pid,))
    if not r:
        await show(update, t(uid, "f_missing"), back_kb(uid, "plc:list:0"))
        return
    p = r[0]
    where = "place_id=? AND deleted=0 AND kind IN ('photo','video')"
    total = run(f"SELECT COUNT(*) c FROM items WHERE {where}", (pid,))[0]["c"]
    rows = run(f"SELECT * FROM items WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
               (pid, ALBUM_SIZE, page * ALBUM_SIZE))
    chat = q.message.chat_id
    if page == 0:
        await context.bot.send_location(chat_id=chat, latitude=p["lat"], longitude=p["lon"],
                                        protect_content=True)
    if rows:
        await send_media(context.bot, chat, rows)
    nav = []
    if page > 0:
        nav.append(Btn(t(uid, "prev"), callback_data=f"plc:{pid}:{page - 1}"))
    if (page + 1) * ALBUM_SIZE < total:
        nav.append(Btn(t(uid, "next"), callback_data=f"plc:{pid}:{page + 1}"))
    kb = [nav] if nav else []
    kb.append([Btn(t(uid, "b_plc_add"), callback_data=f"pla:{pid}")])
    kb.append([Btn(t(uid, "b_map"), url=f"https://www.google.com/maps?q={p['lat']},{p['lon']}")])
    kb.append([Btn(t(uid, "b_plc_del"), callback_data=f"pdel:{pid}")])
    kb.append([Btn(t(uid, "b_back"), callback_data="plc:list:0")])
    await q.message.reply_text(t(uid, "plc_info", name=p["name"], created=p["created"], n=total),
                               reply_markup=Markup(kb))


# ---------- Buyruq va xabarlar ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    context.user_data["state"] = None
    context.user_data.pop("place_id", None)
    if uid in BLOCKED:
        await update.message.reply_text(t(uid, "banned"))
        return
    if is_auth(uid):
        await send_menu(update, context)
    else:
        await update.message.reply_text(t(uid, "ask_pw"))

async def alert_others(bot, uid, key, **kw):
    for r in run("SELECT user_id FROM users WHERE user_id!=?", (uid,)):
        try:
            await bot.send_message(r["user_id"], t(r["user_id"], key, **kw))
        except Exception:
            pass

async def after_login(bot, uid, name, username):
    uname = username or "-"
    known = run("SELECT user_id FROM users WHERE user_id=?", (uid,))
    if not known:
        run("INSERT INTO users(user_id,name,lang,first_seen,username,last_seen) VALUES(?,?,?,?,?,?)",
            (uid, name, "uz", now(), uname, now()))
        await alert_others(bot, uid, "alert_new", name=name, username=uname, id=uid)
    else:
        run("UPDATE users SET name=?, username=?, last_seen=? WHERE user_id=?",
            (name, uname, now(), uid), mark=False)

async def group_reply(msg, uid, gid, place):
    last = -1
    while group_buf.get(gid, [0, 0])[0] + group_buf.get(gid, [0, 0])[1] != last:
        cur = group_buf.get(gid, [0, 0])
        last = cur[0] + cur[1]
        await asyncio.sleep(2.5)
    new, dup = group_buf.pop(gid, [1, 0])
    text = t(uid, "saved_dup", n=new, m=dup) if dup else t(uid, "saved_n", n=new)
    await msg.reply_text(text, reply_markup=place_kb(uid) if place else menu_kb(uid))

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = update.effective_user.id
    name = update.effective_user.first_name or "?"
    ud = context.user_data

    if uid in BLOCKED:
        await msg.reply_text(t(uid, "banned"))
        return

    # 1) Asosiy parol
    if not is_auth(uid):
        if not msg.text:
            await msg.reply_text(t(uid, "need_pw"))
            return
        wait = blocked_for(uid)
        if wait:
            await msg.reply_text(t(uid, "blocked", s=wait))
            return
        await safe_delete(msg)
        if eq(msg.text, BOT_PASSWORD):
            fails.pop(uid, None)
            authorized[uid] = time.time()
            await after_login(context.bot, uid, name, update.effective_user.username)
            await msg.reply_text(t(uid, "welcome"))
            await send_menu(update, context)
        else:
            if register_fail(uid):
                await alert_others(context.bot, uid, "alert_block", n=MAX_FAILS, name=name, id=uid)
            await msg.reply_text(t(uid, "wrong"))
        return

    # 2) Holatlar
    state = ud.get("state")
    if state and not msg.text:
        ud["state"] = None
        state = None
    if state:
        ud["state"] = None
        text = msg.text
        if state == "vault_pin":
            wait = blocked_for(uid)
            if wait:
                await msg.reply_text(t(uid, "blocked", s=wait))
                return
            await safe_delete(msg)
            if eq(text, VAULT_PASSWORD):
                fails.pop(uid, None)
                vault_open.add(uid)
                await msg.reply_text(t(uid, "vault_ok"), reply_markup=kb_vault(uid))
            else:
                register_fail(uid)
                await msg.reply_text(t(uid, "pin_wrong"), reply_markup=back_kb(uid))
        elif state == "add_pass":
            if uid not in vault_open:
                return
            save("pass", content=enc(text))
            await safe_delete(msg)
            await msg.reply_text(t(uid, "v_saved"), reply_markup=kb_vault(uid))
        elif state == "gen_label":
            if uid not in vault_open:
                return
            pw = gen_password()
            label = text.strip()[:40]
            save("pass", content=enc(f"{label}: {pw}"))
            out = await msg.reply_text(
                f"{esc(t(uid, 'gen_done'))}\n\n{esc(label)}: <code>{esc(pw)}</code>\n\n{esc(t(uid, 'pass_note'))}",
                parse_mode=ParseMode.HTML, reply_markup=kb_vault(uid))
            schedule_delete(out)
        elif state == "search":
            res = do_search(text)
            if not res:
                await msg.reply_text(t(uid, "search_none"), reply_markup=back_kb(uid))
            else:
                for it in res:
                    await send_item(context.bot, msg.chat_id, uid, it)
                await msg.reply_text(t(uid, "search_found", n=len(res)), reply_markup=back_kb(uid))
        elif state == "new_folder":
            fname = text.strip()[:40]
            run("INSERT INTO folders(name) VALUES(?)", (fname,))
            fid = run("SELECT id FROM folders ORDER BY id DESC LIMIT 1")[0]["id"]
            ud["folder"] = fid
            await msg.reply_text(t(uid, "f_made", name=fname), reply_markup=back_kb(uid, "folders"))
        elif state == "place_name":
            loc = ud.pop("place_loc", None)
            if not loc:
                return
            pname = text.strip()[:60]
            if pname in ("", "-"):
                pname = "Joy " + now()
            run("INSERT INTO places(name,lat,lon,created) VALUES(?,?,?,?)", (pname, loc[0], loc[1], now()))
            pid = run("SELECT id FROM places ORDER BY id DESC LIMIT 1")[0]["id"]
            await msg.reply_text(t(uid, "place_saved", name=pname), reply_markup=Markup([
                [Btn(t(uid, "b_plc_add"), callback_data=f"pla:{pid}")],
                [Btn(t(uid, "b_menu"), callback_data="menu")]]))
        elif state == "send_id":
            if not can_send(uid):
                return
            raw = text.strip()
            if not raw.isdigit():
                ud["state"] = "send_id"
                await msg.reply_text(t(uid, "send_id_bad"))
                return
            txt, kb = send_confirm_view(uid, ud.get("send_fid"), int(raw))
            await msg.reply_text(txt, reply_markup=kb)
        elif state == "rem_text":
            ud["rem_text"] = text[:500]
            ud["state"] = "rem_time"
            await msg.reply_text(t(uid, "rem_time_ask"))
        elif state == "rem_time":
            when = parse_when(text)
            if not when:
                ud["state"] = "rem_time"
                await msg.reply_text(t(uid, "rem_bad"))
                return
            run("INSERT INTO reminders(user_id,text,due) VALUES(?,?,?)",
                (uid, enc(ud.get("rem_text", "")), int(when.timestamp())))
            await msg.reply_text(t(uid, "rem_set", when=when.strftime("%Y-%m-%d %H:%M")),
                                 reply_markup=menu_kb(uid))
        elif state == "msg_to":
            if not is_owner(uid):
                return
            target = ud.get("msg_to")
            try:
                await context.bot.send_message(
                    target, t(target, "owner_msg", text=text),
                    reply_markup=Markup([[Btn(t(target, "b_reply"), callback_data="contact")]]))
                await msg.reply_text(t(uid, "msg_sent"), reply_markup=back_kb(uid, f"usr:{target}"))
            except Exception:
                await msg.reply_text(t(uid, "msg_fail"), reply_markup=back_kb(uid, "users"))
        elif state == "to_owner":
            if not OWNER_ID:
                return
            uname = update.effective_user.username or "-"
            try:
                await context.bot.send_message(
                    OWNER_ID, t(OWNER_ID, "from_user", name=name, username=uname, id=uid, text=text),
                    reply_markup=Markup([[Btn(t(OWNER_ID, "u_write"), callback_data=f"umsg:{uid}")]]))
                await msg.reply_text(t(uid, "reply_sent"), reply_markup=menu_kb(uid))
            except Exception:
                await msg.reply_text(t(uid, "msg_fail"), reply_markup=menu_kb(uid))
        elif state == "edit":
            rows = run("SELECT * FROM items WHERE id=?", (ud.get("edit_id"),))
            if not rows:
                await msg.reply_text(t(uid, "empty"), reply_markup=menu_kb(uid))
                return
            it = rows[0]
            if it["kind"] == "pass":
                run("UPDATE items SET content=? WHERE id=?", (enc(text), it["id"]))
                await safe_delete(msg)
            elif it["kind"] == "text":
                run("UPDATE items SET content=? WHERE id=?", (enc(text), it["id"]))
            else:
                run("UPDATE items SET caption=? WHERE id=?", (text, it["id"]))
            await msg.reply_text(t(uid, "edited"), reply_markup=menu_kb(uid))
        return

    # 3) Joylashuv
    if msg.location or msg.venue:
        loc = msg.venue.location if msg.venue else msg.location
        title = msg.venue.title if msg.venue else None
        if title:
            run("INSERT INTO places(name,lat,lon,created) VALUES(?,?,?,?)",
                (title[:60], loc.latitude, loc.longitude, now()))
            pid = run("SELECT id FROM places ORDER BY id DESC LIMIT 1")[0]["id"]
            await msg.reply_text(t(uid, "place_saved", name=title[:60]), reply_markup=Markup([
                [Btn(t(uid, "b_plc_add"), callback_data=f"pla:{pid}")],
                [Btn(t(uid, "b_menu"), callback_data="menu")]]))
        else:
            ud["place_loc"] = (loc.latitude, loc.longitude)
            ud["state"] = "place_name"
            await msg.reply_text(t(uid, "loc_name_ask"))
        return

    # 4) Kontakt
    folder = ud.get("folder")
    if msg.contact:
        c = msg.contact
        if not c.phone_number:
            await msg.reply_text(t(uid, "unsupported"))
            return
        body = "|".join(x.replace("|", " ") for x in (c.first_name or "?", c.last_name or "", c.phone_number))
        save("contact", content=enc(body), folder=folder)
        await msg.reply_text(t(uid, "saved", what=t(uid, "k_contact")), reply_markup=menu_kb(uid))
        return

    # 5) Oddiy saqlash (bitta yoki ko'p fayl)
    place = ud.get("place_id")
    k = fid = fuid = fname = None
    if msg.photo:
        k, fid, fuid = "photo", msg.photo[-1].file_id, msg.photo[-1].file_unique_id
    elif msg.video:
        k, fid, fuid, fname = "video", msg.video.file_id, msg.video.file_unique_id, msg.video.file_name
    elif msg.document:
        k, fid, fuid, fname = "doc", msg.document.file_id, msg.document.file_unique_id, msg.document.file_name
    elif msg.voice:
        k, fid, fuid = "voice", msg.voice.file_id, msg.voice.file_unique_id
    elif msg.audio:
        k, fid, fuid = "audio", msg.audio.file_id, msg.audio.file_unique_id
        fname = msg.audio.file_name or msg.audio.title
    elif msg.video_note:
        k, fid, fuid = "vnote", msg.video_note.file_id, msg.video_note.file_unique_id

    if k is None:
        if msg.text:
            save("text", content=enc(msg.text), folder=folder)
            await msg.reply_text(t(uid, "saved", what=t(uid, "k_text")), reply_markup=menu_kb(uid))
        else:
            await msg.reply_text(t(uid, "unsupported"))
        return

    dup = run("SELECT id FROM items WHERE fuid=? AND deleted=0 LIMIT 1", (fuid,)) if fuid else []
    if dup:
        if place:
            run("UPDATE items SET place_id=? WHERE id=?", (place, dup[0]["id"]))
            outcome = "attached"
        else:
            outcome = "dup"
    else:
        save(k, file_id=fid, caption=msg.caption, folder=folder, fuid=fuid, fname=fname, place=place)
        outcome = "new"
        try:
            await context.bot.copy_message(CHANNEL_ID, msg.chat_id, msg.message_id)
        except Exception:
            log.exception("Kanalga yuborib bo'lmadi")

    gid = msg.media_group_id
    if gid:
        if gid in group_buf:
            group_buf[gid][0 if outcome == "new" else 1] += 1
        else:
            group_buf[gid] = [1, 0] if outcome == "new" else [0, 1]
            spawn(group_reply(msg, uid, gid, place))
        return
    if outcome == "dup":
        await msg.reply_text(t(uid, "dup_one"), reply_markup=menu_kb(uid))
    elif outcome == "attached":
        await msg.reply_text(t(uid, "dup_attached"), reply_markup=place_kb(uid))
    elif place:
        await msg.reply_text(t(uid, "plc_attached"), reply_markup=place_kb(uid))
    else:
        await msg.reply_text(t(uid, "saved", what=t(uid, "k_" + k)), reply_markup=menu_kb(uid))


# ---------- Tugmalar ----------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data
    ud = context.user_data

    if uid in BLOCKED:
        return
    if not is_auth(uid):
        await q.message.reply_text(t(uid, "session_end"))
        return

    if data == "menu":
        vault_open.discard(uid)
        await send_menu(update, context)

    elif data.startswith("list:"):
        _, key, page = data.split(":")
        await show_list(update, context, key, int(page))

    elif data.startswith("alb:"):
        _, key, page = data.split(":")
        await show_album(update, context, key, int(page))

    elif data.startswith("fav:"):
        iid = int(data[4:])
        run("UPDATE items SET fav = 1 - fav WHERE id=?", (iid,))
        rows = run("SELECT * FROM items WHERE id=?", (iid,))
        if rows:
            await q.message.edit_reply_markup(item_kb(uid, rows[0]))

    elif data.startswith("unfav:"):
        run("UPDATE items SET fav=0 WHERE id=?", (int(data[6:]),))
        await safe_delete(q.message)

    elif data.startswith("del:"):
        iid = int(data[4:])
        rows = run("SELECT kind, deleted FROM items WHERE id=?", (iid,))
        hard = bool(rows) and bool(rows[0]["deleted"] or rows[0]["kind"] == "pass")
        text = t(uid, "del_hard") if hard else t(uid, "del_confirm", d=TRASH_DAYS)
        kb = Markup([[Btn(t(uid, "b_yes"), callback_data=f"delok:{iid}:{q.message.message_id}"),
                      Btn(t(uid, "b_no"), callback_data="delno")]])
        await q.message.reply_text(text, reply_markup=kb)

    elif data == "delno":
        await safe_delete(q.message)

    elif data.startswith("delok:"):
        _, iid, mid = data.split(":")
        rows = run("SELECT kind, deleted FROM items WHERE id=?", (int(iid),))
        if rows:
            if rows[0]["deleted"] or rows[0]["kind"] == "pass":
                run("DELETE FROM items WHERE id=?", (int(iid),))
            else:
                run("UPDATE items SET deleted=1, deleted_at=? WHERE id=?", (now(), int(iid)))
        try:
            await context.bot.delete_message(chat_id=q.message.chat_id, message_id=int(mid))
        except Exception:
            pass
        await safe_delete(q.message)

    elif data.startswith("rest:"):
        run("UPDATE items SET deleted=0, deleted_at=NULL WHERE id=?", (int(data[5:]),))
        await safe_delete(q.message)

    elif data.startswith("edit:"):
        ud["edit_id"] = int(data[5:])
        ud["state"] = "edit"
        await q.message.reply_text(t(uid, "edit_ask"))

    elif data.startswith("mvto:"):
        _, iid, fid = data.split(":")
        run("UPDATE items SET folder_id=? WHERE id=?", (int(fid) or None, int(iid)))
        await show(update, t(uid, "moved"), menu_kb(uid))

    elif data.startswith("mv:"):
        iid = int(data[3:])
        kb = [[Btn(t(uid, "f_none"), callback_data=f"mvto:{iid}:0")]]
        for r in run("SELECT * FROM folders ORDER BY name"):
            kb.append([Btn("📂 " + r["name"], callback_data=f"mvto:{iid}:{r['id']}")])
        await q.message.reply_text(t(uid, "move_title"), reply_markup=Markup(kb))

    elif data == "folders":
        kb = [[Btn("📂 " + r["name"], callback_data=f"dopen:{r['id']}")]
              for r in run("SELECT * FROM folders ORDER BY name")]
        kb.append([Btn(t(uid, "f_new"), callback_data="newfolder"),
                   Btn(t(uid, "f_none"), callback_data="folder_none")])
        kb.append([Btn(t(uid, "b_back"), callback_data="menu")])
        await show(update, t(uid, "folders_title"), Markup(kb))

    elif data.startswith("dopen:"):
        fid = int(data[6:])
        r = run("SELECT name FROM folders WHERE id=?", (fid,))
        if not r:
            await show(update, t(uid, "f_missing"), back_kb(uid, "folders"))
            return
        ud["folder"] = fid
        kb = [[Btn(t(uid, "f_view"), callback_data=f"list:d{fid}:0")]]
        if can_send(uid):
            kb.append([Btn(t(uid, "b_send"), callback_data=f"fsend:{fid}")])
        kb.append([Btn(t(uid, "f_del"), callback_data=f"dfdel:{fid}")])
        kb.append([Btn(t(uid, "b_back"), callback_data="folders")])
        await show(update, t(uid, "f_sel", name=r[0]["name"]), Markup(kb))

    elif data.startswith("dfdel:"):
        fid = int(data[6:])
        run("UPDATE items SET folder_id=NULL WHERE folder_id=?", (fid,))
        run("DELETE FROM folders WHERE id=?", (fid,))
        if ud.get("folder") == fid:
            ud.pop("folder", None)
        await show(update, t(uid, "f_deleted"), back_kb(uid, "folders"))

    elif data == "folder_none":
        ud.pop("folder", None)
        await show(update, t(uid, "f_cleared"), back_kb(uid, "folders"))

    elif data == "newfolder":
        ud["state"] = "new_folder"
        await show(update, t(uid, "f_ask"), back_kb(uid, "folders"))

    elif data.startswith("fsend:"):
        fid = int(data[6:])
        if not can_send(uid):
            await show(update, t(uid, "send_denied"), back_kb(uid))
            return
        if media_count(fid) == 0:
            await show(update, t(uid, "send_none"), back_kb(uid, f"dopen:{fid}"))
            return
        kb = []
        for r in run("SELECT user_id, name, username FROM users WHERE user_id!=? AND blocked=0 "
                     "ORDER BY last_seen DESC LIMIT 20", (uid,)):
            kb.append([Btn(f"👤 {r['name']} · @{r['username'] or '-'}",
                           callback_data=f"fto:{fid}:{r['user_id']}")])
        kb.append([Btn(t(uid, "b_send_id"), callback_data=f"fid:{fid}")])
        kb.append([Btn(t(uid, "b_back"), callback_data=f"dopen:{fid}")])
        await show(update, t(uid, "send_who"), Markup(kb))

    elif data.startswith("fid:"):
        ud["send_fid"] = int(data[4:])
        ud["state"] = "send_id"
        await show(update, t(uid, "send_id_ask"), back_kb(uid, f"fsend:{data[4:]}"))

    elif data.startswith("fto:"):
        _, fid, tid = data.split(":")
        txt, kb = send_confirm_view(uid, int(fid), int(tid))
        await show(update, txt, kb)

    elif data.startswith("fgo:"):
        _, fid, tid = data.split(":")
        fid, tid = int(fid), int(tid)
        if not can_send(uid):
            await show(update, t(uid, "send_denied"), back_kb(uid))
            return
        rows = run("SELECT * FROM items WHERE folder_id=? AND deleted=0 AND kind IN ('photo','video') "
                   "ORDER BY id", (fid,))
        if not rows:
            await show(update, t(uid, "send_none"), back_kb(uid, f"dopen:{fid}"))
            return
        sender = q.from_user.first_name or "?"
        sent = 0
        try:
            await context.bot.send_message(tid, t(tid, "send_header", name=sender, n=len(rows)))
            for i in range(0, len(rows), ALBUM_SIZE):
                chunk = rows[i:i + ALBUM_SIZE]
                await send_media(context.bot, tid, chunk)
                sent += len(chunk)
                await asyncio.sleep(1.5)
        except Exception:
            log.exception("Papkani yuborishda xato")
            extra = f"\n({sent}/{len(rows)})" if sent else ""
            await show(update, t(uid, "send_fail") + extra, back_kb(uid, f"dopen:{fid}"))
            return
        await show(update, t(uid, "send_done", n=sent), back_kb(uid, f"dopen:{fid}"))

    elif data.startswith("plc:"):
        parts = data.split(":")
        if parts[1] == "list":
            page = int(parts[2])
            rows = run("SELECT * FROM places ORDER BY id DESC LIMIT 10 OFFSET ?", (page * 10,))
            total = places_count()
            if not rows:
                await show(update, t(uid, "plc_empty"), back_kb(uid))
                return
            kb = [[Btn("📍 " + r["name"], callback_data=f"plc:{r['id']}:0")] for r in rows]
            nav = []
            if page > 0:
                nav.append(Btn(t(uid, "prev"), callback_data=f"plc:list:{page - 1}"))
            if (page + 1) * 10 < total:
                nav.append(Btn(t(uid, "next"), callback_data=f"plc:list:{page + 1}"))
            if nav:
                kb.append(nav)
            kb.append([Btn(t(uid, "b_back"), callback_data="menu")])
            await show(update, t(uid, "plc_title"), Markup(kb))
        else:
            await show_place(update, context, int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)

    elif data.startswith("pla:"):
        ud["place_id"] = int(data[4:])
        await q.message.reply_text(t(uid, "plc_add_ask"), reply_markup=place_kb(uid))

    elif data == "plok":
        pid = ud.pop("place_id", None)
        n = 0
        if pid:
            n = run("SELECT COUNT(*) c FROM items WHERE place_id=? AND deleted=0", (pid,))[0]["c"]
        await show(update, t(uid, "plc_done", n=n),
                   back_kb(uid, f"plc:{pid}:0") if pid else menu_kb(uid))

    elif data.startswith("pdel:"):
        pid = int(data[5:])
        r = run("SELECT name FROM places WHERE id=?", (pid,))
        if r:
            kb = Markup([[Btn(t(uid, "b_yes"), callback_data=f"pdelok:{pid}"),
                          Btn(t(uid, "b_no"), callback_data="delno")]])
            await q.message.reply_text(t(uid, "plc_del_confirm", name=r[0]["name"]), reply_markup=kb)

    elif data.startswith("pdelok:"):
        pid = int(data[7:])
        run("UPDATE items SET place_id=NULL WHERE place_id=?", (pid,))
        run("DELETE FROM places WHERE id=?", (pid,))
        await show(update, t(uid, "plc_deleted"), back_kb(uid, "plc:list:0"))

    elif data == "months":
        rows = run("SELECT substr(created,1,7) m, COUNT(*) c FROM items "
                   "WHERE deleted=0 AND kind!='pass' GROUP BY m ORDER BY m DESC LIMIT 36")
        rows = [r for r in rows if r["m"]]
        if not rows:
            await show(update, t(uid, "months_none"), back_kb(uid))
            return
        btns = [Btn(f"{r['m']} ({r['c']})", callback_data=f"list:m{r['m']}:0") for r in rows]
        kb = [btns[i:i + 2] for i in range(0, len(btns), 2)]
        kb.append([Btn(t(uid, "b_back"), callback_data="menu")])
        await show(update, t(uid, "months_title"), Markup(kb))

    elif data == "search":
        ud["state"] = "search"
        await show(update, t(uid, "search_ask"), back_kb(uid))

    elif data == "rem":
        kb = Markup([
            [Btn(t(uid, "rem_new"), callback_data="rem_new")],
            [Btn(t(uid, "rem_list"), callback_data="rem_list")],
            [Btn(t(uid, "b_back"), callback_data="menu")],
        ])
        await show(update, t(uid, "rem_title"), kb)

    elif data == "rem_new":
        ud["state"] = "rem_text"
        await show(update, t(uid, "rem_text_ask"), back_kb(uid, "rem"))

    elif data == "rem_list":
        rows = run("SELECT * FROM reminders WHERE user_id=? AND done=0 ORDER BY due", (uid,))
        if not rows:
            await show(update, t(uid, "rem_none"), back_kb(uid, "rem"))
            return
        kb = []
        for r in rows[:20]:
            when = datetime.fromtimestamp(r["due"], LOCAL).strftime("%m-%d %H:%M")
            kb.append([Btn(f"🗑 {when} · {dec(r['text'])[:25]}", callback_data=f"rdel:{r['id']}")])
        kb.append([Btn(t(uid, "b_back"), callback_data="rem")])
        await show(update, t(uid, "rem_list"), Markup(kb))

    elif data.startswith("rdel:"):
        run("DELETE FROM reminders WHERE id=? AND user_id=?", (int(data[5:]), uid))
        await show(update, t(uid, "deleted"), back_kb(uid, "rem_list"))

    elif data == "stats":
        c = counts()
        nf = run("SELECT COUNT(*) c FROM folders")[0]["c"]
        nr = run("SELECT COUNT(*) c FROM reminders WHERE user_id=? AND done=0", (uid,))[0]["c"]
        await show(update, t(uid, "stats", photo=c.get("photo", 0), video=c.get("video", 0),
                             doc=c.get("doc", 0), text=c.get("text", 0),
                             audio=c.get("voice", 0) + c.get("audio", 0) + c.get("vnote", 0),
                             contacts=c.get("contact", 0), places=places_count(),
                             passw=c.get("pass", 0), folders=nf, rem=nr), back_kb(uid))

    elif data == "vault":
        if uid in vault_open:
            await show(update, t(uid, "vault_ok"), kb_vault(uid))
        else:
            wait = blocked_for(uid)
            if wait:
                await show(update, t(uid, "blocked", s=wait), back_kb(uid))
                return
            ud["state"] = "vault_pin"
            await show(update, t(uid, "vault_ask_pin"), back_kb(uid))

    elif data == "vault_add":
        if uid not in vault_open:
            return
        ud["state"] = "add_pass"
        await show(update, t(uid, "v_add_ask"), back_kb(uid, "vault"))

    elif data == "vault_gen":
        if uid not in vault_open:
            return
        ud["state"] = "gen_label"
        await show(update, t(uid, "gen_ask"), back_kb(uid, "vault"))

    elif data == "export":
        kb = Markup([
            [Btn(t(uid, "exp_texts"), callback_data="exp_texts")],
            [Btn(t(uid, "exp_pass"), callback_data="exp_pass")],
            [Btn(t(uid, "b_back"), callback_data="menu")],
        ])
        await show(update, t(uid, "exp_title"), kb)

    elif data in ("exp_texts", "exp_pass"):
        kind = "text" if data == "exp_texts" else "pass"
        if kind == "pass" and uid not in vault_open:
            await q.message.reply_text(t(uid, "vault_first"))
            return
        rows = run("SELECT * FROM items WHERE kind=? AND deleted=0 ORDER BY id", (kind,))
        if not rows:
            await show(update, t(uid, "exp_none"), back_kb(uid, "export"))
            return
        body = "\n\n".join(f"[{r['created']}] {dec(r['content'])}" for r in rows)
        buf = io.BytesIO(body.encode("utf-8"))
        await context.bot.send_document(chat_id=q.message.chat_id, document=buf,
                                        filename=("matnlar.txt" if kind == "text" else "parollar.txt"),
                                        protect_content=True)

    elif data == "backup":
        ok = await backup(context.bot, force=True)
        await show(update, t(uid, "backup_ok" if ok else "backup_fail"), back_kb(uid))

    elif data == "users":
        if not is_owner(uid):
            return
        rows = run("SELECT * FROM users ORDER BY last_seen DESC LIMIT 30")
        if not rows:
            await show(update, t(uid, "users_none"), back_kb(uid))
            return
        kb = []
        for r in rows:
            mark = "🚫 " if r["blocked"] else "👤 "
            kb.append([Btn(f"{mark}{r['name']} · @{r['username'] or '-'}", callback_data=f"usr:{r['user_id']}")])
        kb.append([Btn(t(uid, "b_back"), callback_data="menu")])
        await show(update, t(uid, "users_title"), Markup(kb))

    elif data.startswith("usr:"):
        if not is_owner(uid):
            return
        tid = int(data[4:])
        r = run("SELECT * FROM users WHERE user_id=?", (tid,))
        if not r:
            await show(update, t(uid, "users_none"), back_kb(uid, "users"))
            return
        r = r[0]
        status = t(uid, "st_ban") if r["blocked"] else t(uid, "st_ok")
        info = t(uid, "u_info", name=r["name"], username=r["username"] or "-", id=tid,
                 first=r["first_seen"] or "-", last=r["last_seen"] or "-", status=status)
        rows = [[Btn(t(uid, "u_write"), callback_data=f"umsg:{tid}")]]
        if tid != OWNER_ID:
            rows.append([Btn(t(uid, "u_unblock") if r["blocked"] else t(uid, "u_block"),
                             callback_data=f"ublk:{tid}")])
        rows.append([Btn(t(uid, "b_back"), callback_data="users")])
        await show(update, info, Markup(rows))

    elif data.startswith("umsg:"):
        if not is_owner(uid):
            return
        tid = int(data[5:])
        r = run("SELECT name FROM users WHERE user_id=?", (tid,))
        ud["msg_to"] = tid
        ud["state"] = "msg_to"
        await q.message.reply_text(t(uid, "msg_ask", name=r[0]["name"] if r else str(tid)))

    elif data.startswith("ublk:"):
        if not is_owner(uid):
            return
        tid = int(data[5:])
        if tid == OWNER_ID:
            await show(update, t(uid, "u_self"), back_kb(uid, "users"))
            return
        r = run("SELECT blocked FROM users WHERE user_id=?", (tid,))
        if r and r[0]["blocked"]:
            run("UPDATE users SET blocked=0 WHERE user_id=?", (tid,))
            BLOCKED.discard(tid)
            msgkey = "u_unblocked"
        else:
            run("UPDATE users SET blocked=1 WHERE user_id=?", (tid,))
            BLOCKED.add(tid)
            authorized.pop(tid, None)
            vault_open.discard(tid)
            msgkey = "u_blocked"
        await show(update, t(uid, msgkey), back_kb(uid, f"usr:{tid}"))

    elif data == "contact":
        if not OWNER_ID:
            return
        ud["state"] = "to_owner"
        await q.message.reply_text(t(uid, "reply_ask"))

    elif data == "logout":
        authorized.pop(uid, None)
        vault_open.discard(uid)
        ud.clear()
        await show(update, t(uid, "logged_out"))

async def on_error(update, context):
    log.error("Xato:", exc_info=context.error)


def main():
    import web
    web.start(globals())
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(
        filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)
    print("Bot ishga tushdi...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
