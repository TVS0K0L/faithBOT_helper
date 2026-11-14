# one-off: what the church's channels posted in the last 3 weeks (read only)
import asyncio, os, re, time
from telethon import TelegramClient
from telethon.sessions import StringSession
CH = [('-1001517620980', 'morning'), ('HolyCapital', 'gornitsa'), ('loveofchrist_church', 'public')]
async def main():
    async with TelegramClient(StringSession(os.environ['TG_SESSION']), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH']) as c:
        await c.get_dialogs()
        for ch, k in CH:
            try:
                e = await c.get_entity(int(ch) if re.match(r'^-?\d+$', ch) else ch)
            except Exception as x:
                print('##', ch, 'not reachable:', x); continue
            print('##', k, ch, getattr(e, 'title', ''))
            async for m in c.iter_messages(e, limit=200):
                if time.time() - m.date.timestamp() > 21 * 86400: break
                dur = next((a.duration for a in (m.document.attributes or []) if hasattr(a, 'duration')), 0) if m.document else 0
                links = [getattr(x, 'url', None) for x in (m.entities or []) if getattr(x, 'url', None)] + re.findall(r'https?://\S+', m.message or '')
                wp = getattr(getattr(m, 'media', None), 'webpage', None)
                if getattr(wp, 'url', None): links.append(wp.url)
                media = 'VIDEO %dmin' % round(dur / 60) if m.video else ('DOC ' + (m.file.mime_type or '')) if m.document else ('PHOTO' if m.photo else '')
                print(m.id, m.date.strftime('%d.%m %H:%M'), 'grp=%s' % m.grouped_id if m.grouped_id else '', media, '|', re.sub(r'\s+', ' ', m.message or '')[:150], '|', ' '.join(l for l in links if l)[:160])
asyncio.run(main())
