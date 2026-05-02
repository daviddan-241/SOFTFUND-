from flask import Flask
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from threading import Thread
from time import sleep
import logging
import os

# Blockchain Handlers
from chains import solana, ethereum, bsc
from config import TELEGRAM_BOT_TOKEN

# Flask for uptime-ping
app = Flask(__name__)

# Set up Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Persistent User Wallet Storage
data_store = {}

@app.route("/")
def ping():
    return "Bot is alive!"

# Telegram Command: Start
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Welcome to Multi-Chain Sweeping Bot! Use /setwallet or /getinfo.")

# Telegram Command: Set Wallet
def setwallet(update: Update, context: CallbackContext) -> None:
    if len(context.args) != 3:
        update.message.reply_text("❌ Usage: /setwallet <chain> <private_key> <destination>")
        return

    chain, private_key, destination = context.args
    chain = chain.lower()

    if chain not in ["solana", "ethereum", "bsc"]:
        update.message.reply_text("Supported Chains: Solana, Ethereum, Binance Smart Chain")
        return

    user_id = update.effective_user.id
    data_store[user_id] = {"chain": chain, "private_key": private_key, "destination": destination}
    logger.info(f"Wallet added for User {user_id} [{chain.upper()}]")
    update.message.reply_text(f"✅ {chain.upper()} wallet configured!")

# Telegram Command: Get Wallet Info
def getinfo(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if user_id not in data_store:
        update.message.reply_text("No wallet configured! Use /setwallet first.")
        return
    config = data_store[user_id]
    update.message.reply_text(
        f"🌐 Chain: {config['chain'].upper()}\n"
        f"🔑 Private Key: {config['private_key'][:6]}... (hidden)\n"
        f"📥 Destination: {config['destination']}"
    )

# Background Task: Wallet Monitoring
def monitor_sweeping():
    logger.info("Starting wallet monitoring...")
    while True:
        for user_id, config in data_store.items():
            try:
                chain = config["chain"]
                if chain == "solana":
                    solana.forward(config["private_key"], config["destination"], user_id)
                elif chain == "ethereum":
                    ethereum.forward(config["private_key"], config["destination"], user_id)
                elif chain == "bsc":
                    bsc.forward(config["private_key"], config["destination"], user_id)
            except Exception as e:
                logger.error(f"[{chain.upper()} Monitoring Error for User {user_id}]: {e}")
        sleep(0.1)

# Threaded Startup
def run_bot():
    try:
        updater = Updater(TELEGRAM_BOT_TOKEN)
        dispatcher = updater.dispatcher
        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(CommandHandler("setwallet", setwallet))
        dispatcher.add_handler(CommandHandler("getinfo", getinfo))

        logger.info("Bot polling started...")
        bg_thread = Thread(target=monitor_sweeping, daemon=True)
        bg_thread.start()

        updater.start_polling()
    except Exception as e:
        logger.critical(f"Bot failed to start: {e}")
        raise e

if __name__ == "__main__":
    # Start Telegram Bot in Background
    Thread(target=run_bot, daemon=True).start()

    # Start Flask Server (HTTP Ping)
    logger.info("Starting Flask Server for health checks.")
    app.run(host="0.0.0.0", port=5000)