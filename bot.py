import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Ты — личный ассистент и бизнес-тренер Матвея.
Твоя задача — помогать ему достигать целей.
Ты придерживаешься учений Кови, понимаешь метод Гарварда, умеешь фокусироваться на главном.
Отвечай по-русски, коротко и по делу. Без воды."""

conversation_history: dict[int, list] = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    conversation_history[chat_id] = []
    await update.message.reply_text(
        "Привет, Матвей! Я твой личный ассистент на базе Claude. Чем могу помочь?"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    conversation_history[chat_id] = []
    await update.message.reply_text("История очищена. Начинаем с чистого листа.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text

    if chat_id not in conversation_history:
        conversation_history[chat_id] = []

    conversation_history[chat_id].append({
        "role": "user",
        "content": user_text
    })

    history = conversation_history[chat_id][-20:]

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=history
        )

        assistant_reply = response.content[0].text

        conversation_history[chat_id].append({
            "role": "assistant",
            "content": assistant_reply
        })

        await update.message.reply_text(assistant_reply)

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {str(e)}")

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
