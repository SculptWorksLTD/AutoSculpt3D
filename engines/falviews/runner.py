# AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
API = os.environ.get('AUTOSCULPT_TEST_API', 'https://queue.fal.run')
SERVICE = 'fal.ai'

def say(kind, *parts, **values):
    line = ' '.join([kind, *[str(p) for p in parts]])
    if values:
        line += '\t' + json.dumps(values)
    print(line, flush=True)

class Fail(Exception):

    def __init__(self, text, **values):
        super().__init__(text)
        self.text, self.values = (text, values)

def data_uri(path):
    ext = os.path.splitext(path)[1].lower()
    mime = {'.png': 'image/png', '.webp': 'image/webp'}.get(ext, 'image/jpeg')
    return f'data:{mime};base64,' + base64.b64encode(open(path, 'rb').read()).decode()

def call(method, url, key, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={'Authorization': 'Key ' + key, 'Content-Type': 'application/json', 'User-Agent': 'AutoSculpt3D'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        try:
            j = json.loads(detail)
            detail = j.get('detail') or j.get('message') or detail
            if not isinstance(detail, str):
                detail = json.dumps(detail)[:300]
        except ValueError:
            pass
        if e.code == 402 or (e.code == 403 and ('balance' in detail.lower() or 'locked' in detail.lower())):
            raise Fail('Not enough credits on your {service} account (the API needs a paid plan)', service=SERVICE)
        if e.code in (401, 403):
            raise Fail('{service} refused the API key: check it in Tools → Photo → 3D engines', service=SERVICE)
        if e.code == 429:
            raise Fail('{service} is busy: try again in a minute', service=SERVICE)
        raise Fail('{service} error {code}: {detail}', service=SERVICE, code=e.code, detail=detail)
    except urllib.error.URLError as e:
        raise Fail('Cannot reach {service}: check the internet connection ({detail})', service=SERVICE, detail=str(e.reason))

def download(url, dest):
    req = urllib.request.Request(url, headers={'User-Agent': 'AutoSculpt3D'})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, 'wb') as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
SIDES = [('its right side', 'turned a quarter turn: the camera has moved 90 degrees around the object, to its right'), ('the back', 'turned half a turn: the camera has moved 180 degrees around the object and now sees its back'), ('its left side', 'turned three quarters of a turn: the camera has moved 270 degrees around the object, to its left')]
PROMPT = 'Show exactly the same {subject} from {side}, {how}. Keep the same three-quarter angle slightly from above, the same shape, proportions, pose, colours and every detail; the whole object visible and centred on a plain white background, soft even light, no shadow, nothing added.'

def ask(key, body, label, t0):
    sub = call('POST', f'{API}/fal-ai/nano-banana/edit', key, body, timeout=120)
    status_url, response_url = (sub.get('status_url'), sub.get('response_url'))
    if not (status_url and response_url):
        raise Fail('{service} error: {detail}', service=SERVICE, detail=str(sub)[:300])
    while True:
        time.sleep(2)
        if call('GET', status_url, key).get('status') == 'COMPLETED':
            break
        if time.time() - t0 > 10 * 60:
            raise Fail('{service} is taking too long: try again later', service=SERVICE)
    res = call('GET', response_url, key, timeout=120)
    imgs = [im.get('url') for im in res.get('images', []) if im.get('url')]
    if not imgs:
        raise Fail('{service} did not return any picture', service=SERVICE)
    return imgs[0]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--image', required=True)
    ap.add_argument('--prompt-file', default='')
    ap.add_argument('--lang', default='en')
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--work', default='.')
    ap.add_argument('--count', type=int, default=4)
    a = ap.parse_args()
    key = os.environ.get('AUTOSCULPT_API_KEY', '')
    t0 = time.time()
    try:
        subject = 'object'
        if a.prompt_file and os.path.exists(a.prompt_file) and (a.lang == 'en'):
            subject = open(a.prompt_file, encoding='utf-8').read().strip()[:200] or 'object'
        os.makedirs(a.output_dir, exist_ok=True)
        first = os.path.join(a.output_dir, 'image1.png')
        with open(a.image, 'rb') as fi, open(first, 'wb') as fo:
            fo.write(fi.read())
        say('IMAGE', first)
        ref = data_uri(a.image)
        import threading
        results, errors = ({}, [])

        def one(i, side, how):
            try:
                body = {'prompt': PROMPT.format(subject=subject, side=side, how=how), 'image_urls': [ref], 'num_images': 1, 'output_format': 'png', 'aspect_ratio': '1:1'}
                results[i] = ask(key, body, side, t0)
            except Fail as e:
                errors.append(e)
            except Exception as e:
                errors.append(Fail('{service} error: {detail}', service=SERVICE, detail=repr(e)[:300]))
        say('PROGRESS', '0.05', 'Sending the picture to {service}', service=SERVICE)
        threads = [threading.Thread(target=one, args=(i, s, h)) for i, (s, h) in enumerate(SIDES)]
        for t in threads:
            t.start()
        n = 0
        while any((t.is_alive() for t in threads)):
            time.sleep(1)
            n += 1
            say('PROGRESS', f'{0.1 + 0.7 * (1 - 0.95 ** n):.2f}', '{service} is drawing the other sides', service=SERVICE)
        if errors and (not results):
            raise errors[0]
        for i in sorted(results):
            p = os.path.join(a.output_dir, f'image{i + 2}.png')
            download(results[i], p)
            say('IMAGE', p)
        if errors:
            raise errors[0]
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
