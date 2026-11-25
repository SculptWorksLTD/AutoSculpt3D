# AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.
import argparse
import base64
import json
import os
import shutil
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

def post(model, key, body, timeout=180):
    url = f'{API}/v1beta/models/{model}:generateContent'
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST', headers={'x-goog-api-key': key, 'Content-Type': 'application/json', 'User-Agent': 'AutoSculpt3D'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')[:600]
        try:
            err = json.loads(raw).get('error') or {}
        except ValueError:
            err = {}
        msg, status = (str(err.get('message') or raw)[:300], str(err.get('status') or ''))
        if e.code == 404 or status == 'NOT_FOUND':
            raise NoModel(model)
        if e.code in (401, 403) or 'API key not valid' in msg or status in ('UNAUTHENTICATED', 'PERMISSION_DENIED'):
            raise Fail('{service} refused the API key: check it in Tools → Engines and online services', service=SERVICE)
        if e.code == 429 or status == 'RESOURCE_EXHAUSTED':
            raise Fail('{service} has reached the limit of your account: wait a minute, or enable billing in Google AI Studio', service=SERVICE)
        if status == 'FAILED_PRECONDITION' or 'billing' in msg.lower():
            raise Fail('{service} needs billing enabled on your Google AI Studio project to draw pictures', service=SERVICE)
        raise Fail('{service} error {code}: {detail}', service=SERVICE, code=e.code, detail=msg)
    except urllib.error.URLError as e:
        raise Fail('Cannot reach {service}: check the internet connection ({detail})', service=SERVICE, detail=str(e.reason))

def ask(models, key, body, timeout=180):
    for m in models:
        try:
            return post(m, key, body, timeout)
        except NoModel:
            continue
    raise Fail('{service}: none of the models is available for this API key', service=SERVICE)

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

def text_of(res):
    t = ''.join((p.get('text', '') for p in parts_of(res) if not p.get('thought'))).strip()
    if not t:
        raise Fail('{service} returned nothing: try again', service=SERVICE)
    return t

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

def image_body(text, reference=None):
    parts = [{'text': text}]
    if reference is not None:
        parts.append({'inline_data': {'mime_type': mime_of(reference), 'data': base64.b64encode(reference).decode()}})
    return {'contents': [{'role': 'user', 'parts': parts}], 'generationConfig': {'responseModalities': ['IMAGE'], 'imageConfig': {'aspectRatio': '1:1'}}}

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
                instr = P['prompt'] + (' ' + P['prompt_symmetric'] if a.symmetric == '1' else '')
                out = text_of(ask(TEXT_MODELS, key, text_body(instr, 'DESCRIPTION:\n' + text)))
            elif a.task == 'metalwork':
                say('PROGRESS', '0.1', '{service} is reading the request', service=SERVICE)
                ctx = open(a.context_file, encoding='utf-8').read() if a.context_file else '{}'
                out = text_of(ask(TEXT_MODELS, key, text_body(P['metalwork'].replace('{context}', ctx).replace('{text}', text), '', json_out=True)))
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
            text = text_of(ask(TEXT_MODELS, key, body, timeout=120))
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
                    img = image_of(ask(IMAGE_MODELS, key, image_body(P['views'].replace('{side}', s), ref)))
                    p = os.path.join(a.output_dir, f'image{i + 2}.png')
                    save_picture(img, p)
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
                    img = image_of(ask(IMAGE_MODELS, key, image_body(t)))
                    p = os.path.join(a.output_dir, f'image{i + 1}.png')
                    save_picture(img, p)
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
