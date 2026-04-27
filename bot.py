import os
import asyncpg
from datetime import datetime
import pytz
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from groq import Groq

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
MOSCOW_TZ = pytz.timezone("Europe/Moscow")

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """Ты — личный ассистент и бизнес-тренер Матвея.
Твоя задача — помогать ему достигать целей.
Ты придерживаешься учений Кови, понимаешь метод Гарварда, умеешь фокусироваться на главном.
Отвечай по-русски, коротко и по делу. Без воды."""

DIARY_QUESTIONS = [
    "📔 Доброе утро, Матвей! Время дневника.\n\n*Вопрос 1/4* — Как ты себя чувствуешь сегодня утром?",
    "*Вопрос 2/4* — Что было главным вчера? Чего достиг?",
    "*Вопрос 3/4* — Какой главный фокус на сегодня? Одна самая важная задача.",
    "*Вопрос 4/4* — За что благодарен прямо сейчас?"
]

# Хранилище состояний
conversation_history: dict[int, list] = {}
diary_state: dict[int, int] = {}
diary_answers: dict[int, list] = {}
db_pool = None


# ─── База данных ───────────────────────────────────────────────

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id BIGINT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS diary (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT,
                date DATE,
                q1 TEXT, q2 TEXT, q3 TEXT, q4 TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

async def save_user(chat_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (chat_id) VALUES ($1) ON CONFLICT DO NOTHING",
            chat_id
        )

async def save_diary(chat_id: int, answers: list):
    today = datetime.now(MOSCOW_TZ).date()
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO diary (chat_id, date, q1, q2, q3, q4)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, chat_id, today,
            answers[0] if len(answers) > 0 else "",
            answers[1] if len(answers) > 1 else "",
            answers[2] if len(answers) > 2 else "",
            answers[3] if len(answers) > 3 else ""
        )

async def get_history(chat_id: int, limit: int = 7):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT date, q1, q2, q3, q4
            FROM diary WHERE chat_id = $1
            ORDER BY date DESC LIMIT $2
        """, chat_id, limit)
    return rows

async def get_all_users():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT chat_id FROM users")
    return [r["chat_id"] for r in rows]


# ─── Команды ───────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    conversation_history[chat_id] = []
    await save_user(chat_id)
    await update.message.reply_text(
        "Привет, Матвей! Я твой личный ассистент.\n\n"
        "/diary — заполнить дневник\n"
        "/history — последние записи\n"
        "/clear — очистить историю\n\n"
        "Каждый день в 9:45 напомню о дневнике. Чем могу помочь?"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    conversation_history[chat_id] = []
    await update.message.reply_text("История очищена.")

async def diary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_diary(context.bot, update.effective_chat.id)

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows = await get_history(chat_id)
    if not rows:
        await update.message.reply_text("Записей пока нет. Заполни первый дневник: /diary")
        return
    text = "📔 *Твои последние записи:*\n\n"
    for row in rows:
        date_str = row["date"].strftime("%d.%m.%Y")
        text += f"*{date_str}*\n😊 {row['q1']}\n✅ {row['q2']}\n🎯 {row['q3']}\n🙏 {row['q4']}\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── Дневник ───────────────────────────────────────────────────

async def start_diary(bot, chat_id: int):
    diary_state[chat_id] = 0
    diary_answers[chat_id] = []
    await bot.send_message(chat_id=chat_id, text=DIARY_QUESTIONS[0], parse_mode="Markdown")

async def handle_diary_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    diary_answers[chat_id].append(update.message.text)
    next_q = diary_state[chat_id] + 1
    if next_q < len(DIARY_QUESTIONS):
        diary_state[chat_id] = next_q
        await update.message.reply_text(DIARY_QUESTIONS[next_q], parse_mode="Markdown")
    else:
        del diary_state[chat_id]
        answers = diary_answers.pop(chat_id)
        await save_diary(chat_id, answers)
        await update.message.reply_text("✅ Дневник сохранён. Хорошего дня!\n\nПосмотреть: /history")


# ─── Обычный чат ───────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id in diary_state:
        await handle_diary_answer(update, context)
        return

    user_text = update.message.text
    if chat_id not in conversation_history:
        conversation_history[chat_id] = []

    conversation_history[chat_id].append({"role": "user", "content": user_text})
    history = conversation_history[chat_id][-20:]

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=1024
        )
        assistant_reply = response.choices[0].message.content
        conversation_history[chat_id].append({"role": "assistant", "content": assistant_reply})
        await update.message.reply_text(assistant_reply)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {str(e)}")


# ─── Планировщик ───────────────────────────────────────────────

async def daily_diary_reminder(bot):
    users = await get_all_users()
    for chat_id in users:
        try:
            await start_diary(bot, chat_id)
        except Exception as e:
            print(f"Ошибка напоминания {chat_id}: {e}")


# ─── Запуск ────────────────────────────────────────────────────

async def post_init(app):
    await init_db()
    scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
    scheduler.add_job(
        daily_diary_reminder,
        trigger="cron",
        hour=9,
        minute=45,
        args=[app.bot]
    )
    scheduler.start()
    print("Планировщик запущен — напоминание в 9:45 МСК")

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("diary", diary_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
