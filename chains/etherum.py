from web3 import Web3
from config import ETH_RPC_URL

web3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))

def forward(private_key, destination, user_id):
    try:
        account = web3.eth.account.privateKeyToAccount(private_key)
        balance = web3.eth.get_balance(account.address)
        if balance > 0:
            tx = {
                "to": destination,
                "value": balance - web3.toWei(0.00021, "ether"),
                "gas": 21000,
                "gasPrice": web3.eth.gas_price,
                "nonce": web3.eth.get_transaction_count(account.address),
            }
            signed_tx = web3.eth.account.sign_transaction(tx, private_key)
            web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            print(f"[Ethereum] Forwarded funds for user {user_id}")
    except Exception as e:
        print(f"[Ethereum Error]: {str(e)}")