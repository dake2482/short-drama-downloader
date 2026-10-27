#!/bin/bash
# short-drama-downloader 一键下载脚本
# 用法: ./download.sh <剧集URL> <目标目录> [起始集] [结束集]
# 说明: 逐集下载（幂等，已存在的集自动跳过）；首次运行自动创建 venv 并安装依赖

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"   # 仓库根目录（含 download_drama.py 和 src/）
URL="${1:?用法: ./download.sh <剧集URL> <目标目录> [起始集] [结束集]}"
DEST="${2:?需要目标目录}"
START="${3:-1}"
END="${4:-}"

# ---------- venv 自动准备 ----------
if [ ! -d "$REPO/.venv" ]; then
  echo "🔧 首次运行：创建虚拟环境并安装依赖..."
  python3 -m venv "$REPO/.venv" || { echo "❌ 创建 venv 失败（需要 python3）"; exit 1; }
  "$REPO/.venv/bin/pip" install -q --upgrade pip
  "$REPO/.venv/bin/pip" install -q yt-dlp requests aiohttp curl_cffi || { echo "❌ 依赖安装失败"; exit 1; }
fi
PY="$REPO/.venv/bin/python"
cd "$REPO"

# ---------- 1. 解析剧集信息 ----------
INFO_OUT=$($PY download_drama.py "$URL" --info 2>/dev/null)
if [ -z "$INFO_OUT" ]; then echo "❌ 解析剧集列表失败（检查 URL 和网络）"; exit 1; fi

if [ -z "$END" ]; then
  END=$(echo "$INFO_OUT" | grep "本次匹配剧集数" | grep -oE '[0-9]+' | head -1)
  [ -z "$END" ] && { echo "❌ 无法确定总集数"; exit 1; }
fi

DRAMA=$(echo "$INFO_OUT" | grep "剧名:" | sed 's/.*剧名: //' | head -1)
[ -z "$DRAMA" ] && { echo "❌ 无法获取剧名"; exit 1; }

DEST_DIR="$DEST/$DRAMA"
mkdir -p "$DEST_DIR"
echo "📺 剧名: $DRAMA"
echo "📦 计划下载: 第 $START ~ $END 集 → $DEST_DIR"

# ---------- 2. 逐集下载（/tmp 中转，幂等跳过） ----------
TMP="/tmp/drama_dl_$$"
mkdir -p "$TMP"
OK=0; FAIL=0; FAILED_EPS=""

for i in $(seq "$START" "$END"); do
  EP=$(printf "Episode %02d" "$i")
  if [ -f "$DEST_DIR/$EP.mp4" ] && [ -s "$DEST_DIR/$EP.mp4" ]; then
    echo "⏭️  $EP 已存在，跳过"
    continue
  fi
  rm -f "$TMP/$EP.mp4"* 2>/dev/null
  LOG="/tmp/sd_ep_${i}_$$.log"
  $PY download_drama.py "$URL" --start "$i" --end "$i" --out "$TMP" > "$LOG" 2>&1
  SRC=$(find "$TMP" -name "$EP.mp4" 2>/dev/null | head -1)
  if [ -n "$SRC" ]; then
    mv "$SRC" "$DEST_DIR/$EP.mp4"
    echo "✅ $EP"
    OK=$((OK+1))
  else
    echo "❌ $EP (详见 $LOG)"
    FAIL=$((FAIL+1)); FAILED_EPS="$FAILED_EPS $i"
  fi
  sleep 1
done

# ---------- 3. 收尾 ----------
rm -rf "$TMP" 2>/dev/null || true
echo "=============================="
echo "🏁 完成: 新增 $OK 集, 失败 $FAIL 集"
[ -n "$FAILED_EPS" ] && echo "失败集数:$FAILED_EPS（重跑本脚本即可续传，已完成的会跳过）"
echo "📂 目录: $DEST_DIR"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
