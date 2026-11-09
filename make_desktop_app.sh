#!/bin/bash
# Builds "Rehab Glove Trainer.app" on the Desktop: double-click to start the app, no Terminal.
# Re-run this if you move the project folder (the app remembers where the project is).
set -e
PROJECT="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Desktop/Rehab Glove Trainer.app"

if [ ! -x "$PROJECT/.venv/bin/python" ]; then
  echo "Setting up the Python environment (first time only, takes a few minutes)..."
  python3 -m venv "$PROJECT/.venv"
  "$PROJECT/.venv/bin/pip" install -q -r "$PROJECT/requirements.txt"
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/MacOS/launcher" <<EOF
#!/bin/bash
cd "$PROJECT"
mkdir -p data
echo "--- \$(date) ---" >> data/launcher.log
# On Apple Silicon always run Python natively: if the app is opened "using Rosetta"
# (Get Info checkbox, or macOS's choice for script apps), Python would start as Intel
# and fail to load the arm64 numpy/Qt libraries.
if [ "\$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
  exec arch -arm64 "$PROJECT/.venv/bin/python" main.py >> data/launcher.log 2>&1
fi
exec "$PROJECT/.venv/bin/python" main.py >> data/launcher.log 2>&1
EOF
chmod +x "$APP/Contents/MacOS/launcher"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Rehab Glove Trainer</string>
  <key>CFBundleDisplayName</key><string>Rehab Glove Trainer</string>
  <key>CFBundleIdentifier</key><string>local.rehab-glove-trainer</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSArchitecturePriority</key><array><string>arm64</string><string>x86_64</string></array>
  <key>NSCameraUsageDescription</key><string>The camera tracks the hand during exercises.</string>
</dict></plist>
EOF

# Icon: drawn with Qt (already installed), converted with macOS iconutil
ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
QT_QPA_PLATFORM=offscreen "$PROJECT/.venv/bin/python" - "$ICONSET" <<'PY'
import sys
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
app = QGuiApplication([])
out = sys.argv[1]
fingers = [((0.30, 0.62), (0.18, 0.40), "#e8743b"), ((0.42, 0.50), (0.38, 0.18), "#1f6feb"),
           ((0.52, 0.48), (0.53, 0.14), "#19a979"), ((0.62, 0.50), (0.67, 0.19), "#945ecf"),
           ((0.70, 0.56), (0.79, 0.30), "#d6456d")]
for size in (16, 32, 64, 128, 256, 512, 1024):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#f4f6fa"))
    m = size * 0.06
    p.drawRoundedRect(QRectF(m, m, size - 2 * m, size - 2 * m), size * 0.2, size * 0.2)
    wrist = QPointF(size * 0.5, size * 0.84)
    for base, tip, color in fingers:
        pen = QPen(QColor(color), size * 0.07)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        b = QPointF(base[0] * size, base[1] * size)
        p.drawLine(wrist, b)
        p.drawLine(b, QPointF(tip[0] * size, tip[1] * size))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#1f2937"))
    p.drawEllipse(wrist, size * 0.06, size * 0.06)
    p.end()
    for scale, name in ((1, size), (2, size // 2)):
        if 16 <= name <= 512 and (scale == 1 or size >= 32):
            img.save(f"{out}/icon_{name}x{name}{'@2x' if scale == 2 else ''}.png")
PY
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
touch "$APP"
echo "Created: $APP"
