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
    for line in p.stdout:
        line = line.rstrip()
        if not line:
            continue
        job.add_log(line)
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

def engine_home(eid):
    return SUPPORT / 'engines' / eid

def engine_status(spec):
    if spec.get('type') == 'cloud':
        key = load_keys().get(spec['id'], '')
        return {'id': spec['id'], 'name': spec['name'], 'type': 'cloud', 'version': spec.get('version'), 'order': spec.get('order', 50), 'description': spec.get('description'), 'license': spec.get('license'), 'price': spec.get('price'), 'minutes': spec.get('minutes'), 'key_url': spec.get('key_url'), 'key_hint': spec.get('key_hint'), 'max_images': spec.get('max_images', 1), 'options': spec.get('options', []), 'installed': bool(key), 'key_set': bool(key), 'key_end': key[-4:] if len(key) > 8 else '', 'update': False}
    home = engine_home(spec['id'])
    info = {}
    try:
        info = json.loads((home / 'installed.json').read_text())
    except Exception:
        pass
    return {'id': spec['id'], 'name': spec['name'], 'version': spec.get('version'), 'description': spec.get('description'), 'license': spec.get('license'), 'download_gb': spec.get('download_gb'), 'minutes': spec.get('minutes'), 'options': spec.get('options', []), 'installed': bool(info), 'installed_version': info.get('version'), 'type': 'local', 'order': spec.get('order', 50), 'max_images': 1, 'update': bool(info) and vtuple(spec.get('install_version', spec.get('version'))) > vtuple(info.get('version'))}

def copy_runner(spec, home):
    src, dst = (Path(spec['_dir']) / spec['runner'], home / spec['runner'])
    if src.resolve() != dst.resolve() and (not dst.exists() or src.read_bytes() != dst.read_bytes()):
        shutil.copy2(src, dst)
    return dst

def engine_env(home):
    return {'PYTORCH_ENABLE_MPS_FALLBACK': '1', 'HF_HOME': str(home / 'hf'), 'U2NET_HOME': str(home / 'u2net'), 'HF_HUB_DISABLE_TELEMETRY': '1'}

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
    if w:
        at(k, 'Downloading the model weights ({gb} GB in total)', gb=spec.get('download_gb', '?'))
        code = "import sys\nfrom huggingface_hub import hf_hub_download\nrepo, dest = sys.argv[1], sys.argv[2]\nimport time\nfor i, f in enumerate(sys.argv[3:]):\n    print('FILE', i, f, flush=True)\n    for t in range(4):\n        try:\n            hf_hub_download(repo, f, local_dir=dest); break\n        except Exception as e:\n            print('RETRY', t + 1, e, flush=True)\n            if t == 3: raise\n            time.sleep(5 * (t + 1))\n"
        rc = stream(job, [py, '-c', code, w['hf_repo'], home / 'weights', *w['files']], env={'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_ENABLE_HF_TRANSFER': '0'}, on_line=lambda l: job.update(None, 'Downloading the weights: {file}', file=l.split(' ', 2)[-1]) if l.startswith('FILE') else None)
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

def run_engine(job, spec, images, options):
    if spec.get('type') == 'cloud':
        key = load_keys().get(spec['id'])
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
                html = html.replace('<head>', '<head>\n' + inject, 1)
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
            if path == '/api/info':
                return self.send_json({'app': APP_NAME, 'version': VERSION, 'ui': ui_version(), 'product': product_version(), 'machine': platform.machine(), 'macos': platform.mac_ver()[0], 'os': sys.platform, 'support': str(SUPPORT), 'settings': load_settings()})
            if path == '/api/engines':
                return self.send_json(sorted((engine_status(s) for s in engine_specs().values()), key=lambda e: (e['order'], e['name'])))
            if path.startswith('/api/jobs/'):
                parts = path.split('/')
                job = JOBS.get(parts[3]) if len(parts) > 3 else None
                if not job:
                    return self.send_error(404)
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
                spec = engine_specs().get(b.get('engine') or 'triposr')
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
            if path == '/api/keys':
                if (b.get('engine') or '') not in engine_specs():
                    return self.send_json({'error': 'unknown engine'}, 404)
                save_key(b['engine'], str(b.get('key') or '').strip())
                return self.send_json({'ok': True})
            if path.startswith('/api/jobs/') and path.endswith('/cancel'):
                job = JOBS.get(path.split('/')[3])
                if job:
                    job.cancelled = True
                    if job.proc:
                        job.proc.terminate()
                return self.send_json({'ok': True})
            if path == '/api/update/apply':
                job = run_job(Job('update', 'Update'), apply_updates)
                return self.send_json(job.as_dict())
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
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
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
        exts = {'project': '*.json', 'image': '*.jpg;*.jpeg;*.png;*.heic;*.webp' if IS_MAC else '*.jpg;*.jpeg;*.png;*.webp', 'all': '*.obj;*.stl;*.glb;*.gltf;*.ply;*.json'}[kind]
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
        p, n = (folder / (base + '.autosculpt.json'), 2)
        while p.exists():
            p, n = (folder / f'{base} {n}.autosculpt.json', n + 1)
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
            subprocess.Popen(['/bin/sh', '-c', f'n=0; while kill -0 {pid} 2>/dev/null && [ $n -lt 100 ]; do sleep 0.2; n=$((n+1)); done; sleep 0.5; open "{app}"'], env=clean_env(), start_new_session=True)

        def close_and_exit():
            time.sleep(0.3)
            threading.Timer(3.0, lambda: os._exit(0)).start()
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
    a = ap.parse_args()
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
    bridge = Bridge()
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
        webview.start(private_mode=False, storage_path=str(SUPPORT / 'webview'), menu=menu)
    except TypeError:
        NATIVE_MENU = False
        webview.start(private_mode=False, storage_path=str(SUPPORT / 'webview'))
if __name__ == '__main__':
    main()
