"""הזיפים הצפוניים (46220/46205/46260/46240) מול ה-BUY BOX — מנתוני המכר בלבד."""
import os, sys, statistics as st, zipfile, importlib
from collections import defaultdict, Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import sdf, analyze
dm = importlib.import_module("map")
ALL = sdf.BUY_BOX + dm.WATCH + dm.AVOID
NORTH = ["46220", "46205", "46260", "46240"]
REF = ["46236", "46217", "46228", "46219"]
RULE_A, RULE_B, RENO_A, RENO_B = 0.70, 0.72, 52_416, 75_000

def load_ext(zips):
    out = []
    for year, path in sdf.files():
        with zipfile.ZipFile(path) as zf:
            names = {n.upper(): n for n in zf.namelist()}
            contacts = sdf._contacts(zf, year)
            parcels = defaultdict(list)
            for p in sdf._rows(zf, names["SALEPARCEL.TXT"]):
                parcels[(p.get("SDF_ID") or "").strip()].append(p)
            for s in sdf._rows(zf, names["SALEDISC.TXT"]):
                if (s.get("County_ID") or "").strip() != "49" or not sdf._yes(s, "C10_Residential_Property"): continue
                price = sdf._money(s, "E1_Sales_Price")
                if price < 10_000: continue
                for p in parcels.get((s.get("SDF_ID") or "").strip(), []):
                    z = (p.get("A5_ZipCode") or "").strip()[:5]
                    if z not in zips: continue
                    out.append({"zip": z, "parcel": (p.get("A1_Parcel_Number") or "").strip(),
                        "buyer": contacts.get((s.get("SDF_ID") or "").strip(), ["", ""])[0],
                        "date": (s.get("C7_Conveyance_Date") or "").strip()[:10], "price": price, "dom": 0,
                        "owner_occ": sdf._yes(s, "J1_Primary_Residence"),
                        "distress": sdf._yes(s, "C1_Sheriff_Sale") or sdf._yes(s, "C2_Short_Sale") or sdf._yes(s, "C4_Auction"),
                        "av": sdf._int(p.get("P2_5_Total_AV")), "class": (p.get("P2_6_Prop_Class_Code") or "").strip(),
                        "improved": sdf._yes(p, "A4_Improvement"), "vacant": sdf._yes(s, "B3_Vacant_Land"),
                        "newc": sdf._yes(s, "D1_Physical_Change"), "appr": sdf._yes(s, "E9_Appraisal_Value"),
                        "address": (p.get("A5_Street1") or "").strip(), "city": "Indianapolis"})
    return out

sales = load_ext(set(ALL))
flips = sdf.find_flips(sales)           # כבר בלי בנייה חדשה
Z = NORTH + REF
def med(x): return st.median(x) if x else float("nan")

print("=== א. השוק ב-12 החודשים האחרונים (1.7.2025–30.6.2026) ===")
print(f"{'zip':<7}{'מכירות':>7}{'חציון':>10}{'≥$300K':>8}{'תופס':>6}{'ממומן*':>8}{'מצוקה':>7}{'משקיע':>7}")
for z in Z:
    g = [s for s in sales if s["zip"] == z and s["date"] >= "2025-07-01"]
    oo = [s for s in g if s["owner_occ"]]
    print(f"{z:<7}{len(g):>7}{med([s['price'] for s in g]):>10,.0f}{sum(s['price']>=300e3 for s in g)/len(g):>8.0%}"
          f"{len(oo)/len(g):>6.0%}{sum(s['appr'] for s in oo)/max(1,len(oo)):>8.0%}{sum(s['distress'] for s in g)/len(g):>7.1%}{1-len(oo)/len(g):>7.0%}")
print("* ממומן = בוצעה שמאות (E9) בקרב קונים תופסים")

print("\n=== ב. כיוון המחיר: חציון מכירה לקונה תופס לפי חצי-שנה ===")
halves = ["2023-H1","2023-H2","2024-H1","2024-H2","2025-H1","2025-H2","2026-H1"]
def half(d): return f"{d[:4]}-H{1 if d[5:7] <= '06' else 2}"
print(f"{'zip':<7}" + "".join(f"{h:>10}" for h in halves) + f"{'24m→12m':>10}")
for z in Z:
    row = []
    for h in halves:
        g = [s["price"] for s in sales if s["zip"] == z and s["owner_occ"] and half(s["date"]) == h]
        row.append(med(g))
    prev = med([s["price"] for s in sales if s["zip"] == z and s["owner_occ"] and "2024-07-01" <= s["date"] < "2025-07-01"])
    last = med([s["price"] for s in sales if s["zip"] == z and s["owner_occ"] and s["date"] >= "2025-07-01"])
    print(f"{z:<7}" + "".join(f"{v/1000:>9,.0f}K" for v in row) + f"{last/prev-1:>10.1%}")

print("\n=== ג. מדד מכירות חוזרות של אותו בית (בעל בית → בעל בית, 12-42 ח', בלי מצוקה/בנייה) — שינוי שנתי ===")
by = defaultdict(list)
for s in sales:
    if s["parcel"]: by[s["parcel"]].append(s)
rs = defaultdict(list)
for rows in by.values():
    rows.sort(key=lambda r: r["date"])
    for a, b in zip(rows, rows[1:]):
        gap = sdf._months(a["date"], b["date"])
        if not (12 <= gap <= 42) or not (a["owner_occ"] and b["owner_occ"]) or a["distress"] or b["distress"] or sdf._new_build(a, b): continue
        if b["price"] / a["price"] > 1.6 or b["price"] / a["price"] < 0.6: continue   # שיפוץ/טעות רישום, לא שוק
        rs[b["zip"]].append(((b["price"] / a["price"]) ** (12 / gap) - 1, b["date"]))
print(f"{'zip':<7}{'n':>5}{'שנתי, כל התקופה':>18}{'n 12m':>7}{'שנתי, מכרו ב-12m':>19}")
for z in Z + ["BUYBOX"]:
    g = rs[z] if z != "BUYBOX" else [x for zz in sdf.BUY_BOX for x in rs[zz]]
    r12 = [c for c, d in g if d >= "2025-07-01"]
    print(f"{z:<7}{len(g):>5}{med([c for c, d in g]):>18.1%}{len(r12):>7}{med(r12):>19.1%}")

print("\n=== ד. פליפים אמיתיים שנמכרו ב-24 החודשים האחרונים (1.7.2024–30.6.2026) ===")
def bname(f):
    return " ".join((f["flipper"] or "?").split()[:2])
print(f"{'zip':<7}{'n':>4}{'3-9ח':>5}{'קנייה':>9}{'מכירה':>9}{'מרווח':>9}{'מכפיל':>7}{'החזקה':>6}{'תופס':>6}{'ממומן':>7}"
      f"{'70/52K':>8}{'72/52K':>8}{'70/75K':>8}{'72/75K':>8}")
for z in Z:
    g = [f for f in flips if f["zip"] == z and f["date"] >= "2024-07-01"]
    r = [f for f in g if 3 <= f["months"] <= 9]
    if not g: continue
    def p(rule, reno): return sum(rule * f["sell"] - f["buy"] >= reno for f in g) / len(g)
    oo = [f for f in g if f["owner_occ"]]
    print(f"{z:<7}{len(g):>4}{len(r):>5}{med([f['buy'] for f in g]):>9,.0f}{med([f['sell'] for f in g]):>9,.0f}"
          f"{med([f['sell']-f['buy'] for f in g]):>9,.0f}{med([f['mult'] for f in g]):>7.2f}{med([f['months'] for f in g]):>6.1f}"
          f"{len(oo)/len(g):>6.0%}{sum(f['appr'] for f in oo)/max(1,len(oo)):>7.0%}"
          f"{p(RULE_A, RENO_A):>8.0%}{p(RULE_B, RENO_A):>8.0%}{p(RULE_A, RENO_B):>8.0%}{p(RULE_B, RENO_B):>8.0%}")
print("מרווח = מכירה − קנייה. 70/52K = עובר את כלל ה-70% בתקציב $52,416; 75K = תקציב לבית גדול/ישן יותר")

print("\n=== ה. מדרגת המכירה של הפליפים העוברים (72%/$52K, 24 ח') ===")
for z in NORTH:
    g = [f for f in flips if f["zip"] == z and f["date"] >= "2024-07-01" and RULE_B * f["sell"] - f["buy"] >= RENO_A]
    bands = Counter("<250K" if f["sell"] < 250e3 else "250-350K" if f["sell"] < 350e3 else "350-500K" if f["sell"] < 500e3 else "500K+" for f in g)
    print(f"{z}: {len(g)} עוברים · {dict(bands)} · חציון מכירה ${med([f['sell'] for f in g]):,.0f} · חציון קנייה ${med([f['buy'] for f in g]):,.0f}")

print("\n=== ו. מי קנה את הפליפים העוברים (72%/$52K, 24 ח') ===")
for z in NORTH:
    g = [f for f in flips if f["zip"] == z and f["date"] >= "2024-07-01" and RULE_B * f["sell"] - f["buy"] >= RENO_A]
    c = Counter(bname(f) for f in g)
    print(f"{z}: {len(c)} קונים ל-{len(g)} עסקאות · " + " · ".join(f"{n} ({k})" for n, k in c.most_common(5)))

print("\n=== ז. שווי שומה של הבתים שנקנו לפליפ (פרוקסי לגודל) ===")
for z in Z:
    g = [f for f in flips if f["zip"] == z and f["date"] >= "2024-07-01" and f["av"] > 0]
    print(f"{z}: n={len(g)} · שומה חציונית ${med([f['av'] for f in g]):,.0f} · p75 ${sorted(f['av'] for f in g)[int(len(g)*.75)] if g else 0:,.0f}")
