#!/usr/bin/env python3
"""
faithBOT · cloud helper (runs on GitHub, every 30 minutes; no Mac needed).

1. Telegram: watches the church's channels with your Telegram account (the same rules as the Mac helper). A new main video
   that faithBOT can't fetch itself (too big, in a private group, or a VK / YouTube link in the post) is downloaded, turned into a small MP3 and put straight into
   Google Drive → faithBOT → Входящие → <kind>. faithBOT then makes the notes as usual.
2. SoundCloud: KingdomCity Dubai sermons too big for Google to fetch (for you and Diana only).
3. YouTube: the videos faithBOT is waiting for (the Кафедра, the ministries) are fetched as audio only and put next to
   its list. If YouTube refuses (it sometimes blocks cloud computers), faithBOT simply uses YouTube itself after 3 hours.

faithBOT gives a one-time upload link for each file, so no Google password or key is stored here.
Secrets (GitHub → Settings → Secrets and variables → Actions):
  TG_API_ID, TG_API_HASH, TG_SESSION (from login.py), FAITHBOT_URL and FAITHBOT_KEY (from faithBOT: ⚙️ More → ☁️ Cloud helper)
"""
import asyncio, glob, json, os, re, shutil, subprocess, sys, tempfile, time
import requests

URL = os.environ.get('FAITHBOT_URL', '').strip()
KEY = os.environ.get('FAITHBOT_KEY', '').strip()
MIN_MB = 45          # smaller videos faithBOT fetches from the channel itself
RECENT_DAYS = 3      # only posts this new are considered
TMP = tempfile.mkdtemp(prefix='faithbot-')
STARTED = time.time()
BUDGET_MIN = 32       # the backlog stops after this many minutes of a run (GitHub allows 45)


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def bot(a, **body):
    body['a'] = a
    for attempt in range(3):
        try:
            r = requests.post(URL, params={'helper': '1', 'k': KEY}, data=json.dumps(body), timeout=120,
                              headers={'Content-Type': 'text/plain;charset=utf-8'})
            return r.json()
        except Exception as e:  # Apps Script answers through a redirect; retry on a hiccup
            log('faithBOT not reachable (%s), retrying' % e)
            time.sleep(5 * (attempt + 1))
    return {}


def tell(text, notify=False):
    log(text)
    bot('log', t=text, notify=notify)


def ffmpeg():
    p = shutil.which('ffmpeg')
    if p:
        return p
    log('installing ffmpeg…')
    subprocess.run(['sudo', 'apt-get', 'install', '-y', '-qq', 'ffmpeg'], check=False, capture_output=True)
    return shutil.which('ffmpeg') or 'ffmpeg'


def to_mp3(src, dst):
    r = subprocess.run([ffmpeg(), '-nostdin', '-loglevel', 'error', '-y', '-i', src, '-vn', '-ac', '1', '-ar', '22050',
                        '-c:a', 'libmp3lame', '-b:a', '48k', '-f', 'mp3', dst])
    return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 10000


def upload(path, name, **where):
    """Asks faithBOT for a one-time upload link into the right Drive folder, then sends the file there."""
    r = bot('upload', name=name, mime='audio/mpeg', **where)
    if r.get('have'):
        log('· already in faithBOT:', name)
        return 'have'
    if not r.get('url'):
        log('⚠️ no upload link:', r)
        return False
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        up = requests.put(r['url'], data=f, headers={'Content-Length': str(size), 'Content-Type': 'audio/mpeg'}, timeout=1800)
    if up.status_code in (200, 201):
        return True
    log('⚠️ upload failed', up.status_code, up.text[:200])
    return False


LINK_RE = re.compile(r'https?://(?:www\.|m\.)?(?:vk\.com/(?:video|clip)|vkvideo\.ru/(?:video|clip)|vk\.ru/video|youtube\.com/(?:watch|live)|youtu\.be/)\S*', re.I)


def post_link(msg):
    """The VK or YouTube video a post points to (the church posts its morning word as a VK Video link)."""
    urls = []
    for e in msg.entities or []:
        if getattr(e, 'url', None):
            urls.append(e.url)
    text = msg.message or ''
    for e in msg.entities or []:
        if type(e).__name__ == 'MessageEntityUrl':
            urls.append(text[e.offset:e.offset + e.length])
    wp = getattr(getattr(msg, 'media', None), 'webpage', None)
    if getattr(wp, 'url', None):
        urls.append(wp.url)
    urls += re.findall(r'https?://\S+', text)
    return next((u for u in urls if LINK_RE.match(u)), None)


def link_audio(url):
    """Downloads only the sound of a VK or YouTube video with yt-dlp. Returns the file, or None."""
    if not getattr(link_audio, 'ready', False):
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'yt-dlp'], check=False)
        link_audio.ready = True
    for f in glob.glob(os.path.join(TMP, 'lk.*')):
        os.remove(f)
    r = subprocess.run([sys.executable, '-m', 'yt_dlp', '-q', '--no-playlist', '-f', 'bestaudio/best', '-o', os.path.join(TMP, 'lk.%(ext)s'), url],
                       capture_output=True, text=True)
    got = [f for f in glob.glob(os.path.join(TMP, 'lk.*')) if not f.endswith('.part')]
    if r.returncode != 0 or not got:
        log('· could not download', url, (r.stderr or '').strip()[-200:])
        return None
    return got[0]


async def main_video(client, msg):
    """A post of several videos (e.g. «Тема: … + форум»: the lesson, then the forum) shares one caption.
    The lesson is always the FIRST video of the group, whichever one carries the caption."""
    if not msg.grouped_id:
        return msg if msg.video else None
    near = await client.get_messages(msg.peer_id, ids=list(range(msg.id - 9, msg.id + 10)))
    vids = sorted([m for m in near if m and m.grouped_id == msg.grouped_id and m.video], key=lambda m: m.id)
    if len(vids) > 1:
        log('· %d videos in this post: taking the first (%s min), not the forum' % (len(vids), round((vids[0].file.duration or 0) / 60) if hasattr(vids[0].file, 'duration') else '?'))
    return vids[0] if vids else None


async def post_audio(msg, out, client=None):
    """The post's video (uploaded to Telegram, or a VK / YouTube link) as a small MP3 in `out`. True when done."""
    first = await main_video(client, msg) if client and msg.grouped_id else (msg if msg.video else None)
    if first:
        log('  · from the Telegram video (%.0f MB)' % ((first.file.size or 0) / 1048576))
        src = await first.download_media(file=TMP + '/')
    else:
        link = post_link(msg)
        log('  · from the link', link)
        src = link_audio(link) if link else None
    if not src:
        return False
    ok = to_mp3(src, out)
    os.remove(src)
    return ok


def clean(s):
    s = re.sub(r"['’‘`]", '', s or '')
    s = re.sub(r'[_\\/:*?"<>|«»“”#%&{}$!@=+^~\[\]]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()[:90] or 'faithBOT'


def details(text, date):
    title = (re.search(r'«([^»]+)»', text or '') or [None, ''])[1] or (text or '').split('\n')[0]
    title = re.sub(r'\s*\+\s*форум.*$', '', title, flags=re.I).strip()
    part = re.search(r'»\s*\((\d+)\)', text or '')
    if part:
        title += ' (%s)' % part[1]
    m = re.search(r'(?:Запись от|от)\s*(\d{1,2})\.(\d{1,2})\.(\d{4})', text or '')
    if m:
        return clean(title), '%02d.%02d.%s' % (int(m[1]), int(m[2]), m[3])
    return clean(title), date.strftime('%d.%m.%Y')


async def telegram(state):
    api_id, api_hash, session = os.environ.get('TG_API_ID'), os.environ.get('TG_API_HASH'), os.environ.get('TG_SESSION')
    if not (api_id and api_hash and session):
        log('Telegram: not set up (TG_API_ID, TG_API_HASH, TG_SESSION), skipped')
        return
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    seen = set(state.get('tg') or [])
    first = not seen
    new_seen = []
    async with TelegramClient(StringSession(session), int(api_id), api_hash) as client:
        if any(s.get('priv') for s in state.get('sources') or []):
            await client.get_dialogs()  # private groups are known by number only after this
        for src in state.get('sources') or []:
            ch, need, skip = src['ch'], re.compile(src['need'], re.I), re.compile(src['skip'], re.I)
            entity = await client.get_entity(int(ch) if re.match(r'^-?\d+$', str(ch)) else ch)
            async for msg in client.iter_messages(entity, limit=60 if src.get('priv') else 12, reply_to=src.get('topic') or None):  # a busy group: look further back
                key = '%s/%s' % (ch, msg.grouped_id or msg.id)
                if key in seen:
                    continue
                age_days = (time.time() - msg.date.timestamp()) / 86400
                text = msg.message or ''
                if age_days > RECENT_DAYS or not (msg.video or post_link(msg)) or not need.search(text) or skip.search(text):
                    if age_days <= RECENT_DAYS and (msg.video or msg.document or post_link(msg) or need.search(text)):  # say why a recent post was passed over
                        why = 'no video or link' if not (msg.video or post_link(msg)) else 'no «%s»' % src['need'] if not need.search(text) else 'forum'
                        log('· skipped', key, msg.date.strftime('%d.%m %H:%M'), why, '|', re.sub(r'\s+', ' ', text)[:140])
                    if age_days > RECENT_DAYS or first:
                        new_seen.append(key)
                    continue
                dur = next((a.duration for a in (msg.document.attributes or []) if hasattr(a, 'duration')), 0) if msg.video else 0
                size_mb = (msg.file.size or 0) / 1048576 if msg.video else 999  # a link: faithBOT can't fetch VK itself
                if dur and dur < src['min'] * 60:
                    new_seen.append(key)
                    continue
                if size_mb < MIN_MB and not src.get('priv'):  # faithBOT can't open a private group itself
                    log('·', key, '%.0f MB: faithBOT fetches it itself' % size_mb)
                    new_seen.append(key)
                    continue
                title, date = details(text, msg.date)
                log('⬇️', key, title, '%.0f MB' % size_mb if msg.video else post_link(msg))
                mp3 = os.path.join(TMP, 'tg.mp3')
                ok = await post_audio(msg, mp3, client)
                if not ok:
                    tell('⚠️ could not fetch %s «%s» %s: put its MP3 in Drive → faithBOT → Входящие' % (src['folder'], title, date), notify=True)
                    new_seen.append(key)  # told once, not every 30 minutes
                    continue
                res = upload(mp3, '%s %s.mp3' % (title, date.replace('.', '-')), dest='tg', kind=src['kind'], date=date)
                os.remove(mp3)
                if res:
                    new_seen.append(key)
                    if res is True:
                        tell('✅ %s «%s» %s → Drive' % (src['folder'], title, date), notify=True)  # you see each new recording arrive
            log('Telegram %s (%s): checked' % (src['folder'], ch))
        if new_seen:
            bot('seen', posts=new_seen)
        await backlog(client, state)


async def backlog(client, state):
    """/backlog in faithBOT: every Горница and morning word from before the cutoff, newest first, into faithBOT/Архив.
    A run stops after BUDGET_MIN minutes; the next one goes on from where it stopped."""
    bl = state.get('backlog')
    if not bl:
        return
    from datetime import datetime
    before = datetime.fromisoformat(bl['before'].replace('Z', '+00:00'))
    pos, done = dict(bl.get('pos') or {}), set(bl.get('done') or [])
    budget = min(BUDGET_MIN, float(bl.get('minutes') or BUDGET_MIN))  # faithBOT keeps old recordings to ~25 min a day (GitHub's free minutes)
    t0 = time.time()
    for src in state.get('sources') or []:
        ch = str(src['ch'])
        if ch in done:
            continue
        need, skip = re.compile(src['need'], re.I), re.compile(src['skip'], re.I)
        entity = await client.get_entity(int(ch) if re.match(r'^-?\d+$', ch) else ch)
        kw = {'reply_to': src.get('topic') or None}
        if pos.get(ch):
            kw['offset_id'] = int(pos[ch])
        else:
            kw['offset_date'] = before
        n, finished = 0, True
        async for msg in client.iter_messages(entity, **kw):
            if (time.time() - t0) / 60 > budget or (time.time() - STARTED) / 60 > 40:
                finished = False
                break
            pos[ch] = msg.id
            text = msg.message or ''
            if not (msg.video or post_link(msg)) or not need.search(text) or skip.search(text):
                continue
            dur = next((a.duration for a in (msg.document.attributes or []) if hasattr(a, 'duration')), 0) if msg.video else 0
            if dur and dur < src['min'] * 60:
                continue
            title, date = details(text, msg.date)
            log('📚', ch, msg.id, title, date)
            mp3 = os.path.join(TMP, 'bl.mp3')
            ok = await post_audio(msg, mp3, client)
            if ok and upload(mp3, '%s %s.mp3' % (title, date), dest='archive', kind=src['kind'], date=date) is True:
                n += 1
            if os.path.exists(mp3):
                os.remove(mp3)
            bot('blseen', pos={ch: msg.id}, n=1 if ok else 0)
        bot('blseen', pos={ch: pos.get(ch, 0)}, done=[ch] if finished else [], n=0, min=round((time.time() - t0) / 60, 1))
        t0 = time.time()
        if n:
            tell('📚 %s: %d older recordings → faithBOT/Архив%s' % (src['folder'], n, '' if finished else ' (more next run)'))
        if not finished:
            return


def youtube(state):
    wanted = state.get('yt') or []
    if not wanted:
        return
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'yt-dlp'], check=False)
    for w in wanted:
        vid = w['id']
        log('⬇️ YouTube', vid, w.get('title', '')[:60])
        for f in glob.glob(os.path.join(TMP, vid + '.*')):
            os.remove(f)
        r = subprocess.run([sys.executable, '-m', 'yt_dlp', '-q', '--no-playlist', '-f', 'bestaudio/best', '-o', os.path.join(TMP, vid + '.%(ext)s'),
                            'https://www.youtube.com/watch?v=' + vid], capture_output=True, text=True)
        got = [f for f in glob.glob(os.path.join(TMP, vid + '.*')) if not f.endswith('.part')]
        if r.returncode != 0 or not got:
            log('· YouTube refused this computer (faithBOT will use YouTube itself):', (r.stderr or '').strip()[-160:])
            continue
        mp3 = os.path.join(TMP, vid + '.mp3')
        ok = to_mp3(got[0], mp3 + '.tmp.mp3')
        os.remove(got[0])
        if ok and upload(mp3 + '.tmp.mp3', vid + '.mp3', dest='yt'):
            tell('✅ YouTube %s → audio for faithBOT' % vid)


def soundcloud(state):
    wanted = state.get('sc') or []
    if not wanted:
        return
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'yt-dlp'], check=False)
    for w in wanted:
        log('⬇️ SoundCloud', w['title'][:60])
        for f in glob.glob(os.path.join(TMP, 'sc.*')):
            os.remove(f)
        r = subprocess.run([sys.executable, '-m', 'yt_dlp', '-q', '-f', 'bestaudio/best', '-o', os.path.join(TMP, 'sc.%(ext)s'), w['link']], capture_output=True, text=True)
        got = [f for f in glob.glob(os.path.join(TMP, 'sc.*')) if not f.endswith('.part')]
        if r.returncode != 0 or not got:
            log('⚠️ SoundCloud download failed:', (r.stderr or '').strip()[-160:])
            continue
        name = '%s - %s.mp3' % (clean(w['title']), w.get('name', 'SoundCloud'))
        mp3 = os.path.join(TMP, 'sc-out.mp3')
        ok = to_mp3(got[0], mp3)
        os.remove(got[0])
        if ok and upload(mp3, name, dest='sc') is True:
            bot('scdone', link=w['link'], name=name)
            tell('✅ %s «%s» → faithBOT' % (w.get('name', 'SoundCloud'), w['title']))


def main():
    if not URL or not KEY:
        sys.exit('FAITHBOT_URL and FAITHBOT_KEY are missing (GitHub → Settings → Secrets).')
    state = bot('wanted')
    if state.get('error'):
        sys.exit('faithBOT refused: %s (check FAITHBOT_KEY)' % state['error'])
    try:
        asyncio.run(telegram(state))
    except Exception as e:
        tell('⚠️ Telegram: %s' % e, notify='auth' in str(e).lower() or 'session' in str(e).lower())
    try:
        soundcloud(state)
    except Exception as e:
        log('⚠️ SoundCloud:', e)
    try:
        youtube(state)
    except Exception as e:
        log('⚠️ YouTube:', e)
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == '__main__':
    main()
