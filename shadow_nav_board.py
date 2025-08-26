# shadow_nav_board.py
# Shadow NAV board: Board view + Single wallet view per your UI spec.

import os
import time
import socket
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------- DeBank client (focused) ----------------

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
        user_agent: str = "shadow-nav/board/1.0",
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

    # ---- endpoints we need ----
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
    st.set_page_config(page_title="Shadow NAV Board", layout="wide")

    # ------------- Sidebar setup -------------
    st.sidebar.header("Setup")
    api_key = st.sidebar.text_input("DEBANK_API_KEY", value=os.getenv("DEBANK_API_KEY", ""), type="password")
    base_url = st.sidebar.text_input("Base URL", value=os.getenv("DEBANK_BASE_URL", DEFAULT_BASE_URL))
    header_name = st.sidebar.text_input("Header Name", value=os.getenv("DEBANK_HEADER_NAME", "AccessKey"))
    show_debug = st.sidebar.toggle("Show debug tracebacks", value=False)

    st.sidebar.divider()
    st.sidebar.caption("Enter wallets (one per line). Format options:\n"
                       "  Client, Wallet Label, 0xAddress\n"
                       "  Wallet Label, 0xAddress\n"
                       "  0xAddress  (auto-labeled Wallet N)")
    wallets_text = st.sidebar.text_area(
        "Wallets",
        placeholder="Darren, Darren #1, 0x123...\nDarren, Darren #2, 0xabc...\nDarren, Darren #3, 0xdef...\nAlice, Wallet A, 0x456...",
        height=140,
    )
    if "refresh_nonce" not in st.session_state:
        st.session_state.refresh_nonce = 0
    if "view" not in st.session_state:
        st.session_state.view = "board"   # "board" or "detail"
    if "active_idx" not in st.session_state:
        st.session_state.active_idx = None
    if "wallets" not in st.session_state:
        st.session_state.wallets = []     # list of dicts: {client, label, addr}
    if "comments" not in st.session_state:
        st.session_state.comments = {}    # addr -> list of {ts, text}

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
            if not label:
                label = f"Wallet {i}"
            if not client:
                client = "Unassigned"
            items.append({"client": client, "label": label, "addr": addr})
            i += 1
        return items

    col_sb1, col_sb2 = st.sidebar.columns(2)
    load = col_sb1.button("Load Wallets")
    clear = col_sb2.button("Clear All")
    if load:
        st.session_state.wallets = parse_wallets(wallets_text)
        st.session_state.view = "board"
        st.session_state.active_idx = None
    if clear:
        st.session_state.wallets = []
        st.session_state.view = "board"
        st.session_state.active_idx = None

    # ------------- Build client -------------
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

    # ------------- Helpers -------------
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

    # --- Table helpers (INDENTED inside `if st:`) ---
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
                "TVL": p.get("tvl"),
                "Site": p.get("site_url"),
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
                "Protocol": t.get("protocol_id") or "",
            })
        rows.sort(key=lambda r: (r["USD Value"] or 0), reverse=True)
        return rows

    # ------------- Views -------------
    st.title("Shadow NAV Board")

    if st.session_state.view == "board":
        # ===== Board View =====
        st.caption("Board view: filter by client, select to sum total, delete rows, click wallet to open detail.")

        if not st.session_state.wallets:
            st.info("Add wallets in the sidebar and click **Load Wallets**.")
            st.stop()

        # Filter by Client
        clients = sorted({w["client"] for w in st.session_state.wallets})
        sel_clients = st.multiselect("Filter by Client", options=clients, default=clients)

        # Refresh & Sum controls
        c1, c2, c3 = st.columns([1,1,2])
        if c1.button("Refresh balances"):
            st.session_state.refresh_nonce = int(time.time())

        selected_total_placeholder = c2.empty()
        selected_total_value = 0.0

        # Table-ish list
        st.markdown("### Wallets")
        hdr = st.columns([2, 3, 3, 2, 1, 1])
        hdr[0].markdown("**Client**")
        hdr[1].markdown("**Wallet**")
        hdr[2].markdown("**Address**")
        hdr[3].markdown("**Dollar Value**")
        hdr[4].markdown("**Select**")
        hdr[5].markdown("**Delete**")

        for idx, w in enumerate(st.session_state.wallets):
            if w["client"] not in sel_clients:
                continue

            cols = st.columns([2, 3, 3, 2, 1, 1])
            cols[0].write(w["client"])
            # Clicking the wallet label opens detail view
            if cols[1].button(w["label"], key=f"open_{idx}"):
                st.session_state.view = "detail"
                st.session_state.active_idx = idx
                st.rerun()

            cols[2].code(w["addr"], language=None)

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

            if cols[5].button("🗑", key=f"del_{idx}"):
                st.session_state.wallets.pop(idx)
                st.rerun()

        selected_total_placeholder.metric("Selected Total Balance", fmt_usd(selected_total_value))

    else:
        # ===== Single Wallet View =====
        idx = st.session_state.active_idx
        if idx is None or idx >= len(st.session_state.wallets):
            st.session_state.view = "board"
            st.rerun()

        w = st.session_state.wallets[idx]
        st.markdown(f"### {w['client']} — {w['label']}")

        topbar = st.columns([1,1,6,2])
        if topbar[0].button("← Back"):
            st.session_state.view = "board"
            st.session_state.active_idx = None
            st.rerun()
        if topbar[1].button("Refresh"):
            st.session_state.refresh_nonce = int(time.time())
            st.rerun()

        # Dollar Value
        total = {"total_usd_value": 0, "chain_list": []}
        if api:
            total = safe_call("Total Balance", api.get_total_balance, w["addr"]) or total
        st.metric("Total Balance (USD)", fmt_usd(total.get("total_usd_value") or total.get("usd_value") or 0))

        # DeFi Position & Coins
        c1, c2 = st.columns(2)

        with c1:
            st.markdown("**DeFi Positions (top 15 by USD)**")
            positions = safe_call("DeFi positions", api.get_complex_protocol_list, w["addr"]) or []
            st.dataframe(position_rows(positions)[:15], use_container_width=True)
            if show_debug:
                st.caption("Debug sample:")
                st.json((positions or [])[:1])

        with c2:
            st.markdown("**Coins in Wallet (top 25 by USD)**")
            tokens = safe_call("Coins in wallet", api.get_all_token_list, w["addr"], True) or []
            st.dataframe(token_rows(tokens)[:25], use_container_width=True)
            if show_debug:
                st.caption("Debug sample:")
                st.json((tokens or [])[:1])

        # Comment Log
        st.markdown("### Comment Log")
        addr = w["addr"]
        if addr not in st.session_state.comments:
            st.session_state.comments[addr] = []
        with st.expander("Add a comment"):
            comment_text = st.text_area("Note", placeholder="What happened to this wallet?", key=f"note_{idx}")
            if st.button("Save Comment", key=f"save_{idx}"):
                if comment_text.strip():
                    st.session_state.comments[addr].append({
                        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                        "text": comment_text.strip(),
                    })
                    st.success("Saved.")
                else:
                    st.warning("Please type something before saving.")

        if st.session_state.comments[addr]:
            st.markdown("**History**")
            for entry in reversed(st.session_state.comments[addr][-50:]):
                st.write(f"- *{entry['ts']}*: {entry['text']}")

else:
    if __name__ == "__main__":
        print("Install Streamlit and run:")
        print("  pip install streamlit requests urllib3")
        print("  export DEBANK_API_KEY='YOUR_ACCESSKEY'")
        print("  streamlit run shadow_nav_board.py")
