import base58
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from config import SOLANA_RPC_URL
from chains.sweep_engine import smart_sweep_solana, SOL_RPCS

def keypair_from_secret(secret: str) -> Keypair:
    s = secret.strip()
    if "," in s:
        return Keypair.from_bytes(bytes(map(int, s.split(","))))
    try:
        raw = base58.b58decode(s)
        if len(raw) == 64:
            return Keypair.from_bytes(raw)
        if len(raw) == 32:
            return Keypair.from_seed(raw)
    except Exception:
        pass
    if len(s.split()) >= 12:
        try:
            from mnemonic import Mnemonic
            seed = Mnemonic("english").to_seed(s)[:32]
            return Keypair.from_seed(seed)
        except Exception:
            pass
    return Keypair.from_bytes(bytes.fromhex(s))

def get_balance(address: str) -> float:
    for rpc in [SOLANA_RPC_URL] + SOL_RPCS:
        try:
            client = Client(rpc)
            return client.get_balance(Pubkey.from_string(address)).value / 1_000_000_000
        except Exception:
            continue
    return 0.0

def forward(secret: str, destination: str, user_id: int) -> list:
    try:
        keypair = keypair_from_secret(secret)
    except Exception as e:
        return [{"chain": "solana", "asset": "SOL", "status": "error",
                 "amount": 0, "tx_hash": None, "error": f"Key parse error: {e}"}]

    results = smart_sweep_solana(keypair, destination, user_id)
    return [{"chain": "solana", **r} for r in results]
