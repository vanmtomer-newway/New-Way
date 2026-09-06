#!/usr/bin/env python3
"""
מנוע ARV — חיתום נכס בודד.

`sdf.py` טוען את השוק · `analyze.py` חוקר אותו · הקובץ הזה חותם נכס אחד.

למה קומפס ולא מכפיל: נבדק מחוץ למדגם (כיול 2023-24, מבחן 2025-26) שכל מכפיל
ברמת זיפ נכשל — AV×מכפיל 21.8% שגיאה, חציון מחיר הזיפ 21.3%, מחיר קנייה×מכפיל
17.3%. קומפס גאוגרפיים: 12.6%. ראה `decisions.md` (6.9.2026) ו-`arv.py backtest`.

🔴 ולכן המוצר הוא טווח, לא מספר. 12.6% על ARV של $301K הם $38K — יותר ממחצית
הרווח בעסקת הדוגמה. מי שקורא מכאן מספר בודד ומכניס אותו למודל, מרמה את עצמו.

    python3 arv.py comps 490101100001000001     # קומפס + טווח ARV
    python3 arv.py comps "5302 N ARLINGTON AVE"
    python3 arv.py deal "5302 N ARLINGTON AVE" --offer 158284 --reno 52416
    python3 arv.py backtest                     # מייצר מחדש את טבלת הדיוק
    python3 arv.py selftest
"""
import math, statistics as st, sys
from collections import defaultdict

import sdf

# ── פרמטרי הקומפס — הערכים שמדדו 12.6% ב-backtest ──────────────────────
K_COMPS    = 10       # כמה קומפס. 5 נותן 13.6%, 10 עם סינון AV נותן 12.6%
COMP_MONTHS = 12      # כמה אחורה. מעבר לזה הקומפ כבר לא מתמחר את השוק הנוכחי
AV_BAND    = 0.223    # ln(1.25) — שווי שומה ±25%, פרוקסי לגודל ואיכות

# ── דיוק מדוד. 🔧 להריץ `arv.py backtest` אחרי עדכון נתונים ולכייל ──────
ERR_P50 = 0.126       # שגיאה חציונית מוחלטת בחיזוי מחיר המכירה
ERR_P90 = 0.429

# ── פ"ל של עסקת מזומן. מקור: `CLAUDE.md` § "העסקה לדוגמה — 46236 Geist" ──
RULE          = 0.70      # כלל ה-70%
BUY_CLOSE_PCT = 0.0244    # $3,865 סגירה על רכישה של $158,284
EXIT_PCT      = 0.0825    # 8.25% מה-ARV — תיווך + הטבות + סגירה
HOLD_MONTH    = 763       # $5,449 על 7.14 חודשים
RENO_DEFAULT  = 52_416    # שיפוץ $32/sqft × 1,400 + 17% רזרבה
MONTHS_DEFAULT = 7.14


def _km(a, b):
    """מרחק מקורב בק"מ. במריון קאונטי הקירוב הזה מדויק דיו לדירוג קומפס."""
    return math.hypot((a[0] - b[0]) * 111.0, (a[1] - b[1]) * 85.0)


def market(years=sdf.YEARS, zips=sdf.BUY_BOX):
    """כל המכירות עם קואורדינטות, ממוינות לפי תאריך (לחיתוך חלון מהיר)."""
    sales = sdf.load(years, zips)
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


def arv_band(comps):
    """
    נקודת האומדן היא חציון הקומפס. הטווח נגזר מ**שגיאת החיזוי שנמדדה**,
    לא מפיזור הקומפס עצמם — כי זה מה שה-backtest אימת בפועל.
    """
    if not comps:
        return None
    px = sorted(c["price"] for c in comps)
    mid = st.median(px)
    return {
        "arv": mid,
        "low": mid * (1 - ERR_P50), "high": mid * (1 + ERR_P50),
        "worst": mid * (1 - ERR_P90),
        "comp_p25": px[len(px) // 4], "comp_p75": px[3 * len(px) // 4],
        "n": len(comps),
    }


def deal(arv, offer, reno=RENO_DEFAULT, months=MONTHS_DEFAULT):
    """פ"ל של עסקת מזומן. אין ריבית, אין נקודות, אין שעון מלווה."""
    basis = offer + offer * BUY_CLOSE_PCT + reno + months * HOLD_MONTH
    exit_costs = arv * EXIT_PCT
    profit = arv - exit_costs - basis
    return {"arv": arv, "basis": basis, "exit": exit_costs, "profit": profit,
            "roc": profit / basis, "offer_70": RULE * arv - reno}


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
             "   כרגע נתמכים רק נכסים שנמכרו ב-2023-2026 בזיפי ה-BUY BOX.\n"
             "   למודעה חיה צריך כתובת + קואורדינטות — ראה `tasks/todo.md` פאזה 2.")


def cmd_comps(sales, loc, args):
    subj = _resolve(sales, loc, args[0])
    today = max(s["date"] for s in loc)
    cs = find_comps(loc, subj["ll"], today, subj["av"], exclude=subj["parcel"])
    b = arv_band(cs)
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
    print(f"טווח סביר   ±12.6% (שגיאה חציונית): ${b['low']:>11,.0f} – ${b['high']:,.0f}")
    print(f"תרחיש p90   −42.9%:                 ${b['worst']:>11,.0f}")
    print(f"רבעוני הקומפס עצמם:                 ${b['comp_p25']:>11,.0f} – ${b['comp_p75']:,.0f}")
    print("🔴 האומדן אינו מספר. חתום על הטווח התחתון.")


def cmd_deal(sales, loc, args):
    query = args[0]
    offer = _arg(args, "--offer")
    reno = _arg(args, "--reno") or RENO_DEFAULT
    months = _arg(args, "--months") or MONTHS_DEFAULT
    subj = _resolve(sales, loc, query)
    today = max(s["date"] for s in loc)
    b = arv_band(find_comps(loc, subj["ll"], today, subj["av"],
                            exclude=subj["parcel"]))
    if not b:
        return print("אין קומפס — אי אפשר לחתום.")
    print(f"\n▌ {subj['address']}  ·  {subj['zip']}")
    _print_band(b)

    if offer is None:
        offer = RULE * b["arv"] - reno
        print(f"\nלא ניתנה הצעה — משתמש בכלל ה-70% על האומדן: ${offer:,.0f}")
    print(f"\n{'─'*66}\nטווח הרווח — מזומן מלא · שיפוץ ${reno:,.0f} · {months} ח' החזקה")
    print(f"הצעה ${offer:,.0f}\n")
    print(f"{'תרחיש ARV':<22}{'ARV':>11}{'בסיס':>11}{'רווח':>11}{'על העלות':>11}{'70% מתיר':>11}")
    for lbl, arv in (("p90 שלילי  −42.9%", b["worst"]), ("תחתון  −12.6%", b["low"]),
                     ("אומדן", b["arv"]), ("עליון  +12.6%", b["high"])):
        d = deal(arv, offer, reno, months)
        flag = "" if d["profit"] > 0 else "  ❌"
        print(f"{lbl:<22}{d['arv']:>11,.0f}{d['basis']:>11,.0f}"
              f"{d['profit']:>11,.0f}{d['roc']:>10.1%}{d['offer_70']:>11,.0f}{flag}")
    d = deal(b["low"], offer, reno, months)
    print(f"\n👉 בתרחיש התחתון הרווח הוא ${d['profit']:,.0f}. "
          f"{'זו העסקה.' if d['profit'] > 0 else '🔴 העסקה מפסידה בתרחיש התחתון.'}")


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
            for z in sdf.BUY_BOX}
    medpx = {z: st.median([s["price"] for s in sales
                           if s["zip"] == z and s["date"] < CUT] or [1])
             for z in sdf.BUY_BOX}
    bmult = {z: st.median([f["sell"] / f["buy"] for a, f in tr if f["zip"] == z] or [1])
             for z in sdf.BUY_BOX}

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
    print(f"\nהקבועים בקובץ: ERR_P50={ERR_P50:.1%} · ERR_P90={ERR_P90:.1%}")
    if abs(best[0] - ERR_P50) > 0.015 or abs(best[1] - ERR_P90) > 0.03:
        print(f"🔧 סטייה. לכייל ל-ERR_P50={best[0]:.3f} · ERR_P90={best[1]:.3f}")
    else:
        print("✅ הקבועים תואמים למדידה.")


def selftest():
    # מרחק: קו רוחב אחד ≈ 111 ק"מ
    assert abs(_km((39.0, -86.0), (40.0, -86.0)) - 111) < 1
    assert _km((39.0, -86.0), (39.0, -86.0)) == 0

    # פ"ל: משחזר את עסקת הדוגמה מ-CLAUDE.md § 46236 Geist, מזומן מלא
    d = deal(301_000, 158_284, 52_416, 7.14)
    assert abs(d["basis"] - 220_010) < 50, d["basis"]      # התיעוד: $220,014
    assert abs(d["offer_70"] - 158_284) < 1, d["offer_70"]  # התיעוד: $158,284
    assert abs(d["profit"] - 56_158) < 50, d["profit"]
    # רווח יורד ב-ARV נמוך יותר, וכל דולר של ARV שווה יותר מדולר של רווח
    # (כי עלויות היציאה גם הן אחוז מה-ARV)
    lo = deal(301_000 * 0.874, 158_284, 52_416, 7.14)
    assert lo["profit"] < d["profit"]
    assert abs((d["profit"] - lo["profit"]) / (301_000 * 0.126) - (1 - EXIT_PCT)) < 1e-9

    # הטווח נגזר מהשגיאה המדודה, ונקודת האומדן היא חציון הקומפס
    b = arv_band([{"price": p, "ll": (0, 0)} for p in (200_000, 250_000, 300_000)])
    assert b["arv"] == 250_000 and b["n"] == 3
    assert abs(b["low"] - 250_000 * (1 - ERR_P50)) < 1

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
