import os
import time
import json
import logging
from typing import Any

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("sportybet-test-bot")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
API_BASE = "https://www.sportybet.com/api/ng"
HTTP_TIMEOUT = 15

# Example IDs copied from the supplied endpoint capture. They may be expired now.
SAMPLE_SELECTIONS = [
    {
        "eventId": "sr:match:72466526",
        "marketId": "1",
        "specifier": None,
        "outcomeId": "2",
        "stake": 1000000,
    },
    {
        "eventId": "sr:match:68774340",
        "marketId": "1",
        "specifier": None,
        "outcomeId": "2",
        "stake": 1000000,
    },
    {
        "eventId": "sr:match:72388006",
        "marketId": "1",
        "specifier": None,
        "outcomeId": "2",
        "stake": 1000000,
    },
]

LIABILITY_SELECTIONS = [
    {
        "eventId": item["eventId"],
        "marketId": item["marketId"],
        "sportId": "sr:sport:1",
        "outcomeId": item["outcomeId"],
        "childSelections": [],
        "isLive": False,
    }
    for item in SAMPLE_SELECTIONS
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.sportybet.com",
    "Referer": "https://www.sportybet.com/ng/",
})


def api_request(method: str, path: str, payload: dict | None = None):
    url = f"{API_BASE}{path}"
    started = time.perf_counter()
    try:
        response = SESSION.request(
            method,
            url,
            params={"_t": int(time.time() * 1000)} if method.upper() == "GET" else None,
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
        elapsed = round((time.perf_counter() - started) * 1000)
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text[:1200]
        return {
            "ok": response.ok,
            "status": response.status_code,
            "ms": elapsed,
            "body": body,
        }
    except requests.RequestException as exc:
        return {"ok": False, "status": None, "ms": None, "error": str(exc)}


def short_result(label: str, result: dict) -> str:
    status = result.get("status")
    ms = result.get("ms")
    timing = f"{ms} ms" if ms is not None else "n/a"
    if status is None:
        return f"{label}: CONNECTION ERROR\nTime: {timing}\n{result.get('error', 'Unknown error')}"
    body = result.get("body")
    if isinstance(body, dict):
        biz = body.get("bizCode", "n/a")
        message = body.get("message", "")
        available = body.get("isAvailable", "n/a")
        detail = f"bizCode: {biz}\nAvailable: {available}\nMessage: {message}"
    else:
        sample = str(body or "").replace("\n", " ")[:240]
        detail = f"Body: {sample}"
    return f"{label}: HTTP {status}\nTime: {timing}\n{detail}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "SportyBet Endpoint Test Bot\n\n"
        "Commands:\n"
        "/test — basic API reachability and share-code retrieval\n"
        "/liability — test the documented liability-check endpoint\n"
        "/share — try creating a share slip with sample selections\n"
        "/help — show this menu\n\n"
        "This is a diagnostic test only. It does not place a real-money bet. "
        "The sample event IDs in the PDF may be expired."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text("Testing SportyBet API from the Render bot...")
    # Endpoint explicitly documented in the supplied PDF.
    banned = api_request("GET", "/factsCenter/banned/events")
    # Sample share code copied from the capture; it may no longer exist.
    share = api_request("GET", "/orders/share/XML9Z7")

    text = (
        "SPORTYBET API TEST\n\n"
        + short_result("1) Banned-events endpoint", banned)
        + "\n\n"
        + short_result("2) Retrieve sample share code XML9Z7", share)
        + "\n\nInterpretation:\n"
        "• HTTP response means Render reached the server.\n"
        "• HTTP 200 with bizCode 10000 indicates API-level success for that request.\n"
        "• 403/429 may indicate anti-bot/rate limiting; 404 or an API error for XML9Z7 "
        "may simply mean the sample code expired.\n"
        "• This test does not confirm that every endpoint or real-money betting works."
    )
    await msg.edit_text(text)


async def liability_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text("Testing liability/check...")
    result = api_request(
        "POST",
        "/orders/liability/check",
        {
            "selections": LIABILITY_SELECTIONS,
            "liabilityCheckProcessors": ["10"],
        },
    )
    await msg.edit_text(
        "LIABILITY CHECK TEST\n\n"
        + short_result("POST /orders/liability/check", result)
        + "\n\nThe captured event IDs are examples from the PDF and may be expired. "
        "An unavailable-selection response does not by itself prove the endpoint is broken."
    )


async def share_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text("Trying share-slip creation with sample selections...")
    result = api_request(
        "POST",
        "/orders/share?throwInvalidEvent=true",
        {
            "selections": SAMPLE_SELECTIONS,
            "orderType": 2,
            "betType": "MULTIPLE",
        },
    )
    body = result.get("body")
    extra = ""
    if isinstance(body, dict):
        data = body.get("data") or {}
        code = data.get("shareCode")
        url = data.get("shareURL")
        if code:
            extra = f"\n\nShare code: {code}"
        if url:
            extra += f"\nShare URL: {url}"
    await msg.edit_text(
        "SHARE-SLIP CREATION TEST\n\n"
        + short_result("POST /orders/share", result)
        + extra
        + "\n\nThis creates a shareable slip only; it does not submit a real-money wager. "
        "The example fixture IDs may be expired."
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled bot error", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(CommandHandler("liability", liability_command))
    app.add_handler(CommandHandler("share", share_command))
    app.add_error_handler(error_handler)
    log.info("Starting SportyBet endpoint test bot using Telegram long polling")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
