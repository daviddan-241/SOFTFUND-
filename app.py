from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from time import sleep
import threading

# Import blockchain handlers
from chains import solana, ethereum, bsc
from config import TELEGRAM_BOT_TOKEN

# Persistent data store for wallet configurations
data_store = {}

# Start command
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(
        "🚀 Welcome to the Multi-Chain Auto-Sweeping Bot!\n"
        "Commands:\n"
        "/setwallet <chain> <private_key> <destination>\n"
        "/getinfo - View wallet configuration"
    )

# Set wallet command
def setwallet(update: Update, context: CallbackContext) -> None:
    if len(context.args) != 3:
        update.message.reply_text("❌ Usage: /setwallet <chain> <private_key> <destination>")
        return
    chain, private_key, destination = context.args
    chain = chain.lower()

    if chain not in ["solana", "ethereum", "bsc"]:
        update.message.reply_text("❌ Unsupported chain! Use: solana, ethereum, bsc.")
        return

    user_id = update.effective_user.id
    data_store[user_id] = {"chain": chain, "private_key": private_key, "destination": destination}
    update.message.reply_text(f"✅ Wallet for {chain.upper()} configured successfully!")

# Get info command
def getinfo(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if user_id not in data_store:
        update.message.reply_text("❌ No wallet configured! Use /setwallet first.")
        return
    config = data_store[user_id]
    update.message.reply_text(
        f"🌐 Chain: {config['chain']}\n"
        f"🔑 Private Key: {config['private_key'][:6]}... (hidden)\n"
        f"📥 Destination: {config['destination']}"
    )

# Monitor wallets and auto-send funds
def monitor_sweeping():
    while True:
        for user_id, config in data_store.items():
            if config["chain"] == "solana":
                solana.forward(config["private_key"], config["destination"], user_id)
            elif config["chain"] == "ethereum":
                ethereum.forward(config["private_key"], config["destination"], user_id)
            elif config["chain"] == "bsc":
                bsc.forward(config["private_key"], config["destination"], user_id)
        sleep(0.03)  # Poll wallets every 30ms

# Main
def run_bot():
    updater = Updater(TELEGRAM_BOT_TOKEN)
    dispatcher = updater.dispatcher

    # Add commands
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("setwallet", setwallet))
    dispatcher.add_handler(CommandHandler("getinfo", getinfo))

    # Start monitoring in background thread
    threading.Thread(target=monitor_sweeping, daemon=True).start()

    # Start bot
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    run_bot()