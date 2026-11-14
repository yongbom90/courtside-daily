"""ESPN 공개 API → 분석용 텍스트 브리프.
usage: python espn_brief.py nba|nfl YYYYMMDD [review]
  review 지정 시 해당 날짜 경기를 '결과 리뷰' 모드로 출력 (쿼터 점수 + 선수 박스)
"""
import json, sys, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
SPORT = {"nba": "basketball/nba", "nfl": "football/nfl"}


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def base(lg):
    return f"https://site.api.espn.com/apis/site/v2/sports/{SPORT[lg]}"


def preview(lg, ev):
    s = get(f"{base(lg)}/summary?event={ev['id']}")
    c = ev["competitions"][0]
    print(f"\n### {ev['shortName']} | {c['status']['type']['shortDetail']}")
    for t in c["competitors"]:
        rec = ", ".join(r.get("summary", "") for r in t.get("records", []))
        print(f"  {t['homeAway']}: {t['team']['abbreviation']} ({rec})")
    for p in s.get("pickcenter", [])[:1]:
        print(f"  LINE[{p['provider']['name']}]: {p.get('details')} / O/U {p.get('overUnder')} "
              f"(O {p.get('overOdds')} U {p.get('underOdds')}) ML away {p['awayTeamOdds'].get('moneyLine')} home {p['homeTeamOdds'].get('moneyLine')}")
    pr = s.get("predictor") or {}
    if pr:
        print(f"  ESPN BPI 승률: home {pr['homeTeam'].get('gameProjection')}% / away {pr['awayTeam'].get('gameProjection')}%")
    for a in s.get("againstTheSpread", []):
        print(f"  ATS {a['team']['abbreviation']}: " + "; ".join(f"{r.get('type','')} {r.get('summary','')}" for r in a.get("records", [])))
    for l5 in s.get("lastFiveGames", []):
        g = " ".join(f"{e['gameResult']}{e['score']}{'@' if e['atVs']=='@' else 'v'}{e['opponent']['abbreviation']}" for e in l5["events"])
        print(f"  L5 {l5['team']['abbreviation']}: {g}")
    for inj in s.get("injuries", []):
        rows = []
        for i in inj.get("injuries", []):
            dt = i.get("details") or {}
            rows.append(f"{i['athlete']['displayName']}={i['status']}({dt.get('type','')} {dt.get('detail','')}".rstrip() + ")")
        print(f"  INJ {inj['team']['abbreviation']}: " + ("; ".join(rows) or "없음"))
    for t in s.get("boxscore", {}).get("teams", []):
        st = {x.get("name") or x.get("label"): x.get("displayValue") for x in t.get("statistics", [])}
        print(f"  STATS {t['team']['abbreviation']}: " + ", ".join(f"{k}={v}" for k, v in list(st.items())[:16]))


def review(lg, ev):
    s = get(f"{base(lg)}/summary?event={ev['id']}")
    c = ev["competitions"][0]
    print(f"\n### {ev['shortName']} FINAL | " + " - ".join(
        f"{t['team']['abbreviation']} {t.get('score')} [Q:{','.join(str(int(q['value'])) for q in t.get('linescores', []))}]"
        for t in c["competitors"]))
    for p in s.get("pickcenter", [])[:1]:
        print(f"  마감라인: {p.get('details')} O/U {p.get('overUnder')}")
    for t in s.get("boxscore", {}).get("players", []):
        for grp in t["statistics"]:
            keys = grp.get("keys") or grp.get("labels")
            out = []
            for a in grp.get("athletes", []):
                if not a.get("stats"):
                    continue
                d = dict(zip(keys, a["stats"]))
                if lg == "nba":
                    if d.get("minutes") in (None, "0", "--"):
                        continue
                    out.append(f"{a['athlete']['shortName']} {d.get('minutes')}m {d.get('points')}p {d.get('rebounds')}r {d.get('assists')}a "
                               f"FG{d.get('fieldGoalsMade-fieldGoalsAttempted')} 3P{d.get('threePointFieldGoalsMade-threePointFieldGoalsAttempted')} "
                               f"TO{d.get('turnovers')} {d.get('plusMinus')}")
                else:
                    out.append(f"{a['athlete']['shortName']} " + " ".join(f"{k}={v}" for k, v in d.items()))
            if out:
                print(f"  {t['team']['abbreviation']} {grp.get('name','')}: " + " | ".join(out))


if __name__ == "__main__":
    lg, date = sys.argv[1], sys.argv[2]
    sb = get(f"{base(lg)}/scoreboard?dates={date}")
    fn = review if len(sys.argv) > 3 else preview
    print(f"# {lg.upper()} {date} {'REVIEW' if fn is review else 'PREVIEW'} ({len(sb['events'])} games)")
    for ev in sb["events"]:
        try:
            fn(lg, ev)
        except Exception as e:  # 한 경기 실패가 전체를 막지 않게
            print(f"  !! {ev.get('shortName')} 실패: {e}")
