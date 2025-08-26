# shadow_nav_board.py
# Shadow NAV board: single-page UI (board + selected wallet details below)
# Now with per-wallet Comment Log (timestamped) beside every wallet.

import os
import time
import socket
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
        user_agent: str = "shadow-nav/board/1.2",
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

    # Endpoints used
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
    st.set_page_config(page_title="Shadow NAV Board (One Page)", layout="wide")

    # Sidebar
    st.sidebar.header("Setup")
    api_key = st.sidebar.text_input("DEBANK_API_KEY", value=os.getenv("DEBANK_API_KEY", ""), type="password")
    base_url = st.sidebar.text_input("Base URL", value=os.getenv("DEBANK_BASE_URL", DEFAULT_BASE_URL))
    header_name = st.sidebar.text_input("Header Name", value=os.getenv("DEBANK_HEADER_NAME", "AccessKey"))
    show_debug = st.sidebar.toggle("Show debug tracebacks", value=False)

    st.sidebar.divider()
    st.sidebar.caption(
        "Enter wallets (one per line). Formats:\n"
        "  Client, Wallet Label, 0xAddress\n"
        "  Wallet Label, 0xAddress\n"
        "  0xAddress  (auto-labeled Wallet N)"
    )
    wallets_text = st.sidebar.text_area(
        "Wallets",
        placeholder="Darren, Darren #1, 0x123...\nDarren, Darren #2, 0xabc...\nAlice, Wallet A, 0x456...",
        height=140,
    )

    # State
    if "wallets" not in st.session_state:
        st.session_state.wallets = []
    if "active_idx" not in st.session_state:
        st.session_state.active_idx = None
    if "refresh_nonce" not in st.session_state:
        st.session_state.refresh_nonce = 0
    if "comments" not in st.session_state:
        st.session_state.comments = {}  # addr -> list of {ts, text}

    def parse_wallets(text: str) -> List[Dict[str, str]]:
        items, i = [], 1
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            client, label, addr = None, None, None
            if len(parts) >= 3:
                client, label, addr = parts[0], parts[1], parts[2]
            elif len(parts) == 2:
                label, addr = parts[0], parts[1]
            else:
                addr = parts[0]
            label = label or f"Wallet {i}"
            client = client or "Unassigned"
            items.append({"client": client, "label": label, "addr": addr})
            i += 1
        return items

    col_sb1, col_sb2 = st.sidebar.columns(2)
    if col_sb1.button("Load Wallets"):
        st.session_state.wallets = parse_wallets(wallets_text)
        st.session_state.active_idx = None
    if col_sb2.button("Clear All"):
        st.session_state.wallets = []
        st.session_state.active_idx = None

    # Build client
    def build_client() -> Optional[DebankClient]:
        if not api_key:
            st.error("Please provide DEBANK_API_KEY.")
            return None
        try:
            return DebankClient(api_key=api_key, base_url=base_url, header_name=header_name)
        except Exception as e:
            st.error(f"Client init failed: {e}")
            if show_debug:
                st.exception(e)
            return None

    api = build_client()

    # Helpers
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

    def fmt_usd(v) -> str:
        try:
            return f"${float(v):,.2f}"
        except Exception:
            return "-"

    def position_rows(positions):
        rows = []
        for p in positions or []:
            usd = p.get("usd_value")
            if usd is None:
                total = 0.0
                for it in p.get("portfolio_item_list", []) or []:
                    stats = (it or {}).get("stats") or {}
                    v = stats.get("net_usd_value") or stats.get("usd_value") or stats.get("asset_usd_value") or 0
                    try:
                        total += float(v)
                    except Exception:
                        pass
                usd = total
            rows.append({
                "Protocol": p.get("name") or p.get("id"),
                "Chain": p.get("chain"),
                "USD Value": usd,
            })
        rows.sort(key=lambda r: (r["USD Value"] or 0), reverse=True)
        return rows

    def token_rows(tokens):
        rows = []
        for t in tokens or []:
            usd = t.get("usd_value")
            if usd is None:
                try:
                    usd = float(t.get("price") or 0) * float(t.get("amount") or 0)
                except Exception:
                    usd = 0.0
            rows.append({
                "Token": t.get("display_symbol") or t.get("symbol") or t.get("name"),
                "Chain": t.get("chain"),
                "Amount": t.get("amount"),
                "Price": t.get("price"),
                "USD Value": usd,
            })
        rows.sort(key=lambda r: (r["USD Value"] or 0), reverse=True)
        return rows

    # --------- PAGE: Board (top) + Selected Wallet Pane (bottom) ---------
    st.title("Shadow NAV Board — One Page")

    if not st.session_state.wallets:
        st.info("Add wallets in the sidebar and click **Load Wallets**.")
        st.stop()

    # Filters / actions
    clients = sorted({w["client"] for w in st.session_state.wallets})
    sel_clients = st.multiselect("Filter by Client", options=clients, default=clients)
    c1, c2, c3 = st.columns([1,1,3])
    if c1.button("Refresh balances"):
        st.session_state.refresh_nonce = int(time.time())

    selected_total_placeholder = c2.empty()
    selected_total_value = 0.0

    # Board list
    st.markdown("### Wallets")
    # Added a "Comments" column before Delete
    hdr = st.columns([2, 3, 4, 2, 1, 2, 1])
    hdr[0].markdown("**Client**")
    hdr[1].markdown("**Wallet**")
    hdr[2].markdown("**Address**")
    hdr[3].markdown("**Dollar Value**")
    hdr[4].markdown("**Select**")
    hdr[5].markdown("**Comments**")
    hdr[6].markdown("**Delete**")

    to_delete_idx = None
    for idx, w in enumerate(st.session_state.wallets):
        if w["client"] not in sel_clients:
            continue

        cols = st.columns([2, 3, 4, 2, 1, 2, 1])
        cols[0].write(w["client"])

        # Clicking label or address selects the wallet (details shown below)
        if cols[1].button(w["label"], key=f"open_label_{idx}"):
            st.session_state.active_idx = idx
        if cols[2].button(w["addr"], key=f"open_addr_{idx}"):
            st.session_state.active_idx = idx

        total = {"total_usd_value": None}
        if api:
            total = safe_call(f"{w['label']} total", api.get_total_balance, w["addr"]) or total
        cols[3].write(fmt_usd(total.get("total_usd_value") or total.get("usd_value") or 0))

        sel = cols[4].checkbox("", key=f"sel_{idx}")
        if sel:
            try:
                selected_total_value += float(total.get("total_usd_value") or total.get("usd_value") or 0)
            except Exception:
                pass

        # ----- Per-wallet Comment Log (timestamped) -----
        with cols[5].expander("💬 Log", expanded=False):
            addr = w["addr"]
            # show last 5 comments (most recent first)
            log = st.session_state.comments.get(addr, [])
            if log:
                for entry in reversed(log[-5:]):
                    st.write(f"- *{entry['ts']}*: {entry['text']}")
            else:
                st.caption("No comments yet.")
            new_text = st.text_input("Add a comment", key=f"cmt_input_{idx}", placeholder="e.g., Moved funds to Aave")
            if st.button("Save", key=f"cmt_save_{idx}"):
                tstamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                if new_text.strip():
                    st.session_state.comments.setdefault(addr, []).append({"ts": tstamp, "text": new_text.strip()})
                    st.success("Saved.")
                    st.rerun()
                else:
                    st.warning("Please type something before saving.")

        if cols[6].button("🗑", key=f"del_{idx}"):
            to_delete_idx = idx

    if to_delete_idx is not None:
        if st.session_state.active_idx == to_delete_idx:
            st.session_state.active_idx = None
        st.session_state.wallets.pop(to_delete_idx)
        st.rerun()

    selected_total_placeholder.metric("Selected Total Balance", fmt_usd(selected_total_value))

    # ---- Selected wallet details (appear below the board) ----
    st.markdown("---")
    st.markdown("### Selected Wallet")

    if st.session_state.active_idx is None or st.session_state.active_idx >= len(st.session_state.wallets):
        st.caption("Click a wallet label or address above to view details here.")
    else:
        w = st.session_state.wallets[st.session_state.active_idx]
        header_cols = st.columns([6,1,1])
        header_cols[0].markdown(f"**{w['client']} — {w['label']}**  \n`{w['addr']}`")
        if header_cols[1].button("↻ Refresh", key="detail_refresh"):
            st.session_state.refresh_nonce = int(time.time())
            st.rerun()
        if header_cols[2].button("Clear Selection", key="detail_clear"):
            st.session_state.active_idx = None
            st.rerun()

        # Dollar Value
        total = {"total_usd_value": 0}
        if api:
            total = safe_call("Total Balance", api.get_total_balance, w["addr"]) or total
        st.metric("Dollar Value", fmt_usd(total.get("total_usd_value") or total.get("usd_value") or 0))

        # Details: DeFi Positions + Token Holdings
        d1, d2 = st.columns(2)

        with d1:
            st.markdown("**DeFi Positions**")
            positions = safe_call("DeFi positions", api.get_complex_protocol_list, w["addr"]) or []
            st.dataframe(position_rows(positions), use_container_width=True)
            if show_debug:
                st.caption("Debug sample:"); st.json((positions or [])[:1])

        with d2:
            st.markdown("**Token Holdings**")
            tokens = safe_call("Coins in wallet", api.get_all_token_list, w["addr"], True) or []
            st.dataframe(token_rows(tokens)[:25], use_container_width=True)
            if show_debug:
                st.caption("Debug sample:"); st.json((tokens or [])[:1])

else:
    if __name__ == "__main__":
        print("Install Streamlit and run:")
        print("  pip install streamlit requests urllib3")
        print("  export DEBANK_API_KEY='YOUR_ACCESSKEY'")
        print("  streamlit run shadow_nav_board.py")
