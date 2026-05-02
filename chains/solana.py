from solana.rpc.api import Client
from solana.account import Account
from solana.transaction import Transaction
from solana.system_program import transfer
from config import SOLANA_RPC_URL

client = Client(SOLANA_RPC_URL)

def forward(private_key, destination, user_id):
    try:
        account = Account(list(map(int, private_key.split(","))))
        balance = client.get_balance(account.public_key())["result"]["value"]
        if balance > 0:
            txn = Transaction().add(transfer(account.public_key(), destination, balance - 5000))
            client.send_transaction(txn, account)
            print(f"[Solana] Forwarded funds for user {user_id}")
    except Exception as e:
        print(f"[Solana Error]: {str(e)}")