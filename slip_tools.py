"""slip_tools.py - pure slip logic for /trim and /merge (no network, no Telegram).

Works on "legs": dicts built from a decoded SportyBet share response.
    leg = {eventId, marketId, outcomeId, specifier, title, market, pick, odds}
Feed the kept legs to sportybet_endpoint.create_code() to get a new share code.
"""
import math
from typing import Optional

EXACT_LIMIT = 16  # brute-force all subsets up to this many legs; greedy above
EPS = 1e-9


# ---------- decoded response -> legs ----------
def legs_from_decoded(data: dict) -> list:
    """Return one leg per selection in a fetch_code() result ([] if undecodable)."""
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    ticket = payload.get("ticket") or {}
    outcomes = payload.get("outcomes") or []
    by_event = {o.get("eventId"): o for o in outcomes if isinstance(o, dict)}

    legs = []
    for sel in ticket.get("selections") or []:
        ev = sel.get("eventId")
        detail = by_event.get(ev, {})
        market_name = pick_name = odds = market_spec = None
        for m in detail.get("markets") or []:
            if str(m.get("id")) != str(sel.get("marketId")):
                continue
            market_name = m.get("desc") or m.get("name")
            market_spec = m.get("specifier")
            for oc in m.get("outcomes") or []:
                if str(oc.get("id")) == str(sel.get("outcomeId")):
                    pick_name = oc.get("desc")
                    try:
                        odds = float(oc.get("odds"))
                    except (TypeError, ValueError):
                        odds = None
            break
        home, away = detail.get("homeTeamName"), detail.get("awayTeamName")
        legs.append({
            "eventId": ev,
            "marketId": str(sel.get("marketId")),
            "outcomeId": str(sel.get("outcomeId")),
            "specifier": sel.get("specifier") or market_spec,
            "title": f"{home} vs {away}" if home and away else str(ev),
            "market": market_name or str(sel.get("marketId")),
            "pick": pick_name or str(sel.get("outcomeId")),
            "odds": odds,
        })
    return legs


def total_odds(legs) -> float:
    t = 1.0
    for l in legs:
        t *= l["odds"]
    return t


# ---------- /trim ----------
def trim_to_target(legs, target: float, mode: str = "below"):
    """Pick the subset of legs whose combined odds best match `target`.

    mode "below": highest total that is <= target (default).
    mode "near" : closest total on either side.
    Ties prefer keeping more legs. Legs without odds must be removed by the caller.
    Returns (kept, removed, note) where note is "" or a short warning.
    """
    n = len(legs)
    if n == 0:
        return [], [], "No legs."
    odds = [l["odds"] for l in legs]
    if total_odds(legs) <= target * (1 + EPS) and mode == "below":
        return list(legs), [], ""

    best_idx = None
    if n <= EXACT_LIMIT:
        best_idx = _exact(odds, target, mode)
    else:
        best_idx = _greedy(odds, target, mode)

    note = ""
    if best_idx is None:  # even the lowest single leg is above target
        best_idx = [min(range(n), key=lambda i: odds[i])]
        note = "every single leg is above the target; kept the lowest-odds leg"
    kept = [legs[i] for i in sorted(best_idx)]
    removed = [legs[i] for i in range(n) if i not in best_idx]
    return kept, removed, note


def _score(prod: float, count: int, target: float, mode: str):
    """Lower is better. Returns None if the subset is not allowed."""
    if mode == "below":
        if prod > target * (1 + EPS):
            return None
        return (target - prod, -count)
    return (abs(math.log(prod / target)), -count)


def _exact(odds, target, mode):
    n = len(odds)
    prod = [1.0] * (1 << n)
    best, best_mask = None, 0
    for mask in range(1, 1 << n):
        low = mask & -mask
        prod[mask] = prod[mask ^ low] * odds[low.bit_length() - 1]
        sc = _score(prod[mask], bin(mask).count("1"), target, mode)
        if sc is not None and (best is None or sc < best):
            best, best_mask = sc, mask
    if best is None:
        return None
    return {i for i in range(n) if best_mask >> i & 1}


def _greedy(odds, target, mode):
    keep = set(range(len(odds)))

    def prod(s):
        p = 1.0
        for i in s:
            p *= odds[i]
        return p

    while len(keep) > 1 and prod(keep) > target * (1 + EPS):
        # prefer a removal that lands at/below target as high as possible
        cand = [(prod(keep - {i}), i) for i in keep]
        ok = [c for c in cand if c[0] <= target * (1 + EPS)]
        if ok:
            keep.discard(max(ok)[1])
            break
        keep.discard(max(keep, key=lambda i: odds[i]))
    if mode == "below" and prod(keep) > target * (1 + EPS):
        return None
    return keep


# ---------- /merge ----------
def merge_slips(slips):
    """Combine several slips (each a list of legs) into one.

    A multiple can't hold two picks from the same match, so:
      - identical pick repeated      -> dropped as a duplicate
      - different pick, same match   -> first one kept, later one reported as a conflict
    Returns (merged_legs, duplicates, conflicts).
    """
    merged, seen = [], {}
    duplicates, conflicts = [], []
    for slip in slips:
        for leg in slip:
            key = leg["eventId"]
            if key not in seen:
                seen[key] = leg
                merged.append(leg)
            elif (seen[key]["marketId"], seen[key]["outcomeId"], seen[key]["specifier"]) == (
                leg["marketId"], leg["outcomeId"], leg["specifier"]
            ):
                duplicates.append(leg)
            else:
                conflicts.append((leg, seen[key]))
    return merged, duplicates, conflicts


def leg_line(i: Optional[int], leg: dict, esc=lambda s: s) -> str:
    odds = f"{leg['odds']:.2f}" if leg.get("odds") is not None else "n/a"
    prefix = f"{i}. " if i else "- "
    return f"{prefix}{esc(leg['title'])} | {esc(str(leg['market']))}: <b>{esc(str(leg['pick']))}</b> @ {odds}"
