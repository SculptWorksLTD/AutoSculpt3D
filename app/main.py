# AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.
import argparse
import base64
import hashlib
import io
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
import zipfile
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
for sub in ('engines', 'jobs', 'logs', 'ui', 'app', 'updates'):
    (SUPPORT / sub).mkdir(parents=True, exist_ok=True)
LOG = SUPPORT / 'logs' / 'autosculpt.log'
SETTINGS_FILE = SUPPORT / 'settings.json'
TOKEN = secrets.token_urlsafe(24)
BASE_PY = os.environ.get('AUTOSCULPT_PYTHON', sys.executable)
if IS_WIN and 'AUTOSCULPT_PYTHON' not in os.environ:
    _console = Path(sys.executable).with_name('python.exe')
    if _console.exists():
        BASE_PY = str(_console)
NO_WINDOW = {'creationflags': 134217728} if IS_WIN else {}

def clean_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONHOME', 'PYTHONPATH', '__PYVENV_LAUNCHER__')}
    env.update(extra or {})
    return env

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
UPDATE_URL = 'https://raw.githubusercontent.com/SculptWorksLTD/AutoSculpt3D/main/update.json'
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
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text())}
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

def stream(job, cmd, env=None, cwd=None, on_line=None):
    log('Running:', ' '.join((str(c) for c in cmd)))
    p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=clean_env({'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8', **(env or {})}), cwd=cwd, encoding='utf-8', errors='replace', **NO_WINDOW)
    job.proc = p
    for raw in p.stdout:
        line = raw.rstrip()
        for tok in ('PROGRESS ', 'IMAGE ', 'RESULT ', 'ERROR '):
            k = line.find(tok)
            if k > 0 and '%|' in line[:k]:
                job.add_log(line[:k].rstrip())
                line = line[k:]
                break
        if not line:
            continue
        job.add_log(line)
        if not line.startswith(('PROGRESS', 'IMAGE')) and (not (line.startswith('  ') and len(line) > 300)):
            log('  |', line[:500])
        if on_line:
            on_line(line)
        if job.cancelled:
            p.terminate()
    p.wait()
    job.proc = None
    if job.cancelled:
        raise UserError('Cancelled')
    return p.returncode

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
        except Exception as exc:
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
RETIRED_ENGINES = {'triposr'}
APPLE_CHIP = IS_MAC and platform.machine() == 'arm64'
_NVIDIA = []

def nvidia_card():
    if not _NVIDIA:
        _NVIDIA.append(IS_WIN and has_nvidia())
    return _NVIDIA[0]

def platform_allows(spec):
    if os.environ.get('AUTOSCULPT_TEST_ANY_PLATFORM') == '1':
        return True
    only, hide = (spec.get('only'), spec.get('hide_on'))
    if only == 'apple_chip' and (not APPLE_CHIP) or (only == 'nvidia' and (not nvidia_card())):
        return False
    if hide == 'apple_chip' and APPLE_CHIP or (hide == 'apple_chip_or_nvidia' and (APPLE_CHIP or nvidia_card())):
        return False
    return True

def remove_retired_engines():
    for eid in RETIRED_ENGINES:
        for d in (SUPPORT / 'engines' / eid,):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                log('Removed the retired engine', eid)
    if any(((SUPPORT / 'engines' / e / 'installed.json').exists() for e in ('fluxlocal', 'fluxcuda'))):
        old = SUPPORT / 'engines' / 'ssd1b'
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
            log('Removed the replaced engine ssd1b')

def engine_specs():
    specs = {}
    for base in (RESOURCES / 'engines', SUPPORT / 'engines'):
        if not base.exists():
            continue
        for d in base.iterdir():
            f = d / 'engine.json'
            if f.exists():
                try:
                    spec = json.loads(f.read_text())
                    if spec.get('id') in RETIRED_ENGINES or not platform_allows(spec):
                        continue
                    spec['_dir'] = str(d)
                    if spec['id'] not in specs or vtuple(spec.get('version')) >= vtuple(specs[spec['id']].get('version')):
                        specs[spec['id']] = spec
                except Exception:
                    log('Invalid engine.json:', f)
    return specs
KEYS_FILE = SUPPORT / 'keys.json'

def load_keys():
    try:
        return json.loads(KEYS_FILE.read_text())
    except Exception:
        return {}

def save_key(eid, key):
    keys = load_keys()
    if key:
        keys[eid] = key
    else:
        keys.pop(eid, None)
    KEYS_FILE.write_text(json.dumps(keys))
    try:
        os.chmod(KEYS_FILE, 384)
    except OSError:
        pass
LIBRARIES = {'sketchfab': {'name': 'Sketchfab', 'key': 'required to download', 'key_url': 'https://sketchfab.com/settings/password', 'key_hint': 'Settings → Password & API → API token'}, 'polypizza': {'name': 'Poly Pizza', 'key': 'required', 'key_url': 'https://poly.pizza/settings/api', 'key_hint': 'free account → Settings → API'}, 'thingiverse': {'name': 'Thingiverse', 'key': 'required', 'key_url': 'https://www.thingiverse.com/apps/create', 'key_hint': 'create a free app (Desktop app) and copy its App Token'}, 'smithsonian': {'name': 'Smithsonian 3D', 'key': 'optional', 'key_url': 'https://api.data.gov/signup/', 'key_hint': 'free api.data.gov key (without it there is a shared limit of a few searches per hour)'}}
FREE_LICENSES = ('cc0', 'public domain', 'cc attribution', 'cc-by', 'cc by', 'creative commons - attribution', 'creative commons - public domain')
NON_COMMERCIAL = ('noncommercial', 'non-commercial', 'non commercial', '-nc', ' nc', 'noderivatives', 'noderivs', 'no derivatives', '-nd', 'gnu', 'gpl', 'lgpl', 'bsd')

def lib_key(src):
    return load_keys().get('lib:' + src, '')

def lib_get(url, headers=None, binary=False, timeout=40):
    req = urllib.request.Request(url, headers={'User-Agent': f'AutoSculpt3D/{VERSION}', **(headers or {})})
    try:
        with urlopen(req, timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise UserError('The key for {name} is not valid: check it in Tools → Accounts and keys', name=LIBRARIES.get(_lib_now[0], {}).get('name', ''))
        if e.code == 429:
            raise UserError('Too many requests to {name}: wait a minute and try again', name=LIBRARIES.get(_lib_now[0], {}).get('name', ''))
        raise UserError('{name} replied {code}', name=LIBRARIES.get(_lib_now[0], {}).get('name', ''), code=e.code)
    except UserError:
        raise
    except Exception as e:
        raise UserError('Cannot reach {name}: {error}', name=LIBRARIES.get(_lib_now[0], {}).get('name', ''), error=str(getattr(e, 'reason', e)))
    return data if binary else json.loads(data)
_lib_now = ['']

def commercial_ok(lic):
    l = (lic or '').lower()
    return any((f in l for f in FREE_LICENSES)) and (not any((n in l for n in NON_COMMERCIAL)))

def library_search(src, q):
    _lib_now[0] = src
    key, qq = (lib_key(src), urllib.parse.quote(q))
    out = []
    if src == 'polypizza':
        if not key:
            raise UserError('{name} needs a free key: add it in Tools → Accounts and keys', name='Poly Pizza')
        j = lib_get(f'https://api.poly.pizza/v1.1/search/{qq}?limit=32', {'X-Auth-Token': key})
        for m in j.get('results', []):
            lic = m.get('Licence') or m.get('License') or ''
            if not commercial_ok(lic):
                continue
            creator = m.get('Creator') or {}
            out.append({'src': src, 'uid': m.get('ID'), 'name': m.get('Title'), 'author': creator.get('Username') if isinstance(creator, dict) else str(creator), 'license': lic, 'thumb': m.get('Thumbnail'), 'faces': m.get('TriangleCount') or m.get('Tri Count'), 'url': f"https://poly.pizza/m/{m.get('ID')}"})
    elif src == 'thingiverse':
        if not key:
            raise UserError('{name} needs a free key: add it in Tools → Accounts and keys', name='Thingiverse')
        h = {'Authorization': 'Bearer ' + key}
        j = lib_get(f'https://api.thingiverse.com/search/{qq}/?type=things&per_page=30&sort=relevant', h)
        hits = j.get('hits', j) if isinstance(j, dict) else j
        for t in (hits or [])[:30]:
            lic = t.get('license') or ''
            if not lic:
                try:
                    lic = lib_get(f"https://api.thingiverse.com/things/{t['id']}", h, timeout=20).get('license', '')
                except UserError:
                    continue
            if not commercial_ok(lic):
                continue
            cr = t.get('creator') or {}
            out.append({'src': src, 'uid': str(t.get('id')), 'name': t.get('name'), 'author': cr.get('name') or cr.get('first_name') or '', 'license': lic, 'thumb': t.get('preview_image') or t.get('thumbnail'), 'url': t.get('public_url') or f"https://www.thingiverse.com/thing:{t.get('id')}"})
    elif src == 'smithsonian':
        j = lib_get(f"https://3d-api.si.edu/api/v1.0/content/file/search?q={qq}&file_type=glb&rows=100&api_key={urllib.parse.quote(key or 'DEMO_KEY')}")
        best, rank = ({}, {'Medium': 0, 'Low': 1, 'High': 2, 'Thumb': 3})
        for r in j.get('rows', []):
            c = r.get('content') or {}
            pk = c.get('model_url') or c.get('uri')
            if c.get('quality') not in rank or not c.get('uri'):
                continue
            if pk not in best or rank[c['quality']] < rank[best[pk]['content']['quality']]:
                best[pk] = r
        for pk, r in best.items():
            out.append({'src': src, 'uid': r['content']['uri'], 'name': r.get('title') or 'Smithsonian', 'author': 'Smithsonian Institution', 'license': 'CC0', 'thumb': '', 'url': 'https://3d.si.edu/explorer?q=' + qq})
    else:
        raise UserError('Unknown library')
    return out

def library_file(src, uid):
    _lib_now[0] = src
    cache = SUPPORT / 'library'
    cache.mkdir(exist_ok=True)
    safe = re.sub('[^A-Za-z0-9_-]', '_', uid)[-80:]
    for ext, ctype in (('.glb', 'model/gltf-binary'), ('.zip', 'application/zip'), ('.stl', 'model/stl'), ('.obj', 'text/plain')):
        f = cache / f'{src}_{safe}{ext}'
        if f.exists() and f.stat().st_size > 0:
            return (f, ctype)
    key = lib_key(src)
    if src == 'sketchfab':
        if not key:
            raise UserError('{name} needs a free key: add it in Tools → Accounts and keys', name='Sketchfab')
        j = lib_get(f'https://api.sketchfab.com/v3/models/{urllib.parse.quote(uid)}/download', {'Authorization': 'Token ' + key})
        if j.get('glb', {}).get('url'):
            data, ext, ctype = (lib_get(j['glb']['url'], binary=True, timeout=180), '.glb', 'model/gltf-binary')
        elif j.get('gltf', {}).get('url'):
            data, ext, ctype = (lib_get(j['gltf']['url'], binary=True, timeout=180), '.zip', 'application/zip')
        else:
            raise UserError('unexpected download format')
    elif src == 'polypizza':
        j = lib_get(f'https://api.poly.pizza/v1.1/model/{urllib.parse.quote(uid)}', {'X-Auth-Token': key})
        url = j.get('Download') or (j.get('results') or [{}])[0].get('Download')
        if not url:
            raise UserError('unexpected download format')
        data, ext, ctype = (lib_get(url, binary=True, timeout=180), '.glb', 'model/gltf-binary')
    elif src == 'thingiverse':
        h = {'Authorization': 'Bearer ' + key}
        files = lib_get(f'https://api.thingiverse.com/things/{urllib.parse.quote(uid)}/files', h)
        models = [f for f in files if str(f.get('name', '')).lower().endswith(('.stl', '.obj'))]
        if not models:
            raise UserError('This object has no STL or OBJ file')
        f0 = max(models, key=lambda f: f.get('size') or 0)
        data = lib_get(f0.get('download_url') or f"https://api.thingiverse.com/files/{f0['id']}/download", h, binary=True, timeout=180)
        ext = '.obj' if f0['name'].lower().endswith('.obj') else '.stl'
        ctype = 'text/plain' if ext == '.obj' else 'model/stl'
    elif src == 'smithsonian':
        if not uid.startswith('https://3d-api.si.edu/'):
            raise UserError('Unknown library')
        data, ext, ctype = (lib_get(uid, binary=True, timeout=180), '.glb', 'model/gltf-binary')
    else:
        raise UserError('Unknown library')
    f = cache / f'{src}_{safe}{ext}'
    f.write_bytes(data)
    return (f, ctype)

def engine_home(eid):
    return SUPPORT / 'engines' / eid

def engine_status(spec):
    if spec.get('type') == 'cloud':
        key = load_keys().get(spec.get('key_engine', spec['id']), '')
        return {'id': spec['id'], 'name': spec['name'], 'type': 'cloud', 'kind': spec.get('kind', 'photo3d'), 'key_engine': spec.get('key_engine'), 'version': spec.get('version'), 'order': spec.get('order', 50), 'description': spec.get('description'), 'license': spec.get('license'), 'price': spec.get('price'), 'minutes': spec.get('minutes'), 'key_url': spec.get('key_url'), 'key_hint': spec.get('key_hint'), 'max_images': spec.get('max_images', 1), 'options': spec.get('options', []), 'installed': bool(key), 'key_set': bool(key), 'key_end': key[-4:] if len(key) > 8 else '', 'update': False}
    home = engine_home(spec['id'])
    info = {}
    try:
        info = json.loads((home / 'installed.json').read_text())
    except Exception:
        pass
    return {'id': spec['id'], 'name': spec['name'], 'version': spec.get('version'), 'description': spec.get('description'), 'license': spec.get('license'), 'download_gb': spec.get('download_gb'), 'minutes': spec.get('minutes'), 'options': spec.get('options', []), 'installed': bool(info), 'installed_version': info.get('version'), 'type': 'local', 'kind': spec.get('kind', 'photo3d'), 'order': spec.get('order', 50), 'max_images': 1, 'update': bool(info) and vtuple(spec.get('install_version', spec.get('version'))) > vtuple(info.get('version'))}

def copy_runner(spec, home):
    src, dst = (Path(spec['_dir']) / spec['runner'], home / spec['runner'])
    if src.resolve() != dst.resolve() and (not dst.exists() or src.read_bytes() != dst.read_bytes()):
        shutil.copy2(src, dst)
    return dst

def engine_env(home):
    return {'PYTORCH_ENABLE_MPS_FALLBACK': '1', 'HF_HOME': str(home / 'hf'), 'U2NET_HOME': str(home / 'u2net'), 'HF_HUB_DISABLE_TELEMETRY': '1'}

def total_ram_gb():
    try:
        if IS_WIN:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong), ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong), ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong), ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong), ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / 2 ** 30)
        return round(os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2 ** 30)
    except Exception:
        return 0

def has_nvidia():
    try:
        return subprocess.run(['nvidia-smi', '-L'], capture_output=True, timeout=10, **NO_WINDOW).returncode == 0
    except Exception:
        return False

def venv_python(home):
    return home / 'venv' / ('Scripts/python.exe' if IS_WIN else 'bin/python')

def install_engine(job, spec):
    eid, home = (spec['id'], engine_home(spec['id']))
    home.mkdir(parents=True, exist_ok=True)
    py = venv_python(home)
    steps = spec.get('steps', [])
    n = len(steps) + 3
    k = 0

    def at(i, msg, **values):
        job.update(i / n, msg, **values)

    def watch_weights(i):
        total = float(spec.get('download_gb') or 0)
        stop = threading.Event()

        def run():
            while not stop.wait(3):
                got = 0
                for root, _, files in os.walk(home / 'weights'):
                    for f in files:
                        try:
                            got += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
                gb = got / 2 ** 30
                if total > 0:
                    job.progress = max(job.progress, min((i + min(gb / total, 0.99)) / n, 1.0))
                job.message, job.values = ('Downloading the engine files ({got} of {gb} GB)', {'got': f'{gb:.1f}', 'gb': spec.get('download_gb', '?')})
        threading.Thread(target=run, daemon=True).start()
        return stop
    at(k, "Preparing the engine's Python environment")
    if not py.exists():
        if stream(job, [BASE_PY, '-m', 'venv', home / 'venv']) != 0:
            raise UserError('Cannot create the Python environment')
    stream(job, [py, '-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel', '--disable-pip-version-check'])
    cons = home / 'constraints.txt'
    cons.write_text('\n'.join(spec.get('constraints', [])) + '\n')
    k += 1
    for st in steps:
        at(k, 'Installing {what}', what=st.get('label', 'libraries'))
        options = st.get('alternatives') or [st.get('pkgs', [])]
        if IS_WIN and st.get('win_nvidia') and has_nvidia():
            options = [[*pk, *st['win_nvidia']] for pk in options] + options
        ok = False
        for pk in options:
            rc = stream(job, [py, '-m', 'pip', 'install', '--disable-pip-version-check', '--prefer-binary', '-c', cons, *pk], on_line=lambda l: job.update(None, None))
            if rc == 0:
                ok = True
                break
            job.add_log('Variant failed, trying the next one')
        if not ok:
            raise UserError('Installation failed: {what}', what=st.get('label', ''))
        k += 1
    at(k, 'Downloading the engine code')
    src = spec.get('source')
    if src:
        tmp = home / 'source.zip'
        download(src['url'], tmp, job, k / n, (k + 0.5) / n, 'Engine code')
        dest = home / 'src'
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir()
        with zipfile.ZipFile(tmp) as z:
            for name in z.namelist():
                parts = name.split('/', 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                rel = parts[1]
                if src.get('subdir') and (not (rel == src['subdir'] or rel.startswith(src['subdir'] + '/'))):
                    continue
                target = dest / rel
                if name.endswith('/'):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(name) as fi, open(target, 'wb') as fo:
                        shutil.copyfileobj(fi, fo)
        tmp.unlink(missing_ok=True)
    k += 1
    w = spec.get('weights')
    for ws in spec.get('weights_sets', []):
        at(k, 'Downloading the model weights ({gb} GB in total)', gb=spec.get('download_gb', '?'))
        dest = home / 'weights' / ws.get('subdir', '')
        code = "import sys, time\nfrom huggingface_hub import hf_hub_download\nrepo, dest = sys.argv[1], sys.argv[2]\nfor i, f in enumerate(sys.argv[3:]):\n    print('FILE', i, f, flush=True)\n    for t in range(4):\n        try:\n            hf_hub_download(repo, f, local_dir=dest); break\n        except Exception as e:\n            print('RETRY', t + 1, e, flush=True)\n            if t == 3: raise\n            time.sleep(5 * (t + 1))\n"
        stop = watch_weights(k)
        try:
            rc = stream(job, [py, '-c', code, ws['hf_repo'], dest, *ws['files']], env={'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_ENABLE_HF_TRANSFER': '0'}, on_line=lambda l: job.add_log('Downloading ' + l.split(' ', 2)[-1]) if l.startswith('FILE') else None)
        finally:
            stop.set()
        if rc != 0:
            tail = ' / '.join(job.log[-3:])
            raise UserError('Downloading the weights failed: {detail}', detail=tail or '-')
    if w:
        at(k, 'Downloading the model weights ({gb} GB in total)', gb=spec.get('download_gb', '?'))
        code = "import sys\nfrom huggingface_hub import hf_hub_download\nrepo, dest = sys.argv[1], sys.argv[2]\nimport time\nfor i, f in enumerate(sys.argv[3:]):\n    print('FILE', i, f, flush=True)\n    for t in range(4):\n        try:\n            hf_hub_download(repo, f, local_dir=dest); break\n        except Exception as e:\n            print('RETRY', t + 1, e, flush=True)\n            if t == 3: raise\n            time.sleep(5 * (t + 1))\n"
        stop = watch_weights(k)
        try:
            rc = stream(job, [py, '-c', code, w['hf_repo'], home / 'weights', *w['files']], env={'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_ENABLE_HF_TRANSFER': '0'}, on_line=lambda l: job.add_log('Downloading ' + l.split(' ', 2)[-1]) if l.startswith('FILE') else None)
        finally:
            stop.set()
        if rc != 0:
            tail = ' / '.join(job.log[-3:])
            raise UserError('Downloading the weights failed: {detail}', detail=tail or '-')
    pre = spec.get('prefetch_hf', [])
    if pre:
        job.update(None, 'Downloading the helper models')
        code = "import sys\nfrom huggingface_hub import snapshot_download\nfor r in sys.argv[1:]:\n    print('MODEL', r, flush=True)\n    snapshot_download(r, allow_patterns=['*.json', '*.txt'])\n"
        if stream(job, [py, '-c', code, *pre], env=engine_env(home)) != 0:
            job.add_log('Helper models not downloaded now: they will be downloaded with the first photo')
    k += 1
    copy_runner(spec, home)
    (home / 'installed.json').write_text(json.dumps({'version': spec.get('version'), 'when': time.time()}))
    job.update(1, 'Engine installed')
    job.result = {'engine': eid}

def run_text2image(job, spec, prompt, lang, count, options, reference=None):
    out = job.dir / 'images'
    pf = job.dir / 'prompt.txt'
    pf.write_text(prompt, encoding='utf-8')
    if spec.get('type') == 'cloud':
        key = load_keys().get(spec.get('key_engine', spec['id']))
        if not key:
            raise UserError('No API key for {name}: add it in Tools → Photo → 3D engines', name=spec['name'])
        py, env, runner = (BASE_PY, {'AUTOSCULPT_API_KEY': key}, Path(spec['_dir']) / spec['runner'])
        cmd = [py, runner]
    else:
        home = engine_home(spec['id'])
        py = venv_python(home)
        if not py.exists() or not (home / 'installed.json').exists():
            raise UserError('Engine not installed')
        runner, env = (copy_runner(spec, home), engine_env(home))
        cmd = [py, runner, '--weights', home / 'weights', '--src', home / 'src']
    cmd += ['--prompt-file', pf, '--lang', lang or 'en', '--output-dir', out, '--count', str(count), '--work', job.dir]
    if reference is not None:
        ref = job.dir / 'reference.png'
        ref.write_bytes(reference)
        cmd += ['--image', ref]
    for o in spec.get('options', []):
        if o['id'] in options:
            cmd += ['--' + o['id'], str(options[o['id']])]
    errors, images = ([], [])

    def on_line(line):
        if line.startswith('IMAGE '):
            images.append(Path(line[6:].strip()).name)
            job.result = {'images': [f'/api/jobs/{job.id}/image/{n}' for n in images], 'partial': True}
        else:
            parse_runner_line(job, line, errors)
    rc = stream(job, cmd, env=env, on_line=on_line)
    if job.cancelled:
        raise UserError('Cancelled')
    found = sorted((p.name for p in out.glob('image*.png'))) if out.exists() else []
    if rc != 0 or not found:
        if errors and errors[-1][1]:
            raise UserError(errors[-1][0], **errors[-1][1])
        raise UserError('The engine stopped: {detail}', detail=errors[-1][0] if errors else '-')
    job.result = {'images': [f'/api/jobs/{job.id}/image/{n}' for n in found]}

def run_speech2text(job, spec, wav, lang):
    out = job.dir / 'text.txt'
    if spec.get('type') == 'cloud':
        key = load_keys().get(spec.get('key_engine', spec['id']))
        if not key:
            raise UserError('No API key for {name}: add it in Tools → Photo → 3D engines', name=spec['name'])
        cmd, env = ([BASE_PY, Path(spec['_dir']) / spec['runner']], {'AUTOSCULPT_API_KEY': key})
    else:
        home = engine_home(spec['id'])
        py = venv_python(home)
        if not py.exists() or not (home / 'installed.json').exists():
            raise UserError('Engine not installed')
        cmd, env = ([py, copy_runner(spec, home), '--weights', home / 'weights', '--src', home / 'src'], engine_env(home))
    cmd += ['--audio', wav, '--lang', lang or '', '--output', out, '--work', job.dir]
    errors = []
    rc = stream(job, cmd, env=env, on_line=lambda l: parse_runner_line(job, l, errors))
    if job.cancelled:
        raise UserError('Cancelled')
    if rc != 0 or not out.exists():
        if errors:
            raise UserError(errors[-1][0], **errors[-1][1])
        raise UserError('The engine stopped: {detail}', detail='-')
    job.result = {'text': out.read_text(encoding='utf-8').strip()}

def microphone_allowed():
    if not IS_MAC:
        return True
    import plistlib
    for p in Path(sys.executable).resolve().parents:
        if p.name == 'Contents' and (p / 'Info.plist').exists():
            try:
                return 'NSMicrophoneUsageDescription' in plistlib.loads((p / 'Info.plist').read_bytes())
            except Exception:
                return False
    return False

def parse_runner_line(job, line, errors):
    if line.startswith('PROGRESS '):
        parts = line.split(' ', 2)
        text, values = (parts[2] if len(parts) > 2 else '', {})
        if '\t' in text:
            text, raw = text.split('\t', 1)
            try:
                values = json.loads(raw)
            except ValueError:
                values = {}
        try:
            job.update(float(parts[1]), text or None, **values)
        except (ValueError, KeyError, IndexError):
            pass
    elif line.startswith('ERROR '):
        text, values = (line[6:], {})
        if '\t' in text:
            text, raw = text.split('\t', 1)
            try:
                values = json.loads(raw)
            except ValueError:
                values = {}
        errors.append((text, values))

def run_engine(job, spec, images, options):
    if spec.get('type') == 'cloud':
        key = load_keys().get(spec.get('key_engine', spec['id']))
        if not key:
            raise UserError('No API key for {name}: add it in Tools → Photo → 3D engines', name=spec['name'])
        py, env = (BASE_PY, {'AUTOSCULPT_API_KEY': key})
        runner = Path(spec['_dir']) / spec['runner']
        out = job.dir / 'result.glb'
        cmd = [py, runner, '--output', out, '--work', job.dir]
        for img in images[:spec.get('max_images', 1)]:
            cmd += ['--input', img]
    else:
        home = engine_home(spec['id'])
        py = venv_python(home)
        if not py.exists() or not (home / 'installed.json').exists():
            raise UserError('Engine not installed')
        runner = copy_runner(spec, home)
        env = engine_env(home)
        out = job.dir / 'result.obj'
        cmd = [py, runner, '--input', images[0], '--output', out, '--src', home / 'src', '--weights', home / 'weights', '--work', job.dir]
    for o in spec.get('options', []):
        if o['id'] in options:
            v = options[o['id']]
            cmd += ['--' + o['id'], ('1' if v else '0') if o['type'] == 'bool' else str(v)]
    errors = []

    def on_line(line):
        if line.startswith('PROGRESS '):
            parts = line.split(' ', 2)
            text, values = (parts[2] if len(parts) > 2 else '', {})
            if '\t' in text:
                text, raw = text.split('\t', 1)
                try:
                    values = json.loads(raw)
                except ValueError:
                    values = {}
            try:
                job.update(float(parts[1]), text or None, **values)
            except (ValueError, KeyError, IndexError):
                pass
        elif line.startswith('ERROR '):
            text, values = (line[6:], {})
            if '\t' in text:
                text, raw = text.split('\t', 1)
                try:
                    values = json.loads(raw)
                except ValueError:
                    values = {}
            errors.append((text, values))
    rc = stream(job, cmd, env=env, on_line=on_line)
    if job.cancelled:
        raise UserError('Cancelled')
    if rc != 0 or not out.exists():
        if errors and errors[-1][1]:
            raise UserError(errors[-1][0], **errors[-1][1])
        tail = [l for l in job.log[-6:] if not l.startswith(('PROGRESS', 'ERROR'))]
        detail = (errors[-1][0] if errors else '-') + (' | ' + ' / '.join(tail[-3:]) if tail else '')
        raise UserError('The engine stopped: {detail}', detail=detail)
    job.result = {'url': f'/api/jobs/{job.id}/result', 'name': out.name}
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
    if not re.fullmatch('[a-z0-9_]{1,80}', hid or '') or res not in ('1k', '2k', '4k'):
        raise UserError('Invalid landscape')
    folder = SUPPORT / 'hdri'
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f'{hid}_{res}.hdr'
    if f.exists() and f.stat().st_size > 10000:
        return f
    info = polyhaven('/files/' + hid)['hdri'][res]['hdr']
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
            threading.Thread(target=lambda: bridge.window and bridge.window.evaluate_js(js), daemon=True).start()
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

def fetch_manifest():
    url = os.environ.get('AUTOSCULPT_UPDATE_URL', UPDATE_URL)
    if url.startswith('https://'):
        url += ('&' if '?' in url else '?') + 't=' + str(int(time.time()))
    req = urllib.request.Request(url, headers={'User-Agent': 'AutoSculpt3D/' + VERSION, 'Cache-Control': 'no-cache', 'Pragma': 'no-cache'})
    try:
        with urlopen(req, 20) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as exc:
        log('Update check failed:', repr(exc))
        raise UserError('The update server is not responding ({detail})', detail=str(getattr(exc, 'reason', exc)))

def check_updates():
    m = fetch_manifest()
    s = load_settings()
    s['last_check'] = time.time()
    save_settings(s)
    out = {'notes': m.get('notes', ''), 'latest': m.get('latest'), 'current': product_version(), 'items': []}
    cur = {'ui': ui_version(), 'app': VERSION}
    for comp in ('ui', 'app'):
        c = m.get(comp)
        if c and vtuple(c.get('version')) > vtuple(cur[comp]):
            out['items'].append({'component': comp, 'from': cur[comp], 'to': c['version']})
    specs = engine_specs()
    for e in m.get('engines', []):
        old = specs.get(e['id'])
        if not old or vtuple(e.get('version')) > vtuple(old.get('version')):
            out['items'].append({'component': 'engine:' + e['id'], 'from': old.get('version') if old else None, 'to': e.get('version'), 'name': e.get('name', e['id'])})
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
                if '..' in Path(rel).parts or rel.startswith('/'):
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
        elif comp.startswith('engine:'):
            e = next((x for x in m['engines'] if x['id'] == comp.split(':', 1)[1]))
            d = SUPPORT / 'engines' / e['id']
            d.mkdir(parents=True, exist_ok=True)
            for f in e['files']:
                rel = f['path']
                if '..' in Path(rel).parts or rel.startswith('/') or '/' in rel:
                    raise UserError('Invalid path in the manifest: {path}', path=rel)
                tmp = d / (rel + '.new')
                download(f['url'], tmp, job, i / len(todo), (i + 1) / len(todo), rel)
                if sha256(tmp) != f['sha256'].lower():
                    tmp.unlink(missing_ok=True)
                    raise UserError('Corrupted file, update blocked: {path}', path=rel)
                tmp.replace(d / rel)
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
                return self.send_json({'app': APP_NAME, 'version': VERSION, 'ui': ui_version(), 'product': product_version(), 'machine': platform.machine(), 'macos': platform.mac_ver()[0], 'os': sys.platform, 'support': str(SUPPORT), 'settings': load_settings(), 'ram_gb': total_ram_gb(), 'nvidia': IS_WIN and has_nvidia(), 'apple_chip': sys.platform == 'darwin' and platform.machine() == 'arm64', 'free_gb': round(shutil.disk_usage(SUPPORT).free / 2 ** 30), 'microphone': microphone_allowed()})
            if path == '/api/engines':
                allk = 'kind=all' in (urllib.parse.urlparse(self.path).query or '')
                return self.send_json(sorted((engine_status(s) for s in engine_specs().values() if allk or s.get('kind', 'photo3d') == 'photo3d'), key=lambda e: (e['order'], e['name'])))
            if path.startswith('/api/jobs/'):
                parts = path.split('/')
                job = JOBS.get(parts[3]) if len(parts) > 3 else None
                if not job:
                    return self.send_error(404)
                if len(parts) > 5 and parts[4] == 'image' and re.fullmatch('image\\d{1,2}\\.png', parts[5]):
                    f = job.dir / 'images' / parts[5]
                    return self.send_file(f, 'image/png') if f.exists() else self.send_error(404)
                if len(parts) > 4 and parts[4] == 'result':
                    for f, ctype in ((job.dir / 'result.glb', 'model/gltf-binary'), (job.dir / 'result.obj', 'text/plain')):
                        if f.exists():
                            return self.send_file(f, ctype)
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
            if path == '/api/library/sources':
                keys = load_keys()
                return self.send_json([{'id': k, **v, 'key_set': bool(keys.get('lib:' + k)), 'key_end': keys.get('lib:' + k, '')[-4:]} for k, v in LIBRARIES.items()])
            if path in ('/api/library/search', '/api/library/file'):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                src = (q.get('source') or [''])[0]
                try:
                    if path.endswith('search'):
                        return self.send_json(library_search(src, (q.get('q') or [''])[0][:120]))
                    f, ctype = library_file(src, (q.get('id') or [''])[0][:300])
                    return self.send_file(f, ctype, {'X-AS-Name': f.name})
                except UserError as exc:
                    return self.send_json({'error': exc.text, 'error_values': exc.values}, 502)
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
            if path.startswith('/api/engines/') and path.endswith('/install'):
                spec = engine_specs().get(path.split('/')[3])
                if not spec:
                    return self.send_json({'error': 'unknown engine'}, 404)
                job = run_job(Job('install', 'Installation ' + spec['name']), lambda j: install_engine(j, spec))
                return self.send_json(job.as_dict())
            if path.startswith('/api/engines/') and path.endswith('/remove'):
                eid = path.split('/')[3]
                if eid not in engine_specs():
                    return self.send_json({'error': 'unknown engine'}, 404)
                if engine_specs()[eid].get('type') == 'cloud':
                    save_key(eid, '')
                    return self.send_json({'ok': True})
                shutil.rmtree(engine_home(eid) / 'venv', ignore_errors=True)
                shutil.rmtree(engine_home(eid) / 'weights', ignore_errors=True)
                shutil.rmtree(engine_home(eid) / 'src', ignore_errors=True)
                (engine_home(eid) / 'installed.json').unlink(missing_ok=True)
                return self.send_json({'ok': True})
            if path == '/api/photo3d':
                spec = engine_specs().get(b.get('engine') or 'triposg')
                if not spec:
                    return self.send_json({'error': 'unknown engine'}, 404)
                photos = b.get('images') or [b.get('image', '')]
                job = Job('photo3d', 'Photo to 3D')
                imgs = []
                try:
                    for i, data in enumerate(photos[:8]):
                        head, _, data = data.rpartition(',')
                        ext = '.png' if 'png' in head else '.webp' if 'webp' in head else '.jpg'
                        img = job.dir / f'photo{i + 1}{ext}'
                        img.write_bytes(base64.b64decode(data))
                        imgs.append(img)
                except Exception:
                    return self.send_json({'error': 'invalid image'}, 400)
                if not imgs:
                    return self.send_json({'error': 'invalid image'}, 400)
                run_job(job, lambda j: run_engine(j, spec, imgs, b.get('options') or {}))
                return self.send_json(job.as_dict())
            if path == '/api/text2image':
                spec = engine_specs().get(b.get('engine') or '')
                if not spec or spec.get('kind') != 'text2image':
                    return self.send_json({'error': 'unknown engine'}, 404)
                prompt = str(b.get('prompt') or '').strip()[:600]
                if not prompt:
                    return self.send_json({'error': 'Write a description of the subject'}, 400)
                job = Job('text2image', 'Description to pictures')
                run_job(job, lambda j: run_text2image(j, spec, prompt, str(b.get('lang') or 'en')[:5], max(1, min(int(b.get('count') or 4), 6)), b.get('options') or {}))
                return self.send_json(job.as_dict())
            if path == '/api/multiview':
                spec = engine_specs().get(b.get('engine') or '')
                if not spec or spec.get('kind') != 'multiview':
                    return self.send_json({'error': 'unknown engine'}, 404)
                src = str(b.get('image') or '')
                try:
                    if src.startswith('/api/jobs/'):
                        parts = src.split('/')
                        ref = (JOBS[parts[3]].dir / 'images' / Path(parts[-1]).name).read_bytes()
                    else:
                        ref = base64.b64decode(src.split(',', 1)[1])
                except Exception:
                    return self.send_json({'error': 'picture not found'}, 400)
                job = Job('multiview', 'Views of the subject')
                run_job(job, lambda j: run_text2image(j, spec, str(b.get('prompt') or '').strip()[:600], str(b.get('lang') or 'en')[:5], 4, b.get('options') or {}, reference=ref))
                return self.send_json(job.as_dict())
            if path == '/api/speech2text':
                spec = engine_specs().get(b.get('engine') or '')
                if not spec or spec.get('kind') != 'speech2text':
                    return self.send_json({'error': 'unknown engine'}, 404)
                job = Job('speech2text', 'Voice to text')
                try:
                    wav = job.dir / 'voice.wav'
                    wav.write_bytes(base64.b64decode(str(b.get('audio') or '').rpartition(',')[2]))
                except Exception:
                    return self.send_json({'error': 'invalid audio'}, 400)
                run_job(job, lambda j: run_speech2text(j, spec, wav, str(b.get('lang') or '')[:5]))
                return self.send_json(job.as_dict())
            if path == '/api/keys' and b.get('library'):
                if b['library'] not in LIBRARIES:
                    return self.send_json({'error': 'unknown library'}, 404)
                save_key('lib:' + b['library'], str(b.get('key') or '').strip())
                return self.send_json({'ok': True})
            if path == '/api/keys':
                if (b.get('engine') or '') not in engine_specs():
                    return self.send_json({'error': 'unknown engine'}, 404)
                save_key(b['engine'], str(b.get('key') or '').strip())
                return self.send_json({'ok': True})
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

class Bridge:

    def __init__(self):
        self.window = None

    def _dialog(self, kind, multiple=False, **kw):
        import webview
        fd = getattr(webview, 'FileDialog', None)
        const = {'save': getattr(fd, 'SAVE', None) if fd else None, 'open': getattr(fd, 'OPEN', None) if fd else None}[kind]
        if const is None:
            const = webview.SAVE_DIALOG if kind == 'save' else webview.OPEN_DIALOG
        r = self.window.create_file_dialog(const, allow_multiple=multiple, **kw) if multiple else self.window.create_file_dialog(const, **kw)
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
        Path(path).write_bytes(base64.b64decode(b64))
        log('Saved', path)
        return {'path': path, 'name': Path(path).name}

    def open_file(self, kind='all', label=None):
        exts = {'project': '*.as3d;*.json', 'image': '*.jpg;*.jpeg;*.png;*.heic;*.webp' if IS_MAC else '*.jpg;*.jpeg;*.png;*.webp', 'all': '*.as3d;*.obj;*.stl;*.glb;*.gltf;*.ply;*.json'}[kind]
        default = {'project': 'AutoSculpt 3D projects', 'image': 'Images', 'all': '3D models and projects'}[kind]
        types = (f'{label or default} ({exts})',)
        path = self._dialog('open', file_types=types, directory=str(Path.home()))
        if not path:
            return None
        return self.read_file(path)

    def open_files(self, kind='image', label=None, limit=5):
        exts = '*.jpg;*.jpeg;*.png;*.heic;*.webp' if IS_MAC else '*.jpg;*.jpeg;*.png;*.webp'
        paths = self._dialog('open', multiple=True, file_types=(f"{label or 'Images'} ({exts})",), directory=str(Path.home()))
        return [f for f in (self.read_file(p) for p in paths[:int(limit)]) if f]

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
            threading.Timer(3.0, lambda: (stop_engines(), os._exit(0))).start()
            try:
                self.window.destroy()
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

def stop_engines():
    for job in list(JOBS.values()):
        pr = getattr(job, 'proc', None)
        if pr and pr.poll() is None:
            job.cancelled = True
            try:
                pr.kill()
            except Exception:
                pass

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
    remove_retired_engines()
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
    bridge.window = win
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
    finally:
        stop_engines()
if __name__ == '__main__':
    main()
