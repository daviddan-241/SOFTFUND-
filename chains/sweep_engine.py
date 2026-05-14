"""
Smart Sweep Engine — detects errors, picks the right strategy, retries relentlessly.
Like a detective: if one approach fails, it tries another until the money is gone.
"""
import time
import logging
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
class ErrorKind:
    NONCE          = "nonce"
    GAS_PRICE      = "gas_price"
    GAS_LIMIT      = "gas_limit"
    NO_GAS_FUNDS   = "no_gas_funds"
    NO_BALANCE     = "no_balance"
    REVERT         = "revert"
    NETWORK        = "network"
    RATE_LIMIT     = "rate_limit"
    DUPLICATE      = "duplicate"
    BLOCKHASH      = "blockhash"
    UNKNOWN        = "unknown"

def classify(err: str) -> str:
    e = err.lower()
    if "nonce too low" in e or "nonce" in e:                     return ErrorKind.NONCE
    if "replacement transaction underpriced" in e:               return ErrorKind.GAS_PRICE
    if "transaction underpriced" in e or "underpriced" in e:     return ErrorKind.GAS_PRICE
    if "out of gas" in e or "gas too low" in e:                  return ErrorKind.GAS_LIMIT
    if "insufficient funds for gas" in e or "insufficient fee" in e: return ErrorKind.NO_GAS_FUNDS
    if "insufficient" in e and "balance" in e:                   return ErrorKind.NO_BALANCE
    if "execution reverted" in e or "revert" in e:               return ErrorKind.REVERT
    if "connection" in e or "timeout" in e or "network" in e or "eof" in e or "read error" in e: return ErrorKind.NETWORK
    if "429" in e or "rate limit" in e or "too many" in e:       return ErrorKind.RATE_LIMIT
    if "already known" in e or "known transaction" in e:         return ErrorKind.DUPLICATE
    if "blockhash" in e or "block hash" in e:                    return ErrorKind.BLOCKHASH
    return ErrorKind.UNKNOWN

# ── EVM smart sweep ──────────────────────────────────────────────
def smart_sweep_evm(
    account, destination: str, chain_id: int, native_symbol: str,
    token_map: dict, rpc_pool: list, config_rpc: str,
) -> list:
    """
    Sweep native coin + all tokens.
    Detects errors, adjusts strategy, rotates RPCs, retries up to 4 times per asset.
    """
    results = []
    all_rpcs = [config_rpc] + [r for r in rpc_pool if r != config_rpc]

    def make_w3(idx=0):
        url = all_rpcs[idx % len(all_rpcs)]
        return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 20}))

    # ── Token sweep ──────────────────────────────────────────────
    from chains.tokens import ERC20_ABI
    for symbol, token_addr in token_map.items():
        addr = account.address
        dest = Web3.to_checksum_address(destination)
        gas_multiplier = 1.0
        gas_limit_base = 100_000
        rpc_idx = 0

        for attempt in range(4):
            try:
                w3 = make_w3(rpc_idx)
                contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
                balance = contract.functions.balanceOf(addr).call()
                if balance == 0:
                    break  # nothing here, skip

                try:
                    decimals = contract.functions.decimals().call()
                except Exception:
                    decimals = 18
                human = balance / (10 ** decimals)

                gas_price = int(w3.eth.gas_price * gas_multiplier)
                gas_limit = int(gas_limit_base)
                native_bal = w3.eth.get_balance(addr)
                fee = gas_price * gas_limit

                if native_bal < fee:
                    results.append({
                        "asset": symbol, "status": "skip", "amount": human,
                        "tx_hash": None,
                        "error": f"Need {Web3.from_wei(fee,'ether'):.6f} {native_symbol} for gas (have {Web3.from_wei(native_bal,'ether'):.6f})",
                    })
                    break

                nonce = w3.eth.get_transaction_count(addr, "pending")
                tx = contract.functions.transfer(dest, balance).build_transaction({
                    "chainId": chain_id, "gas": gas_limit,
                    "gasPrice": gas_price, "nonce": nonce,
                })
                signed = account.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
                results.append({"asset": symbol, "status": "success", "amount": human, "tx_hash": tx_hash, "error": None})
                logger.info(f"[SMART] Swept {human:.4f} {symbol} tx={tx_hash[:12]}...")
                time.sleep(0.2)
                break

            except Exception as e:
                err_str = str(e)
                kind = classify(err_str)
                logger.warning(f"[SMART] {symbol} attempt {attempt+1}: {kind} — {err_str[:80]}")

                if kind == ErrorKind.NO_GAS_FUNDS:
                    results.append({"asset": symbol, "status": "skip", "amount": 0, "tx_hash": None,
                                    "error": f"Insufficient {native_symbol} for gas"})
                    break
                elif kind == ErrorKind.NO_BALANCE or kind == ErrorKind.REVERT:
                    # Token might be paused / blacklisted / transfer restricted
                    results.append({"asset": symbol, "status": "skip", "amount": 0, "tx_hash": None,
                                    "error": f"Transfer restricted or zero balance: {err_str[:60]}"})
                    break
                elif kind == ErrorKind.GAS_PRICE:
                    gas_multiplier = min(gas_multiplier * 1.4, 5.0)
                elif kind == ErrorKind.GAS_LIMIT:
                    gas_limit_base = min(gas_limit_base * 2, 500_000)
                elif kind == ErrorKind.NONCE:
                    time.sleep(1)  # wait for nonce to settle
                elif kind == ErrorKind.NETWORK:
                    rpc_idx += 1
                    time.sleep(1)
                elif kind == ErrorKind.RATE_LIMIT:
                    time.sleep(3 * (attempt + 1))
                elif kind == ErrorKind.DUPLICATE:
                    results.append({"asset": symbol, "status": "skip", "amount": 0, "tx_hash": None,
                                    "error": "Transaction already pending"})
                    break
                else:
                    gas_multiplier = min(gas_multiplier * 1.2, 3.0)
                    time.sleep(1.5 * (attempt + 1))

                if attempt == 3:
                    results.append({"asset": symbol, "status": "error", "amount": 0,
                                    "tx_hash": None, "error": err_str[:150]})

    # ── Native sweep ─────────────────────────────────────────────
    gas_multiplier = 1.0
    rpc_idx = 0
    addr = account.address
    dest = Web3.to_checksum_address(destination)

    for attempt in range(4):
        try:
            w3 = make_w3(rpc_idx)
            balance = w3.eth.get_balance(addr)
            gas_price = int(w3.eth.gas_price * gas_multiplier)
            gas_limit = 21_000
            fee = gas_price * gas_limit

            if balance <= fee:
                results.append({
                    "asset": native_symbol, "status": "skip",
                    "amount": float(Web3.from_wei(balance, "ether")),
                    "tx_hash": None,
                    "error": f"Balance {Web3.from_wei(balance,'ether'):.8f} {native_symbol} ≤ gas fee",
                })
                break

            value = balance - fee
            amount = float(Web3.from_wei(value, "ether"))
            nonce  = w3.eth.get_transaction_count(addr, "pending")

            tx = {"to": dest, "value": value, "gas": gas_limit,
                  "gasPrice": gas_price, "nonce": nonce, "chainId": chain_id}
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
            results.append({"asset": native_symbol, "status": "success",
                            "amount": amount, "tx_hash": tx_hash, "error": None})
            logger.info(f"[SMART] Swept {amount:.8f} {native_symbol} tx={tx_hash[:12]}...")
            break

        except Exception as e:
            err_str = str(e)
            kind = classify(err_str)
            logger.warning(f"[SMART] {native_symbol} attempt {attempt+1}: {kind} — {err_str[:80]}")

            if kind == ErrorKind.NO_GAS_FUNDS or kind == ErrorKind.NO_BALANCE:
                results.append({"asset": native_symbol, "status": "skip", "amount": 0,
                                "tx_hash": None, "error": err_str[:100]})
                break
            elif kind in (ErrorKind.GAS_PRICE, ErrorKind.GAS_LIMIT):
                gas_multiplier = min(gas_multiplier * 1.4, 5.0)
            elif kind == ErrorKind.NONCE:
                time.sleep(1.5)
            elif kind == ErrorKind.NETWORK:
                rpc_idx += 1; time.sleep(1)
            elif kind == ErrorKind.RATE_LIMIT:
                time.sleep(4 * (attempt + 1))
            elif kind == ErrorKind.DUPLICATE:
                results.append({"asset": native_symbol, "status": "skip", "amount": 0,
                                "tx_hash": None, "error": "Already pending"})
                break
            else:
                gas_multiplier = min(gas_multiplier * 1.2, 3.0)
                time.sleep(2 * (attempt + 1))

            if attempt == 3:
                results.append({"asset": native_symbol, "status": "error", "amount": 0,
                                "tx_hash": None, "error": err_str[:150]})

    return results


# ── Solana smart sweep ───────────────────────────────────────────
def smart_sweep_solana(keypair, destination: str, user_id: int) -> list:
    """
    Sweep SOL + all SPL tokens.
    Retries with fresh blockhash, rotates RPCs on network errors.
    """
    from solana.rpc.api import Client
    from solana.rpc.types import TxOpts
    from solders.pubkey import Pubkey
    from solders.system_program import transfer, TransferParams
    from solders.transaction import Transaction
    from config import SOLANA_RPC_URL

    results = []
    rpc_pool = [SOLANA_RPC_URL] + [r for r in SOL_RPCS if r != SOLANA_RPC_URL]

    def make_client(idx=0):
        return Client(rpc_pool[idx % len(rpc_pool)])

    LAMPORTS = 1_000_000_000
    MIN_RENT  = 890_880
    FEE_BUF   = 15_000
    TOKEN_PROG = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
    ATA_PROG   = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bT7")

    from_pk  = keypair.pubkey()
    dest_pk  = Pubkey.from_string(destination)
    rpc_idx  = 0

    # ── SPL tokens ──────────────────────────────────────────────
    for attempt in range(3):
        try:
            client = make_client(rpc_idx)
            resp   = client.get_token_accounts_by_owner_json_parsed(
                from_pk, {"programId": str(TOKEN_PROG)}
            )
            accounts = resp.value
            break
        except Exception as e:
            kind = classify(str(e))
            if kind == ErrorKind.NETWORK:
                rpc_idx += 1; time.sleep(1)
            else:
                time.sleep(2)
            if attempt == 2:
                logger.error(f"[SOL] Can't fetch token accounts: {e}")
                accounts = []

    for acc in accounts:
        try:
            parsed     = acc.account.data.parsed
            info       = parsed["info"]
            mint_str   = info["mint"]
            tok_amount = info["tokenAmount"]
            raw_amount = int(tok_amount.get("amount", 0))
            if raw_amount == 0:
                continue

            ui_amount  = float(tok_amount.get("uiAmount") or 0)
            decimals   = int(tok_amount.get("decimals", 9))
            mint_pk    = Pubkey.from_string(mint_str)
            src_ata    = Pubkey.from_string(str(acc.pubkey))

            seeds = [bytes(dest_pk), bytes(TOKEN_PROG), bytes(mint_pk)]
            dest_ata, _ = Pubkey.find_program_address(seeds, ATA_PROG)

            for attempt in range(4):
                try:
                    client = make_client(rpc_idx)
                    from spl.token.instructions import transfer_checked, TransferCheckedParams
                    from solders.instruction import Instruction, AccountMeta

                    instructions = []
                    dest_ata_info = client.get_account_info(dest_ata).value
                    if dest_ata_info is None:
                        keys = [
                            AccountMeta(pubkey=from_pk,    is_signer=True,  is_writable=True),
                            AccountMeta(pubkey=dest_ata,   is_signer=False, is_writable=True),
                            AccountMeta(pubkey=dest_pk,    is_signer=False, is_writable=False),
                            AccountMeta(pubkey=mint_pk,    is_signer=False, is_writable=False),
                            AccountMeta(pubkey=Pubkey.from_string("11111111111111111111111111111111"), is_signer=False, is_writable=False),
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
                    resp2 = client.send_transaction(tx, opts=TxOpts(skip_confirmation=False, preflight_commitment="confirmed"))
                    sig   = str(resp2.value)
                    results.append({"asset": mint_str[:8]+"...", "asset_full": mint_str,
                                    "status": "success", "amount": ui_amount,
                                    "tx_hash": sig, "error": None})
                    logger.info(f"[SOL SPL] Swept {ui_amount} mint={mint_str[:10]}...")
                    break

                except Exception as e:
                    err_str = str(e)
                    kind = classify(err_str)
                    if kind in (ErrorKind.BLOCKHASH, ErrorKind.NETWORK):
                        rpc_idx += 1; time.sleep(1)
                    elif kind == ErrorKind.RATE_LIMIT:
                        time.sleep(3 * (attempt + 1))
                    else:
                        time.sleep(1.5)
                    if attempt == 3:
                        results.append({"asset": mint_str[:8]+"...", "status": "error",
                                        "amount": ui_amount, "tx_hash": None, "error": err_str[:100]})

        except Exception as e:
            logger.error(f"[SOL SPL outer] {e}")

    # ── Native SOL ──────────────────────────────────────────────
    for attempt in range(4):
        try:
            client  = make_client(rpc_idx)
            balance = client.get_balance(from_pk).value
            send    = balance - MIN_RENT - FEE_BUF

            if send <= 0:
                results.append({"asset": "SOL", "status": "skip",
                                "amount": balance / LAMPORTS, "tx_hash": None,
                                "error": f"Balance {balance/LAMPORTS:.6f} SOL too low"})
                break

            blockhash = client.get_latest_blockhash().value.blockhash
            tx = Transaction.new_signed_with_payer(
                [transfer(TransferParams(from_pubkey=from_pk, to_pubkey=dest_pk, lamports=send))],
                payer=from_pk, signing_keypairs=[keypair], recent_blockhash=blockhash,
            )
            resp = client.send_transaction(tx, opts=TxOpts(skip_confirmation=False, preflight_commitment="confirmed"))
            sig  = str(resp.value)
            results.append({"asset": "SOL", "status": "success",
                            "amount": send / LAMPORTS, "tx_hash": sig, "error": None})
            logger.info(f"[SOL] Swept {send/LAMPORTS:.6f} SOL sig={sig[:12]}...")
            break

        except Exception as e:
            err_str = str(e)
            kind = classify(err_str)
            if kind in (ErrorKind.BLOCKHASH, ErrorKind.NETWORK):
                rpc_idx += 1; time.sleep(1)
            elif kind == ErrorKind.RATE_LIMIT:
                time.sleep(4 * (attempt + 1))
            else:
                time.sleep(2 * (attempt + 1))
            if attempt == 3:
                results.append({"asset": "SOL", "status": "error", "amount": 0,
                                "tx_hash": None, "error": err_str[:150]})

    return results
