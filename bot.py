import asyncio
import logging
import os

from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from decoder import NetError, SAMPLE_CODE, fetch_code, fetch_raw, format_decoded, trim_code
from healthcheck import start_health_server

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("sportybet-test-bot")

HELP = (
    "SportyBet decode test bot\n\n"
    "Send a share code or link, or use:\n"
    "/decode XML9Z7 – decode a code\n"
    "/test – decode the sample code from the PDF\n"
    "/ping – check if SportyBet is reachable from this server\n"
    "/raw CODE – show server IP + raw SportyBet response (debug)"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def run_decode(update: Update, raw: str):
    code = trim_code(raw)
    if not code:
        await update.message.reply_text("I couldn't find a share code in that. Send just the code.")
        return
    status = await update.message.reply_text(f"Decoding {code}…")
    try:
        data = await asyncio.to_thread(fetch_code, code)
        await status.edit_text(
            format_decoded(data, code), parse_mode="HTML", disable_web_page_preview=True
        )
    except NetError as exc:
        log.warning("SportyBet request failed: %s", exc)
        await status.edit_text(f"❌ Could not reach SportyBet: {type(exc).__name__}")
    except Exception:
        log.exception("Decode failed")
        await status.edit_text("❌ Unexpected error. Check the Render logs.")


async def decode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /decode XML9Z7")
        return
    await run_decode(update, " ".join(context.args))


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await run_decode(update, SAMPLE_CODE)


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await asyncio.to_thread(fetch_code, SAMPLE_CODE, 15)
        st = data.get("_http_status")
        verdict = "✅ reachable" if st == 200 else "⚠️ reachable but not 200 (likely blocked or code expired)"
        await update.message.reply_text(
            f"{verdict}\nHTTP {st} | bizCode {data.get('bizCode')} | {data.get('_elapsed_ms')} ms"
        )
    except NetError as exc:
        await update.message.reply_text(f"❌ Not reachable: {type(exc).__name__}: {exc}")


async def raw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = trim_code(" ".join(context.args)) if context.args else SAMPLE_CODE
    try:
        text = await asyncio.to_thread(fetch_raw, code)
        await update.message.reply_text(f"Code: {code}\n{text}"[:3900])
    except NetError as exc:
        await update.message.reply_text(f"❌ {type(exc).__name__}: {exc}"[:3900])


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await run_decode(update, update.message.text or "")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, Conflict):
        log.warning(
            "Telegram Conflict: another instance is polling with this token. "
            "Stop the other deploy/local run (this resolves itself once it stops)."
        )
        return
    log.error("Unhandled bot error", exc_info=context.error)


def main():
    if not TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in the environment.")

    port = start_health_server()  # satisfies Render's "open port" check
    log.info("Health server listening on port %s", port)

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("decode", decode_command))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("raw", raw_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(on_error)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
