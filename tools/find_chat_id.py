#!/usr/bin/env python3
"""Add the bot to the group, then run this to print the chat_id.

Token comes from TELEGRAM_BOT_TOKEN env or --token, never from this file.
"""

import argparse
import json
import os
from urllib.request import urlopen, Request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    args = ap.parse_args()

    token = args.token
    if not token:
        print("No token. Start the bot, add it to the group, then run:")
        print("  $env:TELEGRAM_BOT_TOKEN='<token>' ; python tools/find_chat_id.py")
        raise SystemExit(1)

    req = Request(f"https://api.telegram.org/bot{token}/getUpdates?offset=-10")
    with urlopen(req, timeout=30) as r:
        updates = json.loads(r.read().decode()).get("result", [])

    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat", {})
        if chat:
            chats[chat.get("id")] = chat.get("title") or chat.get("username") or str(chat.get("id"))

    if chats:
        for cid, name in chats.items():
            print(f"chat_id: {cid}  name: {name}")
    else:
        print("No chats yet. Add the bot to the group and send a message, then rerun.")
        print(json.dumps(updates, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()