from flask import Flask, render_template, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    ConversationHandler, CallbackQueryHandler, MessageHandler, filters
)
import asyncio
import logging
import json
import os
import base64
import hashlib
import warnings
from cryptography.fernet import Fernet
import threading
from threading import RLock          # ← RLock allows re-entrant locking (fixes deadlock)
from datetime import datetime
from collections import deque

warnings.filterwarnings("ignore", category=UserWarning, module="telegram")

from chains import solana, ethereum, bsc
from config import TELEGRAM_BOT_TOKEN

app = Flask(__name__)
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_FILE    = "wallets.json"
HISTORY_FILE = "history.json"
_raw_key     = os.getenv("SECRET_KEY", "default-sweep-secret-key-2024")
PORT         = int(os.getenv("PORT", 5000))

# ── Fernet: derive a valid 32-byte key from whatever SECRET_KEY is set ──
try:
    cipher = Fernet(_raw_key.encode())
except Exception:
    _derived = base64.urlsafe_b64encode(hashlib.sha256(_raw_key.encode()).digest())
    cipher   = Fernet(_derived)

data_lock   = RLock()               # ← was Lock() — caused deadlock in save_data()
data_store  = {}
sweep_history = deque(maxlen=500)
sweeper_running = True
stats = {"sweeps_today": 0, "total_sweeps": 0, "errors_today": 0, "tokens_swept": 0}

CHAIN_MODULES  = {"solana": solana, "ethereum": ethereum, "bsc": bsc}
CHAIN_SYMBOLS  = {"solana": "SOL", "ethereum": "ETH", "bsc": "BNB"}

# ─────────────────────────────────────────────
# CRYPTO  (always safe — key is always valid)
# ─────────────────────────────────────────────
def encrypt(data: str) -> str:
    if not data:
        raise ValueError("Cannot encrypt empty secret")
    return cipher.encrypt(data.encode()).decode()

def decrypt(data: str) -> str:
    if not data:
        raise ValueError("Cannot decrypt empty secret")
    return cipher.decrypt(data.encode()).decode()

# ─────────────────────────────────────────────
# PERSISTENCE  (save_data does NOT acquire lock — callers hold it)
# ─────────────────────────────────────────────
def load_data():
    global data_store, sweep_history
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                data_store = {int(k): v for k, v in json.load(f).items()}
            logger.info(f"Loaded {sum(len(v) for v in data_store.values())} wallets")
        except Exception as e:
            logger.error(f"Load error: {e}")
            data_store = {}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                sweep_history = deque(json.load(f), maxlen=500)
        except Exception:
            pass

def save_data():
    """Write wallets.json — caller must hold data_lock (or not need it)."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data_store, f, indent=2)
    except Exception as e:
        logger.error(f"Save error: {e}")

def save_history():
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(list(sweep_history), f, indent=2)
    except Exception:
        pass

def record(entry: dict):
    entry["timestamp"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    sweep_history.appendleft(entry)
    if entry.get("status") == "success":
        stats["sweeps_today"]  += 1
        stats["total_sweeps"]  += 1
        if entry.get("asset") not in ("ETH", "BNB", "SOL", None):
            stats["tokens_swept"] += 1
    elif entry.get("status") == "error":
        stats["errors_today"] += 1
    save_history()

# ─────────────────────────────────────────────
# SWEEP HELPER
# ─────────────────────────────────────────────
async def run_sweep_wallet(uid, wallet) -> list:
    try:
        secret  = decrypt(wallet["secret"])
        mod     = CHAIN_MODULES[wallet["chain"]]
        results = await asyncio.to_thread(mod.forward, secret, wallet["destination"], uid)
        for r in results:
            r["user_id"]     = uid
            r["destination"] = wallet["destination"]
            if r.get("status") == "success":
                wallet["last_sweep"]  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                wallet["last_amount"] = r.get("amount", 0)
                wallet["last_asset"]  = r.get("asset", "")
            record(r)
        return results
    except Exception as e:
        logger.error(f"Sweep uid={uid}: {e}")
        return []

# ─────────────────────────────────────────────
# FLASK ROUTES
# ─────────────────────────────────────────────
@app.route("/")
def dashboard():
    return render_template("index.html")

@app.route("/api/stats")
def api_stats():
    with data_lock:
        total_wallets = sum(len(v) for v in data_store.values())
        active_chains = len({w["chain"] for wallets in data_store.values() for w in wallets})
    return jsonify({
        "total_wallets":   total_wallets,
        "active_chains":   active_chains,
        "sweeps_today":    stats["sweeps_today"],
        "total_sweeps":    stats["total_sweeps"],
        "errors_today":    stats["errors_today"],
        "tokens_swept":    stats["tokens_swept"],
        "sweeper_running": sweeper_running,
    })

@app.route("/api/wallets")
def api_wallets():
    rows = []
    with data_lock:
        for uid, wallets in data_store.items():
            for i, w in enumerate(wallets):
                rows.append({
                    "user_id":     uid,
                    "index":       i,
                    "chain":       w["chain"],
                    "symbol":      CHAIN_SYMBOLS.get(w["chain"], w["chain"].upper()),
                    "type":        w.get("type", "private_key"),
                    "label":       w.get("label", f"Wallet {i+1}"),
                    "destination": w["destination"],
                    "enabled":     w.get("enabled", True),
                    "last_sweep":  w.get("last_sweep"),
                    "last_amount": w.get("last_amount"),
                    "last_asset":  w.get("last_asset"),
                })
    return jsonify(rows)

@app.route("/api/wallets", methods=["POST"])
def api_add_wallet():
    try:
        body = request.json or {}
        uid  = int(body.get("user_id", 0))
        for f_ in ["chain", "secret", "destination"]:
            if not body.get(f_):
                return jsonify({"error": f"{f_} required"}), 400
        chain = body["chain"].lower()
        if chain not in CHAIN_MODULES:
            return jsonify({"error": "Unsupported chain"}), 400
        encrypted_secret = encrypt(body["secret"])
        wallet = {
            "chain":       chain,
            "type":        body.get("type", "private_key"),
            "secret":      encrypted_secret,
            "destination": body["destination"].strip(),
            "label":       (body.get("label") or f"{CHAIN_SYMBOLS[chain]} Wallet").strip(),
            "enabled":     True,
            "last_sweep":  None,
            "last_amount": None,
            "last_asset":  None,
        }
        with data_lock:
            data_store.setdefault(uid, []).append(wallet)
            save_data()                         # ← safe: RLock allows re-entry
        logger.info(f"Wallet added uid={uid} chain={chain} label={wallet['label']}")
        return jsonify({"ok": True, "label": wallet["label"]})
    except Exception as e:
        logger.error(f"api_add_wallet error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/wallets/<int:uid>/<int:idx>", methods=["DELETE"])
def api_remove_wallet(uid, idx):
    try:
        with data_lock:
            wallets = data_store.get(uid, [])
            if idx >= len(wallets):
                return jsonify({"error": "Not found"}), 404
            wallets.pop(idx)
            if not wallets:
                data_store.pop(uid, None)
            save_data()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/wallets/<int:uid>/<int:idx>/toggle", methods=["POST"])
def api_toggle_wallet(uid, idx):
    try:
        with data_lock:
            wallets = data_store.get(uid, [])
            if idx >= len(wallets):
                return jsonify({"error": "Not found"}), 404
            wallets[idx]["enabled"] = not wallets[idx].get("enabled", True)
            save_data()
            return jsonify({"enabled": wallets[idx]["enabled"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/toggle", methods=["POST"])
def api_toggle_sweeper():
    global sweeper_running
    sweeper_running = not sweeper_running
    return jsonify({"sweeper_running": sweeper_running})

@app.route("/api/sweep", methods=["POST"])
def api_manual_sweep():
    body       = request.json or {}
    uid_filter = body.get("user_id")
    with data_lock:
        snapshot = list(data_store.items())

    results = []
    async def do_sweeps():
        tasks = []
        for uid, wallets in snapshot:
            if uid_filter and int(uid_filter) != uid:
                continue
            for w in wallets:
                if w.get("enabled", True):
                    tasks.append(run_sweep_wallet(uid, w))
        if not tasks:
            return []
        return list(await asyncio.gather(*tasks, return_exceptions=True))

    try:
        loop = asyncio.new_event_loop()
        gathered = loop.run_until_complete(do_sweeps())
        loop.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    for res_list in gathered:
        if isinstance(res_list, list):
            results.extend(res_list)

    with data_lock:
        save_data()

    successes = [r for r in results if r.get("status") == "success"]
    return jsonify({"results": results, "success_count": len(successes), "total": len(results)})

@app.route("/api/history")
def api_history():
    limit = int(request.args.get("limit", 100))
    chain = request.args.get("chain")
    asset = request.args.get("asset")
    hist  = list(sweep_history)
    if chain:
        hist = [h for h in hist if h.get("chain") == chain]
    if asset:
        hist = [h for h in hist if h.get("asset", "").upper() == asset.upper()]
    return jsonify(hist[:limit])

@app.route("/api/balance", methods=["POST"])
def api_balance():
    """Single address balance check — now uses parallel token fetching."""
    body    = request.json or {}
    chain   = body.get("chain", "").lower()
    address = body.get("address", "").strip()
    if chain not in CHAIN_MODULES:
        return jsonify({"error": "Unsupported chain"}), 400
    try:
        mod        = CHAIN_MODULES[chain]
        native_bal = mod.get_balance(address)
        balances   = {"native": native_bal, "symbol": CHAIN_SYMBOLS[chain], "tokens": {}}
        if chain in ("ethereum", "bsc"):
            all_bals = mod.get_all_balances(address)   # parallel inside
            balances["tokens"] = {k: v for k, v in all_bals.items()
                                  if k != CHAIN_SYMBOLS[chain] and v > 0}
        return jsonify(balances)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/balances/<int:uid>")
def api_balances_for_user(uid: int):
    """
    Fast parallel balance check for all wallets owned by a user.
    Returns list of {label, chain, address, native, tokens, no_gas_assets}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FTE
    with data_lock:
        wallets = list(data_store.get(uid, []))
    if not wallets:
        return jsonify([])

    def _check_wallet(w):
        try:
            secret = decrypt(w["secret"])
            mod    = CHAIN_MODULES[w["chain"]]
            chain  = w["chain"]
            label  = w.get("label", "Wallet")
            if chain in ("ethereum", "bsc"):
                from eth_account import Account; Account.enable_unaudited_hdwallet_features()
                from web3 import Web3
                acct    = Account.from_mnemonic(secret) if len(secret.split()) >= 12 \
                          else Account.from_key(secret if secret.startswith("0x") else "0x" + secret)
                address = acct.address
                all_bals = mod.get_all_balances(address)
                native   = all_bals.pop(CHAIN_SYMBOLS[chain], 0.0)
                tokens   = {k: v for k, v in all_bals.items() if v > 0}
            else:
                from chains.solana import keypair_from_secret
                kp      = keypair_from_secret(secret)
                address = str(kp.pubkey())
                native  = mod.get_balance(address)
                tokens  = {}
            return {"label": label, "chain": chain, "address": address,
                    "native": native, "symbol": CHAIN_SYMBOLS[chain],
                    "tokens": tokens, "error": None}
        except Exception as e:
            return {"label": w.get("label","?"), "chain": w["chain"],
                    "address": "", "native": 0, "symbol": "", "tokens": {},
                    "error": str(e)[:80]}

    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_check_wallet, w): w for w in wallets}
        for fut in as_completed(futs, timeout=25):
            try:
                results.append(fut.result(timeout=5))
            except FTE:
                w = futs[fut]
                results.append({"label": w.get("label","?"), "chain": w["chain"],
                                 "error": "Timed out"})
            except Exception as e:
                w = futs[fut]
                results.append({"label": w.get("label","?"), "chain": w["chain"],
                                 "error": str(e)[:80]})
    return jsonify(results)

@app.route("/api/chains")
def api_chains():
    out = {}
    with data_lock:
        for chain in ["solana", "ethereum", "bsc"]:
            count = sum(1 for wallets in data_store.values() for w in wallets if w["chain"] == chain)
            out[chain] = {"symbol": CHAIN_SYMBOLS[chain], "wallet_count": count}
    return jsonify(out)

# ─────────────────────────────────────────────
# SWEEPER LOOP
# ─────────────────────────────────────────────
async def sweeper_loop():
    logger.info("Sweeper started (0.5s interval)")
    while True:
        if sweeper_running:
            with data_lock:
                snapshot = list(data_store.items())
            tasks = []
            for uid, wallets in snapshot:
                for w in wallets:
                    if w.get("enabled", True):
                        tasks.append(run_sweep_wallet(uid, w))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                with data_lock:
                    save_data()
        await asyncio.sleep(0.5)

# ─────────────────────────────────────────────
# TELEGRAM BOT
# ─────────────────────────────────────────────
CHAIN_SELECT, TYPE_SELECT, SECRET_INPUT, DEST_INPUT, LABEL_INPUT = range(5)

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Wallet",     callback_data="add_wallet"),
         InlineKeyboardButton("📋 My Wallets",     callback_data="my_wallets")],
        [InlineKeyboardButton("💰 Check Balances", callback_data="check_balances"),
         InlineKeyboardButton("⚡ Sweep Now",       callback_data="manual_sweep")],
        [InlineKeyboardButton("📊 Stats",          callback_data="stats"),
         InlineKeyboardButton("📜 History",        callback_data="history")],
        [InlineKeyboardButton("🗑 Remove Last",     callback_data="remove_last"),
         InlineKeyboardButton("⏯ Toggle Sweeper", callback_data="toggle")],
        [InlineKeyboardButton("❓ Help",           callback_data="help")],
    ])

def status_text():
    return "🟢 Running" if sweeper_running else "🔴 Stopped"

# ── /start ──────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with data_lock:
        wcount = sum(len(v) for v in data_store.values())
    await update.message.reply_text(
        f"⚡ *Multi-Chain Deep Sweeper*\n\n"
        f"Status: {status_text()}\nWallets: *{wcount}*\n"
        f"Chains: SOL · ETH · BNB\n"
        f"Sweeps every 0.5s — native coins *and* all tokens.\n\n"
        f"Tap ➕ Add Wallet to begin.",
        parse_mode="Markdown", reply_markup=main_kb()
    )

# ── Main inline keyboard handler (outside conversation) ──────────
async def main_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global sweeper_running
    query = update.callback_query
    await query.answer()
    d = query.data

    if d == "my_wallets":
        uid = query.from_user.id
        with data_lock:
            wallets = list(data_store.get(uid, []))
        if not wallets:
            await query.edit_message_text("No wallets yet. Tap ➕ Add Wallet.", reply_markup=main_kb())
            return
        lines = ["📋 *Your Wallets*\n"]
        for i, w in enumerate(wallets):
            icon = "✅" if w.get("enabled", True) else "⏸"
            last = (w.get("last_sweep") or "Never")[:16]
            amt  = f"{w['last_amount']:.4f} {w.get('last_asset','')}" if w.get("last_amount") else "—"
            lines.append(f"{icon} *{i+1}. {w.get('label','Wallet')}* [{w['chain'].upper()}]\n"
                         f"   Dest: `{w['destination'][:14]}...`\n   Last: {last} | {amt}")
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

    elif d == "check_balances":
        uid = query.from_user.id
        with data_lock:
            wallets = list(data_store.get(uid, []))
        if not wallets:
            await query.edit_message_text("No wallets to check.", reply_markup=main_kb()); return
        await query.edit_message_text("⏳ Checking balances (all chains in parallel)...")
        async def _bal_line(w):
            try:
                secret = decrypt(w["secret"])
                mod    = CHAIN_MODULES[w["chain"]]
                lbl    = w.get("label", "?")
                ch     = w["chain"]
                if ch in ("ethereum", "bsc"):
                    from eth_account import Account; Account.enable_unaudited_hdwallet_features()
                    from web3 import Web3
                    acct = Account.from_mnemonic(secret) if len(secret.split()) >= 12 \
                           else Account.from_key(secret if secret.startswith("0x") else "0x" + secret)
                    bals = await asyncio.wait_for(
                        asyncio.to_thread(mod.get_all_balances, acct.address), timeout=20)
                    parts = [f"*{lbl}* [{ch.upper()}]"]
                    parts += [f"  {s}: `{a:.6f}`" for s, a in list(bals.items())[:10] if a > 0]
                    if len(parts) == 1:
                        parts.append("  (no balance)")
                    return "\n".join(parts)
                else:
                    from chains.solana import keypair_from_secret
                    kp  = keypair_from_secret(secret)
                    bal = await asyncio.wait_for(
                        asyncio.to_thread(mod.get_balance, str(kp.pubkey())), timeout=10)
                    return f"*{lbl}* [SOL]: `{bal:.6f} SOL`"
            except asyncio.TimeoutError:
                return f"*{w.get('label','?')}*: Timed out (RPC slow)"
            except Exception as e:
                return f"*{w.get('label','?')}*: Error — {str(e)[:50]}"
        # All wallets checked simultaneously
        results_bal = await asyncio.gather(*[_bal_line(w) for w in wallets])
        lines = ["💰 *All Balances*\n"] + list(results_bal)
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

    elif d == "manual_sweep":
        await query.edit_message_text("⏳ Running deep sweep (all coins + tokens)...")
        uid = query.from_user.id
        with data_lock:
            wallets = list(data_store.get(uid, []))
        if not wallets:
            await query.edit_message_text("No wallets.", reply_markup=main_kb()); return
        all_res = []
        for w in wallets:
            if w.get("enabled", True):
                res = await run_sweep_wallet(uid, w)
                all_res.extend(res)
        with data_lock:
            save_data()
        success = [r for r in all_res if r.get("status") == "success"]
        lines = [f"✅ *Sweep Complete*\nSuccess: {len(success)} / {len(all_res)} ops\n"]
        for r in success[:15]:
            lines.append(f"• {r['asset']}: {r['amount']:.6f}")
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

    elif d == "stats":
        with data_lock:
            tw = sum(len(v) for v in data_store.values())
        text = (f"📊 *Statistics*\n\nTotal wallets: *{tw}*\n"
                f"Sweeps today: *{stats['sweeps_today']}*\nTokens swept: *{stats['tokens_swept']}*\n"
                f"Total sweeps: *{stats['total_sweeps']}*\nErrors today: *{stats['errors_today']}*\n"
                f"Sweeper: {status_text()}")
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_kb())

    elif d == "history":
        uid  = query.from_user.id
        hist = [h for h in sweep_history if h.get("user_id") == uid and h.get("status") == "success"][:10]
        if not hist:
            await query.edit_message_text("No successful sweeps yet.", reply_markup=main_kb()); return
        lines = ["📜 *Recent Sweeps*\n"] + [
            f"• {h.get('asset','?')}: {h.get('amount',0):.6f} | {h.get('timestamp','')[:16]}"
            for h in hist
        ]
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

    elif d == "remove_last":
        uid = query.from_user.id
        with data_lock:
            if uid in data_store and data_store[uid]:
                removed = data_store[uid].pop()
                if not data_store[uid]:
                    del data_store[uid]
                save_data()
                await query.edit_message_text(
                    f"🗑 Removed *{removed.get('label','wallet')}*",
                    parse_mode="Markdown", reply_markup=main_kb()
                )
            else:
                await query.edit_message_text("Nothing to remove.", reply_markup=main_kb())

    elif d == "toggle":
        sweeper_running = not sweeper_running
        await query.edit_message_text(f"Sweeper: {status_text()}", reply_markup=main_kb())

    elif d == "help":
        await query.edit_message_text(
            "❓ *Deep Sweeper — Help*\n\nSweeps *all* assets every 0.5 seconds:\n"
            "• Native coins: SOL, ETH, BNB\n"
            "• ERC-20 (ETH): USDT, USDC, DAI, SHIB + 30 more\n"
            "• BEP-20 (BSC): USDT, BUSD, CAKE, FLOKI + 25 more\n"
            "• SPL (Solana): all token accounts auto-detected\n\n"
            "*Commands:*\n/start /wallets /sweep /balances /history /stats /toggle /remove /help",
            parse_mode="Markdown", reply_markup=main_kb()
        )

    elif d == "back_main":
        await query.edit_message_text("⚡ *Multi-Chain Deep Sweeper*", parse_mode="Markdown", reply_markup=main_kb())

# ── Conversation: Add Wallet ─────────────────
async def conv_start_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Entry point: ➕ Add Wallet button → chain picker."""
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("◎ Solana",   callback_data="chain_solana"),
         InlineKeyboardButton("Ξ Ethereum", callback_data="chain_ethereum"),
         InlineKeyboardButton("⬡ BSC",      callback_data="chain_bsc")],
        [InlineKeyboardButton("✖ Cancel",   callback_data="conv_cancel")],
    ])
    await query.edit_message_text("*Select chain to add:*", parse_mode="Markdown", reply_markup=kb)
    return CHAIN_SELECT

async def chain_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    chain = q.data.replace("chain_", "")
    ctx.user_data["chain"] = chain
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Private Key", callback_data="type_private_key"),
         InlineKeyboardButton("📜 Seed Phrase", callback_data="type_seed_phrase")],
        [InlineKeyboardButton("✖ Cancel",       callback_data="conv_cancel")],
    ])
    await q.edit_message_text(f"Chain: *{chain.upper()}*\nChoose key type:", parse_mode="Markdown", reply_markup=kb)
    return TYPE_SELECT

async def type_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    key_type = q.data.replace("type_", "")
    ctx.user_data["type"] = key_type
    chain = ctx.user_data.get("chain", "")
    if key_type == "seed_phrase":
        hint = "12 or 24-word seed phrase"
    else:
        if chain == "solana":
            hint = "base58 private key, or comma-separated bytes"
        else:
            hint = "hex private key (with or without 0x)"
    await q.edit_message_text(
        f"✏️ Paste your *{'Seed Phrase' if key_type == 'seed_phrase' else 'Private Key'}*:\n_({hint})_\n\n"
        f"⚠️ This message will be deleted for security.",
        parse_mode="Markdown"
    )
    return SECRET_INPUT

async def receive_secret(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["secret"] = update.message.text.strip()
        try:
            await update.message.delete()
        except Exception:
            pass
        await update.message.reply_text(
            "📍 Paste the *destination address*\n_(all swept funds go here)_:",
            parse_mode="Markdown"
        )
        return DEST_INPUT
    except Exception as e:
        logger.error(f"receive_secret error: {e}")
        await update.message.reply_text(f"❌ Error: {e}\nTry /start to restart.")
        return ConversationHandler.END

async def receive_dest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        dest  = update.message.text.strip()
        chain = ctx.user_data.get("chain", "")
        sym   = CHAIN_SYMBOLS.get(chain, chain.upper())
        ctx.user_data["destination"] = dest
        await update.message.reply_text(
            f"🏷 Enter a *label* for this wallet (e.g. 'My {sym} Wallet')\nor type `skip`:",
            parse_mode="Markdown"
        )
        return LABEL_INPUT
    except Exception as e:
        logger.error(f"receive_dest error: {e}")
        await update.message.reply_text(f"❌ Error: {e}\nTry /start to restart.")
        return ConversationHandler.END

async def receive_label(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        uid    = update.effective_user.id
        raw    = update.message.text.strip()
        chain  = ctx.user_data.get("chain")
        secret = ctx.user_data.get("secret")
        dest   = ctx.user_data.get("destination")

        if not chain or not secret or not dest:
            await update.message.reply_text(
                "❌ Session lost. Please use /start and add the wallet again.",
                reply_markup=main_kb()
            )
            ctx.user_data.clear()
            return ConversationHandler.END

        sym   = CHAIN_SYMBOLS.get(chain, chain.upper())
        label = raw if raw.lower() != "skip" else f"{sym} Wallet"

        enc_secret = encrypt(secret)

        wallet = {
            "chain":       chain,
            "type":        ctx.user_data.get("type", "private_key"),
            "secret":      enc_secret,
            "destination": dest,
            "label":       label,
            "enabled":     True,
            "last_sweep":  None,
            "last_amount": None,
            "last_asset":  None,
        }

        with data_lock:
            data_store.setdefault(uid, []).append(wallet)
            save_data()                     # ← RLock allows this inside with data_lock

        logger.info(f"Wallet saved via bot uid={uid} chain={chain} label={label}")

        await update.message.reply_text(
            f"✅ *Wallet Added!*\n\n"
            f"Label: *{label}*\n"
            f"Chain: *{chain.upper()}*\n"
            f"Dest: `{dest[:20]}...`\n\n"
            f"🔄 Deep sweep (native + all tokens) starts immediately.",
            parse_mode="Markdown", reply_markup=main_kb()
        )
        ctx.user_data.clear()
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"receive_label error: {e}")
        await update.message.reply_text(
            f"❌ Failed to save wallet: {e}\n\nPlease try /start and add again.",
            reply_markup=main_kb()
        )
        ctx.user_data.clear()
        return ConversationHandler.END

async def conv_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Cancel button inside conversation."""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ Cancelled.", reply_markup=main_kb())
    else:
        await update.message.reply_text("❌ Cancelled.", reply_markup=main_kb())
    ctx.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_kb())
    return ConversationHandler.END

# ── Direct commands ──────────────────────────
async def cmd_wallets(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = u.effective_user.id
    with data_lock:
        wallets = list(data_store.get(uid, []))
    if not wallets:
        await u.message.reply_text("No wallets yet.", reply_markup=main_kb()); return
    lines = ["📋 *Your Wallets*\n"]
    for i, w in enumerate(wallets):
        lines.append(f"{i+1}. *{w.get('label','?')}* [{w['chain'].upper()}] → `{w['destination'][:12]}...`")
    await u.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

async def cmd_sweep(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = u.effective_user.id
    msg = await u.message.reply_text("⏳ Deep sweeping all assets...")
    with data_lock:
        wallets = list(data_store.get(uid, []))
    all_res = []
    for w in wallets:
        if w.get("enabled", True):
            res = await run_sweep_wallet(uid, w)
            all_res.extend(res)
    with data_lock:
        save_data()
    ok = [r for r in all_res if r.get("status") == "success"]
    if ok:
        lines = ["✅ *Swept:*"] + [f"• {r['asset']}: {r['amount']:.6f}" for r in ok[:15]]
        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    else:
        await msg.edit_text(f"✅ Checked {len(wallets)} wallets — no funds to move right now.")

async def cmd_toggle(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global sweeper_running
    sweeper_running = not sweeper_running
    await u.message.reply_text(f"Sweeper: {status_text()}", reply_markup=main_kb())

async def cmd_stats(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with data_lock:
        tw = sum(len(v) for v in data_store.values())
    await u.message.reply_text(
        f"📊 *Stats*\nWallets: {tw} | Sweeps today: {stats['sweeps_today']} | "
        f"Tokens: {stats['tokens_swept']} | Errors: {stats['errors_today']}\nSweeper: {status_text()}",
        parse_mode="Markdown", reply_markup=main_kb()
    )

async def cmd_history(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = u.effective_user.id
    hist = [h for h in sweep_history if h.get("user_id") == uid and h.get("status") == "success"][:10]
    if not hist:
        await u.message.reply_text("No history.", reply_markup=main_kb()); return
    lines = ["📜 *Sweeps*\n"] + [
        f"• {h.get('asset','?')}: {h.get('amount',0):.6f} | {h.get('timestamp','')[:16]}" for h in hist
    ]
    await u.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

async def cmd_remove(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = u.effective_user.id
    with data_lock:
        if uid in data_store and data_store[uid]:
            r = data_store[uid].pop()
            if not data_store[uid]:
                del data_store[uid]
            save_data()
            await u.message.reply_text(
                f"🗑 Removed *{r.get('label','wallet')}*", parse_mode="Markdown", reply_markup=main_kb()
            )
        else:
            await u.message.reply_text("Nothing to remove.", reply_markup=main_kb())

async def cmd_help(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "❓ *Commands*\n/start /wallets /sweep /balances /history /stats /toggle /remove /help",
        parse_mode="Markdown", reply_markup=main_kb()
    )

async def cmd_balances(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = u.effective_user.id
    with data_lock:
        wallets = list(data_store.get(uid, []))
    if not wallets:
        await u.message.reply_text("No wallets.", reply_markup=main_kb()); return
    msg = await u.message.reply_text("⏳ Fetching balances (parallel — usually 5-10s)...")

    async def _one(w):
        try:
            secret = decrypt(w["secret"])
            mod    = CHAIN_MODULES[w["chain"]]
            lbl    = w.get("label", "?")
            ch     = w["chain"]
            if ch in ("ethereum", "bsc"):
                from eth_account import Account; Account.enable_unaudited_hdwallet_features()
                from web3 import Web3
                acct = Account.from_mnemonic(secret) if len(secret.split()) >= 12 \
                       else Account.from_key(secret if secret.startswith("0x") else "0x" + secret)
                bals = await asyncio.wait_for(
                    asyncio.to_thread(mod.get_all_balances, acct.address), timeout=20)
                non_zero = {k: v for k, v in bals.items() if v > 0}
                if not non_zero:
                    return f"*{lbl}* [{ch.upper()}]: no balance"
                return f"*{lbl}* [{ch.upper()}]: " + " | ".join(f"{k}:{v:.4f}" for k, v in list(non_zero.items())[:8])
            else:
                from chains.solana import keypair_from_secret
                kp  = keypair_from_secret(secret)
                bal = await asyncio.wait_for(
                    asyncio.to_thread(mod.get_balance, str(kp.pubkey())), timeout=10)
                return f"*{lbl}* [SOL]: `{bal:.6f} SOL`"
        except asyncio.TimeoutError:
            return f"*{w.get('label','?')}*: Timed out"
        except Exception as e:
            return f"*{w.get('label','?')}*: Error — {str(e)[:40]}"

    lines = ["💰 *Balances*\n"] + list(await asyncio.gather(*[_one(w) for w in wallets]))
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")

# ─────────────────────────────────────────────
# MAIN  —  wire everything together
# ─────────────────────────────────────────────
async def main():
    load_data()
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # ── Conversation: Add Wallet (clean, isolated from main_button) ──
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_start_add, pattern="^add_wallet$")],
        states={
            CHAIN_SELECT:  [CallbackQueryHandler(chain_selected, pattern="^chain_(solana|ethereum|bsc)$"),
                            CallbackQueryHandler(conv_cancel,    pattern="^conv_cancel$")],
            TYPE_SELECT:   [CallbackQueryHandler(type_selected,  pattern="^type_(private_key|seed_phrase)$"),
                            CallbackQueryHandler(conv_cancel,    pattern="^conv_cancel$")],
            SECRET_INPUT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_secret)],
            DEST_INPUT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_dest)],
            LABEL_INPUT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_label)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(conv_cancel, pattern="^conv_cancel$"),
        ],
        per_message=False,
        allow_reentry=True,
    )

    # Register conversation FIRST (takes priority)
    application.add_handler(conv)

    # Register commands
    for cmd, fn in [
        ("start",    start),    ("wallets",  cmd_wallets), ("sweep",    cmd_sweep),
        ("toggle",   cmd_toggle),("stats",    cmd_stats),   ("history",  cmd_history),
        ("remove",   cmd_remove),("help",     cmd_help),    ("balances", cmd_balances),
    ]:
        application.add_handler(CommandHandler(cmd, fn))

    # Global inline-keyboard handler (for everything except add_wallet)
    application.add_handler(CallbackQueryHandler(main_button))

    asyncio.create_task(sweeper_loop())
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot ready")
    await asyncio.Event().wait()

if __name__ == "__main__":
    logger.info(f"Starting on port {PORT}")
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False),
        daemon=True,
    ).start()
    asyncio.run(main())
