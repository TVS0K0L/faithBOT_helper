#!/usr/bin/env python3
"""
faithBOT · cloud helper (runs on GitHub, every 30 minutes; no Mac needed).

1. Telegram: watches the church's channels with your Telegram account (the same rules as the Mac helper). A new main video
   that is too big for faithBOT to fetch itself is downloaded, turned into a small MP3 and put straight into
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


def clean(s):
    s = re.sub(r"['’‘`]", '', s or '')
    s = re.sub(r'[_\\/:*?"<>|«»“”#%&{}$!@=+^~\[\]]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()[:90] or 'faithBOT'


def details(text, date):
    title = (re.search(r'«([^»]+)»', text or '') or [None, ''])[1] or (text or '').split('\n')[0]
    title = re.sub(r'\s*\+\s*форум.*$', '', title, flags=re.I).strip()
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
            async for msg in client.iter_messages(entity, limit=12, reply_to=src.get('topic') or None):
                key = '%s/%s' % (ch, msg.grouped_id or msg.id)
                if key in seen:
                    continue
                age_days = (time.time() - msg.date.timestamp()) / 86400
                text = msg.message or ''
                if age_days > RECENT_DAYS or not msg.video or not need.search(text) or skip.search(text):
                    if age_days > RECENT_DAYS or first:
                        new_seen.append(key)
                    continue
                dur = next((a.duration for a in (msg.document.attributes or []) if hasattr(a, 'duration')), 0)
                size_mb = (msg.file.size or 0) / 1048576
                if dur and dur < src['min'] * 60:
                    new_seen.append(key)
                    continue
                if size_mb < MIN_MB and not src.get('priv'):  # faithBOT can't open a private group itself
                    log('·', key, '%.0f MB: faithBOT fetches it itself' % size_mb)
                    new_seen.append(key)
                    continue
                title, date = details(text, msg.date)
                log('⬇️', key, title, '%.0f MB' % size_mb)
                video = await msg.download_media(file=TMP + '/')
                mp3 = os.path.join(TMP, 'tg.mp3')
                ok = to_mp3(video, mp3)
                os.remove(video)
                if not ok:
                    tell('⚠️ could not convert %s «%s»' % (src['kind'], title), notify=True)
                    continue
                res = upload(mp3, '%s %s.mp3' % (title, date.replace('.', '-')), dest='tg', kind=src['kind'], date=date)
                os.remove(mp3)
                if res:
                    new_seen.append(key)
                    if res is True:
                        tell('✅ %s «%s» %s → Drive' % (src['folder'], title, date))
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
            if (time.time() - STARTED) / 60 > BUDGET_MIN:
                finished = False
                break
            pos[ch] = msg.id
            text = msg.message or ''
            if not msg.video or not need.search(text) or skip.search(text):
                continue
            dur = next((a.duration for a in (msg.document.attributes or []) if hasattr(a, 'duration')), 0)
            if dur and dur < src['min'] * 60:
                continue
            title, date = details(text, msg.date)
            log('📚', ch, msg.id, title, date)
            video = await msg.download_media(file=TMP + '/')
            mp3 = os.path.join(TMP, 'bl.mp3')
            ok = to_mp3(video, mp3)
            os.remove(video)
            if ok and upload(mp3, '%s %s.mp3' % (title, date), dest='archive', kind=src['kind'], date=date) is True:
                n += 1
            if os.path.exists(mp3):
                os.remove(mp3)
            bot('blseen', pos={ch: msg.id}, n=1 if ok else 0)
        bot('blseen', pos={ch: pos.get(ch, 0)}, done=[ch] if finished else [], n=0)
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
