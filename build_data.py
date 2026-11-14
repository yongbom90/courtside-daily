"""오늘 NBA 경기 자동 데이터(auto.json): 라인·부상·L5·선수 시즌 로그·80% 스탯·어제 결과.
usage: python build_data.py YYYYMMDD out.json
분석 문장(팀 분석, 방향, 리뷰)은 Claude가 analysis.json에 따로 쓴다.
"""
import glob, json, os, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")
B = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
HERE = os.path.dirname(os.path.abspath(__file__))
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
            rows.append({"n": a["athlete"]["displayName"], "id": a["athlete"]["id"], "min": mins,
                         "PTS": int(d.get("points") or 0), "REB": int(d.get("rebounds") or 0),
                         "AST": int(d.get("assists") or 0), "3PM": int(tpm or 0)})
            r = rows[-1]
            r.update(PR=r["PTS"] + r["REB"], PA=r["PTS"] + r["AST"], RA=r["REB"] + r["AST"], PRA=r["PTS"] + r["REB"] + r["AST"])
        out[t["team"]["abbreviation"]] = rows
    return out


THR = {"PTS": [10, 12, 15, 18, 20, 22, 25, 28, 30, 35], "REB": [4, 5, 6, 8, 10, 12, 14],
       "AST": [3, 4, 5, 6, 8, 10], "3PM": [1, 2, 3, 4],
       # 콤보
       "PRA": [15, 20, 25, 30, 35, 40, 45, 50], "PR": [12, 15, 20, 25, 30, 35, 40],
       "PA": [12, 15, 20, 25, 30, 35, 40], "RA": [6, 8, 10, 12, 15, 18, 20]}
COMBO = {"PRA", "PR", "PA", "RA"}


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
        mf = minutes_flag(logs)
        if mf and "감소" in mf:
            flags.append(mf)
        if rate >= 90 and h5 == 5 and a5 >= th * 1.25 and not flags:
            rec = "top"
        elif rate >= 80 and h5 >= 4 and not flags:
            rec = "rec"
        else:
            rec = "caution"
        res.append({"n": name, "t": team, "g": game, "cat": cat, "th": th, "hit": hit, "N": N, "rate": rate,
                    "h5": h5, "avg": avg, "a5": a5, "last": last, "rec": rec, "flags": flags})
    return res


def minutes_flag(logs):
    """최근 3경기 평균 출전시간이 시즌보다 15% 이상 줄거나 늘면 문구."""
    if len(logs) < 5:
        return None
    ms = sum(l["min"] for l in logs) / len(logs)
    m3 = sum(l["min"] for l in logs[:3]) / 3
    if m3 < ms * 0.85:
        return f"출전시간 감소 (시즌 {ms:.1f} → 최근3 {m3:.1f}분)"
    if m3 > ms * 1.15:
        return f"출전시간 증가 (시즌 {ms:.1f} → 최근3 {m3:.1f}분)"
    return None


PROP = {"Total Points": "PTS", "Total Rebounds": "REB", "Total Assists": "AST", "Total 3-Point Field Goals": "3PM",
        "Total Points, Rebounds, and Assists": "PRA", "Total Points and Rebounds": "PR",
        "Total Points and Assists": "PA", "Total Assists and Rebounds": "RA"}


_ITEMS = {}


def prop_items(eid):
    """DraftKings 선수 prop 원본(ESPN core API). 경기당 한 번만 받음."""
    if eid not in _ITEMS:
        try:
            _ITEMS[eid] = get(f"{CORE}/events/{eid}/competitions/{eid}/odds/100/propBets?limit=1000").get("items", [])
        except Exception as e:
            print(f"  props {eid}: {e}", file=sys.stderr)
            _ITEMS[eid] = []
    return _ITEMS[eid]


MILE = {"Points Milestones": "PTS", "Rebounds Milestones": "REB", "Assists Milestones": "AST", "3-Point Field Goals Milestones": "3PM",
        "Points + Assists + Rebounds Milestones": "PRA", "Points + Rebounds Milestones": "PR",
        "Points + Assists Milestones": "PA", "Rebounds + Assists Milestones": "RA"}


def miles_for(eid):
    """DraftKings 단계별 오버 라인(마일스톤)과 배당. {(선수id, 항목): [(기준선, 소수 배당, 미국식 배당)]}"""
    out = {}
    for i in prop_items(eid):
        cat = MILE.get(i.get("type", {}).get("name", ""))
        aid = i.get("athlete", {}).get("$ref", "").split("/athletes/")[-1].split("?")[0]
        t = (i.get("current") or {}).get("target", {}).get("value")
        o = i.get("odds") or {}
        try:
            dec, am = float(o["decimal"]["value"]), int(o["american"]["value"].replace("+", ""))
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        if cat and aid and t is not None and dec > 1:
            out.setdefault((aid, cat), []).append((t, dec, am))
    # 같은 이름의 마일스톤에 경기 전체가 아닌 묶음(쿼터·하프로 추정)이 섞여 있음 → 경기 전체 배당만 남긴다.
    # 1) 기준선이 높을수록 확률이 낮아야 함(위에서부터 내려오며 확률이 줄어드는 항목은 버림)
    # 2) 기본 라인(예: 리바운드 9.5)보다 낮은 기준선인데 확률 35% 미만이면 버림
    base = props_for(eid)
    clean = {}
    for key, arr in out.items():
        line = (base.get(key) or (None,))[0]
        keep, pmax = [], 0.0
        for t, dec, am in sorted(set(arr), key=lambda x: (-x[0], x[1])):
            imp = 1 / dec
            if imp + 1e-9 < pmax or (line is not None and t < line and imp < 0.35):
                continue
            if keep and keep[-1][0] == t:
                continue  # 같은 기준선은 확률 높은(경기 전체) 쪽 하나만
            keep.append((t, dec, am))
            pmax = imp
        clean[key] = keep
    return clean


def fair_am(p):
    """확률 → 공정 미국식 배당."""
    p = min(max(p, 0.01), 0.99)
    return -round(100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def props_for(eid):
    """DraftKings 선수 기본 라인. {(선수id, 항목): (현재 라인, 오픈 라인)}"""
    items = prop_items(eid)
    out = {}
    for i in items:
        cat = PROP.get(i.get("type", {}).get("name", ""))
        aid = i.get("athlete", {}).get("$ref", "").split("/athletes/")[-1].split("?")[0]
        if cat and aid and (i.get("current") or {}).get("target"):
            out[(aid, cat)] = (i["current"]["target"]["value"], (i.get("open") or {}).get("target", {}).get("value"))
    return out


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
        list(ex.map(prop_items, [g["id"] for g in games]))
    lines_by_game = {g["id"]: props_for(g["id"]) for g in games}
    miles_by_game = {g["id"]: miles_for(g["id"]) for g in games}
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

    # 선수 id ↔ (팀, 이름)
    key_of = {}
    for (ab, name), L in logs.items():
        key_of[L[0]["id"]] = (ab, name)
    id_of = {v: k for k, v in key_of.items()}
    inj_s = {i["n"]: i["s"] for g in games for v in g["inj"].values() for i in v}
    all_lines = {}
    for g in games:
        for (aid, cat), ln in lines_by_game.get(g["id"], {}).items():
            all_lines[(aid, cat)] = ln
    for r in stat80:
        ln = all_lines.get((id_of.get((r["t"], r["n"])), r["cat"]))
        r["dk"] = ln[0] if ln else None

    # 라인 vs 기록: DraftKings 기본 라인과 시즌 로그 비교(오버 = 라인 초과)
    props = []
    for g in games:
        for (aid, cat), (line, opn) in lines_by_game.get(g["id"], {}).items():
            if aid not in key_of:
                continue
            ab, name = key_of[aid]
            if ab not in (g["away"], g["home"]) or name in out_names:
                continue
            L = logs[(ab, name)]
            vals = [l[cat] for l in L]
            last = vals[:5]
            hit = sum(v > line for v in vals)
            flags = []
            mf = minutes_flag(L)
            if mf:
                flags.append(mf)
            if len(vals) < 5:
                flags.append(f"표본 적음({len(vals)}경기)")
            if opn is not None and opn != line:
                flags.append(f"라인 이동 {opn} → {line}")
            if name in q_names:
                flags.append(f"부상 리포트 {inj_s.get(name, 'DTD/Q')}")
            if g["abs"] >= 12:
                flags.append(f"스프레드 {g['abs']} 블로아웃 위험")
            props.append({"id": aid, "n": name, "t": ab, "g": g["m"], "gid": g["id"], "cat": cat, "line": line, "open": opn,
                          "N": len(vals), "hit": hit, "rate": round(100 * hit / len(vals)), "last": last,
                          "h5": sum(v > line for v in last), "avg": round(sum(vals) / len(vals), 1),
                          "a5": round(sum(last) / len(last), 1), "mn": min(vals), "mx": max(vals),
                          "min": round(sum(l["min"] for l in L) / len(L), 1), "min3": round(sum(l["min"] for l in L[:3]) / min(3, len(L)), 1),
                          "flags": flags})

    # 💰 가성비: DraftKings 마일스톤(오버) 배당의 내재 확률 vs 시즌 적중률(표본 보정)
    value = []
    for g in games:
        for (aid, cat), arr in miles_by_game.get(g["id"], {}).items():
            if aid not in key_of:
                continue
            ab, name = key_of[aid]
            if ab not in (g["away"], g["home"]) or name in out_names:
                continue
            L = logs[(ab, name)]
            N = len(L)
            if N < 5:
                continue
            vals = [l[cat] for l in L]
            for th, dec, am in sorted(set(arr)):
                hit = sum(v >= th for v in vals)
                last = vals[:5]
                h5 = sum(v >= th for v in last)
                # 적중 확률 추정 = 시즌(표본 보정)과 최근 5경기(표본 보정)를 반반
                ps, p5 = (hit + 1) / (N + 2), (h5 + 1) / (len(last) + 2)
                p = (ps + p5) / 2
                imp = 1 / dec
                if imp > 0.95 or p < 0.4:
                    continue
                edge = p - imp
                flags = []
                mf = minutes_flag(L)
                if mf and "감소" in mf:
                    flags.append(mf)
                if name in q_names:
                    flags.append(f"부상 리포트 {inj_s.get(name, 'DTD/Q')}")
                if g["abs"] >= 12:
                    flags.append(f"스프레드 {g['abs']} 블로아웃 위험")
                if h5 <= 2:
                    flags.append(f"최근 5경기 {h5}/5")
                value.append({"id": aid, "n": name, "t": ab, "g": g["m"], "gid": g["id"], "cat": cat, "th": th,
                              "am": am, "dec": dec, "imp": round(100 * imp), "hit": hit, "N": N, "p": round(100 * p),
                              "ps": round(100 * ps), "p5": round(100 * p5),
                              "fair": fair_am(p), "edge": round(100 * edge, 1),
                              "val": ("good" if not flags else "warn") if edge >= 0.05 else "bad" if edge <= -0.05 else "fair",
                              "last": last, "h5": h5,
                              "min": round(sum(l["min"] for l in L) / N, 1), "min3": round(sum(l["min"] for l in L[:3]) / 3, 1),
                              "flags": flags})
    value.sort(key=lambda v: -v["edge"])
    vidx = {(v["id"], v["cat"], v["th"]): v for v in value}
    for r in stat80:
        v = vidx.get((id_of.get((r["t"], r["n"])), r["cat"], r["th"]))
        if v:
            r.update(am=v["am"], fair=v["fair"], edge=v["edge"], val=v["val"])

    # 출전시간 변화: 최근 3경기 vs 시즌 (오늘 경기 선수, 5경기 이상, 시즌 평균 12분 이상)
    minutes = []
    playing = {t for g in games for t in (g["away"], g["home"])}
    for (ab, name), L in logs.items():
        if ab not in playing or name in out_names or len(L) < 5:
            continue
        ms = sum(l["min"] for l in L) / len(L)
        m3 = sum(l["min"] for l in L[:3]) / 3
        if ms < 12 and m3 < 20:
            continue
        minutes.append({"n": name, "t": ab, "g": teams[ab][1], "N": len(L), "ms": round(ms, 1), "m3": round(m3, 1),
                        "d": round(m3 - ms, 1), "lastM": [l["min"] for l in L[:5]], "pts3": round(sum(l["PTS"] for l in L[:3]) / 3, 1),
                        "pts": round(sum(l["PTS"] for l in L) / len(L), 1), "q": inj_s.get(name, "")})
    minutes.sort(key=lambda m: -abs(m["d"]))

    prev = (datetime.strptime(date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    results, final_box = [], {}
    for ev in get(f"{B}/scoreboard?dates={prev}")["events"]:
        c = ev["competitions"][0]
        if not c["status"]["type"]["completed"]:
            continue
        s = summary(ev["id"])
        final_box[ev["id"]] = {r["id"]: r for rows in box_rows(s).values() for r in rows}
        pc = (s.get("pickcenter") or [{}])[0]
        results.append({"m": ev["shortName"], "close": f"{pc.get('details', '')} / {pc.get('overUnder', '')}",
                        "score": {t["team"]["abbreviation"]: int(t["score"]) for t in c["competitors"]},
                        "q": {t["team"]["abbreviation"]: [int(q["value"]) for q in t.get("linescores", [])] for t in c["competitors"]},
                        "headline": (s.get("article") or {}).get("headline", "")})

    # 어제 위젯 라인 vs 실제 기록
    prop_review, pr_from = [], None
    pf = sorted(f for f in glob.glob(os.path.join(HERE, "archive", "nba_*_props.json")) if os.path.basename(f)[4:12] < date)
    if pf:
        pr_from = os.path.basename(pf[-1])[4:12]
        for p in json.load(open(pf[-1], encoding="utf-8")):
            box = final_box.get(p["gid"])
            if box is None:
                continue
            r = box.get(p["id"])
            act = r[p["cat"]] if r else None
            prop_review.append({**{k: p.get(k) for k in ("n", "t", "g", "cat", "line", "rate")}, "act": act,
                                "r": "-" if act is None else ("O" if act > p["line"] else "U")})

    vgrades, vg_from = [], None
    vf = sorted(f for f in glob.glob(os.path.join(HERE, "archive", "nba_*_value.json")) if os.path.basename(f)[4:12] < date)
    if vf:
        vg_from = os.path.basename(vf[-1])[4:12]
        for p in json.load(open(vf[-1], encoding="utf-8")):
            box = final_box.get(p["gid"])
            if box is None:
                continue
            r = box.get(p["id"])
            act = r[p["cat"]] if r else None
            vgrades.append({**{k: p.get(k) for k in ("n", "t", "g", "cat", "th", "am", "edge")}, "act": act,
                            "r": "-" if act is None else ("O" if act >= p["th"] else "X")})

    json.dump({"date": date, "prev": prev, "games": games, "stat80": stat80, "form": form, "results": results,
               "value": [v for v in value if v["cat"] not in COMBO][:300],
               "cvalue": [v for v in value if v["cat"] in COMBO][:200], "vgrades": vgrades, "vgFrom": vg_from,
               "props": props, "minutes": minutes[:40], "propReview": prop_review, "propReviewFrom": pr_from,

               "built": datetime.now().strftime("%Y-%m-%d %H:%M")}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"combo: stat80={sum(r['cat'] in COMBO for r in stat80)} props={sum(p['cat'] in COMBO for p in props)} "
          f"value={sum(v['cat'] in COMBO for v in value)} (good={sum(v['cat'] in COMBO and v['val'] == 'good' for v in value)})")
    print(f"value={len(value)} (good={sum(v['val'] == 'good' for v in value)}) vgrades={len(vgrades)}")
    print(f"games={len(games)} stat80={len(stat80)} props={len(props)} minutes={len(minutes)} propReview={len(prop_review)} "
          f"players={sum(len(v) for v in form.values())} results={len(results)} events={len(all_ev)}")


def snapshot(date, auto):
    """게시한 DraftKings 라인을 archive/nba_D_props.json 에 남김 → 다음 날 '어제 라인 vs 실제' 리뷰용."""
    P = json.load(open(auto, encoding="utf-8")).get("props", [])
    os.makedirs(os.path.join(HERE, "archive"), exist_ok=True)
    out = os.path.join(HERE, "archive", f"nba_{date}_props.json")
    json.dump([{k: p.get(k) for k in ("id", "n", "t", "g", "gid", "cat", "line", "rate")} for p in P],
              open(out, "w", encoding="utf-8"), ensure_ascii=False)
    A = json.load(open(auto, encoding="utf-8"))
    V = [v for v in A.get("value", []) + A.get("cvalue", []) if v["val"] == "good"]  # 경고 없는 가성비만 채점
    out2 = os.path.join(HERE, "archive", f"nba_{date}_value.json")
    json.dump([{k: v.get(k) for k in ("id", "n", "t", "g", "gid", "cat", "th", "am", "edge")} for v in V],
              open(out2, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"snapshot {len(P)} props → {os.path.relpath(out, HERE)}; {len(V)} value → {os.path.relpath(out2, HERE)}")


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
    elif sys.argv[2] == "--snapshot":
        snapshot(sys.argv[1], sys.argv[3])
    elif sys.argv[2] == "--tips":
        print(" ".join(str(t) for t in tips(sys.argv[1])))
    else:
        main(sys.argv[1], sys.argv[2])
