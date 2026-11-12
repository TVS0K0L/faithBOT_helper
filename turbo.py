#!/usr/bin/env python3
"""
faithBOT · the backlog on GitHub ("turbo").

Makes the FULL notes of every old morning word and Горница (from the church's Telegram) and every Кафедра of
Bishop Andrey and Pastor Olga (Gemini reads the YouTube video), exactly as faithBOT writes its own: the same
transcription, the same notes prompt (format, example, memory, library), the same analysis. faithBOT then saves
them quietly (Google Doc, members' PDF, library card, long-term memory). Nothing is sent to anyone.

Runs for up to ~5.5 hours, then the next run continues. Uses its own Gemini key (GEMINI_KEY), so faithBOT's
own daily quota stays for new sermons and the Pastor.
Secrets: TG_API_ID, TG_API_HASH, TG_SESSION, FAITHBOT_URL, FAITHBOT_KEY, GEMINI_KEY
"""
import asyncio, glob, json, os, re, shutil, subprocess, sys, tempfile, time
from datetime import datetime
import requests

URL = os.environ.get('FAITHBOT_URL', '').strip()
KEY = os.environ.get('FAITHBOT_KEY', '').strip()
GKEY = os.environ.get('GEMINI_KEY', '').strip()
STARTED = time.time()
LIMIT_MIN = float(os.environ.get('TURBO_MINUTES', '330'))
CHUNK = 15 * 60                 # transcription parts of 15 minutes
GAPI = 'https://generativelanguage.googleapis.com'
WRITE_MODELS = ['gemini-flash-latest', 'gemini-flash-lite-latest']
FAST_MODELS = ['gemini-flash-lite-latest', 'gemini-flash-latest']
TMP = tempfile.mkdtemp(prefix='turbo-')
SPENT = set()                   # models whose daily quota is used up in this run
YT_SPENT = False                # Gemini's daily YouTube allowance is used up


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def left_min():
    return LIMIT_MIN - (time.time() - STARTED) / 60


def bot(a, timeout=360, **body):
    body['a'] = a
    for attempt in range(4):
        try:
            r = requests.post(URL, params={'helper': '1', 'k': KEY}, data=json.dumps(body), timeout=timeout,
                              headers={'Content-Type': 'text/plain;charset=utf-8'})
            return r.json()
        except Exception as e:
            log('faithBOT not reachable (%s), retrying' % str(e)[:80])
            time.sleep(10 * (attempt + 1))
    return {}


class Daily(Exception):
    pass


def gemini(models, parts, max_tokens=32768, as_json=False):
    """One answer from the first model that has quota; waits on per-minute limits, skips models out of daily quota."""
    global YT_SPENT
    last = ''
    for model in [m for m in models if m not in SPENT]:
        for attempt in range(6):
            body = {'contents': [{'role': 'user', 'parts': parts}],
                    'generationConfig': {'maxOutputTokens': max_tokens, 'temperature': 0.3}}
            if as_json:
                body['generationConfig']['responseMimeType'] = 'application/json'
            if any('file_data' in p and 'youtube' in p['file_data'].get('file_uri', '') for p in parts):
                body['generationConfig']['mediaResolution'] = 'MEDIA_RESOLUTION_LOW'
            try:
                r = requests.post('%s/v1beta/models/%s:generateContent?key=%s' % (GAPI, model, GKEY), json=body, timeout=900)
            except Exception as e:
                last = str(e)[:120]
                time.sleep(20)
                continue
            if r.status_code == 200:
                j = r.json()
                cand = (j.get('candidates') or [{}])[0]
                text = ''.join(p.get('text', '') for p in (cand.get('content') or {}).get('parts', []) if 'text' in p)
                if text.strip():
                    return text, model
                last = 'empty answer (%s)' % cand.get('finishReason', '?')
                break
            last = '%s %s' % (r.status_code, r.text[:200])
            if r.status_code == 429:
                if re.search(r'PerDay|per day|daily', r.text, re.I):
                    if re.search(r'video|youtube', r.text, re.I):
                        YT_SPENT = True
                        raise Daily('YouTube allowance for today used up')
                    SPENT.add(model)
                    break
                time.sleep(45)
                continue
            if r.status_code in (500, 502, 503, 504):
                time.sleep(30)
                continue
            if r.status_code == 404:
                SPENT.add(model)
            break
    if all(m in SPENT for m in models):
        raise Daily('Gemini daily quota used up (%s)' % last)
    raise RuntimeError('Gemini: ' + last)


def upload(path, mime):
    """A file into Gemini (resumable upload); waits until it is ready."""
    size = os.path.getsize(path)
    r = requests.post('%s/upload/v1beta/files?key=%s' % (GAPI, GKEY), timeout=120, json={'file': {'display_name': os.path.basename(path)}},
                      headers={'X-Goog-Upload-Protocol': 'resumable', 'X-Goog-Upload-Command': 'start',
                               'X-Goog-Upload-Header-Content-Length': str(size), 'X-Goog-Upload-Header-Content-Type': mime})
    up = r.headers.get('x-goog-upload-url')
    if not up:
        raise RuntimeError('upload refused: %s' % r.text[:200])
    with open(path, 'rb') as f:
        r = requests.post(up, data=f, timeout=900, headers={'X-Goog-Upload-Offset': '0', 'X-Goog-Upload-Command': 'upload, finalize'})
    info = r.json().get('file', {})
    for _ in range(60):
        if info.get('state') == 'ACTIVE':
            return info
        time.sleep(5)
        info = requests.get('%s/v1beta/%s?key=%s' % (GAPI, info['name'], GKEY), timeout=60).json()
    raise RuntimeError('file not ready')


def forget(info):
    try:
        requests.delete('%s/v1beta/%s?key=%s' % (GAPI, info['name'], GKEY), timeout=60)
    except Exception:
        pass


def clock(s):
    s = int(round(s))
    return '%d:%02d:%02d' % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else '%d:%02d' % (s // 60, s % 60)


def shift(text, off):
    def f(m):
        a, b, c = m.group(1), m.group(2), m.group(3)
        s = (int(a) * 3600 + int(b) * 60 + int(c)) if c is not None else (int(a) * 60 + int(b))
        return '[' + clock(s + off) + ']'
    return re.sub(r'\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]', f, text)


def duration(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0


def transcribe_audio(path, prompt):
    """Verbatim transcript of an audio file, in 15-minute parts, timestamps from the start."""
    total = duration(path)
    parts_dir = os.path.join(TMP, 'parts')
    shutil.rmtree(parts_dir, ignore_errors=True)
    os.makedirs(parts_dir)
    subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-i', path, '-f', 'segment', '-segment_time', str(CHUNK), '-c', 'copy', os.path.join(parts_dir, 'p%03d.mp3')], check=True)
    files = sorted(glob.glob(os.path.join(parts_dir, 'p*.mp3')))
    out = []
    for i, p in enumerate(files):
        info = upload(p, 'audio/mpeg')
        try:
            q = prompt.replace('{PART}', str(i + 1)).replace('{PARTS}', str(len(files))).replace('{RANGE}', '')
            text, _ = gemini(WRITE_MODELS, [{'text': q}, {'file_data': {'file_uri': info['uri'], 'mime_type': 'audio/mpeg'}}])
        finally:
            forget(info)
        text = text.replace('[[END]]', '').strip()
        if text:
            out.append(shift(text, i * CHUNK))
    return '\n\n'.join(out), total


def transcribe_youtube(vid, length, prompt):
    """Verbatim transcript of a YouTube video, read by Gemini in 15-minute windows (sound first, low picture)."""
    total = length or 0
    out, pos, n = [], 0, 0
    parts = max(1, -(-int(total) // CHUNK)) if total else 0
    while True:
        end = pos + CHUNK
        q = prompt.replace('{PART}', str(n + 1)).replace('{PARTS}', str(parts or '?')).replace('{RANGE}', ', covering %s–%s of the recording' % (clock(pos), clock(end)))
        media = {'file_data': {'file_uri': 'https://www.youtube.com/watch?v=' + vid}, 'video_metadata': {'start_offset': '%ds' % max(0, pos - 5), 'end_offset': '%ds' % end, 'fps': 0.2}}
        try:
            text, _ = gemini(WRITE_MODELS, [{'text': q}, media])
        except RuntimeError as e:
            if n and '400' in str(e):
                break  # past the end of a video of unknown length
            raise
        ended = '[[END]]' in text and len(text.replace('[[END]]', '').strip()) < 40
        if not ended:
            out.append(shift(text.replace('[[END]]', '').strip(), max(0, pos - 5)))
        n += 1
        pos = end
        if ended or (total and pos >= total) or pos >= 4 * 3600:
            break
    return '\n\n'.join(out), total or pos


def notes(meta, transcript, total):
    """The full notes with faithBOT's own prompt, then its analysis, then saved by faithBOT (quietly)."""
    meta = dict(meta, transcript=transcript, duration=int(total))
    pr = bot('turbo_prompt', **meta)
    if not pr.get('write'):
        raise RuntimeError('no prompt from faithBOT: %s' % str(pr)[:120])
    md, model = gemini(WRITE_MODELS, [{'text': pr['write']}], max_tokens=65536)
    md = re.sub(r'^```(?:markdown|md)?\s*\n', '', md.strip())
    md = re.sub(r'\n```\s*$', '', md).strip()
    if len(md) < 300:
        raise RuntimeError('the notes came back empty')
    a = {}
    try:
        aj, _ = gemini(FAST_MODELS, [{'text': pr['analysis'].replace('{WRITEUP}', md[:40000])}], max_tokens=8192, as_json=True)
        a = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', aj.strip()))
    except Exception as e:
        log('  · analysis skipped:', str(e)[:80])
    res = bot('turbo_done', md=md, analysis=a, model=model, **meta)
    return res


# ---------------------------------------------------------------------- Telegram: morning words and Горница
def post_link(msg):
    urls = [getattr(e, 'url', None) for e in (msg.entities or []) if getattr(e, 'url', None)]
    wp = getattr(getattr(msg, 'media', None), 'webpage', None)
    if getattr(wp, 'url', None):
        urls.append(wp.url)
    urls += re.findall(r'https?://\S+', msg.message or '')
    return next((u for u in urls if re.match(r'https?://(?:www\.|m\.)?(?:vk\.com/(?:video|clip)|vkvideo\.ru/(?:video|clip)|vk\.ru/video)', u, re.I)), None)


def details(text, date):
    title = (re.search(r'«([^»]+)»', text or '') or [None, ''])[1] or (text or '').split('\n')[0]
    title = re.sub(r'\s*\+\s*форум.*$', '', title, flags=re.I).strip()
    part = re.search(r'»\s*\((\d+)\)', text or '')
    if part:
        title += ' (%s)' % part[1]
    m = re.search(r'(?:Запись от|от)\s*(\d{1,2})\.(\d{1,2})\.(\d{4})', text or '')
    d = '%02d.%02d.%s' % (int(m[1]), int(m[2]), m[3]) if m else date.strftime('%d.%m.%Y')
    sp = ''
    first = (text or '').split('\n')[0]
    if re.search(r'Матюжов(?!а)', first, re.I):
        sp = 'Епископ Андрей Матюжов'
    elif re.search(r'Матюжов', first, re.I):
        sp = 'Пастор Ольга Матюжова'
    else:
        sp = (re.search(r'\sс\s+(.+)$', first) or [None, ''])[1].strip()
    return title[:120], d, sp


async def telegram(cfg, have):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    before = datetime.fromisoformat(cfg['before'].replace('Z', '+00:00'))
    totals = {}
    async with TelegramClient(StringSession(os.environ['TG_SESSION']), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH']) as client:
        await client.get_dialogs()
        for src in cfg['sources']:
            ch = str(src['ch'])
            entity = await client.get_entity(int(ch) if re.match(r'^-?\d+$', ch) else ch)
            need, skip = re.compile(src['need'], re.I), re.compile(src['skip'], re.I)
            todo = []
            async for msg in client.iter_messages(entity, offset_date=before, reply_to=src.get('topic') or None):
                text = msg.message or ''
                if not (msg.video or post_link(msg)) or not need.search(text) or skip.search(text):
                    continue
                dur = next((a.duration for a in (msg.document.attributes or []) if hasattr(a, 'duration')), 0) if msg.video else 0
                if dur and dur < src['min'] * 60:
                    continue
                todo.append(msg)
            totals[src['kind']] = len(todo)
            bot('turbo_totals', totals={src['kind']: len(todo)})
            log('%s: %d in the archive' % (src['kind'], len(todo)))
            for msg in todo:  # newest first
                if left_min() < 25:
                    return
                title, date, sp = details(msg.message or '', msg.date)
                if '%s|%s' % (src['kind'], date) in have:
                    continue
                try:
                    path = await fetch_post(client, msg)
                    if not path:
                        continue
                    log('📝', src['kind'], date, title[:60])
                    tr, total = transcribe_audio(path, cfg['transcribe'])
                    os.remove(path)
                    if len(tr) < 200:
                        log('  · no speech found')
                        continue
                    res = notes({'kind': src['kind'], 'title': title, 'date': date, 'speaker': sp}, tr, total)
                    log('  ✅' if res.get('ok') else '  · %s' % str(res)[:100])
                    have.add('%s|%s' % (src['kind'], date))
                except Daily as e:
                    log('⏸', e)
                    return
                except Exception as e:
                    log('  ⚠️', str(e)[:160])


async def fetch_post(client, msg):
    """The post's lesson (the first video of a group, never the forum) as a small MP3."""
    first = msg
    if msg.grouped_id:
        near = await client.get_messages(msg.peer_id, ids=list(range(msg.id - 9, msg.id + 10)))
        vids = sorted([m for m in near if m and m.grouped_id == msg.grouped_id and m.video], key=lambda m: m.id)
        first = vids[0] if vids else msg
    src = None
    if first.video:
        src = await first.download_media(file=TMP + '/')
    else:
        link = post_link(msg)
        if link:
            subprocess.run([sys.executable, '-m', 'yt_dlp', '-q', '-f', 'bestaudio/best', '-o', os.path.join(TMP, 'lk.%(ext)s'), link], capture_output=True)
            got = [f for f in glob.glob(os.path.join(TMP, 'lk.*')) if not f.endswith('.part')]
            src = got[0] if got else None
    if not src:
        return None
    mp3 = os.path.join(TMP, 'rec.mp3')
    subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-i', src, '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', '32k', mp3], check=True)
    os.remove(src)
    return mp3


# ---------------------------------------------------------------------- YouTube: the Кафедра archive
def kafedra(cfg, have):
    for v in cfg.get('yt') or []:
        if left_min() < 40 or YT_SPENT:
            return
        if v.get('date') and 'kafedra|%s' % v['date'] in have:
            continue
        log('📝 kafedra', v.get('date'), (v.get('title') or '')[:60])
        try:
            tr, total = transcribe_youtube(v['id'], v.get('len') or 0, cfg['transcribe'])
            if len(tr) < 200:
                bot('turbo_skip', yt=v['id'])
                continue
            res = notes({'kind': 'kafedra', 'title': v.get('title', ''), 'date': v.get('date', ''), 'speaker': v.get('speaker', ''), 'yt': v['id']}, tr, total)
            log('  ✅' if res.get('ok') else '  · %s' % str(res)[:100])
        except Daily as e:
            log('⏸', e)
            return
        except Exception as e:
            log('  ⚠️', str(e)[:160])
            bot('turbo_skip', yt=v['id'])


def main():
    if not (URL and KEY and GKEY):
        sys.exit('FAITHBOT_URL, FAITHBOT_KEY and GEMINI_KEY are needed (GitHub → Settings → Secrets).')
    cfg = bot('turbo_cfg')
    if not cfg.get('transcribe'):
        sys.exit('faithBOT did not answer (is v36 installed?): %s' % str(cfg)[:200])
    have = set(cfg.get('have') or [])
    rounds = 0
    while left_min() > 40 and rounds < 50:
        rounds += 1
        kafedra(cfg, have)  # Gemini reads YouTube (up to its daily allowance); the rest of the time goes to Telegram
        asyncio.run(telegram(cfg, have))
        cfg = bot('turbo_cfg')
        if not (cfg.get('yt') and not YT_SPENT) or left_min() < 40:
            break
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == '__main__':
    main()
