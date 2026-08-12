import os
import sqlite3
import random
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# Load variables from .env file
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

# Word Bank (Only words longer than 5 letters)
WORD_LIST = [
    "BREAKFAST", "MAGNETIC", "DEVELOPER", "CHESSBOARD", "ALGORITHM",
    "PYTHONIC", "DATABASE", "TELEGRAM", "KEYBOARD", "SOFTWARE",
    "AUTOMATION", "COMMUNITY", "LIBRARIES", "REPOSITORY"
]

# Database Setup
def init_db():
    conn = sqlite3.connect("hangman.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS wins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    chat_id INTEGER,
                    timestamp DATETIME
                )''')
    conn.commit()
    conn.close()

init_db()

# Active game state storage: {chat_id: game_dict}
active_games = {}

def get_keyboard(guessed_letters):
    """Generates a 4x7 interactive inline keyboard A-Z."""
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
    """Renders concealed word like 'B R E A K F A S T' or '■ ■ ■ ■ ■'."""
    return " ".join([char if char in guessed_letters else "■" for char in word])

async def start_hangman(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # Check for game in progress
    if chat_id in active_games:
        await update.message.reply_text("⚠️ A game is already in progress in this chat! Finish it before starting a new one.")
        return

    word = random.choice(WORD_LIST)
    active_games[chat_id] = {
        "word": word,
        "guessed": set(),
        "wrong": [],
        "lives": 7
    }

    game = active_games[chat_id]
    display = render_word(game["word"], game["guessed"])
    
    text = f"🟢 **Hangman Game Started!**\n\n{display}\n\n❤️ {game['lives']} lives left"
    await update.message.reply_text(text, reply_markup=get_keyboard(game["guessed"]), parse_mode="Markdown")

async def handle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "ignore":
        return

    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in active_games:
        await query.edit_message_text("No active game found. Use /hangman to start a new game!")
        return

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
        record_win(user.id, user.first_name, chat_id)
        del active_games[chat_id]
        await query.edit_message_text(f"🎯 **{user.first_name}** solved it — the word was **{game['word']}**!\n\nUse /hangman to play again.")
        return

    # Loss Condition
    if game["lives"] <= 0:
        target_word = game["word"]
        del active_games[chat_id]
        await query.edit_message_text(f"💀 Out of lives! The word was **{target_word}**.\n\nUse /hangman to try again.")
        return

    # Game state update
    text = f"🟢 **Hangman**\n\n{word_display}\n\n❤️ {game['lives']} lives left\n✖️ Wrong: {wrong_str}"
    await query.edit_message_text(text, reply_markup=get_keyboard(game["guessed"].union(set(game["wrong"]))), parse_mode="Markdown")

def record_win(user_id, username, chat_id):
    conn = sqlite3.connect("hangman.db")
    c = conn.cursor()
    c.execute("INSERT INTO wins (user_id, username, chat_id, timestamp) VALUES (?, ?, ?, ?)",
              (user_id, username, chat_id, datetime.utcnow()))
    conn.commit()
    conn.close()

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    scope = args[0].lower() if args else "chat"

    conn = sqlite3.connect("hangman.db")
    c = conn.cursor()

    if scope == "global":
        c.execute("SELECT username, COUNT(*) as score FROM wins GROUP BY user_id ORDER BY score DESC LIMIT 10")
        title = "🏆 **Global Leaderboard**"
    else:
        c.execute("SELECT username, COUNT(*) as score FROM wins WHERE chat_id = ? GROUP BY user_id ORDER BY score DESC LIMIT 10", (chat_id,))
        title = "🏆 **Group Leaderboard**"

    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("No wins recorded yet!")
        return

    text = f"{title}\n\n"
    for idx, (username, score) in enumerate(rows, start=1):
        text += f"{idx}. {username} — {score} wins\n"

    await update.message.reply_text(text, parse_mode="Markdown")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("hangman", start_hangman))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CallbackQueryHandler(handle_guess, pattern="^guess_"))

    print("Bot running...")
    app.run_polling()