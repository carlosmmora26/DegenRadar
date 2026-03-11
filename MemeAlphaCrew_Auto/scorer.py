"""
scorer.py — El Algoritmo / The Algorithm.

Calculates an Alpha Score (0-100) for each wallet based on four weighted factors:
  - PnL (35%):         Net profit/loss in SOL (logarithmic scale for big winners)
  - Win Rate (25%):    Per-token round-trip profitability (not per-trade!)
  - Copyability (25%): How easy it is to replicate trades (filters MEV/sandwich)
  - Consistency (15%): Diversity of tokens traded (rewards diversification)

Calcula un Alpha Score (0-100) para cada cartera basándose en cuatro factores:
  - PnL (35%):         Ganancia/pérdida neta en SOL
  - Win Rate (25%):    Rentabilidad por token (no por trade)
  - Copiabilidad (25%): Qué tan fácil es replicar los trades
  - Consistencia (15%): Diversidad de tokens operados

Key Design Decisions / Decisiones de Diseño Clave:
  - No "humanity penalty": we WANT to copy profitable bots.
  - Win rate uses per-token round-trips, not individual buys/sells.
  - Confidence multipliers heavily penalize wallets with < 5 trades.
"""
import math
import logging
import pandas as pd
from MemeAlphaCrew_Auto.config import (
    WEIGHT_PNL,
    WEIGHT_CONSISTENCY,
    WEIGHT_WIN_RATE,
    WEIGHT_COPYABILITY,
    COPYABILITY_TIERS,
    CONFIDENCE_MULTIPLIERS,
    CONFIDENCE_DEFAULT,
    MIN_TRADES_FOR_QUALITY,
)

logger = logging.getLogger(__name__)


def calculate_hold_times(trades_df: pd.DataFrame) -> list[float]:
    """
    Calculates hold times (seconds) for round-trip trades.
    Calcula tiempos de retención (segundos) para operaciones de ida y vuelta.

    A round-trip = buy then sell of the same token by the same wallet.
    Uses FIFO matching: first buy pairs with first sell.
    Un round-trip = compra y luego venta del mismo token.
    Usa emparejamiento FIFO: primera compra con primera venta.

    Returns:
        List of hold times in seconds. Empty if no round-trips found.
    """
    hold_times = []

    if trades_df.empty or 'timestamp' not in trades_df.columns:
        return hold_times

    # Group by token to find buy-sell pairs within the same token
    # Agrupar por token para encontrar pares compra-venta del mismo token
    for token, group in trades_df.groupby('token_mint'):
        buys = group[group['is_buy'] == True].sort_values('timestamp')
        sells = group[group['is_sell'] == True].sort_values('timestamp')

        # FIFO matching: first buy → first sell, second buy → second sell, etc.
        pairs = min(len(buys), len(sells))
        for i in range(pairs):
            buy_time = buys.iloc[i]['timestamp']
            sell_time = sells.iloc[i]['timestamp']
            if sell_time and buy_time and sell_time > buy_time:
                hold_times.append(sell_time - buy_time)

    return hold_times


def calculate_copyability(hold_times: list[float]) -> float:
    """
    Scores how copyable a wallet is based on average hold time.
    Puntúa qué tan copiable es una cartera según su tiempo promedio de retención.

    Returns / Retorna:
        Float 0.0 to 1.0 where:
        - 0.0 = uncopyable  (MEV/sandwich, holds < 5s)  → imposible de copiar
        - 0.3 = hard         (fast sniper, 5-30s)        → requiere automatización
        - 0.7 = moderate     (30s - 5min)                → copiable con alertas
        - 1.0 = easy         (holds 5+ minutes)           → fácil de copiar
        - 0.5 = unknown      (no round-trip data)         → sin datos suficientes
    """
    if not hold_times:
        # No round-trip data — can't assess, give neutral score
        # Sin datos de round-trip — puntuación neutral
        return 0.5

    avg_hold = sum(hold_times) / len(hold_times)

    # Match average hold time against tier thresholds
    # Comparar tiempo promedio contra umbrales de cada nivel
    for _name, (min_s, max_s, score) in COPYABILITY_TIERS.items():
        if min_s <= avg_hold < max_s:
            return score

    return 0.5  # Fallback if no tier matched


def calculate_metrics(trades_df: pd.DataFrame) -> dict:
    """
    Computes trading performance metrics from a trades DataFrame.
    Calcula métricas de rendimiento de trading desde un DataFrame de operaciones.

    Returns dict with keys:
        pnl_sol, win_rate (0-1 fraction), consistency (0-1 fraction),
        total_trades, unique_tokens, profitable_trades, avg_hold_time,
        copyability, hold_times_count
    """
    if trades_df.empty:
        return {
            "pnl_sol": 0.0,
            "win_rate": 0.0,
            "consistency": 0.0,
            "total_trades": 0,
            "unique_tokens": 0,
            "profitable_trades": 0,
            "avg_hold_time": 0.0,
            "copyability": 0.0,
            "hold_times_count": 0,
        }

    # ── PnL: Total SOL gained/lost across all trades ─────────
    # Suma total de SOL ganado/perdido en todas las operaciones
    total_pnl = trades_df['sol_change'].sum()

    # ── Win Rate: Per-token round-trip profitability ──────────
    # Group trades by token, sum SOL change per token.
    # A token is "won" if:
    #   1. It has at least one sell (position was closed)
    #   2. Net SOL change is positive (sold for more than bought)
    # This avoids counting open positions as wins/losses.
    #
    # Agrupar por token, sumar cambio de SOL por token.
    # Un token es "ganado" si tiene al menos una venta y el cambio neto es positivo.
    token_pnl = trades_df.groupby('token_mint')['sol_change'].sum()
    tokens_with_sells = set(
        trades_df[trades_df['is_sell'] == True]['token_mint'].unique()
    )
    completed = token_pnl[token_pnl.index.isin(tokens_with_sells)]
    profitable = int((completed > 0).sum())
    total_completed = len(completed)
    win_rate = profitable / total_completed if total_completed > 0 else 0.0
    total = len(trades_df)

    # ── Consistency: Diversity of tokens traded ───────────────
    # More unique tokens = more diversified = higher consistency.
    # Capped at 1.0 (10+ tokens = maximum consistency).
    # Más tokens únicos = más diversificado = mayor consistencia.
    unique_tokens = trades_df['token_mint'].nunique()
    consistency = min(unique_tokens / 10.0, 1.0)

    # ── Hold Time & Copyability ──────────────────────────────
    hold_times = calculate_hold_times(trades_df)
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0.0
    copyability = calculate_copyability(hold_times)

    # ── Sanity checks — catch data format bugs at the source ─
    # CRITICAL: These asserts have saved us from 15 days of corrupted data.
    # CRÍTICO: Estos asserts nos salvaron de 15 días de datos corruptos.
    assert 0.0 <= win_rate <= 1.0, f"win_rate={win_rate} must be 0-1 fraction"
    assert 0.0 <= consistency <= 1.0, f"consistency={consistency} must be 0-1 fraction"
    assert 0.0 <= copyability <= 1.0, f"copyability={copyability} must be 0-1 fraction"

    return {
        "pnl_sol": round(total_pnl, 4),
        "win_rate": round(win_rate, 4),
        "consistency": round(consistency, 4),
        "total_trades": total,
        "unique_tokens": unique_tokens,
        "profitable_trades": profitable,
        "avg_hold_time": round(avg_hold, 1),
        "copyability": round(copyability, 2),
        "hold_times_count": len(hold_times),
    }


def calculate_alpha_score(metrics: dict) -> float:
    """
    Calculates the final Alpha Score (0-100) using a weighted formula.
    Calcula el Alpha Score final (0-100) usando una fórmula ponderada.

    Formula / Fórmula:
        raw = (PnL × 0.35) + (WinRate × 0.25) + (Copyability × 0.25) + (Consistency × 0.15)
        final = raw × 100 × confidence_multiplier

    No humanity penalty. High win rate = GOOD.
    Copyability rewards wallets we can actually follow.
    Sin penalización por ser bot. Alta win rate = BUENO.
    """
    pnl = metrics["pnl_sol"]
    win_rate = metrics["win_rate"]
    consistency = metrics["consistency"]
    copyability = metrics["copyability"]
    trades = metrics["total_trades"]

    # ── Normalize PnL: logarithmic scale for profits ─────────
    # Why log scale? Because PnL varies wildly (0.1 SOL to 500+ SOL).
    # Linear would make small profits invisible. Log compresses the range.
    # 10 SOL ≈ 0.60, 20 SOL ≈ 0.77, 50 SOL = 1.0 (max)
    # ¿Por qué escala logarítmica? Porque PnL varía enormemente.
    if pnl > 0:
        normalized_pnl = min(math.log10(1 + pnl) / math.log10(51), 1.0)
    else:
        # Losses use linear scale, capped at -0.5 to avoid crushing the score
        # Pérdidas usan escala lineal, limitada a -0.5
        normalized_pnl = max(pnl / 10.0, -0.5)

    # ── Weighted base score (0-1 range, can go slightly negative) ─
    raw_score = (
        (normalized_pnl * WEIGHT_PNL) +
        (win_rate * WEIGHT_WIN_RATE) +
        (copyability * WEIGHT_COPYABILITY) +
        (consistency * WEIGHT_CONSISTENCY)
    )

    # ── Confidence multiplier: penalize small sample sizes ───
    # 1 trade = 0.10x, 2 trades = 0.25x, ..., 10+ = 1.0x
    # This is the most impactful filter: it crushes lucky one-hit wonders.
    # Este es el filtro más impactante: destruye las rachas de suerte.
    if trades <= 5:
        confidence = CONFIDENCE_MULTIPLIERS.get(trades, 0.25)
    elif trades <= 9:
        confidence = CONFIDENCE_DEFAULT  # 0.90
    else:
        confidence = 1.0  # Full confidence / Confianza total

    final_score = raw_score * 100 * confidence

    # ── Hard cap: few trades → max score 35 ──────────────────
    # One lucky trade ≠ alpha. We need repeated evidence of skill.
    # Un trade con suerte ≠ alpha. Necesitamos evidencia repetida.
    if trades < MIN_TRADES_FOR_QUALITY:
        final_score = min(final_score, 35.0)

    # ── Hard cap: single-token wallets → max score 45 ────────
    # Trading only 1 token could be insider knowledge, not skill.
    # Operar solo 1 token podría ser información privilegiada, no habilidad.
    unique_tokens = metrics.get("unique_tokens", 1)
    if unique_tokens < 2:
        final_score = min(final_score, 45.0)

    # Clamp to [0, 100] and round
    return round(min(max(final_score, 0), 100), 2)
