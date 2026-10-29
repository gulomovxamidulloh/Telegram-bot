import os, time, sqlite3, base64, hashlib, logging, asyncio, threading, hmac
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography.fernet import Fernet
from telegram import Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup
from telegram.ext import (ApplicationBuilder, ContextTypes, CommandHandler,
                          MessageHandler, CallbackQueryHandler, filters)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    level=logging.INFO)
log = logging.getLogger("bot")

# ---------- Sozlamalar (Render > Environment dan o'qiladi) ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_PASSWORD = os.environ["BOT_PASSWORD"]
VAULT_PASSWORD = os.environ["VAULT_PASSWORD"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
SECRET_KEY = os.environ["SECRET_KEY"]  # shifrlash kaliti, hech qachon o'zgartirmang

DB_PATH = "archive.db"
BACKUP_NAME = "archive_backup.db"
AUTO_LOCK = 600       # 10 daqiqa harakatsiz bo'lsa qulflanadi
MAX_FAILS = 5         # xato urinishlar soni
BLOCK_SECONDS = 300   # bloklash vaqti
PAGE_SIZE = 5

fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode()).digest()))

authorized = {}   # user_id -> oxirgi faollik vaqti
vault_open = set()
fails = {}        # user_id -> [xatolar, bloklanish_vaqti]
dirty = False     # bazada zaxiralanmagan o'zgarish bormi


# ---------- Yordamchi funksiyalar ----------
def eq(a, b):
    return hmac.compare_digest(a.encode(), b.encode())

def enc(s):
    return fernet.encrypt(s.encode()).decode()

def dec(s):
    try:
        return fernet.decrypt(s.encode()).decode()
    except Exception:
        return "⚠️ O'qib bo'lmadi"

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

async def safe_delete(msg):
    try:
        await msg.delete()
    except Exception:
        pass

def is_auth(uid):
    t = authorized.get(uid)
    if t is None:
        return False
    if time.time() - t > AUTO_LOCK:
        authorized.pop(uid, None)
        vault_open.discard(uid)
        return False
    authorized[uid] = time.time()
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


# ---------- Baza ----------
def run(sql, args=()):
    global dirty
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, args).fetchall()
        con.commit()
        if not sql.lstrip().upper().startswith("SELECT"):
            dirty = True
        return rows
    finally:
        con.close()

def init_db():
    global dirty
    run("""CREATE TABLE IF NOT EXISTS items(
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, file_id TEXT,
        content TEXT, caption TEXT, folder_id INTEGER, fav INTEGER DEFAULT 0, created TEXT)""")
    run("CREATE TABLE IF NOT EXISTS folders(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)")
    dirty = False

def save(kind, file_id=None, content=None, caption=None, folder=None):
    run("INSERT INTO items(kind,file_id,content,caption,folder_id,created) VALUES(?,?,?,?,?,?)",
        (kind, file_id, content, caption, folder, now()))

def fetch(key, page):
    if key in ("photo", "video", "doc", "text", "pass"):
        where, args = "kind=?", (key,)
    elif key == "fav":
        where, args = "fav=1 AND kind!='pass'", ()
    else:  # d<id> = papka
        where, args = "folder_id=? AND kind!='pass'", (int(key[1:]),)
    total = run(f"SELECT COUNT(*) c FROM items WHERE {where}", args)[0]["c"]
    rows = run(f"SELECT * FROM items WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
               args + (PAGE_SIZE, page * PAGE_SIZE))
    return rows, total


# ---------- Zaxira nusxa (Render fayllarni o'chirib yuboradi) ----------
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

async def backup_loop(app):
    while True:
        await asyncio.sleep(300)
        await backup(app.bot)

async def post_init(app):
    await restore_backup(app.bot)
    init_db()
    app.bot_data["backup_task"] = asyncio.create_task(backup_loop(app))


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


# ---------- Menyular ----------
def main_kb():
    return Markup([
        [Btn("🖼 Rasmlar", callback_data="list:photo:0"), Btn("🎥 Videolar", callback_data="list:video:0")],
        [Btn("📁 Fayllar", callback_data="list:doc:0"), Btn("📝 Matnlar", callback_data="list:text:0")],
        [Btn("⭐ Sevimlilar", callback_data="list:fav:0"), Btn("📂 Papkalar", callback_data="folders")],
        [Btn("🔍 Qidiruv", callback_data="search"), Btn("📊 Statistika", callback_data="stats")],
        [Btn("🔐 Parollar ombori", callback_data="vault")],
        [Btn("💾 Zaxira", callback_data="backup"), Btn("🚪 Chiqish", callback_data="logout")],
    ])

def back_kb(target="menu"):
    return Markup([[Btn("⬅️ Orqaga", callback_data=target)]])

def vault_kb():
    return Markup([
        [Btn("🔑 Parollarni ko'rish", callback_data="list:pass:0")],
        [Btn("➕ Yangi parol/eslatma", callback_data="vault_add")],
        [Btn("⬅️ Menyu", callback_data="menu")],
    ])

def item_kb(it):
    fav = "⭐ Sevimlidan chiqarish" if it["fav"] else "☆ Sevimli"
    return Markup([[Btn(fav, callback_data=f"fav:{it['id']}"),
                    Btn("🗑 O'chirish", callback_data=f"del:{it['id']}")]])

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
    context.user_data["state"] = None
    text = "📂 Asosiy menyu\nKerakli bo'limni tanlang:"
    fid = context.user_data.get("folder")
    if fid:
        r = run("SELECT name FROM folders WHERE id=?", (fid,))
        if r:
            text += f"\n\n📂 Joriy papka: {r[0]['name']}"
    await show(update, text, main_kb())


# ---------- Elementlarni ko'rsatish ----------
def cap_of(it):
    c = it["caption"] or ""
    return (c + "\n" if c else "") + "🕒 " + it["created"]

async def send_item(bot, chat_id, it):
    kb, k = item_kb(it), it["kind"]
    if k == "photo":
        await bot.send_photo(chat_id, it["file_id"], caption=cap_of(it), reply_markup=kb, protect_content=True)
    elif k == "video":
        await bot.send_video(chat_id, it["file_id"], caption=cap_of(it), reply_markup=kb, protect_content=True)
    elif k == "doc":
        await bot.send_document(chat_id, it["file_id"], caption=cap_of(it), reply_markup=kb, protect_content=True)
    else:
        icon = "🔑" if k == "pass" else "📝"
        text = dec(it["content"])[:3800]
        await bot.send_message(chat_id, f"{icon} {text}\n\n🕒 {it['created']}",
                               reply_markup=kb, protect_content=True)

async def show_list(update, context, key, page):
    q = update.callback_query
    uid = q.from_user.id
    if key == "pass" and uid not in vault_open:
        await q.message.reply_text("🔐 Avval Parollar omborini oching.")
        return
    rows, total = fetch(key, page)
    if not rows:
        await show(update, "Bu bo'limda hozircha hech narsa yo'q.",
                   back_kb("vault" if key == "pass" else "menu"))
        return
    for it in rows:
        await send_item(context.bot, q.message.chat_id, it)
    nav = []
    if page > 0:
        nav.append(Btn("⬅️ Oldingi", callback_data=f"list:{key}:{page-1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(Btn("Keyingi ➡️", callback_data=f"list:{key}:{page+1}"))
    kb = [nav] if nav else []
    kb.append([Btn("⬅️ Orqaga", callback_data="vault" if key == "pass" else "menu")])
    a = page * PAGE_SIZE + 1
    await q.message.reply_text(f"📄 {a}–{a+len(rows)-1} / {total}", reply_markup=Markup(kb))

def do_search(term):
    term = term.lower()
    res = []
    for it in run("SELECT * FROM items WHERE kind!='pass' ORDER BY id DESC"):
        hay = (it["caption"] or "") if it["kind"] in ("photo", "video", "doc") else dec(it["content"])
        if term in hay.lower():
            res.append(it)
            if len(res) >= 10:
                break
    return res


# ---------- Buyruq va xabarlar ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = None
    if is_auth(update.effective_user.id):
        await send_menu(update, context)
    else:
        await update.message.reply_text("🔒 Botdan foydalanish uchun maxfiy parolni yuboring:")

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = update.effective_user.id

    # 1) Asosiy parol
    if not is_auth(uid):
        if not msg.text:
            await msg.reply_text("🔒 Avval parolni yuboring.")
            return
        wait = blocked_for(uid)
        if wait:
            await msg.reply_text(f"⏳ Juda ko'p xato urinish. {wait} soniyadan keyin urinib ko'ring.")
            return
        await safe_delete(msg)  # parol chatda qolmasin
        if eq(msg.text, BOT_PASSWORD):
            fails.pop(uid, None)
            authorized[uid] = time.time()
            await msg.reply_text("✅ Xush kelibsiz!")
            await send_menu(update, context)
        else:
            register_fail(uid)
            await msg.reply_text("❌ Noto'g'ri parol.")
        return

    # 2) Holatlar (PIN, qidiruv, papka nomi, parol qo'shish)
    state = context.user_data.get("state")
    if state and msg.text:
        context.user_data["state"] = None
        if state == "vault_pin":
            wait = blocked_for(uid)
            if wait:
                await msg.reply_text(f"⏳ {wait} soniyadan keyin urinib ko'ring.")
                return
            await safe_delete(msg)
            if eq(msg.text, VAULT_PASSWORD):
                fails.pop(uid, None)
                vault_open.add(uid)
                await msg.reply_text("🔐 Parollar ombori ochildi.", reply_markup=vault_kb())
            else:
                register_fail(uid)
                await msg.reply_text("❌ PIN xato.", reply_markup=back_kb())
        elif state == "add_pass":
            if uid not in vault_open:
                return
            save("pass", content=enc(msg.text))
            await safe_delete(msg)  # parol chatda qolmasin
            await msg.reply_text("✅ Omborga saqlandi (shifrlangan).", reply_markup=vault_kb())
        elif state == "search":
            res = do_search(msg.text)
            if not res:
                await msg.reply_text("Hech narsa topilmadi.", reply_markup=back_kb())
            else:
                for it in res:
                    await send_item(context.bot, msg.chat_id, it)
                await msg.reply_text(f"🔍 Topildi: {len(res)} ta", reply_markup=back_kb())
        elif state == "new_folder":
            name = msg.text.strip()[:40]
            run("INSERT INTO folders(name) VALUES(?)", (name,))
            fid = run("SELECT id FROM folders ORDER BY id DESC LIMIT 1")[0]["id"]
            context.user_data["folder"] = fid
            await msg.reply_text(f"📂 «{name}» papkasi yaratildi va tanlandi.", reply_markup=back_kb("folders"))
        return

    # 3) Oddiy saqlash
    folder = context.user_data.get("folder")
    if msg.photo:
        save("photo", file_id=msg.photo[-1].file_id, caption=msg.caption, folder=folder)
        label = "🖼 Rasm"
    elif msg.video:
        save("video", file_id=msg.video.file_id, caption=msg.caption, folder=folder)
        label = "🎥 Video"
    elif msg.document:
        save("doc", file_id=msg.document.file_id, caption=msg.caption, folder=folder)
        label = "📁 Fayl"
    elif msg.text:
        save("text", content=enc(msg.text), folder=folder)
        label = "📝 Matn"
    else:
        await msg.reply_text("Bu turdagi xabar qo'llab-quvvatlanmaydi.")
        return

    if label != "📝 Matn":  # fayllarni kanalga ham nusxalaymiz (matn kanalga chiqmaydi)
        try:
            await context.bot.copy_message(CHANNEL_ID, msg.chat_id, msg.message_id)
        except Exception:
            log.exception("Kanalga yuborib bo'lmadi")
    await msg.reply_text(f"✅ {label} saqlandi.",
                         reply_markup=Markup([[Btn("📂 Menyu", callback_data="menu")]]))


# ---------- Tugmalar ----------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    if not is_auth(uid):
        await q.message.reply_text("🔒 Sessiya tugadi. Parolni yuboring.")
        return

    if data == "menu":
        vault_open.discard(uid)
        await send_menu(update, context)

    elif data.startswith("list:"):
        _, key, page = data.split(":")
        await show_list(update, context, key, int(page))

    elif data.startswith("fav:"):
        iid = int(data[4:])
        run("UPDATE items SET fav = 1 - fav WHERE id=?", (iid,))
        rows = run("SELECT * FROM items WHERE id=?", (iid,))
        if rows:
            await q.message.edit_reply_markup(item_kb(rows[0]))

    elif data.startswith("del:"):
        run("DELETE FROM items WHERE id=?", (int(data[4:]),))
        try:
            await q.message.delete()
        except Exception:
            await q.message.reply_text("🗑 O'chirildi.")

    elif data == "folders":
        rows = run("SELECT * FROM folders ORDER BY name")
        kb = [[Btn("📂 " + r["name"], callback_data=f"dopen:{r['id']}")] for r in rows]
        kb.append([Btn("➕ Yangi papka", callback_data="newfolder"),
                   Btn("🚫 Papkasiz", callback_data="folder_none")])
        kb.append([Btn("⬅️ Orqaga", callback_data="menu")])
        await show(update, "📂 Papkalar\nTanlangan papkaga yangi fayllar tushadi.", Markup(kb))

    elif data.startswith("dopen:"):
        fid = int(data[6:])
        r = run("SELECT name FROM folders WHERE id=?", (fid,))
        if not r:
            await show(update, "Papka topilmadi.", back_kb("folders"))
            return
        context.user_data["folder"] = fid
        kb = Markup([
            [Btn("👁 Ichidagilarni ko'rish", callback_data=f"list:d{fid}:0")],
            [Btn("🗑 Papkani o'chirish", callback_data=f"dfdel:{fid}")],
            [Btn("⬅️ Orqaga", callback_data="folders")],
        ])
        await show(update, f"📂 «{r[0]['name']}» tanlandi.\nEndi yuborgan narsalaringiz shu papkaga tushadi.", kb)

    elif data.startswith("dfdel:"):
        fid = int(data[6:])
        run("UPDATE items SET folder_id=NULL WHERE folder_id=?", (fid,))
        run("DELETE FROM folders WHERE id=?", (fid,))
        if context.user_data.get("folder") == fid:
            context.user_data.pop("folder", None)
        await show(update, "🗑 Papka o'chirildi (ichidagilar saqlanib qoldi).", back_kb("folders"))

    elif data == "folder_none":
        context.user_data.pop("folder", None)
        await show(update, "🚫 Papka tanlanmagan.", back_kb("folders"))

    elif data == "newfolder":
        context.user_data["state"] = "new_folder"
        await show(update, "✏️ Yangi papka nomini yozing:", back_kb("folders"))

    elif data == "search":
        context.user_data["state"] = "search"
        await show(update, "🔍 Qidirayotgan so'zingizni yozing\n(izoh va matnlar ichidan izlanadi):", back_kb())

    elif data == "stats":
        c = {r["kind"]: r["c"] for r in run("SELECT kind, COUNT(*) c FROM items GROUP BY kind")}
        nf = run("SELECT COUNT(*) c FROM folders")[0]["c"]
        text = (f"📊 Statistika\n\n🖼 Rasmlar: {c.get('photo', 0)}\n🎥 Videolar: {c.get('video', 0)}\n"
                f"📁 Fayllar: {c.get('doc', 0)}\n📝 Matnlar: {c.get('text', 0)}\n"
                f"🔑 Parollar: {c.get('pass', 0)}\n📂 Papkalar: {nf}")
        await show(update, text, back_kb())

    elif data == "vault":
        if uid in vault_open:
            await show(update, "🔐 Parollar ombori", vault_kb())
        else:
            wait = blocked_for(uid)
            if wait:
                await show(update, f"⏳ {wait} soniyadan keyin urinib ko'ring.", back_kb())
                return
            context.user_data["state"] = "vault_pin"
            await show(update, "🔐 Parollar ombori yashirin.\nPIN-kodni yozing:", back_kb())

    elif data == "vault_add":
        if uid not in vault_open:
            return
        context.user_data["state"] = "add_pass"
        await show(update, "✏️ Saqlamoqchi bo'lgan parol yoki eslatmani yozing\n(yuborganingizdan keyin chatdan o'chiriladi):",
                   back_kb("vault"))

    elif data == "backup":
        ok = await backup(context.bot, force=True)
        await show(update, "💾 Zaxira nusxa kanalga yuborildi." if ok else "⚠️ Zaxira qilib bo'lmadi.", back_kb())

    elif data == "logout":
        authorized.pop(uid, None)
        vault_open.discard(uid)
        context.user_data.clear()
        await show(update, "🔒 Chiqdingiz. Qaytadan kirish uchun /start ni bosing.")

async def on_error(update, context):
    log.error("Xato:", exc_info=context.error)


def main():
    start_web()
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
