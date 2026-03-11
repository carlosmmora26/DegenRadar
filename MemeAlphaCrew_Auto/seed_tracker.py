"""
seed_tracker.py — La Red de Espías / The Spy Network.

Reads a list of known "good" wallets (seed wallets) and discovers
what tokens they're currently trading, feeding new mints back into
the discovery pipeline.

Lee una lista de carteras conocidas como "buenas" (semillas) y descubre
qué tokens están operando actualmente, alimentando nuevos mints de vuelta
al pipeline de descubrimiento.

This provides an alternative discovery source beyond DexScreener/Pump.fun:
instead of finding tokens and then finding wallets, we start from wallets
we already trust and discover what they're trading.

Esto provee una fuente de descubrimiento alternativa a DexScreener/Pump.fun:
en vez de encontrar tokens y luego encontrar carteras, empezamos desde
carteras que ya confiamos y descubrimos qué están operando.
"""
import json
import os
import logging
import time
from MemeAlphaCrew_Auto.config import (
    DATA_DIR,
    ALL_AMM_PROGRAM_IDS,
    SOL_MINT,
    RPC_PACING_SECONDS,
)

logger = logging.getLogger(__name__)

SEED_FILE = os.path.join(DATA_DIR, "seed_wallets.txt")


def load_seed_wallets() -> list[str]:
    """
    Reads seed wallet addresses from data/seed_wallets.txt.
    Ignores blank lines and lines starting with #.
    """
    if not os.path.exists(SEED_FILE):
        logger.info(f"No seed file found at {SEED_FILE}. Skipping seed tracking.")
        return []

    wallets = []
    with open(SEED_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                wallets.append(line)

    logger.info(f"🌱 Loaded {len(wallets)} seed wallets")
    return wallets


def track_seed_wallets(rpc_client) -> list[str]:
    """
    For each seed wallet, fetches recent transactions and extracts
    token mints they've been trading (Raydium V4 swaps).

    Returns a list of unique token mint addresses discovered from seeds.
    """
    seeds = load_seed_wallets()
    if not seeds:
        return []

    discovered_mints = set()

    for wallet_addr in seeds:
        logger.info(f"  🔎 Spying on seed wallet: {wallet_addr[:12]}...")

        try:
            sig_resp = rpc_client.get_signatures_for_address(wallet_addr, limit=50)
            signatures = sig_resp.value if hasattr(sig_resp, 'value') else []
        except Exception as e:
            logger.warning(f"     Failed to fetch sigs for seed {wallet_addr[:12]}: {e}")
            continue

        if not signatures:
            logger.info(f"     No recent transactions for {wallet_addr[:12]}")
            continue

        # Parse each transaction for Raydium swaps
        for sig_info in signatures:
            try:
                sig_str = str(sig_info.signature)
                tx_resp = rpc_client.get_transaction(sig_str)
                mints = _extract_traded_mints(tx_resp, wallet_addr)
                discovered_mints.update(mints)
            except Exception as e:
                logger.debug(f"     Parse error for seed tx: {e}")
                continue

            time.sleep(RPC_PACING_SECONDS)

        logger.info(
            f"     Found {len(discovered_mints)} unique mints so far "
            f"from seed wallets"
        )

    logger.info(f"🌱 Seed tracking complete: {len(discovered_mints)} token mints discovered")
    return list(discovered_mints)


def _extract_traded_mints(tx_response, wallet_addr: str) -> set[str]:
    """
    Extracts token mints from a transaction that involves Raydium V4 swaps.
    Returns a set of mint addresses.
    """
    if not tx_response or not hasattr(tx_response, 'value') or tx_response.value is None:
        return set()

    try:
        tx_data = json.loads(tx_response.to_json()).get('result')
    except Exception:
        try:
            tx_data = json.loads(tx_response.value.to_json())
        except Exception:
            return set()

    if not tx_data:
        return set()

    meta = tx_data.get('meta')
    transaction = tx_data.get('transaction')
    if not meta or not transaction:
        return set()

    # Check if any supported AMM is involved
    message = transaction.get('message', {})
    account_keys = message.get('accountKeys', [])
    program_ids = [str(key) for key in account_keys]

    has_amm = any(amm_id in program_ids for amm_id in ALL_AMM_PROGRAM_IDS)

    # Also check log messages for versioned transactions (pump.fun, Jupiter, etc.)
    # which use Address Lookup Tables — AMM program ID may not be in accountKeys
    if not has_amm:
        log_messages = meta.get('logMessages', [])
        log_text = "\n".join(log_messages)
        has_amm = any(
            f"Program {amm_id} invoke" in log_text
            for amm_id in ALL_AMM_PROGRAM_IDS
        )

    if not has_amm:
        return set()

    # Skip failed transactions
    if meta.get('err') is not None:
        return set()

    # Extract token mints from postTokenBalances
    mints = set()
    for balance in meta.get('postTokenBalances', []):
        owner = balance.get('owner', '')
        mint = str(balance.get('mint', ''))
        if owner == wallet_addr and mint != SOL_MINT and mint:
            mints.add(mint)

    return mints
