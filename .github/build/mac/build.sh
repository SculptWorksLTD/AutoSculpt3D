#!/bin/bash
# AutoSculpt 3D - builds the Mac app and installer package.
# (c) 2026 Enrico Migliorati. All rights reserved.
#
# Usage, from Terminal, in the project folder:   ./build.sh
# Result:  dist/AutoSculpt3D-<version>.pkg   (and dist/AutoSculpt 3D.app)
# Needs internet. Nothing else to install: it only uses tools already in macOS.

set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
VERSION="$(cat app/VERSION | tr -d '[:space:]')"
APPNAME="AutoSculpt 3D"
BUNDLE_ID="it.migliorati.autosculpt3d"
BUILD="$ROOT/build"; CACHE="$BUILD/cache"; DIST="$ROOT/dist"
APP="$BUILD/stage/$APPNAME.app"
PY_MINOR="3.11"
PY_TAG="20261026"; PY_FULL="3.11.18"   # pinned and tested python-build-standalone version

say() { printf "\n\033[1;33m▸ %s\033[0m\n" "$*"; }
fail() { printf "\n\033[1;31m✗ %s\033[0m\n" "$*"; exit 1; }
trap 'printf "\n\033[1;31m✗ Error at line %s: %s\033[0m\n" "$LINENO" "$BASH_COMMAND"' ERR

[ "$(uname)" = "Darwin" ] || fail "This script must be run on a Mac."
ARCH="$(uname -m)"
case "$ARCH" in
  arm64) PBS_ARCH="aarch64-apple-darwin"; HOSTARCH="arm64" ;;
  x86_64) PBS_ARCH="x86_64-apple-darwin"; HOSTARCH="x86_64" ;;
  *) fail "Unsupported architecture: $ARCH" ;;
esac
for t in curl tar pkgbuild productbuild codesign sips iconutil ditto; do command -v $t >/dev/null || fail "Missing system tool: $t"; done

say "AutoSculpt 3D $VERSION for Mac ($ARCH)"
rm -rf "$BUILD/stage" "$BUILD/pkg"; mkdir -p "$CACHE" "$DIST" "$BUILD/stage" "$BUILD/pkg"

# 1. embedded Python (python-build-standalone, PSF/MIT licence)
PY_TGZ="$CACHE/python-$PY_MINOR-$PBS_ARCH.tar.gz"
if [ ! -s "$PY_TGZ" ]; then
  say "Downloading embedded Python $PY_MINOR"
  URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PY_TAG/cpython-$PY_FULL%2B$PY_TAG-$PBS_ARCH-install_only.tar.gz"
  if ! curl -fsIL -o /dev/null "$URL"; then
    echo "   pinned version not available, looking for the latest release"
    URL=$( (curl -fsSL https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest || true) \
          | (grep -o "https://[^\"]*cpython-$PY_MINOR\.[0-9]*+[0-9]*-$PBS_ARCH-install_only\.tar\.gz" || true) | sed -n 1p)
  fi
  [ -n "$URL" ] || fail "Cannot find Python $PY_MINOR for $PBS_ARCH: check the internet connection."
  echo "   $URL"
  curl -fL --progress-bar -o "$PY_TGZ" "$URL"
fi

# 2. interface libraries (work without internet)
VENDOR="$BUILD/vendor"
if [ ! -s "$VENDOR/three/build/three.module.js" ]; then
  say "Downloading three.js 0.169.0 and jsPDF 2.5.1"
  rm -rf "$VENDOR"; mkdir -p "$VENDOR/three/build" "$VENDOR/three/examples" "$CACHE/three"
  curl -fsSL -o "$CACHE/three.tgz" https://registry.npmjs.org/three/-/three-0.169.0.tgz
  tar -xzf "$CACHE/three.tgz" -C "$CACHE/three"
  cp "$CACHE/three/package/build/three.module.js" "$VENDOR/three/build/"
  cp -R "$CACHE/three/package/examples/jsm" "$VENDOR/three/examples/"
  cp "$CACHE/three/package/LICENSE" "$VENDOR/three/LICENSE"
  curl -fsSL -o "$VENDOR/jspdf.umd.min.js" https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js
fi
if [ ! -s "$VENDOR/fonts.css" ]; then
  say "Downloading the fonts (IBM Plex, Big Shoulders - SIL Open Font License)"
  mkdir -p "$VENDOR/fonts"
  CSS_URL="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@600;800&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap"
  if curl -fsSL -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36" -o "$CACHE/fonts.css" "$CSS_URL"; then
    cp "$CACHE/fonts.css" "$VENDOR/fonts.css.tmp"
    for f in $(grep -o 'https://fonts.gstatic.com/[^)]*' "$CACHE/fonts.css" | sort -u); do
      n="$(echo "$f" | md5 | cut -c1-12).woff2"
      curl -fsSL -o "$VENDOR/fonts/$n" "$f" && sed -i '' "s#$f#/vendor/fonts/$n#g" "$VENDOR/fonts.css.tmp"
    done
    mv "$VENDOR/fonts.css.tmp" "$VENDOR/fonts.css"
  else
    echo "   (fonts not downloaded: the app will use the system fonts)"
  fi
fi

# 3. app structure
say "Assembling $APPNAME.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
tar -xzf "$PY_TGZ" -C "$APP/Contents/Resources"          # creates Resources/python
PYHOME="$APP/Contents/Resources/python"
PY="$PYHOME/bin/python3"
"$PY" -c "import sys; print('   Python', sys.version.split()[0])"
say "Installing the native window (pywebview)"
"$PY" -m pip install --disable-pip-version-check --no-warn-script-location --upgrade pip >/dev/null
"$PY" -m pip install --disable-pip-version-check --no-warn-script-location "pywebview>=5,<6"
rm -rf "$PYHOME/lib/python$PY_MINOR/test" "$PYHOME/lib/python$PY_MINOR/idlelib" "$PYHOME/lib/python$PY_MINOR/tkinter" 2>/dev/null || true

ditto "$ROOT/app" "$APP/Contents/Resources/app"
rm -rf "$APP/Contents/Resources/app/ui"
mkdir -p "$APP/Contents/Resources/app/ui"
ditto "$VENDOR" "$APP/Contents/Resources/app/ui/vendor"
"$PY" "$ROOT/tools/prepare_ui.py" "$ROOT/app/ui/AutoSculpt3D.html" "$APP/Contents/Resources/app/ui" "$(cat app/ui/VERSION | tr -d '[:space:]')"
find "$APP/Contents/Resources/app" -name "__pycache__" -prune -exec rm -rf {} +

# launch: the script in Contents/MacOS starts the embedded Python (same process, so Dock and icon stay AutoSculpt 3D's)
cat > "$APP/Contents/MacOS/AutoSculpt3D" <<'LAUNCH'
#!/bin/bash
# AutoSculpt 3D - launcher. (c) 2026 Enrico Migliorati. All rights reserved.
HERE="$(cd "$(dirname "$0")" && pwd)"
CONTENTS="$(dirname "$HERE")"
RES="$CONTENTS/Resources"
export PYTHONDONTWRITEBYTECODE=1
export AUTOSCULPT_BUNDLE="$RES/app"
export AUTOSCULPT_PYTHON="$RES/python/bin/python3"
export AUTOSCULPT_APP="$(dirname "$CONTENTS")"
export AUTOSCULPT_ICON="$RES/AppIcon.icns"
LOGDIR="$HOME/Library/Application Support/AutoSculpt3D/logs"; mkdir -p "$LOGDIR"
exec "$RES/python/bin/python3" "$RES/app/main.py" "$@" >>"$LOGDIR/avvio.log" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/AutoSculpt3D"

# icon
say "Creating the icon"
ICONSET="$BUILD/AppIcon.iconset"; rm -rf "$ICONSET"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$ROOT/assets/icon_1024.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  d=$((s*2)); sips -z $d $d "$ROOT/assets/icon_1024.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
# icon of the .as3d project files
if [ -f "$ROOT/assets/project_icon_1024.png" ]; then
  PICONSET="$BUILD/ProjectIcon.iconset"; rm -rf "$PICONSET"; mkdir -p "$PICONSET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$ROOT/assets/project_icon_1024.png" --out "$PICONSET/icon_${s}x${s}.png" >/dev/null
    d=$((s*2)); sips -z $d $d "$ROOT/assets/project_icon_1024.png" --out "$PICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$PICONSET" -o "$APP/Contents/Resources/ProjectIcon.icns"
else
  cp "$APP/Contents/Resources/AppIcon.icns" "$APP/Contents/Resources/ProjectIcon.icns"
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$APPNAME</string>
  <key>CFBundleDisplayName</key><string>$APPNAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>AutoSculpt3D</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleLocalizations</key><array><string>en</string><string>it</string><string>de</string><string>fr</string><string>es</string><string>pt_BR</string><string>ja</string></array>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.graphics-design</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>AutoSculpt 3D uses the microphone only while you dictate the description of a sculpture.</string>
  <key>NSHumanReadableCopyright</key><string>From dream to reality. © 2026 Enrico Migliorati. All rights reserved.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>CFBundleDocumentTypes</key><array><dict>
    <key>CFBundleTypeName</key><string>AutoSculpt 3D project</string>
    <key>CFBundleTypeRole</key><string>Editor</string>
    <key>LSHandlerRank</key><string>Owner</string>
    <key>CFBundleTypeIconFile</key><string>ProjectIcon</string>
    <key>LSItemContentTypes</key><array><string>it.migliorati.autosculpt3d.project</string></array>
  </dict></array>
  <key>UTExportedTypeDeclarations</key><array><dict>
    <key>UTTypeIdentifier</key><string>it.migliorati.autosculpt3d.project</string>
    <key>UTTypeDescription</key><string>AutoSculpt 3D project</string>
    <key>UTTypeIconFile</key><string>ProjectIcon</string>
    <key>UTTypeConformsTo</key><array><string>public.data</string><string>public.archive</string></array>
    <key>UTTypeTagSpecification</key><dict>
      <key>public.filename-extension</key><array><string>as3d</string></array>
      <key>public.mime-type</key><array><string>application/x-autosculpt3d</string></array>
    </dict>
  </dict></array>
</dict></plist>
PLIST

# local (ad-hoc) signature: needed on Macs with Apple chips
say "Signing the app (local signature)"
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "   (local signature not applied: not needed for use on this Mac)"

# 4. installer package
say "Building the installer package"
cat > "$BUILD/pkg/component.plist" <<CPLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><array><dict>
  <key>BundleHasStrictIdentifier</key><true/>
  <key>BundleIsRelocatable</key><false/>
  <key>BundleIsVersionChecked</key><false/>
  <key>BundleOverwriteAction</key><string>upgrade</string>
  <key>RootRelativeBundlePath</key><string>$APPNAME.app</string>
</dict></array></plist>
CPLIST
pkgbuild --root "$BUILD/stage" --component-plist "$BUILD/pkg/component.plist" --identifier "$BUNDLE_ID" \
  --version "$VERSION" --install-location /Applications "$BUILD/pkg/app.pkg"
sed -e "s/@VERSION@/$VERSION/g" -e "s/@ARCH@/$HOSTARCH/g" "$ROOT/installer/distribution.xml" > "$BUILD/pkg/distribution.xml"
OUT="$DIST/AutoSculpt3D-$VERSION.pkg"
productbuild --distribution "$BUILD/pkg/distribution.xml" --resources "$ROOT/installer" --package-path "$BUILD/pkg" "$OUT"
rm -rf "$DIST/$APPNAME.app"; ditto "$APP" "$DIST/$APPNAME.app"

SIZE=$(du -sh "$OUT" | cut -f1)
say "Done!"
echo "   Package: $OUT ($SIZE)"
echo "   App:       $DIST/$APPNAME.app"
echo
echo "   To install: double-click the package. The first time macOS may block it because it is not"
echo "   signed with an Apple certificate: right-click the file → Open → Open."
