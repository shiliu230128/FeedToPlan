#!/usr/bin/env bash
# 把 6 张 1080×1440 的 HTML 卡片渲染成 PNG
# 先以 2 倍像素密度出图（2160×2880），再降采样回 1080×1440，文字边缘更干净
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
W=1080
H=1440
SCALE=2

mkdir -p "$DIR/out" "$DIR/.tmp" "$DIR/.chrome"

for f in "$DIR"/p*.html; do
  name="$(basename "$f" .html)"
  echo "→ $name"
  "$CHROME" \
    --headless=new \
    --no-sandbox \
    --disable-setuid-sandbox \
    --disable-dev-shm-usage \
    --no-first-run \
    --no-default-browser-check \
    --hide-scrollbars \
    --force-device-scale-factor="$SCALE" \
    --window-size="$W,$H" \
    --user-data-dir="$DIR/.chrome" \
    --virtual-time-budget=4000 \
    --screenshot="$DIR/.tmp/$name@2x.png" \
    "file://$f" >/dev/null 2>&1

  sips -z "$H" "$W" "$DIR/.tmp/$name@2x.png" --out "$DIR/out/$name.png" >/dev/null
done

rm -rf "$DIR/.tmp" "$DIR/.chrome"
echo "完成 → $DIR/out"
sips -g pixelWidth -g pixelHeight "$DIR"/out/*.png | sed 's/^/  /'
