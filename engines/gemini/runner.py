# AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.
import argparse
import base64
import json
import os
import http.client
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
API = os.environ.get('AUTOSCULPT_TEST_API', 'https://generativelanguage.googleapis.com')
SERVICE = 'Gemini'
IMAGE_MODELS = ['gemini-nano-banana-2.1', 'gemini-3.1-flash-image', 'gemini-2.5-flash-image']
TEXT_MODELS = ['gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-2.5-flash']
HERE = os.path.dirname(os.path.abspath(__file__))
SAY_LOCK = threading.Lock()

def say(kind, *parts, **values):
    line = ' '.join([kind, *[str(p) for p in parts]])
    if values:
        line += '\t' + json.dumps(values)
    print(line, flush=True)

class Fail(Exception):

    def __init__(self, text, **values):
        super().__init__(text)
        self.text, self.values = (text, values)

class NoModel(Exception):
    pass

class Busy(Exception):

    def __init__(self, quota=False):
        super().__init__('busy')
        self.quota = quota
RETRY_FOR = float(os.environ.get('AUTOSCULPT_GEMINI_RETRY', '120'))
RETRY_PAUSE = float(os.environ.get('AUTOSCULPT_GEMINI_PAUSE', '2'))
TEXT_WAIT = float(os.environ.get('AUTOSCULPT_GEMINI_TEXT_WAIT', '45'))
IMAGE_WAIT = float(os.environ.get('AUTOSCULPT_GEMINI_IMAGE_WAIT', '120'))

def slow_network(e):
    if isinstance(e, (socket.timeout, TimeoutError, ConnectionError, http.client.HTTPException)):
        return True
    r = getattr(e, 'reason', None)
    return isinstance(r, (socket.timeout, TimeoutError, ConnectionError)) or 'timed out' in str(r or e).lower()

class BadRequest(Fail):
    pass

def http_fail(model, code, raw):
    try:
        err = json.loads(raw).get('error') or {}
    except (ValueError, AttributeError):
        err = {}
    msg, status = (str(err.get('message') or raw)[:300], str(err.get('status') or ''))
    if code == 404 or status == 'NOT_FOUND':
        return NoModel(model)
    if code in (401, 403) or 'API key not valid' in msg or status in ('UNAUTHENTICATED', 'PERMISSION_DENIED'):
        return Fail('{service} refused the API key: check it in Tools → Engines and online services', service=SERVICE)
    if code == 429 or status == 'RESOURCE_EXHAUSTED':
        return Busy(quota=True)
    if code in (500, 502, 503, 504) or status in ('UNAVAILABLE', 'INTERNAL', 'DEADLINE_EXCEEDED'):
        return Busy()
    if status == 'FAILED_PRECONDITION' or 'billing' in msg.lower():
        return Fail('{service} needs billing enabled on your Google AI Studio project to draw pictures', service=SERVICE)
    if code == 400:
        return BadRequest('{service} error {code}: {detail}', service=SERVICE, code=code, detail=msg)
    return Fail('{service} error {code}: {detail}', service=SERVICE, code=code, detail=msg)

def open_url(url, key, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST', headers={'x-goog-api-key': key, 'Content-Type': 'application/json', 'User-Agent': 'AutoSculpt3D'})
    return urllib.request.urlopen(req, timeout=timeout)

def net_error(e, model):
    if isinstance(e, urllib.error.HTTPError):
        return http_fail(model, e.code, e.read().decode('utf-8', 'replace')[:600])
    if isinstance(e, urllib.error.URLError) and (not slow_network(e)):
        return Fail('Cannot reach {service}: check the internet connection ({detail})', service=SERVICE, detail=str(e.reason))
    if isinstance(e, (OSError, http.client.HTTPException, ValueError)):
        return Busy()
    return e

def post(model, key, body, timeout=TEXT_WAIT):
    try:
        with open_url(f'{API}/v1beta/models/{model}:generateContent', key, body, timeout) as r:
            return json.loads(r.read() or b'{}')
    except Exception as e:
        raise net_error(e, model)

def ask(models, key, body, timeout=TEXT_WAIT):
    end, usable, quota = (time.time() + RETRY_FOR + timeout, list(models), False)
    while True:
        busy = False
        for m in list(usable):
            left = end - time.time()
            if left < 5 and busy:
                break
            try:
                return post(m, key, body, max(min(10, timeout), min(timeout, left)))
            except NoModel:
                usable.remove(m)
            except Busy as b:
                busy, quota = (True, quota or b.quota)
        if not usable:
            raise Fail('{service}: none of the models is available for this API key', service=SERVICE)
        if not busy or time.time() + RETRY_PAUSE > end:
            break
        time.sleep(RETRY_PAUSE)
    raise busy_fail(quota)

def busy_fail(quota):
    if quota:
        return Fail('{service} has reached the limit of your account: wait a minute, or enable billing in Google AI Studio', service=SERVICE)
    return Fail('{service} is busy: try again in a minute', service=SERVICE)
_LISTED = {}
_LIST_LOCK = threading.Lock()

def listed_models(key):
    with _LIST_LOCK:
        return _listed(key)

def _listed(key):
    if 'all' not in _LISTED:
        names = []
        try:
            req = urllib.request.Request(f'{API}/v1beta/models?pageSize=200', headers={'x-goog-api-key': key, 'User-Agent': 'AutoSculpt3D'})
            with urllib.request.urlopen(req, timeout=10) as r:
                for m in json.loads(r.read() or b'{}').get('models') or []:
                    name = str(m.get('name') or '').replace('models/', '', 1)
                    if name.startswith('gemini-') and 'generateContent' in (m.get('supportedGenerationMethods') or []):
                        names.append(name)
        except Exception:
            pass
        _LISTED['all'] = names
    return _LISTED['all']

def version_of(name):
    for t in name.split('-')[1:]:
        try:
            return float(t)
        except ValueError:
            pass
    return 0.0

def text_models(key):
    skip = ('image', 'tts', 'live', 'audio', 'embed', 'thinking', 'exp', 'learnlm', 'robotics', 'computer', 'omni', 'customtools', 'transcribe')
    found = [n for n in listed_models(key) if 'flash' in n and (not any((w in n for w in skip)))]
    rank = lambda n: (('lite' in n) + 2 * ('preview' in n or 'latest' in n), -version_of(n))
    names = sorted(found, key=rank)
    names += [n for n in TEXT_MODELS + ['gemini-2.5-flash-lite'] if n not in names]
    return names[:8]

def image_models(key):
    found = [n for n in listed_models(key) if ('image' in n or 'nano-banana' in n) and 'imagen' not in n]
    rank = lambda n: ('preview' in n, 'nano-banana' not in n, 'lite' in n, 'flash' not in n and 'nano-banana' not in n, -version_of(n))
    names = sorted(found, key=rank)
    names += [n for n in IMAGE_MODELS if n not in names]
    return names[:6]
FIRST_WAIT = float(os.environ.get('AUTOSCULPT_GEMINI_FIRST', '6'))
LONG_WAIT = float(os.environ.get('AUTOSCULPT_GEMINI_LONG', '240'))

def stream(model, key, body, q):
    try:
        r = open_url(f'{API}/v1beta/models/{model}:streamGenerateContent?alt=sse', key, body, LONG_WAIT)
    except Exception as e:
        q.put(('err', net_error(e, model)))
        return
    text, alive, finish = ([], False, None)
    try:
        with r:
            for raw in r:
                line = raw.decode('utf-8', 'replace').strip()
                if not line.startswith('data:'):
                    continue
                v = json.loads(line[5:].strip() or '{}')
                if v.get('error'):
                    e = v['error']
                    q.put(('err', http_fail(model, int(e.get('code') or 500), json.dumps(v))))
                    return
                fb = v.get('promptFeedback') or {}
                if fb.get('blockReason'):
                    q.put(('err', Fail('{service} refused this description ({reason}): write it in another way', service=SERVICE, reason=fb['blockReason'])))
                    return
                if not alive:
                    alive = True
                    q.put(('alive', None))
                for c in (v.get('candidates') or [])[:1]:
                    finish = c.get('finishReason') or finish
                    for p in (c.get('content') or {}).get('parts') or []:
                        if not p.get('thought') and p.get('text'):
                            text.append(p['text'])
    except Exception as e:
        q.put(('err', net_error(e, model)))
        return
    out = ''.join(text).strip()
    if not out and finish not in (None, 'STOP', 'MAX_TOKENS'):
        q.put(('err', Fail('{service} refused this description ({reason}): write it in another way', service=SERVICE, reason=finish)))
    else:
        q.put(('done', out))

def ask_text(key, body):
    import queue
    thoughts = json.loads(json.dumps(body))
    thoughts.setdefault('generationConfig', {})['thinkingConfig'] = {'includeThoughts': True}
    end, usable, quota, rnd = (time.time() + RETRY_FOR, text_models(key), False, 0)
    while True:
        busy = False
        for m in list(usable):
            if busy and time.time() > end:
                break
            for b in (thoughts, body):
                q = queue.Queue()
                threading.Thread(target=stream, args=(m, key, b, q), daemon=True).start()
                try:
                    ev, val = q.get(timeout=FIRST_WAIT * (1 if rnd < 2 else 2.5))
                    if ev == 'alive':
                        say('PROGRESS', '0.3', '{service} is working', service=SERVICE)
                        ev, val = q.get(timeout=LONG_WAIT)
                except queue.Empty:
                    busy = True
                    break
                if ev == 'done':
                    if val:
                        return val
                    busy = True
                    break
                if isinstance(val, BadRequest) and b is thoughts:
                    continue
                if isinstance(val, NoModel):
                    usable.remove(m)
                    break
                if isinstance(val, Busy):
                    busy, quota = (True, quota or val.quota)
                    break
                raise val
        rnd += 1
        if not usable:
            raise Fail('{service}: none of the models is available for this API key', service=SERVICE)
        if not busy or time.time() + RETRY_PAUSE > end:
            break
        time.sleep(RETRY_PAUSE)
    raise busy_fail(quota)

def parts_of(res):
    fb = res.get('promptFeedback') or {}
    if fb.get('blockReason'):
        raise Fail('{service} refused this description ({reason}): write it in another way', service=SERVICE, reason=fb['blockReason'])
    cands = res.get('candidates') or []
    if not cands:
        raise Fail('{service} returned nothing: try again', service=SERVICE)
    c = cands[0]
    parts = (c.get('content') or {}).get('parts') or []
    if not parts and c.get('finishReason') not in (None, 'STOP'):
        raise Fail('{service} refused this description ({reason}): write it in another way', service=SERVICE, reason=c.get('finishReason'))
    return parts

def image_of(res):
    for p in parts_of(res):
        d = p.get('inlineData') or p.get('inline_data')
        if d and d.get('data') and (not p.get('thought')):
            return base64.b64decode(d['data'])
    raise Fail('{service} did not return any picture: write the description in another way', service=SERVICE)

def text_body(instruction, text, json_out=False):
    body = {'contents': [{'role': 'user', 'parts': [{'text': instruction + '\n\n' + text if text else instruction}]}], 'generationConfig': {'temperature': 0.4}}
    if json_out:
        body['generationConfig']['responseMimeType'] = 'application/json'
    return body

def image_body(text, reference=None, aspect='1:1'):
    parts = [{'text': text}]
    if reference is not None:
        parts.append({'inline_data': {'mime_type': mime_of(reference), 'data': base64.b64encode(reference).decode()}})
    return {'contents': [{'role': 'user', 'parts': parts}], 'generationConfig': {'responseModalities': ['IMAGE'], 'imageConfig': {'aspectRatio': aspect}}}

def mime_of(data):
    return 'image/png' if data[:8] == b'\x89PNG\r\n\x1a\n' else 'image/webp' if data[8:12] == b'WEBP' else 'image/jpeg'

def save_picture(data, path):
    with open(path, 'wb') as f:
        f.write(data)

def in_parallel(jobs):
    out, errs = ([None] * len(jobs), [])

    def run(i, fn):
        try:
            out[i] = fn()
        except Exception as e:
            errs.append(e)
    th = [threading.Thread(target=run, args=(i, fn), daemon=True) for i, fn in enumerate(jobs)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    if errs and (not any((o is not None for o in out))):
        raise errs[0]
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--task', default='')
    ap.add_argument('--input-file', default='')
    ap.add_argument('--context-file', default='')
    ap.add_argument('--audio', default='')
    ap.add_argument('--output', default='')
    ap.add_argument('--prompt-file', default='')
    ap.add_argument('--output-dir', default='')
    ap.add_argument('--image', default='')
    ap.add_argument('--count', type=int, default=4)
    ap.add_argument('--lang', default='en')
    ap.add_argument('--symmetric', default='0')
    ap.add_argument('--work', default='.')
    a = ap.parse_args()
    key = os.environ.get('AUTOSCULPT_API_KEY', '')
    with open(os.path.join(HERE, 'prompts.json'), encoding='utf-8') as f:
        P = json.load(f)
    t0 = time.time()
    try:
        if a.task:
            text = open(a.input_file, encoding='utf-8').read().strip()
            if not text:
                raise Fail('Write a description of the subject')
            if a.task == 'prompt':
                say('PROGRESS', '0.1', '{service} is writing the prompt', service=SERVICE)
                sym = a.symmetric == '1'
                instr = P['subject'] + ' ' + P['subject_symmetric' if sym else 'subject_asymmetric']
                subject = ' '.join(ask_text(key, text_body(instr, 'DESCRIPTION:\n' + text)).split()).strip().rstrip('.')
                if not subject:
                    raise Fail('{service} gave an answer that is not readable: try again', service=SERVICE)
                out = P['sheet_symmetric' if sym else 'sheet_asymmetric'].replace('{subject}', subject)
            elif a.task == 'metalwork':
                say('PROGRESS', '0.1', '{service} is reading the request', service=SERVICE)
                ctx = open(a.context_file, encoding='utf-8').read() if a.context_file else '{}'
                out = ask_text(key, text_body(P['metalwork'].replace('{context}', ctx).replace('{text}', text), '', json_out=True))
                try:
                    json.loads(out)
                except ValueError:
                    raise Fail('{service} gave an answer that is not readable: try again', service=SERVICE)
            else:
                raise Fail('Unknown task {task}', task=a.task)
            with open(a.output, 'w', encoding='utf-8') as f:
                f.write(out)
            say('PROGRESS', '1', 'Done in {s} seconds', s=round(time.time() - t0))
            say('RESULT', a.output)
            return
        if a.audio:
            say('PROGRESS', '0.1', '{service} is listening', service=SERVICE)
            with open(a.audio, 'rb') as f:
                wav = f.read()
            hint = f' (probably {a.lang})' if a.lang else ''
            body = {'contents': [{'role': 'user', 'parts': [{'text': P['transcribe'].replace('{lang_hint}', hint)}, {'inline_data': {'mime_type': 'audio/wav', 'data': base64.b64encode(wav).decode()}}]}], 'generationConfig': {'temperature': 0}}
            text = ask_text(key, body)
            with open(a.output, 'w', encoding='utf-8') as f:
                f.write(text)
            say('PROGRESS', '1', 'Done in {s} seconds', s=round(time.time() - t0))
            say('RESULT', a.output)
            return
        os.makedirs(a.output_dir, exist_ok=True)
        if a.image:
            with open(a.image, 'rb') as f:
                ref = f.read()
            say('PROGRESS', '0.05', '{service} is drawing the other sides', service=SERVICE)
            done = [0]

            def side(i, s):

                def fn():
                    img = image_of(ask(image_models(key), key, image_body(P['views'].replace('{side}', s), ref), IMAGE_WAIT))
                    p = os.path.join(a.output_dir, f'image{i + 2}.png')
                    save_picture(img, p)
                    with SAY_LOCK:
                        done[0] += 1
                        say('PROGRESS', f'{0.1 + 0.85 * done[0] / 3:.2f}', '{service} is drawing the other sides', service=SERVICE)
                    return p
                return fn
            paths = in_parallel([side(i, s) for i, s in enumerate(P['sides'][:3])])
            if any((p is None for p in paths)):
                for p in paths:
                    if p:
                        os.remove(p)
                raise Fail('{service} could not draw every side: try again', service=SERVICE)
            shutil.copyfile(a.image, os.path.join(a.output_dir, 'image1.png'))
            for p in [os.path.join(a.output_dir, 'image1.png'), *paths]:
                say('IMAGE', p)
        else:
            prompt = open(a.prompt_file, encoding='utf-8').read().strip()
            if not prompt:
                raise Fail('Write a description of the subject')
            n = max(1, min(a.count, 6))
            say('PROGRESS', '0.05', '{service} is drawing the pictures', service=SERVICE)
            done = [0]

            def pic(i):

                def fn():
                    t = P['image'].replace('{prompt}', prompt) + ('\n' + P['image_variant'].replace('{n}', str(i + 1)) if i else '')
                    img = image_of(ask(image_models(key), key, image_body(t, aspect=P.get('image_aspect', '1:1')), IMAGE_WAIT))
                    p = os.path.join(a.output_dir, f'image{i + 1}.png')
                    save_picture(img, p)
                    with SAY_LOCK:
                        done[0] += 1
                        say('PROGRESS', f'{0.1 + 0.85 * done[0] / n:.2f}', '{service} is drawing the pictures', service=SERVICE)
                        say('IMAGE', p)
                    return p
                return fn
            if not any(in_parallel([pic(i) for i in range(n)])):
                raise Fail('{service} did not return any picture: write the description in another way', service=SERVICE)
        say('PROGRESS', '1', 'Done in {s} seconds', s=round(time.time() - t0))
        say('RESULT', a.output_dir)
    except Fail as e:
        say('ERROR', e.text, **e.values)
        sys.exit(1)
    except Exception as e:
        say('ERROR', '{service} error: {detail}', service=SERVICE, detail=repr(e)[:300])
        sys.exit(1)
if __name__ == '__main__':
    main()
