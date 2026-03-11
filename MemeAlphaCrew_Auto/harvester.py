"""
harvester.py — El Cosechador / The Collector.

For each discovered token, fetches its recent on-chain transactions
and extracts the wallet addresses that performed swap instructions
on supported DEXs (Raydium, Pump.fun, Jupiter, Orca, Meteora).

Para cada token descubierto, obtiene sus transacciones recientes on-chain
y extrae las direcciones de las carteras que realizaron swaps en los
DEXs soportados.

This is the core data extraction module. The swap parser (_parse_swap)
handles the complexity of Solana transaction formats, including:
  - Versioned transactions (Address Lookup Tables)
  - Multiple AMM program detection via logs and account keys
  - Fee exclusion from PnL calculations
  - Token balance change detection for buy/sell classification

Este es el módulo principal de extracción de datos. El parser de swaps
maneja la complejidad de los formatos de transacción de Solana.
"""
import json
import re
import time
import logging
from tqdm import tqdm
from MemeAlphaCrew_Auto.config import (
    ALL_AMM_PROGRAM_IDS,
    SOL_MINT,
    TX_SIGNATURES_LIMIT,
    RPC_PACING_SECONDS,
)

logger = logging.getLogger(__name__)


def harvest_wallets(token_mints: list[str], rpc_client) -> dict[str, list[dict]]:
    """
    For each token mint, fetches recent transactions and identifies
    unique wallets that executed swaps on any supported DEX.

    Para cada token mint, obtiene transacciones recientes e identifica
    carteras únicas que ejecutaron swaps en cualquier DEX soportado.

    Args:
        token_mints: List of token mint addresses to scan.
        rpc_client: SolanaRPCClient instance.

    Returns:
        Dict mapping wallet_address -> list of parsed trade dicts.
        Each trade dict contains: wallet, signature, timestamp,
        sol_change, token_mint, token_change, is_buy, is_sell, trade_size_sol.
    """
    wallet_trades: dict[str, list[dict]] = {}
    total_txs_scanned = 0

    for mint in token_mints:
        logger.info(f"🌾 Harvesting wallets from token: {mint[:12]}...")

        # Fetch transaction signatures for this token's mint address
        # Obtener firmas de transacciones para la dirección mint del token
        try:
            sig_resp = rpc_client.get_signatures_for_address(
                mint, limit=TX_SIGNATURES_LIMIT
            )
            signatures = sig_resp.value if hasattr(sig_resp, 'value') else []
        except Exception as e:
            logger.warning(f"   Failed to fetch signatures for {mint[:12]}: {e}")
            continue

        if not signatures:
            logger.info(f"   No signatures found for {mint[:12]}.")
            continue

        logger.info(f"   Found {len(signatures)} signatures. Parsing swaps...")

        # Parse each transaction looking for swap instructions
        # Parsear cada transacción buscando instrucciones de swap
        for sig_info in tqdm(signatures, desc=f"  Parsing {mint[:8]}",
                             leave=False, ncols=80):
            total_txs_scanned += 1

            try:
                sig_str = str(sig_info.signature)
                tx_resp = rpc_client.get_transaction(sig_str)
                trade = _parse_swap(tx_resp, mint)

                if trade:
                    wallet = trade["wallet"]
                    if wallet not in wallet_trades:
                        wallet_trades[wallet] = []
                    wallet_trades[wallet].append(trade)

            except Exception as e:
                logger.debug(f"   Parse error: {e}")
                continue

            # Respect RPC rate limits / Respetar límites de tasa RPC
            time.sleep(RPC_PACING_SECONDS)

    logger.info(
        f"✅ Harvest complete: {total_txs_scanned} txs scanned, "
        f"{len(wallet_trades)} unique wallets found"
    )
    return wallet_trades


def _parse_swap(tx_response, target_mint: str) -> dict | None:
    """
    Parses a single Solana transaction to extract swap data.
    Parsea una transacción de Solana para extraer datos de swap.

    This is the most complex function in the codebase. It handles:
    Esta es la función más compleja del proyecto. Maneja:

    1. Transaction deserialization (two possible JSON formats)
    2. Swap detection via log message regex (faster than program ID check)
    3. AMM program identification (logs first, then accountKeys fallback)
    4. SOL change calculation WITH fee exclusion
    5. Token balance change detection for buy/sell classification
    6. Target mint matching (or any-token mode when target_mint="")

    Args:
        tx_response: Raw RPC response from get_transaction.
        target_mint: Token mint to look for. Empty string = match any token.

    Returns:
        Trade dict or None if transaction is not a relevant swap.
    """
    # ── Step 0: Deserialize transaction ──────────────────────
    # Paso 0: Deserializar la transacción
    if not tx_response or not hasattr(tx_response, 'value') or tx_response.value is None:
        return None

    try:
        tx_data = json.loads(tx_response.to_json()).get('result')
    except Exception:
        try:
            tx_data = json.loads(tx_response.value.to_json())
        except Exception:
            return None

    if not tx_data:
        return None

    meta = tx_data.get('meta')
    transaction = tx_data.get('transaction')
    if not meta or not transaction:
        return None

    # Skip failed transactions / Saltar transacciones fallidas
    if meta.get('err') is not None:
        return None

    # ── Step 1: Fast-filter via log messages (regex) ─────────
    # Paso 1: Filtro rápido vía mensajes de log (regex)
    # Check logs FIRST before doing any expensive balance parsing.
    # This catches 90%+ of non-swap transactions cheaply.
    # Revisar logs ANTES de parsear balances (es más rápido).
    log_messages = meta.get('logMessages', [])
    log_text = "\n".join(log_messages)
    swap_patterns = [
        # Raydium V4 / CPMM
        r"Program log: Instruction: Swap",
        r"Program log: Instruction: SwapExactAmountIn",
        r"Program log: Instruction: SwapBaseIn",
        r"Program log: Instruction: SwapBaseOut",
        # Pump.fun
        r"Program log: Instruction: Buy",
        r"Program log: Instruction: Sell",
        # Jupiter V6 (aggregator — routes through multiple DEXs)
        r"Program log: Instruction: SharedAccountsRoute",
        r"Program log: Instruction: Route",
        r"Program log: Instruction: SharedAccountsExactOutRoute",
        r"Program log: Instruction: ExactOutRoute",
        # Orca Whirlpool
        r"Program log: Instruction: TwoHopSwap",
        # Meteora DLMM
        r"Program log: Instruction: SwapExactIn",
        r"Program log: Instruction: SwapWithPriceImpact",
    ]
    if not any(re.search(p, log_text) for p in swap_patterns):
        return None  # Not a swap transaction / No es una transacción de swap

    # ── Step 2: Verify AMM program involvement ───────────────
    # Paso 2: Verificar que un programa AMM está involucrado
    # Check LOGS first (works for versioned txs with Address Lookup Tables),
    # then fall back to accountKeys (classic transactions).
    # Revisar LOGS primero (funciona con txs versionadas), luego accountKeys.
    matched_amm = None
    for amm_id in ALL_AMM_PROGRAM_IDS:
        if f"Program {amm_id} invoke" in log_text:
            matched_amm = amm_id
            break

    # Fallback: check accountKeys for non-versioned transactions
    # Fallback: revisar accountKeys para transacciones no versionadas
    if not matched_amm:
        message = transaction.get('message', {})
        account_keys = message.get('accountKeys', [])
        program_ids = [str(key) for key in account_keys]
        for amm_id in ALL_AMM_PROGRAM_IDS:
            if amm_id in program_ids:
                matched_amm = amm_id
                break

    if not matched_amm:
        return None  # No supported AMM found / No se encontró AMM soportado

    # ── Step 3: Identify the signer (wallet) ─────────────────
    # Paso 3: Identificar el firmante (cartera)
    # The fee payer (index 0) is the signer in Solana transactions.
    # El pagador de fees (índice 0) es el firmante en transacciones de Solana.
    message = transaction.get('message', {})
    account_keys = message.get('accountKeys', [])
    if not account_keys:
        return None
    signer_wallet = str(account_keys[0])
    wallet_index = 0  # Fee payer is always index 0

    # ── Step 4: Calculate SOL change (excluding fees) ────────
    # Paso 4: Calcular cambio de SOL (excluyendo fees)
    # pre/postBalances include fee deduction, so we ADD the fee back
    # to measure only the swap's SOL movement.
    # IMPORTANT: Without this fix, PnL was ~0.000005 SOL off per trade.
    # pre/postBalances incluyen la deducción de fee, así que SUMAMOS el
    # fee de vuelta para medir solo el movimiento de SOL del swap.
    pre_balances = meta.get('preBalances', [])
    post_balances = meta.get('postBalances', [])
    fee_lamports = meta.get('fee', 0)
    sol_change = 0.0
    if len(pre_balances) > wallet_index and len(post_balances) > wallet_index:
        sol_change = (post_balances[wallet_index] - pre_balances[wallet_index] + fee_lamports) / 1e9

    # ── Step 5: Calculate token balance changes ──────────────
    # Paso 5: Calcular cambios en balances de tokens
    # Build pre/post maps of token balances owned by the signer.
    # Construir mapas pre/post de balances de tokens del firmante.
    pre_token_balances = meta.get('preTokenBalances', [])
    post_token_balances = meta.get('postTokenBalances', [])

    # Map: token_mint -> balance (for the signer only, excluding SOL)
    pre_map = {}
    for b in pre_token_balances:
        owner = b.get('owner', '')
        mint = str(b.get('mint', ''))
        if owner == signer_wallet and mint != SOL_MINT:
            pre_map[mint] = float(
                b.get('uiTokenAmount', {}).get('uiAmount', 0) or 0
            )

    post_map = {}
    for b in post_token_balances:
        owner = b.get('owner', '')
        mint = str(b.get('mint', ''))
        if owner == signer_wallet and mint != SOL_MINT:
            post_map[mint] = float(
                b.get('uiTokenAmount', {}).get('uiAmount', 0) or 0
            )

    all_mints = set(pre_map.keys()) | set(post_map.keys())
    if not all_mints:
        return None  # No token changes detected / No se detectaron cambios de token

    # ── Step 6: Find the token change ────────────────────────
    # Paso 6: Encontrar el cambio de token
    # Prefer the target mint if specified; otherwise use first non-zero change.
    # Preferir el target_mint si se especificó; si no, usar el primer cambio no-cero.
    token_change = 0.0
    matched_mint = None

    if target_mint in all_mints:
        pre_amt = pre_map.get(target_mint, 0)
        post_amt = post_map.get(target_mint, 0)
        token_change = post_amt - pre_amt
        matched_mint = target_mint
    else:
        # Use the first non-SOL token with a balance change
        # Usar el primer token no-SOL con un cambio de balance
        for mint in all_mints:
            pre_amt = pre_map.get(mint, 0)
            post_amt = post_map.get(mint, 0)
            change = post_amt - pre_amt
            if change != 0:
                token_change = change
                matched_mint = mint
                break

    if token_change == 0 or not matched_mint:
        return None

    # ── Build and return trade dict ──────────────────────────
    return {
        "wallet": signer_wallet,
        "signature": transaction.get('signatures', [None])[0],
        "timestamp": tx_data.get('blockTime'),
        "sol_change": sol_change,
        "token_mint": matched_mint,
        "token_change": token_change,
        "is_buy": token_change > 0,   # Tokens increased = bought / Tokens aumentaron = compra
        "is_sell": token_change < 0,   # Tokens decreased = sold / Tokens disminuyeron = venta
        "trade_size_sol": abs(sol_change),
    }
