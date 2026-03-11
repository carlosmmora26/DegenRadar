"""
filters.py — El Guardián / The Gatekeeper.

Filters out wallets that are uncopyable (sandwich bots, MEV) or too risky
to follow (extreme whales, sell-only insiders).

Filtra carteras que no son copiables (bots sandwich, MEV) o demasiado
riesgosas de seguir (ballenas extremas, insiders solo-venta).

IMPORTANT DESIGN DECISION / DECISIÓN DE DISEÑO IMPORTANTE:
  We do NOT filter out bots — profitable bots are GOOD to copy.
  We only filter wallets that are IMPOSSIBLE to follow due to speed or size.
  NO filtramos bots — los bots rentables son BUENOS para copiar.
  Solo filtramos carteras IMPOSIBLES de seguir por velocidad o tamaño.

Filter Order / Orden de Filtros:
  1. Anti-Whale:      Avg buy > 25 SOL → reject (too large to follow)
  2. Anti-Insider:     Zero unique tokens → reject (empty data)
  3. Anti-Sell-Only:   Only sells, zero buys → reject (deployer/airdrop recipient)
  4. Anti-Uncopyable:  All holds < 2s → reject (sandwich bot)
"""
import logging
import pandas as pd
from MemeAlphaCrew_Auto.config import (
    MAX_AVG_BUY_SIZE_SOL,
    MIN_WALLET_AGE_DAYS,
    MIN_UNIQUE_TOKENS,
    MAX_TX_PER_MINUTE,
    MIN_ROUND_TRIP_HOLD_SECS,
)

logger = logging.getLogger(__name__)


def anti_whale_check(trades_df: pd.DataFrame) -> tuple[bool, str | None]:
    """
    Filter 1: Anti-Whale / Anti-Ballena.
    Reject if average buy > 25 SOL. We want wallets we can follow
    without needing massive capital.
    Rechazar si compra promedio > 25 SOL. Queremos carteras que podamos
    seguir sin necesitar capital masivo.
    """
    if trades_df.empty:
        return False, None

    buys = trades_df[trades_df['is_buy'] == True]
    if buys.empty:
        return False, None

    avg_buy = buys['trade_size_sol'].mean()
    if avg_buy > MAX_AVG_BUY_SIZE_SOL:
        return True, f"whale: avg buy {avg_buy:.2f} SOL > {MAX_AVG_BUY_SIZE_SOL}"

    return False, None


def anti_insider_check(trades_df: pd.DataFrame) -> tuple[bool, str | None]:
    """
    Filter 2: Anti-Insider (minimal) / Anti-Insider (mínimo).
    Only reject if zero unique tokens or activity too short.
    Solo rechazar si cero tokens únicos o actividad muy corta.
    """
    if trades_df.empty:
        return True, "No trades found"

    unique_tokens = trades_df['token_mint'].nunique()
    if unique_tokens < MIN_UNIQUE_TOKENS:
        return True, f"insider: only {unique_tokens} unique tokens (min: {MIN_UNIQUE_TOKENS})"

    # Check activity timespan if timestamps available
    # Verificar duración de actividad si hay timestamps disponibles
    if 'timestamp' in trades_df.columns:
        timestamps = trades_df['timestamp'].dropna()
        if len(timestamps) >= 2:
            oldest = timestamps.min()
            newest = timestamps.max()
            activity_days = (newest - oldest) / (60 * 60 * 24)
            if activity_days < MIN_WALLET_AGE_DAYS:
                return True, (
                    f"insider: wallet active only {activity_days:.1f} days "
                    f"(min: {MIN_WALLET_AGE_DAYS})"
                )

    return False, None


def anti_sell_only_check(trades_df: pd.DataFrame) -> tuple[bool, str | None]:
    """
    Filter 2b: Anti Sell-Only / Anti Solo-Venta.
    Reject wallets that only sell (never buy) — they received tokens
    for free (deployers, airdrops) and are impossible to copy.
    Rechazar carteras que solo venden — recibieron tokens gratis
    (deployers, airdrops) y son imposibles de copiar.

    Only flags if >= 2 sells with 0 buys (to avoid false positives).
    Solo marca si >= 2 ventas con 0 compras (para evitar falsos positivos).
    """
    if trades_df.empty:
        return False, None

    buys = trades_df[trades_df['is_buy'] == True]
    sells = trades_df[trades_df['is_sell'] == True]

    if len(sells) >= 2 and len(buys) == 0:
        return True, f"insider: sell-only ({len(sells)} sells, 0 buys)"

    return False, None


def uncopyable_check(trades_df: pd.DataFrame) -> tuple[bool, str | None]:
    """
    Filter 3: Anti-Uncopyable / Anti-No-Copiable.
    Detects truly uncopyable wallets:
    Detecta carteras verdaderamente no copiables:

    1. Sandwich bots: ALL round-trips complete in < 2 seconds.
       These bots front-run transactions within the same block.
       Estos bots hacen front-run en el mismo bloque.

    2. Extreme spam: > 30 tx/minute (programmatic, impossible to follow).
       Spam extremo: > 30 tx/minuto (programático, imposible de seguir).

    Normal bots, snipers, fast traders all PASS — they are copyable.
    Bots normales, snipers, traders rápidos PASAN — son copiables.
    """
    if trades_df.empty or 'timestamp' not in trades_df.columns:
        return False, None

    timestamps = trades_df['timestamp'].dropna().sort_values()
    if len(timestamps) < 2:
        return False, None

    # Check 1: Extreme spam rate / Verificar tasa de spam extrema
    total_seconds = timestamps.max() - timestamps.min()
    if total_seconds > 0:
        total_minutes = total_seconds / 60
        tx_per_minute = len(timestamps) / total_minutes
        if tx_per_minute > MAX_TX_PER_MINUTE:
            return True, f"uncopyable: {tx_per_minute:.1f} tx/min (spam)"

    # Check 2: Sandwich bot detection via FIFO hold time matching
    # Detección de bots sandwich vía emparejamiento FIFO de tiempos de retención
    hold_times = []
    for token, group in trades_df.groupby('token_mint'):
        buys = group[group['is_buy'] == True].sort_values('timestamp')
        sells = group[group['is_sell'] == True].sort_values('timestamp')

        # FIFO: match first buy with first sell
        pairs = min(len(buys), len(sells))
        for i in range(pairs):
            buy_time = buys.iloc[i]['timestamp']
            sell_time = sells.iloc[i]['timestamp']
            if sell_time and buy_time and sell_time > buy_time:
                hold_times.append(sell_time - buy_time)

    # If ALL round-trips are under 2 seconds → sandwich bot
    # Si TODOS los round-trips son menores a 2 segundos → bot sandwich
    if hold_times and all(ht < MIN_ROUND_TRIP_HOLD_SECS for ht in hold_times):
        avg_ht = sum(hold_times) / len(hold_times)
        return True, f"uncopyable: sandwich bot (avg hold {avg_ht:.1f}s)"

    return False, None


def run_all_filters(trades_df: pd.DataFrame) -> tuple[bool, str | None]:
    """
    Applies all filters in sequence. Returns on first rejection.
    Aplica todos los filtros en secuencia. Retorna en el primer rechazo.

    Returns (is_filtered, reason) — True means REJECT.
    Retorna (fue_filtrado, razón) — True significa RECHAZADO.
    """
    # Filter 1: Anti-Whale / Anti-Ballena
    filtered, reason = anti_whale_check(trades_df)
    if filtered:
        return True, reason

    # Filter 2: Anti-Insider / Anti-Insider
    filtered, reason = anti_insider_check(trades_df)
    if filtered:
        return True, reason

    # Filter 2b: Anti Sell-Only / Anti Solo-Venta
    filtered, reason = anti_sell_only_check(trades_df)
    if filtered:
        return True, reason

    # Filter 3: Uncopyable / No-Copiable (sandwich/MEV only)
    filtered, reason = uncopyable_check(trades_df)
    if filtered:
        return True, reason

    return False, None
