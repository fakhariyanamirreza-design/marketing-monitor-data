# Marketing Monitor Agent

An agent that every few hours scans free sources for your marketing keywords
and reports matches to a Telegram group, storing full history in this private
repo.

## How it works

- Runs on a Freestyle VM (`marketing-monitor` slug).
- Every `interval_hours` it scans:
  - **Google News RSS** (free, no API key)
  - **Telegram public channels** (via `t.me/s/<channel>`)
  - **Web search** for `site:x.com`, `site:instagram.com`, `site:linkedin.com` (best-effort, no key)
- Sends a formatted report to your Telegram group.
- Appends the report to `data/<YYYY-MM>.md` and pushes to this repo.

## Setup

1. `config.example.json` → `config.json` and fill:
   - `keywords` — your brand/competitor/service queries
   - `telegram.bot_token` and `telegram.chat_id`
   - `sources.telegram_channels.channels` — public channel usernames (e.g. `"elonmusk"`)
2. `python3 monitor.py --once` to test a single cycle.
3. Run `python3 monitor.py` (loop) or set a cron every 3 hours.

Find the group `chat_id`: add the bot to the group, send a message, then
`python3 tools/find_chat_id.py`.

Note: `config.json` and `state.json` are gitignored. The token never leaves the
VM.