#!/usr/bin/env python3
"""
faithBOT · the backlog on GitHub ("turbo").

Makes the FULL notes of every old morning word and Горница (from the church's Telegram) and every Кафедра of
Bishop Andrey and Pastor Olga (Gemini reads the YouTube video), exactly as faithBOT writes its own: the same
transcription, the same notes prompt (format, example, memory, library), the same analysis. faithBOT then saves
them quietly (Google Doc, members' PDF, library card, long-term memory). Nothing is sent to anyone.

Runs for up to ~5.5 hours, then the next run continues. Uses its own Gemini key (GEMINI_KEY), so faithBOT's
own daily quota stays for new sermons and the Pastor.
v41: also finds the recording of any church note without a transcript and transcribes it (timestamps), and at
night (Dubai 22:00–07:00) studies every message once for the Pastor. After 22 Nov 2026, 18:00 Dubai only these quiet jobs run.
Secrets: TG_API_ID, TG_API_HASH, TG_SESSION, FAITHBOT_URL, FAITHBOT_KEY, GEMINI_KEY
"""
import asyncio, glob, json, os, re, shutil, subprocess, sys, tempfile, time
from datetime import datetime, timezone
import requests

URL = os.environ.get('FAITHBOT_URL', '').strip()
KEY = os.environ.get('FAITHBOT_KEY', '').strip()
KEYS = [k for k in [os.environ.get(n, '').strip() for n in ('GEMINI_KEY', 'GEMINI_KEY2', 'GEMINI_KEY3', 'GEMINI_KEY4')] if k]
GKEY = KEYS[0] if KEYS else ''
STARTED = time.time()
LIMIT_MIN = float(os.environ.get('TURBO_MINUTES', '330'))
CHUNK = 30 * 60                 # v46: transcription parts of 30 minutes (half the requests)
GAPI = 'https://generativelanguage.googleapis.com'
WRITE_MODELS = ['gemini-3.7-flash', 'gemini-3.6-flash', 'gemini-3.5-flash']  # v48: full Flash, each with its own daily allowance; never the models NewsBOT and SportsBOT use (flash-latest, 3.8), so their morning digests always have quota
#  # v47: the notes and the Russian edition only on Flash (complete, never the lighter model)
FAST_MODELS = ['gemini-3.5-flash-lite', 'gemini-3.1-flash-lite', 'gemini-3.6-flash']  # v48: not the digests' models
TR_MODELS = ['gemini-3.5-flash-lite', 'gemini-3.1-flash-lite', 'gemini-3.5-flash']  # v48: not flash-lite-latest / flash-latest (the digests' models)
#  # v46: transcription on Flash-Lite (its own, larger daily allowance); Flash kept for the notes
TMP = tempfile.mkdtemp(prefix='turbo-')
SPENT = set()                   # models whose daily quota is used up (or that keep timing out) in this run
SLOW = {}                       # timeouts per model
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
                r = requests.post('%s/v1beta/models/%s:generateContent?key=%s' % (GAPI, model, GKEY), json=body, timeout=300)
            except Exception as e:
                last = str(e)[:120]
                SLOW[model] = SLOW.get(model, 0) + 1
                if SLOW[model] >= 1:  # v52: one hang (5 minutes) is enough: the next model for the rest of this run
                    SPENT.add(model)
                    break
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
            if r.status_code in (500, 502, 503, 504):  # v52: overloaded: two short waits (20 s, 40 s), then the next model (never half an hour on one)
                if attempt >= 2:
                    break
                time.sleep(20 * (attempt + 1))
                continue
            if r.status_code == 404:
                SPENT.add(model)
            break
    if all(m in SPENT for m in models):
        if next_key():  # v45: another Google account's free quota
            return gemini(models, parts, max_tokens, as_json)
        raise Daily('Gemini daily quota used up (%s)' % last)
    raise RuntimeError('Gemini: ' + last)


def next_key():
    """The next Gemini key (another account's daily quota); False when there is none left."""
    global GKEY
    i = KEYS.index(GKEY) if GKEY in KEYS else -1
    if i + 1 >= len(KEYS):
        return False
    GKEY = KEYS[i + 1]
    SPENT.clear()
    SLOW.clear()
    log('🔑 Gemini key %d of %d (the previous one used its free daily quota)' % (i + 2, len(KEYS)))
    return True


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
            text, _ = gemini(TR_MODELS, [{'text': q}, {'file_data': {'file_uri': info['uri'], 'mime_type': 'audio/mpeg'}}], max_tokens=60000)
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
            text, _ = gemini(TR_MODELS, [{'text': q}, media], max_tokens=60000)
        except RuntimeError as e:
            if n and '400' in str(e):
                break  # past the end of a video of unknown length
            raise
        ended = '[[END]]' in text and len(text.replace('[[END]]', '').strip()) < 40
        if not ended:
            body = text.replace('[[END]]', '').strip()
            m = re.search(r'\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]', body)
            first = (int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])) if m and m[3] else (int(m[1]) * 60 + int(m[2])) if m else 0
            # Gemini often gives the video's own times for a window: then they are already right
            out.append(body if pos and first >= pos - 90 else shift(body, max(0, pos - 5)))
        n += 1
        pos = end
        if ended or (total and pos >= total) or pos >= 4 * 3600:
            break
    return '\n\n'.join(out), total or pos


HAVE = set()  # what this run already has (the same set the loops use)
MEMO = os.path.join('pending', 'memo.json')  # v45: kept between runs: what was finished, rewrites that keep failing


def memo():
    try:
        return json.load(open(MEMO, encoding='utf-8'))
    except Exception:
        return {'done': [], 'redofail': {}}


def memo_save(m):
    os.makedirs('pending', exist_ok=True)
    json.dump(m, open(MEMO, 'w', encoding='utf-8'), ensure_ascii=False)


def mark_done(meta):
    m = memo()
    for k in ['%s|%s' % (meta.get('kind'), meta.get('date'))] + (['rt:' + meta['rt']] if meta.get('rt') else []) + (['yt:' + meta['yt']] if meta.get('yt') else []):
        if k not in m['done']:
            m['done'].append(k)
        HAVE.add(k)
    memo_save(m)
PEND = 'pending'  # transcripts whose notes could not be written yet (kept between runs by the workflow's cache)


def pend_key(meta):
    return re.sub(r'[^\w.-]+', '_', '%s_%s_%s' % (meta.get('kind', ''), meta.get('date', ''), meta.get('rt') or meta.get('yt') or (meta.get('speaker') or '')[:12]))


def save_pending(meta, transcript, total):
    os.makedirs(PEND, exist_ok=True)
    with open(os.path.join(PEND, pend_key(meta) + '.json'), 'w', encoding='utf-8') as f:
        json.dump({'meta': meta, 'transcript': transcript, 'total': total}, f, ensure_ascii=False)
    log('  💾 transcript kept: the notes will be written next time (no second transcription)')
    HAVE.add('%s|%s' % (meta.get('kind'), meta.get('date')))
    if meta.get('rt'):
        HAVE.add('rt:' + meta['rt'])


def run_pending(have):
    """v44: first the transcripts already made: only the notes are written (minutes, not hours)."""
    files = sorted(glob.glob(os.path.join(PEND, '*.json')))
    if files:
        log('pending: %d transcripts waiting for their notes' % len(files))
    for path in files:
        if left_min() < 15:
            return
        try:
            d = json.load(open(path, encoding='utf-8'))
            m = d['meta']
            log('📝 (kept) %s %s %s' % (m.get('kind'), m.get('date'), (m.get('title') or '')[:50]))
            res = notes(m, d['transcript'], d['total'], keep=False)
            if res.get('ok') or res.get('have'):
                os.remove(path)
                log('  ✅')
                have.add('%s|%s' % (m.get('kind'), m.get('date')))
                if m.get('rt'):
                    have.add('rt:' + m['rt'])
        except Daily:
            raise
        except Exception as e:
            log('  ⚠️ still waiting:', str(e)[:120])


def notes(meta, transcript, total, keep=True):
    """The full notes with faithBOT's own prompt, then its analysis, then saved by faithBOT (quietly).
    v44: if the AI is overloaded, the transcript is kept and the notes are written on the next run."""
    try:
        return notes_(meta, transcript, total)
    except Exception:
        if keep:
            save_pending(meta, transcript, total)
        raise


def notes_(meta, transcript, total):
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
    ru = member_edition(pr, md)
    res = bot('turbo_done', md=md, analysis=a, model=model, ru=ru, **meta)
    if res.get('ok') or res.get('have'):
        mark_done(meta)  # v45: never made twice, even if faithBOT lists it under another date
    return res


def member_edition(pr, md):
    """v39: the members' notes in Russian (no Форум, no Diana, written to the reader); empty if it fails."""
    if not pr.get('member'):
        return ''
    try:
        ru, _ = gemini(WRITE_MODELS, [{'text': pr['member'].replace('{NOTES}', md)}], max_tokens=65536)  # the Russian edition, complete, on Flash (as the notes)
        ru = re.sub(r'\n```\s*$', '', re.sub(r'^```(?:markdown|md)?\s*\n', '', ru.strip())).strip()
        return ru if len(ru) > 300 else ''
    except Exception as e:
        log('  · members\' edition skipped:', str(e)[:80])
        return ''


def editions(cfg):
    """v51: the Russian edition of every church message that has none yet (newest first), so Russian readers always get Russian."""
    for it in (cfg.get('med') or []):
        if left_min() < 25:
            return
        try:
            src = bot('turbo_md', id=it['id']) or {}
            if not src.get('md') or not src.get('member'):
                continue
            log('🇷🇺 edition: %s %s «%s»' % (it['kind'], it['date'], it['title'][:50]))
            ru = member_edition({'member': src['member']}, src['md'])
            r = bot('turbo_med', id=it['id'], ru=ru)
            log('  ✓ saved' if r.get('ok') else '  · %s' % str(r)[:100])
        except Daily:
            return
        except Exception as e:
            log('  · edition skipped:', str(e)[:100])


def redo(cfg):
    """v39: older church notes rewritten in the new format from their transcripts (the same Google Doc, its link stays)."""
    fails = memo().get('redofail', {})
    for it in (cfg.get('redo') or []):
        if left_min() < 25:
            return
        if fails.get(it['id'], 0) >= 2:  # v45: it failed twice on faithBOT's side: not again
            continue
        try:
            tr = (bot('turbo_tr', id=it['id']) or {}).get('transcript') or ''
            if len(tr) < 200:
                continue
            log('rewrite: %s %s «%s»' % (it['kind'], it['date'], it['title'][:50]))
            pr = bot('turbo_prompt', kind=it['kind'], title=it['title'], date=it['date'], speaker=it.get('speaker', ''), transcript=tr, duration=0)
            md, model = gemini(WRITE_MODELS, [{'text': pr['write']}], max_tokens=65536)
            md = re.sub(r'\n```\s*$', '', re.sub(r'^```(?:markdown|md)?\s*\n', '', md.strip())).strip()
            if len(md) < 300:
                continue
            r = bot('turbo_redo', id=it['id'], md=md, ru=member_edition(pr, md), model=model)
            log('  ✓ rewritten' if r.get('ok') else '  · not saved: %s' % str(r)[:100])
            if not r.get('ok'):
                m = memo()
                m.setdefault('redofail', {})[it['id']] = m.get('redofail', {}).get(it['id'], 0) + 1
                memo_save(m)
        except Daily:
            return
        except Exception as e:
            log('  · rewrite skipped:', str(e)[:100])


# ---------------------------------------------------------------------- Telegram: morning words and Горница
def post_link(msg):
    urls = [getattr(e, 'url', None) for e in (msg.entities or []) if getattr(e, 'url', None)]
    wp = getattr(getattr(msg, 'media', None), 'webpage', None)
    if getattr(wp, 'url', None):
        urls.append(wp.url)
    urls += re.findall(r'https?://\S+', msg.message or '')
    return next((u for u in urls if re.match(r'https?://(?:www\.|m\.)?(?:vk\.com/(?:video|clip)|vkvideo\.ru/(?:video|clip)|vk\.ru/video)', u, re.I)), None)


NOM = [('ским', 'ский'), ('цким', 'цкий'), ('ской', 'ская'), ('цкой', 'цкая'), ('овым', 'ов'), ('евым', 'ев'), ('ёвым', 'ёв'), ('иным', 'ин'), ('ыным', 'ын'),
       ('овой', 'ова'), ('евой', 'ева'), ('ёвой', 'ёва'), ('иной', 'ина'), ('ыной', 'ына'), ('ием', 'ий'), ('ией', 'ия'), ('ьёй', 'ья'), ('ьей', 'ья'),
       ('еем', 'ей'), ('аем', 'ай'), ('уем', 'уй'), ('оем', 'ой')]


def nom_word(w):
    """v52: one Russian name from the instrumental case («Антоном», «Евцихевичем», «Леденевым») to the nominative."""
    lo = w.lower()
    for a, b in NOM:
        if lo.endswith(a):
            return w[:-len(a)] + b
    if re.search(r'[жшчщц]ем$', lo):
        return w[:-2]
    if re.search(r'[жшчщц]ей$', lo):
        return w[:-2] + 'а'
    if lo.endswith('ем'):
        return w[:-2] + 'ь'
    if lo.endswith('ей'):
        return w[:-2] + 'я'
    if lo.endswith('ой'):
        return w[:-2] + 'а'
    if lo.endswith('ом'):
        return w[:-2]
    return w


def post_speaker(text):
    """v52: «Утреннее сосредоточение с Антоном Евцихевичем «…»» → Антон Евцихевич (the bishop keeps his rank, faithBOT adds it)."""
    m = re.search(r'сосредоточени\w*\s+с\s+((?:[А-ЯЁа-яё-]+\s+){0,3}?[А-ЯЁ][а-яё-]+)\s*[«"“]', text or '')
    if not m:
        return ''
    words = [w for w in m[1].split() if not re.match(r'^(епископ|пастор|апостол|брат|сестр)\w*$', w, re.I)]
    if not 1 <= len(words) <= 3 or not all(re.match(r'^[А-ЯЁ]', w) for w in words):
        return ''
    return ' '.join(nom_word(w) for w in words)


def details(text, date):
    title = (re.search(r'«([^»]+)»', text or '') or [None, ''])[1] or (text or '').split('\n')[0]
    title = re.sub(r'\s*\+\s*форум.*$', '', title, flags=re.I).strip()
    part = re.search(r'»\s*\((\d+)\)', text or '')
    if part:
        title += ' (%s)' % part[1]
    m = re.search(r'(?:Запись от|от)\s*(\d{1,2})\.(\d{1,2})\.(\d{4})', text or '')
    d = '%02d.%02d.%s' % (int(m[1]), int(m[2]), m[3]) if m else date.strftime('%d.%m.%Y')
    sp = post_speaker(text)  # v52: the name the church gives in the post (faithBOT adds the rank only for Bishop Andrey and Pastor Olga)
    first = (text or '').split('\n')[0]
    if sp and 'Матюжов' in sp:
        sp = 'Пастор Ольга Матюжова' if re.search(r'Ольг', sp) else 'Епископ Андрей Матюжов'
    elif sp:
        pass
    elif re.search(r'Матюжов(?!а)', first, re.I):
        sp = 'Епископ Андрей Матюжов'
    elif re.search(r'Матюжов', first, re.I):
        sp = 'Пастор Ольга Матюжова'
    return title[:120], d, sp


async def telegram(cfg, have, kinds=None, fresh_days=0):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    before = datetime.fromisoformat(cfg['before'].replace('Z', '+00:00'))
    totals = {}
    async with TelegramClient(StringSession(os.environ['TG_SESSION']), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH']) as client:
        await client.get_dialogs()
        for src in sorted([x for x in cfg['sources'] if not kinds or x['kind'] in kinds], key=lambda x: 0 if x['kind'] == 'gornitsa' else 1):  # v40: Горница first
            ch = str(src['ch'])
            entity = await client.get_entity(int(ch) if re.match(r'^-?\d+$', ch) else ch)
            need, skip = re.compile(src['need'], re.I), re.compile(r'форум|^\s*Начинаем|zoom\.us', re.I)  # v52: never a forum post (morning word or Горница), never the live-start notice
            todo, meta = [], []
            kw = {'reply_to': src.get('topic') or None}
            if not fresh_days:
                kw['offset_date'] = before
            async for msg in client.iter_messages(entity, **kw):
                if fresh_days and (time.time() - msg.date.timestamp()) > fresh_days * 86400:
                    break  # v51: the new recordings only (newest first)
                text = msg.message or ''
                if fresh_days and (msg.video or post_link(msg)) and need.search(text) and not re.match(r'^\s*Начинаем', text):  # v52: names and forum posts of what the library already has
                    t_, d_, s_ = details(text, msg.date)
                    if '%s|%s' % (src['kind'], d_) in have:
                        meta.append({'kind': src['kind'], 'date': d_, 'title': t_, 'speaker': s_, 'forum': bool(re.search('форум', text, re.I))})
                if not (msg.video or post_link(msg)) or not need.search(text) or skip.search(text):
                    continue
                dur = next((a.duration for a in (msg.document.attributes or []) if hasattr(a, 'duration')), 0) if msg.video else 0
                if dur and dur < src['min'] * 60:
                    continue
                todo.append(msg)
            if meta:
                r = bot('turbo_meta', items=meta)
                if r.get('fixed') or r.get('removed'):
                    log('%s: %s names fixed, %s forum posts taken out of the library' % (src['kind'], r.get('fixed', 0), r.get('removed', 0)))
            totals[src['kind']] = len(todo)
            if not fresh_days:
                bot('turbo_totals', totals={src['kind']: len(todo)})
            log('%s: %d %s' % (src['kind'], len(todo), 'new (last %d days)' % fresh_days if fresh_days else 'in the archive'))
            for msg in todo:  # newest first
                if left_min() < 10:
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
        if left_min() < 15:
            return
        if v.get('date') and kkey(v['date'], v.get('speaker', '')) in have:
            continue
        log('📝 kafedra', v.get('date'), (v.get('title') or '')[:60])
        try:
            mp3 = yt_audio(v['id'])  # v52: the sound as a small MP3 first (quicker, no YouTube allowance); the video only if YouTube refuses
            if mp3:
                tr, total = transcribe_audio(mp3, cfg['transcribe'])
                os.remove(mp3)
            else:
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


# ---------------------------------------------------------------------- v52: the YouTube ministries
def yt_audio(vid):
    """The sound of a YouTube video as a small MP3 (no YouTube allowance used); None if YouTube refuses."""
    src = os.path.join(TMP, 'yt-' + vid)
    try:
        subprocess.run([sys.executable, '-m', 'yt_dlp', '-q', '--no-warnings', '--no-playlist', '-f', 'worstaudio/bestaudio/worst', '-o', src + '.%(ext)s',
                        'https://www.youtube.com/watch?v=' + vid], capture_output=True, timeout=900)
    except Exception:
        return None
    got = [f for f in glob.glob(src + '.*') if not f.endswith('.part')]
    if not got:
        return None
    mp3 = src + '.mp3'
    if not got[0].endswith('.mp3'):
        subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-i', got[0], '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', '32k', mp3], check=True)
        os.remove(got[0])
    return mp3


def ministries(cfg, have):
    """v52: after the church's own recordings, the YouTube ministries from faithBOT's queue, in your order
    (Authentic Church, Vlad Savchuk, David Diga Hernandez, Aaron Burke, Brayden Steckler), newest first.
    A video leaves the queue only when its notes are in; on Gemini's daily limit the next hourly run goes on."""
    q = cfg.get('ministries') or []
    if not q:
        return True
    log('🕊️ ministries: %d waiting' % len(q))
    for v in q:
        if left_min() < 25:
            return True
        if 'yt:' + v['id'] in have:
            continue
        log('📝 %s %s «%s»' % (v.get('channel', ''), v.get('date', ''), (v.get('title') or '')[:60]))
        try:
            mp3 = yt_audio(v['id'])
            if mp3:
                tr, total = transcribe_audio(mp3, cfg['transcribe'])
                os.remove(mp3)
            elif not YT_SPENT:
                tr, total = transcribe_youtube(v['id'], v.get('dur') or 0, cfg['transcribe'])
            else:
                log('  · YouTube refused the download and Gemini\'s YouTube allowance is used up: next run')
                continue
            if len(tr) < 200:
                bot('turbo_ytfail', id=v['id'], why='no speech found')
                continue
            res = notes({'kind': 'youtube', 'title': v.get('title', ''), 'date': v.get('date', ''), 'speaker': v.get('speaker', ''), 'channel': v.get('channel', ''), 'yt': v['id']}, tr, total)
            log('  ✅' if res.get('ok') else '  · %s' % str(res)[:100])
            have.add('yt:' + v['id'])
        except Daily as e:
            log('⏸ Gemini\'s free daily limit: the ministries go on in the next run', e)
            return False
        except Exception as e:
            log('  ⚠️', str(e)[:160])
            bot('turbo_ytfail', id=v['id'], why=str(e)[:200])
    return True


# ---------------------------------------------------------------------- Rutube: the Кафедра as audio (no YouTube limit)
RUTUBE = '30869662'  # the church's Rutube channel: every Кафедра of Bishop Andrey and Pastor Olga since 2023


def recent_date(d, days):
    try:
        return (datetime.now() - datetime.strptime(d, '%d.%m.%Y')).days <= days
    except ValueError:
        return False


FRESH_DAYS = 21  # v51: recordings this new are the church's new ones: done first, every hour, and sent to everyone


def fresh(cfg, have):
    """v51: the newest Кафедра, Горница and morning words first, before any archive work."""
    try:
        if cfg.get('end'):
            rutube_kafedra(cfg, have, FRESH_DAYS)
        asyncio.run(telegram(cfg, have, ['gornitsa', 'morning'], FRESH_DAYS))
    except Daily as e:
        log('⏸ Gemini\'s free daily limit: the next run continues', e)
        return False
    return True


def kkey(date, speaker):
    """A Кафедра is known by its date and preacher (both preach on some Sundays)."""
    return 'kafedra|%s|%s' % (date, 'o' if re.search(r'Ольг|Olga', speaker or '', re.I) else 'a')


MONTHS = {'январ': 1, 'феврал': 2, 'март': 3, 'апрел': 4, 'ма': 5, 'июн': 6, 'июл': 7, 'август': 8, 'сентябр': 9, 'октябр': 10, 'ноябр': 11, 'декабр': 12}


def title_date(t):
    """The preached date written in the title: 01.11.2026 · 16.09.18 · 3 февраля 2019 · 23/апреля/2017; else ''."""
    m = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})\b', t)
    if m:
        y = m[3] if len(m[3]) == 4 else '20' + m[3]
        return '%02d.%02d.%s' % (int(m[1]), int(m[2]), y)
    m = re.search(r'(\d{1,2})\s*[/ ]\s*([а-яё]+)\s*[/ ]\s*(\d{4})', t.lower())
    if m:
        mon = next((n for k, n in sorted(MONTHS.items(), key=lambda x: -len(x[0])) if m[2].startswith(k)), 0)
        if mon:
            return '%02d.%02d.%s' % (int(m[1]), mon, m[3])
    return ''


def rutube_list():
    out, page = [], 1
    while page < 60:
        r = requests.get('https://rutube.ru/api/video/person/%s/?page=%d&page_size=20' % (RUTUBE, page), timeout=30, headers={'User-Agent': 'Mozilla/5.0'}).json()
        for v in r.get('results') or []:
            t = v.get('title') or ''
            up = t.upper()
            if not ('АНДРЕЙ' in up or 'ОЛЬГА' in up) or 'ПОЗДРАВ' in up or (v.get('duration') or 0) < 15 * 60:
                continue
            date = title_date(t)
            title = re.sub(r'^\s*(пастор|епископ)\s+(ольга|андрей)\s+матюжов\w*\s*[-–:]\s*', '', t, flags=re.I)
            title = re.sub(r'\s*(\||//|-)?\s*(пастор|епископ)\s+(ольга|андрей)\s+#?матюжов\w*.*$', '', title, flags=re.I)
            title = re.split(r'\s+\d{1,2}\.\d{1,2}\.\d{2,4}|\s+//', title)[0].strip(' |«»"-').strip()
            title = title[:1].upper() + title[1:].lower() if title.isupper() else title
            out.append({'id': v['id'], 'title': title or t[:80], 'date': date or datetime.fromisoformat(v['created_ts']).strftime('%d.%m.%Y'), 'dated': bool(date),
                        'len': v.get('duration') or 0, 'speaker': 'Пастор Ольга Матюжова' if 'ОЛЬГА' in up else 'Епископ Андрей Матюжов'})
        if not r.get('has_next'):
            break
        page += 1
    return out  # newest first


def rutube_audio(vid):
    """A Rutube video as a small MP3 (mono, 32 kbit/s: about 8 MB an hour), or None."""
    src = os.path.join(TMP, 'rt')
    for f in glob.glob(src + '*'):
        os.remove(f)
    r = subprocess.run([sys.executable, '-m', 'yt_dlp', '-q', '--no-warnings', '-f', 'worstaudio/worst', '-o', src + '.%(ext)s',
                        'https://rutube.ru/video/%s/' % vid], capture_output=True, text=True, timeout=1500)
    got = [f for f in glob.glob(src + '.*') if not f.endswith('.part')]
    if r.returncode != 0 or not got:
        log('  · Rutube download failed:', (r.stderr or '').strip()[-120:])
        return None
    mp3 = src + '-a.mp3'
    subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-i', got[0], '-vn', '-ac', '1', '-ar', '16000', '-b:a', '32k', mp3], check=True)
    os.remove(got[0])
    return mp3


def rutube_kafedra(cfg, have, fresh_days=0):
    """v40: every Кафедра from Rutube as small audio (about 8 MB an hour): fast, and no daily YouTube limit."""
    try:
        todo = [v for v in rutube_list() if 'rt:' + v['id'] not in have and not (v['dated'] and kkey(v['date'], v['speaker']) in have)]
        if fresh_days:  # v51: the new Кафедра only
            todo = [v for v in todo if v['dated'] and recent_date(v['date'], fresh_days)]
    except Exception as e:
        log('Rutube: list failed:', str(e)[:120])
        return
    if not fresh_days:
        bot('turbo_totals', totals={'kafedra': len(todo) + sum(1 for h in have if h.startswith('kafedra|'))})
    log('kafedra (Rutube): %d to do%s' % (len(todo), ' (new)' if fresh_days else ''))
    for v in todo:
        if left_min() < 25:
            return
        log('📝 kafedra', v['date'], v['speaker'].split()[-1], v['title'][:60])
        try:
            mp3 = rutube_audio(v['id'])
            if not mp3:
                continue
            tr, total = transcribe_audio(mp3, cfg['transcribe'])
            os.remove(mp3)
            if len(tr) < 200:
                log('  · no speech found')
                continue
            res = notes({'kind': 'kafedra', 'title': v['title'], 'date': v['date'], 'speaker': v['speaker'], 'rt': v['id']}, tr, total)
            log('  ✅' if res.get('ok') else '  · %s' % str(res)[:100])
            have.add('rt:' + v['id'])
            if v['dated']:
                have.add(kkey(v['date'], v['speaker']))
        except Daily as e:
            log('⏸', e)
            raise
        except Exception as e:
            log('  ⚠️', str(e)[:160])


# ---------------------------------------------------------------------- v43: a picture and a link for every Кафедра
def rt_links(cfg):
    """Кафедра notes with no video: found on Rutube by date and preacher (a picture and a link in the app)."""
    items = cfg.get('nort') or []
    if not items:
        return
    try:
        vids = rutube_list()
    except Exception as e:
        log('Rutube links: list failed:', str(e)[:100])
        return
    by = {kkey(v['date'], v['speaker']): v['id'] for v in vids if v['dated']}
    day = {}
    for v in vids:
        day.setdefault(v['date'], []).append(v)

    def near(d, k):  # the same preacher a few days before or after (a late upload)
        try:
            t = datetime.strptime(d, '%d.%m.%Y')
        except ValueError:
            return ''
        for off in (1, -1, 2, -2, 3, -3):  # v50: an upload up to three days late or early
            dd = (t + __import__('datetime').timedelta(days=off)).strftime('%d.%m.%Y')
            for v in day.get(dd, []):
                if kkey(dd, v['speaker']).endswith(k):
                    return v['id']
        return ''
    n = 0
    for it in items:
        sp = it.get('speaker', '') or ''
        known = bool(re.search(r'Андре|Andre|Ольг|Olga', sp, re.I))
        k = kkey(it['date'], sp)
        rt = by.get(k, '') if known else ''
        if not rt and not known and len(day.get(it['date'], [])) == 1:  # preacher unknown: the only video of that day
            rt = day[it['date']][0]['id']
        if not rt and known:
            rt = near(it['date'], k[-2:])
        bot('turbo_rtlink', id=it['id'], rt=rt)
        n += 1 if rt else 0
    log('Rutube links: %d of %d Кафедра linked' % (n, len(items)))


# ---------------------------------------------------------------------- v41: every church message gets its transcript
async def transcripts(cfg):
    """Notes without a transcript (often older ones): the recording is found (Rutube, the church's Telegram, YouTube),
    transcribed word for word with timestamps, and faithBOT saves it as a Google Doc next to the notes."""
    items = cfg.get('notr') or []
    if not items:
        return
    log('transcripts: %d messages have none yet' % len(items))
    rt, posts, client = None, None, None
    try:
        for it in items:
            if left_min() < 20:
                return
            path, total, tr = None, 0, ''
            try:
                if it['kind'] == 'kafedra':
                    vid = it.get('rt')
                    if not vid:
                        if rt is None:
                            rt = rutube_list()
                        m = next((v for v in rt if v['dated'] and kkey(v['date'], v['speaker']) == kkey(it['date'], it.get('speaker', ''))), None)
                        vid = m['id'] if m else None
                    if vid:
                        path = rutube_audio(vid)
                elif it['kind'] in ('gornitsa', 'morning'):
                    if posts is None:
                        client, posts = await post_index(cfg)
                    msg = posts.get('%s|%s' % (it['kind'], it['date']))
                    if msg:
                        path = await fetch_post(client, msg)
                if path:
                    log('🗒', it['kind'], it['date'], it['title'][:60])
                    tr, total = transcribe_audio(path, cfg['transcribe'])
                    os.remove(path)
                elif it.get('yt') and not YT_SPENT:
                    log('🗒 (YouTube)', it['kind'], it['date'], it['title'][:60])
                    tr, total = transcribe_youtube(it['yt'], 0, cfg['transcribe'])
                if len(tr) >= 200:
                    r = bot('turbo_trsave', id=it['id'], transcript=tr, duration=int(total))
                    log('  ✓ transcript saved' if r.get('ok') else '  · %s' % str(r)[:100])
                else:
                    bot('turbo_trmiss', id=it['id'])
                    log('  · recording not found:', it['kind'], it['date'])
            except Daily:
                if YT_SPENT and not all(m in SPENT for m in WRITE_MODELS):
                    continue  # only YouTube's daily allowance: the next one may be on Rutube or Telegram
                raise
            except Exception as e:
                log('  ⚠️', str(e)[:160])
                bot('turbo_trmiss', id=it['id'])
    finally:
        if client:
            await client.disconnect()


async def post_index(cfg):
    """Every Горница and morning word post of the church's Telegram, by kind and date (the newest post of a day wins)."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(os.environ['TG_SESSION']), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH'])
    await client.connect()
    await client.get_dialogs()
    out = {}
    for src in [x for x in cfg.get('sources') or [] if x['kind'] in ('gornitsa', 'morning')]:
        ch = str(src['ch'])
        entity = await client.get_entity(int(ch) if re.match(r'^-?\d+$', ch) else ch)
        need, skip = re.compile(src['need'], re.I), re.compile(r'форум|^\s*Начинаем|zoom\.us', re.I)  # v51: never a forum post
        async for msg in client.iter_messages(entity, reply_to=src.get('topic') or None):
            text = msg.message or ''
            if not (msg.video or post_link(msg)) or not need.search(text) or skip.search(text):
                continue
            _, date, _ = details(text, msg.date)
            out.setdefault('%s|%s' % (src['kind'], date), msg)
    return client, out


# ---------------------------------------------------------------------- v41: the mentor's night study
def night(cfg):
    """Dubai night (22:00–07:00): nobody is waiting for the bot."""
    a, b = cfg.get('night') or [22, 7]
    h = (datetime.now(timezone.utc).hour + int(cfg.get("tz", 4))) % 24
    return h >= a or h < b


def wiki_film(title, year, kind):
    """v49: the English Wikipedia summary of a film or series (the best match among the usual page names)."""
    names = ['%s (%s %s)' % (title, year, 'TV series' if kind == 'series' else 'film')] if year else []
    names += ['%s (%s)' % (title, 'TV series' if kind == 'series' else 'film'), title]
    for n in names:
        try:
            r = requests.get('https://en.wikipedia.org/api/rest_v1/page/summary/' + requests.utils.quote(n.replace(' ', '_')), timeout=20,
                             headers={'User-Agent': 'faithBOT-helper/1.0 (church film library)'})
            if r.status_code == 200:
                j = r.json()
                ex = j.get('extract') or ''
                if j.get('type') != 'disambiguation' and re.search(r'\b(film|movie|series|documentary|animated|television)\b', ex, re.I):
                    return j.get('title') or n, ex
        except Exception:
            pass
        time.sleep(1)
    return '', ''


def films(cfg):
    """v49: describes the films waiting in the library (Wikipedia + a light model), so faithBOT spends no minutes on them."""
    todo, base = cfg.get('films') or [], cfg.get('filmprompt') or ''
    if not todo or not base:
        return
    n, t0 = 0, time.time()
    for f in todo[:25]:
        if time.time() - t0 > 600 or left_min() < 30:
            break
        title, intro = wiki_film(f['en'], f.get('year'), f.get('kind'))
        prompt = (base.replace('{KIND}', 'series' if f.get('kind') == 'series' else 'film').replace('{TITLE}', f['en'])
                  .replace('{YEAR}', str(f.get('year') or '?')).replace('{EN}', intro or '(not found)').replace('{REC}', '(none)').replace('{RU}', '(not found)'))
        try:
            text, _ = gemini(FAST_MODELS, [{'text': prompt}], max_tokens=6144, as_json=True)
            r = json.loads(text[text.find('{'):text.rfind('}') + 1])
        except Daily:
            break
        except Exception as e:
            log('  · film %s: %s' % (f['en'], str(e)[:80]))
            continue
        out = bot('turbo_film', id=f['id'], r=r, wiki=title)
        n += 1 if out.get('ok') else 0
    log('🎬 films described: %d of %d waiting' % (n, len(todo)))


def study(cfg):
    if cfg.get('key2busy'):  # v45: the live bot is on the second key: leave its quota to the members
        log('study: the bot is using the second key, waiting'); return
    """Each Кафедра, Горница, morning word and YouTube ministry read in full once, written up for the Pastor (quietly)."""
    n = 0
    for it in cfg.get('study') or []:
        if left_min() < 10 or not night(cfg):
            break
        src = bot('turbo_src', id=it['id'])
        if src.get('gone'):
            continue
        if src.get('empty') or not src.get('prompt'):
            bot('turbo_stskip', id=it['id'])
            continue
        try:
            text, _ = gemini(WRITE_MODELS, [{'text': src['prompt']}], max_tokens=12288)
            text = re.sub(r'\n```\s*$', '', re.sub(r'^```(?:markdown|md)?\s*\n', '', text.strip())).strip()
            r = bot('turbo_study', id=it['id'], text=text)
            if r.get('ok'):
                n += 1
                log('🌙 studied:', it['kind'], it['date'], it['title'][:50])
        except Daily:
            raise
        except Exception as e:
            log('  · study skipped:', str(e)[:100])
            bot('turbo_stskip', id=it['id'])
    return n


def quiet(cfg):
    """v41: missing transcripts, older notes in the new format, and at night the study. After 21 Nov this is all that runs."""
    try:
        rt_links(cfg)  # v43
        redo(cfg)
        asyncio.run(transcripts(cfg))
        rounds = 0
        while night(cfg) and left_min() > 10 and rounds < 30:
            rounds += 1
            if not study(cfg):
                break
            cfg = bot('turbo_cfg')
    except Daily as e:
        log('⏸ Gemini\'s free daily limit: the next run continues', e)


def main():
    if not (URL and KEY and GKEY):
        sys.exit('FAITHBOT_URL, FAITHBOT_KEY and GEMINI_KEY are needed (GitHub → Settings → Secrets).')
    global LIMIT_MIN
    os.makedirs(PEND, exist_ok=True)
    cfg = bot('turbo_cfg')
    if cfg.get('after') and cfg.get('daily'):  # v52: the archive is closed: every new recording and the ministries go on, then the quiet work
        log('The archive is closed: new recordings, the ministries, Russian editions, then the quiet work.')
        have = HAVE
        have.update(cfg.get('have') or [])
        have.update(memo().get('done', []))
        if fresh(cfg, have) and ministries(cfg, have):  # v52: the new recordings and the ministries first
            try:
                run_pending(have)
            except Daily as e:
                log('⏸', e)
            editions(cfg)
            if left_min() > 20:
                quiet(cfg)
        shutil.rmtree(TMP, ignore_errors=True)
        return
    if cfg.get('after'):  # v41: the archive is closed (21 Nov 2026); transcripts, rewrites and the night study go on
        log('The archive is closed: missing transcripts, rewrites and the night study only.')
        quiet(cfg)
        shutil.rmtree(TMP, ignore_errors=True)
        return
    if cfg.get('ended'):
        print('The archive is closed (22 Nov 2026, 18:00 Dubai): faithBOT now makes notes only of new recordings. Nothing to do.')
        return
    if cfg.get('pause'):
        sys.exit('Paused by faithBOT.')
    LIMIT_MIN = min(LIMIT_MIN, float(cfg.get('minutes') or LIMIT_MIN))
    if cfg.get('light'):
        log('light pace: %d minutes today' % LIMIT_MIN)
    if not cfg.get('transcribe'):
        sys.exit('faithBOT did not answer (is v39 installed?): %s' % str(cfg)[:200])
    have = HAVE
    have.update(cfg.get('have') or [])
    have.update(memo().get('done', []))  # v45
    for path in glob.glob(os.path.join(PEND, '*.json')):  # still waiting: never transcribed again in this run
        try:
            m = json.load(open(path, encoding='utf-8'))['meta']
            have.add('%s|%s' % (m.get('kind'), m.get('date')))
            if m.get('rt'):
                have.add('rt:' + m['rt'])
            if m.get('yt'):
                have.add('yt:' + m['yt'])  # v52: a ministry video waiting for its notes is not transcribed again
        except Exception:
            pass
    if not cfg.get('pause') and not fresh(cfg, have):  # v51: the new recordings, before anything else
        shutil.rmtree(TMP, ignore_errors=True)
        return
    if not ministries(cfg, have):  # v52: then the YouTube ministries, in your order, newest first
        shutil.rmtree(TMP, ignore_errors=True)
        return
    try:
        run_pending(have)  # v44: transcripts already made (only their notes to write); v52: after the new recordings
    except Daily as e:
        log('⏸', e)
    rt_links(cfg)  # v43: pictures and links for the Кафедра (a few seconds); v52: after the new recordings
    films(cfg)  # v49: the film library's descriptions; v52: never before the church's new recordings
    editions(cfg)  # v51: Russian editions for every message, right after the new recordings
    redo(cfg)  # v39: the notes you already have, in the new format (no transcription needed: quick)
    rounds = 0
    while left_min() > 10 and rounds < 50:
        rounds += 1
        try:  # v50 order: Кафедра first (Rutube audio, then any only on YouTube), then Горница, then the morning words
            if cfg.get('end'):  # faithBOT v40 or newer (it knows Rutube ids and the two preachers of one day)
                rutube_kafedra(cfg, have)
            kafedra(cfg, have)
            asyncio.run(telegram(cfg, have, ['gornitsa']))
            asyncio.run(telegram(cfg, have, ['morning']))
        except Daily as e:
            log('⏸ Gemini\'s free daily limit: the next run continues', e)
            break
        cfg = bot('turbo_cfg')
        if cfg.get('pause') or not (cfg.get('yt') and not YT_SPENT) or left_min() < 40:
            break
    if left_min() > 20 and not cfg.get('pause') and not all(m in SPENT for m in WRITE_MODELS):
        quiet(cfg)  # v41: time left in this run: missing transcripts, then the night study
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == '__main__':
    main()
