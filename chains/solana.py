from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solders.transaction import Transaction
from config import SOLANA_RPC_URL

client = Client(SOLANA_RPC_URL)

def forward(private_key: str, destination: str, user_id: int):
    try:
        secret = bytes(map(int, private_key.split(",")))
        keypair = Keypair.from_bytes(secret)

        from_pubkey = keypair.pubkey()
        to_pubkey = Pubkey.from_string(destination)

        balance = client.get_balance(from_pubkey).value

        if balance <= 5000:
            print(f"[SOL] Not enough balance user {user_id}")
            return

        tx = Transaction.new_signed_with_payer(
            [
                transfer(
                    TransferParams(
                        from_pubkey=from_pubkey,
                        to_pubkey=to_pubkey,
                        lamports=balance - 5000
                    )
                )
            ],
            payer=from_pubkey,
            signing_keypairs=[keypair],
            recent_blockhash=client.get_latest_blockhash().value.blockhash,
        )

        client.send_transaction(tx, opts=TxOpts(skip_confirmation=False))

        print(f"[SOL] Forwarded user {user_id}")

    except Exception as e:
        print(f"[SOL ERROR] {user_id}: {e}")