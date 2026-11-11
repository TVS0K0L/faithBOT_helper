#!/usr/bin/env python3
"""
faithBOT · cloud helper: the one-time Telegram sign-in.
Run it once on any computer:   pip3 install telethon   then   python3 login.py
It asks for your api_id and api_hash (from my.telegram.org), your phone number and the code Telegram sends you,
and prints one long line: your TG_SESSION. Paste it into GitHub as a secret. Treat it like a password:
anyone with it can read your Telegram. You can end it anytime in Telegram → Settings → Devices.
"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input('api_id (a number, from my.telegram.org): ').strip())
api_hash = input('api_hash (a long code): ').strip()
with TelegramClient(StringSession(), api_id, api_hash) as client:
    me = client.get_me()
    print('\n✅ Signed in as', me.first_name)
    print('\nYour TG_SESSION (copy the whole line below, keep it private):\n')
    print(client.session.save())
    print()
