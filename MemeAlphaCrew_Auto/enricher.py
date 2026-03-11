"""
enricher.py — La Lupa / The Lens.

After the harvester finds wallets from token scans (typically 1-2 trades each),
this module fetches each wallet's recent transaction history to build a fuller
picture before scoring.

Después de que el cosechador encuentra carteras desde escaneos de tokens
(típicamente 1-2 trades cada una), este módulo obtiene el historial reciente
de transacciones de cada cartera para construir un panorama más completo
antes del scoring.

WHY THIS EXISTS / POR QUÉ EXISTE:
  Without enrichment: Scorer sees 1-2 trades → can't distinguish luck from skill.
  With enrichment:    Scorer sees 30-50+ trades → can measure real performance.
  Sin enriquecimiento: Scorer ve 1-2 trades → no distingue suerte de habilidad.
  Con enriquecimiento: Scorer ve 30-50+ trades → puede medir rendimiento real.

Performance Impact / Impacto en Rendimiento:
  +10-15 min per cycle (50 wallets × 50 txs × 0.3s RPC pacing).
  Cycles go from ~31 min to ~45 min. Still within the 2h interval.
"""
import time
import logging
import pandas as pd
from tqdm import tqdm
from MemeAlphaCrew_Auto.config import RPC_PACING_SECONDS
from MemeAlphaCrew_Auto.harvester import _parse_swap

logger = logging.getLogger(__name__)

# ── Enrichment Settings / Configuración de Enriquecimiento ───
ENRICH_TX_LIMIT = 50           # Fetch last 50 txs per wallet (lightweight deep dive)
ENRICH_TOP_N = 50              # Only enrich top N wallets (by initial trade count)
ENRICH_MIN_INITIAL_TRADES = 2  # Need at least 2 trades from harvester to bother


def enrich_wallets(
    wallet_trades: dict[str, list[dict]],
    rpc_client,
    top_n: int = ENRICH_TOP_N,
    tx_limit: int = ENRICH_TX_LIMIT,
    cache=None,
    master_wallets: dict = None,
) -> dict[str, pd.DataFrame]:
    """
    Enriches the most promising wallets by fetching their full recent
    transaction history directly from the wallet (not from the token).

    Enriquece las carteras más prometedoras obteniendo su historial
    de transacciones completo directamente de la cartera (no del token).

    Args:
        wallet_trades: Dict from harvester (wallet_addr -> list of trade dicts).
        rpc_client: SolanaRPCClient instance.
        top_n: Max wallets to enrich.
        tx_limit: How many recent txs to fetch per wallet.
        cache: Optional CycleCache to skip recently enriched wallets.
        master_wallets: Optional master list dict to skip well-known wallets.

    Returns:
        Dict mapping wallet_addr -> enriched trades DataFrame.
        Wallets that weren't enriched still get their original trades.
    """
    # Select candidates: wallets with the most initial trades
    # Seleccionar candidatos: carteras con más trades iniciales
    candidates = [
        (addr, trades)
        for addr, trades in wallet_trades.items()
        if len(trades) >= ENRICH_MIN_INITIAL_TRADES
    ]
    candidates.sort(key=lambda x: len(x[1]), reverse=True)
    candidates = candidates[:top_n]

    if not candidates:
        logger.info("No wallets qualify for enrichment (need 2+ trades from harvester).")
        return _to_dataframes(wallet_trades)

    # Skip wallets we've recently enriched or already know well
    # Saltar carteras que ya enriquecimos recientemente o ya conocemos bien
    if cache:
        master = master_wallets or {}
        before = len(candidates)
        candidates = [
            (addr, trades) for addr, trades in candidates
            if cache.should_enrich(
                addr,
                master_trades=master.get(addr, {}).get("total_trades", 0)
            )
        ]
        skipped = before - len(candidates)
        if skipped > 0:
            logger.info(
                f"  📦 Cache: {skipped} wallets skipped "
                f"(recently enriched or already well-known)"
            )

    if not candidates:
        logger.info("All candidates were recently enriched. Skipping.")
        return _to_dataframes(wallet_trades)

    logger.info(
        f"🔍 Enriching {len(candidates)} wallets "
        f"(fetching last {tx_limit} txs each)..."
    )

    enriched = {}
    enriched_count = 0
    enriched_addrs = []

    for wallet_addr, initial_trades in tqdm(
        candidates, desc="  Enriching", ncols=80, leave=False
    ):
        wallet_full_trades = _fetch_wallet_history(
            wallet_addr, rpc_client, tx_limit
        )

        # Only use enriched data if it found MORE trades than the initial scan
        # Solo usar datos enriquecidos si encontró MÁS trades que el escaneo inicial
        if wallet_full_trades and len(wallet_full_trades) > len(initial_trades):
            enriched[wallet_addr] = pd.DataFrame(wallet_full_trades)
            enriched_count += 1
            enriched_addrs.append(wallet_addr)
        else:
            # Enrichment didn't find more — keep original
            enriched[wallet_addr] = pd.DataFrame(initial_trades)

    # Add non-enriched wallets as-is
    # Agregar carteras no enriquecidas tal cual
    for addr, trades in wallet_trades.items():
        if addr not in enriched:
            enriched[addr] = pd.DataFrame(trades) if trades else pd.DataFrame()

    # Mark enriched wallets in cache to avoid re-processing
    # Marcar carteras enriquecidas en caché para evitar re-procesamiento
    if cache and enriched_addrs:
        cache.save_enrichment_batch(enriched_addrs)

    logger.info(
        f"✅ Enrichment complete: {enriched_count}/{len(candidates)} wallets "
        f"got fuller history"
    )
    return enriched


def _fetch_wallet_history(
    wallet_addr: str, rpc_client, tx_limit: int
) -> list[dict]:
    """
    Fetches a wallet's last N transactions and parses all swaps.
    Same logic as deep_dive but lighter (fewer txs, no report).

    Obtiene las últimas N transacciones de una cartera y parsea todos los swaps.
    Misma lógica que deep_dive pero más ligera.
    """
    try:
        sig_resp = rpc_client.get_signatures_for_address(
            wallet_addr, limit=tx_limit
        )
        signatures = sig_resp.value if hasattr(sig_resp, 'value') else []
    except Exception as e:
        logger.debug(f"   Failed to fetch sigs for {wallet_addr[:12]}: {e}")
        return []

    if not signatures:
        return []

    trades = []
    for sig_info in signatures:
        try:
            sig_str = str(sig_info.signature)
            tx_resp = rpc_client.get_transaction(sig_str)
            # target_mint="" means match ANY token swap
            # target_mint="" significa coincidir con CUALQUIER token swap
            trade = _parse_swap(tx_resp, target_mint="")
            if trade and trade["wallet"] == wallet_addr:
                trades.append(trade)
        except Exception:
            continue
        time.sleep(RPC_PACING_SECONDS)

    return trades


def _to_dataframes(wallet_trades: dict[str, list[dict]]) -> dict[str, pd.DataFrame]:
    """Convert raw trade lists to DataFrames (no enrichment fallback)."""
    return {
        addr: pd.DataFrame(trades) if trades else pd.DataFrame()
        for addr, trades in wallet_trades.items()
    }
