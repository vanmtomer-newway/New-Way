#!/usr/bin/env python3
"""
מנוע ARV — חיתום נכס בודד.

`sdf.py` טוען את השוק · `analyze.py` חוקר אותו · הקובץ הזה חותם נכס אחד.

למה קומפס ולא מכפיל: נבדק מחוץ למדגם (כיול 2023-24, מבחן 2025-26) שכל מכפיל
ברמת זיפ נכשל — AV×מכפיל 21.8% שגיאה, חציון מחיר הזיפ 21.3%, מחיר קנייה×מכפיל
17.3%. קומפס גאוגרפיים: 12.4% ב-BUY BOX, **18.7% בשכבה הצפונית** — הטווח נקבע לפי
השכבה של הזיפ. ראה `decisions.md` (6.9.2026) ו-`arv.py backtest`.

🔴 ולכן המוצר הוא טווח, לא מספר. 12.4% על ARV של $301K הם $37K — יותר ממחצית
הרווח בעסקת הדוגמה. מי שקורא מכאן מספר בודד ומכניס אותו למודל, מרמה את עצמו.

    python3 arv.py comps 490101100001000001     # קומפס + טווח ARV
    python3 arv.py comps "5302 N ARLINGTON AVE"
    python3 arv.py deal "5302 N ARLINGTON AVE" --offer 158284 --reno 52416
    python3 arv.py deal "5302 N ARLINGTON AVE" --sqft 1400 --year 1960   # שיפוץ לפי שטח ושנה
    python3 arv.py backtest                     # מייצר מחדש את טבלת הדיוק
    python3 arv.py selftest
"""
import csv, math, os, statistics as st, sys
from collections import defaultdict
from datetime import date

import sdf

# ── פרמטרי הקומפס — הערכים שמדדו 12.6% ב-backtest ──────────────────────
K_COMPS    = 10       # כמה קומפס. 5 נותן 13.6%, 10 עם סינון AV נותן 12.6%
COMP_MONTHS = 12      # כמה אחורה. מעבר לזה הקומפ כבר לא מתמחר את השוק הנוכחי
AV_BAND    = 0.223    # ln(1.25) — שווי שומה ±25%, פרוקסי לגודל ואיכות

# ── דיוק מדוד, לפי שכבה: (שגיאה חציונית, p90). 🔧 `arv.py backtest` אחרי עדכון נתונים ──
ERR = {"BUY":   (0.124, 0.429),   # BUY BOX, 8 זיפים, n=540
       "NORTH": (0.187, 0.574)}   # שכבה צפונית, n=284 — מלאי הטרוגני ותוספות בנייה. הטווח שם רחב ב-50%


def err_for(zip5):
    """מחוץ ל-BUY BOX — הערכים השמרניים של הצפון."""
    return ERR["BUY"] if zip5 in sdf.BUY_BOX else ERR["NORTH"]

# ── פ"ל של עסקת מזומן. מקור: `CLAUDE.md` § "העסקה לדוגמה — 46236 Geist" ──
LOG           = os.path.join(os.path.dirname(os.path.abspath(__file__)), "underwriting_log.csv")
BUY_CLOSE     = 3_865     # טייטל $2,000 + בדיקה $900 + רישום $65 + היתרים $900. קבוע, לא אחוז (Excel B20:B23)
EXIT_PCT      = 0.0825    # 8.25% מה-ARV — תיווך + הטבות + סגירה
HOLD_MONTH    = 763       # $5,449 על 7.14 חודשים
MONTHS_DEFAULT = 7.14
RENO_SQFT     = 32        # $/sqft, סקופ בינוני, אינדי 2026 (Excel B12)
RENO_PRE78    = 8         # תוספת למלאי טרום-1978: עופרת, אסבסט, מגולוון (Excel B13)
RENO_RESERVE  = 0.17      # רזרבה (Excel B16)
RENO_DEFAULT  = 52_416    # = reno_budget(1400, 1993). כשלא ידוע שטח


def reno_budget(sqft, year=None):
    """תקציב שיפוץ כמו בגיליון מודל_עסקה: sqft × ($32 [+$8 לפני 1978]) × 1.17."""
    if not sqft:
        return RENO_DEFAULT
    rate = RENO_SQFT + (RENO_PRE78 if year and year < 1978 else 0)
    return sqft * rate * (1 + RENO_RESERVE)


def _km(a, b):
    """מרחק מקורב בק"מ. במריון קאונטי הקירוב הזה מדויק דיו לדירוג קומפס."""
    return math.hypot((a[0] - b[0]) * 111.0, (a[1] - b[1]) * 85.0)


def market(years=sdf.YEARS, zips=sdf.TARGET):
    """כל המכירות עם קואורדינטות, ממוינות לפי תאריך (לחיתוך חלון מהיר)."""
    sales = sdf.load(years, zips)
    sdf.stamp(sales)
    geo = sdf.geocode(sales)
    for s in sales:
        s["ll"] = geo.get(sdf._geo_key(s))
    loc = sorted((s for s in sales if s["ll"]), key=lambda s: s["date"])
    return sales, loc


def find_comps(loc, ll, before, av=0, k=K_COMPS, months=COMP_MONTHS,
               av_band=AV_BAND, exclude=""):
    """
    הקומפס: מכירות לקונה תופס בעצמו, בחלון של `months` לפני `before`,
    הקרובות ביותר גאוגרפית, ואם ידוע שווי שומה — גם דומות בגודל.

    קונה תופס הוא הפרוקסי ל"נמכר משופץ במחיר קמעונאי". זו ההגדרה שמדדה 12.6%;
    שינוי שלה מחייב להריץ `backtest` מחדש.
    """
    pool = [s for s in loc if s["owner_occ"] and s["parcel"] != exclude
            and 0 < sdf._months(s["date"], before) <= months]
    if av and av_band:
        pool = [s for s in pool if s["av"] > 0
                and abs(math.log(s["av"] / av)) < av_band]
    return sorted(pool, key=lambda s: _km(ll, s["ll"]))[:k]


def arv_band(comps, zip5=""):
    """
    נקודת האומדן היא חציון הקומפס. הטווח נגזר מ**שגיאת החיזוי שנמדדה** לשכבה
    של הזיפ, לא מפיזור הקומפס עצמם — כי זה מה שה-backtest אימת בפועל.
    """
    if not comps:
        return None
    px = sorted(c["price"] for c in comps)
    mid = st.median(px)
    p50, p90 = err_for(zip5)
    return {
        "arv": mid, "p50": p50, "p90": p90,
        "low": mid * (1 - p50), "high": mid * (1 + p50),
        "worst": mid * (1 - p90),
        "comp_p25": px[len(px) // 4], "comp_p75": px[3 * len(px) // 4],
        "n": len(comps),
    }


def deal(arv, offer, reno=RENO_DEFAULT, months=MONTHS_DEFAULT, zip5=""):
    """פ"ל של עסקת מזומן. אין ריבית, אין נקודות, אין שעון מלווה. הכלל לפי שכבת הזיפ."""
    basis = offer + BUY_CLOSE + reno + months * HOLD_MONTH
    exit_costs = arv * EXIT_PCT
    profit = arv - exit_costs - basis
    rule = sdf.rule_for(zip5)
    return {"arv": arv, "basis": basis, "exit": exit_costs, "profit": profit,
            "roc": profit / basis, "rule": rule, "offer_rule": rule * arv - reno}


# ─────────────────────────── פקודות ───────────────────────────

def _resolve(sales, loc, query):
    """חלקה או כתובת -> נושא החיתום. כתובת שאינה בנתונים מגואקדת בנפרד."""
    q = query.strip().upper()
    hits = [s for s in sales if s["parcel"] == q or s["address"].upper() == q]
    if hits:
        s = max(hits, key=lambda x: x["date"])
        if not s["ll"]:
            sys.exit(f"⚠️ '{query}' לא גואקד — אין קואורדינטות, אין קומפס.")
        return {"ll": s["ll"], "av": s["av"], "zip": s["zip"],
                "address": s["address"], "parcel": s["parcel"],
                "note": f"נמצא בנתונים · מכירה אחרונה {s['date']} ב-${s['price']:,.0f}"}
    sys.exit(f"⚠️ '{query}' לא נמצא ב-{len(sales):,} המכירות.\n"
             "   כאן נתמכים רק נכסים שנמכרו ב-2023-2026 בזיפי היעד.\n"
             "   למודעה חיה: python3 listings.py <CSV של Redfin> --comps \"<כתובת>\"")


def cmd_comps(sales, loc, args):
    subj = _resolve(sales, loc, args[0])
    today = max(s["date"] for s in loc)
    cs = find_comps(loc, subj["ll"], today, subj["av"], exclude=subj["parcel"])
    b = arv_band(cs, subj["zip"])
    print(f"\n▌ {subj['address']}  ·  {subj['zip']}  ·  שווי שומה ${subj['av']:,}")
    print(f"  {subj['note']}\n")
    if not b:
        return print("אין קומפס בטווח. הרפה את הסינון או הרחב את החלון.")
    print(f"{'תאריך':<12}{'מרחק':>7}{'מחיר':>11}{'שומה':>11}  כתובת")
    for c in sorted(cs, key=lambda c: _km(subj["ll"], c["ll"])):
        print(f"{c['date']:<12}{_km(subj['ll'], c['ll']):>6.2f}ק\"מ"
              f"{c['price']:>11,.0f}{c['av']:>11,.0f}  {c['address'][:34]}")
    _print_band(b)


def _print_band(b):
    print(f"\n{'─'*66}")
    print(f"אומדן ARV (חציון {b['n']} קומפס):        ${b['arv']:>11,.0f}")
    print(f"טווח סביר   ±{b['p50']:.1%} (שגיאה חציונית): ${b['low']:>11,.0f} – ${b['high']:,.0f}")
    print(f"תרחיש p90   −{b['p90']:.1%}:                 ${b['worst']:>11,.0f}")
    print(f"רבעוני הקומפס עצמם:                 ${b['comp_p25']:>11,.0f} – ${b['comp_p75']:,.0f}")
    print("🔴 האומדן אינו מספר. חתום על הטווח התחתון.")


def cmd_deal(sales, loc, args):
    query = args[0]
    offer = _arg(args, "--offer")
    sqft, year = _arg(args, "--sqft"), _arg(args, "--year")
    reno = _arg(args, "--reno") or reno_budget(sqft, year)
    how = ("נתון" if _arg(args, "--reno") else
           f"{sqft:,.0f} sqft × ${RENO_SQFT + (RENO_PRE78 if year and year < 1978 else 0)} × {1 + RENO_RESERVE:.2f}"
           if sqft else "ברירת מחדל: 1,400 sqft")
    months = _arg(args, "--months") or MONTHS_DEFAULT
    subj = _resolve(sales, loc, query)
    today = max(s["date"] for s in loc)
    b = arv_band(find_comps(loc, subj["ll"], today, subj["av"],
                            exclude=subj["parcel"]), subj["zip"])
    if not b:
        return print("אין קומפס — אי אפשר לחתום.")
    print(f"\n▌ {subj['address']}  ·  {subj['zip']}")
    _print_band(b)

    rule = sdf.rule_for(subj["zip"])
    if offer is None:
        offer = rule * b["arv"] - reno
        print(f"\nלא ניתנה הצעה — משתמש בכלל ה-{rule:.0%} על האומדן: ${offer:,.0f}")
    print(f"\n{'─'*66}\nטווח הרווח — מזומן מלא · שיפוץ ${reno:,.0f} ({how}) · {months} ח' החזקה")
    print(f"הצעה ${offer:,.0f}\n")
    print(f"{'תרחיש ARV':<22}{'ARV':>11}{'בסיס':>11}{'רווח':>11}{'על העלות':>11}{format(rule, '.0%') + ' מתיר':>11}")
    for lbl, arv in ((f"p90 שלילי  −{b['p90']:.1%}", b["worst"]), (f"תחתון  −{b['p50']:.1%}", b["low"]),
                     ("אומדן", b["arv"]), (f"עליון  +{b['p50']:.1%}", b["high"])):
        d = deal(arv, offer, reno, months, subj["zip"])
        flag = "" if d["profit"] > 0 else "  ❌"
        print(f"{lbl:<22}{d['arv']:>11,.0f}{d['basis']:>11,.0f}"
              f"{d['profit']:>11,.0f}{d['roc']:>10.1%}{d['offer_rule']:>11,.0f}{flag}")
    d = deal(b["low"], offer, reno, months, subj["zip"])
    print(f"\n👉 בתרחיש התחתון הרווח הוא ${d['profit']:,.0f}. "
          f"{'זו העסקה.' if d['profit'] > 0 else '🔴 העסקה מפסידה בתרחיש התחתון.'}")
    _log(subj, b, offer, reno, how, months, d, deal(b["arv"], offer, reno, months, subj["zip"]))


def _log(subj, b, offer, reno, how, months, low, mid):
    """
    כל הרצת deal נרשמת ב-underwriting_log.csv (בריפו). ה-backtest מודד את השיטה
    על פליפים של אחרים; זה הסט היחיד שימדוד אותה על שלך. את עמודת outcome ממלאים ביד.
    """
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "address", "zip", "parcel", "comps", "arv", "arv_low", "offer", "reno",
                        "reno_basis", "months", "profit_low", "profit_mid", "verdict", "outcome"])
        w.writerow([date.today().isoformat(), subj["address"], subj["zip"], subj["parcel"], b["n"],
                    round(b["arv"]), round(b["low"]), round(offer), round(reno), how, months,
                    round(low["profit"]), round(mid["profit"]), "pass" if low["profit"] > 0 else "fail", ""])
    print(f"   נרשם ב-{os.path.basename(LOG)}")


def _arg(args, flag):
    return float(args[args.index(flag) + 1]) if flag in args else None


def cmd_backtest(sales, loc, args):
    """
    מייצר מחדש את טבלת הדיוק. כיול 2023-24, מבחן 2025-26 — מחוץ למדגם.
    זו ההוכחה למספרים ב-`decisions.md`, ולא מספר שנרשם פעם אחת במסמך.
    """
    CUT = "2025-01-01"
    avmap = {(s["parcel"], s["date"]): s["av"] for s in sales}
    flips = [f for f in sdf.find_flips(sales) if f["ll"] and f["av"] > 1000]
    pairs = [(avmap.get((f["parcel"], f["buy_date"]), 0), f) for f in flips]
    pairs = [(a, f) for a, f in pairs if a > 1000]
    tr = [(a, f) for a, f in pairs if f["date"] < CUT]
    te = [(a, f) for a, f in pairs if f["date"] >= CUT]
    print(f"\nכיול: {len(tr):,} פליפים 2023-24  ·  מבחן: {len(te):,} פליפים 2025-26")
    print("שגיאה = |חיזוי − מחיר מכירה בפועל| ÷ מחיר מכירה בפועל\n")

    mult = {z: st.median([f["sell"] / a for a, f in tr if f["zip"] == z] or [1])
            for z in sdf.TARGET}
    medpx = {z: st.median([s["price"] for s in sales
                           if s["zip"] == z and s["date"] < CUT] or [1])
             for z in sdf.TARGET}
    bmult = {z: st.median([f["sell"] / f["buy"] for a, f in tr if f["zip"] == z] or [1])
             for z in sdf.TARGET}

    def score(fn):
        e = sorted(x for x in (fn(a, f) for a, f in te) if x is not None)
        return st.median(e), e[int(len(e) * .9)], len(e)

    def comp_pred(a, f, k, band):
        cs = find_comps(loc, f["ll"], f["date"], f["av"] if band else 0,
                        k=k, av_band=band, exclude=f["parcel"])
        if not cs:
            return None
        return abs(st.median([c["price"] for c in cs]) - f["sell"]) / f["sell"]

    rows = [
        ("AV × מכפיל הזיפ", lambda a, f: abs(a * mult[f["zip"]] - f["sell"]) / f["sell"]),
        ("חציון מחיר הזיפ", lambda a, f: abs(medpx[f["zip"]] - f["sell"]) / f["sell"]),
        ("מחיר קנייה × מכפיל", lambda a, f: abs(f["buy"] * bmult[f["zip"]] - f["sell"]) / f["sell"]),
        ("5 קומפס", lambda a, f: comp_pred(a, f, 5, None)),
        (f"{K_COMPS} קומפס + AV ±25%", lambda a, f: comp_pred(a, f, K_COMPS, AV_BAND)),
    ]
    print(f"{'שיטה':<24}{'חציונית':>11}{'p90':>9}{'n':>7}")
    best = None
    for lbl, fn in rows:
        m, p90, n = score(fn)
        print(f"{lbl:<24}{m:>10.1%}{p90:>9.1%}{n:>7,}")
        best = (m, p90)
    print("\nלפי שכבה (השיטה הנבחרת) מול הקבועים בקובץ:")
    for lbl, key, zz in (("BUY BOX", "BUY", sdf.BUY_BOX), ("שכבה צפונית", "NORTH", sdf.NORTH)):
        e = sorted(x for x in (comp_pred(a, f, K_COMPS, AV_BAND) for a, f in te if f["zip"] in zz)
                   if x is not None)
        if not e:
            continue
        m, p90 = st.median(e), e[int(len(e) * .9)]
        ok = abs(m - ERR[key][0]) <= 0.015 and abs(p90 - ERR[key][1]) <= 0.03
        print(f"   {lbl}: חציונית {m:.1%} · p90 {p90:.1%} · n {len(e)}   "
              f"{'✅ תואם' if ok else f'🔧 לכייל ל-({m:.3f}, {p90:.3f})'}")


def selftest():
    # מרחק: קו רוחב אחד ≈ 111 ק"מ
    assert abs(_km((39.0, -86.0), (40.0, -86.0)) - 111) < 1
    assert _km((39.0, -86.0), (39.0, -86.0)) == 0

    # פ"ל: משחזר את עסקת הדוגמה מ-CLAUDE.md § 46236 Geist, מזומן מלא
    d = deal(301_000, 158_284, 52_416, 7.14, "46236")
    assert abs(d["basis"] - 220_010) < 50, d["basis"]      # התיעוד: $220,014
    assert d["rule"] == 0.72 and abs(d["offer_rule"] - (0.72 * 301_000 - 52_416)) < 1, d["offer_rule"]
    dn = deal(301_000, 0, zip5="46220")                    # הצפון: 69%
    assert dn["rule"] == 0.69 and abs(dn["offer_rule"] - (0.69 * 301_000 - 52_416)) < 1
    assert abs(d["profit"] - 56_158) < 50, d["profit"]
    # עלויות רכישה קבועות: הצעה כפולה לא מכפילה אותן (Excel B20:B23, לא אחוז)
    assert deal(301_000, 2 * 158_284)["basis"] - d["basis"] == 158_284
    # תקציב שיפוץ כמו באקסל: 1,400 sqft × $32 × 1.17 = $52,416; לפני 1978 +$8/sqft
    assert abs(reno_budget(1400, 1993) - RENO_DEFAULT) < 1
    assert abs(reno_budget(1400, 1960) - 65_520) < 1
    assert reno_budget(0) == RENO_DEFAULT and reno_budget(None) == RENO_DEFAULT
    # רווח יורד ב-ARV נמוך יותר, וכל דולר של ARV שווה יותר מדולר של רווח
    # (כי עלויות היציאה גם הן אחוז מה-ARV)
    lo = deal(301_000 * 0.874, 158_284, 52_416, 7.14)
    assert lo["profit"] < d["profit"]
    assert abs((d["profit"] - lo["profit"]) / (301_000 * 0.126) - (1 - EXIT_PCT)) < 1e-9

    # הטווח נגזר מהשגיאה המדודה, ונקודת האומדן היא חציון הקומפס
    b = arv_band([{"price": p, "ll": (0, 0)} for p in (200_000, 250_000, 300_000)], "46219")
    assert b["arv"] == 250_000 and b["n"] == 3
    assert abs(b["low"] - 250_000 * (1 - ERR["BUY"][0])) < 1
    # הצפון מקבל טווח רחב יותר; זיפ לא מוכר מקבל את השמרני
    bn = arv_band([{"price": p, "ll": (0, 0)} for p in (200_000, 250_000, 300_000)], "46220")
    assert bn["low"] < b["low"] and bn["worst"] < b["worst"] and err_for("99999") == ERR["NORTH"]

    # קומפס: רק קונה תופס, רק לפני התאריך, רק בחלון, והנכס עצמו מוחרג
    loc = [
        {"date": "2025-01-01", "price": 200_000, "ll": (39.0, -86.0), "owner_occ": True,  "av": 190_000, "parcel": "A"},
        {"date": "2025-02-01", "price": 900_000, "ll": (39.0, -86.0), "owner_occ": False, "av": 190_000, "parcel": "B"},  # משקיע
        {"date": "2020-01-01", "price": 900_000, "ll": (39.0, -86.0), "owner_occ": True,  "av": 190_000, "parcel": "C"},  # ישן מדי
        {"date": "2025-03-01", "price": 900_000, "ll": (39.0, -86.0), "owner_occ": True,  "av": 190_000, "parcel": "D"},  # הנכס עצמו
        {"date": "2025-02-15", "price": 900_000, "ll": (39.0, -86.0), "owner_occ": True,  "av":  10_000, "parcel": "E"},  # שומה רחוקה
    ]
    cs = find_comps(loc, (39.0, -86.0), "2025-06-01", av=190_000, exclude="D")
    assert [c["parcel"] for c in cs] == ["A"], [c["parcel"] for c in cs]
    # בלי סינון שומה, E נכנס
    assert {c["parcel"] for c in find_comps(loc, (39.0, -86.0), "2025-06-01",
                                            av=190_000, av_band=None, exclude="D")} == {"A", "E"}
    # הקרוב יותר קודם
    far = dict(loc[0], parcel="F", ll=(39.5, -86.0))
    assert find_comps(loc + [far], (39.0, -86.0), "2025-06-01", exclude="D")[0]["parcel"] == "A"
    print("selftest: ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "comps"
    if cmd == "selftest":
        selftest()
    elif cmd in ("comps", "deal", "backtest"):
        if cmd != "backtest" and len(sys.argv) < 3:
            sys.exit(f"שימוש: python3 arv.py {cmd} <חלקה | \"כתובת\">")
        sales, loc = market()
        {"comps": cmd_comps, "deal": cmd_deal, "backtest": cmd_backtest}[cmd](
            sales, loc, sys.argv[2:])
    else:
        sys.exit(__doc__)
