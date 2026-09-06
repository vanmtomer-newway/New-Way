import os, sys, math, statistics as st, zipfile, importlib
from collections import defaultdict, Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import sdf, analyze
dm = importlib.import_module("map")
ALL = sdf.BUY_BOX + dm.WATCH + dm.AVOID
RENO = 52_416

def load_ext(years=sdf.YEARS, zips=sdf.BUY_BOX):
    out = []
    for year in years:
        with zipfile.ZipFile(sdf._fetch(year)) as zf:
            names = {n.upper(): n for n in zf.namelist()}
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
                        "date": (s.get("C7_Conveyance_Date") or "").strip()[:10], "price": price,
                        "dom": sdf._int(s.get("C8_Market_Days")), "owner_occ": sdf._yes(s, "J1_Primary_Residence"),
                        "distress": sdf._yes(s, "C1_Sheriff_Sale") or sdf._yes(s, "C2_Short_Sale") or sdf._yes(s, "C4_Auction"),
                        "av": sdf._int(p.get("P2_5_Total_AV")), "class": (p.get("P2_6_Prop_Class_Code") or "").strip(),
                        "address": (p.get("A5_Street1") or "").strip(), "city": (p.get("A5_City") or "Indianapolis").strip() or "Indianapolis",
                        "appr": sdf._yes(s, "E9_Appraisal_Value"), "special": (s.get("P2_15_Special_Circum") or "").strip().upper(),
                        "valid": sdf._yes(s, "P2_16_Valid_Trending")})
    return out

sales = load_ext(zips=ALL)
flips = sdf.find_flips(sales)
tier = {z: ("BUY" if z in sdf.BUY_BOX else "AVOID" if z in dm.AVOID else "WATCH") for z in ALL}
print(f"33 zips: {len(sales):,} sales · {len(flips):,} flips")

# ── (i) financed share (appraisal done) of retail exits ──
print("\n=== (i) E9 'appraisal done' = financed-buyer proxy — BUY BOX ===")
bb = [s for s in sales if s["zip"] in sdf.BUY_BOX]
oo = [s for s in bb if s["owner_occ"]]
print(f"all BUY BOX sales: appraisal {sum(s['appr'] for s in bb)/len(bb):.0%} · owner-occ buyers: {sum(s['appr'] for s in oo)/len(oo):.0%} · investor buyers: {sum(s['appr'] for s in bb if not s['owner_occ'])/max(1,len(bb)-len(oo)):.0%}")
ex = [f for f in flips if f["zip"] in sdf.BUY_BOX]
exo = [f for f in ex if f["owner_occ"]]
print(f"flip exits: all {sum(f['appr'] for f in ex)/len(ex):.0%} (n={len(ex)}) · to owner-occ {sum(f['appr'] for f in exo)/len(exo):.0%} (n={len(exo)}) · to investor {sum(f['appr'] for f in ex if not f['owner_occ'])/max(1,len(ex)-len(exo)):.0%}")
print("owner-occ flip exits by sell band:")
for lo, hi in [(0,200e3),(200e3,250e3),(250e3,300e3),(300e3,400e3),(400e3,9e9)]:
    g = [f for f in exo if lo <= f["sell"] < hi]
    if g: print(f"   ${lo/1e3:.0f}K–{hi/1e3:.0f}K  n={len(g):<4} appraisal {sum(f['appr'] for f in g)/len(g):.0%}")
print("by year (owner-occ flip exits):", {y: f"{sum(f['appr'] for f in exo if f['date'][:4]==y)/max(1,sum(1 for f in exo if f['date'][:4]==y)):.0%}" for y in ("2023","2024","2025","2026")})

# ── (ii) qualifying deal flow — last 24 months, all 33 zips ──
print("\n=== (ii) flips sold 2024-07-01..2026-06-30 that pass 0.70×sell − buy ≥ $52,416 ===")
recent = [f for f in flips if f["date"] >= "2024-07-01"]
last12 = [f for f in flips if f["date"] >= "2025-07-01"]
def passes(f): return 0.70 * f["sell"] - f["buy"] >= RENO
rows = []
for z in ALL:
    g = [f for f in recent if f["zip"] == z]
    if not g: continue
    p = [f for f in g if passes(f)]
    p12 = [f for f in last12 if f["zip"] == z and passes(f)]
    reno_type = [f for f in p if 3 <= f["months"] <= 9]
    rows.append((z, tier[z], len(g), len(p), len(p12), len(reno_type),
                 st.median([f["sell"] for f in g]), st.median([f["sell"] for f in p]) if p else 0,
                 st.median([0.70*f["sell"]-f["buy"] for f in p]) if p else 0))
rows.sort(key=lambda r: -r[3])
print(f"{'zip':<7}{'tier':<7}{'flips24m':>9}{'pass24m':>8}{'pass12m':>8}{'3-9mo':>7}{'med sell':>10}{'pass sell':>10}{'70% room':>10}")
for r in rows:
    print(f"{r[0]:<7}{r[1]:<7}{r[2]:>9}{r[3]:>8}{r[4]:>8}{r[5]:>7}{r[6]:>10,.0f}{r[7]:>10,.0f}{r[8]:>10,.0f}")
for t in ("BUY", "WATCH", "AVOID"):
    g = [f for f in last12 if tier[f["zip"]] == t]; p = [f for f in g if passes(f)]
    print(f"{t}: last 12 months {len(g)} flips, {len(p)} pass ({len(p)/max(1,len(g)):.0%})")
print("passing flips last 12 months by sell band, all 33 zips:")
for lo, hi in [(0,200e3),(200e3,250e3),(250e3,300e3),(300e3,400e3),(400e3,9e9)]:
    g = [f for f in last12 if lo <= f["sell"] < hi]; p = [f for f in g if passes(f)]
    print(f"   ${lo/1e3:.0f}K–{hi/1e3:.0f}K  flips {len(g):<4} pass {len(p):<4} ({len(p)/max(1,len(g)):.0%})  tiers of passing: {Counter(tier[f['zip']] for f in p)}")

# ── who captured the passing flips (buy-leg buyer name) ──
print("\n=== who bought the passing flips (last 24 months, 33 zips) ===")
buyers, titles = analyze._buyer_index(set(ALL)); ids = analyze._sale_ids(set(ALL))
pas = [f for f in recent if passes(f)]
names = Counter()
for f in pas:
    n = (buyers.get(ids.get((f["parcel"], f["buy_date"]))) or "?").upper()
    n = " ".join(n.split()[:2])
    names[n] += 1
top = names.most_common(15)
print(f"{len(pas)} passing flips · distinct buyers {len(names)} · top-15 share {sum(c for _,c in top)/len(pas):.0%}")
for n, c in top: print(f"   {c:>3}  {n}")
llc = sum(c for n, c in names.items() if "LLC" in n or "INC" in n or "CORP" in n or "TRUST" in n or "PROPERT" in n or "HOLDING" in n or "HOME" in n)
print(f"entity-named buyers: {llc/len(pas):.0%} · rest look like individuals")

# ── (iii) distress channels in BUY BOX, 2023-26 ──
print("\n=== (iii) P2_15 special circumstances, BUY BOX 2023-26 ===")
sp = Counter(s["special"][:22] for s in bb if s["special"])
print(sp.most_common(12), "of", len(bb), "sales")
print("flip buy-legs that were sheriff/short/auction:", f"{sum(f['buy_distress'] for f in ex)/len(ex):.1%}")

# ── (iv) 46236 exits — where does $301K sit ──
print("\n=== (iv) 46236 Geist flip exits 2023-26 ===")
g = sorted(f["sell"] for f in ex if f["zip"] == "46236")
print(f"n={len(g)} · p25 ${g[len(g)//4]:,.0f} · median ${st.median(g):,.0f} · p75 ${g[3*len(g)//4]:,.0f} · share of exits ≥$301K: {sum(1 for x in g if x>=301000)/len(g):.0%}")
gp = [f for f in ex if f["zip"] == "46236" and passes(f)]
print(f"passing in 46236 (4 yrs): {len(gp)} · of which 3-9 months hold: {sum(1 for f in gp if 3<=f['months']<=9)} · median 70%-room ${st.median([0.7*f['sell']-f['buy'] for f in gp]) if gp else 0:,.0f}")
print("data ends:", max(s["date"] for s in sales))
