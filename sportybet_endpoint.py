"""sportybet_endpoint.py - SportyBet endpoint client (proven working, all PDF endpoints).

Covers every endpoint in Sportybet_endpoint.pdf:
  create_code()      POST /api/ng/orders/share?throwInvalidEvent=true   -> new share code
  fetch_code()       GET  /api/ng/orders/share/{code}?_t=...            -> decode a code
  liability_check()  POST /api/ng/orders/liability/check
  banned_events()    GET  /api/ng/factsCenter/banned/events?_t=...      -> list of banned event ids

Why it works: CloudFront fingerprints the TLS/HTTP2 handshake. Plain `requests`
gets an empty HTTP 202; `curl_cffi` impersonating Chrome gets the real JSON
from SportyBet's origin. Verified from Render (AWS IP): GET share -> HTTP 200.
(create/liability/banned use the same transport but are untested live.)

Install:  pip install curl_cffi requests
Library:  from sportybet_endpoint import (trim_code, fetch_code, format_decoded,
              create_code, liability_check, banned_events, NetError)
CLI:      python sportybet_endpoint.py decode RGXG82
          python sportybet_endpoint.py raw RGXG82
          python sportybet_endpoint.py banned
          python sportybet_endpoint.py create sr:match:72466526,1,2 sr:match:68774340,1,2
          python sportybet_endpoint.py liability sr:match:72466526,1,2
          (selection = eventId,marketId,outcomeId ; bare 72466526 is read as sr:match:72466526)
"""
import html
import re
import time
from datetime import datetime, timedelta, timezone

import random

import requests  # plain client: only used for the ipinfo diagnostic

try:  # browser-grade TLS/HTTP2 fingerprint (JA3/JA4) via curl_cffi
    from curl_cffi import requests as cffi
    from curl_cffi.requests.exceptions import RequestException as _CffiError
    HAS_CFFI = True
except ImportError:  # falls back to plain requests (will likely get 202'd)
    cffi = None
    _CffiError = requests.RequestException
    HAS_CFFI = False

# Catch this in bot.py instead of requests.RequestException
NetError = (requests.RequestException, _CffiError)

# Profile must match the User-Agent family. Tried in order until one is supported.
IMPERSONATE_PROFILES = ("chrome131_android", "chrome124", "chrome120", "chrome")

API = "https://www.sportybet.com/api/ng"
BASE_URL = f"{API}/orders/share/"  # GET + code
SHARE_URL = f"{API}/orders/share"  # POST create
LIABILITY_URL = f"{API}/orders/liability/check"
BANNED_URL = f"{API}/factsCenter/banned/events"
# No User-Agent here on purpose: impersonate= sends the matching one.
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.sportybet.com",
    "Referer": "https://www.sportybet.com/ng/",
}
SAMPLE_CODE = "XML9Z7"  # from the endpoint PDF (may have expired)
WAT = timezone(timedelta(hours=1))  # Nigeria time


# ---------- http layer ----------
_session = None
_profile = None


def _get_session():
    """One persistent session so cookies (e.g. WAF tokens) survive between calls."""
    global _session, _profile
    if _session is not None:
        return _session
    if not HAS_CFFI:
        _session = requests.Session()
        _session.headers["User-Agent"] = (
            "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
        )
        return _session
    for prof in IMPERSONATE_PROFILES:
        try:
            _session = cffi.Session(impersonate=prof)
            _profile = prof
            return _session
        except Exception:  # noqa: BLE001 - profile not in this curl_cffi build
            continue
    raise RuntimeError("No usable curl_cffi impersonation profile")


def _request(method: str, url: str, params=None, payload=None, timeout: int = 20, retries: int = 3):
    """Browser-fingerprinted request; retries empty/202 edge responses with jitter."""
    sess = _get_session()
    headers = dict(HEADERS)
    kwargs = {"params": params, "headers": headers, "timeout": timeout}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        kwargs["json"] = payload
    resp = None
    for attempt in range(retries):
        resp = sess.request(method, url, **kwargs)
        if resp.status_code != 202 and resp.content:
            return resp
        time.sleep(0.6 * (attempt + 1) + random.random() * 0.5)
    return resp


def http_get(url: str, params=None, timeout: int = 20, retries: int = 3):
    return _request("GET", url, params=params, timeout=timeout, retries=retries)


def http_post(url: str, payload, params=None, timeout: int = 20, retries: int = 3):
    return _request("POST", url, params=params, payload=payload, timeout=timeout, retries=retries)


def _as_dict(resp, started: float) -> dict:
    """Parse JSON body into a dict and attach diagnostics."""
    try:
        body = resp.json()
    except ValueError:
        body = {"_raw": resp.text[:500]}
    if not isinstance(body, dict):
        body = {"_raw": str(body)[:500]}
    body["_http_status"] = resp.status_code
    body["_elapsed_ms"] = round((time.time() - started) * 1000)
    return body


# ---------- trimming ----------
def trim_code(raw: str):
    """Extract a clean share code from whatever the user pasted.

    Handles: ' xml9z7 ', 'XML9Z7\\n', 'Code: XML9Z7',
    'http://www.sportybet.com/ng/?shareCode=XML9Z7', 'XML9 Z7'.
    Returns an upper-case code or None.
    """
    if not raw:
        return None
    text = raw.strip()

    # 1) full link with ?shareCode=...
    m = re.search(r"shareCode=([A-Za-z0-9]+)", text)
    if m:
        return m.group(1).upper()

    # 2) /orders/share/CODE style link
    m = re.search(r"/share/([A-Za-z0-9]+)", text)
    if m:
        return m.group(1).upper()

    # 3) message is a single token -> that's the code
    cleaned = re.sub(r"[^A-Za-z0-9\s]", " ", text).split()
    if len(cleaned) == 1 and 4 <= len(cleaned[0]) <= 12:
        return cleaned[0].upper()

    # 4) "label: CODE" -> last 5-8 char token that contains a digit
    tokens = [t for t in cleaned if 5 <= len(t) <= 8 and re.search(r"\d", t)]
    if tokens:
        return tokens[-1].upper()

    # 5) code split by a space, e.g. "XML9 Z7"
    joined = "".join(cleaned)
    if len(cleaned) <= 3 and all(len(t) <= 4 for t in cleaned) and 5 <= len(joined) <= 8 and re.search(r"\d", joined):
        return joined.upper()
    return None


# ---------- fetching ----------
def fetch_code(code: str, timeout: int = 20) -> dict:
    """GET /orders/share/{code}. Always returns a dict with diagnostics."""
    started = time.time()
    resp = http_get(f"{BASE_URL}{code}", params={"_t": int(time.time() * 1000)}, timeout=timeout)
    return _as_dict(resp, started)


# ---------- other endpoints ----------
def normalize_selection(sel) -> dict:
    """Accept 'eventId,marketId,outcomeId' or a dict; return an API selection dict."""
    if isinstance(sel, str):
        parts = [p.strip() for p in sel.split(",")]
        if len(parts) < 3:
            raise ValueError(f"Selection needs eventId,marketId,outcomeId: {sel!r}")
        sel = {"eventId": parts[0], "marketId": parts[1], "outcomeId": parts[2]}
    ev = str(sel["eventId"])
    if ev.isdigit():
        ev = f"sr:match:{ev}"
    return {
        "eventId": ev,
        "marketId": str(sel["marketId"]),
        "outcomeId": str(sel["outcomeId"]),
        "specifier": sel.get("specifier"),
    }


def selections_from_decoded(data: dict) -> list:
    """Pull re-creatable selections out of a fetch_code() result."""
    ticket = ((data.get("data") or {}).get("ticket")) or {}
    return [normalize_selection(s) for s in ticket.get("selections") or []]


def create_code(selections, bet_type: str = "MULTIPLE", stake: int = 1000000, timeout: int = 20) -> dict:
    """POST /orders/share?throwInvalidEvent=true -> new share code in data.shareCode.

    selections: list of 'eventId,marketId,outcomeId' strings or dicts.
    stake/orderType mirror the documented request (stake 1000000, orderType 2).
    """
    sels = []
    for s in selections:
        n = normalize_selection(s)
        n["stake"] = stake
        sels.append(n)
    payload = {"selections": sels, "orderType": 2, "betType": bet_type}
    started = time.time()
    resp = http_post(SHARE_URL, payload, params={"throwInvalidEvent": "true"}, timeout=timeout)
    return _as_dict(resp, started)


def liability_check(selections, sport_id: str = "sr:sport:1", timeout: int = 20) -> dict:
    """POST /orders/liability/check (documented processors: ["10"])."""
    sels = []
    for s in selections:
        n = normalize_selection(s)
        sels.append({
            "eventId": n["eventId"],
            "marketId": n["marketId"],
            "sportId": sport_id,
            "outcomeId": n["outcomeId"],
            "childSelections": [],
            "isLive": False,
        })
    payload = {"selections": sels, "liabilityCheckProcessors": ["10"]}
    started = time.time()
    resp = http_post(LIABILITY_URL, payload, timeout=timeout)
    return _as_dict(resp, started)


def banned_events(timeout: int = 20) -> list:
    """GET /factsCenter/banned/events -> list of banned event ids (empty on failure)."""
    resp = http_get(BANNED_URL, params={"_t": int(time.time() * 1000)}, timeout=timeout)
    try:
        body = resp.json()
    except ValueError:
        return []
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, list) else []


# ---------- formatting ----------
def _find_pick(detail: dict, market_id, outcome_id):
    """Look up market name, pick name and odds inside the event's markets."""
    for market in detail.get("markets", []) or []:
        if str(market.get("id")) != str(market_id):
            continue
        for oc in market.get("outcomes", []) or []:
            if str(oc.get("id")) == str(outcome_id):
                return market.get("desc") or market.get("name"), oc.get("desc"), oc.get("odds")
        return market.get("desc") or market.get("name"), None, None
    return None, None, None


def _kickoff(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, WAT).strftime("%d %b %H:%M")
    except (TypeError, ValueError, OSError):
        return None


def format_decoded(data: dict, code: str) -> str:
    """Return an HTML-formatted Telegram message."""
    esc = html.escape
    biz = data.get("bizCode")
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    ticket = payload.get("ticket") or {}
    selections = ticket.get("selections") or []
    outcomes = payload.get("outcomes") or []

    if biz not in (None, 10000) or not selections:
        reason = data.get("message") or data.get("_raw") or "No selections returned."
        return (
            f"❌ <b>Could not decode</b> <code>{esc(code)}</code>\n"
            f"HTTP: <code>{data.get('_http_status', '?')}</code> | "
            f"bizCode: <code>{biz}</code>\n"
            f"Message: {esc(str(reason)[:400])}"
        )

    by_event = {o.get("eventId"): o for o in outcomes if isinstance(o, dict)}
    lines = [
        "✅ <b>SportyBet code decoded</b>",
        f"Code: <code>{esc(str(payload.get('shareCode', code)))}</code>",
        f"Bet type: <code>{esc(str(ticket.get('betType', payload.get('betType', '?'))))}</code>",
        f"Selections: <b>{len(selections)}</b>",
        "",
    ]

    total = 1.0
    all_odds = True
    for i, sel in enumerate(selections, 1):
        ev = sel.get("eventId", "?")
        detail = by_event.get(ev, {})
        home, away = detail.get("homeTeamName"), detail.get("awayTeamName")
        title = f"{home} vs {away}" if home and away else ev
        market, pick, odds = _find_pick(detail, sel.get("marketId"), sel.get("outcomeId"))

        lines.append(f"<b>{i}. {esc(title)}</b>")
        tour = (((detail.get("sport") or {}).get("category") or {}))
        league = (tour.get("tournament") or {}).get("name")
        if league:
            lines.append(f"   🏆 {esc(tour.get('name', ''))} – {esc(league)}")
        ko = _kickoff(detail.get("estimateStartTime"))
        if ko:
            lines.append(f"   🕒 {ko} (WAT)")
        lines.append(
            f"   Market: {esc(str(market or sel.get('marketId')))} | "
            f"Pick: <b>{esc(str(pick or sel.get('outcomeId')))}</b>"
        )
        if odds is not None:
            lines.append(f"   Odds: <code>{esc(str(odds))}</code>")
            try:
                total *= float(odds)
            except ValueError:
                all_odds = False
        else:
            all_odds = False
        lines.append("")

    if all_odds and len(selections) > 1:
        lines.append(f"📊 Total odds: <b>{total:.2f}</b>")
    url = payload.get("shareURL")
    if url:
        lines.append(f'<a href="{esc(url)}">Open slip on SportyBet</a>')
    lines.append(f"<i>fetched in {data.get('_elapsed_ms', '?')} ms</i>")
    return "\n".join(lines)[:3900]


# ---------- diagnostics ----------
def fetch_raw(code: str, timeout: int = 20) -> str:
    """Plain-text diagnostic: where this server appears to be, and exactly what
    SportyBet sent back. Used by /raw to debug blocks (e.g. HTTP 202)."""
    out = []
    try:
        ip = requests.get("https://ipinfo.io/json", timeout=10).json()
        out.append(f"Server IP: {ip.get('ip')} | {ip.get('country')} | {ip.get('org')}")
    except Exception as exc:  # noqa: BLE001
        out.append(f"IP lookup failed: {type(exc).__name__}")

    resp = http_get(
        f"{BASE_URL}{code}",
        params={"_t": int(time.time() * 1000)},
        timeout=timeout,
    )
    out.append(f"Client: {'curl_cffi/' + str(_profile) if HAS_CFFI else 'plain requests (no fingerprint)'}")
    out.append(f"HTTP {resp.status_code} | {len(resp.content)} bytes")
    keep = ("content-type", "server", "biz-code", "biz-code-message",
            "request-country", "x-cache", "via", "location", "retry-after")
    for k, v in resp.headers.items():
        if k.lower() in keep:
            out.append(f"{k}: {v[:80]}")
    body = resp.text[:300].replace("\n", " ") or "(empty body)"
    out.append(f"Body: {body}")
    return "\n".join(out)


# ---------- CLI (handy in Termux) ----------
def _plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(text))


if __name__ == "__main__":
    import json
    import sys

    argv = [a for a in sys.argv[1:] if a != "--raw"]
    cmd = argv[0].lower() if argv else "decode"
    if cmd in ("decode", "raw", "banned", "create", "liability"):
        argv = argv[1:]
    else:
        cmd = "raw" if "--raw" in sys.argv else "decode"
    try:
        if cmd == "banned":
            ids = banned_events()
            print(f"{len(ids)} banned events")
            print("\n".join(ids[:20]) + ("\n..." if len(ids) > 20 else ""))
        elif cmd in ("create", "liability"):
            if not argv:
                sys.exit("Give at least one selection: eventId,marketId,outcomeId")
            res = create_code(argv) if cmd == "create" else liability_check(argv)
            if cmd == "create":
                d = res.get("data") or {}
                print(f"HTTP {res['_http_status']} | bizCode {res.get('bizCode')} | {res.get('message')}")
                print("Share code:", d.get("shareCode"), "|", d.get("shareURL"))
            else:
                print(json.dumps(res, indent=2)[:1500])
        else:
            code = trim_code(" ".join(argv)) if argv else SAMPLE_CODE
            if not code:
                sys.exit("No share code found in input.")
            if cmd == "raw":
                print(fetch_raw(code))
            else:
                print(_plain(format_decoded(fetch_code(code), code)))
    except NetError as exc:
        sys.exit(f"Network error: {type(exc).__name__}: {exc}")
