from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solders.transaction import Transaction
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from config import SOLANA_RPC_URL

client = Client(SOLANA_RPC_URL)

def forward(private_key: str, destination: str, user_id: int):
    try:
        # Convert private key string to bytes
        secret_key = bytes(map(int, private_key.split(",")))
        keypair = Keypair.from_bytes(secret_key)

        from_pubkey = keypair.pubkey()
        to_pubkey = Pubkey.from_string(destination)

        # Get balance
        balance_resp = client.get_balance(from_pubkey)
        balance = balance_resp.value

        if balance > 5000:
            txn = Transaction.new_signed_with_payer(
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

            client.send_transaction(txn, opts=TxOpts(skip_confirmation=False))
            print(f"[Solana] Successfully forwarded funds for user {user_id}.")
        else:
            print(f"[Solana] Not enough balance for user {user_id}.")

    except Exception as e:
        print(f"[Solana Error] User ID {user_id}: {e}")