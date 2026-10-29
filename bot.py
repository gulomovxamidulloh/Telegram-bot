import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# Loggingni sozlash
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Bot tokeni, Asosiy parol, Parollar bo'limi uchun alohida maxfiy kod va Kanal ID raqami
BOT_TOKEN = "9234429945:AAGTtF03VtGBmiC_bZgtDFpKzCwzRuLKkK4"
BOT_PASSWORD = "19734682Mm$$"         # Botga kirish uchun asosiy parol
VAULT_PASSWORD = "7777"               # 🔐 Parollar bo'limiga kirish uchun maxfiy PIN-kod (buni o'zingiz xohlagancha o'zgartirishingiz mumkin)
CHANNEL_ID = -1003958434741           # Sizning maxfiy arxivingiz uchun kanal

# Foydalanuvchilarning kirish va holatlarini saqlash
authorized_users = set()
vault_unlocked_users = set() # Parollar bo'limiga ruxsat olganlar

# /start buyrug'i
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in authorized_users:
        await show_main_menu(update, context)
    else:
        await update.message.reply_text(
            "🔒 Botdan foydalanish uchun iltimos, maxfiy parolni kiriting:\n\n"
            "(Parolni yuborishingiz bilan menyu ochiladi)"
        )

# Xabarlar va parollarni tekshirish
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # 1. Agar foydalanuvchi hali asosiy botga kirmagan bo'lsa
    if user_id not in authorized_users:
        if text == BOT_PASSWORD:
            authorized_users.add(user_id)
            await update.message.reply_text("✅ Parol to'g'ri! Xush kelibsiz.")
            await show_main_menu(update, context)
        else:
            await update.message.reply_text("❌ Noto'g'ri parol! Qaytadan urinib ko'ring:")
        return

    # 2. Agar foydalanuvchi "Parollar ombori" uchun maxfiy kodni kiritayotgan bo'lsa
    if context.user_data.get("waiting_for_vault_password"):
        if text == VAULT_PASSWORD:
            context.user_data["waiting_for_vault_password"] = False
            vault_unlocked_users.add(user_id)
            await show_main_menu_vault(update, context)
        else:
            await update.message.reply_text("❌ Parollar bo'limi uchun kiritilgan kod xato! Qaytadan kiriting yoki /start ni bosing:")
        return

    # 3. Oddiy matn, rasm, video yoki hujjat yuborsa -> kanalga saqlaymiz
    if update.message.photo:
        await context.bot.send_photo(chat_id=CHANNEL_ID, photo=update.message.photo[-1].file_id, caption=f"📸 Rasm yuboruvchi: {update.effective_user.first_name}")
        await update.message.reply_text("📸 Rasm maxfiy arxiv kanaliga saqlandi!")
    elif update.message.video:
        await context.bot.send_video(chat_id=CHANNEL_ID, video=update.message.video.file_id, caption=f"🎥 Video yuboruvchi: {update.effective_user.first_name}")
        await update.message.reply_text("🎥 Video maxfiy arxiv kanaliga saqlandi!")
    elif update.message.document:
        await context.bot.send_document(chat_id=CHANNEL_ID, document=update.message.document.file_id, caption=f"📁 Hujjat yuboruvchi: {update.effective_user.first_name}")
        await update.message.reply_text("📁 Maxfiy hujjat/fayl arxiv kanaliga saqlandi!")
    elif update.message.text:
        # Matn yoki parollarni ham kanalga yuborib saqlaymiz
        await context.bot.send_message(chat_id=CHANNEL_ID, text=f"📝 **Saqlangan matn/eslatma:**\n\n{text}")
        await update.message.reply_text("📝 Matn va ma'lumot maxfiy arxiv kanaliga saqlandi!")

# Asosiy menyuni chiqarish
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎥 Videolar va Rasmlar", callback_data="menu_videos"),
         InlineKeyboardButton("📁 Maxfiy fayllar", callback_data="menu_docs")],
        [InlineKeyboardButton("🔐 Parollar ombori", callback_data="menu_vault")],
        [InlineKeyboardButton("🚪 Chiqish (Bloklash)", callback_data="logout")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.message.edit_text("📂 **Asosiy Arxiv Menyu:**\nKerakli bo'limni tanlang:", reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text("📂 **Asosiy Arxiv Menyu:**\nKerakli bo'limni tanlang:", reply_markup=reply_markup, parse_mode="Markdown")

# Parollar ombori menyusi
async def show_main_menu_vault(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔑 Parollarni ko'rish/boshqarish", callback_data="vault_list")],
        [InlineKeyboardButton("⬅️ Asosiy menyuga qaytish", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "🔐 **Parollar Ombori ochildi!**\nXavfsiz hudushdasiz. Kerakli amalni tanlang:"
    
    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

# Tugmalarni boshqarish
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id not in authorized_users and query.data != "logout":
        await query.message.reply_text("⚠️ Avval asosiy parolni kiritishingiz kerak. /start ni bosing.")
        return

    if query.data == "menu_videos":
        text = "🎥 **Videolar va Rasmlar bo'limi**\nBotga yuborgan barcha rasm va videolaringiz maxfiy kanalingizda saqlanmoqda."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_menu")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif query.data == "menu_docs":
        text = "📁 **Maxfiy fayllar bo'limi**\nYuborgan barcha hujjat va fayllaringiz kanalda saqlanmoqda."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_menu")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif query.data == "menu_vault":
        # Parollar bo'limiga kirish uchun qo'shimcha PIN-kod so'raymiz
        if user_id in vault_unlocked_users:
            await show_main_menu_vault(update, context)
        else:
            context.user_data["waiting_for_vault_password"] = True
            text = "🔐 **Parollar ombori yashiringan!**\nBu bo'limga kirish uchun maxfiy **PIN-kod**ni kiriting:"
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_menu")]]
            await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif query.data == "vault_list":
        text = "🔑 **Saqlangan parollar:**\nBarcha parollaringiz va matnli eslatmalaringiz maxfiy kanalingizga yuborilgan va doimiy saqlanmoqda."
        keyboard = [
            [InlineKeyboardButton("⬅️ Orqaga", callback_data="menu_vault")]
        ]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif query.data == "back_to_menu":
        context.user_data["waiting_for_vault_password"] = False
        await show_main_menu(update, context)

    elif query.data == "logout":
        if user_id in authorized_users:
            authorized_users.remove(user_id)
        if user_id in vault_unlocked_users:
            vault_unlocked_users.remove(user_id)
        context.user_data["waiting_for_vault_password"] = False
        await query.message.edit_text("🔒 Botdan chiqdingiz. Qaytadan kirish uchun /start ni bosing.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.TEXT, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Bot ishga tushdi...")
    app.run_polling()

if __name__ == "__main__":
    main()

