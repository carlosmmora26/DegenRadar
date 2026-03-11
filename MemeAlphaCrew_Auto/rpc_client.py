"""
rpc_client.py — El Enlace / The Link.

Solana RPC wrapper with three reliability layers:
Envoltorio de RPC de Solana con tres capas de confiabilidad:

  1. Multi-node rotation: Hops between providers when one gets rate-limited (429).
  2. Exponential backoff: Last resort when ALL nodes are saturated.
  3. Pacing: Minimum delay between calls to respect free-tier limits.

Supports Helius, Alchemy, QuickNode, Ankr, PublicNode, and the official
Solana mainnet RPC. Add as many providers as you want in .env.
"""
import time
import logging
import requests
from solana.rpc.api import Client
from solders.pubkey import Pubkey
from solders.signature import Signature
from MemeAlphaCrew_Auto.config import RPC_URLS, RPC_PACING_SECONDS

logger = logging.getLogger(__name__)


class SolanaRPCClient:
    """Multi-node RPC client with automatic rotation on rate limits."""

    def __init__(self, rpc_urls: list[str] = None):
        self.urls = rpc_urls or RPC_URLS
        self._current_index = 0
        self._clients: dict[str, Client] = {}
        self._last_call = 0.0

        logger.info(f"🌐 RPC Rotation initialized with {len(self.urls)} nodes:")
        for i, url in enumerate(self.urls):
            # Mask API keys in log output
            display = url.split("?")[0] + "?..." if "?" in url else url[:40] + "..."
            logger.info(f"   [{i+1}] {display}")

    @property
    def _current_url(self) -> str:
        return self.urls[self._current_index]

    @property
    def client(self) -> Client:
        """Lazy-initialize Client instances per URL."""
        url = self._current_url
        if url not in self._clients:
            self._clients[url] = Client(url)
        return self._clients[url]

    def _rotate(self):
        """Switch to the next RPC node in the list."""
        old_index = self._current_index
        self._current_index = (self._current_index + 1) % len(self.urls)
        old_display = self.urls[old_index].split("?")[0].split("/")[-1] or "node"
        new_display = self._current_url.split("?")[0].split("/")[-1] or "node"
        logger.info(f"  🔄 Rotating RPC: {old_display} → {new_display}")

    # ── Rate-limit aware request ──────────────────────────────
    def _request_with_backoff(self, method_name: str, *args, **kwargs):
        """
        Calls an RPC method with:
          1. Pacing (minimum delay between calls)
          2. Node rotation on 429 errors
          3. Exponential backoff only if ALL nodes are saturated
        """
        max_retries = len(self.urls) * 3  # 3 full rotations max
        base_delay = 2.0
        nodes_exhausted = 0

        # Pacing
        elapsed = time.time() - self._last_call
        if elapsed < RPC_PACING_SECONDS:
            time.sleep(RPC_PACING_SECONDS - elapsed)

        for attempt in range(max_retries):
            try:
                self._last_call = time.time()
                method = getattr(self.client, method_name)
                response = method(*args, **kwargs)

                if hasattr(response, 'value'):
                    return response

                if isinstance(response, dict):
                    if 'error' in response:
                        error_code = response['error'].get('code')
                        if error_code == -32005:
                            raise requests.exceptions.HTTPError("RPC rate limit (-32005)")
                    return response

                return response

            except Exception as e:
                error_str = str(e)
                cause_str = str(e.__cause__) if e.__cause__ else ""
                context_str = str(e.__context__) if e.__context__ else ""
                all_text = f"{error_str} {cause_str} {context_str}"

                is_rate_limit = any(hint in all_text for hint in [
                    "429", "Too Many Requests", "Rate limit",
                    "rate limit", "-32005"
                ])

                if is_rate_limit:
                    nodes_exhausted += 1

                    if nodes_exhausted < len(self.urls):
                        # Rotate to next node immediately (no sleep!)
                        self._rotate()
                        continue
                    else:
                        # All nodes exhausted — backoff before retrying
                        cycle = nodes_exhausted // len(self.urls)
                        delay = base_delay * (2 ** min(cycle, 5))
                        logger.warning(
                            f"⏳ All {len(self.urls)} nodes rate-limited. "
                            f"Sleeping {delay:.0f}s (cycle {cycle + 1})"
                        )
                        time.sleep(delay)
                        nodes_exhausted = 0  # Reset for next rotation cycle
                        self._rotate()
                else:
                    logger.error(f"RPC error [{type(e).__name__}]: {error_str}")
                    raise

        raise RuntimeError(
            f"Failed after {max_retries} attempts across "
            f"{len(self.urls)} nodes (all rate limited)"
        )

    # ── Public Methods ────────────────────────────────────────
    def get_balance(self, wallet_address: str):
        pubkey = Pubkey.from_string(wallet_address)
        return self._request_with_backoff("get_balance", pubkey)

    def get_signatures_for_address(self, address: str, limit: int = 1000):
        pubkey = Pubkey.from_string(address)
        return self._request_with_backoff(
            "get_signatures_for_address", pubkey, limit=limit
        )

    def get_transaction(self, signature):
        if isinstance(signature, str):
            signature = Signature.from_string(signature)
        return self._request_with_backoff(
            "get_transaction", signature,
            max_supported_transaction_version=0
        )

    def get_token_accounts_by_owner(self, wallet_address: str):
        pubkey = Pubkey.from_string(wallet_address)
        token_program = Pubkey.from_string(
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
        )
        return self._request_with_backoff(
            "get_token_accounts_by_owner",
            pubkey, program_id=token_program
        )

    # Known Solana program IDs to exclude from funding source detection
    _KNOWN_PROGRAMS = {
        "11111111111111111111111111111111",       # System Program
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # Token Program
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
        "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # Associated Token Program
        "ComputeBudget111111111111111111111111111111",     # Compute Budget
        "SysvarRent111111111111111111111111111111111",     # Sysvar Rent
        "SysvarC1ock11111111111111111111111111111111",     # Sysvar Clock
        "Vote111111111111111111111111111111111111111",     # Vote Program
    }

    def get_funding_source(self, wallet_address: str) -> str | None:
        """
        Attempts to find the wallet address that first sent SOL to this account.
        Returns the funding address or None if not found/error.
        """
        try:
            # Fetch signatures for this address
            sigs_response = self.get_signatures_for_address(wallet_address, limit=100)
            if not sigs_response or not sigs_response.value:
                return None

            # The last signature in the list is the oldest one (chronologically first)
            oldest_sig = sigs_response.value[-1].signature

            # Fetch the transaction details
            tx_response = self.get_transaction(oldest_sig)
            if not tx_response or not tx_response.value:
                return None

            tx_data = tx_response.value.transaction
            account_keys = tx_data.transaction.message.account_keys
            wallet_pubkey = Pubkey.from_string(wallet_address)

            for i, key in enumerate(account_keys):
                key_str = str(key)
                if key != wallet_pubkey and key_str not in self._KNOWN_PROGRAMS:
                    return key_str

            return None
        except Exception as e:
            logger.error(f"Error finding funding source for {wallet_address}: {e}")
            return None
