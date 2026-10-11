# SportyBet Endpoint Test Bot

A minimal Telegram bot built from scratch using the endpoints documented in `Sportybet endpoint.pdf`.

## Commands
- `/test` — checks the banned-events endpoint and attempts to retrieve the example share code `XML9Z7`.
- `/liability` — sends the example payload to `/orders/liability/check`.
- `/share` — attempts to create a share slip from the sample fixture IDs in the PDF.

The sample fixture IDs/share code may have expired. A failed sample request is not conclusive evidence that an endpoint is broken. This bot does not place a real-money wager.

## Deploy on Render
1. Upload `bot.py`, `requirements.txt`, and `render.yaml` to a new GitHub repository.
2. In Render, create a **Background Worker** from that repository.
3. Set the build command to `pip install -r requirements.txt`.
4. Set the start command to `python bot.py`.
5. Add environment variable `TELEGRAM_BOT_TOKEN` with the token from BotFather.
6. Deploy, then open your Telegram bot and send `/start`, followed by `/test`.

Do not put your Telegram token in the source code or commit it to GitHub.
