# AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
API = os.environ.get('AUTOSCULPT_TEST_API', 'https://openapi.tripo3d.ai/v3')
SERVICE = 'Tripo'

def say(kind, *parts, **values):
    line = ' '.join([kind, *[str(p) for p in parts]])
    if values:
        line += '\t' + json.dumps(values)
    print(line, flush=True)

class Fail(Exception):

    def __init__(self, text, **values):
        super().__init__(text)
        self.text, self.values = (text, values)

def call(method, url, key, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'User-Agent': 'AutoSculpt3D'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        try:
            j = json.loads(detail)
            detail = j.get('message') or detail
        except (ValueError, AttributeError):
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

def upload(path, key):
    boundary = '----AutoSculpt3D' + str(int(time.time() * 1000))
    ext = os.path.splitext(path)[1].lower()
    mime = {'.png': 'image/png', '.webp': 'image/webp'}.get(ext, 'image/jpeg')
    body = f"""--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="photo{ext or '.jpg'}"\r\nContent-Type: {mime}\r\n\r\n""".encode() + open(path, 'rb').read() + f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(f'{API}/files', data=body, method='POST', headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'multipart/form-data; boundary=' + boundary, 'User-Agent': 'AutoSculpt3D'})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            j = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise Fail('{service} refused the API key: check it in Tools → Photo → 3D engines', service=SERVICE)
        raise Fail('{service} error {code}: {detail}', service=SERVICE, code=e.code, detail=e.read().decode('utf-8', 'replace')[:300])
    except urllib.error.URLError as e:
        raise Fail('Cannot reach {service}: check the internet connection ({detail})', service=SERVICE, detail=str(e.reason))
    token = (j.get('data') or {}).get('file_token')
    if not token:
        raise Fail('{service} error: {detail}', service=SERVICE, detail=str(j)[:300])
    return token

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', action='append', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--work', default='.')
    ap.add_argument('--model', default='v3.1-20260211')
    ap.add_argument('--geometry', default='detailed')
    ap.add_argument('--faces', type=int, default=50000)
    a = ap.parse_args()
    key = os.environ.get('AUTOSCULPT_API_KEY', '')
    t0 = time.time()
    try:
        say('PROGRESS', '0.03', 'Sending the photos to {service}', service=SERVICE)
        token = upload(a.input[0], key)
        body = {'input': token, 'model': a.model, 'texture': False, 'pbr': False, 'face_limit': a.faces}
        if not a.model.startswith('v2.'):
            body['geometry_quality'] = a.geometry
        r = call('POST', f'{API}/generation/image-to-model', key, body)
        if r.get('code') not in (0, None):
            raise Fail('{service} error: {detail}', service=SERVICE, detail=r.get('message') or str(r)[:300])
        task = r['data']['task_id']
        say('PROGRESS', '0.08', 'Waiting for {service}', service=SERVICE)
        while True:
            time.sleep(3)
            d = call('GET', f'{API}/tasks/{task}', key).get('data') or {}
            st, prog = (d.get('status'), int(d.get('progress') or 0))
            if st == 'success':
                break
            if st in ('failed', 'cancelled', 'banned', 'expired', 'unknown'):
                raise Fail('{service} could not build the model: {detail}', service=SERVICE, detail=d.get('error_msg') or st)
            say('PROGRESS', f'{0.08 + 0.8 * prog / 100:.2f}', '{service} is building the model ({p}%)', service=SERVICE, p=prog)
            if time.time() - t0 > 30 * 60:
                raise Fail('{service} is taking too long: try again later', service=SERVICE)
        out = d.get('output') or {}
        url = out.get('model_url') or out.get('pbr_model_url') or out.get('base_model_url') or out.get('model')
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
