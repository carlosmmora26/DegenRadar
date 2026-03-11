"""
config.py — La Configuración Central / The Central Configuration.

All tunable parameters, thresholds, and environment variables live here.
Changing a value here affects the entire pipeline without touching any logic.

Todos los parámetros ajustables, umbrales y variables de entorno viven aquí.
Cambiar un valor aquí afecta todo el pipeline sin tocar lógica.
"""
import os
from dotenv import load_dotenv

# Load .env from the package directory (not the working directory)
# Carga .env desde el directorio del paquete (no el directorio de trabajo)
_env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(_env_path)


# ═══════════════════════════════════════════════════════════════
#  RPC CONFIGURATION (Multi-Node Rotation)
#  Configuración RPC (Rotación Multi-Nodo)
# ═══════════════════════════════════════════════════════════════
# The client rotates between these URLs when one gets rate-limited (429).
# Add as many free-tier providers as you want in your .env file.
# El cliente rota entre estas URLs cuando una es limitada por rate (429).
_rpc_candidates = [
    os.getenv("RPC_URL_HELIUS"),       # Helius (fast, generous free tier)
    os.getenv("RPC_URL_ALCHEMY"),      # Alchemy (reliable)
    os.getenv("RPC_URL_QUICKNODE"),     # QuickNode
    os.getenv("RPC_URL_ANKR"),         # Ankr
    os.getenv("RPC_URL"),              # Fallback / default
    "https://solana-rpc.publicnode.com",    # PublicNode (free, no key needed)
    "https://api.mainnet-beta.solana.com",  # Official Public RPC (heavily rate-limited)
]
RPC_URLS = list(dict.fromkeys(url for url in _rpc_candidates if url))  # dedup, preserve order
if not RPC_URLS:
    RPC_URLS = ["https://api.mainnet-beta.solana.com"]


# ═══════════════════════════════════════════════════════════════
#  EXTERNAL API ENDPOINTS
#  Endpoints de APIs Externas
# ═══════════════════════════════════════════════════════════════
DEXSCREENER_API_URL = "https://api.dexscreener.com"  # Token/pair data
PUMPFUN_API_URL = "https://frontend-api-v3.pump.fun"  # Pump.fun graduated tokens


# ═══════════════════════════════════════════════════════════════
#  TOKEN DISCOVERY — "SURVIVOR" CRITERIA
#  Descubrimiento de Tokens — Criterios "Sobreviviente"
# ═══════════════════════════════════════════════════════════════
# Survivors = tokens that didn't die in the first hours.
# They have real liquidity, volume, and have been around long enough
# to have a trading history worth analyzing.
# Sobrevivientes = tokens que no murieron en las primeras horas.
MIN_TOKEN_AGE_DAYS = 1       # Must have survived at least 1 day / Mínimo 1 día de vida
MAX_TOKEN_AGE_DAYS = 21      # Not older than 21 days / No mayor a 21 días
MIN_LIQUIDITY_USD = 30_000   # Minimum $30k liquidity / Mínimo $30k de liquidez
MIN_VOLUME_24H_USD = 50_000  # Minimum $50k 24h volume / Mínimo $50k volumen 24h
TOP_TOKENS_LIMIT = 10        # Return top N survivor tokens / Retornar top N tokens


# ═══════════════════════════════════════════════════════════════
#  TOKEN DISCOVERY — "MOMENTUM" CRITERIA
#  Descubrimiento de Tokens — Criterios "Momentum"
# ═══════════════════════════════════════════════════════════════
# Momentum = very fresh tokens (1h-24h) that are showing high activity.
# Riskier than survivors, but can catch early movers.
# Momentum = tokens muy frescos (1h-24h) con alta actividad.
MOMENTUM_TOKENS_LIMIT = 5         # Return top N momentum tokens
MOMENTUM_MIN_AGE_HOURS = 1        # Minimum 1 hour old (avoid rug pulls)
MOMENTUM_MAX_AGE_HOURS = 24       # Maximum 24 hours old
MOMENTUM_MIN_LIQUIDITY = 10_000   # Min $10k liquidity
MOMENTUM_MIN_VOLUME_1H = 10_000   # Min $10k volume in recent period


# ═══════════════════════════════════════════════════════════════
#  WALLET FILTERS
#  Filtros de Carteras
# ═══════════════════════════════════════════════════════════════
# These filters remove wallets that are uncopyable or too risky.
# We do NOT filter out profitable bots — they are GOOD to copy.
# Estos filtros eliminan carteras no copiables o demasiado riesgosas.

# Anti-Whale: Reject if average buy > 25 SOL (we want retail-sized wallets)
MAX_AVG_BUY_SIZE_SOL = 25.0

# Anti-Insider: Disabled (can't measure real wallet age from small sample)
MIN_WALLET_AGE_DAYS = 0
MIN_UNIQUE_TOKENS = 1  # Lowered: our sample only has 3-5 tokens initially

# Anti-Uncopyable (MEV/Sandwich detection)
MAX_TX_PER_MINUTE = 30        # Only reject extreme spam (>30 tx/min)
MIN_ROUND_TRIP_HOLD_SECS = 2  # If ALL round-trips < 2 seconds → sandwich bot


# ═══════════════════════════════════════════════════════════════
#  SCORING WEIGHTS & CONFIDENCE
#  Pesos de Puntuación y Confianza
# ═══════════════════════════════════════════════════════════════
# Alpha Score = weighted sum of four factors, scaled to 0-100.
# Alpha Score = suma ponderada de cuatro factores, escalada a 0-100.
WEIGHT_PNL = 0.35          # 35% — Net profit/loss (the most important factor)
WEIGHT_WIN_RATE = 0.25     # 25% — Per-token round-trip profitability
WEIGHT_COPYABILITY = 0.25  # 25% — How easy it is to follow this wallet's trades
WEIGHT_CONSISTENCY = 0.15  # 15% — Diversity of tokens traded (not a one-trick pony)

# Confidence Multipliers: aggressively penalize wallets with few observed trades.
# 1-2 trades = noise, not signal. We need repeated evidence of skill.
# Multiplicadores de Confianza: penalización agresiva para pocas operaciones.
CONFIDENCE_MULTIPLIERS = {1: 0.10, 2: 0.25, 3: 0.45, 4: 0.65, 5: 0.80}
CONFIDENCE_DEFAULT = 0.90  # 6-9 trades
# 10+ trades: 1.0 (full confidence / confianza total)

# Hard cap: wallets below this trade count can score max 35/100.
# Rationale: 1-2 lucky trades ≠ alpha. We need repeated evidence.
# Carteras debajo de este umbral tienen score máximo de 35/100.
MIN_TRADES_FOR_QUALITY = 5


# ═══════════════════════════════════════════════════════════════
#  COPYABILITY TIERS
#  Niveles de Copiabilidad
# ═══════════════════════════════════════════════════════════════
# Based on average hold time (seconds between buy and sell of same token).
# Basado en tiempo promedio de retención (segundos entre compra y venta).
MIN_HOLD_TIME_SECONDS = 2  # Below this = MEV/sandwich bot (uncopyable)
COPYABILITY_TIERS = {
    # (min_seconds, max_seconds): copyability_score
    "uncopyable": (0, 5, 0.0),       # 0-5s:    MEV/sandwich, impossible to follow
    "hard":       (5, 30, 0.3),      # 5-30s:   Fast sniper, needs automation
    "moderate":   (30, 300, 0.7),    # 30s-5m:  Copyable with alert notifications
    "easy":       (300, float('inf'), 1.0),  # 5m+: Easily copyable by hand
}


# ═══════════════════════════════════════════════════════════════
#  SOLANA AMM PROGRAM IDS
#  IDs de Programas AMM de Solana
# ═══════════════════════════════════════════════════════════════
# These are the on-chain program addresses for the DEXs we support.
# If a transaction invokes one of these, it's a swap we want to parse.
# Estas son las direcciones de programas on-chain de los DEXs soportados.
RAYDIUM_V4_PROGRAM_ID = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
RAYDIUM_CPMM_PROGRAM_ID = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
JUPITER_V6_PROGRAM_ID = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
ORCA_WHIRLPOOL_PROGRAM_ID = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
METEORA_DLMM_PROGRAM_ID = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
ALL_AMM_PROGRAM_IDS = [
    RAYDIUM_V4_PROGRAM_ID,
    RAYDIUM_CPMM_PROGRAM_ID,
    PUMPFUN_PROGRAM_ID,
    JUPITER_V6_PROGRAM_ID,
    ORCA_WHIRLPOOL_PROGRAM_ID,
    METEORA_DLMM_PROGRAM_ID,
]
SOL_MINT = "So11111111111111111111111111111111111111112"  # Native SOL wrapped mint


# ═══════════════════════════════════════════════════════════════
#  HARVESTER LIMITS
#  Límites del Cosechador
# ═══════════════════════════════════════════════════════════════
TX_SIGNATURES_LIMIT = 200    # Last 200 tx signatures per token (good coverage)
RPC_PACING_SECONDS = 0.3     # Delay between RPC calls (respect free-tier limits)


# ═══════════════════════════════════════════════════════════════
#  LOCAL STORAGE PATHS
#  Rutas de Almacenamiento Local
# ═══════════════════════════════════════════════════════════════
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_CSV = os.path.join(DATA_DIR, "alpha_wallets.csv")
WALLETS_JSON = os.path.join(DATA_DIR, "wallets_data.json")

# Create data directory if it doesn't exist
os.makedirs(DATA_DIR, exist_ok=True)
