# shadow_nav_min.py
# Minimal DeBank Pro OpenAPI viewer:
# - Total Balance (+ top-5 chain breakdown)
# - Coins in Wallet (all chains)
# - DeFi Positions (all chains)
#
# Run:
#   pip install streamlit requests urllib3
#   export DEBANK_API_KEY="YOUR_ACCESSKEY"
#   streamlit run shadow_nav_min.py

import os
import socket
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------- DeBank client (trimmed to what we need) ----------------

DEFAULT_BASE_URL = "https://pro-openapi.debank.com"

class DebankError(Exception):
    pass

class DebankClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        header_name: Optional[str] = None,
        timeout: int = 20,
        max_retries: int = 3,
        backoff: float = 0.8,
        proxies: Optional[Dict[str, str]] = None,
        user_agent: str = "shadow-nav/mini/1.1",
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.getenv("DEBANK_API_KEY")
        if not self.api_key:
            raise ValueError("Missing API key. Set DEBANK_API_KEY or pass api_key=...")
        self.header_name = header_name or os.getenv("DEBANK_HEADER_NAME", "AccessKey")
        self.timeout = timeout

        self.session = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=backoff,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=50)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        if proxies:
            self.session.proxies.update(proxies)

        self.user_agent = user_agent

    def _headers(self) -> Dict[str, str]:
        return {
            self.header_name: self.api_key,
            "accept": "application/json",
            "user-agent": self.user_agent,
        }

    def _diagnose_dns(self) -> Optional[str]:
        try:
            host = self.base_url.split("://", 1)[-1].split("/", 1)[0]
            socket.gethostbyname(host)
            return None
        except Exception as e:
            return f"DNS failed for host in Base URL '{self.base_url}': {e}"

    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        dns_issue = self._diagnose_dns()
        if dns_issue:
            raise DebankError(dns_issue + ". Try VPN/proxy or change DNS (1.1.1.1 / 8.8.8.8).")
        try:
            r = self.session.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise DebankError(f"Network error calling {url}: {e}") from e
        if r.status_code == 429:
            raise DebankError("Rate limited by DeBank (HTTP 429). Reduce frequency or add backoff.")
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            try:
                body = r.json()
            except Exception:
                body = r.text[:500]
            raise DebankError(f"HTTP {r.status_code} on {path}: {body}") from e
        data = r.json()
        return data["data"] if isinstance(data, dict) and "data" in data else data

    # ---- endpoints used in this mini app ----
    def get_total_balance(self, addr: str) -> Dict[str, Any]:
        return self._get("/v1/user/total_balance", {"id": addr})

    def get_all_token_list(self, addr: str, is_all: bool = True) -> List[Dict[str, Any]]:
        return self._get("/v1/user/all_token_list", {"id": addr, "is_all": str(is_all).lower()})

    def get_complex_protocol_list(self, addr: str) -> List[Dict[str, Any]]:
        return self._get("/v1/user/all_complex_protocol_list", {"id": addr})

# ---------------- Streamlit UI ----------------

try:
    import streamlit as st
except Exception:
    st = None

if st:
    st.set_page_config(page_title="Shadow NAV – Minimal", layout="wide")

    st.sidebar.header("Setup")
    api_key = st.sidebar.text_input("DEBANK_API_KEY", value=os.getenv("DEBANK_API_KEY", ""), type="password")
    base_url = st.sidebar.text_input("Base URL", value=os.getenv("DEBANK_BASE_URL", DEFAULT_BASE_URL))
    header_name = st.sidebar.text_input("Header Name", value=os.getenv("DEBANK_HEADER_NAME", "AccessKey"))
    show_debug = st.sidebar.toggle("Show debug tracebacks", value=False)

    st.sidebar.divider()
    st.sidebar.caption("Enter one wallet per line. You can optionally label like: `Wallet 1, 0xabc...`")
    wallets_text = st.sidebar.text_area("Wallets", placeholder="Wallet 1, 0x123...\nWallet 2, 0xabc...\n0xdef... (auto-labeled)")
    go = st.sidebar.button("Load")

    st.title("Shadow NAV – Total, Coins & DeFi (Minimal)")

    def parse_wallets(text: str) -> List[Dict[str, str]]:
        items = []
        i = 1
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            label, addr = None, None
            if "," in line:
                parts = [p.strip() for p in line.split(",", 1)]
                if len(parts) == 2:
                    label, addr = parts
            elif ":" in line:
                parts = [p.strip() for p in line.split(":", 1)]
                if len(parts) == 2:
                    label, addr = parts
            else:
                addr = line
            label = label or f"Wallet {i}"
            items.append({"label": label, "addr": addr})
            i += 1
        return items

    def safe_call(msg, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DebankError as e:
            st.error(f"{msg}: {e}")
            if show_debug:
                st.exception(e)
        except Exception as e:
            st.error(f"{msg}: Unexpected error: {e}")
            if show_debug:
                st.exception(e)
        return None

    if go:
        if not api_key:
            st.error("Please provide DEBANK_API_KEY.")
            st.stop()

        try:
            api = DebankClient(api_key=api_key, base_url=base_url, header_name=header_name)
        except Exception as e:
            st.error(f"Client init failed: {e}")
            if show_debug:
                st.exception(e)
            st.stop()

        wallets = parse_wallets(wallets_text)
        if not wallets:
            st.warning("Add at least one wallet.")
            st.stop()

        for w in wallets:
            st.markdown(f"## {w['label']}")

            # ---- Total Balance + top-5 chains ----
            total = safe_call(f"{w['label']} total balance", api.get_total_balance, w["addr"]) or {}
            if total:
                total_usd = total.get("total_usd_value") or total.get("usd_value") or 0
                st.metric("Total Balance (USD)", f"{float(total_usd):,.2f}")
                chains = sorted(total.get("chain_list", []), key=lambda c: c.get("usd_value", 0), reverse=True)
                top5 = [{"chain": (c.get("name") or c.get("id")), "usd_value": c.get("usd_value")} for c in chains[:5]]
                st.json(top5)

            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**Coins in Wallet (all chains)**")
                tokens = safe_call(f"{w['label']} tokens", api.get_all_token_list, w["addr"], True) or []

                def token_usd(t):
                    if isinstance(t, dict):
                        if "usd_value" in t and isinstance(t["usd_value"], (int, float)):
                            return t["usd_value"]
                        price = t.get("price") or 0
                        amt = t.get("amount") or 0
                        try:
                            return float(price) * float(amt)
                        except Exception:
                            return 0.0
                    return 0.0

                tokens_sorted = sorted(tokens, key=token_usd, reverse=True)
                st.json(tokens_sorted[:20])

            with col2:
                st.markdown("**DeFi Positions (all chains, cached)**")
                positions = safe_call(f"{w['label']} positions", api.get_complex_protocol_list, w["addr"]) or []
                st.json(positions[:15])

else:
    if __name__ == "__main__":
        print("Install Streamlit and run:")
        print("  pip install streamlit requests urllib3")
        print("  export DEBANK_API_KEY='YOUR_ACCESSKEY'")
        print("  streamlit run shadow_nav_min.py")
