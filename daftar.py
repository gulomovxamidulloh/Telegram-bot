# Shaxsiy daftarlar: xarajat, qarz, hujjat muddatlari, kontaktlar, so'zlar va ZIP.
# Bu fayl bot.py bilan bir papkada turishi kerak. bot.py uni main() ichida ulaydi.
import os, re, time, random, shutil, zipfile, tempfile, logging
from datetime import datetime

log = logging.getLogger("daftar")
G = {}  # bot.py ning global o'zgaruvchilari

CURN = {"som": "so'm", "von": "von"}
CUR = {"som": "som", "so'm": "som", "sum": "som", "uzs": "som",
       "von": "von", "won": "von", "krw": "von", "₩": "von"}
LEIT = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}  # So'z qutilari: necha kundan keyin qayta so'raladi
MB = 1024 * 1024
CATS = [
    ("Oziq-ovqat", ("non", "oziq", "go'sht", "gosht", "market", "bozor", "kafe", "restoran", "ovqat",
                    "choy", "kofe", "coffee", "gs25", "mart", "tushlik", "nonushta")),
    ("Transport", ("taksi", "taxi", "avtobus", "bus", "metro", "benzin", "transport", "poezd", "samolyot", "yo'l")),
    ("Uy", ("ijara", "kommunal", "gaz", "svet", "elektr", "suv", "internet", "kvartira", "rent")),
    ("Kiyim", ("kiyim", "poyabzal", "ko'ylak", "kurtka", "shim")),
    ("Salomatlik", ("dori", "shifoxona", "vrach", "doktor", "apteka", "tish")),
    ("Aloqa", ("telefon", "sim", "aloqa", "tarif")),
]


# ---------- yordamchilar ----------
def R(sql, args=(), mark=True):
    return G["run"](sql, args, mark)

def B(text, cb):
    return G["Btn"](text, callback_data=cb)

def KB(rows):
    return G["Markup"](rows)

def fmt(n):
    return f"{int(n):,}".replace(",", " ")

def money(n, cur):
    return f"{fmt(n)} {CURN.get(cur, cur or '')}".strip()

def today():
    return G["now_dt"]().date()

def enc(s):
    return G["enc"](s)

def dec(s):
    return G["dec"](s)

def parse_amount(tok):
    m = re.fullmatch(r"(\d+)(k|ming|mln)?", tok.lower().replace(",", "").replace("_", ""))
    if not m:
        return None
    n = int(m.group(1))
    if m.group(2) == "mln":
        n *= 1000000
    elif m.group(2):
        n *= 1000
    return n

def merge_groups(toks, i, first):
    """'50 000' kabi bo'linib yozilgan summani birlashtiradi. (summa, keyingi indeks) qaytaradi."""
    a, j = first, i + 1
    if toks[i].isdigit():
        while j < len(toks) and re.fullmatch(r"\d{3}", toks[j]):
            a = a * 1000 + int(toks[j])
            j += 1
    return a, j

def parse_date(tok):
    tok = tok.strip().strip(".,")
    for f in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(tok, f).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None

def parse_expense(text):
    toks = text.replace("\n", " ").split()
    kind, amount, cur, note, i = "out", None, None, [], 0
    while i < len(toks):
        tk = toks[i]
        if tk == "+":
            kind = "in"
            i += 1
            continue
        if amount is None:
            core = tk.lstrip("+-")
            a = parse_amount(core) if core else None
            if a is not None:
                if tk.startswith("+"):
                    kind = "in"
                amount, i = merge_groups(toks[:i] + [core] + toks[i + 1:], i, a)
                continue
        c = CUR.get(tk.lower().strip(",."))
        if c and cur is None:
            cur = c
        else:
            note.append(tk)
        i += 1
    if not amount or amount <= 0:
        return None
    return amount, cur, kind, " ".join(note)

def guess_cat(note, kind):
    if kind == "in":
        return "Daromad"
    low = note.lower()
    for cat, words in CATS:
        if any(w in low for w in words):
            return cat
    return "Boshqa"

def parse_debt(text):
    toks = text.replace("\n", " ").split()
    name, amount, cur, due, note, i = [], None, None, None, [], 0
    while i < len(toks):
        tk = toks[i]
        if amount is None:
            a = parse_amount(tk)
            if a is not None and name:
                amount, i = merge_groups(toks, i, a)
                continue
            name.append(tk)
            i += 1
            continue
        c = CUR.get(tk.lower().strip(",."))
        d = parse_date(tk) if re.search(r"\d", tk) else None
        if c and cur is None:
            cur = c
        elif d and due is None:
            due = d
        else:
            note.append(tk)
        i += 1
    if not name or not amount or amount <= 0:
        return None
    return " ".join(name), amount, cur, due, " ".join(note)


# ---------- baza ----------
def ensure_tables():
    for sql in (
        "CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
        "amount INTEGER, cur TEXT, kind TEXT, cat TEXT, note TEXT, created TEXT)",
        "CREATE TABLE IF NOT EXISTS debts(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, dir TEXT, "
        "who TEXT, amount INTEGER, cur TEXT, due TEXT, note TEXT, paid INTEGER DEFAULT 0, created TEXT)",
        "CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, "
        "expires TEXT, item_id INTEGER, created TEXT)",
        "CREATE TABLE IF NOT EXISTS people(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, "
        "phone TEXT, note TEXT, created TEXT)",
        "CREATE TABLE IF NOT EXISTS words(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, term TEXT, "
        "meaning TEXT, box INTEGER DEFAULT 0, due INTEGER DEFAULT 0, created TEXT)",
    ):
        R(sql, (), False)

def meta_get(k):
    r = R("SELECT v FROM meta WHERE k=?", (k,))
    return r[0]["v"] if r else None

def meta_set(k, v):
    R("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, v))

def user_cur(uid):
    return meta_get(f"dcur:{uid}") or "som"


# ---------- 💳 Xarajatlar ----------
def period_lines(uid, since):
    by = {}
    for r in R("SELECT cur, kind, SUM(amount) s FROM expenses WHERE user_id=? AND created>=? "
               "GROUP BY cur, kind", (uid, since)):
        by.setdefault(r["cur"], {"out": 0, "in": 0})[r["kind"]] = r["s"]
    if not by:
        return "—"
    out = []
    for c, v in by.items():
        bal = v["in"] - v["out"]
        out.append(f"{CURN.get(c, c)}: −{fmt(v['out'])} / +{fmt(v['in'])} (balans {'+' if bal >= 0 else '−'}{fmt(abs(bal))})")
    return "\n".join(out)

def xp_menu_text(uid):
    d = today()
    t0 = d.strftime("%Y-%m-%d 00:00")
    t7 = (d.replace(day=d.day) - __import__("datetime").timedelta(days=6)).strftime("%Y-%m-%d 00:00")
    t30 = d.strftime("%Y-%m-01 00:00")
    return (f"💳 Xarajatlar\n\n📅 Bugun:\n{period_lines(uid, t0)}\n\n🗓 Oxirgi 7 kun:\n{period_lines(uid, t7)}"
            f"\n\n📆 Shu oy:\n{period_lines(uid, t30)}")

def xp_report(uid):
    since = today().strftime("%Y-%m-01 00:00")
    rows = R("SELECT cur, cat, SUM(amount) s FROM expenses WHERE user_id=? AND kind='out' AND created>=? "
             "GROUP BY cur, cat ORDER BY s DESC", (uid, since))
    if not rows:
        return "📊 Shu oyda xarajat yo'q."
    tot = {}
    for r in rows:
        tot[r["cur"]] = tot.get(r["cur"], 0) + r["s"]
    lines = ["📊 Shu oy xarajatlari kategoriya bo'yicha:"]
    for c in tot:
        lines.append(f"\n{CURN.get(c, c)} (jami {fmt(tot[c])}):")
        for r in [x for x in rows if x["cur"] == c][:8]:
            lines.append(f"• {r['cat']}: {fmt(r['s'])} ({round(r['s'] * 100 / tot[c])}%)")
    return "\n".join(lines)

def add_expense(uid, text):
    p = parse_expense(text)
    if not p:
        return None
    amount, cur, kind, note = p
    if cur:
        meta_set(f"dcur:{uid}", cur)
    cur = cur or user_cur(uid)
    cat = guess_cat(note, kind)
    R("INSERT INTO expenses(user_id,amount,cur,kind,cat,note,created) VALUES(?,?,?,?,?,?,?)",
      (uid, amount, cur, kind, cat, enc(note), G["now"]()))
    eid = R("SELECT id FROM expenses WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,))[0]["id"]
    sign = "+" if kind == "in" else "−"
    line = f"✅ {sign}{money(amount, cur)} · {cat}" + (f" · {note}" if note else "")
    return eid, line


# ---------- 💸 Qarzlar ----------
def debt_totals(uid):
    by = {}
    for r in R("SELECT dir, cur, SUM(amount) s FROM debts WHERE user_id=? AND paid=0 GROUP BY dir, cur", (uid,)):
        by.setdefault(r["cur"] or "som", {"lent": 0, "borrowed": 0})[r["dir"]] = r["s"]
    if not by:
        return "Hozircha ochiq qarz yo'q."
    out = []
    for c, v in by.items():
        out.append(f"{CURN.get(c, c)}: menga qarz {fmt(v['lent'])} · men qarzman {fmt(v['borrowed'])}")
    return "\n".join(out)

def debt_label(r):
    arrow = "📤" if r["dir"] == "lent" else "📥"
    return f"{arrow} {dec(r['who'])} · {money(r['amount'], r['cur'])}" + (f" · {r['due']}" if r["due"] else "")


# ---------- 🪪 Hujjatlar ----------
def days_left(expires):
    return (datetime.strptime(expires, "%Y-%m-%d").date() - today()).days

def doc_label(r):
    d = days_left(r["expires"])
    mark = "❗" if d < 0 else ("⚠️" if d <= 30 else "✅")
    return f"{mark} {dec(r['title'])} · {r['expires']} ({d} kun)" if d >= 0 else f"{mark} {dec(r['title'])} · {r['expires']} (muddati o'tgan)"


# ---------- 📇 Kontaktlar ----------
PHONE_RE = re.compile(r"\+?\d[\d\-]{5,}")

def parse_person(text):
    toks = text.replace("\n", " ").split()
    for i, tk in enumerate(toks):
        if i and PHONE_RE.fullmatch(tk):
            return " ".join(toks[:i]), tk, " ".join(toks[i + 1:])
    return None

def people_sorted(uid):
    rows = list(R("SELECT * FROM people WHERE user_id=?", (uid,)))
    rows.sort(key=lambda r: dec(r["name"]).lower())
    return rows


# ---------- 🔤 So'zlar ----------
def parse_words(text):
    out = []
    for line in text.splitlines():
        parts = re.split(r"\s*(?:—|-|=|:)\s*", line.strip(), maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            out.append((parts[0].strip(), parts[1].strip()))
    return out

def word_stats(uid):
    n = R("SELECT COUNT(*) c FROM words WHERE user_id=?", (uid,))[0]["c"]
    due = R("SELECT COUNT(*) c FROM words WHERE user_id=? AND due<=?", (uid, int(time.time())))[0]["c"]
    done = R("SELECT COUNT(*) c FROM words WHERE user_id=? AND box>=5", (uid,))[0]["c"]
    return n, due, done

def next_word(uid):
    r = R("SELECT * FROM words WHERE user_id=? AND due<=? ORDER BY due LIMIT 1", (uid, int(time.time())))
    return r[0] if r else None

def grade(wid, uid, ok):
    r = R("SELECT box FROM words WHERE id=? AND user_id=?", (wid, uid))
    if not r:
        return
    if ok:
        box = min(r[0]["box"] + 1, 5)
        due = int(time.time()) + LEIT[box] * 86400
    else:
        box, due = 0, int(time.time()) + 600
    R("UPDATE words SET box=?, due=? WHERE id=?", (box, due, wid))


# ---------- 📦 ZIP ----------
def ensure_folder(name):
    r = R("SELECT id FROM folders WHERE name=?", (name,))
    if r:
        return r[0]["id"]
    R("INSERT INTO folders(name) VALUES(?)", (name,))
    return R("SELECT id FROM folders WHERE name=?", (name,))[0]["id"]

def unique_name(name, used):
    base, ext = os.path.splitext(os.path.basename(name) or "fayl")
    cand, n = base + ext, 1
    while cand in used:
        n += 1
        cand = f"{base}_{n}{ext}"
    used.add(cand)
    return cand

async def zp_collect(update, context):
    msg, ud = update.message, context.user_data
    files = ud.setdefault("zp_files", [])
    n = len(files) + 1
    if msg.document:
        fid, name, size = msg.document.file_id, msg.document.file_name or f"fayl_{n}", msg.document.file_size
    elif msg.photo:
        p = msg.photo[-1]
        fid, name, size = p.file_id, f"rasm_{n}.jpg", p.file_size
    elif msg.video:
        fid, name, size = msg.video.file_id, msg.video.file_name or f"video_{n}.mp4", msg.video.file_size
    elif msg.audio:
        fid, name, size = msg.audio.file_id, msg.audio.file_name or f"audio_{n}.mp3", msg.audio.file_size
    elif msg.voice:
        fid, name, size = msg.voice.file_id, f"ovoz_{n}.ogg", msg.voice.file_size
    else:
        return False
    if (size or 0) > 20 * MB:
        await msg.reply_text("⚠️ Bu fayl 20 MB dan katta, ZIP'ga qo'shilmadi.")
        return True
    total = sum(f["size"] or 0 for f in files) + (size or 0)
    if total > 45 * MB:
        await msg.reply_text("⚠️ Jami hajm 45 MB dan oshadi. Avval «Tayyor» ni bosing, qolganini keyin yuboring.")
        return True
    files.append({"file_id": fid, "name": name, "size": size or 0})
    gid = msg.media_group_id
    if gid and ud.get("zp_group") == gid:
        return True
    ud["zp_group"] = gid
    await msg.reply_text(f"✅ {len(files)} ta fayl ({total / MB:.1f} MB). Yana yuboring yoki «Tayyor» ni bosing.",
                         reply_markup=KB([[B("✅ Tayyor", "zp:done"), B("❌ Bekor", "zp:cancel")]]))
    return True

async def zp_done(update, context):
    q, ud = update.callback_query, context.user_data
    bot, uid = context.bot, q.from_user.id
    files = ud.get("zp_files") or []
    if not files:
        await G["show"](update, "Hech narsa yuborilmadi.", KB([[B("⬅️ Orqaga", "dm")]]))
        return
    await G["show"](update, f"⏳ {len(files)} ta fayl siqilmoqda...", None)
    tmpdir = tempfile.mkdtemp()
    try:
        zpath, used = os.path.join(tmpdir, "arxiv.zip"), set()
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for i, f in enumerate(files):
                tgf = await bot.get_file(f["file_id"])
                p = os.path.join(tmpdir, f"f{i}.bin")
                await tgf.download_to_drive(p)
                z.write(p, unique_name(f["name"], used))
                os.remove(p)
        zname = "arxiv_" + G["now"]().replace(" ", "_").replace(":", "-") + ".zip"
        with open(zpath, "rb") as fh:
            sent = await bot.send_document(q.message.chat_id, document=fh, filename=zname,
                                           caption=f"📦 {len(files)} ta fayl")
        doc = getattr(sent, "document", None)
        if doc:
            G["save"]("doc", file_id=doc.file_id, caption="📦 ZIP", folder=ensure_folder("Siqilgan"),
                      fuid=doc.file_unique_id, fname=zname)
        ud.pop("zp_files", None)
        ud.pop("dmode", None)
        await q.message.reply_text("✅ ZIP tayyor. U 📂 «Siqilgan» papkasiga ham saqlandi.",
                                   reply_markup=KB([[B("📦 Yana ZIP", "zp:menu"), B("⬅️ Daftarlar", "dm")]]))
    except Exception:
        log.exception("ZIP xatosi")
        await q.message.reply_text("⚠️ ZIP yasab bo'lmadi. Keyinroq urinib ko'ring.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------- kunlik eslatmalar (qarz va hujjat muddatlari) ----------
async def alerts(bot):
    n = G["now_dt"]()
    day = n.strftime("%Y-%m-%d")
    if n.hour < 9 or meta_get("daf_alert") == day:
        return
    meta_set("daf_alert", day)
    lines = {}
    for r in R("SELECT * FROM debts WHERE paid=0 AND due IS NOT NULL"):
        d = days_left(r["due"])
        if d in (0, 3):
            when = "bugun" if d == 0 else "3 kundan keyin"
            lines.setdefault(r["user_id"], []).append(f"💸 Qarz muddati {when}: {debt_label(r)}")
    for r in R("SELECT * FROM docs"):
        d = days_left(r["expires"])
        if d in (0, 1, 7, 30):
            when = "bugun" if d == 0 else f"{d} kundan keyin"
            lines.setdefault(r["user_id"], []).append(f"🪪 {dec(r['title'])} muddati {when} tugaydi ({r['expires']})")
    for uid, ls in lines.items():
        try:
            await bot.send_message(uid, "🔔 Eslatma:\n\n" + "\n".join(ls))
        except Exception:
            log.warning("Eslatma yuborilmadi")


# ---------- menyular va tugmalar ----------
def dm_markup():
    return KB([
        [B("💳 Xarajatlar", "xp:menu"), B("💸 Qarzlar", "db:menu")],
        [B("🪪 Hujjat muddatlari", "dc:menu"), B("📇 Kontakt daftari", "pb:menu")],
        [B("🔤 So'z daftari", "wd:menu"), B("📦 ZIP", "zp:menu")],
        [B("⬅️ Orqaga", "menu")],
    ])

def back(cb="dm"):
    return KB([[B("⬅️ Orqaga", cb)]])

def confirm_kb(yes, no):
    return KB([[B("✅ Ha, o'chirish", yes), B("❌ Yo'q", no)]])

async def route(update, context, data):
    q, ud = update.callback_query, context.user_data
    uid, show = q.from_user.id, G["show"]
    parts = data.split(":")
    area, act = parts[0], (parts[1] if len(parts) > 1 else "")
    arg = parts[2] if len(parts) > 2 else ""
    ud.pop("dmode", None)

    if area == "dm":
        await show(update, "📒 Daftarlar\nNimani ochamiz?", dm_markup())

    # ---- xarajatlar ----
    elif area == "xp":
        if act == "menu":
            await show(update, xp_menu_text(uid), KB([
                [B("➕ Yozish", "xp:add"), B("📋 Oxirgilar", "xp:list")],
                [B("📊 Oy hisoboti", "xp:rep")], [B("⬅️ Orqaga", "dm")]]))
        elif act == "add":
            ud["dmode"] = "xp"
            await show(update, "✏️ Xarajatni yozing, har biri alohida xabarda:\n\n"
                       "50000 som non\n3000 von kofe\n+2000000 som maosh (plyus = kirim)\n\n"
                       "Valyuta yozmasangiz, oxirgi ishlatilgani olinadi. Tugagach «Tayyor» ni bosing.",
                       KB([[B("✅ Tayyor", "xp:menu")]]))
            ud["dmode"] = "xp"
        elif act == "undo":
            R("DELETE FROM expenses WHERE id=? AND user_id=?", (int(arg), uid))
            ud["dmode"] = "xp"
            await show(update, "↩️ Bekor qilindi. Yangisini yozing yoki «Tayyor» ni bosing.",
                       KB([[B("✅ Tayyor", "xp:menu")]]))
        elif act == "list":
            rows = R("SELECT * FROM expenses WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,))
            if not rows:
                await show(update, "Hali yozuv yo'q.", back("xp:menu"))
                return
            kb = []
            for r in rows:
                sign = "+" if r["kind"] == "in" else "−"
                note = dec(r["note"])
                kb.append([B(f"🗑 {r['created'][5:10]} {sign}{money(r['amount'], r['cur'])} {r['cat']}"
                             + (f" · {note[:15]}" if note else ""), f"xp:del:{r['id']}")])
            kb.append([B("⬅️ Orqaga", "xp:menu")])
            await show(update, "📋 Oxirgi 10 ta yozuv (o'chirish uchun bosing):", KB(kb))
        elif act == "del":
            await show(update, "⚠️ Bu yozuv o'chirilsinmi?", confirm_kb(f"xp:delok:{arg}", "xp:list"))
        elif act == "delok":
            R("DELETE FROM expenses WHERE id=? AND user_id=?", (int(arg), uid))
            await show(update, "🗑 O'chirildi.", back("xp:list"))
        elif act == "rep":
            await show(update, xp_report(uid), back("xp:menu"))

    # ---- qarzlar ----
    elif area == "db":
        if act == "menu":
            await show(update, f"💸 Qarzlar\n\n{debt_totals(uid)}", KB([
                [B("➕ Men qarz berdim", "db:addl"), B("➕ Men qarz oldim", "db:addb")],
                [B("📋 Ro'yxat", "db:list")], [B("⬅️ Orqaga", "dm")]]))
        elif act in ("addl", "addb"):
            ud["dmode"] = "db_l" if act == "addl" else "db_b"
            who = "Kimga qarz berdingiz" if act == "addl" else "Kimdan qarz oldingiz"
            await show(update, f"✏️ {who}? Shunday yozing:\n\nAli 500000 som 2026-12-01 izoh\n\n"
                       "(ism, summa, valyuta, qaytarish sanasi va izoh. Sana va izoh shart emas.)",
                       back("db:menu"))
            ud["dmode"] = "db_l" if act == "addl" else "db_b"
        elif act == "list":
            rows = R("SELECT * FROM debts WHERE user_id=? AND paid=0 ORDER BY due IS NULL, due, id", (uid,))
            if not rows:
                await show(update, "Ochiq qarz yo'q.", back("db:menu"))
                return
            kb = [[B(debt_label(r)[:60], f"db:v:{r['id']}")] for r in rows[:30]]
            kb.append([B("⬅️ Orqaga", "db:menu")])
            await show(update, "📋 Ochiq qarzlar (📤 menga qarz, 📥 men qarzman):", KB(kb))
        elif act == "v":
            r = R("SELECT * FROM debts WHERE id=? AND user_id=?", (int(arg), uid))
            if not r:
                await show(update, "Topilmadi.", back("db:list"))
                return
            r = r[0]
            note = dec(r["note"])
            text = debt_label(r) + (f"\n📝 {note}" if note else "") + f"\n📅 Yozilgan: {r['created']}"
            await show(update, text, KB([[B("✅ To'landi", f"db:paid:{r['id']}"), B("🗑 O'chirish", f"db:del:{r['id']}")],
                                         [B("⬅️ Orqaga", "db:list")]]))
        elif act == "paid":
            R("UPDATE debts SET paid=1 WHERE id=? AND user_id=?", (int(arg), uid))
            await show(update, "✅ To'langan deb belgilandi.", back("db:list"))
        elif act == "del":
            await show(update, "⚠️ Bu qarz yozuvi o'chirilsinmi?", confirm_kb(f"db:delok:{arg}", f"db:v:{arg}"))
        elif act == "delok":
            R("DELETE FROM debts WHERE id=? AND user_id=?", (int(arg), uid))
            await show(update, "🗑 O'chirildi.", back("db:list"))

    # ---- hujjat muddatlari ----
    elif area == "dc":
        if act == "menu":
            rows = sorted(R("SELECT * FROM docs WHERE user_id=?", (uid,)), key=lambda r: r["expires"])
            kb = [[B(doc_label(r)[:60], f"dc:v:{r['id']}")] for r in rows[:30]]
            kb.append([B("➕ Qo'shish", "dc:add")])
            kb.append([B("⬅️ Orqaga", "dm")])
            await show(update, "🪪 Hujjat muddatlari" + ("" if rows else "\nHali hujjat yo'q."), KB(kb))
        elif act == "add":
            ud["dmode"] = "dc_add"
            await show(update, "✏️ Hujjat nomi va tugash sanasini yozing:\n\nPasport 2027-03-15\nViza 15.09.2026",
                       back("dc:menu"))
            ud["dmode"] = "dc_add"
        elif act == "v":
            r = R("SELECT * FROM docs WHERE id=? AND user_id=?", (int(arg), uid))
            if not r:
                await show(update, "Topilmadi.", back("dc:menu"))
                return
            r = r[0]
            kb = []
            if r["item_id"]:
                kb.append([B("👁 Rasmni ko'rish", f"dc:img:{r['id']}")])
            kb.append([B("📎 Rasm biriktirish", f"dc:att:{r['id']}"), B("✏️ Sana", f"dc:ed:{r['id']}")])
            kb.append([B("🗑 O'chirish", f"dc:del:{r['id']}")])
            kb.append([B("⬅️ Orqaga", "dc:menu")])
            await show(update, doc_label(r), KB(kb))
        elif act == "att":
            ud["dmode"] = f"dc_att:{arg}"
            await show(update, "📎 Hujjat rasmini yoki faylini yuboring:", back(f"dc:v:{arg}"))
            ud["dmode"] = f"dc_att:{arg}"
        elif act == "img":
            r = R("SELECT item_id FROM docs WHERE id=? AND user_id=?", (int(arg), uid))
            it = R("SELECT * FROM items WHERE id=? AND deleted=0", (r[0]["item_id"],)) if r and r[0]["item_id"] else []
            if it:
                await G["send_item"](context.bot, q.message.chat_id, uid, it[0])
            else:
                await q.message.reply_text("Rasm topilmadi (o'chirilgan bo'lishi mumkin).")
        elif act == "ed":
            ud["dmode"] = f"dc_ed:{arg}"
            await show(update, "✏️ Yangi sanani yozing (masalan 2027-03-15 yoki 15.03.2027):", back(f"dc:v:{arg}"))
            ud["dmode"] = f"dc_ed:{arg}"
        elif act == "del":
            await show(update, "⚠️ Bu hujjat o'chirilsinmi?\n(Biriktirilgan rasm arxivda qoladi.)",
                       confirm_kb(f"dc:delok:{arg}", f"dc:v:{arg}"))
        elif act == "delok":
            R("DELETE FROM docs WHERE id=? AND user_id=?", (int(arg), uid))
            await show(update, "🗑 O'chirildi.", back("dc:menu"))

    # ---- kontakt daftari ----
    elif area == "pb":
        if act == "menu":
            n = R("SELECT COUNT(*) c FROM people WHERE user_id=?", (uid,))[0]["c"]
            await show(update, f"📇 Kontakt daftari: {n} ta", KB([
                [B("➕ Qo'shish", "pb:add"), B("🔍 Qidirish", "pb:find")],
                [B("📋 Hammasi", "pb:list:0")], [B("⬅️ Orqaga", "dm")]]))
        elif act == "add":
            ud["dmode"] = "pb_add"
            await show(update, "✏️ Ism, telefon va izohni yozing:\n\nAli Valiyev +821012345678 qo'shni",
                       back("pb:menu"))
            ud["dmode"] = "pb_add"
        elif act == "find":
            ud["dmode"] = "pb_find"
            await show(update, "🔍 Ism, raqam yoki izohdan bir qismini yozing:", back("pb:menu"))
            ud["dmode"] = "pb_find"
        elif act == "list":
            page = int(arg or 0)
            rows = people_sorted(uid)
            chunk = rows[page * 8:page * 8 + 8]
            if not chunk:
                await show(update, "Hali kontakt yo'q.", back("pb:menu"))
                return
            kb = [[B(f"👤 {dec(r['name'])} · {dec(r['phone'])}"[:60], f"pb:v:{r['id']}")] for r in chunk]
            nav = []
            if page > 0:
                nav.append(B("⬅️", f"pb:list:{page - 1}"))
            if (page + 1) * 8 < len(rows):
                nav.append(B("➡️", f"pb:list:{page + 1}"))
            if nav:
                kb.append(nav)
            kb.append([B("⬅️ Orqaga", "pb:menu")])
            await show(update, f"📋 Kontaktlar ({len(rows)} ta):", KB(kb))
        elif act == "v":
            r = R("SELECT * FROM people WHERE id=? AND user_id=?", (int(arg), uid))
            if not r:
                await show(update, "Topilmadi.", back("pb:menu"))
                return
            r = r[0]
            note = dec(r["note"])
            await show(update, f"👤 {dec(r['name'])}\n📞 {dec(r['phone'])}" + (f"\n📝 {note}" if note else ""),
                       KB([[B("📞 Kartochka yuborish", f"pb:card:{r['id']}")],
                           [B("✏️ Izoh", f"pb:ed:{r['id']}"), B("🗑 O'chirish", f"pb:del:{r['id']}")],
                           [B("⬅️ Orqaga", "pb:list:0")]]))
        elif act == "card":
            r = R("SELECT * FROM people WHERE id=? AND user_id=?", (int(arg), uid))
            if r:
                await context.bot.send_contact(q.message.chat_id, phone_number=dec(r[0]["phone"]),
                                               first_name=dec(r[0]["name"]))
        elif act == "ed":
            ud["dmode"] = f"pb_ed:{arg}"
            await show(update, "✏️ Yangi izohni yozing:", back(f"pb:v:{arg}"))
            ud["dmode"] = f"pb_ed:{arg}"
        elif act == "del":
            await show(update, "⚠️ Bu kontakt o'chirilsinmi?", confirm_kb(f"pb:delok:{arg}", f"pb:v:{arg}"))
        elif act == "delok":
            R("DELETE FROM people WHERE id=? AND user_id=?", (int(arg), uid))
            await show(update, "🗑 O'chirildi.", back("pb:list:0"))

    # ---- so'z daftari ----
    elif area == "wd":
        if act == "menu":
            n, due, done = word_stats(uid)
            await show(update, f"🔤 So'z daftari\n\nJami: {n} ta\nTakrorlash kerak: {due} ta\nO'zlashtirilgan: {done} ta",
                       KB([[B("➕ So'z qo'shish", "wd:add")],
                           [B("🎯 Mashq", "wd:pr"), B("📝 Test", "wd:ts")],
                           [B("📋 Ro'yxat", "wd:list:0")], [B("⬅️ Orqaga", "dm")]]))
        elif act == "add":
            ud["dmode"] = "wd"
            await show(update, "✏️ So'zlarni yozing, har qatorda bittadan:\n\nolma - 사과\nkitob = 책\n\n"
                       "Ko'p qatorni bir xabarda yuborsangiz bo'ladi. Tugagach «Tayyor» ni bosing.",
                       KB([[B("✅ Tayyor", "wd:menu")]]))
            ud["dmode"] = "wd"
        elif act == "pr":
            w = next_word(uid)
            if not w:
                await show(update, "🎉 Hozircha takrorlash kerak so'z yo'q.", back("wd:menu"))
                return
            await show(update, f"🎯 {dec(w['term'])}", KB([[B("👁 Javobni ko'rish", f"wd:sh:{w['id']}")],
                                                         [B("⬅️ Orqaga", "wd:menu")]]))
        elif act == "sh":
            w = R("SELECT * FROM words WHERE id=? AND user_id=?", (int(arg), uid))
            if w:
                await show(update, f"🎯 {dec(w[0]['term'])}\n\n💡 {dec(w[0]['meaning'])}",
                           KB([[B("✅ Bilaman", f"wd:ok:{arg}"), B("❌ Bilmayman", f"wd:no:{arg}")]]))
        elif act in ("ok", "no"):
            grade(int(arg), uid, act == "ok")
            w = next_word(uid)
            if not w:
                await show(update, "🎉 Hozircha takrorlash kerak so'z yo'q.", back("wd:menu"))
                return
            await show(update, f"🎯 {dec(w['term'])}", KB([[B("👁 Javobni ko'rish", f"wd:sh:{w['id']}")],
                                                         [B("⬅️ Orqaga", "wd:menu")]]))
        elif act == "ts":
            rows = list(R("SELECT * FROM words WHERE user_id=?", (uid,)))
            if len(rows) < 4:
                await show(update, "📝 Test uchun kamida 4 ta so'z kerak.", back("wd:menu"))
                return
            w = next_word(uid) or random.choice(rows)
            others = random.sample([r for r in rows if r["id"] != w["id"]], 3)
            opts = others + [w]
            random.shuffle(opts)
            ud["wd_q"] = {"id": w["id"], "opts": [o["id"] for o in opts]}
            kb = [[B(dec(o["meaning"])[:50], f"wd:an:{i}")] for i, o in enumerate(opts)]
            kb.append([B("⬅️ Orqaga", "wd:menu")])
            await show(update, f"📝 «{dec(w['term'])}» nima degani?", KB(kb))
        elif act == "an":
            tq = ud.pop("wd_q", None)
            if not tq:
                await show(update, "Test tugagan. Yangisini boshlang.", back("wd:menu"))
                return
            chosen = tq["opts"][int(arg)] if arg.isdigit() and int(arg) < len(tq["opts"]) else None
            ok = chosen == tq["id"]
            grade(tq["id"], uid, ok)
            w = R("SELECT * FROM words WHERE id=?", (tq["id"],))[0]
            await show(update, ("✅ To'g'ri!" if ok else "❌ Noto'g'ri.") + f"\n\n{dec(w['term'])} — {dec(w['meaning'])}",
                       KB([[B("➡️ Keyingisi", "wd:ts")], [B("⬅️ Orqaga", "wd:menu")]]))
        elif act == "list":
            page = int(arg or 0)
            rows = R("SELECT * FROM words WHERE user_id=? ORDER BY id DESC LIMIT 10 OFFSET ?", (uid, page * 10))
            total = R("SELECT COUNT(*) c FROM words WHERE user_id=?", (uid,))[0]["c"]
            if not rows:
                await show(update, "Hali so'z yo'q.", back("wd:menu"))
                return
            kb = [[B(f"{dec(r['term'])} — {dec(r['meaning'])}"[:60], f"wd:v:{r['id']}")] for r in rows]
            nav = []
            if page > 0:
                nav.append(B("⬅️", f"wd:list:{page - 1}"))
            if (page + 1) * 10 < total:
                nav.append(B("➡️", f"wd:list:{page + 1}"))
            if nav:
                kb.append(nav)
            kb.append([B("⬅️ Orqaga", "wd:menu")])
            await show(update, f"📋 So'zlar ({total} ta):", KB(kb))
        elif act == "v":
            w = R("SELECT * FROM words WHERE id=? AND user_id=?", (int(arg), uid))
            if w:
                await show(update, f"{dec(w[0]['term'])} — {dec(w[0]['meaning'])}\n📦 Quti: {w[0]['box']}/5",
                           KB([[B("🗑 O'chirish", f"wd:del:{arg}")], [B("⬅️ Orqaga", "wd:list:0")]]))
        elif act == "del":
            await show(update, "⚠️ Bu so'z o'chirilsinmi?", confirm_kb(f"wd:delok:{arg}", f"wd:v:{arg}"))
        elif act == "delok":
            R("DELETE FROM words WHERE id=? AND user_id=?", (int(arg), uid))
            await show(update, "🗑 O'chirildi.", back("wd:list:0"))

    # ---- ZIP ----
    elif area == "zp":
        if act == "menu":
            ud["dmode"] = "zp"
            ud["zp_files"] = []
            await show(update, "📦 ZIP\n\nRasm, video, fayl yoki audio yuboring. Hammasi bitta ZIP bo'ladi.\n"
                       "Har fayl 20 MB gacha, jami 45 MB gacha.\nTugagach «Tayyor» ni bosing.",
                       KB([[B("✅ Tayyor", "zp:done"), B("❌ Bekor", "zp:cancel")]]))
            ud["dmode"] = "zp"
        elif act == "done":
            await zp_done(update, context)
        elif act == "cancel":
            ud.pop("zp_files", None)
            await show(update, "❌ Bekor qilindi.", back("dm"))


# ---------- yozilgan xabarlar ----------
async def handle_mode(update, context, mode, orig_message):
    msg, ud = update.message, context.user_data
    uid = update.effective_user.id
    text = msg.text

    if mode == "zp":
        return await zp_collect(update, context)

    if mode.startswith("dc_att:"):
        if not (msg.photo or msg.document):
            return False
        did = int(mode.split(":")[1])
        fuid = msg.photo[-1].file_unique_id if msg.photo else msg.document.file_unique_id
        ud.pop("dmode", None)
        await orig_message(update, context)  # arxivga saqlaydi (ikkilanmasdan)
        it = R("SELECT id FROM items WHERE fuid=? AND deleted=0 ORDER BY id DESC LIMIT 1", (fuid,))
        if it:
            R("UPDATE docs SET item_id=? WHERE id=? AND user_id=?", (it[0]["id"], did, uid))
            await msg.reply_text("📎 Hujjatga biriktirildi.", reply_markup=KB([[B("🪪 Hujjatga qaytish", f"dc:v:{did}")]]))
        return True

    if text is None:
        return False

    if mode == "xp":
        res = add_expense(uid, text)
        if not res:
            await msg.reply_text("❌ Summani raqam bilan yozing. Masalan: 50000 som non")
        else:
            await msg.reply_text(res[1], reply_markup=KB([[B("↩️ Bekor qilish", f"xp:undo:{res[0]}"),
                                                           B("✅ Tayyor", "xp:menu")]]))
        return True

    if mode in ("db_l", "db_b"):
        p = parse_debt(text)
        if not p:
            await msg.reply_text("❌ Ism va summani yozing. Masalan: Ali 500000 som 2026-12-01")
            return True
        who, amount, cur, due, note = p
        if cur:
            meta_set(f"dcur:{uid}", cur)
        R("INSERT INTO debts(user_id,dir,who,amount,cur,due,note,created) VALUES(?,?,?,?,?,?,?,?)",
          (uid, "lent" if mode == "db_l" else "borrowed", enc(who), amount, cur or user_cur(uid), due,
           enc(note), G["now"]()))
        ud.pop("dmode", None)
        await msg.reply_text(f"✅ Saqlandi: {who} · {money(amount, cur or user_cur(uid))}" + (f" · {due}" if due else ""),
                             reply_markup=KB([[B("📋 Ro'yxat", "db:list"), B("⬅️ Qarzlar", "db:menu")]]))
        return True

    if mode == "dc_add" or mode.startswith("dc_ed:"):
        toks = text.split()
        d = parse_date(toks[-1]) if toks else None
        if not d or (mode == "dc_add" and len(toks) < 2):
            await msg.reply_text("❌ Sanani yozing. Masalan: Pasport 2027-03-15")
            return True
        ud.pop("dmode", None)
        if mode == "dc_add":
            R("INSERT INTO docs(user_id,title,expires,created) VALUES(?,?,?,?)",
              (uid, enc(" ".join(toks[:-1])), d, G["now"]()))
            did = R("SELECT id FROM docs WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,))[0]["id"]
            await msg.reply_text(f"✅ Saqlandi: {' '.join(toks[:-1])} · {d}",
                                 reply_markup=KB([[B("📎 Rasm biriktirish", f"dc:att:{did}")],
                                                  [B("📋 Hujjatlar", "dc:menu")]]))
        else:
            did = int(mode.split(":")[1])
            R("UPDATE docs SET expires=? WHERE id=? AND user_id=?", (d, did, uid))
            await msg.reply_text(f"✅ Yangi sana: {d}", reply_markup=KB([[B("🪪 Hujjatga qaytish", f"dc:v:{did}")]]))
        return True

    if mode == "pb_add":
        p = parse_person(text)
        if not p:
            await msg.reply_text("❌ Ism va telefon raqamini yozing. Masalan: Ali Valiyev +821012345678 qo'shni")
            return True
        R("INSERT INTO people(user_id,name,phone,note,created) VALUES(?,?,?,?,?)",
          (uid, enc(p[0]), enc(p[1]), enc(p[2]), G["now"]()))
        ud.pop("dmode", None)
        await msg.reply_text(f"✅ Saqlandi: {p[0]} · {p[1]}", reply_markup=KB([[B("📇 Daftar", "pb:menu")]]))
        return True

    if mode == "pb_find":
        ud.pop("dmode", None)
        qy = text.strip().lower()
        res = [r for r in people_sorted(uid)
               if qy in (dec(r["name"]) + " " + dec(r["phone"]) + " " + dec(r["note"])).lower()][:10]
        if not res:
            await msg.reply_text("Hech narsa topilmadi.", reply_markup=back("pb:menu"))
        else:
            kb = [[B(f"👤 {dec(r['name'])} · {dec(r['phone'])}"[:60], f"pb:v:{r['id']}")] for r in res]
            kb.append([B("⬅️ Orqaga", "pb:menu")])
            await msg.reply_text(f"🔍 Topildi: {len(res)} ta", reply_markup=KB(kb))
        return True

    if mode.startswith("pb_ed:"):
        did = int(mode.split(":")[1])
        R("UPDATE people SET note=? WHERE id=? AND user_id=?", (enc(text.strip()), did, uid))
        ud.pop("dmode", None)
        await msg.reply_text("✅ Izoh yangilandi.", reply_markup=KB([[B("👤 Kontaktga qaytish", f"pb:v:{did}")]]))
        return True

    if mode == "wd":
        pairs = parse_words(text)
        if not pairs:
            await msg.reply_text("❌ Shunday yozing: olma - 사과 (har qatorda bittadan)")
            return True
        have = {dec(r["term"]).lower() for r in R("SELECT term FROM words WHERE user_id=?", (uid,))}
        added = 0
        for term, meaning in pairs:
            if term.lower() in have:
                continue
            have.add(term.lower())
            R("INSERT INTO words(user_id,term,meaning,box,due,created) VALUES(?,?,?,0,0,?)",
              (uid, enc(term), enc(meaning), G["now"]()))
            added += 1
        await msg.reply_text(f"✅ Qo'shildi: {added} ta" + (f" (takrorlari o'tkazib yuborildi: {len(pairs) - added})" if added < len(pairs) else ""),
                             reply_markup=KB([[B("✅ Tayyor", "wd:menu")]]))
        return True

    return False


# ---------- botga ulash ----------
def setup(g):
    """bot.py ning main() idan chaqiriladi: menyu, tugmalar va fon vazifalariga ulanadi."""
    G.clear()
    G.update(g)
    orig_init, orig_kb, orig_button = g["init_db"], g["kb_main"], g["on_button"]
    orig_message, orig_start, orig_fire = g["on_message"], g["start"], g["fire_reminders"]

    def init_db():
        orig_init()
        ensure_tables()

    def kb_main(uid):
        rows = [list(r) for r in orig_kb(uid).inline_keyboard]
        rows.append([B("📒 Daftarlar", "dm")])
        return KB(rows)

    async def on_button(update, context):
        data = update.callback_query.data or ""
        if not (data == "dm" or data[:3] in ("xp:", "db:", "dc:", "pb:", "wd:", "zp:")):
            context.user_data.pop("dmode", None)
            return await orig_button(update, context)
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if uid in g["BLOCKED"]:
            return
        if not g["is_auth"](uid):
            await q.message.reply_text("🔒 Sessiya tugadi. Parolni yuboring.")
            return
        await route(update, context, data)

    async def on_message(update, context):
        msg, mode = update.message, context.user_data.get("dmode")
        if mode and msg is not None:
            uid = update.effective_user.id
            if uid not in g["BLOCKED"] and g["is_auth"](uid):
                try:
                    if await handle_mode(update, context, mode, orig_message):
                        return
                except Exception:
                    log.exception("Daftar xatosi")
                    await msg.reply_text("⚠️ Xatolik yuz berdi. Qaytadan urinib ko'ring.")
                    return
        await orig_message(update, context)

    async def start(update, context):
        context.user_data.pop("dmode", None)
        await orig_start(update, context)

    async def fire_reminders(bot):
        await orig_fire(bot)
        try:
            await alerts(bot)
        except Exception:
            log.exception("Daftar eslatmalari xatosi")

    g.update(init_db=init_db, kb_main=kb_main, on_button=on_button, on_message=on_message,
             start=start, fire_reminders=fire_reminders)
