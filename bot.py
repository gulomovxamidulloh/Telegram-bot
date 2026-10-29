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
LANG = {}
group_buf = {}
bg_tasks = set()
dirty = False

# ---------- Tillar ----------
T = {
"uz": {
 "menu_title": "📂 Asosiy menyu\nKerakli bo'limni tanlang:",
 "cur_folder": "📂 Joriy papka: {name}",
 "b_photos": "🖼 Rasmlar ({n})", "b_videos": "🎥 Videolar ({n})",
 "b_docs": "📁 Fayllar ({n})", "b_texts": "📝 Matnlar ({n})",
 "b_favs": "⭐ Sevimlilar", "b_tags": "🏷 Teglar", "b_folders": "📂 Papkalar",
 "b_search": "🔍 Qidiruv", "b_remind": "⏰ Eslatmalar", "b_stats": "📊 Statistika",
 "b_vault": "🔐 Parollar ombori", "b_export": "📤 Eksport", "b_backup": "💾 Zaxira",
 "b_lang": "🌐 Til", "b_logout": "🚪 Chiqish", "b_back": "⬅️ Orqaga", "b_menu": "📂 Menyu",
 "ask_pw": "🔒 Botdan foydalanish uchun maxfiy parolni yuboring:",
 "need_pw": "🔒 Avval parolni yuboring.",
 "blocked": "⏳ Juda ko'p xato urinish. {s} soniyadan keyin urinib ko'ring.",
 "welcome": "✅ Xush kelibsiz!", "wrong": "❌ Noto'g'ri parol.",
 "session_end": "🔒 Sessiya tugadi. Parolni yuboring.",
 "logged_out": "🔒 Chiqdingiz. Qaytadan kirish uchun /start ni bosing.",
 "saved": "✅ Saqlandi: {what}", "saved_n": "✅ {n} ta fayl saqlandi.",
 "k_photo": "🖼 Rasm", "k_video": "🎥 Video", "k_doc": "📁 Fayl", "k_text": "📝 Matn",
 "unsupported": "Bu turdagi xabar qo'llab-quvvatlanmaydi.",
 "empty": "Bu bo'limda hozircha hech narsa yo'q.", "page": "📄 {a}–{b} / {c}",
 "prev": "⬅️ Oldingi", "next": "Keyingi ➡️",
 "b_del": "🗑 O'chirish", "b_edit": "✏️ Tahrir", "b_move": "📂 Ko'chirish",
 "fav_add": "☆ Sevimli", "fav_del": "⭐ Chiqarish",
 "b_album": "🖼 Albom ko'rinishi", "b_single": "🗂 Alohida ko'rish",
 "pass_note": "🕒 Xabar 60 soniyadan keyin o'chadi. Nusxalash uchun matnni bosing.",
 "vault_ask_pin": "🔐 Parollar ombori yashirin.\nPIN-kodni yozing:",
 "pin_wrong": "❌ PIN xato.", "vault_ok": "🔐 Parollar ombori ochildi.",
 "vault_first": "🔐 Avval Parollar omborini oching.",
 "v_list": "🔑 Parollarni ko'rish", "v_add": "➕ Yangi parol", "v_gen": "🎲 Parol yaratish",
 "v_add_ask": "✏️ Parol yoki eslatmani yozing\n(chatdan o'chiriladi):",
 "v_saved": "✅ Omborga saqlandi (shifrlangan).",
 "gen_ask": "🎲 Qaysi sayt yoki ilova uchun? Nomini yozing:",
 "gen_done": "🎲 Parol yaratildi va omborga saqlandi:",
 "search_ask": "🔍 Qidirayotgan so'zingizni yozing\n(izoh, matn va teglar ichidan):",
 "search_none": "Hech narsa topilmadi.", "search_found": "🔍 Topildi: {n} ta",
 "folders_title": "📂 Papkalar\nTanlangan papkaga yangi fayllar tushadi.",
 "f_new": "➕ Yangi papka", "f_none": "🚫 Papkasiz", "f_ask": "✏️ Yangi papka nomini yozing:",
 "f_made": "📂 «{name}» papkasi yaratildi va tanlandi.",
 "f_sel": "📂 «{name}» tanlandi.\nEndi yuborganlaringiz shu papkaga tushadi.",
 "f_view": "👁 Ichidagilarni ko'rish", "f_del": "🗑 Papkani o'chirish",
 "f_deleted": "🗑 Papka o'chirildi (ichidagilar saqlanib qoldi).",
 "f_cleared": "🚫 Papka tanlanmagan.", "f_missing": "Papka topilmadi.",
 "tags_title": "🏷 Teglar\nMatn yoki izohga #teg yozing, shu yerda chiqadi.",
 "tags_none": "Hali teg yo'q. Matn yoki izohga #ish kabi teg yozing.",
 "rem_title": "⏰ Eslatmalar", "rem_new": "➕ Yangi eslatma", "rem_list": "📋 Eslatmalarim",
 "rem_text_ask": "✏️ Nimani eslatay? Matnni yozing:",
 "rem_time_ask": "⏰ Qachon eslatay?\n\nMisollar:\n30m — 30 daqiqadan keyin\n2h — 2 soatdan keyin\n1d — 1 kundan keyin\n18:30 — bugun/ertaga 18:30\n2026-11-05 09:00",
 "rem_bad": "❌ Vaqt tushunarsiz. Masalan: 30m, 2h, 18:30",
 "rem_set": "✅ Eslatma qo'yildi: {when}", "rem_fire": "⏰ Eslatma:\n\n{text}",
 "rem_none": "Hozircha eslatma yo'q.",
 "stats": "📊 Statistika\n\n🖼 Rasmlar: {photo}\n🎥 Videolar: {video}\n📁 Fayllar: {doc}\n📝 Matnlar: {text}\n🔑 Parollar: {passw}\n📂 Papkalar: {folders}\n⏰ Eslatmalar: {rem}",
 "weekly": "📅 Haftalik hisobot\n\nShu hafta saqlandi: {n} ta\n🖼 {photo} · 🎥 {video} · 📁 {doc} · 📝 {text}\n\nArxivda jami: {total} ta",
 "exp_title": "📤 Eksport\nNimani yuklab olasiz?", "exp_texts": "📝 Matnlar (TXT)",
 "exp_pass": "🔑 Parollar (TXT)", "exp_none": "Eksport qilish uchun hech narsa yo'q.",
 "backup_ok": "💾 Zaxira nusxa kanalga yuborildi.", "backup_fail": "⚠️ Zaxira qilib bo'lmadi.",
 "lang_title": "🌐 Tilni tanlang:",
 "alert_new": "🔔 Botga yangi akkauntdan kirildi: {name} (id {id})",
 "alert_block": "⚠️ Kimdir {n} marta xato parol kiritdi: {name} (id {id})",
 "edit_ask": "✏️ Yangi matn yoki izohni yozing:", "edited": "✅ Yangilandi.",
 "move_title": "📂 Qaysi papkaga ko'chiramiz?", "moved": "✅ Ko'chirildi.",
 "deleted": "🗑 O'chirildi.",
},
"ru": {
 "menu_title": "📂 Главное меню\nВыберите раздел:",
 "cur_folder": "📂 Текущая папка: {name}",
 "b_photos": "🖼 Фото ({n})", "b_videos": "🎥 Видео ({n})",
 "b_docs": "📁 Файлы ({n})", "b_texts": "📝 Тексты ({n})",
 "b_favs": "⭐ Избранное", "b_tags": "🏷 Теги", "b_folders": "📂 Папки",
 "b_search": "🔍 Поиск", "b_remind": "⏰ Напоминания", "b_stats": "📊 Статистика",
 "b_vault": "🔐 Хранилище паролей", "b_export": "📤 Экспорт", "b_backup": "💾 Резерв",
 "b_lang": "🌐 Язык", "b_logout": "🚪 Выйти", "b_back": "⬅️ Назад", "b_menu": "📂 Меню",
 "ask_pw": "🔒 Отправьте секретный пароль, чтобы пользоваться ботом:",
 "need_pw": "🔒 Сначала отправьте пароль.",
 "blocked": "⏳ Слишком много неверных попыток. Повторите через {s} сек.",
 "welcome": "✅ Добро пожаловать!", "wrong": "❌ Неверный пароль.",
 "session_end": "🔒 Сессия истекла. Отправьте пароль.",
 "logged_out": "🔒 Вы вышли. Чтобы войти снова, нажмите /start.",
 "saved": "✅ Сохранено: {what}", "saved_n": "✅ Сохранено файлов: {n}.",
 "k_photo": "🖼 Фото", "k_video": "🎥 Видео", "k_doc": "📁 Файл", "k_text": "📝 Текст",
 "unsupported": "Этот тип сообщений не поддерживается.",
 "empty": "В этом разделе пока ничего нет.", "page": "📄 {a}–{b} / {c}",
 "prev": "⬅️ Пред.", "next": "След. ➡️",
 "b_del": "🗑 Удалить", "b_edit": "✏️ Изменить", "b_move": "📂 Перенести",
 "fav_add": "☆ В избранное", "fav_del": "⭐ Убрать",
 "b_album": "🖼 Альбомом", "b_single": "🗂 По одному",
 "pass_note": "🕒 Сообщение удалится через 60 секунд. Нажмите на текст, чтобы скопировать.",
 "vault_ask_pin": "🔐 Хранилище скрыто.\nВведите PIN-код:",
 "pin_wrong": "❌ Неверный PIN.", "vault_ok": "🔐 Хранилище открыто.",
 "vault_first": "🔐 Сначала откройте хранилище паролей.",
 "v_list": "🔑 Показать пароли", "v_add": "➕ Новый пароль", "v_gen": "🎲 Создать пароль",
 "v_add_ask": "✏️ Напишите пароль или заметку\n(сообщение будет удалено из чата):",
 "v_saved": "✅ Сохранено в хранилище (зашифровано).",
 "gen_ask": "🎲 Для какого сайта или приложения? Напишите название:",
 "gen_done": "🎲 Пароль создан и сохранён в хранилище:",
 "search_ask": "🔍 Напишите, что искать\n(по подписям, текстам и тегам):",
 "search_none": "Ничего не найдено.", "search_found": "🔍 Найдено: {n}",
 "folders_title": "📂 Папки\nНовые файлы попадут в выбранную папку.",
 "f_new": "➕ Новая папка", "f_none": "🚫 Без папки", "f_ask": "✏️ Напишите название новой папки:",
 "f_made": "📂 Папка «{name}» создана и выбрана.",
 "f_sel": "📂 Выбрана «{name}».\nТеперь всё, что вы отправите, попадёт в эту папку.",
 "f_view": "👁 Посмотреть содержимое", "f_del": "🗑 Удалить папку",
 "f_deleted": "🗑 Папка удалена (содержимое сохранено).",
 "f_cleared": "🚫 Папка не выбрана.", "f_missing": "Папка не найдена.",
 "tags_title": "🏷 Теги\nНапишите #тег в тексте или подписи — он появится здесь.",
 "tags_none": "Тегов пока нет. Добавьте, например, #работа в текст или подпись.",
 "rem_title": "⏰ Напоминания", "rem_new": "➕ Новое напоминание", "rem_list": "📋 Мои напоминания",
 "rem_text_ask": "✏️ О чём напомнить? Напишите текст:",
 "rem_time_ask": "⏰ Когда напомнить?\n\nПримеры:\n30m — через 30 минут\n2h — через 2 часа\n1d — через сутки\n18:30 — сегодня/завтра в 18:30\n2026-11-05 09:00",
 "rem_bad": "❌ Не понял время. Например: 30m, 2h, 18:30",
 "rem_set": "✅ Напоминание установлено: {when}", "rem_fire": "⏰ Напоминание:\n\n{text}",
 "rem_none": "Напоминаний пока нет.",
 "stats": "📊 Статистика\n\n🖼 Фото: {photo}\n🎥 Видео: {video}\n📁 Файлы: {doc}\n📝 Тексты: {text}\n🔑 Пароли: {passw}\n📂 Папки: {folders}\n⏰ Напоминания: {rem}",
 "weekly": "📅 Недельный отчёт\n\nЗа неделю сохранено: {n}\n🖼 {photo} · 🎥 {video} · 📁 {doc} · 📝 {text}\n\nВсего в архиве: {total}",
 "exp_title": "📤 Экспорт\nЧто выгрузить?", "exp_texts": "📝 Тексты (TXT)",
 "exp_pass": "🔑 Пароли (TXT)", "exp_none": "Нечего экспортировать.",
 "backup_ok": "💾 Резервная копия отправлена в канал.", "backup_fail": "⚠️ Не удалось сделать резервную копию.",
 "lang_title": "🌐 Выберите язык:",
 "alert_new": "🔔 В бот вошли с нового аккаунта: {name} (id {id})",
 "alert_block": "⚠️ Кто-то ввёл неверный пароль {n} раз: {name} (id {id})",
 "edit_ask": "✏️ Напишите новый текст или подпись:", "edited": "✅ Обновлено.",
 "move_title": "📂 В какую папку перенести?", "moved": "✅ Перенесено.",
 "deleted": "🗑 Удалено.",
},
"ko": {
 "menu_title": "📂 메인 메뉴\n항목을 선택하세요:",
 "cur_folder": "📂 현재 폴더: {name}",
 "b_photos": "🖼 사진 ({n})", "b_videos": "🎥 동영상 ({n})",
 "b_docs": "📁 파일 ({n})", "b_texts": "📝 메모 ({n})",
 "b_favs": "⭐ 즐겨찾기", "b_tags": "🏷 태그", "b_folders": "📂 폴더",
 "b_search": "🔍 검색", "b_remind": "⏰ 알림", "b_stats": "📊 통계",
 "b_vault": "🔐 비밀번호 보관함", "b_export": "📤 내보내기", "b_backup": "💾 백업",
 "b_lang": "🌐 언어", "b_logout": "🚪 나가기", "b_back": "⬅️ 뒤로", "b_menu": "📂 메뉴",
 "ask_pw": "🔒 봇을 사용하려면 비밀번호를 보내주세요:",
 "need_pw": "🔒 먼저 비밀번호를 보내주세요.",
 "blocked": "⏳ 잘못된 시도가 너무 많습니다. {s}초 후에 다시 시도하세요.",
 "welcome": "✅ 환영합니다!", "wrong": "❌ 비밀번호가 틀렸습니다.",
 "session_end": "🔒 세션이 만료되었습니다. 비밀번호를 보내주세요.",
 "logged_out": "🔒 로그아웃했습니다. 다시 들어가려면 /start 를 누르세요.",
 "saved": "✅ 저장됨: {what}", "saved_n": "✅ 파일 {n}개 저장됨.",
 "k_photo": "🖼 사진", "k_video": "🎥 동영상", "k_doc": "📁 파일", "k_text": "📝 메모",
 "unsupported": "지원하지 않는 메시지 형식입니다.",
 "empty": "이 항목에는 아직 아무것도 없습니다.", "page": "📄 {a}–{b} / {c}",
 "prev": "⬅️ 이전", "next": "다음 ➡️",
 "b_del": "🗑 삭제", "b_edit": "✏️ 수정", "b_move": "📂 이동",
 "fav_add": "☆ 즐겨찾기", "fav_del": "⭐ 해제",
 "b_album": "🖼 앨범으로 보기", "b_single": "🗂 하나씩 보기",
 "pass_note": "🕒 이 메시지는 60초 후 삭제됩니다. 글자를 누르면 복사됩니다.",
 "vault_ask_pin": "🔐 보관함이 잠겨 있습니다.\nPIN 코드를 입력하세요:",
 "pin_wrong": "❌ PIN이 틀렸습니다.", "vault_ok": "🔐 보관함이 열렸습니다.",
 "vault_first": "🔐 먼저 비밀번호 보관함을 여세요.",
 "v_list": "🔑 비밀번호 보기", "v_add": "➕ 새 비밀번호", "v_gen": "🎲 비밀번호 만들기",
 "v_add_ask": "✏️ 비밀번호나 메모를 입력하세요\n(채팅에서 삭제됩니다):",
 "v_saved": "✅ 보관함에 저장했습니다 (암호화됨).",
 "gen_ask": "🎲 어떤 사이트/앱용인가요? 이름을 입력하세요:",
 "gen_done": "🎲 비밀번호를 만들어 보관함에 저장했습니다:",
 "search_ask": "🔍 찾을 단어를 입력하세요\n(설명, 메모, 태그에서 검색):",
 "search_none": "찾은 항목이 없습니다.", "search_found": "🔍 검색 결과: {n}개",
 "folders_title": "📂 폴더\n선택한 폴더에 새 파일이 저장됩니다.",
 "f_new": "➕ 새 폴더", "f_none": "🚫 폴더 없음", "f_ask": "✏️ 새 폴더 이름을 입력하세요:",
 "f_made": "📂 «{name}» 폴더를 만들고 선택했습니다.",
 "f_sel": "📂 «{name}» 선택됨.\n이제 보내는 내용이 이 폴더에 저장됩니다.",
 "f_view": "👁 내용 보기", "f_del": "🗑 폴더 삭제",
 "f_deleted": "🗑 폴더를 삭제했습니다 (내용은 유지됨).",
 "f_cleared": "🚫 선택된 폴더가 없습니다.", "f_missing": "폴더를 찾을 수 없습니다.",
 "tags_title": "🏷 태그\n메모나 설명에 #태그 를 쓰면 여기에 나타납니다.",
 "tags_none": "아직 태그가 없습니다. 메모나 설명에 #업무 처럼 써 보세요.",
 "rem_title": "⏰ 알림", "rem_new": "➕ 새 알림", "rem_list": "📋 내 알림",
 "rem_text_ask": "✏️ 무엇을 알려드릴까요? 내용을 입력하세요:",
 "rem_time_ask": "⏰ 언제 알려드릴까요?\n\n예시:\n30m — 30분 후\n2h — 2시간 후\n1d — 하루 후\n18:30 — 오늘/내일 18:30\n2026-11-05 09:00",
 "rem_bad": "❌ 시간을 이해하지 못했습니다. 예: 30m, 2h, 18:30",
 "rem_set": "✅ 알림을 설정했습니다: {when}", "rem_fire": "⏰ 알림:\n\n{text}",
 "rem_none": "아직 알림이 없습니다.",
 "stats": "📊 통계\n\n🖼 사진: {photo}\n🎥 동영상: {video}\n📁 파일: {doc}\n📝 메모: {text}\n🔑 비밀번호: {passw}\n📂 폴더: {folders}\n⏰ 알림: {rem}",
 "weekly": "📅 주간 리포트\n\n이번 주 저장: {n}개\n🖼 {photo} · 🎥 {video} · 📁 {doc} · 📝 {text}\n\n전체 보관: {total}개",
 "exp_title": "📤 내보내기\n무엇을 내려받을까요?", "exp_texts": "📝 메모 (TXT)",
 "exp_pass": "🔑 비밀번호 (TXT)", "exp_none": "내보낼 항목이 없습니다.",
 "backup_ok": "💾 백업 파일을 채널로 보냈습니다.", "backup_fail": "⚠️ 백업에 실패했습니다.",
 "lang_title": "🌐 언어를 선택하세요:",
 "alert_new": "🔔 새 계정으로 봇에 로그인했습니다: {name} (id {id})",
 "alert_block": "⚠️ 누군가 비밀번호를 {n}번 틀렸습니다: {name} (id {id})",
 "edit_ask": "✏️ 새 내용(또는 설명)을 입력하세요:", "edited": "✅ 수정했습니다.",
 "move_title": "📂 어느 폴더로 옮길까요?", "moved": "✅ 옮겼습니다.",
 "deleted": "🗑 삭제했습니다.",
},
}


def t(uid, key, **kw):
    if uid not in LANG:
        load_lang(uid)
    s = T.get(LANG.get(uid, "uz"), T["uz"]).get(key) or T["uz"][key]
    return s.format(**kw) if kw else s


def load_lang(uid):
    try:
        r = run("SELECT lang FROM users WHERE user_id=?", (uid,))
        LANG[uid] = r[0]["lang"] if r else "uz"
    except Exception:
        LANG[uid] = "uz"


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

def is_auth(uid):
    ts = authorized.get(uid)
    if ts is None:
        return False
    if time.time() - ts > AUTO_LOCK:
        authorized.pop(uid, None)
        vault_open.discard(uid)
        return False
    authorized[uid] = time.time()
    if uid not in LANG:
        load_lang(uid)
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

TAG_RE = re.compile(r"#(\w{1,16})")

def tags_of(text):
    if not text:
        return ""
    found = []
    for m in TAG_RE.findall(text):
        m = m.lower()
        if m not in found:
            found.append(m)
    return (" " + " ".join(found) + " ") if found else ""

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
def run(sql, args=()):
    global dirty
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, args).fetchall()
        con.commit()
        if not sql.lstrip().upper().startswith(("SELECT", "PRAGMA")):
            dirty = True
        return rows
    finally:
        con.close()

def init_db():
    global dirty
    run("""CREATE TABLE IF NOT EXISTS items(
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, file_id TEXT,
        content TEXT, caption TEXT, folder_id INTEGER, fav INTEGER DEFAULT 0,
        created TEXT, tags TEXT DEFAULT '')""")
    cols = [r["name"] for r in run("PRAGMA table_info(items)")]
    if "tags" not in cols:
        run("ALTER TABLE items ADD COLUMN tags TEXT DEFAULT ''")
    run("CREATE TABLE IF NOT EXISTS folders(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)")
    run("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY, name TEXT, lang TEXT DEFAULT 'uz', first_seen TEXT)""")
    run("""CREATE TABLE IF NOT EXISTS reminders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT,
        due INTEGER, done INTEGER DEFAULT 0)""")
    run("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    dirty = False

def save(kind, file_id=None, content=None, caption=None, folder=None, tags=""):
    run("INSERT INTO items(kind,file_id,content,caption,folder_id,created,tags) VALUES(?,?,?,?,?,?,?)",
        (kind, file_id, content, caption, folder, now(), tags))

def where_for(key):
    if key in ("photo", "video", "doc", "text", "pass"):
        return "kind=?", (key,)
    if key == "fav":
        return "fav=1 AND kind!='pass'", ()
    if key.startswith("g"):
        return "tags LIKE ? AND kind!='pass'", (f"% {key[1:]} %",)
    return "folder_id=? AND kind!='pass'", (int(key[1:]),)

def fetch(key, page, size=PAGE_SIZE):
    where, args = where_for(key)
    total = run(f"SELECT COUNT(*) c FROM items WHERE {where}", args)[0]["c"]
    rows = run(f"SELECT * FROM items WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
               args + (size, page * size))
    return rows, total

def counts():
    return {r["kind"]: r["c"] for r in run("SELECT kind, COUNT(*) c FROM items GROUP BY kind")}

def do_search(term):
    term = term.lower()
    res = []
    for it in run("SELECT * FROM items WHERE kind!='pass' ORDER BY id DESC"):
        if it["kind"] == "text":
            hay = dec(it["content"]) + " " + (it["tags"] or "")
        else:
            hay = (it["caption"] or "") + " " + (it["tags"] or "")
        if term in hay.lower():
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
        "SELECT kind, COUNT(*) c FROM items WHERE created>=? GROUP BY kind", (since,))}
    total = run("SELECT COUNT(*) c FROM items")[0]["c"]
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
    while True:
        await asyncio.sleep(30)
        try:
            await fire_reminders(app.bot)
            await weekly_report(app.bot)
            if time.time() - last_backup >= 300:
                last_backup = time.time()
                await backup(app.bot)
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
def kb_main(uid):
    c = counts()
    g = lambda k: c.get(k, 0)
    return Markup([
        [Btn(t(uid, "b_photos", n=g("photo")), callback_data="alb:photo:0"),
         Btn(t(uid, "b_videos", n=g("video")), callback_data="list:video:0")],
        [Btn(t(uid, "b_docs", n=g("doc")), callback_data="list:doc:0"),
         Btn(t(uid, "b_texts", n=g("text")), callback_data="list:text:0")],
        [Btn(t(uid, "b_favs"), callback_data="list:fav:0"), Btn(t(uid, "b_tags"), callback_data="tags")],
        [Btn(t(uid, "b_folders"), callback_data="folders"), Btn(t(uid, "b_search"), callback_data="search")],
        [Btn(t(uid, "b_remind"), callback_data="rem"), Btn(t(uid, "b_stats"), callback_data="stats")],
        [Btn(t(uid, "b_vault"), callback_data="vault")],
        [Btn(t(uid, "b_export"), callback_data="export"), Btn(t(uid, "b_backup"), callback_data="backup")],
        [Btn(t(uid, "b_lang"), callback_data="lang"), Btn(t(uid, "b_logout"), callback_data="logout")],
    ])

def back_kb(uid, target="menu"):
    return Markup([[Btn(t(uid, "b_back"), callback_data=target)]])

def menu_kb(uid):
    return Markup([[Btn(t(uid, "b_menu"), callback_data="menu")]])

def kb_vault(uid):
    return Markup([
        [Btn(t(uid, "v_list"), callback_data="list:pass:0")],
        [Btn(t(uid, "v_add"), callback_data="vault_add"), Btn(t(uid, "v_gen"), callback_data="vault_gen")],
        [Btn(t(uid, "b_menu"), callback_data="menu")],
    ])

def item_kb(uid, it):
    i = it["id"]
    if it["kind"] == "pass":
        return Markup([[Btn(t(uid, "b_del"), callback_data=f"del:{i}"),
                        Btn(t(uid, "b_edit"), callback_data=f"edit:{i}")]])
    fav = t(uid, "fav_del") if it["fav"] else t(uid, "fav_add")
    return Markup([
        [Btn(fav, callback_data=f"fav:{i}"), Btn(t(uid, "b_del"), callback_data=f"del:{i}")],
        [Btn(t(uid, "b_edit"), callback_data=f"edit:{i}"), Btn(t(uid, "b_move"), callback_data=f"mv:{i}")],
    ])

def back_target(key):
    if key == "pass":
        return "vault"
    if key.startswith("g"):
        return "tags"
    if key[:1] == "d" and key[1:].isdigit():
        return "folders"
    return "menu"

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
    text = t(uid, "menu_title")
    fid = context.user_data.get("folder")
    if fid:
        r = run("SELECT name FROM folders WHERE id=?", (fid,))
        if r:
            text += "\n\n" + t(uid, "cur_folder", name=r[0]["name"])
    await show(update, text, kb_main(uid))


# ---------- Elementlarni ko'rsatish ----------
async def send_item(bot, chat_id, uid, it):
    kb, k = item_kb(uid, it), it["kind"]
    if k in ("photo", "video", "doc"):
        cap = ((it["caption"] + "\n") if it["caption"] else "") + "🕒 " + it["created"]
        fn = {"photo": bot.send_photo, "video": bot.send_video, "doc": bot.send_document}[k]
        await fn(chat_id, it["file_id"], caption=cap, reply_markup=kb, protect_content=True)
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
        await send_item(context.bot, q.message.chat_id, uid, it)
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
    media = [InputMediaPhoto(it["file_id"]) if it["kind"] == "photo" else InputMediaVideo(it["file_id"])
             for it in rows]
    if len(media) == 1:
        await context.bot.send_photo(chat_id=q.message.chat_id, photo=rows[0]["file_id"],
                                     protect_content=True)
    else:
        await context.bot.send_media_group(chat_id=q.message.chat_id, media=media,
                                           protect_content=True)
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


# ---------- Buyruq va xabarlar ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    context.user_data["state"] = None
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

async def after_login(bot, uid, name):
    known = run("SELECT user_id FROM users WHERE user_id=?", (uid,))
    if not known:
        run("INSERT INTO users(user_id,name,lang,first_seen) VALUES(?,?,?,?)", (uid, name, "uz", now()))
        await alert_others(bot, uid, "alert_new", name=name, id=uid)
    load_lang(uid)

async def group_reply(msg, uid, gid):
    last = -1
    while group_buf.get(gid) != last:
        last = group_buf.get(gid)
        await asyncio.sleep(2.5)
    n = group_buf.pop(gid, 1)
    await msg.reply_text(t(uid, "saved_n", n=n), reply_markup=menu_kb(uid))

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = update.effective_user.id
    name = update.effective_user.first_name or "?"
    ud = context.user_data

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
            await after_login(context.bot, uid, name)
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
                run("UPDATE items SET content=?, tags=? WHERE id=?", (enc(text), tags_of(text), it["id"]))
            else:
                run("UPDATE items SET caption=?, tags=? WHERE id=?", (text, tags_of(text), it["id"]))
            await msg.reply_text(t(uid, "edited"), reply_markup=menu_kb(uid))
        return

    # 3) Oddiy saqlash (bitta yoki ko'p fayl)
    folder = ud.get("folder")
    if msg.photo:
        save("photo", file_id=msg.photo[-1].file_id, caption=msg.caption, folder=folder,
             tags=tags_of(msg.caption))
        kind = "k_photo"
    elif msg.video:
        save("video", file_id=msg.video.file_id, caption=msg.caption, folder=folder,
             tags=tags_of(msg.caption))
        kind = "k_video"
    elif msg.document:
        save("doc", file_id=msg.document.file_id, caption=msg.caption, folder=folder,
             tags=tags_of(msg.caption))
        kind = "k_doc"
    elif msg.text:
        save("text", content=enc(msg.text), folder=folder, tags=tags_of(msg.text))
        kind = "k_text"
    else:
        await msg.reply_text(t(uid, "unsupported"))
        return

    if kind != "k_text":
        try:
            await context.bot.copy_message(CHANNEL_ID, msg.chat_id, msg.message_id)
        except Exception:
            log.exception("Kanalga yuborib bo'lmadi")

    gid = msg.media_group_id
    if gid:
        if gid in group_buf:
            group_buf[gid] += 1
        else:
            group_buf[gid] = 1
            spawn(group_reply(msg, uid, gid))
        return
    await msg.reply_text(t(uid, "saved", what=t(uid, kind)), reply_markup=menu_kb(uid))


# ---------- Tugmalar ----------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data
    ud = context.user_data

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

    elif data.startswith("del:"):
        run("DELETE FROM items WHERE id=?", (int(data[4:]),))
        try:
            await q.message.delete()
        except Exception:
            await q.message.reply_text(t(uid, "deleted"))

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
        kb = Markup([
            [Btn(t(uid, "f_view"), callback_data=f"list:d{fid}:0")],
            [Btn(t(uid, "f_del"), callback_data=f"dfdel:{fid}")],
            [Btn(t(uid, "b_back"), callback_data="folders")],
        ])
        await show(update, t(uid, "f_sel", name=r[0]["name"]), kb)

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

    elif data == "tags":
        cnt = {}
        for r in run("SELECT tags FROM items WHERE tags!='' AND kind!='pass'"):
            for tg in (r["tags"] or "").split():
                cnt[tg] = cnt.get(tg, 0) + 1
        if not cnt:
            await show(update, t(uid, "tags_none"), back_kb(uid))
            return
        top = sorted(cnt.items(), key=lambda x: -x[1])[:30]
        btns = [Btn(f"#{tg} ({n})", callback_data=f"list:g{tg}:0") for tg, n in top]
        kb = [btns[i:i + 2] for i in range(0, len(btns), 2)]
        kb.append([Btn(t(uid, "b_back"), callback_data="menu")])
        await show(update, t(uid, "tags_title"), Markup(kb))

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
                             doc=c.get("doc", 0), text=c.get("text", 0), passw=c.get("pass", 0),
                             folders=nf, rem=nr), back_kb(uid))

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
        rows = run("SELECT * FROM items WHERE kind=? ORDER BY id", (kind,))
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

    elif data == "lang":
        kb = Markup([
            [Btn("🇺🇿 O'zbekcha", callback_data="setlang:uz"),
             Btn("🇷🇺 Русский", callback_data="setlang:ru"),
             Btn("🇰🇷 한국어", callback_data="setlang:ko")],
            [Btn(t(uid, "b_back"), callback_data="menu")],
        ])
        await show(update, t(uid, "lang_title"), kb)

    elif data.startswith("setlang:"):
        code = data[8:]
        if code in T:
            run("UPDATE users SET lang=? WHERE user_id=?", (code, uid))
            LANG[uid] = code
        await send_menu(update, context)

    elif data == "logout":
        authorized.pop(uid, None)
        vault_open.discard(uid)
        ud.clear()
        await show(update, t(uid, "logged_out"))

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
