import os, sys, statistics as st, zipfile, importlib
from collections import defaultdict, Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import sdf, analyze
dm = importlib.import_module("map")
ALL = sdf.BUY_BOX + dm.WATCH + dm.AVOID
RENO = 52_416
tier = {z: ("BUY" if z in sdf.BUY_BOX else "AVOID" if z in dm.AVOID else "WATCH") for z in ALL}

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
                if (s.get("County_ID") or "").strip() != "49": continue
                if not sdf._yes(s, "C10_Residential_Property"): continue
                price = sdf._money(s, "E1_Sales_Price")
                if price < 10_000: continue
                for p in parcels.get((s.get("SDF_ID") or "").strip(), []):
                    z = (p.get("A5_ZipCode") or "").strip()[:5]
                    if z not in zips: continue
                    out.append({"zip": z, "parcel": (p.get("A1_Parcel_Number") or "").strip(),
                        "buyer": contacts.get((s.get("SDF_ID") or "").strip(), ["", ""])[0],
                        "date": (s.get("C7_Conveyance_Date") or "").strip()[:10], "price": price,
                        "owner_occ": sdf._yes(s, "J1_Primary_Residence"),
                        "distress": sdf._yes(s, "C1_Sheriff_Sale") or sdf._yes(s, "C2_Short_Sale") or sdf._yes(s, "C4_Auction"),
                        "av": sdf._int(p.get("P2_5_Total_AV")), "av_imp": sdf._int(p.get("P2_3_AV_Improvement")),
                        "class": (p.get("P2_6_Prop_Class_Code") or "").strip(),
                        "improved": sdf._yes(p, "A4_Improvement"), "vacant": sdf._yes(s, "B3_Vacant_Land"),
                        "newc": sdf._yes(s, "D1_Physical_Change"),
                        "desc": (s.get("D1_Physical_Change_Desc") or "").strip().lower(),
                        "address": (p.get("A5_Street1") or "").strip()})
    return out

sales = load_ext(set(ALL))
by_parcel = defaultdict(list)
for s in sales:
    if s["parcel"]: by_parcel[s["parcel"]].append(s)
pairs = []
for rows in by_parcel.values():
    rows.sort(key=lambda r: r["date"])
    for buy, sell in zip(rows, rows[1:]):
        gap = sdf._months(buy["date"], sell["date"])
        if 0 < gap <= sdf.FLIP_MAX_MONTHS and sell["price"] >= buy["price"] * 1.10:
            pairs.append((buy, sell, gap))
print(f"flip pairs (same logic as find_flips): {len(pairs):,}")

def newbuild(buy, sell):
    return (buy["class"] == "500" or not buy["improved"] or buy["vacant"] or buy["av_imp"] == 0
            or sell["newc"] or "new" in sell["desc"])
def passes(b, s): return 0.70 * s["price"] - b["price"] >= RENO

for t in ("BUY", "WATCH", "AVOID"):
    g = [(b, s, m) for b, s, m in pairs if tier[s["zip"]] == t]
    nb = [x for x in g if newbuild(x[0], x[1])]
    real = [x for x in g if not newbuild(x[0], x[1])]
    print(f"\n{t}: {len(g):,} pairs · new-build {len(nb):,} ({len(nb)/len(g):.0%})")
    print(f"   pass rate all {sum(passes(b,s) for b,s,m in g)/len(g):.0%} → real renovations {sum(passes(b,s) for b,s,m in real)/max(1,len(real)):.0%}"
          f" · median multiple all {st.median(s['price']/b['price'] for b,s,m in g):.2f} → real {st.median(s['price']/b['price'] for b,s,m in real):.2f}"
          f" · median hold real {st.median(m for b,s,m in real):.1f} mo")
    # which flag caught them
    print("   caught by:", {k: sum(1 for b, s, m in nb if f(b, s)) for k, f in
          [("buy class 500", lambda b, s: b["class"] == "500"), ("buy unimproved", lambda b, s: not b["improved"]),
           ("buy vacant flag", lambda b, s: b["vacant"]), ("buy imp AV=0", lambda b, s: b["av_imp"] == 0),
           ("sell D1 new", lambda b, s: s["newc"] or "new" in s["desc"])]})

print("\n=== BUY BOX detail: what did new-build contamination do to the headline numbers? ===")
bb = [(b, s, m) for b, s, m in pairs if tier[s["zip"]] == "BUY"]
real_bb = [x for x in bb if not newbuild(x[0], x[1])]
print(f"BUY BOX flips {len(bb)} → real {len(real_bb)} · 46229: {sum(1 for b,s,m in bb if s['zip']=='46229')} → {sum(1 for b,s,m in real_bb if s['zip']=='46229')}"
      f" · 46237: {sum(1 for b,s,m in bb if s['zip']=='46237')} → {sum(1 for b,s,m in real_bb if s['zip']=='46237')}")

print("\n=== REAL renovation flips sold 2025-07-01..2026-06-30 that pass the 70%/$52K rule ===")
last12 = [(b, s, m) for b, s, m in pairs if s["date"] >= "2025-07-01" and not newbuild(b, s)]
last24 = [(b, s, m) for b, s, m in pairs if s["date"] >= "2024-07-01" and not newbuild(b, s)]
for t in ("BUY", "WATCH", "AVOID"):
    g = [x for x in last12 if tier[x[1]["zip"]] == t]; p = [x for x in g if passes(x[0], x[1])]
    print(f"{t}: {len(g)} real flips, {len(p)} pass ({len(p)/max(1,len(g)):.0%}) · with 3-9mo hold {sum(1 for b,s,m in p if 3<=m<=9)} · median sell of passing ${st.median([s['price'] for b,s,m in p]) if p else 0:,.0f}")
print("\nby sell band (real flips, last 12 months, 33 zips):")
for lo, hi in [(0,200e3),(200e3,250e3),(250e3,300e3),(300e3,400e3),(400e3,9e9)]:
    g = [x for x in last12 if lo <= x[1]["price"] < hi]; p = [x for x in g if passes(x[0], x[1])]
    print(f"   ${lo/1e3:.0f}K–{hi/1e3:.0f}K  flips {len(g):<4} pass {len(p):<4} ({len(p)/max(1,len(g)):.0%})  {dict(Counter(tier[s['zip']] for b,s,m in p))}")

print(f"\n{'zip':<7}{'tier':<7}{'real12m':>8}{'pass12m':>8}{'3-9mo':>7}{'pass24m':>8}{'med sell':>10}{'70% room':>10}  passing buyers (top)")
def bname(b):
    return " ".join((b["buyer"] or "?").split()[:2])
rows = []
for z in ALL:
    g = [x for x in last12 if x[1]["zip"] == z]; p = [x for x in g if passes(x[0], x[1])]
    p24 = [x for x in last24 if x[1]["zip"] == z and passes(x[0], x[1])]
    if not g and not p24: continue
    top = Counter(bname(b) for b, s, m in p24).most_common(3)
    rows.append((z, tier[z], len(g), len(p), sum(1 for b, s, m in p if 3 <= m <= 9), len(p24),
                 st.median([s["price"] for b, s, m in g]) if g else 0,
                 st.median([0.7*s["price"]-b["price"] for b, s, m in p]) if p else 0,
                 " · ".join(f"{n}({c})" for n, c in top)))
rows.sort(key=lambda r: -r[3])
for r in rows: print(f"{r[0]:<7}{r[1]:<7}{r[2]:>8}{r[3]:>8}{r[4]:>7}{r[5]:>8}{r[6]:>10,.0f}{r[7]:>10,.0f}  {r[8][:60]}")

print("\n=== who bought the REAL passing flips, last 24 months, 33 zips ===")
pas = [x for x in last24 if passes(x[0], x[1])]
names = Counter(bname(b) for b, s, m in pas)
top = names.most_common(15)
print(f"{len(pas)} passing · distinct buyers {len(names)} · top-15 share {sum(c for _,c in top)/len(pas):.0%} · '?' (no name) {names['?']}")
for n, c in top: print(f"   {c:>3}  {n}")
ent = sum(c for n, c in names.items() if any(k in n for k in ("LLC","INC","CORP","TRUST","PROPERT","HOLDING","HOME","INVEST","GROUP","CAPITAL","VENTURE","REAL")))
print(f"entity-named share {ent/len(pas):.0%}")
