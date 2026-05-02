from web3 import Web3
from config import ETH_RPC_URL

w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))

def forward(private_key: str, destination: str, user_id: int):
    try:
        account = w3.eth.account.from_key(private_key)
        address = account.address

        balance = w3.eth.get_balance(address)

        if balance <= 21000:
            print(f"[ETH] Not enough balance user {user_id}")
            return

        gas_price = w3.eth.gas_price
        gas_limit = 21000
        fee = gas_price * gas_limit

        value = balance - fee

        if value <= 0:
            print(f"[ETH] Gas too high user {user_id}")
            return

        tx = {
            "to": destination,
            "value": value,
            "gas": gas_limit,
            "gasPrice": gas_price,
            "nonce": w3.eth.get_transaction_count(address),
            "chainId": 1,
        }

        signed_tx = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)

        print(f"[ETH] Sent {tx_hash.hex()} user {user_id}")

    except Exception as e:
        print(f"[ETH ERROR] {user_id}: {e}")