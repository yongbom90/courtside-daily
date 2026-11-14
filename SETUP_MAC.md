# Mac mini 자동화 설정 (한 번만)

웹앱 주소: https://<GitHub아이디>.github.io/courtside-daily/
Mac mini 가 정해진 시간에 데이터 수집 → Claude 분석 → docs/ 갱신 → git push → 웹앱 자동 반영.

## 1. GitHub 저장소 (Windows PC에서, 처음 한 번)
1. github.com 에서 새 저장소 `courtside-daily` 생성 (Public, README 없이).
2. 이 폴더를 그 저장소로 push (Claude 에게 "깃허브에 올려줘" 라고 하면 확인 후 진행).
3. 저장소 Settings → Pages → Source: "Deploy from a branch", Branch: `main` / 폴더 `/docs` → Save.
   1~2분 뒤 위 주소로 접속되면 성공.

## 2. Mac mini 준비 (터미널)
```
xcode-select --install                       # git, python3 (이미 있으면 건너뜀)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install node gh
curl -fsSL https://claude.ai/install.sh | bash   # Claude Code 설치
claude                                       # 처음 한 번 로그인 후 /exit
gh auth login                                # GitHub 로그인 (HTTPS 선택)
git clone https://github.com/<GitHub아이디>/courtside-daily.git ~/courtside-daily
cd ~/courtside-daily && bash mac/install.sh  # launchd 예약 등록
```

## 3. 잠자기 끄기
시스템 설정 → 에너지 → "디스플레이가 꺼져 있을 때 자동으로 잠자기 방지" 켜기.
(잠들면 예약 시간이 지나도 실행이 안 되고, 깨어날 때 한 번 실행됨)

## 4. 시간대
Mac mini 시간대가 미국 동부(ET)여야 오후 3시 = ET 3시. 시스템 설정 → 일반 → 날짜와 시간에서 확인.

## 5. 수동 테스트
```
cd ~/courtside-daily && rm -f sent/$(date +%Y%m%d).txt && bash mac/run.sh nba
tail -50 logs/nba_*.log
```
`deployed` 가 찍히고 웹앱에 오늘 날짜가 뜨면 끝.

## 예약 시간
- NBA: 매일 11:00(첫 경기가 18시 전이면 이때 처리) + 15:00
- NFL: 목·토·월 15:00 (RUN_NFL.md 가 생기면 자동으로 동작)

## 끄기
`launchctl unload ~/Library/LaunchAgents/com.courtside.nba.plist` (nfl 도 동일)
