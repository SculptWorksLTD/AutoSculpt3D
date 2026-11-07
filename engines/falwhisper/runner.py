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
LANGS = {'it': 'it', 'en': 'en', 'de': 'de', 'fr': 'fr', 'es': 'es', 'pt': 'pt', 'ja': 'ja'}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--audio', required=True)
    ap.add_argument('--lang', default='')
    ap.add_argument('--output', required=True)
    ap.add_argument('--work', default='.')
    a = ap.parse_args()
    key = os.environ.get('AUTOSCULPT_API_KEY', '')
    t0 = time.time()
    try:
        say('PROGRESS', '0.1', 'Sending the recording to {service}', service='Whisper')
        uri = 'data:audio/wav;base64,' + base64.b64encode(open(a.audio, 'rb').read()).decode()
        body = {'audio_url': uri, 'task': 'transcribe', 'chunk_level': 'segment'}
        if a.lang in LANGS:
            body['language'] = LANGS[a.lang]
        sub = call('POST', f'{API}/fal-ai/whisper', key, body, timeout=120)
        status_url, response_url = (sub.get('status_url'), sub.get('response_url'))
        if not (status_url and response_url):
            raise Fail('{service} error: {detail}', service=SERVICE, detail=str(sub)[:300])
        while True:
            time.sleep(1)
            if call('GET', status_url, key).get('status') == 'COMPLETED':
                break
            if time.time() - t0 > 180:
                raise Fail('{service} is taking too long: try again later', service='Whisper')
        text = (call('GET', response_url, key, timeout=60).get('text') or '').strip()
        if not text:
            raise Fail('No words were recognised: speak a little louder and closer')
        with open(a.output, 'w', encoding='utf-8') as f:
            f.write(text)
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
