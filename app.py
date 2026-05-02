from flask import Flask
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from threading import Thread
from time import sleep
import logging

# Import blockchain handlers
from chains import solana, ethereum, bsc
from config import TELEGRAM_BOT_TOKEN

# Flask for UptimeRobot pings
app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Persistent data store for configurations
data_store = {}

# Flask route for "ping"
@app.route("/")
def ping():
    return "Bot is Alive!"

# Command: Start
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(
        "🚀 Welcome to the Multi-Chain Auto-Sweeper Bot!\n"
        "Commands:\n"
        "/setwallet <chain> <private_key> <destination>\n"
        "/getinfo\n"
    )

# Command: Set Wallet
def setwallet(update: Update, context: CallbackContext) -> None:
    if len(context.args) != 3:
        update.message.reply_text("❌ Usage: /setwallet <chain> <private_key> <destination>")
        return

    chain, private_key, destination = context.args
    chain = chain.lower()

    if chain not in ["solana", "ethereum", "bsc"]:
        update.message.reply_text("❌ Unsupported chain! Please use: solana, ethereum, bsc.")
        return

    user_id = update.effective_user.id
    data_store[user_id] = {"chain": chain, "private_key": private_key, "destination": destination}
    update.message.reply_text(f"✅ {chain.upper()} wallet configured successfully!")
    logger.info(f"User {user_id} configured wallet for {chain.upper()}.")

# Command: Get Info
def getinfo(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if user_id not in data_store:
        update.message.reply_text("❌ No wallet configured! Use /setwallet.")
        return

    config = data_store[user_id]
    update.message.reply_text(
        f"🌐 Chain: {config['chain']}\n"
        f"🔑 Private Key: {config['private_key'][:6]}... (hidden)\n"
        f"📥 Destination: {config['destination']}"
    )
    logger.info(f"User {user_id} viewed wallet information for {config['chain'].upper()}.")

# Monitor wallets and auto-send funds
def monitor_sweeping():
    logger.info("Starting wallet monitoring thread...")
    while True:
        for user_id, config in list(data_store.items()):
            try:
                if config["chain"] == "solana":
                    solana.forward(config["private_key"], config["destination"], user_id)
                elif config["chain"] == "ethereum":
                    ethereum.forward(config["private_key"], config["destination"], user_id)
                elif config["chain"] == "bsc":
                    bsc.forward(config["private_key"], config["destination"], user_id)
            except Exception as e:
                logger.error(f"Error while processing user {user_id}: {e}")
        sleep(0.1)  # Poll wallets every 100ms

# Main entry point
def run_bot():
    # Telegram Updater
    updater = Updater(TELEGRAM_BOT_TOKEN)
    dispatcher = updater.dispatcher

    # Add commands
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("setwallet", setwallet))
    dispatcher.add_handler(CommandHandler("getinfo", getinfo))

    # Start wallet monitoring thread
    Thread(target=monitor_sweeping, daemon=True).start()

    # Start the bot
    logger.info("Starting Telegram bot...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    # Start the bot in a new thread
    bot_thread = Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Start Flask for uptime monitoring
    logger.info("Starting Flask server for UptimeRobot...")
    app.run(host="0.0.0.0", port=5000)