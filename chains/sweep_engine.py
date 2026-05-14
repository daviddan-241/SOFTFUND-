"""
Smart Sweep Engine — parallel asset sweeping with intelligent error recovery.
Detects every error type, applies the right fix, rotates RPCs, and retries.
If there is truly no gas fee available it reports exactly what is needed.
"""
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from web3 import Web3

logger = logging.getLogger(__name__)

# ── Fallback RPC pools ───────────────────────────────────────────
ETH_RPCS = [
    "https://cloudflare-eth.com",
    "https://rpc.ankr.com/eth",
    "https://ethereum.publicnode.com",
    "https://eth.llamarpc.com",
    "https://eth-mainnet.public.blastapi.io",
]
BSC_RPCS = [
    "https://bsc-dataseed.binance.org/",
    "https://bsc-dataseed1.defibit.io/",
    "https://bsc-dataseed2.ninicoin.io/",
    "https://bsc-dataseed3.binance.org/",
    "https://rpc.ankr.com/bsc",
]
SOL_RPCS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-api.projectserum.com",
    "https://rpc.ankr.com/solana",
]

# ── Error classifier ─────────────────────────────────────────────
class EK:  # ErrorKind
    NONCE        = "nonce"
    GAS_PRICE    = "gas_price"
    GAS_LIMIT    = "gas_limit"
    NO_GAS_FUNDS = "no_gas_funds"
    NO_BALANCE   = "no_balance"
    REVERT       = "revert"
    NETWORK      = "network"
    RATE_LIMIT   = "rate_limit"
    DUPLICATE    = "duplicate"
    BLOCKHASH    = "blockhash"
    UNKNOWN      = "unknown"

def classify(err: str) -> str:
    e = err.lower()
    if "nonce too low" in e or ("nonce" in e and "low" in e):    return EK.NONCE
    if "replacement transaction underpriced" in e:               return EK.GAS_PRICE
    if "transaction underpriced" in e or "underpriced" in e:     return EK.GAS_PRICE
    if "out of gas" in e or "gas too low" in e:                  return EK.GAS_LIMIT
    if "insufficient funds for gas" in e or "insufficient fee" in e \
       or ("insufficient" in e and "gas" in e):                  return EK.NO_GAS_FUNDS
    if "insufficient" in e and "balance" in e:                   return EK.NO_BALANCE
    if "execution reverted" in e or "revert" in e:               return EK.REVERT
    if "connection" in e or "timeout" in e or "network" in e \
       or "eof" in e or "read error" in e or "connect" in e:     return EK.NETWORK
    if "429" in e or "rate limit" in e or "too many" in e:       return EK.RATE_LIMIT
    if "already known" in e or "known transaction" in e \
       or "already submitted" in e:                              return EK.DUPLICATE
    if "blockhash" in e or "block hash" in e:                    return EK.BLOCKHASH
    return EK.UNKNOWN


def _make_w3(url: str) -> Web3:
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))


# ── Smart gas estimation ─────────────────────────────────────────
def _estimate_gas_or_default(w3, tx_dict: dict, default: int) -> int:
    """Try to get the actual gas needed; fall back to default."""
    try:
        return max(w3.eth.estimate_gas(tx_dict) + 5_000, 21_000)
    except Exception:
        return default


def _min_gas_price(w3) -> int:
    """Return the lowest viable gas price (current or 1 gwei floor)."""
    try:
        return max(w3.eth.gas_price, Web3.to_wei(1, "gwei"))
    except Exception:
        return Web3.to_wei(1, "gwei")


# ── EVM token sweep (single token, with retries) ─────────────────
def _sweep_one_token(account, dest_addr, token_addr, symbol,
                     rpc_pool, chain_id, native_symbol) -> dict:
    gas_multiplier = 1.0
    gas_limit_base = 80_000
    rpc_idx        = 0
    addr           = account.address

    for attempt in range(4):
        try:
            from chains.tokens import ERC20_ABI
            w3       = _make_w3(rpc_pool[rpc_idx % len(rpc_pool)])
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
            balance  = contract.functions.balanceOf(addr).call()

            if balance == 0:
                return {"asset": symbol, "status": "skip", "amount": 0,
                        "tx_hash": None, "error": "zero balance"}

            try:
                decimals = contract.functions.decimals().call()
            except Exception:
                decimals = 18
            human = balance / (10 ** decimals)

            gas_price  = int(_min_gas_price(w3) * gas_multiplier)
            gas_limit  = int(gas_limit_base)
            native_bal = w3.eth.get_balance(addr)
            fee        = gas_price * gas_limit

            # ── No gas: try with the absolute minimum estimated gas ──
            if native_bal < fee:
                # estimate real gas needed (often much less than 80k)
                try:
                    estimated = _estimate_gas_or_default(w3, {
                        "from": addr, "to": Web3.to_checksum_address(token_addr),
                        "data": contract.encodeABI("transfer", [dest_addr, balance]),
                    }, gas_limit_base)
                    min_fee = gas_price * estimated
                    if native_bal >= min_fee:
                        gas_limit = estimated  # we can do it with less gas!
                    else:
                        need = Web3.from_wei(min_fee - native_bal, "ether")
                        have = Web3.from_wei(native_bal, "ether")
                        return {
                            "asset": symbol, "status": "no_gas", "amount": human,
                            "tx_hash": None,
                            "error": f"Need {float(need):.6f} more {native_symbol} for gas "
                                     f"(have {float(have):.6f}, need {float(Web3.from_wei(min_fee,'ether')):.6f})",
                        }
                except Exception:
                    need = Web3.from_wei(fee - native_bal, "ether")
                    return {
                        "asset": symbol, "status": "no_gas", "amount": human,
                        "tx_hash": None,
                        "error": f"Need {float(need):.6f} more {native_symbol} for gas",
                    }

            nonce  = w3.eth.get_transaction_count(addr, "pending")
            tx     = contract.functions.transfer(dest_addr, balance).build_transaction({
                "chainId": chain_id, "gas": gas_limit,
                "gasPrice": gas_price, "nonce": nonce,
            })
            signed   = account.sign_transaction(tx)
            tx_hash  = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
            return {"asset": symbol, "status": "success", "amount": human,
                    "tx_hash": tx_hash, "error": None}

        except Exception as e:
            err_str = str(e)
            kind    = classify(err_str)
            logger.warning(f"[EVM token] {symbol} attempt {attempt+1}: {kind} — {err_str[:80]}")

            if kind in (EK.NO_GAS_FUNDS, EK.NO_BALANCE):
                return {"asset": symbol, "status": "skip", "amount": 0,
                        "tx_hash": None, "error": err_str[:100]}
            elif kind == EK.REVERT:
                return {"asset": symbol, "status": "skip", "amount": 0,
                        "tx_hash": None,
                        "error": f"Transfer restricted (blacklisted/paused): {err_str[:60]}"}
            elif kind == EK.DUPLICATE:
                return {"asset": symbol, "status": "skip", "amount": 0,
                        "tx_hash": None, "error": "Already pending"}
            elif kind == EK.GAS_PRICE:
                gas_multiplier = min(gas_multiplier * 1.5, 6.0)
            elif kind == EK.GAS_LIMIT:
                gas_limit_base = min(gas_limit_base * 2, 500_000)
            elif kind == EK.NONCE:
                time.sleep(1.5)
            elif kind == EK.NETWORK:
                rpc_idx += 1; time.sleep(1)
            elif kind == EK.RATE_LIMIT:
                time.sleep(3 * (attempt + 1))
            else:
                gas_multiplier = min(gas_multiplier * 1.2, 3.0)
                time.sleep(1.5 * (attempt + 1))

    return {"asset": symbol, "status": "error", "amount": 0,
            "tx_hash": None, "error": "Max retries reached"}


# ── EVM smart sweep ──────────────────────────────────────────────
def smart_sweep_evm(account, destination: str, chain_id: int,
                    native_symbol: str, token_map: dict,
                    rpc_pool: list, config_rpc: str) -> list:
    """
    Sweep native coin + all tokens in parallel.
    Retries intelligently per error type; falls back across RPCs.
    """
    results   = []
    all_rpcs  = [config_rpc] + [r for r in rpc_pool if r != config_rpc]
    addr      = account.address
    dest_addr = Web3.to_checksum_address(destination)

    # ── Tokens in parallel (all at once, no sequential waiting) ──
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(
                _sweep_one_token,
                account, dest_addr, token_addr, symbol,
                all_rpcs, chain_id, native_symbol
            ): symbol
            for symbol, token_addr in token_map.items()
        }
        for fut in as_completed(futures, timeout=120):
            try:
                r = fut.result(timeout=30)
                if r["status"] != "skip" or r.get("amount", 0) > 0:
                    results.append(r)
                    if r["status"] == "success":
                        logger.info(f"[SWEEP ✓] {r['amount']:.4f} {r['asset']} tx={str(r['tx_hash'])[:12]}...")
            except Exception as e:
                sym = futures[fut]
                results.append({"asset": sym, "status": "error", "amount": 0,
                                 "tx_hash": None, "error": str(e)[:100]})

    # ── Native coin ──────────────────────────────────────────────
    gas_multiplier = 1.0
    rpc_idx        = 0

    for attempt in range(4):
        try:
            w3         = _make_w3(all_rpcs[rpc_idx % len(all_rpcs)])
            balance    = w3.eth.get_balance(addr)
            gas_price  = int(_min_gas_price(w3) * gas_multiplier)
            gas_limit  = 21_000
            fee        = gas_price * gas_limit

            if balance <= fee:
                results.append({
                    "asset": native_symbol, "status": "skip",
                    "amount": float(Web3.from_wei(balance, "ether")),
                    "tx_hash": None,
                    "error": f"Balance {float(Web3.from_wei(balance,'ether')):.8f} {native_symbol} ≤ gas fee "
                             f"({float(Web3.from_wei(fee,'ether')):.8f} {native_symbol})",
                })
                break

            value  = balance - fee
            nonce  = w3.eth.get_transaction_count(addr, "pending")
            tx     = {"to": dest_addr, "value": value, "gas": gas_limit,
                      "gasPrice": gas_price, "nonce": nonce, "chainId": chain_id}
            signed = account.sign_transaction(tx)
            txh    = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
            results.append({"asset": native_symbol, "status": "success",
                            "amount": float(Web3.from_wei(value, "ether")),
                            "tx_hash": txh, "error": None})
            logger.info(f"[SWEEP ✓] {float(Web3.from_wei(value,'ether')):.8f} {native_symbol} tx={txh[:12]}...")
            break

        except Exception as e:
            kind = classify(str(e))
            if kind in (EK.NO_GAS_FUNDS, EK.NO_BALANCE):
                results.append({"asset": native_symbol, "status": "skip", "amount": 0,
                                 "tx_hash": None, "error": str(e)[:100]})
                break
            elif kind == EK.DUPLICATE:
                results.append({"asset": native_symbol, "status": "skip", "amount": 0,
                                 "tx_hash": None, "error": "Already pending"})
                break
            elif kind in (EK.GAS_PRICE, EK.GAS_LIMIT):
                gas_multiplier = min(gas_multiplier * 1.5, 6.0)
            elif kind == EK.NONCE:
                time.sleep(1.5)
            elif kind == EK.NETWORK:
                rpc_idx += 1; time.sleep(1)
            elif kind == EK.RATE_LIMIT:
                time.sleep(4 * (attempt + 1))
            else:
                gas_multiplier = min(gas_multiplier * 1.2, 3.0)
                time.sleep(2 * (attempt + 1))
            if attempt == 3:
                results.append({"asset": native_symbol, "status": "error", "amount": 0,
                                 "tx_hash": None, "error": str(e)[:150]})

    return results


# ── Solana smart sweep ───────────────────────────────────────────
def smart_sweep_solana(keypair, destination: str, user_id: int) -> list:
    """
    Sweep SOL + all SPL tokens with retry and RPC rotation.
    """
    from solana.rpc.api import Client
    from solana.rpc.types import TxOpts
    from solders.pubkey import Pubkey
    from solders.system_program import transfer, TransferParams
    from solders.transaction import Transaction
    from config import SOLANA_RPC_URL

    results  = []
    rpc_pool = [SOLANA_RPC_URL] + [r for r in SOL_RPCS if r != SOLANA_RPC_URL]

    def make_client(idx=0):
        return Client(rpc_pool[idx % len(rpc_pool)])

    LAMPORTS  = 1_000_000_000
    MIN_RENT  = 890_880      # min lamports to keep account alive
    FEE_BUF   = 15_000       # ~0.000015 SOL per tx
    TOKEN_PROG = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
    ATA_PROG   = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bT7")

    from_pk = keypair.pubkey()
    dest_pk = Pubkey.from_string(destination)
    rpc_idx = 0

    # ── SPL tokens ───────────────────────────────────────────────
    for attempt in range(3):
        try:
            client   = make_client(rpc_idx)
            resp     = client.get_token_accounts_by_owner_json_parsed(
                from_pk, {"programId": str(TOKEN_PROG)})
            accounts = resp.value
            break
        except Exception as e:
            kind = classify(str(e))
            if kind == EK.NETWORK:
                rpc_idx += 1
            time.sleep(1)
            if attempt == 2:
                accounts = []
                logger.error(f"[SOL] Can't fetch token accounts: {e}")

    for acc in accounts:
        try:
            parsed     = acc.account.data.parsed
            info       = parsed["info"]
            mint_str   = info["mint"]
            tok_amount = info["tokenAmount"]
            raw_amount = int(tok_amount.get("amount", 0))
            if raw_amount == 0:
                continue

            ui_amount = float(tok_amount.get("uiAmount") or 0)
            decimals  = int(tok_amount.get("decimals", 9))
            mint_pk   = Pubkey.from_string(mint_str)
            src_ata   = Pubkey.from_string(str(acc.pubkey))

            seeds    = [bytes(dest_pk), bytes(TOKEN_PROG), bytes(mint_pk)]
            dest_ata, _ = Pubkey.find_program_address(seeds, ATA_PROG)

            for attempt in range(4):
                try:
                    client = make_client(rpc_idx)
                    from spl.token.instructions import transfer_checked, TransferCheckedParams
                    from solders.instruction import Instruction, AccountMeta

                    instructions  = []
                    dest_ata_info = client.get_account_info(dest_ata).value
                    if dest_ata_info is None:
                        keys = [
                            AccountMeta(pubkey=from_pk,  is_signer=True,  is_writable=True),
                            AccountMeta(pubkey=dest_ata, is_signer=False, is_writable=True),
                            AccountMeta(pubkey=dest_pk,  is_signer=False, is_writable=False),
                            AccountMeta(pubkey=mint_pk,  is_signer=False, is_writable=False),
                            AccountMeta(pubkey=Pubkey.from_string("11111111111111111111111111111111"),
                                        is_signer=False, is_writable=False),
                            AccountMeta(pubkey=TOKEN_PROG, is_signer=False, is_writable=False),
                        ]
                        instructions.append(Instruction(ATA_PROG, b"", keys))

                    instructions.append(transfer_checked(TransferCheckedParams(
                        program_id=TOKEN_PROG, source=src_ata, mint=mint_pk,
                        dest=dest_ata, owner=from_pk, amount=raw_amount,
                        decimals=decimals, signers=[],
                    )))

                    blockhash = client.get_latest_blockhash().value.blockhash
                    tx = Transaction.new_signed_with_payer(
                        instructions, payer=from_pk,
                        signing_keypairs=[keypair], recent_blockhash=blockhash,
                    )
                    resp2 = client.send_transaction(
                        tx, opts=TxOpts(skip_confirmation=False,
                                        preflight_commitment="confirmed"))
                    sig = str(resp2.value)
                    results.append({
                        "asset": mint_str[:8] + "...", "asset_full": mint_str,
                        "status": "success", "amount": ui_amount,
                        "tx_hash": sig, "error": None,
                    })
                    logger.info(f"[SOL SPL ✓] {ui_amount} mint={mint_str[:10]}...")
                    break

                except Exception as e:
                    kind = classify(str(e))
                    if kind in (EK.BLOCKHASH, EK.NETWORK):
                        rpc_idx += 1; time.sleep(1)
                    elif kind == EK.RATE_LIMIT:
                        time.sleep(3 * (attempt + 1))
                    else:
                        time.sleep(1.5)
                    if attempt == 3:
                        results.append({
                            "asset": mint_str[:8] + "...", "status": "error",
                            "amount": ui_amount, "tx_hash": None, "error": str(e)[:100],
                        })
        except Exception as e:
            logger.error(f"[SOL SPL outer] {e}")

    # ── Native SOL ───────────────────────────────────────────────
    for attempt in range(4):
        try:
            client  = make_client(rpc_idx)
            balance = client.get_balance(from_pk).value

            # Check if enough SOL even for fee
            if balance < MIN_RENT + FEE_BUF:
                need = (MIN_RENT + FEE_BUF - balance) / LAMPORTS
                results.append({
                    "asset": "SOL", "status": "no_gas",
                    "amount": balance / LAMPORTS, "tx_hash": None,
                    "error": f"Need {need:.6f} more SOL for transaction fee "
                             f"(have {balance/LAMPORTS:.6f} SOL)",
                })
                break

            send      = balance - MIN_RENT - FEE_BUF
            blockhash = client.get_latest_blockhash().value.blockhash
            tx = Transaction.new_signed_with_payer(
                [transfer(TransferParams(from_pubkey=from_pk,
                                        to_pubkey=dest_pk,
                                        lamports=send))],
                payer=from_pk, signing_keypairs=[keypair],
                recent_blockhash=blockhash,
            )
            resp = client.send_transaction(
                tx, opts=TxOpts(skip_confirmation=False,
                                preflight_commitment="confirmed"))
            sig  = str(resp.value)
            results.append({
                "asset": "SOL", "status": "success",
                "amount": send / LAMPORTS, "tx_hash": sig, "error": None,
            })
            logger.info(f"[SOL ✓] {send/LAMPORTS:.6f} SOL sig={sig[:12]}...")
            break

        except Exception as e:
            kind = classify(str(e))
            if kind in (EK.BLOCKHASH, EK.NETWORK):
                rpc_idx += 1; time.sleep(1)
            elif kind == EK.RATE_LIMIT:
                time.sleep(4 * (attempt + 1))
            else:
                time.sleep(2 * (attempt + 1))
            if attempt == 3:
                results.append({"asset": "SOL", "status": "error", "amount": 0,
                                 "tx_hash": None, "error": str(e)[:150]})

    return results
