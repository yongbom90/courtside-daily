#!/bin/bash
# Mac mini 자동 실행: launchd 가 호출. usage: run.sh nba|nfl
# 1) Claude Code(헤드리스)가 RUN.md 절차로 수집·리서치·분석·렌더·docs 등록
# 2) 새 페이지가 생겼으면 이 스크립트가 git push → GitHub Pages 반영
set -u
KIND="${1:-nba}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
mkdir -p logs
D=$(date +%Y%m%d)
LOG="logs/${KIND}_${D}_$(date +%H%M).log"
exec >>"$LOG" 2>&1
echo "== $(date) $KIND start"
git pull --rebase -q || echo "git pull 실패 (계속 진행)"

if [ "$KIND" = nba ]; then
  [ -f "sent/$D.txt" ] && { echo "already done"; exit 0; }
  TIP=$(python3 build_data.py "$D" --first-tip)
  [ "$TIP" -lt 0 ] && { echo "오늘 NBA 경기 없음"; exit 0; }
  # 오전 실행은 첫 경기가 18시(ET) 전일 때만 (주말·공휴일 낮 경기). 아니면 오후 3시 실행이 처리.
  [ "$(date +%H)" -lt 13 ] && [ "$TIP" -ge 18 ] && { echo "첫 경기 ${TIP}시 → 오후 실행에서 처리"; exit 0; }
  PROMPT="RUN.md 를 읽고 절차를 처음부터 끝까지 그대로 수행해. PY 는 python3 야. 내보내기는 '웹 모드'만 하고 메일은 보내지 마. git push 는 하지 마."
else
  [ -f "sent/nfl_$D.txt" ] && { echo "already done"; exit 0; }
  [ -f RUN_NFL.md ] || { echo "RUN_NFL.md 없음 (NFL 미구성)"; exit 0; }
  PROMPT="RUN_NFL.md 를 읽고 절차를 처음부터 끝까지 그대로 수행해. PY 는 python3 야. 내보내기는 '웹 모드'만 하고 메일은 보내지 마. git push 는 하지 마."
fi

# 헤드리스 실행: 필요한 도구만 허용 (삭제·push·sudo 불가)
claude -p "$PROMPT" \
  --allowedTools "Read" "Write" "Edit" "Glob" "Grep" "WebSearch" "WebFetch" \
    "Bash(python3:*)" "Bash(node:*)" "Bash(cp:*)" "Bash(mkdir:*)" "Bash(ls:*)" "Bash(cat:*)" "Bash(date:*)" "Bash(curl:*)" \
  --max-turns 80
echo "== claude exit $?"

# 새 페이지가 있으면 배포
if [ -n "$(git status --porcelain docs)" ]; then
  git add docs archive && git commit -m "$KIND $D 업데이트" && git push && echo "deployed"
else
  echo "no docs change; skip deploy"
fi
echo "== $(date) end"
