# SportyBet Code Decoder Telegram Bot

This is a Telegram interface for decoding SportyBet share codes using the endpoint documented in the supplied endpoint PDF:

`GET https://www.sportybet.com/api/ng/orders/share/{shareCode}?_t={timestamp}`

## Telegram usage
- `/start` — instructions
- `/decode XML9Z7` — decode a share code
- Or send the share code by itself, e.g. `XML9Z7`

The bot retrieves the shared ticket details returned by SportyBet. It does not place bets or access a user's account. A code can fail if it is invalid, expired, removed, or the endpoint blocks the request.

## Deploy on Render
1. Push this folder to a GitHub repository.
2. On Render, create a **Background Worker** from that repository.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python bot.py`
5. Add environment variable `TELEGRAM_BOT_TOKEN` with your BotFather token.
6. Deploy and open your Telegram bot.

Do not commit your Telegram token to GitHub.
