# NFL 데일리 위젯 실행 절차 (스케줄 작업이 매번 이 파일을 따름)

작업 폴더 = 이 RUN_NFL.md 가 있는 폴더. 모든 명령은 여기서 실행.
PY = Mac 은 `python3`, Windows 는 `python -I`. D = 오늘 날짜 YYYYMMDD(로컬 ET). MMDD = D 의 월일.
예약: 경기 있는 날 아침(9시) + 오후 업데이트(16시, 첫 킥오프가 일찍이면 2시간 전 — 일요일은 11시). 언제 돌지는 mac/run.sh 가 정해요. 슬레이트 = 오늘 경기, 리뷰 = 오늘 이전 가장 최근 경기일(build_nfl.py 가 정함).
분석 문장은 한국어 존댓말("~해요")로.

## 판정과 픽 (중요)
- ⭐강력추천 / ✅추천 / ⚠️주의 판정과 경고는 build_nfl.py 가 시즌 기록으로 계산해요(NBA 와 같은 규칙, 오버=기준선 이상 쪽). 분석 문장에서 판정을 바꾸거나 새 픽을 만들지 않아요.
- 리서치로 판정에 영향을 줄 사실(선발 QB 교체, 주전 결장 확정, 큰 날씨 변화)이 나오면 해당 경기 notes 에 "⚠️ 판정 재확인: …" 으로 적어요.
- 수치는 nfl_auto.json·ESPN·기사에 있는 것만. 확인 못 한 건 "확인 필요", 추측은 "추정"이라고 표시.

## 0. 실행 여부
- 불렀으면 sent/ 파일과 상관없이 절차를 처음부터 끝까지 수행해요(오후 실행도 데이터·리서치·분석 전부 새로).
- `PY build_nfl.py D --count` 가 0 이면(이번 구간 예정 경기 없음) 종료.

## 1. 데이터 (매번 새로 수집)
- `PY build_nfl.py D nfl_auto.json` → 경기·라인·부상·날씨·팀 스탯·80~100% 기준선 판정·prop 라인 대비 시즌 기록·TD·역할 변화·🚀 팀별 첫 3드라이브(공격 득점률·수비 저지율과 막은 방법)·첫 TD 쿼터 분포·지난 경기 결과·직전 위젯 픽 채점·지난 라인 vs 실제.
- 필요하면 `PY espn_brief.py nfl <경기 날짜>` (프리뷰: ATS·팀 스탯·부상), `PY espn_brief.py nfl <지난 경기 날짜> review` (박스·쿼터 점수).
- 지난 경기 AP 리캡: `https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=<results[].id>` 의 article.story.

## 2. 리서치 (WebSearch, 매번 새로)
- 각 경기: 최신 부상 리포트(금요일 공식 지정 Out/Doubtful/Questionable), QB 선발 확정, 주요 리시버·RB 출전 여부, 라인 이동 이유.
- 야외 경기 날씨(바람·비·눈)가 nfl_auto.json 과 다르게 바뀌었는지.
- nfl_auto.json usage(역할 변화) 상위 선수: 역할이 바뀐 이유(동료 부상 복귀·뎁스차트 변경·트레이드).
- 지난 경기에서 예상과 다르게 끝난 경기(라인 대비 큰 차이, 스타 부진): 원인 기사 확인.

## 3. nfl_analysis.json 작성 (키 이름 그대로)
{"games":{"AAA @ BBB":{"ctx":"","notes":["",""]}},
 "teams":{"ABBR":{"style":"","str":"","weak":"","key":""}},          ← 이번 구간 경기하는 모든 팀
 "review":{"summary":"","items":[{"t":"","why":"","lesson":""}]}}
- 경기 키는 nfl_auto.json games[].m 과 정확히 같게.
- ctx: 경기 상황 2~3문장(시즌 성적 흐름, 핵심 결장자, 날씨·장소 변수).
- notes: 데이터에서 눈에 띄는 사실 2~4개. 그중 1개는 인터셉션 매치업(nfl_auto.json ints·intQB: 양 팀 던진/잡은 INT·리그 순위·QB INT 경기 비율·INT 후 실점)으로. 예) "X 의 리시빙 야드 라인 54.5 는 시즌 평균 71.2 보다 낮고, 최근 3경기 타깃이 9.3개로 늘었어요(팀 WR1 결장)". 이 경기의 ⭐·✅ 판정 선수 중 리서치로 바뀐 사정이 있으면 "⚠️ 판정 재확인"으로.
- teams: style(공격·수비 성향), str(강점), weak(약점), key(이번 상대와의 매치업 포인트: 상대 허용 패스·러시 야드 등 수치 근거).
- review: 지난 경기 결과, grades(직전 위젯 픽 채점), propReview(지난 라인 vs 실제)를 보고 summary 에 채점 요약(⭐ x/y, ✅ x/y)과 흐름을, items 에 실패한 픽·크게 빗나간 경기의 원인을. lesson 은 다음에 볼 점(예: "QB 교체 주에는 리시버 기준선 표본이 무의미해요").

## 4. 렌더 + 검증
- `PY render_nfl.py nfl_auto.json nfl_analysis.json nfl_MMDD.html` → "분석 없는 경기: 없음" 과 "팀 카드 없는 팀: 없음" 확인(아니면 보완 후 다시).
- `node -e "const s=require('fs').readFileSync('nfl_MMDD.html','ascii');new Function(s.split('<script>')[1].split('</script>')[0]);console.log('ok')"` → ok 확인.
- `mkdir -p archive && cp nfl_analysis.json archive/nfl_D_analysis.json`

## 5. 내보내기 (프롬프트가 지정한 모드만)
### 웹 모드
- `PY build_nfl.py D --snapshot nfl_auto.json` → archive/nfl_D_props.json·nfl_D_picks.json (다음 실행의 픽 채점·'라인 vs 실제' 리뷰용).
- `PY publish.py nfl D nfl_MMDD.html "<N경기 · 핵심 한 줄>"` → docs/ 에 등록. git push 는 하지 않음(run.sh 가 처리).
- 끝나면 `sent/nfl_D.txt` 에 "web" 기록(덮어써도 됨).
