#!/usr/bin/env python3
"""🏀 First Basket: 점프볼·첫 필드골 기록 수집 + 오늘 예측.
usage: python3 fb.py YYYYMMDD [out.json]   (build_data.py 의 캐시·함수 재사용)"""
import json, sys, os
from datetime import datetime, timedelta
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("bd", os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_data.py"))
bd = importlib.util.module_from_spec(_spec); _argv = sys.argv; sys.argv = ["x"]; _spec.loader.exec_module(bd); sys.argv = _argv
SB = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates="


def scoreboard(d, final):
    return bd.get(SB + d, f"sb_fb_{d}.json" if final else None)


def is_fg(p):
    return p.get("scoringPlay") and (p.get("scoreValue") or 0) >= 2 and "free throw" not in (p.get("text") or "").lower()


def parse_game(eid):
    """한 경기 → {teams:{tid:abbr}, jump:[a,b], tipTeam, firstFG:(aid,tid,text), teamFirst:{tid:(aid,name)}, firstTeam}"""
    s = bd.summary(eid)
    a2t, names, teams = {}, {}, {}
    for t in s.get("boxscore", {}).get("players", []):
        tid = t["team"]["id"]; teams[tid] = t["team"]["abbreviation"]
        for a in t["statistics"][0].get("athletes", []):
            a2t[a["athlete"]["id"]] = tid; names[a["athlete"]["id"]] = a["athlete"]["displayName"]
    plays = s.get("plays", [])
    if not plays or len(teams) != 2:
        return None
    g = {"id": eid, "teams": teams, "names": {}, "jump": None, "tipTeam": None, "firstFG": None, "teamFirst": {}, "firstTeam": None}
    jb = next((p for p in plays[:5] if (p.get("type", {}).get("text") or "").lower().startswith("jump")), None)
    if jb:
        ids = [x.get("athlete", {}).get("id") for x in jb.get("participants", [])]
        g["jump"] = [i for i in ids[:2] if i]
        if len(ids) >= 3 and ids[2] in a2t:
            g["tipTeam"] = a2t[ids[2]]
    for p in plays:
        if p.get("scoringPlay") and g["firstTeam"] is None:
            g["firstTeam"] = (p.get("team") or {}).get("id")
        if is_fg(p):
            aid = (p.get("participants") or [{}])[0].get("athlete", {}).get("id")
            tid = a2t.get(aid, (p.get("team") or {}).get("id"))
            if g["firstFG"] is None:
                g["firstFG"] = (aid, tid, p.get("text"))
            if tid not in g["teamFirst"]:
                g["teamFirst"][tid] = aid
            if len(g["teamFirst"]) == 2:
                break
    played = {}
    for t in s.get("boxscore", {}).get("players", []):
        for a in t["statistics"][0].get("athletes", []):
            if a.get("stats") and not a.get("didNotPlay"):
                played[a["athlete"]["id"]] = t["team"]["id"]
    g["played"] = played
    g["names"] = names
    return g


def season_games(date):
    d = datetime.strptime(bd.SEASON_START, "%Y%m%d"); end = datetime.strptime(date, "%Y%m%d")
    out = []
    while d < end:
        ds = d.strftime("%Y%m%d")
        for e in scoreboard(ds, True).get("events", []):
            if e["status"]["type"].get("completed"):
                out.append((ds, e["id"]))
        d += timedelta(days=1)
    return out


def am(p):  # 확률 → 미국식
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def main(date, out=None):
    today = scoreboard(date, False).get("events", [])
    games = []
    for e in today:
        c = e["competitions"][0]["competitors"]
        home = next(x for x in c if x["homeAway"] == "home"); away = next(x for x in c if x["homeAway"] == "away")
        games.append({"id": e["id"], "m": f'{away["team"]["abbreviation"]} @ {home["team"]["abbreviation"]}',
                      "t": {away["team"]["id"]: away["team"]["abbreviation"], home["team"]["id"]: home["team"]["abbreviation"]}})
    want = {tid for g in games for tid in g["t"]}
    yday = (datetime.strptime(date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    parsed = []
    for ds, eid in season_games(date):
        try:
            pg = parse_game(eid)
        except Exception:
            pg = None
        if pg:
            pg["date"] = ds; parsed.append(pg)
    # 어제 결과
    review = []
    for pg in [p for p in parsed if p["date"] == yday]:
        ab = list(pg["teams"].values())
        f = pg["firstFG"]
        review.append({"g": " vs ".join(ab), "jump": [pg["names"].get(i, "?") for i in (pg["jump"] or [])],
                       "tip": pg["teams"].get(pg["tipTeam"], "?"), "first": pg["names"].get(f[0], "?") if f else "?",
                       "firstTeam": pg["teams"].get(f[1], "?") if f else "?", "how": (f[2] if f else ""), "ab": sorted(ab)})
    # 어제 위젯 픽 채점 (archive/nba_Y_fb.json)
    grades = []
    try:
        prev = json.load(open(os.path.join(HERE, "archive", f"nba_{yday}_fb.json"), encoding="utf-8"))
        for pgm in prev.get("games", []):
            ab = sorted(pgm["m"].split(" @ "))
            r = next((x for x in review if x["ab"] == ab), None)
            if not r:
                continue
            for pk in pgm.get("picks", [])[:3]:
                grades.append({"m": pgm["m"], "n": pk["n"], "t": pk["t"], "p": pk["p"], "dk": pk.get("dk"),
                               "hit": pk["n"] == r["first"], "act": r["first"]})
    except FileNotFoundError:
        pass
    # 팀별 시즌 기록
    team = {}
    for tid in want:
        gs = [p for p in parsed if tid in p["teams"]]
        n = len(gs)
        tipw = sum(1 for p in gs if p["tipTeam"] == tid); tipn = sum(1 for p in gs if p["tipTeam"])
        first = sum(1 for p in gs if p["firstFG"] and p["firstFG"][1] == tid)
        jumpers = {}
        for p in gs:
            for j in (p["jump"] or []):
                if p["played"].get(j) == tid:
                    jumpers[j] = jumpers.get(j, 0) + 1
        tf, gp, gameFirst, names = {}, {}, {}, {}
        for p in gs:
            names.update(p["names"])
            for aid, t in p["played"].items():
                if t == tid:
                    gp[aid] = gp.get(aid, 0) + 1
            a = p["teamFirst"].get(tid)
            if a:
                tf[a] = tf.get(a, 0) + 1
            if p["firstFG"] and p["firstFG"][1] == tid:
                gameFirst[p["firstFG"][0]] = gameFirst.get(p["firstFG"][0], 0) + 1
        jp = max(jumpers, key=jumpers.get) if jumpers else None
        team[tid] = {"n": n, "tip": [tipw, tipn], "first": first, "jumper": [names.get(jp, "?"), jumpers.get(jp, 0)] if jp else None,
                     "tf": sorted([[names.get(a, a), c, gp.get(a, 0), gameFirst.get(a, 0), a] for a, c in tf.items()], key=lambda x: -x[1])}
    # 오늘 결장자 (auto.json)
    out_ids = set()
    try:
        auto = json.load(open(os.path.join(HERE, "auto.json")))
        for g in auto.get("games", []):
            for lst in g.get("inj", {}).values():
                for i in lst:
                    if i.get("s") in ("Out", "Doubtful") and i.get("id"):
                        out_ids.add(i["id"])
    except Exception:
        pass
    res = []
    for g in games:
        (ta, aa), (tb, ab) = list(g["t"].items())
        A, B = team[ta], team[tb]
        ra = A["first"] / A["n"] if A["n"] else 0.5; rb = B["first"] / B["n"] if B["n"] else 0.5
        pa = min(0.75, max(0.25, 0.5 + 0.5 * ((ra - 0.5) + (0.5 - rb))))
        dk, dkteam = {}, {}
        try:
            for i in bd.prop_items(g["id"]):
                nm = i.get("type", {}).get("name")
                v = (i.get("odds") or {}).get("american", {}).get("value")
                if nm == "First Basket" and v:
                    dk[i["athlete"]["$ref"].split("/athletes/")[-1].split("?")[0]] = int(v.replace("+", ""))
                if nm == "First Team to Score" and v:
                    dkteam[i["team"]["$ref"].split("/teams/")[-1].split("?")[0]] = int(v.replace("+", ""))
        except Exception:
            pass
        picks = []
        for tid, T, pt in ((ta, A, pa), (tb, B, 1 - pa)):
            for name, c, n, gf, aid in T["tf"]:
                if aid in out_ids or n < 3:
                    continue
                share = (c + 0.2) / (n + 1)
                p = pt * share
                o = dk.get(aid)
                imp = (100 / (o + 100) if o > 0 else -o / (-o + 100)) if o else None
                picks.append({"n": name, "t": g["t"][tid], "c": c, "gp": n, "gf": gf, "p": round(p * 100, 1),
                              "dk": o, "imp": round(imp * 100, 1) if imp else None,
                              "edge": round((p - imp) * 100, 1) if imp else None})
        picks.sort(key=lambda x: -x["p"])
        res.append({"m": g["m"], "pa": {aa: round(pa * 100), ab: round((1 - pa) * 100)},
                    "dkTeam": {g["t"].get(k, k): v for k, v in dkteam.items()},
                    "teams": {aa: {k: A[k] for k in ("n", "tip", "first", "jumper")} | {"tf": [x[:4] for x in A["tf"][:5]]},
                              ab: {k: B[k] for k in ("n", "tip", "first", "jumper")} | {"tf": [x[:4] for x in B["tf"][:5]]}},
                    "picks": picks[:5]})
    data = {"date": date, "review": review, "grades": grades, "games": res,
            "nGames": len(parsed)}
    if out:
        json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    return data


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[2] == "--merge":  # auto.json 에 fb 키로 넣고 archive 에 스냅샷
        d = main(sys.argv[1])
        a = json.load(open(sys.argv[3], encoding="utf-8")); a["fb"] = d
        json.dump(a, open(sys.argv[3], "w", encoding="utf-8"), ensure_ascii=False)
        os.makedirs(os.path.join(HERE, "archive"), exist_ok=True)
        json.dump(d, open(os.path.join(HERE, "archive", f"nba_{sys.argv[1]}_fb.json"), "w", encoding="utf-8"), ensure_ascii=False)
        print(f"fb: {len(d['games'])}경기 예측, 어제 {len(d['review'])}경기, 채점 {len(d['grades'])}개 → {sys.argv[3]}.fb")
        sys.exit()
    d = main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(json.dumps(d, ensure_ascii=False, indent=1)[:6000])
