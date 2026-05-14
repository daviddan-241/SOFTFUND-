"""
Token definitions and ERC-20 / BEP-20 sweep logic.
Used by both ethereum.py and bsc.py.
"""
from web3 import Web3

ERC20_ABI = [
    {"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
]

# ── Ethereum Mainnet tokens ──────────────────────────────────────
ETH_TOKENS = {
    # Stablecoins
    "USDT":  "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "USDC":  "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "DAI":   "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    "BUSD":  "0x4Fabb145d64652a948d72533023f6E7A623C7C53",
    "FRAX":  "0x853d955aCEf822Db058eb8505911ED77F175b99e",
    "TUSD":  "0x0000000000085d4780B73119b644AE5ecd22b376",
    "USDP":  "0x8E870D67F660D95d5be530380D0eC0bd388289E1",
    # Blue chips
    "WBTC":  "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
    "WETH":  "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "LINK":  "0x514910771AF9Ca656af840dff83E8264EcF986CA",
    "UNI":   "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
    "AAVE":  "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",
    "MKR":   "0x9f8F72aA9304c8B593d555F12eF6589cC3A579A2",
    "CRV":   "0xD533a949740bb3306d119CC777fa900bA034cd52",
    "SNX":   "0xC011a73ee8576Fb46F5E1c5751cA3B9Fe0af2a6F",
    "COMP":  "0xc00e94Cb662C3520282E6f5717214004A7f26888",
    "LDO":   "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32",
    # Meme coins
    "SHIB":  "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE",
    "PEPE":  "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
    "FLOKI": "0xcf0C122c6b73ff809C693DB761e7BaeBe62b6a2E",
    "BONE":  "0x9813037ee2218799597d83D4a5B6F3b6778218d9",
    "LEASH": "0x27C70Cd1946795B66be9d954418546998b546634",
    "KISHU": "0xA2b4C0Af19cC16a6CfAcCe81F192B024d625817D",
    "ELON":  "0x761D38e5ddf6ccf6Cf7c55759d5210750B5D60F3",
    "BABYDOGE": "0xAC57De9C1A09FeC648E93EB98875B212DB0d460B",
    # L2 / Other
    "ARB":   "0xB50721BCf8d664c30412Cfbc6cf7a15145234ad1",
    "OP":    "0x4200000000000000000000000000000000000042",
    "MATIC": "0x7D1AfA7B718fb893dB30A3aBc0Cfc608AaCfeBB0",
    "FTM":   "0x4E15361FD6b4BB609Fa63C81A2be19d873717870",
    "APE":   "0x4d224452801ACEd8B2F0aebE155379bb5D594381",
    "SAND":  "0x3845badAde8e6dFF049820680d1F14bD3903a5d0",
    "MANA":  "0x0F5D2fB29fb7d3CFeE444a200298f468908cC942",
    "AXS":   "0xBB0E17EF65F82Ab018d8EDd776e8DD940327B28b",
    "ENS":   "0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72",
    "1INCH": "0x111111111117dC0aa78b770fA6A738034120C302",
    "SUSHI": "0x6B3595068778DD592e39A122f4f5a5cF09C90fE2",
    "BAL":   "0xba100000625a3754423978a60c9317c58a424e3D",
}

# ── BSC Mainnet tokens ───────────────────────────────────────────
BSC_TOKENS = {
    # Stablecoins
    "USDT":  "0x55d398326f99059fF775485246999027B3197955",
    "USDC":  "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
    "BUSD":  "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
    "DAI":   "0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3",
    "TUSD":  "0x40af3827F39D0EAcBF4A168f8D4ee67c121D11c9",
    "FDUSD": "0xc5f0f7b66764F6ec8C8Dff7BA683102295E16409",
    # Blue chips
    "WBNB":  "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    "WBTC":  "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",
    "ETH":   "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
    "CAKE":  "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
    "XRP":   "0x1D2F0da169ceB9fC7B3144628dB156f3F6c60dBE",
    "ADA":   "0x3EE2200Efb3400fAbB9AacF31297cBdD1d435D47",
    "DOT":   "0x7083609fCE4d1d8Dc0C979AAb8c869Ea2C873402",
    "LINK":  "0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD",
    "LTC":   "0x4338665CBB7B2485A8855A139b75D5e34AB0DB94",
    "DOGE":  "0xbA2aE424d960c26247Dd6c32edC70B295c744C43",
    "MATIC": "0xCC42724C6683B7E57334c4E856f4c9965ED682bD",
    # Meme / DeFi
    "SHIB":  "0x2859e4544C4bB03966803b044A93563Bd2D0DD4D",
    "FLOKI": "0xfb5B838b6cfEEdC2873aB27866079AC55363D37A",
    "BABYDOGE": "0xc748673057861a797275CD8A068AbB95A902e8de",
    "SAFEMOON": "0x8076C74C5e3F5852037F31Ff0093Eeb8c8ADd8D3",
    "BSCS":  "0xbcb24AFb019BE7E93EA9C43B7E22Bb55D5B7f45D",
    "1INCH": "0x111111111117dC0aa78b770fA6A738034120C302",
    "SUSHI": "0x947950BcC74888a40Ffa2593C5798F11Fc9124C",
    "UNI":   "0xBf5140A22578168FD562DCcF235E5D43A02ce9B1",
    "ALPACA":"0x8F0528cE5eF7B51152A59745bEfDD91D97091d2F",
    "XVS":   "0xcF6BB5389c92Bdda8a3747Ddb454cB7a64626C63",
    "VENUS": "0xcF6BB5389c92Bdda8a3747Ddb454cB7a64626C63",
    "AUTO":  "0xa184088a740c695E156F91f5cC086a06bb78b827",
}

GAS_LIMIT_ERC20 = 100_000   # safe upper bound for token transfer
GAS_LIMIT_NATIVE = 21_000

def sweep_tokens(w3: Web3, account, destination: str, token_map: dict, chain_id: int, native_symbol: str) -> list:
    """
    Sweep all tokens in token_map from account -> destination.
    Returns list of result dicts.
    """
    results = []
    address = account.address
    dest = Web3.to_checksum_address(destination)

    gas_price = w3.eth.gas_price

    for symbol, token_addr in token_map.items():
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_addr),
                abi=ERC20_ABI
            )
            balance = contract.functions.balanceOf(address).call()
            if balance == 0:
                continue

            try:
                decimals = contract.functions.decimals().call()
            except Exception:
                decimals = 18

            human_amount = balance / (10 ** decimals)

            # Check if we have enough native token for gas
            native_bal = w3.eth.get_balance(address)
            fee = gas_price * GAS_LIMIT_ERC20
            if native_bal < fee:
                results.append({
                    "asset": symbol,
                    "status": "skip",
                    "amount": human_amount,
                    "error": f"Insufficient {native_symbol} for gas (need {Web3.from_wei(fee,'ether'):.6f})",
                    "tx_hash": None,
                })
                continue

            nonce = w3.eth.get_transaction_count(address, "pending")
            tx = contract.functions.transfer(dest, balance).build_transaction({
                "chainId": chain_id,
                "gas": GAS_LIMIT_ERC20,
                "gasPrice": gas_price,
                "nonce": nonce,
            })

            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_hex = tx_hash.hex()

            results.append({
                "asset": symbol,
                "status": "success",
                "amount": human_amount,
                "tx_hash": tx_hex,
                "error": None,
            })
            print(f"[TOKEN] Swept {human_amount:.4f} {symbol} -> {dest[:10]}... tx={tx_hex[:12]}...")

            # Bump gas price for next tx to avoid stuck nonces
            import time
            time.sleep(0.3)

        except Exception as e:
            err = str(e)
            if "gas" not in err.lower() and "insufficient" not in err.lower():
                print(f"[TOKEN SKIP] {symbol}: {err[:80]}")
            results.append({
                "asset": symbol,
                "status": "error",
                "amount": 0,
                "tx_hash": None,
                "error": err[:120],
            })

    return results

def get_token_balances(w3: Web3, address: str, token_map: dict) -> dict:
    """Return {symbol: human_balance} for all tokens with balance > 0."""
    out = {}
    for symbol, token_addr in token_map.items():
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_addr),
                abi=ERC20_ABI
            )
            balance = contract.functions.balanceOf(Web3.to_checksum_address(address)).call()
            if balance > 0:
                try:
                    decimals = contract.functions.decimals().call()
                except Exception:
                    decimals = 18
                out[symbol] = balance / (10 ** decimals)
        except Exception:
            pass
    return out
