import os
import random
import logging
import threading
import requests
from flask import Flask
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

# Dummy Flask server for Render free web service uptime
flask_app = Flask(__name__)

@flask_app.route("/")
def health_check():
    return "Hangman Bot is Alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# Active game state storage: {chat_id: game_dict}
active_games = {}

# Fallback word bank with definitions and synonyms
BACKUP_WORDS = [
    ("BREAKFAST", "The first meal of the day, usually eaten in the morning.", ["morning meal", "brunch"]),
    ("MAGNETIC", "Capable of attracting iron objects or having great charisma.", ["attractive", "alluring", "compelling"]),
    ("DEVELOPER", "A person or entity that creates computer software.", ["programmer", "coder", "engineer"]),
    ("CHESSBOARD", "A square checkered board divided into 64 squares for playing chess.", ["gameboard"]),
    ("ALGORITHM", "A precise step-by-step procedure for solving a computational problem.", ["procedure", "formula", "logic"]),
    ("DATABASE", "A structured collection of electronic data stored in a computer system.", ["data store", "repository"]),
    ("TELEGRAM", "A cloud-based instant messaging and broadcasting service.", ["message", "dispatch"]),
    ("KEYBOARD", "A set of input keys on a computer or musical instrument.", ["keypad", "terminal"]),
    ("AUTOMATION", "The use of automatic control systems to operate equipment or software.", ["mechanization", "robotics"])
]

# -------------------------------------------------------------------
# Helper Functions & Word Fetcher
# -------------------------------------------------------------------
def fetch_synonyms(word):
    """Fetches up to 3 common synonyms for the target word."""
    try:
        url = f"https://api.datamuse.com/words?rel_syn={word.lower()}&max=3"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            syns = [w.get("word") for w in response.json() if w.get("word")]
            return syns
    except Exception as e:
        logging.error(f"Failed to fetch synonyms: {e}")
    return []

def fetch_random_word(min_length=6, max_length=10):
    """
    Fetches a random word along with its definition and synonyms.
    """
    try:
        start_char = random.choice("abcdefghijklmnopqrstuvwxyz")
        url = f"https://api.datamuse.com/words?sp={start_char}{'?' * (min_length - 1)}*&md=d&max=100"
        
        response = requests.get(url, timeout=5)

        if response.status_code == 200:
            words = response.json()
            valid_items = []
            for w in words:
                word_text = w.get("word", "")
                defs = w.get("defs", [])
                if word_text.isalpha() and min_length <= len(word_text) <= max_length:
                    clean_def = ""
                    if defs:
                        first_def = defs[0]
                        clean_def = first_def.split("\t")[-1].strip().capitalize()
                    valid_items.append((word_text.upper(), clean_def))

            if valid_items:
                chosen_word, chosen_def = random.choice(valid_items)
                synonyms = fetch_synonyms(chosen_word)
                return chosen_word, chosen_def, synonyms
    except Exception as e:
        logging.error(f"Failed to fetch dynamic word details: {e}")

    return random.choice(BACKUP_WORDS)

def format_word_details(definition, synonyms):
    """Helper to format definition and synonyms block cleanly."""
    details = ""
    if definition:
        details += f"\n\n📖 <i>{definition}</i>"
    if synonyms:
        syn_str = ", ".join(synonyms)
        details += f"\n🔗 <b>Synonyms:</b> {syn_str}"
    return details

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
        definition = active_games[chat_id]["definition"]
        synonyms = active_games[chat_id]["synonyms"]
        del active_games[chat_id]

        info_block = format_word_details(definition, synonyms)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏳ Time's up! The word was <b>{word}</b>.{info_block}\n\nUse /hangman to play again.",
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

    word, definition, synonyms = fetch_random_word(min_length=6, max_length=10)

    active_games[chat_id] = {
        "word": word,
        "definition": definition,
        "synonyms": synonyms,
        "guessed": set(),
        "wrong": [],
        "lives": 7,
    }

    reset_game_timer(context.job_queue, chat_id)

    game = active_games[chat_id]
    display = render_word(game["word"], game["guessed"])

    text = f"🔤 <b>Hangman</b>\n\n<code>{display}</code>\n\n❤️ {game['lives']} lives left"
    msg = await update.message.reply_text(
        text, reply_markup=get_keyboard(game["guessed"]), parse_mode="HTML"
    )
    active_games[chat_id]["message_id"] = msg.message_id

async def end_hangman(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user

    if chat_id not in active_games:
        await update.message.reply_text("No game is currently in progress.")
        return

    word = active_games[chat_id]["word"]
    definition = active_games[chat_id]["definition"]
    synonyms = active_games[chat_id]["synonyms"]
    last_msg_id = active_games[chat_id].get("message_id")
    
    clear_game_timer(context.job_queue, chat_id)
    del active_games[chat_id]

    if last_msg_id:
        try:
            await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=last_msg_id, reply_markup=None)
        except Exception:
            pass

    info_block = format_word_details(definition, synonyms)
    await update.message.reply_text(
        f"💀 Game ended by <b>{user.first_name}</b>. The word was <b>{word}</b>.{info_block}\n\nUse /hangman to start a new game.",
        parse_mode="HTML"
    )

async def handle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "ignore":
        await query.answer()
        return

    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in active_games:
        await query.answer("⚠️ No active game. Use /hangman to start a new one!", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    await query.answer()
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
        word = game["word"]
        definition = game["definition"]
        synonyms = game["synonyms"]
        del active_games[chat_id]
        
        info_block = format_word_details(definition, synonyms)
        await query.edit_message_text(
            f"🎯 <b>{user.first_name}</b> solved it — the word was <b>{word}</b>!{info_block}\n\n/hangman for a rematch",
            reply_markup=None,
            parse_mode="HTML"
        )
        return

    # Loss Condition
    if game["lives"] <= 0:
        clear_game_timer(context.job_queue, chat_id)
        target_word = game["word"]
        definition = game["definition"]
        synonyms = game["synonyms"]
        del active_games[chat_id]
        
        info_block = format_word_details(definition, synonyms)
        await query.edit_message_text(
            f"💀 Out of lives! The word was <b>{target_word}</b>.{info_block}\n\n/hangman for a rematch",
            reply_markup=None,
            parse_mode="HTML"
        )
        return

    # Normal state update
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
if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("BOT_TOKEN is missing! Please check your .env file.")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("hangman", start_hangman))
    app.add_handler(CommandHandler("endhangman", end_hangman))
    app.add_handler(CallbackQueryHandler(handle_guess, pattern="^guess_"))

    print("Bot is up and running...")
    app.run_polling()