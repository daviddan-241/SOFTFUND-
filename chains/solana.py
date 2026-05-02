from solders.pubkey import Pubkey
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer
from config import SOLANA_RPC_URL

client = Client(SOLANA_RPC_URL)

def forward(private_key: str, destination: str, user_id: int):
    try:
        # Generate Keypair from private key
        keypair = Keypair.from_secret_key(bytes(map(int, private_key.split(","))))

        # Get current balance
        balance = client.get_balance(keypair.public_key)["result"]["value"]

        # If balance is available, transfer it
        if balance > 0:
            txn = Transaction().add(
                transfer(
                    TransferParams(
                        from_pubkey=keypair.public_key,
                        to_pubkey=destination,
                        lamports=balance - 5000,  # Reserve a small amount for fees
                    )
                )
            )
            # Send transaction
            client.send_transaction(txn, keypair, opts=TxOpts(skip_confirmation=False))
            print(f"[Solana] Successfully forwarded funds for user {user_id}.")
    except Exception as e:
        print(f"[Solana Error] User ID {user_id}: {e}")