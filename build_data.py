"""오늘 NBA 경기 자동 데이터(auto.json): 라인·부상·L5·선수 시즌 로그·80% 스탯·어제 결과.
usage: python build_data.py YYYYMMDD out.json
분석 문장(팀 분석, 방향, 리뷰)은 Claude가 analysis.json에 따로 쓴다.
"""
import glob, json, os, sys, urllib.request
import matchup as MU
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
            fgm, fga = MU._split(d.get("fieldGoalsMade-fieldGoalsAttempted"))
            tpm, tpa = MU._split(d.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
            ftm, fta = MU._split(d.get("freeThrowsMade-freeThrowsAttempted"))
            n_ = lambda k: int(d.get(k) or 0)
            rows.append({"n": a["athlete"]["displayName"], "id": a["athlete"]["id"], "min": mins, "st": bool(a.get("starter")),
                         "PTS": n_("points"), "REB": n_("rebounds"), "AST": n_("assists"), "3PM": tpm,
                         "FGM": fgm, "FGA": fga, "3PA": tpa, "FTM": ftm, "FTA": fta, "OREB": n_("offensiveRebounds"),
                         "DREB": n_("defensiveRebounds"), "STL": n_("steals"), "BLK": n_("blocks"), "TOV": n_("turnovers")})
            r = rows[-1]
            r.update(PR=r["PTS"] + r["REB"], PA=r["PTS"] + r["AST"], RA=r["REB"] + r["AST"], PRA=r["PTS"] + r["REB"] + r["AST"])
        out[t["team"]["abbreviation"]] = rows
    return out



def qstats(s):
    """경기 플레이 기록 → {선수id: {"PTS":[1Q,2Q,3Q,4Q,OT], "REB", "AST", "FGA", "3PA", "FTA"}} (연장은 OT 칸에 합산)."""
    out = {}
    def add(aid, k, q, v):
        out.setdefault(aid, {k_: [0] * 5 for k_ in QK})[k][min(q, 5) - 1] += v
    for p in s.get("plays", []):
        q = (p.get("period") or {}).get("number") or 0
        parts = [x["athlete"]["id"] for x in p.get("participants", []) if x.get("athlete", {}).get("id")]
        if not q or not parts:
            continue
        txt = p.get("text", "")
        if p.get("shootingPlay"):
            pa = p.get("pointsAttempted")
            if pa in (2, 3):
                add(parts[0], "FGA", q, 1)
                if pa == 3:
                    add(parts[0], "3PA", q, 1)
            elif pa == 1:
                add(parts[0], "FTA", q, 1)
        if p.get("scoringPlay"):
            add(parts[0], "PTS", q, int(p.get("scoreValue") or 0))
            if len(parts) > 1 and "assist" in txt.lower():
                add(parts[1], "AST", q, 1)
        elif "rebound" in ((p.get("type") or {}).get("text", "") + " " + txt).lower() and "team" not in txt.lower():
            add(parts[0], "REB", q, 1)
    return out


QK = ("PTS", "REB", "AST", "FGA", "3PA", "FTA")


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


# 🪜 선수별 기준선 사다리 간격(FanDuel 대체 라인처럼 고정 간격). 같은 기준선에 DraftKings 배당이 있으면 배당·가성비도 붙임.
LADDER = {"PTS": [10, 15, 20, 25, 30, 35, 40, 45, 50], "REB": [4, 6, 8, 10, 12, 14, 16], "AST": [2, 4, 6, 8, 10, 12, 14],
          "3PM": [1, 2, 3, 4, 5, 6], "PRA": [15, 20, 25, 30, 35, 40, 45, 50, 55], "PR": [10, 15, 20, 25, 30, 35, 40, 45],
          "PA": [10, 15, 20, 25, 30, 35, 40, 45], "RA": [5, 8, 10, 12, 15, 18, 20]}


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


SEASON_START = "20261020"


def league_profiles(date):
    """시즌 시작 ~ 어제까지 리그 전체 끝난 경기 → 30팀 허용 기록·순위(matchup.profiles)."""
    d = datetime.strptime(SEASON_START, "%Y%m%d")
    end = datetime.strptime(date, "%Y%m%d")
    evs = []
    while d < end:
        ds = d.strftime("%Y%m%d")
        p = os.path.join(CACHE, f"sb_{ds}.json")
        sb = get(f"{B}/scoreboard?dates={ds}", None if not os.path.exists(p) else f"sb_{ds}.json")
        done = [e for e in sb.get("events", []) if e["competitions"][0]["status"]["type"]["completed"]]
        if not os.path.exists(p) and sb.get("events") and len(done) == len(sb["events"]) and (end - d).days >= 2:
            json.dump(sb, open(p, "w", encoding="utf-8"))
        evs += [e["id"] for e in done if (e.get("season") or {}).get("type", 2) == 2]
        d += timedelta(days=1)
    with ThreadPoolExecutor(8) as ex:
        sums = list(ex.map(summary, evs))
    return MU.profiles([MU.team_lines(s) for s in sums]), len(evs)


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
        for dt, e in sorted(evs, reverse=True):
            br = box_rows(sums[e])
            op = next((t for t in br if t != ab), "")
            hm = next((c["homeAway"] for c in (sums[e].get("header", {}).get("competitions") or [{}])[0].get("competitors", [])
                       if c.get("team", {}).get("abbreviation") == ab), "")
            for row in br.get(ab, []):
                logs.setdefault((ab, row["n"]), []).append({**row, "opp": op, "d": dt[:10], "ha": hm[:1], "e": e})

    # 쿼터별 평균(플레이 기록): 출전한 경기만
    qs_by_ev = {e: qstats(sums[e]) for e in all_ev}
    qtr = {}
    for (ab, name), L in logs.items():
        aid = L[0]["id"]
        evs = [e for _, e in sorted(ev_by_team[ab], reverse=True) if any(r["id"] == aid for r in box_rows(sums[e]).get(ab, []))]
        Q = [qs_by_ev[e].get(aid) for e in evs]
        Q = [x for x in Q if x]
        if len(Q) < 3:
            continue
        avg = {k: [round(sum(x[k][i] for x in Q) / len(Q), 2 if i == 4 else 1) for i in range(5)] for k in QK}
        avg["otG"] = sum(any(x[k][4] for k in ("PTS", "REB", "AST")) for x in Q)
        q1z = sum(x["PTS"][0] == 0 for x in Q)
        qtr[aid] = {"G": len(Q), **avg, "q1z": q1z, "l5": [x["PTS"] for x in Q[:5]]}

    out_names = {i["n"] for g in games for v in g["inj"].values() for i in v if i["s"] in ("Out", "Doubtful")}

    # 🧭 매치업: 리그 30팀 허용 기록 순위 + 상대 핵심 선수(빅맨·림 보호·스틸)
    TP, n_league = league_profiles(date)
    opp_of = {}
    for g in games:
        opp_of[g["away"]], opp_of[g["home"]] = g["home"], g["away"]
    km = {ab: MU.keymen({n: L for (t, n), L in logs.items() if t == ab}, out_names) for ab in opp_of}
    _mu = {}
    spme = {}
    for g in games:
        for ab in (g["away"], g["home"]):
            spme[ab] = MU.spread_for(g["sp"], ab)

    def mu_for(ab, name, cat):
        k = (ab, name, cat)
        if k not in _mu:
            L = logs.get((ab, name))
            opp = opp_of.get(ab)
            _mu[k] = MU.grade(TP, opp, cat, MU.player_mix(L), km[opp]) if L and opp in TP else None
        return _mu[k]

    # 🆚 맞대결: 이번 시즌 박스(상대 핵심 선수 결장 여부 포함). 지난 시즌은 오너 요청으로 뺌.
    _h2h = {}

    def h2h_of(aid, ab, name):
        if aid in _h2h:
            return _h2h[aid]
        opp = opp_of.get(ab)
        out = []
        for l in logs.get((ab, name), []):
            if l.get("opp") == opp:
                played = {r["n"] for r in box_rows(sums[l["e"]]).get(opp, [])}
                miss = [b[0] for k_ in ("big", "rim") for b in km[opp][k_] if b[0] not in played]
                out.append({"s": "26-27", "d": l["d"], "ha": l["ha"], "min": l["min"], "miss": sorted(set(miss)),
                            **{k: l[k] for k in ("PTS", "REB", "AST", "3PM", "PRA", "PR", "PA", "RA", "FGA", "3PA", "FTA")}})
        out.sort(key=lambda x: x["d"], reverse=True)
        _h2h[aid] = out
        return out
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
            r["mu"] = mu_for(ab, name, r["cat"])
            _v = [l[r["cat"]] for l in L]
            _p = ((r["hit"] + 1) / (r["N"] + 2) + (r["h5"] + 1) / 7) / 2
            r["flow"] = MU.flow(ab, spme.get(ab), sum(l["min"] for l in L) / len(L))
            _bl = bool(r["flow"] and r["flow"][2])
            r["p"], r["pt"] = round(100 * _p), round(100 * MU.today(_p, r["cat"], r["mu"][1] if r["mu"] else None, _bl))
            if r["mu"] and r["mu"][0] == "bad":
                r["rec0"], r["rec"] = r["rec"], MU.downgrade(r["rec"])
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
            _fl = MU.flow(ab, spme.get(ab), sum(l["min"] for l in L) / len(L))
            if _fl and _fl[2]:
                flags.append(f"스프레드 {g['abs']} 블로아웃 위험")
            props.append({"id": aid, "n": name, "t": ab, "g": g["m"], "gid": g["id"], "cat": cat, "line": line, "open": opn,
                          "N": len(vals), "hit": hit, "rate": round(100 * hit / len(vals)), "last": last,
                          "h5": sum(v > line for v in last), "avg": round(sum(vals) / len(vals), 1),
                          "a5": round(sum(last) / len(last), 1), "mn": min(vals), "mx": max(vals),
                          "min": round(sum(l["min"] for l in L) / len(L), 1), "min3": round(sum(l["min"] for l in L[:3]) / min(3, len(L)), 1),
                          "flags": flags, "mu": mu_for(ab, name, cat)})

    # 💰 가성비: DraftKings 마일스톤(오버) 배당의 내재 확률 vs 시즌 적중률(표본 보정)
    value, ladders = [], []
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
            # 기준선과 상관없는 경고(출전시간·부상·블로아웃)
            base = []
            mf = minutes_flag(L)
            if mf and "감소" in mf:
                base.append(mf)
            if name in q_names:
                base.append(f"부상 리포트 {inj_s.get(name, 'DTD/Q')}")
            _fl = MU.flow(ab, spme.get(ab), sum(l["min"] for l in L) / N)
            blow = bool(_fl and _fl[2])
            if blow:
                base.append(f"스프레드 {g['abs']} 블로아웃 위험")
            lad = {"id": aid, "n": name, "t": ab, "g": g["m"], "cat": cat, "N": N, "last": vals[:5],
                   "min": round(sum(l["min"] for l in L) / N, 1), "min3": round(sum(l["min"] for l in L[:3]) / 3, 1),
                   "st": sum(l.get("st", False) for l in L), "st5": sum(l.get("st", False) for l in L[:5]),
                   "avg": round(sum(vals) / N, 1), "a5": round(sum(vals[:5]) / len(vals[:5]), 1), "mx": max(vals), "mn": min(vals),
                   "fl": base, "r": [], "mu": mu_for(ab, name, cat), "l10": vals[:10],
                   "hm": [round(sum(l[cat] for l in L if l.get("ha") == "h") / max(1, sum(l.get("ha") == "h" for l in L)), 1), sum(l.get("ha") == "h" for l in L)],
                   "aw": [round(sum(l[cat] for l in L if l.get("ha") == "a") / max(1, sum(l.get("ha") == "a" for l in L)), 1), sum(l.get("ha") == "a" for l in L)],
                   "home": ab == g["home"],
                   "proj": MU.project(vals, cat, (mu_for(ab, name, cat) or [None, None])[1], blow) if cat in MU.PROJ else None, "flow": _fl,
                   "att": {"s": [round(sum(l.get(k, 0) for l in L) / N, 1) for k in ("FGA", "3PA", "FTA")],
                           "l5": [round(sum(l.get(k, 0) for l in L[:5]) / len(L[:5]), 1) for k in ("FGA", "3PA", "FTA")]}}
            mbad = bool(lad["mu"] and lad["mu"][0] == "bad")
            hv = [h[cat] for h in h2h_of(aid, ab, name)]  # 이번 시즌 맞대결 기록
            lad["hN"] = len(hv)
            dk = {}
            for th, dec, am in sorted(set(arr)):
                hit = sum(v >= th for v in vals)
                last = vals[:5]
                h5 = sum(v >= th for v in last)
                # 적중 확률 추정 = 시즌(표본 보정)과 최근 5경기(표본 보정)를 반반
                ps, p5 = (hit + 1) / (N + 2), (h5 + 1) / (len(last) + 2)
                p = (ps + p5) / 2
                imp = 1 / dec
                edge = p - imp
                flags = base + ([f"최근 5경기 {h5}/5"] if h5 <= 2 else [])
                val = ("good" if not flags and not mbad else "warn") if edge >= 0.05 else "bad" if edge <= -0.05 else "fair"
                dk[th] = (am, round(100 * imp), round(100 * edge, 1), "low" if p < 0.4 else "heavy" if imp > 0.95 else val)
                if imp > 0.95 or p < 0.4:
                    continue
                value.append({"id": aid, "n": name, "t": ab, "g": g["m"], "gid": g["id"], "cat": cat, "th": th,
                              "am": am, "dec": dec, "imp": round(100 * imp), "hit": hit, "N": N, "p": round(100 * p),
                              "ps": round(100 * ps), "p5": round(100 * p5),
                              "fair": fair_am(p), "edge": round(100 * edge, 1),
                              "val": val,
                              "last": last, "h5": h5,
                              "min": lad["min"], "min3": lad["min3"],
                              "flags": flags, "mu": lad["mu"], "pt": round(100 * MU.today(p, cat, lad["mu"][1] if lad["mu"] else None, blow))})
            # 🪜 고정 간격 사다리: 각 단계 추정 확률(시즌·최근5 반반) + 같은 단계 DK 배당이 있으면 붙임
            last = vals[:5]
            for th in LADDER.get(cat, []):
                hit, h5 = sum(v >= th for v in vals), sum(v >= th for v in last)
                ps, p5 = (hit + 1) / (N + 2), (h5 + 1) / (len(last) + 2)
                p = (ps + p5) / 2
                if p < 0.04 or p > 0.97:
                    continue
                if lad["r"] and hit == 0 and h5 == 0 and lad["r"][-1][6] == 0 and lad["r"][-1][7] == 0:
                    continue  # 한 번도 못 넘은 단계는 첫 줄만
                am, imp, edge, val = dk.get(float(th), dk.get(th, (None, None, None, None)))
                pt = MU.today(p, cat, lad["mu"][1] if lad["mu"] else None, blow)
                lad["r"].append([th, am, imp, round(100 * p), round(100 * ps), round(100 * p5), hit, h5, fair_am(p), edge, val,
                                 round(100 * pt), sum(v >= th for v in hv)])
            lad["dkx"] = sorted(t for t in dk if t not in LADDER.get(cat, []))  # 간격 밖 DK 단계(목록 보기에 있음)
            if lad["r"]:
                ladders.append(lad)
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

    h2h = {d["id"]: _h2h.get(d["id"], []) for d in ladders}
    teams_mu = {ab: {**{k: TP[ab][k] for k in TP[ab] if k != "rk"}, "rk": TP[ab]["rk"], "km": km[ab]} for ab in opp_of if ab in TP}
    json.dump({"date": date, "prev": prev, "games": games, "teams": teams_mu, "nLeague": n_league, "h2h": h2h, "stat80": stat80, "form": form, "results": results,
               "value": [v for v in value if v["cat"] not in COMBO][:300],
               "cvalue": [v for v in value if v["cat"] in COMBO][:200], "ladders": ladders, "qtr": {d["id"]: qtr[d["id"]] for d in ladders if d["id"] in qtr}, "vgrades": vgrades, "vgFrom": vg_from,
               "props": props, "minutes": minutes[:40], "propReview": prop_review, "propReviewFrom": pr_from,

               "built": datetime.now().strftime("%Y-%m-%d %H:%M"), "phase": os.environ.get("CS_PHASE", "")}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
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
