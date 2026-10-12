# SportyBet decode test bot

Telegram bot that trims pasted input to a share code and decodes it via
`GET https://www.sportybet.com/api/ng/orders/share/{code}`.

Commands: `/decode CODE`, `/test` (sample code), `/ping` (is SportyBet reachable from Render?), or just paste a code/link.

## Render setup
- Service type: **Web Service** (this bot opens a port on `$PORT`, so Render's port check passes).
  If you use a *Background Worker* instead, no port is needed, but it isn't free.
- Build: `pip install -r requirements.txt`  |  Start: `python bot.py`
- Env var: `TELEGRAM_BOT_TOKEN`
- Free web services sleep after ~15 min idle; use an uptime pinger on the Render URL to keep it awake.

## "Conflict: terminated by other getUpdates request"
Only ONE process may poll a token. Stop any local run, any old Render service
(old worker + new web service), and avoid manual deploys overlapping.
