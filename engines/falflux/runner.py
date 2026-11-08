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
STYLE = '{subject}. A single complete object, whole body fully visible, centered, three-quarter view, isolated on a plain white background, soft even studio lighting, sharp focus, detailed realistic sculpture reference'
STYLE_SYM = '{subject}. A single complete object, whole body fully visible, centered, straight front view facing the camera, symmetrical pose, left and right sides identical, isolated on a plain white background, soft even studio lighting, sharp focus, detailed realistic sculpture reference'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prompt-file', required=True)
    ap.add_argument('--lang', default='en')
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--work', default='.')
    ap.add_argument('--count', type=int, default=4)
    ap.add_argument('--quality', default='normal')
    ap.add_argument('--symmetric', default='0')
    a = ap.parse_args()
    key = os.environ.get('AUTOSCULPT_API_KEY', '')
    t0 = time.time()
    try:
        subject = open(a.prompt_file, encoding='utf-8').read().strip()
        if not subject:
            raise Fail('Write a description of the subject')
        endpoint = 'fal-ai/flux/dev' if a.quality == 'high' else 'fal-ai/flux/schnell'
        say('PROGRESS', '0.05', 'Sending the description to {service}', service='FLUX')
        body = {'prompt': (STYLE_SYM if a.symmetric == '1' else STYLE).format(subject=subject), 'image_size': 'square_hd', 'num_images': max(1, min(a.count, 4)), 'enable_safety_checker': True, 'output_format': 'png'}
        if endpoint.endswith('schnell'):
            body['num_inference_steps'] = 4
        sub = call('POST', f'{API}/{endpoint}', key, body, timeout=120)
        status_url, response_url = (sub.get('status_url'), sub.get('response_url'))
        if not (status_url and response_url):
            raise Fail('{service} error: {detail}', service=SERVICE, detail=str(sub)[:300])
        n = 0
        while True:
            time.sleep(2)
            n += 1
            s = call('GET', status_url, key).get('status')
            if s == 'COMPLETED':
                break
            say('PROGRESS', f'{0.1 + 0.7 * (1 - 0.9 ** n):.2f}', '{service} is drawing the pictures', service='FLUX')
            if time.time() - t0 > 10 * 60:
                raise Fail('{service} is taking too long: try again later', service='FLUX')
        res = call('GET', response_url, key, timeout=120)
        imgs = [im.get('url') for im in res.get('images', []) if im.get('url')]
        if not imgs:
            raise Fail('{service} did not return any picture', service='FLUX')
        os.makedirs(a.output_dir, exist_ok=True)
        for i, u in enumerate(imgs):
            say('PROGRESS', f'{0.85 + 0.14 * i / len(imgs):.2f}', 'Downloading the pictures')
            p = os.path.join(a.output_dir, f'image{i + 1}.png')
            download(u, p)
            say('IMAGE', p)
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
