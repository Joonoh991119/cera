#!/usr/bin/env bash
# 시험용 로컬 보드 — 임시 TEMPBOARD_HOME, 터널 없음. start → 편집 링크 한 줄 출력, stop → 닫고 임시 폴더 지움.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
STATE="${TB_TEST_STATE:-$ROOT/_workspace/.local_board}"
case "${1:-}" in
  start)
    [ -f "$STATE" ] && "$0" stop >/dev/null 2>&1 || true   # 이전 시험 보드가 고아로 남지 않게
    mkdir -p "$(dirname "$STATE")"
    H="$(mktemp -d "${TMPDIR:-/tmp}/tbtest.XXXXXX")"
    OUT="$(TEMPBOARD_HOME="$H" TEMPBOARD_DIST="$ROOT/web/dist" python3 "$ROOT/bin/tempboard" start --tunnel none --ttl 3h --title "${2:-시험}" --view-link --json)"
    SID="$(printf '%s' "$OUT" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')"
    printf '%s\n%s\n' "$H" "$SID" > "$STATE"
    printf '%s' "$OUT" | python3 -c 'import sys,json;l=json.load(sys.stdin)["links"];print(l.get("local") or l["edit"])'
    ;;
  view)
    H="$(sed -n 1p "$STATE")"; SID="$(sed -n 2p "$STATE")"
    TEMPBOARD_HOME="$H" python3 "$ROOT/bin/tempboard" links "$SID" --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["links"].get("view",""))'
    ;;
  stop)
    [ -f "$STATE" ] || { echo "열린 시험 보드 없음"; exit 0; }
    H="$(sed -n 1p "$STATE")"; SID="$(sed -n 2p "$STATE")"
    TEMPBOARD_HOME="$H" python3 "$ROOT/bin/tempboard" stop "$SID" >/dev/null 2>&1 || true
    case "$H" in */tbtest.*) rm -rf "$H";; esac
    rm -f "$STATE"; echo "닫음 $SID"
    ;;
  *) echo "usage: local_board.sh start [제목] | view | stop"; exit 2;;
esac
