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
# 동시 실행 방지(같은 auto.json·docs 를 씀). inj 는 최대 25분 기다리고, 아침/오후 실행은 바쁘면 다음 30분 슬롯에 다시 시도.
LOCK="logs/.lock"
for i in $(seq 1 50); do
  if mkdir "$LOCK" 2>/dev/null; then echo $$ > "$LOCK/pid"; trap 'rm -rf "$LOCK"' EXIT; break; fi
  P=$(cat "$LOCK/pid" 2>/dev/null); if [ -n "$P" ] && ! kill -0 "$P" 2>/dev/null; then rm -rf "$LOCK"; continue; fi
  # 9시처럼 NBA·NFL 이 같은 슬롯에 동시에 뜨면 한쪽이 그냥 끝나버려서, 아침/오후 실행도 최대 5분(10회) 기다린다
  [ "$KIND" = inj ] || [ "$i" -lt 10 ] || { echo "다른 실행 중 → 다음 슬롯"; exit 0; }
  [ "$i" -eq 50 ] && { echo "잠금 대기 초과"; exit 0; }
  sleep 30
done
git pull --rebase --autostash -q || echo "git pull 실패 (계속 진행)"
NOW=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))

# 아침/오후 두 번 전체 실행. launchd 가 9:00 과 10:00~16:00 30분마다 부름.
#  - 아침(am): 그날 첫 슬롯에서 전체 실행.
#  - 오후(pm): min(15:00, 첫 경기 2시간 전) 이후 첫 슬롯에서 다시 전체 실행. 첫 경기 30분 전이 지났거나 아침 실행 후 1시간이 안 됐으면 건너뜀.
#  주말·공휴일 낮 경기(예: 13:00)는 오후 실행이 11:00 으로 당겨짐.
phase() {  # $1 = 마커 접두사, $2 = 오늘 경기 시각들(분, 공백 구분, 정렬)
  local M="$1" FIRST="${2%% *}" LAST="${2##* }"
  if [ ! -f "sent/${M}_am" ]; then
    # 맥이 잠들어 아침 슬롯을 놓쳤어도 남은 경기가 있으면 지금 전체 실행
    [ "$NOW" -ge $(( LAST - 30 )) ] && { echo "남은 경기 없음 → 실행 생략"; return 1; }
    PHASE=am; return 0
  fi
  [ -f "sent/${M}_pm" ] && { echo "오늘 아침·오후 실행 끝"; return 1; }
  local DUE=$(( FIRST - 120 )); [ "$DUE" -gt 900 ] && DUE=900
  [ "$NOW" -lt "$DUE" ] && { echo "오후 실행 시각($(( DUE / 60 )):$(printf %02d $(( DUE % 60 )))) 전"; return 1; }
  [ "$NOW" -ge $(( FIRST - 30 )) ] && { echo "첫 경기 30분 전 지남 → 오후 실행 생략"; echo skip > "sent/${M}_pm"; return 1; }
  [ "$NOW" -lt $(( $(cat "sent/${M}_am") + 60 )) ] && { echo "아침 실행 직후 → 다음 슬롯"; return 1; }
  PHASE=pm; return 0
}

if [ "$KIND" = inj ]; then
  # 경기 2시간 전 부상·라인 재확인 (30분마다 호출). 아침 본 실행이 끝난 날만.
  { [ -f "sent/${D}_am" ] || [ -f "sent/$D.txt" ]; } || { echo "본 실행 전"; exit 0; }
  NOW=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))
  DUE=""
  for T in $(python3 build_data.py "$D" --tips); do
    L=$(( T - NOW ))
    [ "$L" -gt 90 ] && [ "$L" -le 125 ] && [ ! -f "sent/${D}_inj_$T" ] && DUE="$DUE $T"
  done
  [ -z "$DUE" ] && { echo "2시간 전 경기 없음"; exit 0; }
  echo "재확인 대상 팁오프(분):$DUE"
  cp auto.json auto_prev.json
  export CS_PHASE=inj
  python3 build_data.py "$D" auto.json || exit 1
  OUT=$(python3 inj_diff.py auto_prev.json auto.json "$DUE"); echo "$OUT"
  if [ "$(echo "$OUT" | tail -1)" != "CHANGES 0" ]; then
    claude -p "경기 2시간 전 부상 재확인 결과 변동이 있어. 아래 변동을 반영해서 analysis.json 의 해당 경기 games[\"경기\"].ctx 와 lean, picks 만 고쳐(OUT 된 선수 관련 픽은 빼고, 출전시간을 흡수할 선수·매치업 변화 반영). teams·review 는 건드리지 마. 필요하면 WebSearch 로 해당 선수 상태 1회 확인. 한국어 존댓말. 변동: $OUT"       --allowedTools "Read" "Edit" "Write" "WebSearch" "Bash(python3:*)" "Bash(cat:*)" --max-turns 30
  fi
  M=$(date +%m%d)
  python3 render.py auto.json analysis.json "nba_$M.html" && python3 publish.py nba "$D" "nba_$M.html" "$(python3 -c "import json;d=json.load(open('auto.json',encoding='utf-8'));print(f\"{len(d['games'])}경기 · 부상 재확인 {d['injchk'][-1]}\")")" || exit 1
  for T in $DUE; do echo done > "sent/${D}_inj_$T"; done
  PROMPT=""; MARK=""
elif [ "$KIND" = nba ]; then
  T=$(python3 build_data.py "$D" --tips)
  [ -z "$T" ] && { echo "오늘 NBA 경기 없음"; exit 0; }
  phase "$D" "$T" || exit 0
  export CS_PHASE=$PHASE
  echo "PHASE=$PHASE (첫 경기 $(( ${T%% *} / 60 ))시)"
  PROMPT="RUN.md 를 읽고 절차를 처음부터 끝까지 그대로 수행해. 이번은 $([ $PHASE = am ] && echo '아침' || echo '오후 업데이트') 실행이야(데이터·리서치·분석 전부 새로). PY 는 python3 야. 내보내기는 '웹 모드'만 하고 메일은 보내지 마. git push 는 하지 마."
  MARK="sent/${D}_$PHASE"
else
  [ -f RUN_NFL.md ] || { echo "RUN_NFL.md 없음 (NFL 미구성)"; exit 0; }
  T=$(python3 build_nfl.py "$D" --tips)
  [ -z "$T" ] && { echo "오늘 남은 NFL 경기 없음"; exit 0; }
  phase "nfl_$D" "$T" || exit 0
  export CS_PHASE=$PHASE
  echo "PHASE=$PHASE (첫 킥오프 $(( ${T%% *} / 60 ))시)"
  PROMPT="RUN_NFL.md 를 읽고 절차를 처음부터 끝까지 그대로 수행해. 이번은 $([ $PHASE = am ] && echo '아침' || echo '오후 업데이트') 실행이야(데이터·리서치·분석 전부 새로). PY 는 python3 야. 내보내기는 '웹 모드'만 하고 메일은 보내지 마. git push 는 하지 마."
  MARK="sent/nfl_${D}_$PHASE"
fi

# 헤드리스 실행: 필요한 도구만 허용 (삭제·push·sudo 불가)
[ -n "$PROMPT" ] && claude -p "$PROMPT" \
  --allowedTools "Read" "Write" "Edit" "Glob" "Grep" "WebSearch" "WebFetch" \
    "Bash(python3:*)" "Bash(node:*)" "Bash(cp:*)" "Bash(mkdir:*)" "Bash(ls:*)" "Bash(cat:*)" "Bash(date:*)" "Bash(curl:*)" \
  --max-turns 80
RC=$?
echo "== claude exit $RC"
# 페이지가 실제로 다시 게시됐을 때만 이번 단계 완료로 기록(실패하면 다음 슬롯에서 재시도)
if [ -n "${MARK:-}" ] && [ -n "$(git status --porcelain docs)" ]; then echo "$NOW" > "$MARK"; fi

# 새 페이지가 있으면 배포
if [ -n "$(git status --porcelain docs)" ]; then
  git add docs archive && git commit -m "$KIND $D ${PHASE:-} 업데이트" && git push && echo "deployed"
else
  echo "no docs change; skip deploy"
fi
echo "== $(date) end"
