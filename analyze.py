#!/usr/bin/env python3
"""
שני ניתוחים שהמפה לא עונה עליהם:

    python3 analyze.py filter     # למה כלל ה-70% תופס רק ~16%, ואיך מרחיבים
    python3 analyze.py rivals     # המודל העסקי של המתחרים, מתוך רישומי המכר
    python3 analyze.py inventory  # המלאי החי — מה כל אחד מחזיק *עכשיו*
"""
import json, os, sys, zipfile, statistics as st
from collections import defaultdict, Counter
from urllib.parse import urlencode
from urllib.request import urlopen, Request

import sdf

# מהמחקר: 4 המפעילים הגדולים בזיפי ה-BUY BOX
RIVALS = ["SIMPLE QUARTERS", "BROOKS HOLDINGS", "GRISE HOME", "POWER HOUSE HOLDINGS",
          "AMERICAN INTERNATIONAL HOME", "OWNEZ HOLDINGS"]

# קונה-ומחזיק: 0 מכירות חוזרות ב-4 שנות נתונים. החזקה ארוכה היא המודל
# שלו, לא עסקה תקועה — ולכן הוא מוצא מסטטיסטיקת ה"תקוע" כדי לא להטות אותה.
LANDLORDS = {"AMERICAN INTERNATIONAL HOME"}


# ─────────────────────────── ניתוח 1: המסנן ───────────────────────────

def filter_analysis(sales, flips):
    """
    כלל ה-70%:  הצעה ≤ 0.70·ARV − שיפוץ
    ולכן:       מרווח = מכירה − קנייה ≥ 0.30·ARV + שיפוץ

    זו כל התשובה. הכלל לא דורש "מרווח טוב" — הוא דורש מרווח שגדל
    עם מחיר המכירה. ככל שהנכס יקר יותר, הרף עולה בדולרים.
    """
    S = st.median([f["sell"] for f in flips])
    spread = st.median([f["sell"] - f["buy"] for f in flips])
    print(f"\n{'='*74}\nלמה המסנן צר\n{'='*74}")
    print(f"\nמכירה חציונית בפליפים שנמדדו:   ${S:,.0f}")
    print(f"מרווח גולמי חציוני:             ${spread:,.0f}  ({spread/S*100:.0f}% מהמכירה)")
    print(f"\nכלל ה-70% בתקציב שיפוץ $52,416 דורש:")
    print(f"   מרווח ≥ 0.30 × ${S:,.0f} + $52,416 = ${0.30*S + 52416:,.0f}")
    print(f"   👉 פי {(0.30*S + 52416)/spread:.2f} מהמרווח החציוני בשוק.")
    print(f"\nכלומר הכלל לא 'מחמיר' — הוא מכייל לשוק עם מרווחים רחבים יותר.")

    print(f"\n{'-'*74}\nרגישות: אחוז מ-{len(flips):,} הפליפים שהיו עוברים\n{'-'*74}")
    print(f"{'תקציב שיפוץ':>14}" + "".join(f"{r:>11.0%}" for r in (.70, .72, .75, .78)))
    for R in (20_000, 30_000, 40_000, 52_416, 65_000):
        row = f"{'$'+format(R,','):>14}"
        for rule in (.70, .72, .75, .78):
            n = sum(1 for f in flips if rule * f["sell"] - f["buy"] >= R)
            row += f"{n/len(flips)*100:>10.0f}%"
        print(row)
    print("\nשורות = תקציב השיפוץ · עמודות = הכלל. שניהם מזיזים, התקציב יותר.")

    # פילוח לפי חתימת ההחזקה — מפריד wholesale משיפוץ אמיתי
    print(f"\n{'-'*74}\nלא כל 'פליפ' הוא שיפוץ — פילוח לפי משך ההחזקה\n{'-'*74}")
    print(f"{'חתימה':>26}{'n':>7}{'מרווח':>11}{'מכפיל':>8}{'קונה תופס':>11}{'עובר':>8}")
    bands = [("≤2 ח' — wholesale", 0, 2), ("2-3.5 ח' — ביניים", 2, 3.5),
             ("3.5-9 ח' — שיפוץ", 3.5, 9), (">9 ח' — ממושך", 9, 99)]
    for lbl, lo, hi in bands:
        grp = [f for f in flips if lo < f["months"] <= hi]
        if len(grp) < 20:
            continue
        pas = sum(1 for g in grp if 0.70 * g["sell"] - g["buy"] >= 52_416) / len(grp) * 100
        oo = sum(1 for g in grp if g["owner_occ"]) / len(grp) * 100
        print(f"{lbl:>26}{len(grp):>7,}"
              f"{st.median([g['sell']-g['buy'] for g in grp]):>11,.0f}"
              f"{st.median([g['mult'] for g in grp]):>8.2f}{oo:>10.0f}%{pas:>7.0f}%")
    p_all = sum(1 for f in flips if 0.70 * f["sell"] - f["buy"] >= 52_416) / len(flips) * 100
    nw = [f for f in flips if f["months"] > 2]
    p_nw = sum(1 for f in nw if 0.70 * f["sell"] - f["buy"] >= 52_416) / len(nw) * 100
    print(f"\nהוצאת ה-wholesale מעלה את שיעור המעבר מ-{p_all:.0f}% ל-{p_nw:.0f}% בלבד.")
    print("זה מסביר חלק מהפער, לא את כולו. מדרגת המחיר מסבירה יותר — ראה למטה.")

    # ⚠️ C8_Market_Days אינו שמיש כאינדיקטור מחוץ-לשוק
    zeros = sum(1 for s in sales if s["dom"] == 0) / len(sales) * 100
    zf = sum(1 for f in flips if f["buy_dom"] == 0) / len(flips) * 100
    print(f"\n⚠️ C8_Market_Days: {zeros:.0f}% אפסים בכלל המכירות מול {zf:.0f}% בפליפים.")
    print("   שיעור זהה ⇒ אפס פירושו 'לא דווח', לא 'מחוץ לשוק'. השדה לא שמיש")
    print("   להבחנה בין ערוצי רכישה, ונתוני ה-DOM מגיעים מ-9% מדגם מדווח בלבד.")

    # מדרגות מחיר — איפה הכלל בכלל ניתן להשגה
    print(f"\n{'-'*74}\nלפי מדרגת מחיר מכירה\n{'-'*74}")
    bands = [(0, 150_000), (150_000, 200_000), (200_000, 250_000),
             (250_000, 300_000), (300_000, 400_000), (400_000, 10**9)]
    print(f"{'מדרגה':>20}{'n':>7}{'מרווח חציוני':>15}{'עובר 70%/$52K':>16}")
    for lo, hi in bands:
        grp = [f for f in flips if lo <= f["sell"] < hi]
        if len(grp) < 10:
            continue
        sp = st.median([g["sell"] - g["buy"] for g in grp])
        pas = sum(1 for g in grp if 0.70 * g["sell"] - g["buy"] >= 52_416) / len(grp) * 100
        lbl = f"${lo//1000}K–{hi//1000}K" if hi < 10**9 else f"${lo//1000}K+"
        print(f"{lbl:>20}{len(grp):>7,}{sp:>15,.0f}{pas:>15.0f}%")


# ─────────────────────────── ניתוח 2: המתחרים ───────────────────────────

def _buyer_index(zips):
    """SDF_ID -> (שם קונה, חברת טייטל) עבור כל העסקאות בזיפים המבוקשים."""
    buyers, titles = {}, {}
    for year, path in sdf.files():
        with zipfile.ZipFile(path) as zf:
            names = {n.upper(): n for n in zf.namelist()}
            keep = set()
            for p in sdf._rows(zf, names["SALEPARCEL.TXT"]):
                if (p.get("A5_ZipCode") or "").strip()[:5] in zips:
                    keep.add((p.get("SDF_ID") or "").strip())
            for c in sdf._rows(zf, names["SALECONTAC.TXT"]):
                sid = (c.get("SDF_ID") or "").strip()
                if sid not in keep:
                    continue
                t = (c.get("Contact_Type") or "").strip()
                if t == "B":
                    buyers[sid] = (c.get("Name") or "").strip()
                elif t == "P":
                    titles[sid] = (c.get("Company") or "").strip()
    return buyers, titles


def _sale_ids(zips):
    """(parcel, date) -> SDF_ID, כדי לקשר בין רשומת מכירה לשם הקונה."""
    out = {}
    for year, path in sdf.files():
        with zipfile.ZipFile(path) as zf:
            names = {n.upper(): n for n in zf.namelist()}
            parcels = {}
            for p in sdf._rows(zf, names["SALEPARCEL.TXT"]):
                if (p.get("A5_ZipCode") or "").strip()[:5] in zips:
                    parcels[(p.get("SDF_ID") or "").strip()] = \
                        (p.get("A1_Parcel_Number") or "").strip()
            for s in sdf._rows(zf, names["SALEDISC.TXT"]):
                sid = (s.get("SDF_ID") or "").strip()
                if sid in parcels and (s.get("County_ID") or "").strip() == "49":
                    out[(parcels[sid], (s.get("C7_Conveyance_Date") or "").strip()[:10])] = sid
    return out


def rivals_analysis(sales, flips):
    zips = set(sdf.BUY_BOX)
    print("  קורא שמות קונים וחברות טייטל...", file=sys.stderr)
    buyers, titles = _buyer_index(zips)
    ids = _sale_ids(zips)

    # לכל רכישה: מי קנה, איך קנה, ומה קרה אחר כך
    by_rival = defaultdict(lambda: {"buys": [], "flips": []})
    for s in sales:
        sid = ids.get((s["parcel"], s["date"]))
        name = (buyers.get(sid) or "").upper()
        for r in RIVALS:
            if r in name:
                by_rival[r]["buys"].append((s, sid))
    for f in flips:
        sid = ids.get((f["parcel"], f["buy_date"]))
        name = (buyers.get(sid) or "").upper()
        for r in RIVALS:
            if r in name:
                by_rival[r]["flips"].append((f, sid))

    print(f"\n{'='*74}\nהמודל העסקי של המתחרים — מתוך רישומי המכר\n{'='*74}")
    for r in RIVALS:
        d = by_rival.get(r)
        if not d or not d["buys"]:
            continue
        buys, fl = d["buys"], d["flips"]
        off = sum(1 for s, _ in buys if s["dom"] == 0)
        dist = sum(1 for s, _ in buys if s["distress"])
        tc = Counter(titles.get(sid, "—") for _, sid in buys if titles.get(sid))
        print(f"\n▌ {r}")
        print(f"  רכישות {len(buys)}  ·  מהן נמכרו תוך 18 ח': {len(fl)}"
              f"  ({len(fl)/len(buys)*100:.0f}%)")
        print(f"  מחיר רכישה חציוני   ${st.median([s['price'] for s, _ in buys]):>9,.0f}")
        print(f"  🔑 נרכש מחוץ לשוק   {off:>3}/{len(buys)} = {off/len(buys)*100:.0f}%"
              f"   (0 ימי שיווק ברישום)")
        print(f"     נרכש בעסקת מצוקה {dist:>3}/{len(buys)} = {dist/len(buys)*100:.0f}%")
        if tc:
            top = " · ".join(f"{n[:30]} ({c})" for n, c in tc.most_common(2))
            print(f"     חברת טייטל       {top}")
        if fl:
            oo = sum(1 for f, _ in fl if f["owner_occ"])
            sd = [f["sell"] - f["buy"] for f, _ in fl]
            mo = [f["months"] for f, _ in fl]
            sdom = [f["dom"] for f, _ in fl if f["dom"] > 0]
            print(f"  ── ביציאה ──")
            print(f"     מרווח גולמי חציוני  ${st.median(sd):>9,.0f}"
                  f"   ·  מכפיל {st.median([f['mult'] for f, _ in fl]):.2f}")
            print(f"     החזקה חציונית       {st.median(mo):>5.1f} ח'"
                  f"       ·  ימי שיווק במכירה {st.median(sdom) if sdom else 0:.0f}")
            print(f"  🔑 קונה הקצה         {oo}/{len(fl)} = {oo/len(fl)*100:.0f}% תופס בעצמו"
                  f"  ({len(fl)-oo} למשקיע)")
            quick = sum(1 for f, _ in fl if f["months"] <= 2)
            print(f"     נמכר תוך חודשיים    {quick}/{len(fl)} = {quick/len(fl)*100:.0f}%"
                  f"   ← סימן ל-wholesale ולא לשיפוץ")

    print(f"\n{'-'*74}")
    print("קריאה: החזקה קצרה + קונה קצה שהוא משקיע = wholesale (אין שיפוץ).")
    print("       החזקה 4-8 ח' + קונה תופס = פליפ שיפוץ אמיתי.")


# ─────────────────── ניתוח 3: המלאי החי של המתחרים ───────────────────

PARCEL_URL = ("https://xmaps.indy.gov/arcgis/rest/services/Common/"
              "CommonlyUsedLayers/MapServer/0/query")
INV_CACHE = os.path.join(sdf.DATA, "rival_inventory.json")
# 🔴 השרת הזה הוא MapServer ישן. אומת 6.9.2026 בארבע קומבינציות:
#   f=geojson + גאומטריה      -> "Failed to execute query"
#   כל שאילתה עם resultOffset  -> נכשלת (אין תמיכה ב-pagination)
#   f=json + גאומטריה + outSR  -> ✅ עובד
# ולכן: בלי pagination, ו-`attributes` במקום `properties`.
FIELDS = ("STATEPARCELNUMBER,FULLOWNERNAME,STNUMBER,FULL_STNAME,CITY,ZIPCODE,"
          "TOWNSHIP,PROPERTY_CLASS,PROPERTY_SUB_CLASS,"
          "PROPERTY_SUB_CLASS_DESCRIPTION,ASSESSORYEAR_IMPTOTAL,"
          "ASSESSORYEAR_TOTALAV,OWNERADDRESS,OWNERCITY,ACREAGE")


def _fetch_owner(name):
    """
    כל החלקות שהבעלים הנוכחי שלהן מכיל את `name`.

    ⚠️ בלי pagination — השרת לא תומך. יש תקרה של ~1,000 רשומות לשאילתה,
    ולכן חיתום נבדק במפורש ומדווח. **כישלון זורק, לא מחזיר רשימה ריקה** —
    זה בדיוק הבאג שהחזיר 0 רשומות בשקט.
    """
    q = urlencode({
        "where": f"UPPER(FULLOWNERNAME) LIKE '%{name}%'",
        "outFields": FIELDS,
        "returnGeometry": "true", "outSR": "4326", "f": "json",
    })
    req = Request(f"{PARCEL_URL}?{q}", headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    if "error" in data:
        raise RuntimeError(f"שאילתת החלקות נכשלה עבור {name!r}: "
                           f"{data['error'].get('message')}")
    if data.get("exceededTransferLimit"):
        print(f"    ⚠️ {name}: התוצאה נחתכה בתקרת השרת — הספירה חלקית!",
              file=sys.stderr)
    # נרמול לצורה אחת: rings של ArcGIS -> coordinates, attributes -> properties
    return [{"properties": f.get("attributes", {}),
             "geometry": {"coordinates": (f.get("geometry") or {}).get("rings")}}
            for f in data.get("features", [])]


def live_inventory(refresh=False):
    """
    המלאי החי — מה שכל מפעיל מחזיק *עכשיו* לפי רשומות השומה.
    זה לא היסטוריית מכר; זה הצינור הפעיל שלהם.
    נשמר במטמון כדי לא להעמיס על שרת העירייה.
    """
    if os.path.exists(INV_CACHE) and not refresh:
        with open(INV_CACHE, encoding="utf-8") as f:
            return json.load(f)
    out = {}
    for r in RIVALS:
        print(f"  שולף {r}...", file=sys.stderr)
        out[r] = _fetch_owner(r)
    os.makedirs(sdf.DATA, exist_ok=True)
    with open(INV_CACHE, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


def _centroid(geom):
    """מרכז מקורב של פוליגון חלקה — מספיק לסימון על מפה."""
    if not geom:
        return None
    rings = geom.get("coordinates") or []
    while rings and isinstance(rings[0][0], list):
        rings = rings[0]
    pts = [p for p in rings if isinstance(p, list) and len(p) >= 2]
    if not pts:
        return None
    return (round(sum(p[1] for p in pts) / len(pts), 6),
            round(sum(p[0] for p in pts) / len(pts), 6))


def inventory_analysis(sales, flips):
    inv = live_inventory()
    # תאריך הרכישה האחרון הידוע לכל חלקה, מרישומי המכר
    last_buy = {}
    for s in sales:
        k = s["parcel"]
        if k and (k not in last_buy or s["date"] > last_buy[k][0]):
            last_buy[k] = (s["date"], s["price"])
    today = max((s["date"] for s in sales), default="2026-09-06")

    print(f"\n{'='*78}\nהמלאי החי של המתחרים — מי מחזיק מה *עכשיו*\n{'='*78}")
    print("מקור: רשומות השומה של מריון קאונטי (xmaps.indy.gov), בעלים נוכחי.")
    print("זו לא היסטוריית מכר — זה הצינור הפעיל.\n")
    print(f"{'מפעיל':<30}{'חלקות':>7}{'ב-BUY BOX':>11}{'מגורים':>9}"
          f"{'קרקע ריקה':>11}{'שווי שומה':>13}")
    print("-" * 78)

    buybox_rows = []
    for r in RIVALS:
        feats = inv.get(r) or []
        if not feats:
            continue
        props = [f.get("properties", {}) for f in feats]
        bb = [p for p in props if (p.get("ZIPCODE") or "")[:5] in sdf.BUY_BOX]
        res = sum(1 for p in props if (p.get("PROPERTY_CLASS") or "") == "RESIDENTIAL")
        vac = sum(1 for p in props
                  if "VACANT" in (p.get("PROPERTY_SUB_CLASS_DESCRIPTION") or "").upper())
        av = sum(_i(p.get("ASSESSORYEAR_TOTALAV")) for p in props)
        print(f"{r:<30}{len(props):>7}{len(bb):>11}{res:>9}{vac:>11}{av:>13,.0f}")
        for f in feats:
            p = f.get("properties", {})
            if (p.get("ZIPCODE") or "")[:5] not in sdf.BUY_BOX:
                continue
            pc = _parcel(p.get("STATEPARCELNUMBER"))
            d, pr = last_buy.get(pc, (None, 0))
            buybox_rows.append((r, p, d, pr, sdf._months(d, today) if d else None))

    print("-" * 78)
    print(f"\n🎯 החלקות בזיפי ה-BUY BOX — התחרות הישירה ({len(buybox_rows)})\n")
    print(f"{'מפעיל':<22}{'ZIP':<7}{'שומה':>9}{'נקנה':>9}{'מוחזק':>8}  כתובת")
    for r, p, d, pr, mo in sorted(buybox_rows, key=lambda x: -(x[4] or 0)):
        addr = f"{p.get('STNUMBER') or ''} {p.get('FULL_STNAME') or ''}".strip()
        flag = ("  🔴 תקוע?" if mo and mo > 9 and r not in LANDLORDS else
                "  🏠 משכיר" if r in LANDLORDS else "")
        print(f"{r[:20]:<22}{(p.get('ZIPCODE') or '')[:5]:<7}"
              f"{_i(p.get('ASSESSORYEAR_TOTALAV')):>9,}"
              f"{(d or '—')[:7]:>9}{(f'{mo:.1f} ח' if mo else '—'):>8}  {addr[:30]}{flag}")

    # 🔴 מצרף את כולם יחד היה נותן 32.4 ח' — מספר שנשלט כולו ע"י המשכיר.
    # לכל מפעיל מודל אחר, ולכן החציון מדווח לחוד.
    print(f"\n{'-'*78}\nכמה זמן כל אחד מחזיק בפועל (כל המחוז, לא רק BUY BOX)\n{'-'*78}")
    print(f"{'מפעיל':<30}{'n':>5}{'חציון':>9}{'מעל 12 ח\'':>12}  קריאה")
    for r in RIVALS:
        mo = [sdf._months(last_buy[pc][0], today)
              for f in (inv.get(r) or [])
              for pc in [_parcel(f.get("properties", {}).get("STATEPARCELNUMBER"))]
              if pc in last_buy]
        if not mo:
            continue
        over = sum(1 for m in mo if m > 12)
        read = ("🏠 קונה-ומחזיק — זה המודל" if r in LANDLORDS else
                "✅ מחזור מהיר, בלי מלאי תקוע" if st.median(mo) <= 4 else
                "🔴 יושב על מלאי ישן" if st.median(mo) > 12 else
                "🟡 איטי מהחתימה המוצהרת שלו")
        print(f"{r:<30}{len(mo):>5}{st.median(mo):>8.1f}ח{over:>7}/{len(mo):<4}  {read}")

    print("\n🔑 הקריאה: המלאי החי הוא **הטיית שורדים הפוכה**. המכירות המהירות")
    print("   הן מה שהם הצליחו למכור; מה שנשאר בבעלות הוא מה שלא נמכר.")
    print("\n⚠️ חלקה בלי תאריך רכישה = נקנתה לפני 2023 או מחוץ למדגם המכר שלנו.")


def _parcel(v):
    """
    xmaps מחזיק `49-06-26-126-076.000-101`; ה-SDF מחזיק `490626126076000101`.
    אותה חלקה, שני פורמטים. בלי הנרמול הזה שום חלקה לא מתחברת למכירה.
    """
    return (v or "").replace("-", "").replace(".", "").strip()


def _i(v):
    try:
        return int(float(v or 0))
    except (ValueError, TypeError):
        return 0


def selftest():
    # האלגברה של כלל ה-70%: מרווח נדרש = (1-rule)·S + R
    S, R, rule = 250_000, 52_416, 0.70
    need = (1 - rule) * S + R
    assert abs(need - 127_416) < 1
    # נכס עם מרווח בדיוק בסף עובר, ומתחתיו לא
    f = {"sell": S, "buy": S - need}
    assert rule * f["sell"] - f["buy"] >= R
    f2 = {"sell": S, "buy": S - need + 1}
    assert not (rule * f2["sell"] - f2["buy"] >= R)
    # צנטרואיד: ריבוע היחידה -> (0.5, 0.5), וטבעת מקוננת נפרשת נכון
    sq = [[0, 0], [1, 0], [1, 1], [0, 1]]
    assert _centroid({"coordinates": [sq]}) == (0.5, 0.5)
    assert _centroid({"coordinates": [[sq]]}) == (0.5, 0.5)
    assert _centroid(None) is None and _centroid({"coordinates": []}) is None
    assert _i("70600") == 70600 and _i(None) == 0 and _i("") == 0
    # נרמול מספר חלקה — xmaps מול SDF
    assert _parcel("49-06-26-126-076.000-101") == "490626126076000101"
    assert len(_parcel("49-06-26-126-076.000-101")) == 18
    assert _parcel(None) == "" and _parcel(" 49-01 ") == "4901"
    print("selftest: ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "filter"
    if cmd == "selftest":
        selftest()
    else:
        sales = sdf.load()
        flips = sdf.find_flips(sales)
        {"filter": filter_analysis, "rivals": rivals_analysis,
         "inventory": inventory_analysis}[cmd](sales, flips)
