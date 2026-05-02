from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
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
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DATA_FILE = "wallets.json"
SECRET_KEY = os.getenv("SECRET_KEY")
PORT = int(os.getenv("PORT", 5000))

if not SECRET_KEY:
    raise Exception("Set SECRET_KEY environment variable on Render!")
if not TELEGRAM_BOT_TOKEN:
    raise Exception("TELEGRAM_BOT_TOKEN not set!")

cipher = Fernet(SECRET_KEY.encode())

# ---------------- STORAGE ----------------
data_store = {}
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
                raw = json.load(f)
                data_store = {int(k): v for k, v in raw.items()}
            logger.info(f"✅ Loaded {sum(len(v) for v in data_store.values())} wallets")
        except Exception as e:
            logger.error(f"Failed to load data: {e}")

def save_data():
    try:
        with data_lock:
            with open(DATA_FILE, "w") as f:
                json.dump(data_store, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save data: {e}")

# ---------------- FLASK ----------------
@app.route("/")
def home():
    return "Bot is alive ✅"

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ High-Performance Sweeper\n\n"
        "/setwallet <chain> <secret> <destination>\n"
        "/getinfo\n"
        "/removewallet"
    )

async def setwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /setwallet <chain> <secret> <destination>")
        return

    chain = context.args[0].lower()
    destination = context.args[-1]
    secret_input = " ".join(context.args[1:-1])

    if chain not in ["solana", "ethereum", "bsc"]:
        await update.message.reply_text("Supported chains: solana, ethereum, bsc")
        return

    wallet_type = "seed" if " " in secret_input else "private_key"

    user_id = update.effective_user.id
    wallet = {
        "chain": chain,
        "type": wallet_type,
        "secret": encrypt(secret_input),
        "destination": destination
    }

    with data_lock:
        data_store.setdefault(user_id, []).append(wallet)
        save_data()

    await update.message.reply_text(f"✅ Added {chain.upper()} ({wallet_type})")

async def getinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with data_lock:
        wallets = data_store.get(user_id, [])

    if not wallets:
        await update.message.reply_text("No wallets set.")
        return

    msg = "📂 Your Wallets:\n\n"
    for i, w in enumerate(wallets):
        msg += f"{i+1}. {w['chain'].upper()} → {w['destination']}\n"
    await update.message.reply_text(msg)

async def removewallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with data_lock:
        if user_id not in data_store or not data_store[user_id]:
            await update.message.reply_text("No wallets to remove.")
            return
        data_store[user_id].pop()
        if not data_store[user_id]:
            del data_store[user_id]
        save_data()

    await update.message.reply_text("🗑 Last wallet removed.")

# ---------------- SWEEPER ----------------
MAX_CONCURRENT = 10

async def process_wallet(user_id, wallet, semaphore):
    async with semaphore:
        try:
            secret = decrypt(wallet["secret"])
            chain_func = {
                "solana": solana.forward,
                "ethereum": ethereum.forward,
                "bsc": bsc.forward
            }[wallet["chain"]]

            await asyncio.to_thread(chain_func, secret, wallet["destination"], user_id)
        except Exception as e:
            logger.error(f"Error processing wallet for user {user_id}: {e}")

async def sweeper_loop():
    logger.info("🚀 Sweeper loop started")
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    while True:
        tasks = []
        with data_lock:
            current_wallets = list(data_store.items())

        for user_id, wallets in current_wallets:
            for wallet in wallets:
                tasks.append(process_wallet(user_id, wallet, semaphore))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await asyncio.sleep(1)  # Adjust interval as needed

# ---------------- MAIN ----------------
async def main():
    load_data()
    
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("setwallet", setwallet))
    application.add_handler(CommandHandler("getinfo", getinfo))
    application.add_handler(CommandHandler("removewallet", removewallet))

    # Start background sweeper
    asyncio.create_task(sweeper_loop())

    logger.info("🤖 Telegram bot starting...")
    
    await application.initialize()
    await application.start()
    
    # Run polling
    await application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )
    
    # Keep the bot running
    await asyncio.Event().wait()


if __name__ == "__main__":
    logger.info(f"Starting on port {PORT}")

    # Flask in background thread
    flask_thread = threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0", 
            port=PORT, 
            debug=False,
            use_reloader=False
        ),
        daemon=True
    )
    flask_thread.start()

    # Run the async bot
    asyncio.run(main())