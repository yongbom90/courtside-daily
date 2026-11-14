"""오늘 NBA 경기 자동 데이터(auto.json): 라인·부상·L5·선수 시즌 로그·80% 스탯·어제 결과.
usage: python build_data.py YYYYMMDD out.json
분석 문장(팀 분석, 방향, 리뷰)은 Claude가 analysis.json에 따로 쓴다.
"""
import json, os, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")
B = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
os.makedirs(CACHE, exist_ok=True)


def get(url, cache_key=None):
    p = os.path.join(CACHE, cache_key) if cache_key else None
    if p and os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.load(r)
    if p:
        json.dump(d, open(p, "w", encoding="utf-8"))
    return d


def summary(eid, final=True):
    # 끝난 경기만 캐시 (예정 경기는 부상·라인이 계속 바뀜)
    return get(f"{B}/summary?event={eid}", f"sum_{eid}.json" if final else None)


def box_rows(s):
    out = {}
    for t in s.get("boxscore", {}).get("players", []):
        g = t["statistics"][0]
        rows = []
        for a in g.get("athletes", []):
            if not a.get("stats"):
                continue
            d = dict(zip(g["keys"], a["stats"]))
            try:
                mins = int(d.get("minutes") or 0)
            except ValueError:
                mins = 0
            if mins <= 0:
                continue
            tpm = (d.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted") or "0-0").split("-")[0]
            rows.append({"n": a["athlete"]["displayName"], "min": mins,
                         "PTS": int(d.get("points") or 0), "REB": int(d.get("rebounds") or 0),
                         "AST": int(d.get("assists") or 0), "3PM": int(tpm or 0)})
        out[t["team"]["abbreviation"]] = rows
    return out


THR = {"PTS": [10, 12, 15, 18, 20, 22, 25, 28, 30, 35], "REB": [4, 5, 6, 8, 10, 12, 14],
       "AST": [3, 4, 5, 6, 8, 10], "3PM": [1, 2, 3, 4]}


def s80(name, team, game, logs, spread):
    """시즌 로그(최신순)에서 80%+ 적중하는 가장 높은 기준선. 판정 규칙은 v2 위젯과 동일."""
    res, N = [], len(logs)
    if N < 5 or sum(l["min"] for l in logs[:5]) / 5 < 20:
        return res
    for cat, ths in THR.items():
        best = None
        for th in ths:
            hit = sum(l[cat] >= th for l in logs)
            if hit / N >= 0.8:
                best = (th, hit)
        if not best:
            continue
        th, hit = best
        last = [l[cat] for l in logs[:5]]
        h5 = sum(v >= th for v in last)
        avg = round(sum(l[cat] for l in logs) / N, 1)
        a5 = round(sum(last) / 5, 1)
        rate = round(100 * hit / N)
        flags = []
        if a5 < avg * 0.9:
            flags.append("최근 폼 하락")
        if h5 < 4:
            flags.append(f"최근 5경기 {h5}/5")
        if spread >= 12 and a5 < th * 1.25:
            flags.append(f"스프레드 {spread} 블로아웃 → 출전시간 감소 위험")
        if rate >= 90 and h5 == 5 and a5 >= th * 1.25 and not flags:
            rec = "top"
        elif rate >= 80 and h5 >= 4 and not flags:
            rec = "rec"
        else:
            rec = "caution"
        res.append({"n": name, "t": team, "g": game, "cat": cat, "th": th, "hit": hit, "N": N, "rate": rate,
                    "h5": h5, "avg": avg, "a5": a5, "last": last, "rec": rec, "flags": flags})
    return res


def main(date, out):
    sb = get(f"{B}/scoreboard?dates={date}")
    games, teams = [], {}
    for ev in sb["events"]:
        c = ev["competitions"][0]
        s = summary(ev["id"], final=False)
        home = next(t for t in c["competitors"] if t["homeAway"] == "home")
        away = next(t for t in c["competitors"] if t["homeAway"] == "away")
        A, H = away["team"]["abbreviation"], home["team"]["abbreviation"]
        pc = (s.get("pickcenter") or [{}])[0]
        pr = s.get("predictor") or {}
        g = {"id": ev["id"], "m": f"{A} @ {H}", "away": A, "home": H,
             "time": c["status"]["type"]["shortDetail"].split(" - ")[-1],
             "rec": {t["team"]["abbreviation"]: (t.get("records") or [{}])[0].get("summary", "") for t in c["competitors"]},
             "sp": pc.get("details"), "tot": pc.get("overUnder"), "abs": abs(pc.get("spread") or 0),
             "ml": {A: pc.get("awayTeamOdds", {}).get("moneyLine"), H: pc.get("homeTeamOdds", {}).get("moneyLine")},
             "bpi": {H: pr.get("homeTeam", {}).get("gameProjection"), A: pr.get("awayTeam", {}).get("gameProjection")},
             "inj": {A: [], H: []}, "l5": {}, "stats": {}}
        for inj in s.get("injuries", []):
            g["inj"][inj["team"]["abbreviation"]] = [
                {"n": i["athlete"]["displayName"], "s": i["status"], "d": (i.get("details") or {}).get("type", "")}
                for i in inj.get("injuries", [])]
        for l5 in s.get("lastFiveGames", []):
            g["l5"][l5["team"]["abbreviation"]] = [
                {"r": e["gameResult"], "sc": e["score"], "at": e["atVs"], "opp": e["opponent"]["abbreviation"], "d": e["gameDate"][:10]}
                for e in l5["events"]]
        for t in s.get("boxscore", {}).get("teams", []):
            g["stats"][t["team"]["abbreviation"]] = {x.get("name"): x.get("displayValue") for x in t.get("statistics", [])}
        games.append(g)
        for ab, t in ((A, away), (H, home)):
            teams[ab] = (t["team"]["id"], g["m"], g["abs"])

    # 팀별 시즌 완료 경기 → 선수 로그
    ev_by_team = {}
    for ab, (tid, _, _) in teams.items():
        sch = get(f"{B}/teams/{tid}/schedule")
        ev_by_team[ab] = [(e["date"], e["id"]) for e in sch["events"] if e["competitions"][0]["status"]["type"]["completed"]]
    all_ev = sorted({e for v in ev_by_team.values() for _, e in v})
    with ThreadPoolExecutor(8) as ex:
        sums = dict(zip(all_ev, ex.map(summary, all_ev)))
    logs = {}
    for ab, evs in ev_by_team.items():
        for _, e in sorted(evs, reverse=True):
            for row in box_rows(sums[e]).get(ab, []):
                logs.setdefault((ab, row["n"]), []).append(row)

    out_names = {i["n"] for g in games for v in g["inj"].values() for i in v if i["s"] in ("Out", "Doubtful")}
    q_names = {i["n"] for g in games for v in g["inj"].values() for i in v if i["s"] not in ("Out", "Doubtful")}
    stat80, form = [], {}
    for (ab, name), L in logs.items():
        if name in out_names:
            continue
        rows = s80(name, ab, teams[ab][1], L, teams[ab][2])
        for r in rows:
            if name in q_names:
                r["flags"].append("부상 리포트 DTD/Q")
                r["rec"] = "caution"
        stat80 += rows
        l5 = L[:5]
        mn = sum(x["min"] for x in l5) / len(l5)
        if len(L) >= 3 and mn >= 20:
            form.setdefault(ab, []).append({"n": name, "gp": len(L), "min5": round(mn, 1),
                **{k + "5": round(sum(x[k] for x in l5) / len(l5), 1) for k in ("PTS", "REB", "AST", "3PM")},
                **{k + "s": round(sum(x[k] for x in L) / len(L), 1) for k in ("PTS", "REB", "AST")},
                "last": {k: L[0][k] for k in ("min", "PTS", "REB", "AST", "3PM")}})
    for v in form.values():
        v.sort(key=lambda x: -x["PTS5"])

    prev = (datetime.strptime(date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    results = []
    for ev in get(f"{B}/scoreboard?dates={prev}")["events"]:
        c = ev["competitions"][0]
        if not c["status"]["type"]["completed"]:
            continue
        s = summary(ev["id"])
        pc = (s.get("pickcenter") or [{}])[0]
        results.append({"m": ev["shortName"], "close": f"{pc.get('details', '')} / {pc.get('overUnder', '')}",
                        "score": {t["team"]["abbreviation"]: int(t["score"]) for t in c["competitors"]},
                        "q": {t["team"]["abbreviation"]: [int(q["value"]) for q in t.get("linescores", [])] for t in c["competitors"]},
                        "headline": (s.get("article") or {}).get("headline", "")})

    json.dump({"date": date, "prev": prev, "games": games, "stat80": stat80, "form": form, "results": results, "nfl": [],  # NFL은 build_nfl.py로 분리

               "built": datetime.now().strftime("%Y-%m-%d %H:%M")}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"games={len(games)} stat80={len(stat80)} players={sum(len(v) for v in form.values())} results={len(results)} events={len(all_ev)}")


def et(utc):
    """UTC → 미국 동부(서머타임 반영). zoneinfo 데이터가 없으면(Windows) EST 고정."""
    try:
        from zoneinfo import ZoneInfo
        from datetime import timezone
        return utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:
        return utc - timedelta(hours=5)


def tips(date):
    """오늘 NBA 팁오프 시각들(ET, 자정부터 분), 정렬·중복 제거."""
    ev = get(f"{B}/scoreboard?dates={date}")["events"]
    return sorted({(lambda t: t.hour * 60 + t.minute)(et(datetime.strptime(e["date"], "%Y-%m-%dT%H:%MZ"))) for e in ev})


def first_tip(date):
    """오늘 첫 NBA 팁오프 시각(ET 시, 0~23). 경기 없으면 -1."""
    t = tips(date)
    return t[0] // 60 if t else -1


if __name__ == "__main__":
    if sys.argv[2] == "--first-tip":
        print(first_tip(sys.argv[1]))
    elif sys.argv[2] == "--tips":
        print(" ".join(str(t) for t in tips(sys.argv[1])))
    else:
        main(sys.argv[1], sys.argv[2])
