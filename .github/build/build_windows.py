"""AutoSculpt 3D - Windows version. © 2026 Enrico Migliorati. All rights reserved.
Prepares the folder for the NSIS installer from the PUBLISHED channel (update.json, app/, ui/, engines/: the files the
installed programs download anyway), tests it and leaves it in build/win. Runs on Windows (GitHub Actions of the public
channel repository). Usage, from the root of the channel repository:  python .github/build/build_windows.py
"""
import io
import json
import os
import shutil
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
OUT = ROOT / "build" / "win"
STAGE = OUT / "stage"
CACHE = ROOT / "build" / "cache"
PY_TAG, PY_FULL = "20261026", "3.11.18"
PY_URL = (f"https://github.com/astral-sh/python-build-standalone/releases/download/{PY_TAG}/"
          f"cpython-{PY_FULL}%2B{PY_TAG}-x86_64-pc-windows-msvc-install_only.tar.gz")
RCEDIT_URL = "https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe"


def say(msg):
    print("\n>> " + msg, flush=True)


def fetch(url, dest=None):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AutoSculpt3D-build"}), timeout=180) as r:
                data = r.read()
            if dest:
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                Path(dest).write_bytes(data)
            return data
        except Exception as exc:
            if attempt == 3:
                raise
            print("   retrying:", exc, flush=True)
            time.sleep(5 * (attempt + 1))


def run(cmd, **kw):
    print("   $", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def main():
    man = json.loads((ROOT / "update.json").read_text(encoding="utf-8"))
    version, ui_version = man["app"]["version"], man["ui"]["version"]
    say(f"AutoSculpt 3D {version} (interface {ui_version}) for Windows")
    shutil.rmtree(OUT, ignore_errors=True)
    STAGE.mkdir(parents=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    say("Embedded Python " + PY_FULL)
    tgz = CACHE / f"python-{PY_FULL}-win.tar.gz"
    if not tgz.exists():
        fetch(PY_URL, tgz)
    with tarfile.open(tgz) as t:
        t.extractall(STAGE)
    py = STAGE / "python" / "python.exe"
    run([py, "-c", "import sys; print(sys.version)"])

    say("Native window (pywebview)")
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "pywebview>=5,<6"])
    for junk in ("Lib/test", "Lib/idlelib", "Lib/tkinter", "tcl", "Lib/turtledemo"):
        shutil.rmtree(STAGE / "python" / junk, ignore_errors=True)

    say("Executable and icon")
    from PIL import Image
    ico = OUT / "AutoSculpt3D.ico"
    Image.open(HERE / "icon.png").convert("RGBA").save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    exe = STAGE / "python" / "AutoSculpt3D.exe"
    shutil.copy2(STAGE / "python" / "pythonw.exe", exe)
    rcedit = CACHE / "rcedit-x64.exe"
    if not rcedit.exists():
        fetch(RCEDIT_URL, rcedit)
    run([rcedit, exe, "--set-icon", ico,
         "--set-version-string", "FileDescription", "AutoSculpt 3D",
         "--set-version-string", "ProductName", "AutoSculpt 3D",
         "--set-version-string", "CompanyName", "Enrico Migliorati",
         "--set-version-string", "LegalCopyright", "© 2026 Enrico Migliorati. All rights reserved.",
         "--set-file-version", version, "--set-product-version", version])
    shutil.copy2(ico, STAGE / "AutoSculpt3D.ico")
    Image.open(HERE / "project_icon.png").convert("RGBA").save(STAGE / "AutoSculpt3DProject.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    say("three.js and jsPDF (they work without internet)")
    vendor = STAGE / "app" / "ui" / "vendor"
    tdata = fetch("https://registry.npmjs.org/three/-/three-0.169.0.tgz")
    with tarfile.open(fileobj=io.BytesIO(tdata)) as t:
        for m in t.getmembers():
            if m.isfile() and (m.name == "package/build/three.module.js" or m.name.startswith("package/examples/jsm/") or m.name == "package/LICENSE"):
                dest = vendor / "three" / m.name[len("package/"):]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(t.extractfile(m).read())
    fetch("https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js", vendor / "jspdf.umd.min.js")

    say("Program, interface and engines (the published files)")
    (STAGE / "app").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "app" / "main.py", STAGE / "app" / "main.py")
    (STAGE / "app" / "VERSION").write_text(version + "\n")
    shutil.copy2(ROOT / "ui" / "AutoSculpt3D.html", STAGE / "app" / "ui" / "AutoSculpt3D.html")
    (STAGE / "app" / "ui" / "VERSION").write_text(ui_version + "\n")
    shutil.copytree(ROOT / "engines", STAGE / "app" / "engines")
    for f in HERE.glob("license_*.txt"):
        shutil.copy2(f, OUT / f.name)
    (OUT / "version.txt").write_text(version)

    say("Test")
    run([py, "-c", "import webview, webview.platforms.winforms; print('pywebview ok')"])
    test_home = OUT / "test-home"
    env = {**os.environ, "AUTOSCULPT_HOME": str(test_home), "PYTHONIOENCODING": "utf-8", "AUTOSCULPT_UPDATE_URL": "http://127.0.0.1:9/none.json"}
    p = subprocess.Popen([str(py), str(STAGE / "app" / "main.py"), "--server-only"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    url = token = None
    t0 = time.time()
    while time.time() - t0 < 60 and not (url and token):
        line = p.stdout.readline()
        if not line:
            break
        print("   |", line.rstrip())
        if line.startswith("URL "):
            url = line.split()[1]
        if line.startswith("TOKEN "):
            token = line.split()[1]
    try:
        assert url and token, "the program did not start"
        page = urllib.request.urlopen(url, timeout=20).read().decode("utf-8")
        assert "AS_NATIVE" in page and "AutoSculpt" in page, "interface not served"
        assert "/vendor/three/build/three.module.js" in page
        urllib.request.urlopen(url + "vendor/three/build/three.module.js", timeout=20).read(100)
        info = json.loads(urllib.request.urlopen(urllib.request.Request(url + "api/info", headers={"X-AS-Token": token}), timeout=20).read())
        assert info["os"] == "win32" and info["version"] == version, info
        # (the 3D engines moved to AutoSculpt Studio in 1.3.38: /api/engines no longer exists)
        print("   test passed:", info.get("app"), info["version"])
    finally:
        p.kill()
    shutil.rmtree(test_home, ignore_errors=True)
    size = sum(f.stat().st_size for f in STAGE.rglob("*") if f.is_file()) / 1e6
    say(f"Ready: {STAGE} ({size:.0f} MB)")


if __name__ == "__main__":
    main()
