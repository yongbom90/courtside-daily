"""🔬 심층 지표 (오너 요청 11/19: 베팅에서 실제로 보는 수치)
- 출전시간 안정성: 평균보다 '얼마나 들쭉날쭉한지'(표준편차·하위 20% 바닥·±5분 안 비율)
- 사용률(USG%): 박스 스코어로 계산한 공격 점유율, 시즌 vs 최근 5경기
- 동료 결장 시 기록: 오늘 Out/Doubtful 동료가 빠졌던 경기 vs 같이 뛴 경기
- 포지션별 상대 허용: 상대가 가드/포워드/센터에게 경기당 내준 득·리·어·3점 + 30팀 순위
- 예상 경기 속도: 두 팀 페이스 평균 vs 리그 평균
수치는 ESPN 박스 스코어에서만 계산(추정·보정 없음).
"""
import statistics as st

POS = {"G": "G", "PG": "G", "SG": "G", "F": "F", "SF": "F", "PF": "F", "C": "C", "FC": "C", "GF": "F"}
CATS = ("PTS", "REB", "AST", "3PM")


def minutes(L):
    m = [l["min"] for l in L]
    if len(m) < 5:
        return None
    avg = st.mean(m)
    sd = st.pstdev(m)
    srt = sorted(m)
    floor = srt[max(0, int(len(srt) * 0.2) - 1)] if len(srt) >= 5 else srt[0]
    within = sum(abs(x - avg) <= 5 for x in m) / len(m)
    lvl = "stable" if sd <= 4 and within >= 0.75 else "volatile" if sd >= 7 or within < 0.55 else "mid"
    return {"avg": round(avg, 1), "sd": round(sd, 1), "floor": floor, "in5": round(100 * within),
            "l10": [round(x) for x in m[:10]][::-1], "lvl": lvl}


def usage(L, team_box):
    """L: 선수 로그(최근→과거, 각 항목 e=경기 id), team_box: {e: 팀 박스 rows}."""
    vals = []
    for l in L:
        rows = team_box.get(l["e"])
        if not rows or not l["min"]:
            continue
        tm_min = sum(r["min"] for r in rows)
        tm_use = sum(r["FGA"] + 0.44 * r["FTA"] + r["TOV"] for r in rows)
        if not tm_use:
            continue
        me = l["FGA"] + 0.44 * l["FTA"] + l["TOV"]
        vals.append(100 * me * (tm_min / 5) / (l["min"] * tm_use))
    if len(vals) < 5:
        return None
    s, f = st.mean(vals), st.mean(vals[:5])
    return {"s": round(s, 1), "l5": round(f, 1), "d": round(f - s, 1), "l10": [round(v, 1) for v in vals[:10]][::-1]}


def _avg(rows, k):
    return round(sum(r[k] for r in rows) / len(rows), 1) if rows else None


def with_without(L, team_events, mates_out):
    """mates_out: [(이름, 그 동료가 뛴 경기 id 집합)] — 오늘 결장하는 같은 팀 동료.
    같이 뛴 적이 있고, 빠진 경기도 2경기 이상일 때만."""
    out = []
    mine = {l["e"]: l for l in L}
    for nm, played in mates_out:
        if not played:
            continue
        w = [mine[e] for e in team_events if e in mine and e in played]
        wo = [mine[e] for e in team_events if e in mine and e not in played]
        if len(wo) < 2 or len(w) < 2:
            continue
        out.append({"w": nm, "nW": len(w), "nO": len(wo),
                    "with": {k: _avg(w, k) for k in CATS + ("min", "FGA")},
                    "wo": {k: _avg(wo, k) for k in CATS + ("min", "FGA")}})
    return out


def dvp(box_by_game):
    """box_by_game: [{팀: rows(with 'pos')}] 리그 전체 끝난 경기.
    → {팀: {G|F|C: {cat: [경기당 허용, 30팀 순위(1=가장 많이 내줌)]}}}"""
    acc = {}
    for br in box_by_game:
        if len(br) != 2:
            continue
        a, b = list(br)
        for d, o in ((a, b), (b, a)):
            per = {"G": {k: 0 for k in CATS}, "F": {k: 0 for k in CATS}, "C": {k: 0 for k in CATS}}
            for r in br[o]:
                p = POS.get(r.get("pos", ""), None)
                if p:
                    for k in CATS:
                        per[p][k] += r[k]
            t = acc.setdefault(d, {"G": {k: 0 for k in CATS}, "F": {k: 0 for k in CATS}, "C": {k: 0 for k in CATS}, "n": 0})
            t["n"] += 1
            for p in per:
                for k in CATS:
                    t[p][k] += per[p][k]
    res = {t: {p: {k: round(v[p][k] / v["n"], 1) for k in CATS} for p in ("G", "F", "C")} for t, v in acc.items() if v["n"]}
    for p in ("G", "F", "C"):
        for k in CATS:
            order = sorted(res, key=lambda t: -res[t][p][k])
            for i, t in enumerate(order):
                res[t][p][k] = [res[t][p][k], i + 1]
    return res


def pace(TP, a, h):
    pa, ph = (TP.get(a) or {}).get("pace"), (TP.get(h) or {}).get("pace")
    lg = [v["pace"] for v in TP.values() if v.get("pace")]
    if not (pa and ph and lg):
        return None
    exp, avg = (pa + ph) / 2, st.mean(lg)
    return {"a": round(pa, 1), "h": round(ph, 1), "exp": round(exp, 1), "lg": round(avg, 1), "d": round(100 * (exp / avg - 1), 1),
            "ra": 1 + sum(v["pace"] > pa for v in TP.values()), "rh": 1 + sum(v["pace"] > ph for v in TP.values())}
