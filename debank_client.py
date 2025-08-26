
# debank_client.py
# Lightweight DeBank Pro API client for your Shadow NAV board
# Usage:
#   from debank_client import DebankClient
#   api = DebankClient(api_key="...", header_name="AccessKey")  # or set DEBANK_API_KEY env
#   api.get_total_balance("0x...")
#
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

DEFAULT_BASE_URL = "https://api.cloud.debank.com"

class DebankError(Exception):
    pass

class DebankClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        header_name: str = None,
        timeout: int = 20,
        max_retries: int = 3,
        backoff: float = 0.8,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("DEBANK_API_KEY")
        if not self.api_key:
            raise ValueError("Missing API key. Set DEBANK_API_KEY or pass api_key=...")
        # DeBank Cloud commonly uses 'AccessKey' header. Some accounts may use 'X-API-Key'.
        self.header_name = header_name or os.getenv("DEBANK_HEADER_NAME", "AccessKey")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff

    def _headers(self) -> Dict[str, str]:
        return {
            self.header_name: self.api_key,
            "accept": "application/json",
            "user-agent": "shadow-nav/1.0",
        }

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                r = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout)
                if r.status_code == 429:
                    # simple backoff on rate limit
                    time.sleep(self.backoff * attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                # Normalize both {data: ...} and direct lists/objects
                if isinstance(data, dict) and "data" in data:
                    return data["data"]
                return data
            except Exception as e:
                last_err = e
                time.sleep(self.backoff * attempt)
        raise DebankError(f"GET {path} failed after {self.max_retries} attempts: {last_err}")

    # ----------------
    # Wallet Summary
    # ----------------
    def get_total_balance(self, addr: str) -> Dict[str, Any]:
        return self._get("/v1/user/total_balance", {"id": addr})

    def get_chain_balance(self, addr: str, chain_id: str) -> Dict[str, Any]:
        return self._get("/v1/user/chain_balance", {"id": addr, "chain_id": chain_id})

    def get_used_chains(self, addr: str) -> List[Dict[str, Any]]:
        return self._get("/v1/user/used_chain_list", {"id": addr})

    # ----------------
    # Tokens (Coins in Wallet)
    # ----------------
    def get_token_list(self, addr: str, chain_id: str, is_all: bool = True) -> List[Dict[str, Any]]:
        return self._get("/v1/user/token_list", {"id": addr, "chain_id": chain_id, "is_all": str(is_all).lower()})

    def get_all_token_list(self, addr: str, is_all: bool = True) -> List[Dict[str, Any]]:
        return self._get("/v1/user/all_token_list", {"id": addr, "is_all": str(is_all).lower()})

    def get_token(self, addr: str, chain_id: str, token_id: str) -> Dict[str, Any]:
        return self._get("/v1/user/token", {"id": addr, "chain_id": chain_id, "token_id": token_id})

    # ----------------
    # DeFi Positions
    # ----------------
    def get_complex_protocol_list(self, addr: str, chain_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if chain_id:
            return self._get("/v1/user/complex_protocol_list", {"id": addr, "chain_id": chain_id})
        return self._get("/v1/user/all_complex_protocol_list", {"id": addr})

    def get_protocol_detail(self, addr: str, protocol_id: str) -> Dict[str, Any]:
        # "real-time" for a single protocol drilldown
        return self._get("/v1/user/protocol", {"id": addr, "protocol_id": protocol_id})

    # ----------------
    # Curves (sparklines)
    # ----------------
    def get_total_net_curve(self, addr: str) -> List[Dict[str, Any]]:
        return self._get("/v1/user/total_net_curve", {"id": addr})

    def get_chain_net_curve(self, addr: str, chain_id: str) -> List[Dict[str, Any]]:
        return self._get("/v1/user/chain_net_curve", {"id": addr, "chain_id": chain_id})

    # ----------------
    # Activity
    # ----------------
    def get_all_history_list(self, addr: str, start_time: Optional[int] = None, page_count: int = 50) -> List[Dict[str, Any]]:
        params = {"id": addr, "page_count": page_count}
        if start_time:
            params["start_time"] = start_time
        return self._get("/v1/user/all_history_list", params)

    def get_history_list(self, addr: str, chain_id: str, start_time: Optional[int] = None, page_count: int = 50) -> List[Dict[str, Any]]:
        params = {"id": addr, "chain_id": chain_id, "page_count": page_count}
        if start_time:
            params["start_time"] = start_time
        return self._get("/v1/user/history_list", params)

    # ----------------
    # Approvals / Allowances
    # ----------------
    def get_token_approvals(self, addr: str, chain_id: str) -> List[Dict[str, Any]]:
        return self._get("/v1/user/token_authorized_list", {"id": addr, "chain_id": chain_id})

    def get_nft_approvals(self, addr: str, chain_id: str) -> List[Dict[str, Any]]:
        return self._get("/v1/user/nft_authorized_list", {"id": addr, "chain_id": chain_id})

    # ----------------
    # Helpers to aggregate for UI
    # ----------------
    def summarize_wallet(self, addr: str) -> Dict[str, Any]:
        total = self.get_total_balance(addr)
        chains = self.get_used_chains(addr)
        positions = self.get_complex_protocol_list(addr)  # cached all chains
        return {
            "address": addr,
            "total_usd": total.get("usd_value"),
            "net_usd": total.get("net_usd_value") or total.get("usd_value"),
            "chains": chains,
            "positions": positions,
        }
