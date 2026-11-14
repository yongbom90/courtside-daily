"""NFL 자동 데이터(nfl_auto.json): 이번 실행 구간 경기·라인·부상·날씨·팀 스탯 + 선수 prop 라인 대비 시즌 기록 + 지난 경기 결과.
usage: python build_nfl.py YYYYMMDD out.json
       python build_nfl.py YYYYMMDD --count   (이번 구간 경기 수만 출력. 0이면 실행할 필요 없음)
데이터형 위젯: 픽·방향은 만들지 않는다. 숫자는 전부 ESPN(라인은 DraftKings via ESPN)에서 온 값.
분석 문장(경기 맥락, 팀 카드, 리뷰 원인)은 Claude가 nfl_analysis.json에 따로 쓴다.
"""
import glob, json, os, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")
B = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")
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
    return get(f"{B}/summary?event={eid}", f"nfl_sum_{eid}.json" if final else None)


def et(iso):
    """ESPN UTC 시각 → 미국 동부 시각(naive). zoneinfo 없이 DST 규칙으로 계산(Windows tzdata 문제 회피)."""
    u = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
    y = u.year
    mar = datetime(y, 3, 8 + (6 - datetime(y, 3, 8).weekday()) % 7, 7)   # 3월 둘째 일요일 2시 EST = 7시 UTC
    nov = datetime(y, 11, 1 + (6 - datetime(y, 11, 1).weekday()) % 7, 6)  # 11월 첫째 일요일 2시 EDT = 6시 UTC
    return u - timedelta(hours=4 if mar <= u < nov else 5)


def windows(date):
    """(이번 슬레이트 날짜들, 결과 리뷰 날짜들). 예약 실행: 목·토·월 15시."""
    d = datetime.strptime(date, "%Y%m%d")
    wd = d.weekday()  # 월0 … 일6
    ahead, back = {3: (2, 3), 5: (3, 2), 0: (2, 2)}.get(wd, (3, 4))
    f = lambda k: (d + timedelta(days=k)).strftime("%Y%m%d")
    return [f(k) for k in range(ahead)], [f(-k) for k in range(back, 0, -1)]


# prop 종류 → (짧은 코드, 한국어)
PROP = {
    "Total Passing Yards": ("PYD", "패스 야드"), "Total Pass Completions": ("CMP", "패스 성공"),
    "Total Passing Attempts": ("ATT", "패스 시도"), "Total Passing Touchdowns": ("PTD", "패스 TD"),
    "Total Passing Interceptions": ("INT", "인터셉션"), "Total Carries": ("CAR", "캐리"),
    "Total Rushing Yards": ("RYD", "러시 야드"), "Total Receptions": ("REC", "리셉션"),
    "Total Receiving Yards": ("REYD", "리시빙 야드"), "Total Rushing Plus Receiving Yards": ("RRY", "러시+리시빙 야드"),
    "Total Passing Plus Rushing Yards": ("PRY", "패스+러시 야드"),
}
PASS_CATS, REC_CATS = {"PYD", "CMP", "ATT", "PTD", "INT", "PRY"}, {"REC", "REYD"}


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return 0.0


def box_rows(s):
    """경기 박스 → {팀: {선수id: 기록}}. 패스·러시·리시빙 중 하나라도 기록이 있으면 출전으로 본다."""
    out = {}
    for t in s.get("boxscore", {}).get("players", []):
        ab = t["team"]["abbreviation"]
        rows = out.setdefault(ab, {})
        for g in t.get("statistics", []):
            if g["name"] not in ("passing", "rushing", "receiving"):
                continue
            for a in g.get("athletes", []):
                d = dict(zip(g["keys"], a.get("stats", [])))
                r = rows.setdefault(a["athlete"]["id"], {"n": a["athlete"]["displayName"]})
                if g["name"] == "passing":
                    c, at = (d.get("completions/passingAttempts") or "0/0").split("/")
                    r.update(CMP=num(c), ATT=num(at), PYD=num(d.get("passingYards")), PTD=num(d.get("passingTouchdowns")),
                             INT=num(d.get("interceptions")))
                elif g["name"] == "rushing":
                    r.update(CAR=num(d.get("rushingAttempts")), RYD=num(d.get("rushingYards")), RTD=num(d.get("rushingTouchdowns")))
                else:
                    r.update(REC=num(d.get("receptions")), REYD=num(d.get("receivingYards")), RETD=num(d.get("receivingTouchdowns")),
                             TGT=num(d.get("receivingTargets")))
    for rows in out.values():
        for r in rows.values():
            for k in ("CMP", "ATT", "PYD", "PTD", "INT", "CAR", "RYD", "RTD", "REC", "REYD", "RETD", "TGT"):
                r.setdefault(k, 0.0)
            r["RRY"], r["PRY"], r["TD"] = r["RYD"] + r["REYD"], r["PYD"] + r["RYD"], r["RTD"] + r["RETD"]
    return out


def ats(details, total, score):
    """마감 라인 대비 결과 문자열. details 예: 'KC -3', 'EVEN'."""
    res = []
    try:
        fav, sp = details.rsplit(" ", 1)
        other = next(t for t in score if t != fav)
        margin = score[fav] - score[other] + float(sp)
        res.append(f"{fav} {sp} " + ("커버" if margin > 0 else "푸시" if margin == 0 else "미커버"))
    except (ValueError, StopIteration, AttributeError, KeyError):
        pass
    if total:
        pts = sum(score.values())
        res.append(f"합계 {pts} " + ("오버" if pts > total else "언더" if pts < total else "푸시") + f" ({total})")
    return " · ".join(res)


def props_for(eid):
    url = f"{CORE}/events/{eid}/competitions/{eid}/odds/100/propBets?limit=1000"
    try:
        items = get(url).get("items", [])
    except Exception as e:  # prop 없는 경기(라인 미오픈)
        print(f"  props {eid}: {e}", file=sys.stderr)
        return {}, set()
    lines, td = {}, set()
    for i in items:
        name = i.get("type", {}).get("name", "").replace(" (incl. overtime)", "")
        aid = i.get("athlete", {}).get("$ref", "").split("/athletes/")[-1].split("?")[0]
        if not aid:
            continue
        if name == "Anytime Touchdown Scorer":
            td.add(aid)
        elif name in PROP and (i.get("current") or {}).get("target"):
            lines[(aid, PROP[name][0])] = (i["current"]["target"]["value"], (i.get("open") or {}).get("target", {}).get("value"))
    return lines, td


def main(date, out):
    slate_days, back_days = windows(date)
    games, teams = [], {}
    for day in slate_days:
        for ev in get(f"{B}/scoreboard?dates={day}")["events"]:
            c = ev["competitions"][0]
            if c["status"]["type"]["state"] != "pre" or any(g["id"] == ev["id"] for g in games):
                continue
            s = summary(ev["id"], final=False)
            home = next(t for t in c["competitors"] if t["homeAway"] == "home")
            away = next(t for t in c["competitors"] if t["homeAway"] == "away")
            A, H = away["team"]["abbreviation"], home["team"]["abbreviation"]
            pc = (s.get("pickcenter") or [{}])[0]
            pr = s.get("predictor") or {}
            gi = s.get("gameInfo", {})
            w = gi.get("weather") or {}
            k = et(ev["date"])
            g = {"id": ev["id"], "m": f"{A} @ {H}", "away": A, "home": H, "kick": k.strftime("%Y-%m-%d %H:%M"),
                 "time": k.strftime("%a %-I:%M%p") if os.name != "nt" else k.strftime("%a %I:%M%p"),
                 "rec": {t["team"]["abbreviation"]: (t.get("records") or [{}])[0].get("summary", "") for t in c["competitors"]},
                 "sp": pc.get("details"), "tot": pc.get("overUnder"), "abs": abs(pc.get("spread") or 0),
                 "ml": {A: pc.get("awayTeamOdds", {}).get("moneyLine"), H: pc.get("homeTeamOdds", {}).get("moneyLine")},
                 "bpi": {H: pr.get("homeTeam", {}).get("gameProjection"), A: pr.get("awayTeam", {}).get("gameProjection")},
                 "venue": gi.get("venue", {}).get("fullName", ""), "indoor": bool(c.get("venue", {}).get("indoor")),
                 "grass": gi.get("venue", {}).get("grass"),
                 "wx": None if c.get("venue", {}).get("indoor") else {"temp": w.get("temperature"), "gust": w.get("gust"), "prec": w.get("precipitation")},
                 "inj": {A: [], H: []}, "l5": {}, "stats": {}, "tid": {A: away["team"]["id"], H: home["team"]["id"]}}
            for inj in s.get("injuries", []):
                g["inj"][inj["team"]["abbreviation"]] = [
                    {"n": i["athlete"]["displayName"], "id": i["athlete"].get("id"), "s": i["status"],
                     "pos": i["athlete"].get("position", {}).get("abbreviation", ""), "d": (i.get("details") or {}).get("type", "")}
                    for i in inj.get("injuries", [])]
            for l5 in s.get("lastFiveGames", []):
                g["l5"][l5["team"]["abbreviation"]] = [
                    {"r": e["gameResult"], "sc": e["score"], "at": e["atVs"], "opp": e["opponent"]["abbreviation"], "d": e["gameDate"][:10]}
                    for e in l5["events"]]
            for t in s.get("boxscore", {}).get("teams", []):
                g["stats"][t["team"]["abbreviation"]] = {x.get("name"): num(x.get("displayValue")) for x in t.get("statistics", [])}
            games.append(g)
            for ab, t in ((A, away), (H, home)):
                teams[ab] = (t["team"]["id"], g)
    games.sort(key=lambda g: g["kick"])

    # 팀별 이번 시즌 완료 경기 → 선수 경기 로그(최신순)
    ev_by_team = {}
    for ab, (tid, _) in teams.items():
        sch = get(f"{B}/teams/{tid}/schedule")
        ev_by_team[ab] = [(e["date"], e["id"]) for e in sch["events"] if e["competitions"][0]["status"]["type"]["completed"]]
    all_ev = sorted({e for v in ev_by_team.values() for _, e in v})
    with ThreadPoolExecutor(8) as ex:
        sums = dict(zip(all_ev, ex.map(summary, all_ev)))
        prop_res = dict(zip([g["id"] for g in games], ex.map(props_for, [g["id"] for g in games])))
    logs, who = {}, {}
    for ab, evs in ev_by_team.items():
        for _, e in sorted(evs, reverse=True):
            for aid, row in box_rows(sums[e]).get(ab, {}).items():
                logs.setdefault((ab, aid), []).append(row)
                who.setdefault(aid, (ab, row["n"]))

    # 로스터: 포지션(+로그가 없는 선수 이름)
    pos = {}
    with ThreadPoolExecutor(8) as ex:
        rosters = dict(zip(teams, ex.map(lambda ab: get(f"{B}/teams/{teams[ab][0]}/roster"), teams)))
    for ab, r in rosters.items():
        for grp in r.get("athletes", []):
            for a in grp.get("items", []):
                pos[a["id"]] = (ab, a["displayName"], a.get("position", {}).get("abbreviation", ""))

    # 이번 슬레이트 평균(상대 허용 수치 비교용)
    allow = {k: [g["stats"][t].get(k) for g in games for t in g["stats"] if g["stats"][t].get(k)]
             for k in ("passingYardsPerGameAllowed", "rushingYardsPerGameAllowed")}
    avg_allow = {k: round(sum(v) / len(v), 1) if v else None for k, v in allow.items()}

    props, tds = [], []
    for g in games:
        lines, td_ids = prop_res.get(g["id"], ({}, set()))
        inj = {i["id"]: i for v in g["inj"].values() for i in v}
        qb_out = {t: [i["n"] for i in v if i["pos"] == "QB" and i["s"] in ("Out", "Doubtful")] for t, v in g["inj"].items()}
        for (aid, cat), (line, opn) in lines.items():
            ab, name, ps = pos.get(aid, (None, None, ""))
            if ab not in (g["away"], g["home"]):
                continue
            if aid in inj and inj[aid]["s"] in ("Out", "Doubtful"):
                continue
            L = logs.get((ab, aid), [])
            opp = g["home"] if ab == g["away"] else g["away"]
            ok = "rushingYardsPerGameAllowed" if cat in ("CAR", "RYD") else "passingYardsPerGameAllowed"
            row = {"id": aid, "n": name, "t": ab, "pos": ps, "g": g["m"], "gid": g["id"], "cat": cat, "line": line, "open": opn,
                   "opp": opp, "oppAllow": g["stats"].get(opp, {}).get(ok), "slateAllow": avg_allow.get(ok), "N": len(L)}
            vals = [r[cat] for r in L]
            flags = []
            if vals:
                hit = sum(v > line for v in vals)
                last = vals[:5]
                row.update(hit=hit, rate=round(100 * hit / len(vals)), last=last, h5=sum(v > line for v in last),
                           avg=round(sum(vals) / len(vals), 1), a5=round(sum(last) / len(last), 1), mx=max(vals), mn=min(vals))
                if len(vals) >= 5 and row["a5"] < row["avg"] * 0.85:
                    flags.append("최근 5경기 평균이 시즌보다 15%↓")
                elif len(vals) >= 5 and row["a5"] > row["avg"] * 1.15:
                    flags.append("최근 5경기 평균이 시즌보다 15%↑")
            if len(vals) < 4:
                flags.append(f"표본 적음({len(vals)}경기)")
            if opn is not None and opn != line:
                flags.append(f"라인 이동 {opn} → {line}")
            if aid in inj:
                flags.append(f"부상 리포트 {inj[aid]['s']}")
            if cat in PASS_CATS | REC_CATS | {"RRY"} and qb_out.get(ab) and ps != "QB":
                flags.append(f"팀 QB 결장: {', '.join(qb_out[ab])}")
            wx = g["wx"] or {}
            if cat in PASS_CATS | REC_CATS and (wx.get("gust") or 0) >= 15:
                flags.append(f"돌풍 {wx['gust']}mph 예보")
            if wx.get("temp") is not None and wx["temp"] <= 32:
                flags.append(f"기온 {wx['temp']}°F")
            row["flags"] = flags
            props.append(row)
        for aid in td_ids:
            ab, name, ps = pos.get(aid, (None, None, ""))
            if ab not in (g["away"], g["home"]) or ps in ("QB",) or (aid in inj and inj[aid]["s"] in ("Out", "Doubtful")):
                continue
            L = logs.get((ab, aid), [])
            if not L:
                continue
            t = [int(r["TD"]) for r in L]
            tds.append({"id": aid, "n": name, "t": ab, "pos": ps, "g": g["m"], "N": len(t), "hit": sum(x > 0 for x in t),
                        "rate": round(100 * sum(x > 0 for x in t) / len(t)), "tot": sum(t), "last": t[:5],
                        "q": inj[aid]["s"] if aid in inj else ""})

    # 역할(사용량) 변화: 최근 3경기 타깃·캐리 vs 시즌
    usage = []
    prop_ids = {(p["t"], p["id"]) for p in props}
    for (ab, aid), L in logs.items():
        if (ab, aid) not in prop_ids or len(L) < 4:
            continue
        ps = pos.get(aid, ("", "", ""))[2]
        if ps not in ("RB", "WR", "TE"):
            continue
        tg, cr = [r["TGT"] for r in L], [r["CAR"] for r in L]
        u = {"n": L[0]["n"], "t": ab, "pos": ps, "g": teams[ab][1]["m"], "N": len(L),
             "tgt": round(sum(tg) / len(tg), 1), "tgt3": round(sum(tg[:3]) / 3, 1),
             "car": round(sum(cr) / len(cr), 1), "car3": round(sum(cr[:3]) / 3, 1),
             "lastT": tg[:5], "lastC": cr[:5]}
        u["d"] = round((u["tgt3"] - u["tgt"]) + (u["car3"] - u["car"]), 1)
        usage.append(u)
    usage.sort(key=lambda u: -abs(u["d"]))

    # 지난 경기 결과 + 지난 실행의 prop 라인 vs 실제 기록
    results, final_box = [], {}
    for day in back_days:
        for ev in get(f"{B}/scoreboard?dates={day}")["events"]:
            c = ev["competitions"][0]
            if not c["status"]["type"]["completed"]:
                continue
            s = summary(ev["id"])
            pc = (s.get("pickcenter") or [{}])[0]
            score = {t["team"]["abbreviation"]: int(t["score"]) for t in c["competitors"]}
            results.append({"id": ev["id"], "m": ev["shortName"], "close": f"{pc.get('details', '')} / {pc.get('overUnder', '')}",
                            "res": ats(pc.get("details"), pc.get("overUnder"), score), "score": score,
                            "q": {t["team"]["abbreviation"]: [int(q["value"]) for q in t.get("linescores", [])] for t in c["competitors"]},
                            "headline": (s.get("article") or {}).get("headline", "")})
            final_box[ev["id"]] = {aid: r for rows in box_rows(s).values() for aid, r in rows.items()}
    prop_review = []
    prev_files = sorted(f for f in glob.glob(os.path.join(HERE, "archive", "nfl_*_props.json")) if os.path.basename(f)[4:12] < date)
    if prev_files:
        for p in json.load(open(prev_files[-1], encoding="utf-8")):
            box = final_box.get(p["gid"])
            if box is None:
                continue
            act = box.get(p["id"], {}).get(p["cat"], 0.0) if p["id"] in box else None
            if act is None:
                prop_review.append({**{k: p[k] for k in ("n", "t", "cat", "line", "g")}, "act": None, "r": "-", "rate": p.get("rate")})
            else:
                prop_review.append({**{k: p[k] for k in ("n", "t", "cat", "line", "g")}, "act": act,
                                    "r": "O" if act > p["line"] else "U", "rate": p.get("rate")})

    for g in games:
        g.pop("tid", None)
    A = {"date": date, "slate": slate_days, "back": back_days, "games": games, "props": props, "td": tds, "usage": usage[:30],
         "results": results, "propReview": prop_review, "propReviewFrom": os.path.basename(prev_files[-1])[4:12] if prev_files else None,
         "cats": {v[0]: v[1] for v in PROP.values()}, "avgAllow": avg_allow,
         "built": datetime.now().strftime("%Y-%m-%d %H:%M")}
    json.dump(A, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"games={len(games)} props={len(props)} td={len(tds)} usage={len(usage)} results={len(results)} "
          f"propReview={len(prop_review)} events={len(all_ev)}")


def count(date):
    days, _ = windows(date)
    return sum(e["competitions"][0]["status"]["type"]["state"] == "pre"
               for d in days for e in get(f"{B}/scoreboard?dates={d}")["events"])


if __name__ == "__main__":
    if sys.argv[2] == "--count":
        print(count(sys.argv[1]))
    else:
        main(sys.argv[1], sys.argv[2])
