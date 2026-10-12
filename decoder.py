"""Pure logic: trim input -> share code, fetch from SportyBet, format result.
No Telegram imports here so it can be tested on its own."""
import html
import re
import time
from datetime import datetime, timedelta, timezone

import requests

BASE_URL = "https://www.sportybet.com/api/ng/orders/share/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.sportybet.com",
    "Referer": "https://www.sportybet.com/ng/",
}
SAMPLE_CODE = "XML9Z7"  # from the endpoint PDF (may have expired)
WAT = timezone(timedelta(hours=1))  # Nigeria time


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
    """GET the share endpoint. Always returns a dict with diagnostics."""
    started = time.time()
    resp = requests.get(
        f"{BASE_URL}{code}",
        params={"_t": int(time.time() * 1000)},
        headers=HEADERS,
        timeout=timeout,
    )
    elapsed = round((time.time() - started) * 1000)
    try:
        body = resp.json()
    except ValueError:
        body = {"_raw": resp.text[:500]}
    if not isinstance(body, dict):
        body = {"_raw": str(body)[:500]}
    body["_http_status"] = resp.status_code
    body["_elapsed_ms"] = elapsed
    return body


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

    resp = requests.get(
        f"{BASE_URL}{code}",
        params={"_t": int(time.time() * 1000)},
        headers=HEADERS,
        timeout=timeout,
    )
    out.append(f"HTTP {resp.status_code} | {len(resp.content)} bytes")
    keep = ("content-type", "server", "biz-code", "biz-code-message",
            "request-country", "x-cache", "via", "location", "retry-after")
    for k, v in resp.headers.items():
        if k.lower() in keep:
            out.append(f"{k}: {v[:80]}")
    body = resp.text[:300].replace("\n", " ") or "(empty body)"
    out.append(f"Body: {body}")
    return "\n".join(out)
