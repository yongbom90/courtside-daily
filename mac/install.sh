#!/bin/bash
# launchd 등록 (sudo 불필요). 이 폴더 위치를 plist 에 넣어서 ~/Library/LaunchAgents 에 설치.
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
for k in nba nfl inj; do
  sed "s#__DIR__#$DIR#" "$DIR/mac/com.courtside.$k.plist" > "$HOME/Library/LaunchAgents/com.courtside.$k.plist"
  launchctl unload "$HOME/Library/LaunchAgents/com.courtside.$k.plist" 2>/dev/null || true
  launchctl load "$HOME/Library/LaunchAgents/com.courtside.$k.plist"
  echo "loaded com.courtside.$k"
done
launchctl list | grep courtside
