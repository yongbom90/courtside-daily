"""위젯 HTML을 웹앱(site/)에 날짜별로 등록하고 pages.json 갱신.
usage: python publish.py nba|nfl YYYYMMDD widget.html "짧은 설명"
배포(git push)는 deploy.sh가 따로 한다."""
import json, os, shutil, sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
kind, d, src, label = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else ""
site = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
f = f"{kind}/{d}.html"
shutil.copyfile(src, os.path.join(site, f))
pj = os.path.join(site, "pages.json")
P = json.load(open(pj, encoding="utf-8")) if os.path.exists(pj) else {"nba": [], "nfl": []}
P[kind] = [x for x in P.get(kind, []) if x["d"] != d] + [{"d": d, "f": f, "t": label}]
P["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
json.dump(P, open(pj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"published {f} ({label}); {kind} 총 {len(P[kind])}개")
