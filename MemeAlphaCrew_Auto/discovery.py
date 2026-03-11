"""
discovery.py — El Radar / The Radar.

Scans DexScreener and Pump.fun APIs to find tradeable Solana meme tokens.
Escanea las APIs de DexScreener y Pump.fun para encontrar tokens meme operables.

Two discovery strategies / Dos estrategias de descubrimiento:

1. SURVIVOR Discovery (default):
   Tokens that survived the initial pump-and-dump phase.
   Age: 1-21 days, Liquidity: >$30k, Volume: >$50k/24h.
   These have established trading history worth analyzing.

   Tokens que sobrevivieron la fase inicial de pump-and-dump.
   Edad: 1-21 días, Liquidez: >$30k, Volumen: >$50k/24h.

2. MOMENTUM Discovery (--momentum flag):
   Very fresh tokens (1h-24h) with high activity — early movers.
   Riskier but can catch wallets before they become well-known.

   Tokens muy frescos (1h-24h) con alta actividad — movimientos tempranos.

Data Sources / Fuentes de Datos:
  - DexScreener: Boosted tokens, top tokens, token profiles, keyword search
  - Pump.fun: Graduated tokens (survived bonding curve), newest tokens
"""
import time
import logging
import requests
from MemeAlphaCrew_Auto.config import (
    DEXSCREENER_API_URL,
    PUMPFUN_API_URL,
    MIN_TOKEN_AGE_DAYS,
    MAX_TOKEN_AGE_DAYS,
    MIN_LIQUIDITY_USD,
    MIN_VOLUME_24H_USD,
    TOP_TOKENS_LIMIT,
    MOMENTUM_MIN_AGE_HOURS,
    MOMENTUM_MAX_AGE_HOURS,
    MOMENTUM_MIN_LIQUIDITY,
    MOMENTUM_MIN_VOLUME_1H,
    MOMENTUM_TOKENS_LIMIT,
)

logger = logging.getLogger(__name__)

# DexScreener rate limits: 60/min for boost endpoints, 300/min for pair lookups
# Límites de tasa DexScreener: 60/min para boosts, 300/min para pares
_DELAY = 0.3


def discover_survivor_tokens() -> list[dict]:
    """
    Discovers "Survivor" tokens on Solana via DexScreener + Pump.fun.
    Descubre tokens "Sobrevivientes" en Solana vía DexScreener + Pump.fun.

    Casts a wide net using 5+ data sources, then filters by strict criteria.
    Lanza una red amplia usando 5+ fuentes de datos, luego filtra por criterios estrictos.

    Returns a list of dicts:
      [{"mint": str, "name": str, "symbol": str, "age_days": float,
        "liquidity_usd": float, "volume_24h": float, "pair_address": str}, ...]
    """
    logger.info("🔍 Scanning DexScreener for Survivor tokens...")

    candidate_mints = set()

    # ── Source 1a: Latest boosted tokens ─────────────────────
    # Tokens that paid for promotion on DexScreener = real projects (usually)
    # Tokens que pagaron por promoción en DexScreener = proyectos reales
    try:
        resp = requests.get(
            f"{DEXSCREENER_API_URL}/token-boosts/latest/v1", timeout=15
        )
        resp.raise_for_status()
        boosts = resp.json()
        solana_boosts = [
            b["tokenAddress"] for b in boosts
            if b.get("chainId") == "solana" and b.get("tokenAddress")
        ]
        candidate_mints.update(solana_boosts)
        logger.info(f"  Boosted (latest): {len(solana_boosts)} Solana tokens")
    except Exception as e:
        logger.warning(f"  Boosted latest fetch failed: {e}")

    time.sleep(_DELAY)

    # ── Source 1b: Top boosted tokens (highest boost amount) ─
    # Tokens with the most money spent on promotion = higher signal
    # Tokens con más dinero gastado en promoción = mayor señal
    try:
        resp = requests.get(
            f"{DEXSCREENER_API_URL}/token-boosts/top/v1", timeout=15
        )
        resp.raise_for_status()
        boosts = resp.json()
        solana_top = [
            b["tokenAddress"] for b in boosts
            if b.get("chainId") == "solana" and b.get("tokenAddress")
        ]
        candidate_mints.update(solana_top)
        logger.info(f"  Boosted (top): {len(solana_top)} Solana tokens")
    except Exception as e:
        logger.warning(f"  Boosted top fetch failed: {e}")

    time.sleep(_DELAY)

    # ── Source 1c: Latest token profiles (newest listings) ───
    try:
        resp = requests.get(
            f"{DEXSCREENER_API_URL}/token-profiles/latest/v1", timeout=15
        )
        resp.raise_for_status()
        profiles = resp.json()
        solana_profiles = [
            p["tokenAddress"] for p in profiles
            if p.get("chainId") == "solana" and p.get("tokenAddress")
        ]
        candidate_mints.update(solana_profiles)
        logger.info(f"  Token profiles (latest): {len(solana_profiles)} Solana tokens")
    except Exception as e:
        logger.warning(f"  Token profiles fetch failed: {e}")

    time.sleep(_DELAY)

    # ── Source 2: Pump.fun graduated tokens ──────────────────
    # Tokens that "graduated" from bonding curve to Raydium = survived initial phase.
    # Sorted by market cap → catches best performers regardless of name.
    # Tokens que "graduaron" de bonding curve a Raydium = sobrevivieron la fase inicial.
    for offset in [0, 50]:
        try:
            resp = requests.get(
                f"{PUMPFUN_API_URL}/coins",
                params={
                    "limit": 50,
                    "offset": offset,
                    "sort": "market_cap",
                    "order": "DESC",
                    "complete": "true",       # graduated = completed bonding curve
                    "includeNsfw": "false",
                },
                timeout=15,
            )
            resp.raise_for_status()
            coins = resp.json()
            pf_mints = [c["mint"] for c in coins if c.get("mint")]
            candidate_mints.update(pf_mints)
            logger.info(f"  Pump.fun graduated (offset {offset}): {len(pf_mints)} tokens")
        except Exception as e:
            logger.warning(f"  Pump.fun graduated fetch failed: {e}")
        time.sleep(_DELAY)

    # ── Source 3: DexScreener keyword search (supplementary) ─
    # Cast a wider net by searching for meme-related terms.
    # Red más amplia buscando términos relacionados con memes.
    search_terms = [
        # Classic meme themes / Temas clásicos de memes
        "meme", "pump", "pepe", "doge", "cat", "dog",
        # Solana ecosystem / Ecosistema Solana
        "SOL", "solana", "bonk", "wif", "jup",
        # Trending narratives / Narrativas tendencia
        "ai", "gpt", "agent", "trump", "elon",
        # Meme culture / Cultura meme
        "moon", "inu", "shib", "frog", "bull", "bear",
        # Degen/viral
        "giga", "chad", "based", "wojak",
    ]
    for term in search_terms:
        try:
            resp = requests.get(
                f"{DEXSCREENER_API_URL}/latest/dex/search?q={term}",
                timeout=15
            )
            resp.raise_for_status()
            pairs = resp.json().get("pairs", [])
            for p in pairs:
                if p.get("chainId") == "solana":
                    addr = p.get("baseToken", {}).get("address")
                    if addr:
                        candidate_mints.add(addr)
            logger.info(f"  Search '{term}': found pairs on Solana")
        except Exception as e:
            logger.debug(f"  Search '{term}' failed: {e}")
        time.sleep(_DELAY)

    logger.info(f"  Total candidate mints collected: {len(candidate_mints)}")

    if not candidate_mints:
        logger.warning("⚠️  No candidate tokens found from any source.")
        return []

    # ── Fetch detailed pair data for all candidates ──────────
    # DexScreener /tokens/v1/solana/<addresses> supports up to 30 at once
    # DexScreener soporta hasta 30 direcciones por consulta
    all_pairs = []
    mint_list = list(candidate_mints)

    for i in range(0, len(mint_list), 30):
        batch = mint_list[i:i+30]
        addrs = ",".join(batch)
        try:
            resp = requests.get(
                f"{DEXSCREENER_API_URL}/tokens/v1/solana/{addrs}",
                timeout=15
            )
            resp.raise_for_status()
            pairs = resp.json()
            if isinstance(pairs, list):
                all_pairs.extend(pairs)
                logger.info(f"  Pair lookup batch {i//30 + 1}: {len(pairs)} pairs")
        except Exception as e:
            logger.warning(f"  Pair lookup batch failed: {e}")
        time.sleep(_DELAY)

    # ── Apply Survivor Filters ───────────────────────────────
    # Only keep tokens that match ALL criteria (age, liquidity, volume).
    # Solo mantener tokens que cumplan TODOS los criterios.
    now_ms = time.time() * 1000
    survivors = []
    seen_mints = set()

    for pair in all_pairs:
        if not isinstance(pair, dict):
            continue

        if pair.get("chainId") != "solana":
            continue

        # Age filter / Filtro de edad
        created_at = pair.get("pairCreatedAt")
        if not created_at:
            continue

        age_ms = now_ms - created_at
        age_days = age_ms / (1000 * 60 * 60 * 24)

        if age_days < MIN_TOKEN_AGE_DAYS or age_days > MAX_TOKEN_AGE_DAYS:
            continue

        # Liquidity filter / Filtro de liquidez
        liquidity = pair.get("liquidity", {})
        liquidity_usd = liquidity.get("usd", 0) if isinstance(liquidity, dict) else 0
        if liquidity_usd < MIN_LIQUIDITY_USD:
            continue

        # Volume filter / Filtro de volumen
        volume_24h = pair.get("volume", {}).get("h24", 0)
        if volume_24h < MIN_VOLUME_24H_USD:
            continue

        # Extract base token info (the meme token, not SOL)
        # Extraer info del token base (el meme token, no SOL)
        base_token = pair.get("baseToken", {})
        token_mint = base_token.get("address", "")
        token_name = base_token.get("name", "Unknown")
        token_symbol = base_token.get("symbol", "???")

        if not token_mint or token_mint in seen_mints:
            continue
        seen_mints.add(token_mint)

        survivors.append({
            "mint": token_mint,
            "name": token_name,
            "symbol": token_symbol,
            "age_days": round(age_days, 1),
            "liquidity_usd": round(liquidity_usd, 2),
            "volume_24h": round(volume_24h, 2),
            "pair_address": pair.get("pairAddress", ""),
        })

    # Sort by 24h volume descending, return top N
    # Ordenar por volumen 24h descendente, retornar top N
    survivors.sort(key=lambda x: x["volume_24h"], reverse=True)
    top = survivors[:TOP_TOKENS_LIMIT]

    if top:
        logger.info(f"✅ Found {len(top)} Survivor tokens:")
        for t in top:
            logger.info(
                f"   {t['symbol']} ({t['name']}) | "
                f"Age: {t['age_days']}d | "
                f"Liq: ${t['liquidity_usd']:,.0f} | "
                f"Vol24h: ${t['volume_24h']:,.0f}"
            )
    else:
        logger.warning("⚠️  No Survivor tokens found matching criteria.")

    return top


def discover_momentum_tokens() -> list[dict]:
    """
    Discovers "Momentum" tokens on Solana — fresh and fast-moving.
    Descubre tokens "Momentum" en Solana — frescos y de movimiento rápido.

    Criteria / Criterios:
      - Age: 1h - 24h (fresh but not brand new / frescos pero no recién creados)
      - Liquidity: > $10k
      - High volume relative to age / Alto volumen relativo a su edad
    """
    logger.info("🚀 Scanning DexScreener for MOMENTUM tokens (1h-24h)...")

    candidate_mints = set()

    # ── Source 1: Pump.fun bonding curve tokens ──────────────
    # Tokens still on bonding curve, sorted by market cap = fresh movers.
    # Tokens aún en bonding curve, ordenados por market cap = movimientos frescos.
    try:
        resp = requests.get(
            f"{PUMPFUN_API_URL}/coins",
            params={
                "limit": 50,
                "offset": 0,
                "sort": "market_cap",
                "order": "DESC",
                "complete": "false",  # NOT graduated = still on bonding curve
                "includeNsfw": "false",
            },
            timeout=15,
        )
        resp.raise_for_status()
        coins = resp.json()
        pf_mints = [c["mint"] for c in coins if c.get("mint")]
        candidate_mints.update(pf_mints)
        logger.info(f"  Pump.fun bonding curve (top mcap): {len(pf_mints)} tokens")
    except Exception as e:
        logger.warning(f"  Pump.fun bonding curve fetch failed: {e}")
    time.sleep(_DELAY)

    # ── Source 1b: Pump.fun newest tokens ────────────────────
    try:
        resp = requests.get(
            f"{PUMPFUN_API_URL}/coins",
            params={
                "limit": 50,
                "offset": 0,
                "sort": "last_trade_timestamp",
                "order": "DESC",
                "complete": "false",
                "includeNsfw": "false",
            },
            timeout=15,
        )
        resp.raise_for_status()
        coins = resp.json()
        pf_mints = [c["mint"] for c in coins if c.get("mint")]
        candidate_mints.update(pf_mints)
        logger.info(f"  Pump.fun bonding curve (last traded): {len(pf_mints)} tokens")
    except Exception as e:
        logger.warning(f"  Pump.fun last traded fetch failed: {e}")
    time.sleep(_DELAY)

    # ── Source 2: DexScreener latest token profiles ──────────
    try:
        resp = requests.get(
            f"{DEXSCREENER_API_URL}/token-profiles/latest/v1", timeout=15
        )
        resp.raise_for_status()
        profiles = resp.json()
        solana_profiles = [
            p["tokenAddress"] for p in profiles
            if p.get("chainId") == "solana" and p.get("tokenAddress")
        ]
        candidate_mints.update(solana_profiles)
        logger.info(f"  DexScreener profiles (latest): {len(solana_profiles)} Solana tokens")
    except Exception as e:
        logger.warning(f"  DexScreener profiles fetch failed: {e}")
    time.sleep(_DELAY)

    # ── Source 3: DexScreener keyword search ──────────────────
    search_terms = [
        "sol", "pump", "moon", "gem", "ape",
        "bull", "meme", "ai", "bonk", "cat",
        "new", "launch", "degen",
    ]
    for term in search_terms:
        try:
            resp = requests.get(
                f"{DEXSCREENER_API_URL}/latest/dex/search?q={term}",
                timeout=15
            )
            resp.raise_for_status()
            pairs = resp.json().get("pairs", [])
            for p in pairs:
                if p.get("chainId") == "solana":
                    addr = p.get("baseToken", {}).get("address")
                    if addr:
                        candidate_mints.add(addr)
            time.sleep(_DELAY)
        except Exception:
            pass

    if not candidate_mints:
        logger.warning("⚠️  No candidates found for Momentum search.")
        return []

    logger.info(f"  Momentum candidates collected: {len(candidate_mints)}")

    # ── Fetch detailed pair data ─────────────────────────────
    all_pairs = []
    mint_list = list(candidate_mints)

    for i in range(0, len(mint_list), 30):
        batch = mint_list[i:i+30]
        addrs = ",".join(batch)
        try:
            resp = requests.get(
                f"{DEXSCREENER_API_URL}/tokens/v1/solana/{addrs}",
                timeout=15
            )
            resp.raise_for_status()
            pairs = resp.json()
            if isinstance(pairs, list):
                all_pairs.extend(pairs)
        except Exception:
            pass
        time.sleep(_DELAY)

    # ── Apply Momentum Filters ───────────────────────────────
    now_ms = time.time() * 1000
    momentum_tokens = []
    seen_mints = set()

    for pair in all_pairs:
        if not isinstance(pair, dict) or pair.get("chainId") != "solana":
            continue

        created_at = pair.get("pairCreatedAt")
        if not created_at:
            continue

        age_ms = now_ms - created_at
        age_hours = age_ms / (1000 * 60 * 60)

        # 1. Age Filter (1h - 24h) / Filtro de edad
        if age_hours < MOMENTUM_MIN_AGE_HOURS or age_hours > MOMENTUM_MAX_AGE_HOURS:
            continue

        # 2. Liquidity Filter / Filtro de liquidez
        liquidity = pair.get("liquidity", {})
        liquidity_usd = liquidity.get("usd", 0) if isinstance(liquidity, dict) else 0
        if liquidity_usd < MOMENTUM_MIN_LIQUIDITY:
            continue

        # 3. Volume Check / Verificación de volumen
        volume_24h = pair.get("volume", {}).get("h24", 0)
        if volume_24h < MOMENTUM_MIN_VOLUME_1H:
            continue

        # Extract base token / Extraer token base
        base_token = pair.get("baseToken", {})
        token_mint = base_token.get("address", "")
        token_name = base_token.get("name", "Unknown")
        token_symbol = base_token.get("symbol", "???")

        if not token_mint or token_mint in seen_mints:
            continue
        seen_mints.add(token_mint)

        momentum_tokens.append({
            "mint": token_mint,
            "name": token_name,
            "symbol": token_symbol,
            "age_hours": round(age_hours, 1),
            "liquidity_usd": round(liquidity_usd, 2),
            "volume_24h": round(volume_24h, 2),
            "pair_address": pair.get("pairAddress", ""),
            "price_change_h1": pair.get("priceChange", {}).get("h1", 0),
        })

    # Sort by 1h price change (catch breakouts), then volume as tiebreaker
    # Ordenar por cambio de precio 1h (capturar breakouts), volumen como desempate
    momentum_tokens.sort(
        key=lambda x: (x.get("price_change_h1") or 0, x["volume_24h"]),
        reverse=True,
    )
    top = momentum_tokens[:MOMENTUM_TOKENS_LIMIT]

    if top:
        logger.info(f"🚀 Found {len(top)} MOMENTUM tokens:")
        for t in top:
            logger.info(
                f"   {t['symbol']} | Age: {t['age_hours']}h | "
                f"Liq: ${t['liquidity_usd']:,.0f} | "
                f"Vol: ${t['volume_24h']:,.0f} | "
                f"1h Change: {t['price_change_h1']}%"
            )
    else:
        logger.warning("⚠️  No Momentum tokens found matching criteria.")

    return top
