import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# Loggingni sozlash
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Bot tokeni, Parol va Kanal ID raqami
BOT_TOKEN = "9234429945:AAGTtF03VtGBmiC_bZgtDFpKzCwzRuLKkK4"
BOT_PASSWORD = "19734682Mm$$"
CHANNEL_ID = -1003958434741  # Sizning maxfiy arxivingiz uchun kanal

# Foydalanuvchilarning kirish holatini saqlash
authorized_users = set()

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

# Xabarlar va parolni tekshirish
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Agar foydalanuvchi hali kirmagan bo'lsa, parolni tekshiramiz
    if user_id not in authorized_users:
        if text == BOT_PASSWORD:
            authorized_users.add(user_id)
            await update.message.reply_text("✅ Parol to'g'ri! Xush kelibsiz.")
            await show_main_menu(update, context)
        else:
            await update.message.reply_text("❌ Noto'g'ri parol! Qaytadan urinib ko'ring:")
        return

    # Agar foydalanuvchi kirgan bo'lsa va rasm, video yoki hujjat yuborsa -> kanalga nusxalaymiz
    if update.message.photo:
        await context.bot.send_photo(chat_id=CHANNEL_ID, photo=update.message.photo[-1].file_id, caption=f"📸 Rasm yuboruvchi: {update.effective_user.first_name}")
        await update.message.reply_text("📸 Rasm maxfiy arxiv kanaliga saqlandi!")
    elif update.message.video:
        await context.bot.send_video(chat_id=CHANNEL_ID, video=update.message.video.file_id, caption=f"🎥 Video yuboruvchi: {update.effective_user.first_name}")
        await update.message.reply_text("🎥 Video maxfiy arxiv kanaliga saqlandi!")
    elif update.message.document:
        await context.bot.send_document(chat_id=CHANNEL_ID, document=update.message.document.file_id, caption=f"📁 Hujjat yuboruvchi: {update.effective_user.first_name}")
        await update.message.reply_text("📁 Maxfiy hujjat/fayl arxiv kanaliga saqlandi!")
    else:
        await update.message.reply_text("Iltimos, pastdagi menyulardan foydalaning yoki arxivlash uchun rasm, video, hujjat yuboring.")

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

# Tugmalarni boshqarish
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id not in authorized_users and query.data != "logout":
        await query.message.reply_text("⚠️ Avval parolni kiritishingiz kerak. /start ni bosing.")
        return

    if query.data == "menu_videos":
        text = "🎥 **Videolar va Rasmlar bo'limi**\nSiz botga yuborgan barcha rasm va videolar to'g'ridan-to'g'ri maxfiy kanalingizda doimiy saqlanadi."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_menu")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif query.data == "menu_docs":
        text = "📁 **Maxfiy fayllar bo'limi**\nYuborgan barcha hujjatlaringiz kanalingizda xavfsiz saqlanmoqda."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_menu")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif query.data == "menu_vault":
        text = "🔐 **Parollar Ombori**\nBu yerda shifrlangan parollaringiz saqlanadi."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_menu")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif query.data == "back_to_menu":
        await show_main_menu(update, context)

    elif query.data == "logout":
        if user_id in authorized_users:
            authorized_users.remove(user_id)
        await query.message.edit_text("🔒 Botdan chiqdingiz. Qaytadan kirish uchun /start ni bosing.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Bot ishga tushdi...")
    app.run_polling()

if __name__ == "__main__":
    main()