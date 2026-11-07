# AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
API = os.environ.get('AUTOSCULPT_TEST_API', 'https://api.meshy.ai/openapi/v1')
SERVICE = 'Meshy'

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
    req = urllib.request.Request(url, data=data, method=method, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'User-Agent': 'AutoSculpt3D'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        try:
            detail = json.loads(detail).get('message', detail)
        except ValueError:
            pass
        if e.code in (401, 403):
            raise Fail('{service} refused the API key: check it in Tools → Photo → 3D engines', service=SERVICE)
        if e.code == 402:
            raise Fail('Not enough credits on your {service} account (the API needs a paid plan)', service=SERVICE)
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', action='append', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--work', default='.')
    ap.add_argument('--model', default='latest')
    ap.add_argument('--polycount', type=int, default=50000)
    a = ap.parse_args()
    key = os.environ.get('AUTOSCULPT_API_KEY', '')
    t0 = time.time()
    try:
        images = a.input[:4]
        say('PROGRESS', '0.03', 'Sending the photos to {service}', service=SERVICE)
        body = {'ai_model': a.model, 'should_texture': False, 'should_remesh': True, 'topology': 'triangle', 'target_polycount': max(100, min(300000, a.polycount)), 'target_formats': ['glb']}
        if len(images) > 1:
            kind = 'multi-image-to-3d'
            body['image_urls'] = [data_uri(p) for p in images]
        else:
            kind = 'image-to-3d'
            body['image_url'] = data_uri(images[0])
        task = call('POST', f'{API}/{kind}', key, body)['result']
        say('PROGRESS', '0.08', 'Waiting for {service}', service=SERVICE)
        while True:
            time.sleep(3)
            t = call('GET', f'{API}/{kind}/{task}', key)
            st, prog = (t.get('status'), int(t.get('progress') or 0))
            if st == 'SUCCEEDED':
                break
            if st in ('FAILED', 'CANCELED', 'EXPIRED'):
                msg = (t.get('task_error') or {}).get('message') or st
                raise Fail('{service} could not build the model: {detail}', service=SERVICE, detail=msg)
            say('PROGRESS', f'{0.08 + 0.8 * prog / 100:.2f}', '{service} is building the model ({p}%)', service=SERVICE, p=prog)
            if time.time() - t0 > 30 * 60:
                raise Fail('{service} is taking too long: try again later', service=SERVICE)
        url = (t.get('model_urls') or {}).get('glb')
        if not url:
            raise Fail('{service} did not return a model', service=SERVICE)
        say('PROGRESS', '0.92', 'Downloading the model')
        download(url, a.output)
        say('PROGRESS', '1', 'Done in {s} seconds', s=round(time.time() - t0))
        say('RESULT', a.output)
    except Fail as e:
        say('ERROR', e.text, **e.values)
        sys.exit(1)
    except Exception as e:
        say('ERROR', '{service} error: {detail}', service=SERVICE, detail=repr(e)[:300])
        sys.exit(1)
if __name__ == '__main__':
    main()
