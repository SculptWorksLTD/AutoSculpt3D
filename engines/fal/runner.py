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
MODELS = {'trellis2': {'name': 'TRELLIS 2', 'endpoint': 'fal-ai/trellis-2', 'images': 'image_url', 'max': 1, 'params': {'resolution': 1024}, 'output': ('model_glb',)}, 'hunyuan': {'name': 'Hunyuan 3D', 'endpoint': 'fal-ai/hunyuan-3d/v3.1/rapid/image-to-3d', 'images': 'input_image_url', 'max': 1, 'params': {'enable_geometry': True}, 'output': ('model_glb',)}, 'rodin': {'name': 'Rodin', 'endpoint': 'fal-ai/hyper3d/rodin/v2.5', 'images': 'image_urls', 'max': 5, 'params': {'geometry_file_format': 'glb', 'material': 'None', 'tier': 'High'}, 'output': ('model_mesh',)}}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', action='append', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--work', default='.')
    ap.add_argument('--model', default='trellis2')
    a = ap.parse_args()
    key = os.environ.get('AUTOSCULPT_API_KEY', '')
    m = MODELS.get(a.model, MODELS['trellis2'])
    t0 = time.time()
    try:
        say('PROGRESS', '0.03', 'Sending the photos to {service}', service=m['name'])
        uris = [data_uri(p) for p in a.input[:m['max']]]
        body = dict(m['params'])
        body[m['images']] = uris if m['images'].endswith('s') else uris[0]
        sub = call('POST', f"{API}/{m['endpoint']}", key, body, timeout=300)
        status_url, response_url = (sub.get('status_url'), sub.get('response_url'))
        if not (status_url and response_url):
            raise Fail('{service} error: {detail}', service=SERVICE, detail=str(sub)[:300])
        say('PROGRESS', '0.08', 'Waiting for {service}', service=m['name'])
        n = 0
        while True:
            time.sleep(3)
            n += 1
            st = call('GET', status_url, key)
            s = st.get('status')
            if s == 'COMPLETED':
                break
            if s == 'IN_QUEUE':
                say('PROGRESS', '0.10', 'In the {service} queue (position {n})', service=m['name'], n=st.get('queue_position', '?'))
            else:
                say('PROGRESS', f'{0.12 + 0.75 * (1 - 0.97 ** n):.2f}', '{service} is building the model', service=m['name'])
            if time.time() - t0 > 30 * 60:
                raise Fail('{service} is taking too long: try again later', service=m['name'])
        res = call('GET', response_url, key, timeout=300)
        f = next((res[k] for k in m['output'] if isinstance(res.get(k), dict) and res[k].get('url')), None)
        if not f:
            raise Fail('{service} did not return a model', service=m['name'])
        say('PROGRESS', '0.92', 'Downloading the model')
        download(f['url'], a.output)
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
