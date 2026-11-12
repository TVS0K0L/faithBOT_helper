# One-off: what kinds of posts are in the church group (counts only; no titles printed)
import os, re, asyncio, collections
from telethon import TelegramClient
from telethon.sessions import StringSession
async def main():
    async with TelegramClient(StringSession(os.environ['TG_SESSION']), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH']) as c:
        await c.get_dialogs()
        e = await c.get_entity(-1001517620980)
        cnt = collections.Counter(); first = {}; last = {}; total = 0
        async for m in c.iter_messages(e, limit=None):
            total += 1
            t = (m.message or '')
            k = 'kafedra' if re.search(r'кафедр', t, re.I) else 'morning' if re.search(r'Утреннее\s+сосредоточение', t, re.I) else 'gornitsa' if re.search(r'горниц', t, re.I) else None
            if not k: continue
            media = 'video' if m.video else 'link' if re.search(r'youtu|vk\.com|vkvideo', t + str(getattr(getattr(m, 'media', None), 'webpage', '') or ''), re.I) else 'other'
            if re.search(r'форум', t, re.I): media += '+forum'
            key = k + ':' + media
            cnt[key] += 1; last[key] = m.date.date(); first.setdefault(key, m.date.date())
        print('messages', total)
        for k, v in sorted(cnt.items()): print(k, v, 'from', last[k], 'to', first[k])
asyncio.run(main())
