import os
import time
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
BASE_URL = "https://www.sportybet.com/api/ng/orders/share/"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.sportybet.com",
    "Referer": "https://www.sportybet.com/ng/",
}

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


def decode_code(code: str) -> dict:
    url = f"{BASE_URL}{code}"
    response = requests.get(
        url,
        params={"_t": int(time.time() * 1000)},
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"_http_status": response.status_code, "_raw": response.text[:3000]}


def format_decoded(data: dict, code: str) -> str:
    if not isinstance(data, dict):
        return f"SportyBet returned an unexpected response for code {code}."

    biz_code = data.get("bizCode")
    message = data.get("message", "")
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    ticket = payload.get("ticket", {}) if isinstance(payload, dict) else {}
    selections = ticket.get("selections", []) if isinstance(ticket, dict) else []
    outcomes = payload.get("outcomes", []) if isinstance(payload, dict) else []

    if biz_code not in (None, 10000) or not selections:
        details = message or data.get("_raw") or "No selections were returned."
        return (
            f"❌ *Could not decode code:* `{code}`\n"
            f"Status: `{biz_code if biz_code is not None else data.get('_http_status', 'unknown')}`\n"
            f"Message: {str(details)[:700]}"
        )

    # Match event details returned in outcomes to the selection event IDs.
    by_event = {}
    for item in outcomes if isinstance(outcomes, list) else []:
        if isinstance(item, dict):
            by_event[item.get("eventId")] = item

    lines = [
        f"✅ *SportyBet code decoded*",
        f"Code: `{payload.get('shareCode', code)}`",
        f"Bet type: `{ticket.get('betType', payload.get('betType', 'Unknown'))}`",
        f"Selections: *{len(selections)}*",
        "",
    ]

    for idx, sel in enumerate(selections, 1):
        if not isinstance(sel, dict):
            continue
        event_id = sel.get("eventId", "Unknown event")
        detail = by_event.get(event_id, {})
        home = detail.get("homeTeamName")
        away = detail.get("awayTeamName")
        match = f"{home} vs {away}" if home and away else event_id
        market_id = sel.get("marketId", "?")
        outcome_id = sel.get("outcomeId", "?")
        odds = sel.get("odds")
        if odds is None:
            # Some responses keep market/outcome labels under markets.
            odds = detail.get("odds")
        lines.append(f"*{idx}. {match}*")
        lines.append(f"   Market ID: `{market_id}` | Outcome ID: `{outcome_id}`")
        if odds is not None:
            lines.append(f"   Odds: `{odds}`")

    share_url = payload.get("shareURL") if isinstance(payload, dict) else None
    if share_url:
        lines.extend(["", f"[Open slip on SportyBet]({share_url})"])

    return "\n".join(lines)[:3900]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "SportyBet Code Decoder\n\n"
        "Send a SportyBet share code (for example: XML9Z7), or use:\n"
        "/decode XML9Z7\n\n"
        "This retrieves the shared slip details from SportyBet. It does not place bets."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def decode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /decode SPORTYBET_CODE\nExample: /decode XML9Z7")
        return
    code = context.args[0].strip().upper()
    await handle_code(update, code)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return
    # Accept a code pasted by itself; reject long sentences to avoid accidental calls.
    if len(text) <= 40 and " " not in text and all(c.isalnum() or c in "-_" for c in text):
        await handle_code(update, text.upper())
    else:
        await update.message.reply_text(
            "Send only the SportyBet share code, or use /decode CODE."
        )


async def handle_code(update: Update, code: str):
    if not code or len(code) > 40 or not all(c.isalnum() or c in "-_" for c in code):
        await update.message.reply_text("That code format looks invalid. Send the share code only.")
        return
    status = await update.message.reply_text(f"Decoding SportyBet code `{code}`…", parse_mode="Markdown")
    try:
        data = await __import__("asyncio").to_thread(decode_code, code)
        result = format_decoded(data, code)
        await status.edit_text(result, parse_mode="Markdown", disable_web_page_preview=True)
    except requests.HTTPError as exc:
        code_status = exc.response.status_code if exc.response is not None else "unknown"
        await status.edit_text(
            f"❌ SportyBet returned HTTP {code_status} for `{code}`.\n"
            "The code may be invalid/expired, or SportyBet may be blocking the request.",
            parse_mode="Markdown",
        )
    except requests.RequestException as exc:
        log.warning("SportyBet request failed: %s", exc)
        await status.edit_text(
            "❌ Could not reach SportyBet right now. Check the endpoint/network and try again."
        )
    except Exception:
        log.exception("Decode failed")
        await status.edit_text("❌ Unexpected decoding error. Check the Render logs.")


def main():
    if not TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in the environment.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("decode", decode_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
