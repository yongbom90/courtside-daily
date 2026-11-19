"""상대 팀 성향(시즌 허용 기록) → 선수 항목별 매치업 등급(유리/보통/불리)과 이유.

순위 규칙: 1위 = 상대에게 가장 많이 허용(오버에 유리), 30위 = 가장 적게 허용(불리).
등급: 1~8위 유리, 9~22위 보통, 23~30위 불리. 오늘 결장하는 상대 핵심 빅맨이 있으면 한 단계 유리 쪽으로.
"""

GOOD, BAD = 8, 23
CATS = ("PTS", "REB", "AST", "3PM")


def _split(v):
    try:
        a, b = (v or "0-0").split("-")
        return int(a), int(b)
    except ValueError:
        return 0, 0


def team_lines(s):
    """경기 요약 → {팀: 그 팀 기록}. 박스가 없으면 빈 dict."""
    out = {}
    for t in s.get("boxscore", {}).get("teams", []):
        m = {x.get("name"): x.get("displayValue") for x in t.get("statistics", [])}
        fgm, fga = _split(m.get("fieldGoalsMade-fieldGoalsAttempted"))
        tpm, tpa = _split(m.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
        ftm, fta = _split(m.get("freeThrowsMade-freeThrowsAttempted"))
        if not fga:
            continue
        num = lambda k: int(float(m.get(k) or 0))
        d = dict(FGM=fgm, FGA=fga, TPM=tpm, TPA=tpa, FTM=ftm, FTA=fta, OREB=num("offensiveRebounds"),
                 DREB=num("defensiveRebounds"), REB=num("totalRebounds"), AST=num("assists"), STL=num("steals"),
                 BLK=num("blocks"), TOV=num("totalTurnovers") or num("turnovers"), PITP=num("pointsInPaint"))
        d["PTS"] = 2 * fgm + tpm + ftm
        d["POSS"] = fga - d["OREB"] + d["TOV"] + 0.44 * fta
        out[t["team"]["abbreviation"]] = d
    return out


# 지표: (키, 이름, 큰 값이 상대에게 유리하면 True)
METRICS = [
    ("drtg", "100회당 실점", True), ("pace", "페이스", True),
    ("a3pct", "3점 허용률", True), ("a3pa", "3점 시도 허용", True), ("a2pct", "2점 허용률", True),
    ("afta", "자유투 허용", True), ("apitp", "골밑 실점", True), ("areb", "리바운드 허용", True),
    ("miss", "상대 놓친 슛", True), ("oreb", "상대 공격리바율", False), ("aoreb", "공격리바 허용률", True),
    ("aast", "어시스트 허용", True), ("stl", "상대 스틸", False), ("blk", "상대 블록", False),
    ("tpr", "3점 비중", False),
]
LABEL = {k: n for k, n, _ in METRICS}


def profiles(games):
    """games: [{팀: 기록}] (끝난 경기, 두 팀) → {팀: {지표: 값, "rk": {지표: 순위}, "G": 경기 수}}"""
    acc = {}
    for g in games:
        if len(g) != 2:
            continue
        a, b = list(g)
        for me, op in ((a, b), (b, a)):
            o, x = g[me], g[op]
            r = acc.setdefault(me, {"G": 0, "own": {}, "opp": {}})
            r["G"] += 1
            for k, v in o.items():
                r["own"][k] = r["own"].get(k, 0) + v
            for k, v in x.items():
                r["opp"][k] = r["opp"].get(k, 0) + v
    P = {}
    for t, r in acc.items():
        G, o, x = r["G"], r["own"], r["opp"]
        P[t] = {"G": G,
                "drtg": 100 * x["PTS"] / x["POSS"], "pace": (o["POSS"] + x["POSS"]) / 2 / G,
                "a3pct": 100 * x["TPM"] / max(x["TPA"], 1), "a3pa": x["TPA"] / G,
                "a2pct": 100 * (x["FGM"] - x["TPM"]) / max(x["FGA"] - x["TPA"], 1),
                "afta": x["FTA"] / G, "apitp": x["PITP"] / G, "areb": x["REB"] / G,
                "miss": (o["FGA"] - o["FGM"]) / G,
                "oreb": 100 * o["OREB"] / max(o["OREB"] + x["DREB"], 1),
                "aoreb": 100 * x["OREB"] / max(x["OREB"] + o["DREB"], 1),
                "aast": x["AST"] / G, "stl": o["STL"] / G, "blk": o["BLK"] / G,
                "tpr": 100 * o["TPA"] / max(o["FGA"], 1),
                "ppg": o["PTS"] / G, "oppg": x["PTS"] / G}
    n = len(P)
    for k, _, up in METRICS:
        order = sorted(P, key=lambda t: -P[t][k] if up else P[t][k])
        for i, t in enumerate(order):
            P[t].setdefault("rk", {})[k] = i + 1
    for t in P:
        P[t]["n"] = n
        for k, _, _ in METRICS:
            P[t][k] = round(P[t][k], 1)
    return P


def _g(P, t, k):
    """0~1, 1 = 상대에게 가장 관대."""
    n = P[t]["n"]
    return (n - P[t]["rk"][k]) / max(n - 1, 1)


def player_mix(L):
    """선수 시즌 로그 → 득점 구성·리바 구성·시도 평균."""
    N = len(L)
    s = lambda k: sum(l.get(k, 0) for l in L)
    pts = max(s("PTS"), 1)
    reb = max(s("REB"), 1)
    return {"s3": 3 * s("3PM") / pts, "sft": s("FTM") / pts, "s2": max(0.0, 1 - (3 * s("3PM") + s("FTM")) / pts),
            "so": s("OREB") / reb, "P": s("PTS") / N, "R": s("REB") / N, "A": s("AST") / N}


def score(P, t, cat, mx):
    """상대 팀 t 를 만났을 때 선수 항목 점수(0~1, 높을수록 유리)."""
    g = lambda k: _g(P, t, k)
    if cat == "PTS":
        mixv = mx["s3"] * g("a3pct") + mx["sft"] * g("afta") + mx["s2"] * (g("a2pct") + g("apitp") + g("blk")) / 3
        return 0.35 * g("drtg") + 0.15 * g("pace") + 0.5 * mixv
    # 아래 세 식은 11/1~11/16 되짚어보기에서 적중률 차이가 가장 컸던 조합(허용률·리바 총량보다 '기회 수'가 중요)
    if cat == "3PM":
        return g("a3pa")
    if cat == "REB":
        return (g("miss") + g("oreb")) / 2
    if cat == "AST":
        return g("aast")
    parts = {"PRA": "PRA", "PR": "PR", "PA": "PA", "RA": "RA"}[cat]
    w = {"P": mx["P"], "R": mx["R"], "A": mx["A"]}
    tot = sum(w[c] for c in parts) or 1
    m = {"P": "PTS", "R": "REB", "A": "AST"}
    return sum(w[c] / tot * score(P, t, m[c], mx) for c in parts)


# 항목별 이유에 쓸 지표
WHY = {"PTS": ["drtg", "pace", "a3pct", "a2pct", "apitp", "afta", "blk"], "3PM": ["a3pa", "a3pct"],
       "REB": ["miss", "oreb", "areb"], "AST": ["aast", "pace", "stl"]}
UNIT = {"drtg": "", "pace": "회", "a3pct": "%", "a2pct": "%", "a3pa": "개", "afta": "개", "apitp": "점", "areb": "개",
        "miss": "개", "oreb": "%", "aoreb": "%", "aast": "개", "stl": "개", "blk": "개"}


def keymen(L_by_name, out_names):
    """상대 팀 로그 → 빅맨(리바 8+ 또는 공격리바 2.5+), 림 보호(블록 1.5+), 스틸(1.5+) 선수. 출전 20분+ , 5경기+."""
    res = {"big": [], "rim": [], "stl": []}
    for name, L in L_by_name.items():
        if len(L) < 5 or sum(l["min"] for l in L) / len(L) < 20:
            continue
        a = lambda k: sum(l.get(k, 0) for l in L) / len(L)
        out = name in out_names
        if a("REB") >= 8 or a("OREB") >= 2.5:
            res["big"].append((name, round(a("REB"), 1), round(a("OREB"), 1), out))
        if a("BLK") >= 1.5:
            res["rim"].append((name, round(a("BLK"), 1), out))
        if a("STL") >= 1.5:
            res["stl"].append((name, round(a("STL"), 1), out))
    for v in res.values():
        v.sort(key=lambda x: -x[1])
    return res


def short(n):
    return n.split(" ", 1)[-1] if " " in n else n


# 이유 문장: (무엇, 단위, 많을 때 뜻, 적을 때 뜻). {o} = 상대 팀
EXPL = {
    "drtg": ("{o} 수비: 100번 공격당 실점", "점", "→ 득점이 잘 나오는 수비", "→ 득점이 잘 안 나오는 짠 수비"),
    "pace": ("{o} 경기 속도(경기당 공격 횟수)", "회", "→ 빠른 경기, 슛 기회가 많아요", "→ 느린 경기, 슛 기회가 적어요"),
    "a3pct": ("{o} 팀이 내준 3점 성공률", "%", "→ 3점이 잘 들어가는 상대", "→ 3점이 잘 안 들어가는 상대"),
    "a3pa": ("{o} 팀이 내준 3점 시도", "개", "→ 3점 쏠 기회가 많아요", "→ 3점 쏠 기회를 잘 안 줘요"),
    "a2pct": ("{o} 팀이 내준 2점 성공률", "%", "→ 골밑·중거리 마무리가 쉬운 편", "→ 골밑·중거리 마무리가 어려운 편"),
    "apitp": ("{o} 골밑 실점", "점", "→ 골밑 공략이 잘 통해요", "→ 골밑을 잘 막아요"),
    "afta": ("{o} 팀이 내준 자유투", "개", "→ 파울을 잘 얻어낼 수 있어요", "→ 파울을 잘 안 줘요"),
    "areb": ("{o} 팀이 내준 리바운드", "개", "→ 리바 잡기 쉬운 상대", "→ 리바 잡기 어려운 상대"),
    "miss": ("{o} 팀이 놓치는 슛", "개", "→ 잡을 수비 리바가 많아요", "→ 슛을 잘 넣어서 잡을 리바가 적어요"),
    "oreb": ("{o} 공격 리바운드율", "%", "→ {o} 팀이 자기 리바를 많이 가져가서 수비 리바 몫이 줄어요", "→ {o} 팀이 공격 리바를 잘 못 잡아서 수비 리바 몫이 늘어요"),
    "aoreb": ("{o} 팀이 내준 공격 리바운드율", "%", "→ 공격 리바 잡기 쉬워요", "→ 공격 리바 잡기 어려워요"),
    "aast": ("{o} 팀이 내준 어시스트", "개", "→ 패스로 득점이 잘 나와요", "→ 패스 득점을 잘 막아요"),
    "stl": ("{o} 스틸", "개", "→ 패스 길을 잘 끊어요", "→ 스틸이 적어 패스가 편해요"),
    "blk": ("{o} 블록", "개", "→ 골밑 슛이 막히기 쉬워요", "→ 블록이 적어 골밑이 편해요"),
    "tpr": ("{o} 슛 중 3점 비중", "%", "→ 빗나간 3점은 길게 튀어 가드·윙이 잡는 경우가 많아요(빅맨 리바 불리)", "→ 골밑 슛이 많아 리바가 림 근처로 떨어져요"),
}


def sentence(P, opp, k):
    """지표 하나 → '{o} …: 값 — 30팀 중 N번째로 많음/적음 → 뜻'."""
    n, r = P[opp]["n"], P[opp]["rk"][k]
    up = next(u for k_, _, u in METRICS if k_ == k)
    nat = r if up else n + 1 - r          # 값이 큰 순서
    more = nat <= (n + 1) / 2
    what, unit, m_txt, l_txt = EXPL[k]
    ordn = nat if more else n + 1 - nat
    return (f"{what.format(o=opp)} {P[opp][k]}{unit} — 30팀 중 {'가장' if ordn == 1 else f'{ordn}번째로'} {'많음' if more else '적음'} "
            f"{(m_txt if more else l_txt).format(o=opp)}")


def grade(P, opp, cat, mx, km):
    """→ [등급, 순위, 이유들]. 등급 'good' | 'mid' | 'bad'."""
    if opp not in P:
        return None
    me = score(P, opp, cat, mx)
    rank = 1 + sum(score(P, t, cat, mx) > me + 1e-9 for t in P if t != opp)
    n = P[opp]["n"]
    gr = "good" if rank <= GOOD else "bad" if rank >= n - (30 - BAD) else "mid"
    why = []
    base = [cat] if cat in WHY else [c for c, k in (("PTS", "P"), ("REB", "R"), ("AST", "A")) if k in cat]
    # 결장 반영: 상대 리바 빅맨·림 보호 결장 → 한 단계 유리
    if any(c in ("REB",) for c in base):
        gone = [b for b in km["big"] if b[3]]
        if gone:
            why.append(f"{opp} 리바 강한 " + ", ".join(f"{short(b[0])}(경기당 {b[1]}리바)" for b in gone[:2]) + " 오늘 결장 → 리바 경쟁이 줄어요")
            gr = {"bad": "mid", "mid": "good"}.get(gr, gr)
            rank = max(1, rank - 8)
    if "PTS" in base:
        gone = [b for b in km["rim"] if b[2]]
        if gone and not any("결장" in w for w in why):
            why.append(f"{opp} 림 보호 " + ", ".join(f"{short(b[0])}(경기당 블록 {b[1]})" for b in gone[:2]) + " 오늘 결장 → 골밑이 편해져요")
            gr = {"bad": "mid", "mid": "good"}.get(gr, gr)
            rank = max(1, rank - 8)
    rk = P[opp]["rk"]
    for c in base:
        ms = WHY[c]
        if c == "PTS":
            ms = ["drtg", "pace"] + (["a3pct"] if mx["s3"] >= 0.3 else []) + (["afta"] if mx["sft"] >= 0.2 else []) \
                 + (["a2pct", "apitp", "blk"] if mx["s2"] >= 0.45 else [])
        if c == "REB":
            ms = ["miss", "oreb"]
        cand = []
        for k in ms:
            r = rk[k]
            if gr == "bad" and r >= n - 9 or gr == "good" and r <= 10 or gr == "mid" and (r <= 5 or r >= n - 4):
                cand.append((abs(r - (n + 1) / 2), k, r))
        cand.sort(reverse=True)
        for _, k, r in cand[:2]:
            # 보통 등급은 좋은 점·나쁜 점이 섞여요 → 방향을 앞에 붙여 헷갈리지 않게
            pre = ("유리: " if r <= (n + 1) / 2 else "불리: ") if gr == "mid" else ""
            why.append(pre + sentence(P, opp, k) + (" (성공률은 운 영향이 커서 참고만)" if k == "a3pct" else ""))
        # 3점 등급은 '내준 3점 시도'로만 매겨요(11/1~16 검증). 성공률이 반대 방향이면 헷갈리지 않게 따로 적어요.
        if c == "3PM" and gr in ("bad", "good"):
            r3 = rk["a3pct"]
            if gr == "bad" and r3 <= 8 or gr == "good" and r3 >= n - 7:
                why.append(f"참고: {sentence(P, opp, 'a3pct')} — 하지만 성공률은 운 영향이 커서 등급은 시도 수로 매겨요")
        if c == "REB" and gr != "good":
            act = [b for b in km["big"] if not b[3]]
            if act:
                why.append(f"{opp} 빅맨 " + ", ".join(f"{short(b[0])}(경기당 리바 {b[1]}·공격리바 {b[2]})" for b in act[:3]) + " → 리바 경쟁이 세요")
            # 빅맨(경기당 리바 7+)은 3점 많이 쏘는 상대에게 리바가 줄었어요(11/1~16: 평균 −0.8개, 참고용)
            if mx["R"] >= 7 and P[opp]["rk"]["tpr"] >= n - 7:
                why.append(sentence(P, opp, "tpr"))
        if c == "PTS" and gr != "good" and mx["s2"] >= 0.45:
            act = [b for b in km["rim"] if not b[2]]
            if act:
                why.append(f"{opp} 림 보호 " + ", ".join(f"{short(b[0])}(경기당 블록 {b[1]})" for b in act[:1]) + " → 골밑 마무리가 어려워요")
        if c == "3PM" and gr == "bad":
            act = [b for b in km["stl"] if not b[2]]
            if act:
                why.append(f"{opp} 외곽 수비 " + ", ".join(f"{short(b[0])}(경기당 스틸 {b[1]})" for b in act[:1]) + " → 슛 쏠 틈이 적어요")
        if c == "AST" and gr == "bad":
            act = [b for b in km["stl"] if not b[2]]
            if act:
                why.append(f"{opp} " + ", ".join(f"{short(b[0])}(경기당 스틸 {b[1]})" for b in act[:2]) + " → 패스 길을 잘 끊어요")
    if gr == "mid" and not any("결장" in w for w in why):
        why = why[:2]
    return [gr, rank, why[:4]]


# 오늘 확률 보정: logit(오늘) = logit(시즌 추정) + SLOPE × (15.5 − 순위)/14.5
# 11/1~16 리그 전체 선수-경기·모든 사다리 단계(약 3만 건)로 항목별 로지스틱 회귀. 80% 기준 1위 상대 ≈ +3~4%p, 30위 ≈ −3~5%p.
SLOPE = {"PTS": 0.218, "REB": 0.188, "AST": 0.2, "3PM": 0.062, "PRA": 0.292, "PR": 0.206, "PA": 0.214, "RA": 0.219}


# 11/19 검증(이번 시즌 210경기·주전급 1,586명-경기, 후반부 검증): 시즌:최근5 = 75:25 가 50:50 보다 정확(오차 0.7~1.3%↓),
# 최근 3경기 출전 추세(최근3/시즌 −1, ±40% 제한)를 로짓에 더하면 리바·어시·PRA 가 더 좋아짐. 3점은 효과 없음.
W_SEASON = 0.75
MTR = {"PTS": 0.26, "REB": 0.65, "AST": 1.1, "3PM": 0.0, "PRA": 0.88, "PR": 0.88, "PA": 0.88, "RA": 0.88}


def mtrend(L):
    """L: 최근→과거 로그. 최근 3경기 출전 / 시즌 출전 − 1 (±0.4 제한)"""
    if len(L) < 5:
        return 0.0
    s = sum(l["min"] for l in L) / len(L)
    return max(-0.4, min(0.4, (sum(l["min"] for l in L[:3]) / 3) / s - 1)) if s else 0.0


def today(p, cat, rank, blow=False, mtr=0.0):
    import math
    p = min(max(p, 0.01), 0.99)
    x = math.log(p / (1 - p)) + (SLOPE.get(cat, 0.2) * (15.5 - rank) / 14.5 if rank else 0) + (BLOW_P if blow else 0) \
        + MTR.get(cat, 0.0) * mtr
    return 1 / (1 + math.exp(-x))


# 오늘 예상치 = 시즌 평균 × (1 + PROJ × (15.5 − 순위)/14.5). 11/1~16 검증: 시즌 평균이 최근5·출전시간 섞은 것보다 실제에 가까웠고,
# 상대 보정은 득점 ±4%·어시 ±5% 만 오차를 줄였어요(리바·3점은 효과 없음 → 0).
# 11/19: 포지션별 상대 허용 순위로 바꾸고 재검증 → 득점 ±3%·리바 ±4.4%·어시 ±6.4%(쉬운 5팀 vs 까다로운 5팀 실제 차이의 절반), 3점 0.
PROJ = {"PTS": 0.03, "AST": 0.064, "REB": 0.044, "3PM": 0.0}


# 11/19 체급별 검증(오너: "에이스는 어려운 수비여도 어느 정도 해준다"): 리그 득점 30위 안 또는 팀 1옵션은
# 쉬운→까다로운 상대 득점 +3.1→+3.3%(영향 없음)·리바 −1.9→−6.0%, 일반 선수 득점 +8.8→+0.1%·리바 +3.1→−8.2%, 어시는 둘 다 비슷.
STAR_MULT = {"PTS": 0.0, "REB": 0.4, "AST": 1.0}
ROLE_MULT = {"PTS": 1.45, "REB": 1.25, "AST": 1.0}


def project(vals, cat, rank, blow=False, star=None):
    """→ [오늘 예상, 보통 범위 아래(25%), 위(75%), 상대 보정분, 경기 흐름 보정분]"""
    N = len(vals)
    avg = sum(vals) / N
    mult = 1.0 if star is None else (STAR_MULT if star else ROLE_MULT).get(cat, 1.0)
    adj = avg * PROJ.get(cat, 0) * mult * (15.5 - rank) / 14.5 if rank else 0
    fadj = -avg * BLOW_J if blow else 0
    sv = sorted(vals)
    q = lambda f: sv[min(N - 1, max(0, round(f * (N - 1))))]
    return [round(avg + adj + fadj, 1), q(0.25), q(0.75), round(adj, 1), round(fadj, 1)]


# ⏱️ 경기 흐름(점수차 예상 = 스프레드). 11/1~16 리그 검증(선수 1,391경기):
#  시즌 평균 30분+ 주전이 13점+ 우세 또는 10점+ 열세 경기(36경기·86명) → 출전 평소의 95%, 기준선 적중 66→61%(로짓 −0.25),
#  실제 기록은 예상의 91~96%(득점 91·리바 94·어시 92·3점 96 → 공통 −7%). 접전·5~12점 우세는 차이 없음(62→62%).
#  13점+ 우세 팀 벤치(30분 미만)는 오히려 61→65% → 보정 없이 문장만.
BLOW_P, BLOW_J = -0.25, 0.07


def spread_for(details, team):
    """'NY -14.5' → NY 기준 -14.5(음수 = 우세), 상대 기준 +14.5. 없으면 None"""
    p = (details or "").split()
    if len(p) != 2:
        return 0.0 if (details or "").upper().startswith(("EVEN", "PK")) else None
    try:
        v = float(p[1])
    except ValueError:
        return None
    return v if p[0] == team else -v


def flow(team, sp, ms):
    """→ [종류(bad/good/mid/ok), 문장, 확률·예상치 보정 여부]"""
    if sp is None:
        return None
    a, st = abs(sp), ms >= 30
    if sp <= -13:
        if st:
            return ["bad", f"{team} {a:g}점 우세 예상 → 크게 이기면 4쿼터에 쉬어요. 이런 경기(리그 36경기)에서 주전은 평소의 95%만 뛰고 기록은 예상보다 4~9% 낮았어요", True]
        return ["good", f"{team} {a:g}점 우세 예상 → 주전이 일찍 쉬면 벤치 선수 출전이 늘어요(이런 경기에서 벤치 적중률은 오히려 올랐어요)", False]
    if sp >= 10:
        if st:
            return ["bad", f"{team} {a:g}점 열세 예상 → 크게 지면 주전이 일찍 빠져요. 이런 경기(리그 36경기)에서 주전은 평소의 95%만 뛰고 기록은 예상보다 4~9% 낮았어요", True]
        return ["mid", f"{team} {a:g}점 열세 예상 → 크게 질 수 있는 경기예요(벤치 선수는 영향이 작았어요)", False]
    if a < 5:
        return ["ok", f"접전 예상({team} {sp:+g}) → 출전시간 걱정 없어요", False]
    return ["ok", f"{team} {sp:+g} → 이 정도 점수차는 리그 기록상 출전시간·적중률에 영향이 없었어요", False]


def downgrade(rec):
    return {"top": "rec", "rec": "caution"}.get(rec, rec)


# 📊 팀 시즌 기록(홈/원정 분리) + 30팀 순위. rows: [{"d": 날짜, "t": {팀: (h/a, 기록)}}]
BOOK_KEYS = ("PTS", "FGM", "FGA", "TPM", "TPA", "FTM", "FTA", "OREB", "DREB", "REB", "AST", "TOV", "STL", "BLK", "PITP", "POSS")
# 순위 매길 항목: (키, 큰 값 = 팀에 좋음?)  o_ = 내 기록, x_ = 상대에게 내준 기록
BOOK_RANK = [("o_PTS", True), ("o_FG%", True), ("o_TPM", True), ("o_TPA", True), ("o_3P%", True), ("o_FTA", True),
             ("o_FT%", True), ("o_REB", True), ("o_OREB", True), ("o_AST", True), ("o_TOV", False), ("o_PITP", True),
             ("o_ORTG", True), ("pace", True),
             ("x_PTS", False), ("x_FG%", False), ("x_TPM", False), ("x_TPA", False), ("x_3P%", False), ("x_FTA", False),
             ("x_REB", False), ("x_OREB", False), ("x_AST", False), ("x_TOV", True), ("x_PITP", False), ("x_DRTG", False)]


def _sheet(G, W, o, x):
    pct = lambda m, a: round(100 * m / a, 1) if a else 0.0
    s = {"G": G, "W": W, "L": G - W, "tot": {k: o[k] for k in ("PTS", "FGM", "FGA", "TPM", "TPA", "FTM", "FTA", "REB", "OREB", "AST", "TOV")}}
    for p, r in (("o_", o), ("x_", x)):
        for k in BOOK_KEYS:
            s[p + k] = round(r[k] / G, 1)
        s[p + "FG%"], s[p + "3P%"], s[p + "FT%"] = pct(r["FGM"], r["FGA"]), pct(r["TPM"], r["TPA"]), pct(r["FTM"], r["FTA"])
    s["o_ORTG"] = round(100 * o["PTS"] / max(o["POSS"], 1), 1)
    s["x_DRTG"] = round(100 * x["PTS"] / max(x["POSS"], 1), 1)
    s["pace"] = round((o["POSS"] + x["POSS"]) / 2 / G, 1)
    return s


def team_book(rows):
    acc = {}
    for g in rows:
        T = g["t"]
        if len(T) != 2:
            continue
        a, b = list(T)
        for me, op in ((a, b), (b, a)):
            ha, o = T[me]
            x = T[op][1]
            win = o["PTS"] > x["PTS"]
            for sp in ("all", ha):
                r = acc.setdefault(me, {}).setdefault(sp, {"G": 0, "W": 0, "o": dict.fromkeys(BOOK_KEYS, 0), "x": dict.fromkeys(BOOK_KEYS, 0)})
                r["G"] += 1
                r["W"] += win
                for k in BOOK_KEYS:
                    r["o"][k] += o.get(k, 0)
                    r["x"][k] += x.get(k, 0)
    B = {t: {sp: _sheet(r["G"], r["W"], r["o"], r["x"]) for sp, r in S.items() if r["G"]} for t, S in acc.items()}
    # 순위: 1 = 값이 가장 큼 (색은 BOOK_RANK의 '좋음' 방향으로 화면에서 결정)
    for sp in ("all", "h", "a"):
        ts = [t for t in B if sp in B[t]]
        for k, _ in BOOK_RANK:
            for i, t in enumerate(sorted(ts, key=lambda t: -B[t][sp][k])):
                B[t][sp].setdefault("rk", {})[k] = i + 1
    return B
