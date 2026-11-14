"""경기 2시간 전 재확인: 이전 auto(prev) 와 새 auto(new) 의 부상·라인 차이를 new 에 injchg 로 기록.
usage: python inj_diff.py prev.json new.json "MINS"   (MINS: 이번에 확인한 팁오프 시각들, 분 단위, 공백 구분)
출력 마지막 줄: CHANGES <부상 변동 수>"""
import json, sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
prev, new, mins = sys.argv[1], sys.argv[2], {int(x) for x in sys.argv[3].split()}
A = json.load(open(prev, encoding="utf-8"))
N = json.load(open(new, encoding="utf-8"))
old = {g["m"]: g for g in A["games"]}
chg, n_inj = list(N.get("injchg", [])), 0
for g in N["games"]:
    o = old.get(g["m"])
    if not o:
        continue
    for t in (g["away"], g["home"]):
        before = {i["n"]: i["s"] for i in o["inj"].get(t, [])}
        after = {i["n"]: i["s"] for i in g["inj"].get(t, [])}
        for n in sorted(set(before) | set(after)):
            b, a = before.get(n, "명단 없음"), after.get(n, "명단 제외(출전 가능)")
            if b != a:
                chg.append({"m": g["m"], "k": "부상", "t": t, "txt": f"{n}: {b} → {a}"})
                n_inj += 1
    for k, lab in (("sp", "스프레드"), ("tot", "토탈")):
        if o.get(k) != g.get(k):
            chg.append({"m": g["m"], "k": "라인", "txt": f"{lab} {o.get(k)} → {g.get(k)}"})
N["injchg"] = chg
N["injchk"] = N.get("injchk", []) + [datetime.now().strftime("%H:%M")]
json.dump(N, open(new, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for c in chg[-20:]:
    print(c["m"], c["k"], c["txt"])
print("CHANGES", n_inj)
