# NBA 데일리 위젯 실행 절차 (스케줄 작업이 매번 이 파일을 따름)

작업 폴더 = 이 RUN.md 가 있는 폴더. 모든 명령은 여기서 실행.
PY = Mac 은 `python3`, Windows 는 `python -I`. D = 오늘 날짜 YYYYMMDD(로컬 ET), Y = 어제.
분석 문장은 한국어 존댓말("~해요")로.

## 0. 실행 여부
- 하루 두 번 전체 실행: 아침(9시) + 오후 업데이트(16시, 첫 경기가 일찍이면 첫 경기 2시간 전). 언제 돌지는 mac/run.sh 가 정해요. 불렀으면 sent/ 파일과 상관없이 아래 절차를 처음부터 끝까지 수행해요(오후 실행도 데이터·리서치·분석 전부 새로).
- 오늘 NBA 경기가 없으면(`PY build_data.py D --first-tip` 이 -1) 종료.

## 1. 데이터 (매번 새로 수집)
- `PY build_data.py D auto.json` → 라인·부상·L5·선수 시즌 로그·80% 스탯(DraftKings 라인·출전시간 경고 포함)·라인 vs 기록·💰 가성비(DraftKings 단계별 오버 배당 vs 추정 확률, 콤보 포함 · 🪜 선수별 고정 간격 사다리 ladders)·🧩 콤보(PRA·PR·PA·RA 판정·가성비·라인)·출전시간 변화·어제 결과·어제 라인 vs 실제·어제 가성비 픽 채점(vgrades).
- `PY espn_brief.py nba D` (프리뷰: ATS·팀 스탯·부상) / `PY espn_brief.py nba Y review` (어제 박스·쿼터 점수).
- 어제 경기 AP 리캡: `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=<id>` 의 article.story.

## 2. 리서치 (WebSearch, 매번 새로)
- 오늘 각 경기: 부상 리포트 최신 상태, GTD/Q 스타 출전 여부, 라인 이동, 선발 변경 뉴스.
- 어제 예상과 다르게 끝난 경기(블로아웃, 스타 부진, 업셋): 원인 기사 확인(더블팀·매치업 상성·파울트러블·부상 이탈·가비지 타임).
- 확인 못 한 건 "확인 필요", 추측은 "추정"이라고 표시.

## 3. analysis.json 작성 (키 이름 그대로)
{"games":{"AAA @ BBB":{"ctx":"","mu":["",""],"lean":"","picks":[["라벨",미국식배당 또는 null]]}},
 "teams":{"ABBR":{"style":"","str":"","weak":"","atk":""}},     ← 오늘 경기하는 모든 팀
 "review":{"summary":"","items":[{"t":"","why":"","lesson":""}],"grades":[["픽","결과","O|X|-"]]}}
- 경기 키는 auto.json games[].m 과 정확히 같게.
- **mu (가장 중요, 경기마다 반드시)**: 시즌 기록은 좋지만 오늘 상대 때문에 불리한 픽을 오너에게 알려 주는 칸. 예) "WSH 데이비스 리바 8+는 시즌 10/12지만, 오늘 MEM은 놓치는 슛이 적고 공격리바율 3위에 에디(10.2R·공리 4.1)가 있어 불리해요 → 리바 대신 PRA 쪽이 안전해요".
  - 근거는 auto.json 의 stat80/ladders `mu`([등급, 순위/30, 이유]), `teams`(30팀 허용 기록·순위·빅맨/림 보호/스틸 선수 km), 부상 리포트, 리서치(상대 선발 센터 결장·복귀, 수비 매치업 뉴스).
  - 경기 흐름: ladders/stat80 의 `flow`([종류, 문장, 보정 여부])가 bad(주전 + 13점↑ 우세 또는 10점↑ 열세 = 블로아웃 위험, 오늘 확률·예상치 이미 하향)면 해당 주전 픽을 1줄로 경고해요.
  - 이 경기의 ⭐/✅ 픽 중 `mu` 가 bad 인 것은 빠짐없이 이유와 함께 1줄씩. 반대로 상대 핵심 빅맨·림 보호 선수 결장처럼 오늘만 유리해진 사정도 적어요.
  - 오너는 팔레이(여러 픽 묶음)로 베팅해요. 그래서 배당보다 "오늘 이 상대와 넘길 확률"이 기준이에요. ladders 각 단계의 [11] = 오늘 확률(시즌 추정 + 매치업 보정), [12] = 맞대결에서 넘긴 횟수, `hN` = 맞대결 경기 수, `h2h` = 이번 시즌 맞대결 박스(지난 시즌은 쓰지 않음). 시즌보다 오늘 확률이 떨어진 인기 픽, 맞대결 0/N 인 픽은 꼭 적어요.
  - 2~4줄. 수치는 데이터에 있는 것만. 판정은 build_data.py 가 내린 그대로(바꾸지 않음), 대안(낮은 단계·콤보·다른 선수)을 제안하는 건 됨.
- grades: `archive/Y_analysis.json` 이 있으면 그 picks·lean 을 어제 박스로 채점.
- 원칙: 수치는 데이터·기사에 있는 것만. 가비지 규칙(스프레드 ≥13 언더·낮은 기준선, 스타 득점 오버 금지 / 8~12.5 중간). B2B·핵심 OUT 으로 인한 매치업 변화를 팀 카드 '공략'에 반영. 가성비·판정은 build_data.py 가 계산한 그대로 두고 바꾸지 않음. auto.json minutes(출전시간 변화) 상위 선수는 이유(부상 복귀·선발 변경·트레이드)를 확인해서 ctx 나 lean 에 반영. 시장 라인과 ESPN BPI 가 엇갈리면 사이드 패스.

## 4. 렌더 + 검증
- `PY render.py auto.json analysis.json nba_MMDD.html` → "분석 없는 경기: 없음" 확인(아니면 보완 후 다시).
- `node -e "const s=require('fs').readFileSync('nba_MMDD.html','ascii');new Function(s.split('<script>')[1].split('</script>')[0]);console.log('ok')"` → ok 확인.
- `mkdir -p archive && cp analysis.json archive/D_analysis.json`

## 5. 내보내기 (프롬프트가 지정한 모드만)
### 웹 모드
- `PY build_data.py D --snapshot auto.json` → archive/nba_D_props.json·nba_D_value.json (다음 날 '어제 라인 vs 실제'·'가성비 픽 채점' 리뷰용).
- `PY publish.py nba D nba_MMDD.html "<N경기 · 핵심 한 줄>"` → docs/ 에 등록. git push 는 하지 않음(run.sh 가 처리).
- 끝나면 `sent/D.txt` 에 "web" 기록(덮어써도 됨).
### 메일 모드 (Gmail send_message)
- 수신자: `recipients.txt` 의 주소들(이 파일 외 주소로는 보내지 않음).
- subject: "[NBA] M/D 데일리 위젯"
- htmlBody: 인라인 스타일 HTML. ① 오늘 경기 표(경기·시간·B2B/가비지·라인·방향) ② 어제 리뷰 3~4줄(원인+교훈+채점 요약) ③ "분석은 참고용, 결과 보장 없음".
- body: 한두 줄 plain text.
- attachments: [{"content":"sha256:<해시>:<nba_MMDD.html 절대경로, / 구분>","filename":"nba_MMDD.html","mimeType":"text/html"}] (해시: sha256sum 또는 shasum -a 256)
- 성공하면 `sent/D.txt` 에 메시지 id 기록. 실패하면 1회 재시도, 그래도 실패면 오류를 남기고 종료.
