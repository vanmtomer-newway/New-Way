"""Backtest experiment: do unused SDF fields improve comp-based ARV error?"""
import os, sys, math, statistics as st, zipfile
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import sdf, arv

def load_ext(years=sdf.YEARS, zips=sdf.BUY_BOX):
    out = []
    for year in years:
        with zipfile.ZipFile(sdf._fetch(year)) as zf:
            names = {n.upper(): n for n in zf.namelist()}
            parcels = defaultdict(list)
            for p in sdf._rows(zf, names["SALEPARCEL.TXT"]):
                parcels[(p.get("SDF_ID") or "").strip()].append(p)
            for s in sdf._rows(zf, names["SALEDISC.TXT"]):
                if (s.get("County_ID") or "").strip() != sdf.MARION_COUNTY_ID: continue
                if not sdf._yes(s, "C10_Residential_Property"): continue
                price = sdf._money(s, "E1_Sales_Price")
                if price < 10_000: continue
                for p in parcels.get((s.get("SDF_ID") or "").strip(), []):
                    z = (p.get("A5_ZipCode") or "").strip()[:5]
                    if z not in zips: continue
                    out.append({
                        "zip": z, "parcel": (p.get("A1_Parcel_Number") or "").strip(),
                        "date": (s.get("C7_Conveyance_Date") or "").strip()[:10], "price": price,
                        "dom": sdf._int(s.get("C8_Market_Days")),
                        "owner_occ": sdf._yes(s, "J1_Primary_Residence"),
                        "distress": sdf._yes(s, "C1_Sheriff_Sale") or sdf._yes(s, "C2_Short_Sale") or sdf._yes(s, "C4_Auction"),
                        "av": sdf._int(p.get("P2_5_Total_AV")), "av_imp": sdf._int(p.get("P2_3_AV_Improvement")),
                        "class": (p.get("P2_6_Prop_Class_Code") or "").strip(),
                        "address": (p.get("A5_Street1") or "").strip(),
                        "city": (p.get("A5_City") or "Indianapolis").strip() or "Indianapolis",
                        "valid": sdf._yes(s, "P2_16_Valid_Trending"),
                        "newc": sdf._yes(s, "D1_Physical_Change"),
                        "pcdesc": (s.get("D1_Physical_Change_Desc") or "").strip(),
                        "appr": sdf._yes(s, "E9_Appraisal_Value"),
                        "rental": sdf._yes(s, "D2_Res_Rental"),
                        "nbhd": (p.get("P2_7_Neighborhood_Code") or "").strip(),
                        "subdiv": (p.get("A1_Subdiv_Name") or "").strip().upper(),
                        "received": (s.get("P2_14_Date_Received") or "").strip()[:10],
                        "use": (s.get("D3_Planned_Use") or "").strip(),
                    })
    return out

sales = load_ext()
geo = sdf.geocode(sales)
for s in sales: s["ll"] = geo.get(sdf._geo_key(s))
loc = sorted((s for s in sales if s["ll"]), key=lambda s: s["date"])
print(f"sales {len(sales):,} · geocoded {len(loc):,}")
print(f"valid_trending Y: {sum(s['valid'] for s in sales)/len(sales):.1%} · newc Y: {sum(s['newc'] for s in sales)/len(sales):.1%}"
      f" · appraisal Y: {sum(s['appr'] for s in sales)/len(sales):.1%} · nbhd filled: {sum(bool(s['nbhd']) for s in sales)/len(sales):.1%}"
      f" · subdiv filled: {sum(bool(s['subdiv']) for s in sales)/len(sales):.1%}")
print("max conveyance date in data:", max(s["date"] for s in sales))

flips_all = sdf.find_flips(sales)
flip_exit = {(f["parcel"], f["date"]) for f in flips_all}
CUT = "2025-01-01"
avmap = {(s["parcel"], s["date"]): s["av"] for s in sales}
flips = [f for f in flips_all if f["ll"] and f["av"] > 1000 and avmap.get((f["parcel"], f["buy_date"]), 0) > 1000]
te = [f for f in flips if f["date"] >= CUT]
print(f"test flips 2025-26: {len(te)}")

# precompute per-flip candidate pool: all sales up to 18 months before, excluding own parcel, sorted by distance
def km(a, b): return math.hypot((a[0]-b[0])*111.0, (a[1]-b[1])*85.0)
pools = []
for f in te:
    pool = [s for s in loc if s["parcel"] != f["parcel"] and 0 < sdf._months(s["date"], f["date"]) <= 18]
    for s in pool: s_d = None
    pool = sorted(pool, key=lambda s: km(f["ll"], s["ll"]))
    pools.append([(km(f["ll"], s["ll"]), s) for s in pool[:400]])

def run(name, k=10, months=12, av_band=arv.AV_BAND, owner=True, valid=False, no_newc=False,
        nbhd=False, subdiv=False, fexit=False, max_km=None, imp=False, all_buyers=False, min_n=1):
    errs, short = [], 0
    for f, pool in zip(te, pools):
        cs = [(d, s) for d, s in pool if sdf._months(s["date"], f["date"]) <= months]
        if not all_buyers and owner: cs = [(d, s) for d, s in cs if s["owner_occ"]]
        if valid: cs = [(d, s) for d, s in cs if s["valid"]]
        if no_newc: cs = [(d, s) for d, s in cs if not s["newc"]]
        if fexit: cs = [(d, s) for d, s in cs if (s["parcel"], s["date"]) in flip_exit]
        if av_band:
            ref = f["av_imp"] if imp else f["av"]
            key = "av_imp" if imp else "av"
            if ref > 0:
                cs = [(d, s) for d, s in cs if s[key] > 0 and abs(math.log(s[key]/ref)) < av_band]
        if max_km: cs = [(d, s) for d, s in cs if d <= max_km]
        if nbhd and f["nbhd"]:
            same = [(d, s) for d, s in cs if s["nbhd"] == f["nbhd"]]
            cs = same + [(d, s) for d, s in cs if s["nbhd"] != f["nbhd"]]
        if subdiv and f["subdiv"]:
            same = [(d, s) for d, s in cs if s["subdiv"] == f["subdiv"]]
            cs = same + [(d, s) for d, s in cs if s["subdiv"] != f["subdiv"]]
        cs = cs[:k]
        if len(cs) < min_n: short += 1; continue
        pred = st.median([s["price"] for d, s in cs])
        errs.append(abs(pred - f["sell"]) / f["sell"])
    errs.sort()
    if not errs: return print(f"{name:<52} —")
    p50, p90 = st.median(errs), errs[int(len(errs)*.9)]
    within = sum(1 for e in errs if e <= 0.10)/len(errs)
    print(f"{name:<52}{p50:>8.1%}{p90:>8.1%}{within:>9.0%}{len(errs):>6}{short:>6}")

print(f"\n{'variant':<52}{'p50':>8}{'p90':>8}{'≤10%':>9}{'n':>6}{'skip':>6}")
run("BASELINE 10 comps, owner_occ, AV±25%, 12mo (=arv.py)")
run("+ valid_trending only", valid=True)
run("+ exclude new construction", no_newc=True)
run("+ valid + no newc", valid=True, no_newc=True)
run("+ same assessor nbhd preferred", nbhd=True)
run("+ same subdivision preferred", subdiv=True)
run("+ valid + no newc + nbhd", valid=True, no_newc=True, nbhd=True)
run("+ valid + no newc + subdiv", valid=True, no_newc=True, subdiv=True)
run("+ max 1.5km", max_km=1.5)
run("+ max 1.0km", max_km=1.0)
run("+ valid + nbhd + max 1.5km", valid=True, nbhd=True, max_km=1.5)
run("flip-exit comps only (owner_occ)", fexit=True)
run("flip-exit comps only, 18mo", fexit=True, months=18)
run("flip-exit, 18mo, no AV band", fexit=True, months=18, av_band=None)
run("flip-exit, 18mo, AV±40%", fexit=True, months=18, av_band=math.log(1.4))
run("flip-exit + nbhd, 18mo", fexit=True, months=18, nbhd=True)
run("AV band on improvement AV instead of total", imp=True)
run("all buyers (not just owner_occ), valid", all_buyers=True, valid=True)
run("k=5", k=5)
run("k=15", k=15)
run("k=20", k=20)
run("6 months window", months=6)
run("18 months window", months=18)
run("AV±15%", av_band=math.log(1.15))
run("AV±35%", av_band=math.log(1.35))
run("BEST GUESS: valid+no newc+nbhd+AV±15%+k=10", valid=True, no_newc=True, nbhd=True, av_band=math.log(1.15))
run("valid+no newc+nbhd+AV±15%+k=15", valid=True, no_newc=True, nbhd=True, av_band=math.log(1.15), k=15)
run("valid+no newc+subdiv+AV±15%+k=10", valid=True, no_newc=True, subdiv=True, av_band=math.log(1.15))

# --- reporting lag: how current is the data? ---
lags = sorted(sdf._months(s["date"], s["received"])*30.4 for s in sales if s["received"] and s["received"] > s["date"])
print(f"\nrecording lag (conveyance→received), days: p50 {st.median(lags):.0f} · p75 {lags[int(len(lags)*.75)]:.0f} · p90 {lags[int(len(lags)*.9)]:.0f}")
by_month = defaultdict(int)
for s in sales: by_month[s["date"][:7]] += 1
print("sales per month, last 10:", sorted(by_month.items())[-10:])

# --- what does D1 desc say (renovation mentions?) ---
from collections import Counter
c = Counter(s["pcdesc"].lower()[:40] for s in sales if s["pcdesc"])
print("\nD1_Physical_Change_Desc top 25:", c.most_common(25))
reno_words = ("remod", "renov", "rehab", "updat", "flip", "repair")
print("desc mentions renovation:", sum(v for k2, v in c.items() if any(w in k2 for w in reno_words)))
print("\nD3_Planned_Use top 12:", Counter(s["use"].lower() for s in sales).most_common(12))
