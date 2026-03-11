import os
from dotenv import load_dotenv

load_dotenv()

# RPC Configuration
# Free tier Helius or Alchemy recommended
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

# Thresholds for Filters
MAX_AVG_BUY_SIZE_SOL = 5.0
MAX_CURRENT_SOL_BALANCE = 100.0
MIN_UNIQUE_TOKENS_TRADED = 5
MAX_TX_HISTORY_LIMIT = 50

# Human Check Thresholds
BOT_WIN_RATE_THRESHOLD = 1.0  # 100% win rate is suspicious
GAMBLER_WIN_RATE_THRESHOLD = 0.2  # < 20% win rate is a gambler

# Scoring Weights
WEIGHT_REALIZED_PNL = 0.4
WEIGHT_CONSISTENCY = 0.3
WEIGHT_WIN_RATE = 0.3

# Raydium AMM V4 Program ID
RAYDIUM_V4_PROGRAM_ID = "675k1q2wE9S7n3CH678Dv7GTvA1YDzDDCa7615nWKgxU"
SOL_MINT = "So11111111111111111111111111111111111111112"

# Local Storage
STORAGE_DIR = "data"
WALLETS_DATA_FILE = os.path.join(STORAGE_DIR, "wallets_data.json")

# Create storage dir if not exists
if not os.path.exists(STORAGE_DIR):
    os.makedirs(STORAGE_DIR)
