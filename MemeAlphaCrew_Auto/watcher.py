"""
watcher.py — El Ojo / The Eye.

Standalone process that monitors watchlist wallets for new trades in real-time.
Polls every POLL_INTERVAL seconds, detects new swaps, and logs alerts.

Proceso independiente que monitorea las carteras del watchlist en busca
de nuevos trades en tiempo real. Sondea cada POLL_INTERVAL segundos,
detecta nuevos swaps y registra alertas.

HOW IT WORKS / CÓMO FUNCIONA:
  1. Load the watchlist (top 30 wallets promoted by the discovery pipeline)
  2. For each wallet, fetch its last 5 transactions
  3. Compare against the last known signature (last_tx_sig)
  4. If new swaps found → alert with: wallet, token, BUY/SELL, SOL amount, score
  5. Save alerts to alerts.jsonl (history) and update the watchlist

RPC COST / COSTO RPC:
  30 wallets × 5 txs × 0.3s = ~9 RPC calls per cycle, takes ~18 seconds.
  Very lightweight — safe to run 24/7 even on free-tier RPC.

Usage / Uso:
    python -m MemeAlphaCrew_Auto.watcher
    python -m MemeAlphaCrew_Auto.watcher --interval 120 --once
"""
import sys
import time
import signal
import argparse
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
import os

from MemeAlphaCrew_Auto.config import RPC_PACING_SECONDS, DATA_DIR
from MemeAlphaCrew_Auto.rpc_client import SolanaRPCClient
from MemeAlphaCrew_Auto.harvester import _parse_swap
from MemeAlphaCrew_Auto.watchlist import Watchlist

# ── Settings / Configuración ────────────────────────────────
POLL_INTERVAL = 120       # Check every 2 minutes / Revisar cada 2 minutos
SIGS_PER_WALLET = 5       # Only fetch last 5 txs per poll (lightweight)
ALERT_COOLDOWN = 300      # Don't re-alert same wallet within 5 min

# ── Alert Callbacks / Callbacks de Alertas ───────────────────
# Register external handlers (e.g., Telegram notifications)
# Registrar handlers externos (ej. notificaciones de Telegram)
_alert_callbacks: list = []


def register_alert_callback(fn):
    """Register a function to be called on each new alert."""
    _alert_callbacks.append(fn)

# ── Logging / Registro ──────────────────────────────────────
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCHER_LOG = os.path.join(_PROJECT_DIR, "watcher.log")
ALERTS_LOG = os.path.join(DATA_DIR, "alerts.jsonl")

logger = logging.getLogger("watcher")


def setup_logging():
    """Configure dual logging: file (rotating) + console."""
    logger.setLevel(logging.INFO)

    # File handler (rotating, 2MB × 3 backups)
    fh = RotatingFileHandler(
        WATCHER_LOG, maxBytes=2 * 1024 * 1024, backupCount=3
    )
    fh.setFormatter(logging.Formatter(
        '%(asctime)s │ %(levelname)-7s │ %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(
        '%(asctime)s │ %(message)s', datefmt='%H:%M:%S'
    ))
    logger.addHandler(ch)


def log_alert(alert: dict):
    """Append alert to JSONL file for historical analysis."""
    import json
    with open(ALERTS_LOG, 'a') as f:
        f.write(json.dumps(alert) + '\n')


def poll_wallet(wallet_addr: str, wallet_info: dict, rpc_client) -> tuple[list[dict], str | None]:
    """
    Check a single wallet for new transactions since last poll.
    Revisar una cartera individual por nuevas transacciones desde el último sondeo.

    Returns (alerts, latest_sig):
      - alerts: list of new trade alerts (may be empty)
      - latest_sig: most recent signature seen (for tracking)

    IMPORTANT: On first poll (last_sig=None), we only initialize the reference
    signature WITHOUT generating alerts. This prevents false alerts on startup.
    IMPORTANTE: En el primer sondeo, solo inicializamos la firma de referencia
    SIN generar alertas. Esto previene alertas falsas al iniciar.
    """
    last_sig = wallet_info.get("last_tx_sig")

    try:
        sig_resp = rpc_client.get_signatures_for_address(
            wallet_addr, limit=SIGS_PER_WALLET
        )
        signatures = sig_resp.value if hasattr(sig_resp, 'value') else []
    except Exception as e:
        logger.debug(f"Failed to fetch sigs for {wallet_addr[:12]}: {e}")
        return [], None

    if not signatures:
        return [], None

    latest_sig = str(signatures[0].signature)

    # First poll: no reference point — just record latest sig, don't alert
    # Primer sondeo: sin punto de referencia — solo registrar firma, no alertar
    if last_sig is None:
        return [], latest_sig

    # Find new signatures (ones we haven't seen yet)
    # Encontrar firmas nuevas (que no hemos visto aún)
    new_sigs = []
    for sig_info in signatures:
        sig_str = str(sig_info.signature)
        if sig_str == last_sig:
            break  # Already seen / Ya visto
        new_sigs.append(sig_info)

    if not new_sigs:
        return [], latest_sig

    # Parse new transactions for swaps
    # Parsear nuevas transacciones en busca de swaps
    alerts = []
    for sig_info in new_sigs:
        try:
            sig_str = str(sig_info.signature)
            tx_resp = rpc_client.get_transaction(sig_str)
            trade = _parse_swap(tx_resp, target_mint="")

            if trade and trade["wallet"] == wallet_addr:
                alert = {
                    "timestamp": int(time.time()),
                    "wallet": wallet_addr,
                    "wallet_score": wallet_info.get("alpha_score", 0),
                    "signature": sig_str,
                    "token_mint": trade["token_mint"],
                    "direction": "BUY" if trade["is_buy"] else "SELL",
                    "sol_amount": abs(trade["sol_change"]),
                    "token_change": trade["token_change"],
                }
                alerts.append(alert)
        except Exception:
            continue
        time.sleep(RPC_PACING_SECONDS)

    return alerts, latest_sig


def poll_all(watchlist: Watchlist, rpc_client) -> int:
    """
    Poll all watched wallets for new activity.
    Sondear todas las carteras vigiladas en busca de nueva actividad.

    Returns total number of new alerts.
    """
    watched = watchlist.get_all()
    if not watched:
        logger.info("Watchlist empty — nothing to monitor.")
        return 0

    total_alerts = 0
    now = int(time.time())
    dirty = False  # Track if we need to save / Rastrear si necesitamos guardar

    for info in watched:
        wallet_addr = info["wallet"]

        # During cooldown: still poll to track signatures, but suppress alerts
        # This prevents missing trades that happen during the cooldown window.
        # Durante cooldown: seguir sondeando para rastrear firmas, pero suprimir alertas
        last_alert = info.get("last_alert_at") or 0
        in_cooldown = now - last_alert < ALERT_COOLDOWN

        alerts, latest_sig = poll_wallet(wallet_addr, info, rpc_client)

        # Always update the latest signature (even during cooldown)
        # to avoid re-processing old transactions later.
        # Siempre actualizar la última firma (incluso en cooldown)
        if latest_sig:
            watchlist.update_last_sig(wallet_addr, latest_sig, save=False)
            dirty = True

        # Only emit alerts if not in cooldown
        # Solo emitir alertas si no estamos en cooldown
        if alerts and not in_cooldown:
            for alert in alerts:
                total_alerts += 1
                _print_alert(alert)
                log_alert(alert)
                watchlist.record_alert(wallet_addr, alert, save=False)
                dirty = True
                # Notify external handlers / Notificar handlers externos
                for cb in _alert_callbacks:
                    try:
                        cb(alert)
                    except Exception as e:
                        logger.debug(f"Alert callback error: {e}")

    # Save once at end of cycle instead of per-alert (95% less I/O)
    # Guardar una vez al final del ciclo en vez de por alerta (95% menos I/O)
    if dirty:
        watchlist._save()

    return total_alerts


def _print_alert(alert: dict):
    """Pretty-print a trade alert to the console and log."""
    direction = alert["direction"]
    icon = "🟢" if direction == "BUY" else "🔴"
    wallet_short = alert["wallet"][:12]
    token_short = alert["token_mint"][:12]
    sol = alert["sol_amount"]
    score = alert["wallet_score"]

    logger.info(
        f"{icon} ALERT: {wallet_short}... (score:{score:.0f}) "
        f"{direction} {token_short}... for {sol:.4f} SOL"
    )


def run_watcher(interval: int = POLL_INTERVAL, once: bool = False):
    """
    Main watcher loop. Runs continuously until interrupted.
    Bucle principal del watcher. Corre continuamente hasta ser interrumpido.
    """
    setup_logging()

    logger.info("╔══════════════════════════════════════════════════╗")
    logger.info("║      MEMEALPHACREW WATCHER — THE EYE            ║")
    logger.info("╚══════════════════════════════════════════════════╝")

    watchlist = Watchlist()
    rpc_client = SolanaRPCClient()

    watched_count = len(watchlist.wallets)
    logger.info(f"  Watching: {watched_count} wallets")
    logger.info(f"  Interval: {interval}s")
    logger.info(f"  Alerts log: {ALERTS_LOG}")

    if watched_count == 0:
        logger.warning(
            "Watchlist is empty! Run the discovery pipeline first "
            "to promote wallets. Watcher will keep checking..."
        )

    # Graceful shutdown / Apagado gracioso
    running = True

    def handle_signal(signum, frame):
        nonlocal running
        logger.info("Shutdown signal received. Stopping...")
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    cycle = 0
    while running:
        cycle += 1
        start = time.time()

        # Reload watchlist each cycle (discovery may have added new wallets)
        # Recargar watchlist cada ciclo (discovery pudo haber agregado nuevas carteras)
        watchlist = Watchlist()
        watched_count = len(watchlist.wallets)

        if watched_count > 0:
            logger.info(
                f"[Cycle {cycle}] Polling {watched_count} wallets..."
            )
            new_alerts = poll_all(watchlist, rpc_client)
            elapsed = time.time() - start

            if new_alerts:
                logger.info(
                    f"[Cycle {cycle}] {new_alerts} new alerts "
                    f"({elapsed:.1f}s)"
                )
            else:
                logger.info(
                    f"[Cycle {cycle}] No new activity ({elapsed:.1f}s)"
                )
        else:
            logger.debug(f"[Cycle {cycle}] Watchlist empty, skipping.")

        if once:
            break

        # Sleep until next poll / Dormir hasta el siguiente sondeo
        elapsed = time.time() - start
        sleep_time = max(interval - elapsed, 10)
        next_poll = datetime.fromtimestamp(
            time.time() + sleep_time
        ).strftime("%H:%M:%S")
        logger.info(f"  Next poll at {next_poll}")
        time.sleep(sleep_time)

    logger.info("Watcher stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="DegenRadar Watcher — Monitor top wallets for new trades"
    )
    parser.add_argument(
        "--interval", type=int, default=POLL_INTERVAL,
        help=f"Poll interval in seconds (default: {POLL_INTERVAL})"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one poll cycle and exit"
    )
    args = parser.parse_args()

    run_watcher(interval=args.interval, once=args.once)


if __name__ == "__main__":
    main()
