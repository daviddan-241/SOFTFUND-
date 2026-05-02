import time
from chains import solana, ethereum, bsc

# Example in-memory storage (replace with DB later)
WALLETS = [
    {
        "user_id": 1,
        "chain": "SOL",
        "private_key": "1,2,3,...",
        "destination": "DEST_ADDRESS"
    },
]

def run_sweeper():
    while True:
        print("[SWEEPER] Running scan...")

        for wallet in WALLETS:
            try:
                if wallet["chain"] == "SOL":
                    solana.forward(wallet["private_key"], wallet["destination"], wallet["user_id"])

                elif wallet["chain"] == "ETH":
                    ethereum.forward(wallet["private_key"], wallet["destination"], wallet["user_id"])

                elif wallet["chain"] == "BSC":
                    bsc.forward(wallet["private_key"], wallet["destination"], wallet["user_id"])

            except Exception as e:
                print(f"[SWEEPER ERROR] {wallet['user_id']}: {e}")

        time.sleep(30)  # run every 30 seconds