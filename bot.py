import os
import random
import logging
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# -------------------------------------------------------------------
# Logging & Environment Setup
# -------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# Active game state storage: {chat_id: game_dict}
active_games = {}

# -------------------------------------------------------------------
# Helper Functions & Dynamic Word Fetcher
# -------------------------------------------------------------------
def fetch_random_word(min_length=6, max_length=10):
    try:
        start_char = random.choice("abcdefghijklmnopqrstuvwxyz")
        url = f"https://api.datamuse.com/words?sp={start_char}{'?' * (min_length - 1)}*&max=100"
        
        response = requests.get(url, timeout=5)

        if response.status_code == 200:
            words = response.json()
            valid_words = [
                w["word"].upper()
                for w in words
                if w["word"].isalpha() and min_length <= len(w["word"]) <= max_length
            ]
            if valid_words:
                return random.choice(valid_words)
    except Exception as e:
        logging.error(f"Failed to fetch dynamic word from API: {e}")

    backup_words = [
        "BREAKFAST", "MAGNETIC", "DEVELOPER", "CHESSBOARD",
        "ALGORITHM", "PYTHONIC", "DATABASE", "TELEGRAM",
        "KEYBOARD", "SOFTWARE", "AUTOMATION", "COMMUNITY",
        "INTERFACE", "NETWORK", "SECURITY", "GAMING"
    ]
    return random.choice(backup_words)


def get_keyboard(guessed_letters):
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    keyboard = []
    row = []
    for letter in alphabet:
        if letter in guessed_letters:
            row.append(InlineKeyboardButton(" ", callback_data="ignore"))
        else:
            row.append(InlineKeyboardButton(letter, callback_data=f"guess_{letter}"))
        if len(row) == 7:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)


def render_word(word, guessed_letters):
    return " ".join([char if char in guessed_letters else "■" for char in word])

# -------------------------------------------------------------------
# Auto-Expiration System (10-Min Inactivity Timeout)
# -------------------------------------------------------------------
async def send_timeout_warning(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    if chat_id in active_games:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ Game will end in 2 minutes due to inactivity!",
            parse_mode="HTML"
        )

async def expire_game(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    if chat_id in active_games:
        word = active_games[chat_id]["word"]
        del active_games[chat_id]
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏳ Time's up! The word was <b>{word}</b>.\n\nUse /hangman to play again.",
            parse_mode="HTML"
        )

def reset_game_timer(job_queue, chat_id):
    clear_game_timer(job_queue, chat_id)

    job_queue.run_once(
        send_timeout_warning,
        when=480,
        chat_id=chat_id,
        name=f"warn_{chat_id}"
    )
    job_queue.run_once(
        expire_game,
        when=600,
        chat_id=chat_id,
        name=f"expire_{chat_id}"
    )

def clear_game_timer(job_queue, chat_id):
    for job_name in [f"warn_{chat_id}", f"expire_{chat_id}"]:
        current_jobs = job_queue.get_jobs_by_name(job_name)
        for job in current_jobs:
            job.schedule_removal()

# -------------------------------------------------------------------
# Command & Callback Handlers
# -------------------------------------------------------------------
async def start_hangman(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id in active_games:
        await update.message.reply_text(
            "A game is already in progress! Use /endhangman to stop it first."
        )
        return

    word = fetch_random_word(min_length=6, max_length=10)

    active_games[chat_id] = {
        "word": word,
        "guessed": set(),
        "wrong": [],
        "lives": 7,
    }

    reset_game_timer(context.job_queue, chat_id)

    game = active_games[chat_id]
    display = render_word(game["word"], game["guessed"])

    text = f"🔤 <b>Hangman</b>\n\n<code>{display}</code>\n\n❤️ {game['lives']} lives left"
    await update.message.reply_text(
        text, reply_markup=get_keyboard(game["guessed"]), parse_mode="HTML"
    )


async def end_hangman(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user

    if chat_id not in active_games:
        await update.message.reply_text("No game is currently in progress.")
        return

    word = active_games[chat_id]["word"]
    
    clear_game_timer(context.job_queue, chat_id)
    del active_games[chat_id]

    await update.message.reply_text(
        f"💀 Game ended by <b>{user.first_name}</b>. The word was <b>{word}</b>.\n\nUse /hangman to start a new game.",
        parse_mode="HTML"
    )


async def handle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "ignore":
        return

    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in active_games:
        await query.edit_message_text(
            "No active game found. Use /hangman to start a new game!"
        )
        return

    reset_game_timer(context.job_queue, chat_id)

    game = active_games[chat_id]
    letter = query.data.split("_")[1]

    if letter in game["guessed"] or letter in game["wrong"]:
        return

    if letter in game["word"]:
        game["guessed"].add(letter)
    else:
        game["wrong"].append(letter)
        game["lives"] -= 1

    word_display = render_word(game["word"], game["guessed"])
    wrong_str = " ".join(game["wrong"]) if game["wrong"] else "None"

    # Win Condition
    if set(game["word"]).issubset(game["guessed"]):
        clear_game_timer(context.job_queue, chat_id)
        del active_games[chat_id]
        
        await query.edit_message_text(
            f"🎯 <b>{user.first_name}</b> solved it — the word was <b>{game['word']}</b>!\n\n/hangman for a rematch",
            parse_mode="HTML"
        )
        return

    # Loss Condition
    if game["lives"] <= 0:
        clear_game_timer(context.job_queue, chat_id)
        target_word = game["word"]
        del active_games[chat_id]
        
        await query.edit_message_text(
            f"💀 Out of lives! The word was <b>{target_word}</b>.\n\n/hangman for a rematch",
            parse_mode="HTML"
        )
        return

    # Active play state
    text = (
        f"🔤 <b>Hangman</b>\n\n<code>{word_display}</code>\n\n❤️ {game['lives']} lives left\n✖️ Wrong: {wrong_str}"
    )
    await query.edit_message_text(
        text,
        reply_markup=get_keyboard(game["guessed"].union(set(game["wrong"]))),
        parse_mode="HTML",
    )

# -------------------------------------------------------------------
# Main Execution
# -------------------------------------------------------------------

import threading
from flask import Flask

# Dummy HTTP server to satisfy Render's free Web Service requirement
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return "Hangman Bot is Alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# Run Flask server in background thread
threading.Thread(target=run_flask, daemon=True).start()

if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("BOT_TOKEN is missing! Please check your .env file.")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("hangman", start_hangman))
    app.add_handler(CommandHandler("endhangman", end_hangman))
    app.add_handler(CallbackQueryHandler(handle_guess, pattern="^guess_"))

    print("Bot is up and running...")
    app.run_polling()