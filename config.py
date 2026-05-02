import os

# Telegram Bot Token
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "your-telegram-token")

# RPC URLs for blockchains
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
ETH_RPC_URL = os.getenv("ETH_RPC_URL", "https://mainnet.infura.io/v3/YOUR_INFURA_PROJECT_ID")
BSC_RPC_URL = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")