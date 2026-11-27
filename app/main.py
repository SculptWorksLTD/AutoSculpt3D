# AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.
import argparse
import base64
import hashlib
import json
import mimetypes
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
APP_NAME = 'AutoSculpt 3D'
BUNDLE_APP = Path(__file__).resolve().parent
RESOURCES = Path(os.environ.get('AUTOSCULPT_BUNDLE', BUNDLE_APP))
VERSION = (BUNDLE_APP / 'VERSION').read_text().strip() if (BUNDLE_APP / 'VERSION').exists() else '0.0.0'
IS_WIN, IS_MAC = (sys.platform == 'win32', sys.platform == 'darwin')

def default_support():
    if IS_WIN:
        return Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming')) / 'AutoSculpt3D'
    if IS_MAC:
        return Path.home() / 'Library' / 'Application Support' / 'AutoSculpt3D'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'AutoSculpt3D'
SUPPORT = Path(os.environ.get('AUTOSCULPT_HOME', default_support()))
for sub in ('jobs', 'logs', 'ui', 'app', 'updates'):
    (SUPPORT / sub).mkdir(parents=True, exist_ok=True)
LOG = SUPPORT / 'logs' / 'autosculpt.log'
SETTINGS_FILE = SUPPORT / 'settings.json'
TOKEN = secrets.token_urlsafe(24)
NO_WINDOW = {'creationflags': 134217728} if IS_WIN else {}

def clean_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONHOME', 'PYTHONPATH', '__PYVENV_LAUNCHER__')}
    env.update(extra or {})
    return env

def other_app_env():
    return {k: v for k, v in clean_env().items() if not k.startswith('AUTOSCULPT_')}

def documents_dir():
    if IS_WIN:
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / 'Documents'
UPDATE_URLS = ['https://raw.githubusercontent.com/SculptWorksLTD/AutoSculpt3D/main/update.json', 'https://raw.githubusercontent.com/SculptWorksLTD/AutoSculpt3DRelease/main/update.json']
DEFAULT_SETTINGS = {'auto_check': True, 'last_check': 0}

def log(*parts):
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + ' '.join((str(p) for p in parts))
    try:
        with open(LOG, 'a', encoding='utf-8') as fh:
            fh.write(line + '\n')
        if LOG.stat().st_size > 5000000:
            LOG.replace(LOG.with_suffix('.old.log'))
    except Exception:
        pass
    print(line, flush=True)

def vtuple(v):
    out = []
    for p in str(v).split('.'):
        num = ''.join((ch for ch in p if ch.isdigit()))
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (4 - len(out)))

def load_settings():
    try:
        d = {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text())}
        d.pop('auto_diary', None)
        return d
    except Exception:
        return dict(DEFAULT_SETTINGS)

def save_settings(s):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))

def ui_dir():
    bundled = RESOURCES / 'ui'
    upd = SUPPORT / 'ui'
    try:
        if (upd / 'AutoSculpt3D.html').exists() and vtuple((upd / 'VERSION').read_text()) > vtuple((bundled / 'VERSION').read_text()):
            return (upd, bundled)
    except Exception:
        pass
    return (bundled, bundled)

def ui_version():
    d, _ = ui_dir()
    try:
        return (d / 'VERSION').read_text().strip()
    except Exception:
        return VERSION

class UserError(RuntimeError):

    def __init__(self, text, **values):
        self.text, self.values = (text, values)
        super().__init__(text.format(**values) if values else text)
JOBS = {}
JOBS_LOCK = threading.Lock()

class Job:

    def __init__(self, kind, title):
        self.id = uuid.uuid4().hex[:12]
        self.kind, self.title = (kind, title)
        self.state, self.progress, self.message, self.values = ('running', 0.0, 'In progress', {})
        self.result, self.error, self.error_values, self.proc, self.cancelled = (None, None, {}, None, False)
        self.log = []
        self.dir = SUPPORT / 'jobs' / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        with JOBS_LOCK:
            JOBS[self.id] = self

    def update(self, progress=None, message=None, **values):
        if progress is not None:
            self.progress = max(0.0, min(1.0, float(progress)))
        if message:
            self.message, self.values = (message, values)
            self.add_log(message.format(**values) if values else message)

    def add_log(self, line):
        self.log.append(line)
        if len(self.log) > 400:
            self.log = self.log[-400:]

    def as_dict(self):
        return {'id': self.id, 'kind': self.kind, 'title': self.title, 'state': self.state, 'progress': round(self.progress, 3), 'message': self.message, 'message_values': self.values, 'error': self.error, 'error_values': self.error_values, 'result': self.result, 'log': self.log[-12:]}

def run_job(job, fn):

    def wrap():
        try:
            fn(job)
            if job.cancelled:
                job.state, job.message, job.values = ('cancelled', 'Cancelled', {})
            elif job.state == 'running':
                job.state = 'done'
        except Exception as exc:
            log('Job failed', job.kind, traceback.format_exc())
            if job.cancelled:
                job.state, job.message, job.values = ('cancelled', 'Cancelled', {})
                return
            text, values = (exc.text, exc.values) if isinstance(exc, UserError) else ('{detail}', {'detail': str(exc)})
            job.state, job.error, job.error_values = ('error', text, values)
            job.message, job.values = (text, values)
    threading.Thread(target=wrap, daemon=True).start()
    return job

def fix_certificates():
    if os.environ.get('SSL_CERT_FILE') or os.name == 'nt':
        return
    try:
        import ssl
        p = ssl.get_default_verify_paths()
        if p.cafile and os.path.exists(p.cafile) or (p.capath and os.path.isdir(p.capath) and os.listdir(p.capath)):
            return
    except Exception:
        return
    for f in ('/etc/ssl/cert.pem', '/private/etc/ssl/cert.pem', '/etc/ssl/certs/ca-certificates.crt'):
        if os.path.exists(f):
            os.environ['SSL_CERT_FILE'] = f
            return
fix_certificates()
_SSL = None

def ssl_context():
    global _SSL
    if _SSL is None:
        import ssl
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except Exception:
            p = ssl.get_default_verify_paths()
            has = p.cafile and os.path.exists(p.cafile) or (p.capath and os.path.isdir(p.capath) and os.listdir(p.capath))
            if not has and (not os.environ.get('SSL_CERT_FILE')):
                for f in ('/etc/ssl/cert.pem', '/private/etc/ssl/cert.pem', '/etc/ssl/certs/ca-certificates.crt'):
                    if os.path.exists(f):
                        ctx.load_verify_locations(f)
                        break
        _SSL = ctx
    return _SSL

def urlopen(req, timeout):
    if isinstance(req, str):
        req = urllib.request.Request(req, headers={'User-Agent': 'AutoSculpt3D/' + VERSION})
    if req.full_url.startswith('https://'):
        return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())
    return urllib.request.urlopen(req, timeout=timeout)

def download(url, dest, job=None, p0=0.0, p1=1.0, label='Downloading', tries=3):
    for attempt in range(tries):
        try:
            return _download(url, dest, job, p0, p1, label)
        except Exception:
            if job and job.cancelled or attempt == tries - 1:
                raise
            if job:
                job.update(None, 'Connection lost, retrying ({n}/{total})', n=attempt + 2, total=tries)
            time.sleep(3 * (attempt + 1))

def _download(url, dest, job=None, p0=0.0, p1=1.0, label='Downloading'):
    req = urllib.request.Request(url, headers={'User-Agent': 'AutoSculpt3D/' + VERSION})
    with urlopen(req, 60) as r, open(dest, 'wb') as out:
        total = int(r.headers.get('Content-Length') or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if job:
                if job.cancelled:
                    raise UserError('Cancelled')
                frac = got / total if total else 0
                if total:
                    job.update(p0 + (p1 - p0) * frac, '{what}: {got} MB of {total} MB', what=label, got=round(got / 1000000.0), total=round(total / 1000000.0))
                else:
                    job.update(p0, '{what}: {got} MB', what=label, got=round(got / 1000000.0))
    return dest

def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()
QUIT = {'dirty': False, 'allow': False, 'win': None}

def studio_app():
    if IS_MAC:
        for p in (Path('/Applications/AutoSculpt Studio.app'), Path.home() / 'Applications/AutoSculpt Studio.app'):
            if p.exists():
                return p
    elif IS_WIN:
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
        d = base / 'Programs' / 'AutoSculpt Studio'
        if (d / 'python' / 'AutoSculptStudio.exe').exists() and (d / 'app' / 'main.py').exists():
            return d
    return None

def open_studio():
    app = studio_app()
    if not app:
        return {'installed': False, 'url': 'https://github.com/SculptWorksLTD/AutoSculptStudio'}
    try:
        if IS_MAC:
            subprocess.Popen(['open', str(app)], env=other_app_env())
        else:
            subprocess.Popen([str(app / 'python' / 'AutoSculptStudio.exe'), str(app / 'app' / 'main.py')], cwd=str(app), env=other_app_env(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return {'installed': True, 'opened': True}
    except Exception as exc:
        log('open studio', exc)
        return {'installed': True, 'opened': False, 'error': str(exc)}

def folder_bytes(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total
DISK_FOLDERS = {'jobs': 'jobs', 'hdri': 'hdri', 'library': 'library', 'engines': 'engines', 'gallery': 'gallery'}

def disk_usage_report():
    items = []
    for key, name in DISK_FOLDERS.items():
        size = folder_bytes(SUPPORT / name) if (SUPPORT / name).exists() else 0
        if size > 0:
            items.append({'id': key, 'kind': key, 'name': key, 'bytes': size})
    old = sum((folder_bytes(d) for d in (SUPPORT / 'updates').glob('*-previous'))) if (SUPPORT / 'updates').exists() else 0
    if old > 0:
        items.append({'id': 'updates', 'kind': 'updates', 'name': 'updates', 'bytes': old})
    return {'items': items, 'total': sum((i['bytes'] for i in items)), 'free': shutil.disk_usage(SUPPORT).free}

def disk_clean(ids):
    freed = 0
    with JOBS_LOCK:
        running = {j.id for j in JOBS.values() if getattr(j, 'state', '') in ('running', 'queued')}
    for i in ids:
        if i == 'jobs':
            for d in (SUPPORT / 'jobs').iterdir() if (SUPPORT / 'jobs').exists() else []:
                if d.name not in running:
                    freed += folder_bytes(d)
                    shutil.rmtree(d, ignore_errors=True)
        elif i in DISK_FOLDERS:
            d = SUPPORT / DISK_FOLDERS[i]
            freed += folder_bytes(d) if d.exists() else 0
            shutil.rmtree(d, ignore_errors=True)
        elif i == 'updates':
            for d in (SUPPORT / 'updates').glob('*-previous') if (SUPPORT / 'updates').exists() else []:
                freed += folder_bytes(d)
                shutil.rmtree(d, ignore_errors=True)
    log('Disk cleaned:', ', '.join(ids), 'freed', freed)
    return {'ok': True, 'freed': freed}
PH_API = 'https://api.polyhaven.com'
_PH_CACHE = {}

def polyhaven(path):
    hit = _PH_CACHE.get(path)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    req = urllib.request.Request(PH_API + path, headers={'User-Agent': f'AutoSculpt3D/{VERSION}'})
    with urlopen(req, 30) as r:
        data = json.loads(r.read())
    _PH_CACHE[path] = (time.time(), data)
    return data

def hdri_list(category):
    data = polyhaven('/assets?type=hdris&categories=' + urllib.parse.quote(category or 'outdoor'))
    items = [{'id': k, 'name': v.get('name', k), 'n': v.get('download_count', 0), 'thumb': v.get('thumbnail_url') or f'https://cdn.polyhaven.com/asset_img/thumbs/{k}.png?width=320&height=160'} for k, v in data.items() if isinstance(v, dict)]
    return sorted(items, key=lambda a: -a['n'])

def hdri_file(hid, res):
    if not re.fullmatch('[a-z0-9_]{1,80}', hid or '') or res not in ('1k', '2k', '4k', 'jpg'):
        raise UserError('Invalid landscape')
    folder = SUPPORT / 'hdri'
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / (f'{hid}_tonemapped.jpg' if res == 'jpg' else f'{hid}_{res}.hdr')
    if f.exists() and f.stat().st_size > 10000:
        return f
    files = polyhaven('/files/' + hid)
    info = files['tonemapped'] if res == 'jpg' else files['hdri'][res]['hdr']
    tmp = f.with_suffix('.part')
    req = urllib.request.Request(info['url'], headers={'User-Agent': f'AutoSculpt3D/{VERSION}'})
    h = hashlib.md5()
    with urlopen(req, 120) as r, open(tmp, 'wb') as out:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            out.write(chunk)
    if info.get('md5') and h.hexdigest() != info['md5']:
        tmp.unlink(missing_ok=True)
        raise UserError('Corrupted file, update blocked: {path}', path=f.name)
    tmp.replace(f)
    return f
MENU_FILE = SUPPORT / 'menu.json'
WEBSTATE_FILE = SUPPORT / 'webstate.json'
WEBSTATE_LOCK = threading.Lock()
PREFERRED_PORTS = range(47815, 47825)

def webstate_read():
    try:
        data = json.loads(WEBSTATE_FILE.read_text(encoding='utf-8'))
        return {k: v for k, v in data.items() if isinstance(k, str) and k.startswith('autosculpt.') and isinstance(v, str)}
    except Exception:
        return {}

def webstate_update(changes):
    with WEBSTATE_LOCK:
        data = webstate_read()
        for k, v in changes.items():
            if not (isinstance(k, str) and k.startswith('autosculpt.') and (len(k) < 100)):
                continue
            if v is None:
                data.pop(k, None)
            elif isinstance(v, str):
                data[k] = v
        if len(json.dumps(data)) > 20000000:
            raise UserError('The saved preferences are too large')
        tmp = WEBSTATE_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, WEBSTATE_FILE)

def webstate_script():
    state = json.dumps(webstate_read(), ensure_ascii=False).replace('</', '<\\/')
    token = json.dumps(TOKEN)
    return '<script>(function(){var S=' + state + ',T=' + token + ";try{for(var k in S){if(localStorage.getItem(k)===null)localStorage.setItem(k,S[k]);}}catch(e){}var q={},t=null;function flush(){var b=JSON.stringify(q);q={};t=null;try{fetch('/api/webstate',{method:'POST',headers:{'X-AS-Token':T,'Content-Type':'application/json'},body:b,keepalive:b.length<60000});}catch(e){}}function note(st,k,v){if(st===window.localStorage&&/^autosculpt\\./.test(k)){q[k]=v;if(!t)t=setTimeout(flush,250);}}var P=Storage.prototype,set=P.setItem,rem=P.removeItem;P.setItem=function(k,v){set.call(this,k,v);note(this,k,String(v));};P.removeItem=function(k){rem.call(this,k);note(this,k,null);};window.addEventListener('pagehide',function(){if(t){clearTimeout(t);flush();}});})();</script>"
NATIVE_MENU = False

def native_menu(bridge):
    try:
        spec = json.loads(MENU_FILE.read_text(encoding='utf-8'))
        from webview.menu import Menu, MenuAction, MenuSeparator
    except Exception:
        return []

    def action(mi, label):
        js = f'window.AS_MENU && window.AS_MENU({int(mi)}, {json.dumps(label)})'

        def run():
            threading.Thread(target=lambda: bridge._win and bridge._win.evaluate_js(js), daemon=True).start()
        return run
    menus = []
    try:
        for mi, m in enumerate(spec.get('menus', [])[:10]):
            items = []
            for it in m.get('items', [])[:60]:
                if it is None:
                    if items and (not isinstance(items[-1], MenuSeparator)):
                        items.append(MenuSeparator())
                elif isinstance(it, dict) and it.get('label'):
                    items.append(MenuAction(str(it['label'])[:100], action(mi, str(it['label']))))
            while items and isinstance(items[-1], MenuSeparator):
                items.pop()
            if items:
                menus.append(Menu(str(m.get('label', ''))[:40], items))
    except Exception:
        log('Native menu not built:', traceback.format_exc())
        return []
    return menus

def product_version():
    return max((VERSION, ui_version()), key=vtuple)
SERVICE_URLS = [u.rsplit('/', 1)[0] + '/service.json' for u in UPDATE_URLS]
REPORT_TEXT_MAX, REPORT_DIARY_MAX, REPORT_CONTEXT_MAX = (8000, 400000, 20000)
_service = {'at': 0.0, 'data': {}}

def service_config(wait=True):
    test = os.environ.get('AUTOSCULPT_REPORT_URL')
    if test:
        return {'report_url': test}
    cache = SUPPORT / 'service.json'
    if time.time() - _service['at'] < 6 * 3600:
        return _service['data']
    data = None
    if wait:
        for url in SERVICE_URLS:
            try:
                req = urllib.request.Request(url + '?t=' + str(int(time.time())), headers={'User-Agent': 'AutoSculpt3D/' + VERSION, 'Cache-Control': 'no-cache'})
                with urlopen(req, 12) as r:
                    got = json.loads(r.read().decode('utf-8'))
                if isinstance(got, dict):
                    data = got
                    break
            except Exception:
                continue
    if data is not None:
        _service.update(at=time.time(), data=data)
        try:
            cache.write_text(json.dumps(data), encoding='utf-8')
        except OSError:
            pass
        return data
    try:
        got = json.loads(cache.read_text(encoding='utf-8'))
        return got if isinstance(got, dict) else {}
    except Exception:
        return {}

def report_url(wait=True):
    url = str(service_config(wait).get('report_url') or '')
    return url if url.startswith('https://') or (os.environ.get('AUTOSCULPT_REPORT_URL') and url.startswith('http://127.0.0.1')) else ''

def anonymize(text):
    out = str(text)
    home = str(Path.home())
    names = set()
    for var in ('USER', 'USERNAME', 'LOGNAME'):
        if os.environ.get(var):
            names.add(os.environ[var])
    try:
        import getpass
        names.add(getpass.getuser())
    except Exception:
        pass
    if len(Path(home).name) >= 3:
        names.add(Path(home).name)
    hosts = {platform.node(), platform.node().split('.')[0], os.environ.get('COMPUTERNAME', '')}
    for h in (home, home.replace('\\', '/'), home.replace('\\', '\\\\')):
        if len(h) > 3:
            out = out.replace(h, '~')
    for n in sorted(names, key=len, reverse=True):
        if len(n) >= 3:
            out = re.sub('(?<![A-Za-z0-9])' + re.escape(n) + '(?![A-Za-z0-9])', '<user>', out)
    for h in sorted(hosts, key=len, reverse=True):
        if len(h) >= 3 and h.lower() not in ('localhost',):
            out = out.replace(h, '<pc>')
    out = re.sub('[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}', '<email>', out)
    out = re.sub('\\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\\b', '<mac>', out)
    out = re.sub('\\b(?!127\\.|0\\.0\\.0\\.0)(?:\\d{1,3}\\.){3}\\d{1,3}\\b', '<ip>', out)
    out = re.sub('(token=)[A-Za-z0-9_\\-]+', '\\1<…>', out)
    return out

def report_diary(which):
    if which not in ('last', 'week'):
        return ''
    try:
        lines = LOG.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return ''
    if which == 'last':
        start = 0
        for i, ln in enumerate(lines):
            if f' {APP_NAME} ' in ln[:60] and ' interface ' in ln:
                start = i
        lines = lines[start:]
    else:
        since = time.strftime('%Y-%m-%d', time.localtime(time.time() - 7 * 86400))
        lines = [ln for ln in lines if ln[:10] >= since or not re.match('\\d{4}-\\d\\d-\\d\\d ', ln)]
    text = anonymize('\n'.join(lines))
    if len(text) > REPORT_DIARY_MAX:
        text = '… (the beginning is left out)\n' + text[-REPORT_DIARY_MAX:]
    return text

def build_report(b):
    kind = 'idea' if b.get('kind') == 'idea' else 'bug'
    text = str(b.get('text') or '').strip()[:REPORT_TEXT_MAX]
    if not text:
        raise UserError('Write a few words before sending')
    diary = report_diary(b.get('diary'))
    context = anonymize(str(b.get('context') or '')[:REPORT_CONTEXT_MAX]).strip()
    if context:
        diary = '--- what was open ---\n' + context + ('\n\n--- diary ---\n' + diary if diary else '')
    system = {'darwin': 'macOS', 'win32': 'Windows'}.get(sys.platform, 'Linux')
    return {'app': APP_NAME, 'kind': kind, 'text': text, 'contact': str(b.get('contact') or '').strip()[:120], 'lang': str(b.get('lang') or '')[:5], 'version': product_version(), 'os': system + ' ' + platform.release()[:12], 'arch': platform.machine()[:12], 'diary': diary}

def send_report(b):
    payload = build_report(b)
    if b.get('preview'):
        return {'payload': payload}
    url = report_url()
    if not url:
        raise UserError('The report service is not available yet')
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), method='POST', headers={'Content-Type': 'application/json', 'User-Agent': 'AutoSculpt3D/' + VERSION})
    try:
        with urlopen(req, 30) as r:
            got = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            detail = str(json.loads(exc.read().decode('utf-8')).get('error') or '')
        except Exception:
            pass
        log('Report not sent:', exc.code, detail)
        if exc.code == 429:
            raise UserError('Too many reports in a short time: try again later')
        raise UserError('The report service is not responding ({detail})', detail=detail or str(exc.code))
    except Exception as exc:
        log('Report not sent:', repr(exc))
        raise UserError('The report service is not responding ({detail})', detail=str(getattr(exc, 'reason', exc)))
    log('Report sent:', payload['kind'], 'reference', got.get('id'))
    return {'id': str(got.get('id') or '')}

def fetch_manifest():
    test = os.environ.get('AUTOSCULPT_UPDATE_URL')
    last = None
    for url in [test] if test else UPDATE_URLS:
        if url.startswith('https://'):
            url += ('&' if '?' in url else '?') + 't=' + str(int(time.time()))
        req = urllib.request.Request(url, headers={'User-Agent': 'AutoSculpt3D/' + VERSION, 'Cache-Control': 'no-cache', 'Pragma': 'no-cache'})
        try:
            with urlopen(req, 20) as r:
                m = json.loads(r.read().decode('utf-8'))
            if isinstance(m, dict) and (m.get('ui') or m.get('app')):
                return m
            last = ValueError('not a manifest')
        except Exception as exc:
            last = exc
            log('Update check failed:', url.split('?')[0], repr(exc))
    raise UserError('The update server is not responding ({detail})', detail=str(getattr(last, 'reason', last)))

def check_updates():
    m = fetch_manifest()
    s = load_settings()
    s['last_check'] = time.time()
    save_settings(s)
    out = {'notes': m.get('notes', ''), 'notes_i18n': m.get('notes_i18n') or {}, 'history': m.get('history') or [], 'latest': m.get('latest'), 'current': product_version(), 'items': []}
    cur = {'ui': ui_version(), 'app': VERSION}
    for comp in ('ui', 'app'):
        c = m.get(comp)
        if c and vtuple(c.get('version')) > vtuple(cur[comp]):
            out['items'].append({'component': comp, 'from': cur[comp], 'to': c['version']})
    out['available'] = bool(out['items'])
    return out

def apply_updates(job):
    m = fetch_manifest()
    info = check_updates()
    todo = info['items']
    if not todo:
        job.update(1, 'No updates')
        job.result = {'restart': False}
        return
    restart = False
    for i, it in enumerate(todo):
        comp = it['component']
        job.update(i / len(todo), 'Updating {what}', what=comp)
        if comp in ('ui', 'app'):
            c = m[comp]
            stage = Path(tempfile.mkdtemp(dir=SUPPORT / 'updates'))
            for f in c['files']:
                rel = f['path']
                if '..' in Path(rel).parts or '\\' in rel or rel.startswith('/') or Path(rel).is_absolute() or (':' in rel):
                    raise UserError('Invalid path in the manifest: {path}', path=rel)
                dest = stage / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                download(f['url'], dest, job, i / len(todo), (i + 1) / len(todo), rel)
                if sha256(dest) != f['sha256'].lower():
                    raise UserError('Corrupted file, update blocked: {path}', path=rel)
            (stage / 'VERSION').write_text(c['version'])
            target = SUPPORT / comp
            backup = SUPPORT / 'updates' / (comp + '-previous')
            shutil.rmtree(backup, ignore_errors=True)
            if target.exists():
                target.rename(backup)
            stage.rename(target)
            restart = True
    job.update(1, 'Update completed')
    job.result = {'restart': restart}

class Handler(BaseHTTPRequestHandler):
    server_version = 'AutoSculpt3D'

    def log_message(self, fmt, *args):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype=None, extra=None):
        data = Path(path).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', ctype or mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b''
        try:
            return json.loads(raw or b'{}')
        except Exception:
            return {}

    def authorized(self):
        return secrets.compare_digest(self.headers.get('X-AS-Token', ''), TOKEN)

    def local_host(self):
        port = self.server.server_address[1]
        return self.headers.get('Host', '') in (f'127.0.0.1:{port}', f'localhost:{port}')

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.local_host():
            return self.send_error(403)
        try:
            if path in ('/', '/index.html'):
                d, _ = ui_dir()
                html = (d / 'AutoSculpt3D.html').read_text(encoding='utf-8')
                inject = f'<script>window.AS_NATIVE={{token:{json.dumps(TOKEN)},version:{json.dumps(VERSION)},ui:{json.dumps(ui_version())},product:{json.dumps(product_version())},menu:{json.dumps(NATIVE_MENU)},platform:{json.dumps(platform.machine())},os:{json.dumps(sys.platform)}}};</script>'
                html = html.replace('<head>', '<head>\n' + webstate_script() + inject, 1)
                body = html.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith('/vendor/'):
                d, bundled = ui_dir()
                rel = Path(urllib.parse.unquote(path[1:]))
                if '..' in rel.parts:
                    return self.send_error(403)
                for base in (d, bundled):
                    f = base / rel
                    if f.is_file():
                        ctype = 'text/javascript' if f.suffix in ('.js', '.mjs') else None
                        return self.send_file(f, ctype)
                return self.send_error(404)
            if not self.authorized():
                return self.send_error(403)
            if path == '/api/open-requests':
                return self.send_json({'paths': take_open_requests()})
            if path == '/api/info':
                return self.send_json({'app': APP_NAME, 'version': VERSION, 'ui': ui_version(), 'product': product_version(), 'machine': platform.machine(), 'macos': platform.mac_ver()[0], 'os': sys.platform, 'support': str(SUPPORT), 'settings': load_settings(), 'free_gb': round(shutil.disk_usage(SUPPORT).free / 2 ** 30)})
            if path.startswith('/api/jobs/'):
                parts = path.split('/')
                job = JOBS.get(parts[3]) if len(parts) > 3 else None
                if not job:
                    return self.send_error(404)
                return self.send_json(job.as_dict())
            if path == '/api/update/check':
                try:
                    return self.send_json(check_updates())
                except UserError as exc:
                    return self.send_json({'available': False, 'error': str(exc), 'error_text': exc.text, 'error_values': exc.values})
                except Exception as exc:
                    return self.send_json({'available': False, 'error': str(exc)})
            if path == '/api/hdri/list':
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                try:
                    return self.send_json(hdri_list((q.get('category') or ['outdoor'])[0]))
                except Exception as exc:
                    log('Landscape list failed:', repr(exc))
                    return self.send_json({'error': str(getattr(exc, 'reason', exc))}, 502)
            if path.startswith('/api/hdri/file/'):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                try:
                    f = hdri_file(path.rsplit('/', 1)[1], (q.get('res') or ['2k'])[0])
                except UserError as exc:
                    return self.send_json({'error': str(exc)}, 400)
                except Exception as exc:
                    log('Landscape download failed:', repr(exc))
                    return self.send_json({'error': str(exc)}, 502)
                return self.send_file(f, 'application/octet-stream')
            if path == '/api/report':
                return self.send_json({'available': bool(report_url())})
            if path == '/api/disk':
                return self.send_json(disk_usage_report())
            if path == '/api/log':
                return self.send_file(LOG, 'text/plain; charset=utf-8') if LOG.exists() else self.send_json({})
            return self.send_error(404)
        except Exception as exc:
            log('GET error', path, traceback.format_exc())
            return self.send_json({'error': str(exc)}, 500)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.local_host() or not self.authorized():
            return self.send_error(403)
        try:
            b = self.body()
            if path == '/api/studio':
                return self.send_json(open_studio())
            if path == '/api/dirty':
                QUIT['dirty'] = bool(b.get('dirty'))
                return self.send_json({'ok': True})
            if path == '/api/quit':
                QUIT['allow'] = True
                w = QUIT.get('win')
                if w:
                    threading.Thread(target=lambda: (time.sleep(0.2), w.destroy()), daemon=True).start()
                return self.send_json({'ok': True})
            if path == '/api/disk/clean':
                return self.send_json(disk_clean([str(x) for x in b.get('ids') or []]))
            if path.startswith('/api/jobs/') and path.endswith('/cancel'):
                job = JOBS.get(path.split('/')[3])
                if job:
                    job.cancelled = True
                    pr = job.proc
                    if pr:
                        pr.terminate()
                        threading.Timer(5.0, lambda: pr.poll() is None and pr.kill()).start()
                return self.send_json({'ok': True})
            if path == '/api/update/apply':
                job = run_job(Job('update', 'Update'), apply_updates)
                return self.send_json(job.as_dict())
            if path == '/api/report':
                try:
                    return self.send_json(send_report(b))
                except UserError as exc:
                    return self.send_json({'error': exc.text, 'values': exc.values}, 502)
            if path == '/api/clientlog':
                log('Page:', str(b.get('text', ''))[:800].replace('\n', ' | '))
                return self.send_json({'ok': True})
            if path == '/api/export':
                try:
                    return self.send_json(export_file(b.get('name', ''), b.get('b64', ''), b.get('path')))
                except Exception as exc:
                    log('Export failed:', exc)
                    return self.send_json({'error': str(exc)}, 400)
            if path == '/api/webstate':
                if not isinstance(b, dict):
                    return self.send_json({'error': 'invalid'}, 400)
                webstate_update(b)
                return self.send_json({'ok': True})
            if path == '/api/menu':
                menus = b.get('menus')
                if not isinstance(menus, list) or len(menus) > 10:
                    return self.send_json({'error': 'invalid menu'}, 400)
                data = json.dumps({'lang': str(b.get('lang', ''))[:10], 'menus': menus}, ensure_ascii=False)
                changed = not MENU_FILE.exists() or MENU_FILE.read_text(encoding='utf-8') != data
                if changed:
                    MENU_FILE.write_text(data, encoding='utf-8')
                return self.send_json({'changed': changed, 'active': NATIVE_MENU})
            if path == '/api/settings':
                s = load_settings()
                for k in ('auto_check',):
                    if k in b:
                        s[k] = b[k]
                save_settings(s)
                return self.send_json(s)
            return self.send_error(404)
        except Exception as exc:
            log('POST error', path, traceback.format_exc())
            return self.send_json({'error': str(exc)}, 500)

def cleanup_jobs(days=2):
    limit = time.time() - days * 86400
    for d in (SUPPORT / 'jobs').iterdir():
        try:
            if d.stat().st_mtime < limit:
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass

def start_server():
    cleanup_jobs()
    srv = None
    for port in list(PREFERRED_PORTS) + [0]:
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
            break
        except OSError:
            continue
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
EXPORT_EXT = {'.as3d', '.dxf', '.zip', '.obj', '.stl', '.png', '.pdf', '.usdz', '.glb', '.mp4', '.webm', '.json', '.txt', '.csv', '.svg', '.html'}

def win_save_dialog(name, directory):
    import ctypes
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [('lStructSize', wintypes.DWORD), ('hwndOwner', wintypes.HWND), ('hInstance', wintypes.HINSTANCE), ('lpstrFilter', wintypes.LPCWSTR), ('lpstrCustomFilter', wintypes.LPWSTR), ('nMaxCustFilter', wintypes.DWORD), ('nFilterIndex', wintypes.DWORD), ('lpstrFile', wintypes.LPWSTR), ('nMaxFile', wintypes.DWORD), ('lpstrFileTitle', wintypes.LPWSTR), ('nMaxFileTitle', wintypes.DWORD), ('lpstrInitialDir', wintypes.LPCWSTR), ('lpstrTitle', wintypes.LPCWSTR), ('Flags', wintypes.DWORD), ('nFileOffset', wintypes.WORD), ('nFileExtension', wintypes.WORD), ('lpstrDefExt', wintypes.LPCWSTR), ('lCustData', wintypes.LPARAM), ('lpfnHook', ctypes.c_void_p), ('lpTemplateName', wintypes.LPCWSTR), ('pvReserved', ctypes.c_void_p), ('dwReserved', wintypes.DWORD), ('FlagsEx', wintypes.DWORD)]
    ext = Path(name).suffix.lstrip('.')
    buf = ctypes.create_unicode_buffer(name, 2048)
    filt = ctypes.create_unicode_buffer(f'*.{ext}\x00*.{ext}\x00*.*\x00*.*\x00\x00' if ext else '*.*\x00*.*\x00\x00')
    user32, comdlg32, ole32 = (ctypes.windll.user32, ctypes.windll.comdlg32, ctypes.windll.ole32)
    user32.GetForegroundWindow.restype = wintypes.HWND
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = user32.GetForegroundWindow()
    ofn.lpstrFilter = ctypes.cast(filt, wintypes.LPCWSTR)
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile = 2048
    ofn.lpstrInitialDir = str(directory)
    if ext:
        ofn.lpstrDefExt = ext
    ofn.Flags = 524288 | 2 | 4 | 2048 | 8
    comdlg32.GetSaveFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
    comdlg32.GetSaveFileNameW.restype = wintypes.BOOL
    com = ole32.CoInitializeEx(None, 2) in (0, 1)
    try:
        if comdlg32.GetSaveFileNameW(ctypes.byref(ofn)):
            return buf.value
        err = comdlg32.CommDlgExtendedError()
        if err:
            raise OSError(f'save dialog error {err:#x}')
        return ''
    finally:
        if com:
            ole32.CoUninitialize()

def export_file(name, b64, path=None):
    name = ''.join((ch for ch in Path(str(name)).name if ch not in '/\\:*?"<>|' and ord(ch) >= 32)).strip() or 'export'
    if Path(name).suffix.lower() not in EXPORT_EXT:
        raise ValueError('file type not allowed')
    data = base64.b64decode(b64)
    fallback = False
    if path:
        path = Path(str(path))
        if not path.is_absolute() or path.suffix.lower() not in EXPORT_EXT:
            raise ValueError('invalid path')
    else:
        chosen = None
        if IS_WIN:
            try:
                chosen = win_save_dialog(name, documents_dir())
                if chosen == '':
                    return {'cancelled': True}
            except Exception:
                log('Save dialog failed:', traceback.format_exc().strip().splitlines()[-1])
                chosen = None
        if chosen:
            path = Path(chosen)
            if not path.suffix and Path(name).suffix:
                path = Path(str(path) + Path(name).suffix)
        else:
            folder = documents_dir() / 'AutoSculpt 3D'
            folder.mkdir(parents=True, exist_ok=True)
            stem, suf, n = (Path(name).stem, Path(name).suffix, 2)
            path = folder / name
            while path.exists():
                path, n = (folder / f'{stem} {n}{suf}', n + 1)
            fallback = True
    path.write_bytes(data)
    log('Saved', path)
    if fallback:
        try:
            if IS_WIN:
                subprocess.Popen(['explorer', '/select,', str(path)], **NO_WINDOW)
            elif IS_MAC:
                subprocess.run(['open', '-R', str(path)])
        except Exception:
            pass
    return {'path': str(path), 'name': path.name, 'fallback': fallback}

class Bridge:

    def __init__(self):
        self._win = None

    def _dialog(self, kind, multiple=False, **kw):
        import webview
        fd = getattr(webview, 'FileDialog', None)
        const = {'save': getattr(fd, 'SAVE', None) if fd else None, 'open': getattr(fd, 'OPEN', None) if fd else None}[kind]
        if const is None:
            const = webview.SAVE_DIALOG if kind == 'save' else webview.OPEN_DIALOG
        r = self._win.create_file_dialog(const, allow_multiple=multiple, **kw) if multiple else self._win.create_file_dialog(const, **kw)
        if multiple:
            return list(r or [])
        if isinstance(r, (list, tuple)):
            r = r[0] if r else None
        return r

    def save_file(self, name, b64, path=None):
        if not path:
            path = self._dialog('save', save_filename=name, directory=str(documents_dir()))
            if not path:
                return None
            if not Path(path).suffix and Path(name).suffix:
                path = str(path) + Path(name).suffix
        Path(path).write_bytes(base64.b64decode(b64))
        log('Saved', path)
        return {'path': path, 'name': Path(path).name}

    def open_file(self, kind='all', label=None):
        exts = {'project': '*.as3d;*.json', 'image': '*.jpg;*.jpeg;*.png;*.heic;*.webp' if IS_MAC else '*.jpg;*.jpeg;*.png;*.webp', 'dxf': '*.dxf;*.svg', 'drawing': '*.dxf;*.svg;*.pdf', 'all': '*.as3d;*.obj;*.stl;*.glb;*.gltf;*.ply;*.json;*.dxf;*.svg'}.get(kind, '*.*')
        default = {'project': 'AutoSculpt 3D projects', 'image': 'Images', 'dxf': 'DXF and SVG drawings', 'drawing': 'DXF, SVG and PDF drawings', 'all': '3D models and projects'}.get(kind, 'Files')
        types = (f'{label or default} ({exts})',)
        path = self._dialog('open', file_types=types, directory=str(Path.home()))
        if not path:
            return None
        return self.read_file(path)

    def read_file(self, path):
        p = Path(path)
        if not p.is_file():
            return None
        data = p.read_bytes()
        if p.suffix.lower() == '.heic' and IS_MAC:
            out = Path(tempfile.mkdtemp(dir=SUPPORT / 'updates')) / (p.stem + '.jpg')
            subprocess.run(['sips', '-s', 'format', 'jpeg', str(p), '--out', str(out)], capture_output=True)
            if out.exists():
                data, p = (out.read_bytes(), out)
        return {'path': str(path), 'name': p.name, 'b64': base64.b64encode(data).decode()}

    def autosave_path(self, name, untitled='Untitled'):
        folder = documents_dir() / 'AutoSculpt 3D'
        folder.mkdir(parents=True, exist_ok=True)
        base = ''.join((ch for ch in str(name) if ch not in '/\\:*?"<>|')).strip().replace('_', ' ') or untitled
        p, n = (folder / (base + '.as3d'), 2)
        while p.exists():
            p, n = (folder / f'{base} {n}.as3d', n + 1)
        return str(p)

    def reveal(self, path):
        if IS_WIN:
            subprocess.Popen(['explorer', '/select,', str(Path(path))], **NO_WINDOW)
        elif IS_MAC:
            subprocess.run(['open', '-R', path])
        else:
            subprocess.run(['xdg-open', str(Path(path).parent)])

    def open_url(self, url):
        if url.startswith('https://'):
            import webbrowser
            webbrowser.open(url)

    def restart(self):
        app = os.environ.get('AUTOSCULPT_APP')
        pid = os.getpid()
        log('Restart requested')
        if IS_WIN:
            env = clean_env({'AUTOSCULPT_WAIT_PID': str(pid)})
            env.pop('AUTOSCULPT_UPDATED', None)
            subprocess.Popen([sys.executable, str(RESOURCES / 'main.py')], env=env, close_fds=True, creationflags=8 | 512)
        elif app:
            fresh = clean_env()
            fresh.pop('AUTOSCULPT_UPDATED', None)
            subprocess.Popen(['/bin/sh', '-c', f'n=0; while kill -0 {pid} 2>/dev/null && [ $n -lt 100 ]; do sleep 0.2; n=$((n+1)); done; sleep 0.5; open "{app}"'], env=fresh, start_new_session=True)

        def close_and_exit():
            time.sleep(0.3)
            threading.Timer(3.0, lambda: os._exit(0)).start()
            try:
                self._win.destroy()
            except Exception:
                log('Closing the window failed:', traceback.format_exc())
            time.sleep(0.5)
            os._exit(0)
        threading.Thread(target=close_and_exit, daemon=True).start()
        return True

def maybe_run_updated_copy():
    upd = SUPPORT / 'app' / 'main.py'
    if os.environ.get('AUTOSCULPT_UPDATED') or BUNDLE_APP == upd.parent or (not upd.exists()):
        return
    try:
        newer = vtuple((SUPPORT / 'app' / 'VERSION').read_text()) > vtuple(VERSION)
    except Exception:
        newer = False
    if newer:
        os.environ['AUTOSCULPT_UPDATED'] = '1'
        os.environ.setdefault('AUTOSCULPT_BUNDLE', str(BUNDLE_APP))
        if IS_WIN:
            subprocess.Popen([sys.executable, str(upd), *sys.argv[1:]], env=os.environ.copy(), close_fds=True)
            sys.exit(0)
        os.execv(sys.executable, [sys.executable, str(upd), *sys.argv[1:]])
OPENABLE = {'.as3d', '.json', '.obj', '.stl', '.glb', '.gltf', '.ply'}
OPEN_DIR = SUPPORT / 'open-requests'
PID_FILE = SUPPORT / 'instance.pid'
_open_lock = threading.Lock()
_open_paths = []

def queue_open(path):
    p = Path(str(path))
    if p.suffix.lower() in OPENABLE and p.is_file():
        with _open_lock:
            _open_paths.append(str(p))
        log('Open request', p)

def take_open_requests():
    with _open_lock:
        out, _open_paths[:] = (list(_open_paths), [])
    try:
        for f in sorted(OPEN_DIR.glob('*.txt')):
            out.append(f.read_text(encoding='utf-8').strip())
            f.unlink(missing_ok=True)
    except Exception:
        pass
    return [p for p in out if p]

def pid_alive(pid):
    try:
        pid = int(pid)
        if pid == os.getpid():
            return False
        if IS_WIN:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(4096, False, pid)
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def hand_over_to_running_instance(paths):
    try:
        if not paths or not PID_FILE.exists() or (not pid_alive(PID_FILE.read_text().strip())):
            return False
        OPEN_DIR.mkdir(parents=True, exist_ok=True)
        for i, p in enumerate(paths):
            (OPEN_DIR / f'{time.time_ns()}-{i}.txt').write_text(p, encoding='utf-8')
        return True
    except Exception:
        return False

def mac_open_documents():
    try:
        import objc
        import webview.platforms.cocoa as wc

        def open_files(self, app, filenames):
            for f in filenames:
                queue_open(str(f))
            try:
                app.replyToOpenOrPrint_(0)
            except Exception:
                pass
        candidates = [getattr(wc.BrowserView, n, None) for n in dir(wc.BrowserView)] + [getattr(wc, n, None) for n in dir(wc)]
        targets = [c for c in candidates if isinstance(c, type) and hasattr(c, 'applicationShouldTerminate_')]
        for cls in set(targets):
            objc.classAddMethods(cls, [objc.selector(open_files, selector=b'application:openFiles:', signature=b'v@:@@')])
        log('Open documents handler:', ', '.join((c.__name__ for c in set(targets))) or 'none')
    except Exception:
        log('Open documents handler not installed:', traceback.format_exc())
MAC_APP_MENU = {'it': ('Informazioni su {app}', 'Servizi', 'Nascondi {app}', 'Nascondi altre', 'Mostra tutte', 'Esci da {app}', ('Taglia', 'Copia', 'Incolla', 'Seleziona tutto')), 'de': ('Über {app}', 'Dienste', '{app} ausblenden', 'Andere ausblenden', 'Alle einblenden', '{app} beenden', ('Ausschneiden', 'Kopieren', 'Einsetzen', 'Alles auswählen')), 'fr': ("À propos d'{app}", 'Services', 'Masquer {app}', 'Masquer les autres', 'Tout afficher', 'Quitter {app}', ('Couper', 'Copier', 'Coller', 'Tout sélectionner')), 'es': ('Acerca de {app}', 'Servicios', 'Ocultar {app}', 'Ocultar otros', 'Mostrar todo', 'Salir de {app}', ('Cortar', 'Copiar', 'Pegar', 'Seleccionar todo')), 'pt': ('Sobre o {app}', 'Serviços', 'Ocultar {app}', 'Ocultar Outros', 'Mostrar Tudo', 'Encerrar {app}', ('Recortar', 'Copiar', 'Colar', 'Selecionar Tudo')), 'ja': ('{app} について', 'サービス', '{app} を非表示', 'ほかを非表示', 'すべてを表示', '{app} を終了', ('カット', 'コピー', 'ペースト', 'すべてを選択')), 'en': ('About {app}', 'Services', 'Hide {app}', 'Hide Others', 'Show All', 'Quit {app}', ('Cut', 'Copy', 'Paste', 'Select All'))}
EDIT_MENU_NAMES = {'Edit', 'Modifica', 'Bearbeiten', 'Édition', 'Edición', 'Editar', '編集'}

def fix_mac_menu():
    try:
        lang = json.loads(MENU_FILE.read_text(encoding='utf-8')).get('lang', 'en') if MENU_FILE.exists() else 'en'
    except Exception:
        lang = 'en'
    t = MAC_APP_MENU.get(lang, MAC_APP_MENU['en'])

    def run():
        try:
            from AppKit import NSApp, NSMenuItem
            main = NSApp.mainMenu()
            if main is None:
                return
            for i in reversed(range(main.numberOfItems())):
                item = main.itemAtIndex_(i)
                sub = item.submenu()
                if sub is None:
                    continue
                actions = [str(sub.itemAtIndex_(k).action() or '') for k in range(sub.numberOfItems())]
                if i > 0 and actions and all((a in ('toggleFullScreen:', '') for a in actions)):
                    main.removeItemAtIndex_(i)
                    continue
                if i == 1 or (i == 0 and 'terminate:' in actions):
                    for k in range(sub.numberOfItems()):
                        it = sub.itemAtIndex_(k)
                        a = str(it.action() or '')
                        new = {'orderFrontStandardAboutPanel:': t[0], 'hide:': t[2], 'hideOtherApplications:': t[3], 'unhideAllApplications:': t[4], 'terminate:': t[5]}.get(a)
                        if new:
                            it.setTitle_(new.format(app=APP_NAME))
                        elif it.submenu() is not None and str(it.title()) == 'Services':
                            it.setTitle_(t[1])
                if {str(item.title() or ''), str(sub.title() or '')} & EDIT_MENU_NAMES and 'copy:' not in actions:
                    sub.addItem_(NSMenuItem.separatorItem())
                    for title, sel, key in zip(t[6], ('cut:', 'copy:', 'paste:', 'selectAll:'), ('x', 'c', 'v', 'a')):
                        sub.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, key))
        except Exception:
            log('Menu bar not adjusted:', traceback.format_exc())
    try:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(run)
    except Exception:
        log('Menu bar not adjusted:', traceback.format_exc())

def wait_previous_instance():
    pid = os.environ.pop('AUTOSCULPT_WAIT_PID', None)
    if not (pid and IS_WIN):
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(1048576, False, int(pid))
        if h:
            k32.WaitForSingleObject(h, 10000)
            k32.CloseHandle(h)
    except Exception:
        time.sleep(2)

def main():
    wait_previous_instance()
    maybe_run_updated_copy()
    ap = argparse.ArgumentParser()
    ap.add_argument('--server-only', action='store_true')
    a, rest = ap.parse_known_args()
    paths = [str(Path(x).resolve()) for x in rest if Path(x).suffix.lower() in OPENABLE and Path(x).is_file()]
    if IS_WIN and (not a.server_only) and hand_over_to_running_instance(paths):
        return
    for x in paths:
        queue_open(x)
    if not a.server_only:
        try:
            PID_FILE.write_text(str(os.getpid()))
        except Exception:
            pass
    srv = start_server()
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    log(APP_NAME, VERSION, 'interface', ui_version(), 'on', url)
    if a.server_only:
        print('URL', url, flush=True)
        print('TOKEN', TOKEN, flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return
    import webview
    try:
        from Foundation import NSBundle
        info = NSBundle.mainBundle().infoDictionary()
        info['CFBundleName'] = APP_NAME
        icon = os.environ.get('AUTOSCULPT_ICON')
        if icon and Path(icon).exists():
            from AppKit import NSApplication, NSImage
            NSApplication.sharedApplication().setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(icon))
    except Exception:
        pass
    if IS_MAC:
        mac_open_documents()
    bridge = Bridge()
    try:
        win = webview.create_window(APP_NAME, url, js_api=bridge, width=1440, height=900, min_size=(1024, 680), text_select=True, maximized=True)
    except TypeError:
        win = webview.create_window(APP_NAME, url, js_api=bridge, width=1440, height=900, min_size=(1024, 680), text_select=True)
    bridge._win = win
    QUIT['win'] = win

    def on_closing():
        if QUIT['dirty'] and (not QUIT['allow']):
            threading.Thread(target=lambda: win.evaluate_js('window.AS_QUIT && window.AS_QUIT()'), daemon=True).start()
            return False
        return True
    try:
        win.events.closing += on_closing
    except Exception as exc:
        log('closing event', exc)
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('SculptWorks.AutoSculpt3D')
        except Exception:
            pass
    global NATIVE_MENU
    menu = native_menu(bridge)
    NATIVE_MENU = bool(menu)
    try:
        webview.start((lambda: [(time.sleep(d), fix_mac_menu()) for d in (1.0, 4.0, 8.0)]) if IS_MAC else None, private_mode=False, storage_path=str(SUPPORT / 'webview'), menu=menu)
    except TypeError:
        NATIVE_MENU = False
        webview.start(private_mode=False, storage_path=str(SUPPORT / 'webview'))
if __name__ == '__main__':
    main()
