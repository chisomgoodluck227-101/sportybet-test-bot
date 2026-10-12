import asyncio
import logging
import os

from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import html
import re

from sportybet_endpoint import (
    NetError, SAMPLE_CODE, create_code, fetch_code, fetch_raw, format_decoded, trim_code,
)
from slip_tools import leg_line, legs_from_decoded, merge_slips, total_odds, trim_to_target
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
    "/raw CODE – show server IP + raw SportyBet response (debug)\n"
    "/trim CODE 5.0 – trim a slip down to target total odds (add 'near' for closest either side)\n"
    "/merge CODE1 CODE2 … – merge several codes into one new code"
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


# ---------- /trim and /merge ----------
async def _decode_legs(code: str):
    """Fetch + parse a code. Returns (legs, error_text)."""
    data = await asyncio.to_thread(fetch_code, code)
    legs = legs_from_decoded(data)
    if data.get("bizCode") not in (None, 10000) or not legs:
        reason = data.get("message") or data.get("_raw") or "no selections"
        return None, f"{code}: {str(reason)[:120]} (HTTP {data.get('_http_status', '?')})"
    return legs, None


async def _publish(legs):
    """Create a new share code from legs. Returns (code, url, error_text)."""
    res = await asyncio.to_thread(create_code, legs)
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    if res.get("bizCode") not in (None, 10000) or not data.get("shareCode"):
        reason = res.get("message") or res.get("innerMsg") or res.get("_raw") or "unknown error"
        return None, None, f"bizCode {res.get('bizCode')} | HTTP {res.get('_http_status')} | {str(reason)[:200]}"
    return data["shareCode"], data.get("shareURL"), None


async def trim_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    usage = "Usage: /trim CODE TARGET_ODDS [near]\nExample: /trim RGXG82 5.0"
    if len(args) < 2:
        await update.message.reply_text(usage)
        return
    code = trim_code(args[0])
    try:
        target = float(args[1].replace(",", "."))
        if target <= 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Target odds must be a number above 1.\n" + usage)
        return
    mode = "near" if len(args) > 2 and args[2].lower() == "near" else "below"
    if not code:
        await update.message.reply_text("I couldn't find a share code in that.\n" + usage)
        return

    status = await update.message.reply_text(f"Trimming {code} to ~{target:g}…")
    try:
        legs, err = await _decode_legs(code)
        if err:
            await status.edit_text(f"❌ Could not decode {html.escape(err)}", parse_mode="HTML")
            return
        priced = [l for l in legs if l["odds"] is not None]
        unpriced = [l for l in legs if l["odds"] is None]
        if not priced:
            await status.edit_text("❌ No selection on that slip has odds available right now.")
            return

        before = total_odds(priced)
        kept, removed, note = trim_to_target(priced, target, mode)
        removed = removed + unpriced
        esc = html.escape
        lines = [f"✂️ <b>Trim {esc(code)}</b> → target {target:g} ({'closest' if mode == 'near' else 'at or below'})",
                 f"Before: <b>{before:.2f}</b> ({len(legs)} sel) | After: <b>{total_odds(kept):.2f}</b> ({len(kept)} sel)"]
        if note:
            lines.append(f"⚠️ {esc(note)}")
        lines += ["", "<b>Kept</b>"] + [leg_line(i, l, esc) for i, l in enumerate(kept, 1)]
        if removed:
            lines += ["", "<b>Removed</b>"] + [leg_line(None, l, esc) for l in removed]

        if not removed:
            lines.append("\nNothing to trim - slip is already within target. No new code created.")
        else:
            new_code, url, perr = await _publish(kept)
            if perr:
                lines += ["", f"❌ Could not create new code: {esc(perr)}"]
            else:
                lines += ["", f"🔑 New code: <code>{esc(new_code)}</code>"]
                if url:
                    lines.append(f'<a href="{esc(url)}">Open slip on SportyBet</a>')
        await status.edit_text("\n".join(lines)[:3900], parse_mode="HTML", disable_web_page_preview=True)
    except NetError as exc:
        await status.edit_text(f"❌ Could not reach SportyBet: {type(exc).__name__}")
    except Exception:
        log.exception("Trim failed")
        await status.edit_text("❌ Unexpected error. Check the Render logs.")


async def merge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(context.args or [])
    tokens = [t for t in re.split(r"[\s,;]+", raw) if t]
    codes = []
    for t in tokens:
        c = trim_code(t)
        if c and c not in codes:
            codes.append(c)
    if len(codes) < 2:
        await update.message.reply_text("Usage: /merge CODE1 CODE2 [CODE3 …]")
        return

    status = await update.message.reply_text(f"Merging {len(codes)} codes…")
    try:
        slips, errors = [], []
        for c in codes:
            legs, err = await _decode_legs(c)
            if err:
                errors.append(err)
            else:
                slips.append(legs)
        esc = html.escape
        if errors:
            await status.edit_text("❌ Could not decode:\n" + "\n".join(esc(e) for e in errors),
                                   parse_mode="HTML")
            return

        merged, dups, conflicts = merge_slips(slips)
        priced = [l for l in merged if l["odds"] is not None]
        lines = [f"🔗 <b>Merged {len(codes)} codes</b> → {len(merged)} selections"]
        if dups:
            lines.append(f"Duplicates dropped: {len(dups)}")
        if conflicts:
            lines.append(f"Conflicts skipped (same match, different pick): {len(conflicts)}")
            for leg, kept_leg in conflicts:
                lines.append(f"  ⚠️ {esc(leg['title'])}: skipped {esc(str(leg['pick']))}, kept {esc(str(kept_leg['pick']))}")
        if len(priced) == len(merged):
            lines.append(f"Total odds: <b>{total_odds(merged):.2f}</b>")
        lines += [""] + [leg_line(i, l, esc) for i, l in enumerate(merged, 1)]

        new_code, url, perr = await _publish(merged)
        if perr:
            lines += ["", f"❌ Could not create new code: {esc(perr)}"]
        else:
            lines += ["", f"🔑 New code: <code>{esc(new_code)}</code>"]
            if url:
                lines.append(f'<a href="{esc(url)}">Open slip on SportyBet</a>')
        await status.edit_text("\n".join(lines)[:3900], parse_mode="HTML", disable_web_page_preview=True)
    except NetError as exc:
        await status.edit_text(f"❌ Could not reach SportyBet: {type(exc).__name__}")
    except Exception:
        log.exception("Merge failed")
        await status.edit_text("❌ Unexpected error. Check the Render logs.")


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
    app.add_handler(CommandHandler("trim", trim_command))
    app.add_handler(CommandHandler("merge", merge_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(on_error)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
