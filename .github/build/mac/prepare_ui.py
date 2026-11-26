"""Prepares the interface for the app: local libraries and fonts (works without internet).
AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.

Usage: python prepare_ui.py <source AutoSculpt3D.html> <destination ui folder> <version>
"""
import sys
from pathlib import Path

REPLACE = {
    "https://cdn.jsdelivr.net/npm/three@0.169.0/build/three.module.js": "/vendor/three/build/three.module.js",
    "https://cdn.jsdelivr.net/npm/three@0.169.0/examples/jsm/": "/vendor/three/examples/jsm/",
    "https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js": "/vendor/jspdf.umd.min.js",
}
FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@600;800'
         '&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">')


def main():
    src, dest, version = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    html = src.read_text(encoding="utf-8")
    for a, b in REPLACE.items():
        if a in html:
            html = html.replace(a, b)
        elif b not in html:   # (the published page already has the local addresses)
            sys.exit(f"Warning: {a} not found in the interface")
    vendor_fonts = (dest / "vendor" / "fonts.css").exists() or "--vendor-fonts" in sys.argv
    if vendor_fonts and FONTS in html:
        html = html.replace(FONTS, '<link rel="stylesheet" href="/vendor/fonts.css">')
    html = html.replace('<link rel="preconnect" href="https://fonts.googleapis.com">\n', "")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "AutoSculpt3D.html").write_text(html, encoding="utf-8")
    (dest / "VERSION").write_text(version + "\n")
    print("Interface ready:", dest / "AutoSculpt3D.html")


if __name__ == "__main__":
    main()
