#!/usr/bin/env python3
"""
מנוע ARV — נתוני מכר ממשלתיים של אינדיאנה (Sales Disclosure Form).

מקור: https://www.stats.indiana.edu/topic/sdf.asp — חינם, מתעדכן שבועית.
IC 6-1.1-5.5 מחייבת טופס גילוי עם המחיר בפועל בכל העברה, ולכן אינדיאנה
היא מדינת גילוי מלא: המחירים כאן הם מה שנרשם, לא הערכה של אתר.

מה זה נותן שהמודל היום מנחש:
  1. מכפיל ה-ARV — נמדד מפליפים אמיתיים (אותו נכס נמכר פעמיים) במקום 1.18
  2. DOM אמיתי — C8_Market_Days כפי שדווח לרשות, לא ה-DOM של Redfin
  3. אחוז קוני היציאה התופסים בעצמם — J1_Primary_Residence

הרצה:
    python3 sdf.py report            # תמונת מצב לכל זיפ ב-BUY BOX
    python3 sdf.py flips             # פליפים אמיתיים שנמצאו, לפי זיפ
    python3 sdf.py refresh           # מוריד מחדש רק את השנה הנוכחית (~14MB)
    python3 sdf.py selftest          # בדיקה עצמית
"""
import csv, io, json, os, re, sys, zipfile, statistics as st
from collections import defaultdict
from datetime import date
from urllib.request import urlopen, Request
from urllib.error import HTTPError

BUY_BOX = ["46236", "46217", "46228", "46224", "46229", "46237", "46219", "46107"]
# שכבה צפונית — נוספה 6.9.2026 אחרי בדיקה עמוקה (docs/בדיקה_זיפים_צפוניים.md):
# מדד מכירות חוזרות של אותו בית +3.5%-5.3% בשנה, זרימה של 17-54 פליפים עוברים ב-24 ח' לזיפ.
# הסימון WATCH הקודם נשען על $/sqft של Redfin ברמת זיפ — תמהיל, לא שוק.
NORTH = ["46220", "46205", "46260", "46240"]
TARGET = BUY_BOX + NORTH                 # ברירת המחדל של כל הכלים
# כלל ההצעה לפי שכבה: הצעה ≤ RULE×ARV − שיפוץ. 70% מכויל לקונה ממונף (מימון ~5.4% מה-ARV);
# במזומן חוזרות 2 נקודות ב-BUY BOX. בצפון שגיאת ה-ARV היא 18.7% (מול 12.4%) ⇒ 69% נותן
# את אותה כרית של ~$15K בתרחיש התחתון. decisions.md 6.9.2026
RULES = {"BUY": 0.72, "NORTH": 0.69}
MARION_COUNTY_ID = "49"


def rule_for(zip5):
    """מחוץ ל-BUY BOX — הכלל השמרני של הצפון."""
    return RULES["BUY"] if zip5 in BUY_BOX else RULES["NORTH"]
YEARS = list(range(2023, date.today().year + 1))   # מהלוח: בינואר נוספת שנה לבד
FLIP_MAX_MONTHS = 18        # שתי מכירות רחוקות מזה — כבר לא פליפ
FLIP_MIN_GAIN = 0.10        # עלייה מתחת לזה היא שוק, לא שיפוץ

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
URL = "https://www.stats.indiana.edu/sdfdata/SDF_{year}.zip"


def _fetch(year):
    """מוריד ומאחסן מקומית. הקובץ מתעדכן שבועית — מחיקה מ-data/ תמשוך מחדש."""
    path = os.path.join(DATA, f"SDF_{year}.zip")
    if not os.path.exists(path):
        os.makedirs(DATA, exist_ok=True)
        print(f"  מוריד {year}...", file=sys.stderr)
        req = Request(URL.format(year=year), headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urlopen(req, timeout=300) as r, open(path + ".part", "wb") as f:
                f.write(r.read())
        except HTTPError as e:
            if e.code == 404 and year == YEARS[-1]:    # ינואר: המדינה עוד לא פרסמה את השנה החדשה
                print(f"  ⚠️ אין עדיין SDF_{year} — ממשיך בלעדיו", file=sys.stderr)
                return None
            raise
        os.replace(path + ".part", path)           # הורדה שנקטעה לא משאירה zip שבור בשם המלא
    return path


def files(years=YEARS):
    """(שנה, נתיב) לכל שנה זמינה. מדלג על שנה שהמדינה עוד לא פרסמה."""
    for year in years:
        path = _fetch(year)
        if path:
            yield year, path


def _norm_name(n):
    """'Brooks Holdings, LLC' / 'BROOKS HOLDINGS LLC' -> 'BROOKS HOLDINGS'. אותו קונה, כתיב אחד."""
    n = (n or "").upper().replace("L.L.C.", "LLC").replace("LIMITED LIABILITY COMPANY", "LLC")
    n = re.sub(r"\b(AN?|THE)\s+(INDIANA|DELAWARE|OHIO|ILLINOIS|KENTUCKY|MICHIGAN|TEXAS|FLORIDA|NEVADA|WYOMING)\s+LLC\b.*$", "", n)
    words = re.sub(r"[^\w\s&]", " ", n).split()
    while words and words[-1] in ("LLC", "INC", "CORP", "LP", "LTD", "CO", "LLP"):
        words.pop()
    return " ".join(words)


def _contacts(zf, year):
    """
    SDF_ID -> [קונה ראשון, חברת טייטל] למריון. SALECONTAC הוא UTF-16 וגדול,
    ולכן התוצאה נשמרת ב-data/contacts_{year}.json ומתחדשת רק כשה-zip חדש יותר.
    """
    cache = os.path.join(DATA, f"contacts_{year}.json")
    src = os.path.join(DATA, f"SDF_{year}.zip")
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(src):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    names = {n.upper(): n for n in zf.namelist()}
    out = {}
    for c in _rows(zf, names["SALECONTAC.TXT"]):
        sid = (c.get("SDF_ID") or "").strip()
        if not sid.startswith(f"C{MARION_COUNTY_ID}-"):
            continue
        t = (c.get("Contact_Type") or "").strip()
        rec = out.setdefault(sid, ["", ""])
        if t == "B" and not rec[0]:
            rec[0] = _norm_name(c.get("Name"))
        elif t == "P" and not rec[1]:
            rec[1] = (c.get("Company") or "").strip()
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return out


def _rows(zf, name):
    """SALEDISC הוא UTF-16, SALEPARCEL הוא UTF-8. שניהם מופרדי טאב."""
    raw = zf.read(name)
    enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    return csv.DictReader(io.StringIO(raw.decode(enc, "replace")), delimiter="\t")


def _yes(rec, key):
    return (rec.get(key) or "").strip().upper() == "Y"


def _money(rec, key):
    """E1_Sales_Price מאוחסן באגורות. $185,000 נשמר כ-18500000."""
    try:
        return float((rec.get(key) or "0").replace(",", "").replace("$", "")) / 100
    except ValueError:
        return 0.0


def load(years=YEARS, zips=TARGET):
    """מחזיר מכירות מגורים במריון קאונטי בזיפים המבוקשים, שנה אחר שנה."""
    out = []
    for year, path in files(years):
        with zipfile.ZipFile(path) as zf:
            names = {n.upper(): n for n in zf.namelist()}
            contacts = _contacts(zf, year)
            parcels = defaultdict(list)
            for p in _rows(zf, names["SALEPARCEL.TXT"]):
                parcels[(p.get("SDF_ID") or "").strip()].append(p)
            for s in _rows(zf, names["SALEDISC.TXT"]):
                if (s.get("County_ID") or "").strip() != MARION_COUNTY_ID:
                    continue
                if not _yes(s, "C10_Residential_Property"):
                    continue
                price = _money(s, "E1_Sales_Price")
                if price < 10_000:           # $0, מתנות, העברות בין קרובים
                    continue
                sid = (s.get("SDF_ID") or "").strip()
                buyer, title = contacts.get(sid, ["", ""])
                for p in parcels.get(sid, []):
                    z = (p.get("A5_ZipCode") or "").strip()[:5]
                    if z not in zips:
                        continue
                    out.append({
                        "zip": z, "sid": sid, "buyer": buyer, "title": title,
                        "parcel": (p.get("A1_Parcel_Number") or "").strip(),
                        "date": (s.get("C7_Conveyance_Date") or "").strip()[:10],
                        "price": price,
                        "dom": _int(s.get("C8_Market_Days")),
                        "owner_occ": _yes(s, "J1_Primary_Residence"),
                        "appr": _yes(s, "E9_Appraisal_Value"),   # בוצעה שמאות = קונה ממומן
                        "distress": _yes(s, "C1_Sheriff_Sale") or _yes(s, "C2_Short_Sale")
                                    or _yes(s, "C4_Auction"),
                        "av": _int(p.get("P2_5_Total_AV")),
                        "class": (p.get("P2_6_Prop_Class_Code") or "").strip(),
                        "improved": _yes(p, "A4_Improvement"),   # N = מגרש בלי מבנה
                        "vacant": _yes(s, "B3_Vacant_Land"),
                        "newc": _yes(s, "D1_Physical_Change"),   # Y = כמעט תמיד "new construction"
                        "address": (p.get("A5_Street1") or "").strip(),
                        "city": (p.get("A5_City") or "Indianapolis").strip() or "Indianapolis",
                    })
    return out


def _int(v):
    try:
        return int(float(v or 0))
    except ValueError:
        return 0


def _months(a, b):
    """חודשים בין שני תאריכי ISO. מספיק מדויק לחלון של 18 חודשים."""
    ya, ma, da = (int(x) for x in a.split("-"))
    yb, mb, db = (int(x) for x in b.split("-"))
    return (yb - ya) * 12 + (mb - ma) + (db - da) / 30.4


def _new_build(buy, sell):
    """
    קבלן שקנה מגרש ומכר עליו בית עונה להגדרת "פליפ" — ואינו כזה.
    נמדד 6.9.2026: 10% מ"פליפי" ה-BUY BOX, 28% ב-WATCH (46239/46235/46259 הם
    זיפים של קבלנים). בלי הסינון שיעור המעבר והמכפיל מנופחים.
    """
    return buy["class"] == "500" or not buy["improved"] or buy["vacant"] or sell["newc"]


def find_flips(sales):
    """
    אותו parcel שנמכר פעמיים בתוך FLIP_MAX_MONTHS עם עלייה משמעותית = פליפ.
    זה המדד הישיר לפרמיית השיפוץ — מה שמכפיל ה-1.18 מנסה לנחש.
    """
    by_parcel = defaultdict(list)
    for s in sales:
        if s["parcel"]:
            by_parcel[s["parcel"]].append(s)
    flips = []
    for rows in by_parcel.values():
        rows.sort(key=lambda r: r["date"])
        for buy, sell in zip(rows, rows[1:]):
            gap = _months(buy["date"], sell["date"])
            if not (0 < gap <= FLIP_MAX_MONTHS):
                continue
            if sell["price"] < buy["price"] * (1 + FLIP_MIN_GAIN):
                continue
            if _new_build(buy, sell):
                continue
            flips.append({**sell, "buy": buy["price"], "sell": sell["price"],
                          "months": gap, "mult": sell["price"] / buy["price"],
                          "buy_date": buy["date"], "buy_distress": buy["distress"],
                          "buy_dom": buy["dom"],
                          "flipper": buy.get("buyer", ""), "title_in": buy.get("title", "")})
    return flips


def _fmt(label, rows, key, money=False):
    if not rows:
        return f"{label}: —"
    v = st.median([r[key] for r in rows])
    return f"{label}: {'$' if money else ''}{v:,.0f}"


def report(sales):
    flips = find_flips(sales)
    fz = defaultdict(list)
    for f in flips:
        fz[f["zip"]].append(f)

    print(f"\nנתוני מכר ממשלתיים — Marion County, {YEARS[0]}–{YEARS[-1]}, מגורים בלבד")
    print(f"נשלף: {date.today().isoformat()} · מקור: Indiana SDF (IC 6-1.1-5.5)\n")
    hdr = f"{'ZIP':<8}{'מכירות':>8}{'מדיאן':>11}{'DOM':>6}{'תופס':>7}{'מצוקה':>7}{'פליפים':>8}{'מכפיל':>8}{'רווח גולמי':>13}"
    print(hdr); print("-" * len(hdr.encode('utf-8').decode('utf-8')) )
    for z in TARGET:
        if z == NORTH[0]:
            print("── שכבה צפונית ──")
        rows = [s for s in sales if s["zip"] == z]
        if not rows:
            continue
        dom = [r["dom"] for r in rows if 0 < r["dom"] < 800]
        f = fz.get(z, [])
        mult = f"{st.median([x['mult'] for x in f]):.2f}" if f else "—"
        gain = f"${st.median([x['sell'] - x['buy'] for x in f]):,.0f}" if f else "—"
        print(f"{z:<8}{len(rows):>8}{st.median([r['price'] for r in rows]):>11,.0f}"
              f"{(st.median(dom) if dom else 0):>6.0f}"
              f"{sum(r['owner_occ'] for r in rows)/len(rows)*100:>6.0f}%"
              f"{sum(r['distress'] for r in rows)/len(rows)*100:>6.0f}%"
              f"{len(f):>8}{mult:>8}{gain:>13}")

    print(f"\nסה\"כ {len(sales):,} מכירות · {len(flips)} פליפים מזוהים "
          f"(מכירה חוזרת תוך {FLIP_MAX_MONTHS} ח' עם עלייה מעל {FLIP_MIN_GAIN:.0%})")
    if flips:
        print(f"מכפיל חציוני בפועל: {st.median([f['mult'] for f in flips]):.3f} "
              f"· המודל מניח 1.18")
        print(f"חציון חודשים להחזקה: {st.median([f['months'] for f in flips]):.1f} "
              f"· המודל מניח {5 + 65/30.4:.1f}")


def flips_detail(sales):
    flips = sorted(find_flips(sales), key=lambda f: -(f["sell"] - f["buy"]))
    print(f"\n{len(flips)} פליפים. 25 הגדולים:\n")
    print(f"{'ZIP':<7}{'קנייה':>10}{'מכירה':>10}{'רווח':>10}{'מכפיל':>7}{'חודשים':>8}  כתובת")
    for f in flips[:25]:
        print(f"{f['zip']:<7}{f['buy']:>10,.0f}{f['sell']:>10,.0f}"
              f"{f['sell']-f['buy']:>10,.0f}{f['mult']:>7.2f}{f['months']:>8.1f}  {f['address'][:38]}")


GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
GEO_CACHE = os.path.join(DATA, "geocode.csv")
GEO_BATCH = 8000          # התקרה הרשמית היא 10,000; משאירים מרווח


def _geo_key(s):
    return f"{s['address']}|{s['city']}|{s['zip']}".upper()


def geocode(sales):
    """
    ממפה כתובת -> (lat, lon) דרך הגיאוקודר של לשכת המפקד. חינם, בלי מפתח.
    התוצאות נשמרות ב-data/geocode.csv, כולל כישלונות — כדי לא לבקש אותם שוב.
    """
    cache = {}
    if os.path.exists(GEO_CACHE):
        with open(GEO_CACHE, encoding="utf-8", newline="") as f:
            for row in csv.reader(f):
                if len(row) == 3:
                    cache[row[0]] = (float(row[1]), float(row[2])) if row[1] else None

    todo = {}
    for s in sales:
        k = _geo_key(s)
        if k not in cache and s["address"]:
            todo[k] = s
    if not todo:
        return {k: v for k, v in cache.items() if v}

    print(f"  מגאוקד {len(todo):,} כתובות חדשות...", file=sys.stderr)
    items = list(todo.items())
    with open(GEO_CACHE, "a", encoding="utf-8", newline="") as out:
        w = csv.writer(out)
        for i in range(0, len(items), GEO_BATCH):
            chunk = items[i:i + GEO_BATCH]
            buf = io.StringIO()
            cw = csv.writer(buf)
            for n, (k, s) in enumerate(chunk):
                cw.writerow([n, s["address"], s["city"], "IN", s["zip"]])
            got = _post_batch(buf.getvalue())
            for n, (k, s) in enumerate(chunk):
                ll = got.get(str(n))
                cache[k] = ll
                w.writerow([k, ll[0] if ll else "", ll[1] if ll else ""])
            out.flush()
            print(f"    {i + len(chunk):,}/{len(items):,} "
                  f"({sum(1 for v in cache.values() if v):,} התאמות)", file=sys.stderr)
    return {k: v for k, v in cache.items() if v}


def _post_batch(payload):
    """multipart ידני — כדי להישאר בספריית התקן בלי requests."""
    import ssl, uuid
    from urllib.error import URLError
    b = f"----{uuid.uuid4().hex}"
    body = (
        f"--{b}\r\nContent-Disposition: form-data; name=\"addressFile\"; "
        f"filename=\"a.csv\"\r\nContent-Type: text/csv\r\n\r\n{payload}\r\n"
        f"--{b}\r\nContent-Disposition: form-data; name=\"benchmark\"\r\n\r\n"
        f"Public_AR_Current\r\n--{b}--\r\n"
    ).encode()
    req = Request(GEOCODER, data=body,
                  headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    for attempt in range(3):
        try:
            with urlopen(req, timeout=900, context=ssl.create_default_context()) as r:
                out = {}
                for row in csv.reader(io.StringIO(r.read().decode("utf-8", "replace"))):
                    # id, input, Match?, exactness, matched, "lon,lat", tigerid, side
                    if len(row) >= 6 and row[2] == "Match" and "," in row[5]:
                        lon, lat = row[5].split(",")[:2]
                        out[row[0]] = (float(lat), float(lon))
                return out
        except (URLError, TimeoutError, OSError) as e:
            if attempt == 2:
                print(f"    ⚠️ הגיאוקודר נכשל: {e}", file=sys.stderr)
                return {}
            print(f"    ניסיון {attempt + 2}/3...", file=sys.stderr)
    return {}


def stamp(sales):
    """
    עד מתי הנתונים מגיעים, וכמה ישן הקובץ. המדינה מעדכנת כל יום שישי ב-10:00,
    אבל מריון מגישה בפיגור של ~חודשיים — לכן "היום" של הקומפס הוא תאריך המכירה
    האחרון בקובץ, לא היום בלוח. חייב להיות גלוי בכל הרצה.
    """
    end = max((s["date"] for s in sales), default="—")
    path = os.path.join(DATA, f"SDF_{YEARS[-1]}.zip")
    age = (date.today() - date.fromtimestamp(os.path.getmtime(path))).days if os.path.exists(path) else None
    msg = f"נתונים עד {end}"
    if age is not None:
        msg += f" · SDF_{YEARS[-1]}.zip הורד לפני {age} ימים"
        if age >= 14:
            msg += "  ⚠️ ישן — python3 sdf.py refresh"
    print(msg, file=sys.stderr)
    return end


def refresh():
    """מוריד מחדש רק את השנה הנוכחית. שאר השנים סגורות ולא משתנות."""
    path = os.path.join(DATA, f"SDF_{YEARS[-1]}.zip")
    if os.path.exists(path):
        os.remove(path)
    if _fetch(YEARS[-1]):
        stamp(load())


def selftest():
    assert _money({"p": "18500000"}, "p") == 185_000.0
    assert _money({"p": ""}, "p") == 0.0
    assert _yes({"k": "y"}, "k") and not _yes({"k": "N"}, "k") and not _yes({}, "k")
    assert abs(_months("2026-01-01", "2026-07-01") - 6) < 0.1
    assert abs(_months("2025-06-15", "2026-06-15") - 12) < 0.1
    # פליפ אמיתי נתפס; מכירה חוזרת אחרי שנתיים או בלי עלייה — לא
    base = {"zip": "46219", "dom": 0, "owner_occ": True, "distress": False, "av": 0,
            "class": "510", "improved": True, "vacant": False, "newc": False, "appr": False,
            "sid": "", "buyer": "BROOKS HOLDINGS", "title": "", "address": "", "city": "Indianapolis"}
    flips = find_flips([
        {**base, "parcel": "A", "date": "2025-01-10", "price": 100_000},
        {**base, "parcel": "A", "date": "2025-09-10", "price": 180_000},  # פליפ
        {**base, "parcel": "B", "date": "2023-01-10", "price": 100_000},
        {**base, "parcel": "B", "date": "2025-06-10", "price": 200_000},  # רחוק מדי
        {**base, "parcel": "C", "date": "2025-01-10", "price": 100_000},
        {**base, "parcel": "C", "date": "2025-06-10", "price": 104_000},  # עלייה קטנה
    ])
    assert len(flips) == 1, flips
    assert flips[0]["parcel"] == "A" and abs(flips[0]["mult"] - 1.8) < 1e-9
    assert flips[0]["flipper"] == "BROOKS HOLDINGS"          # הקונה ברגל הקנייה = הפליפר
    assert _norm_name("Brooks Holdings, LLC") == _norm_name("BROOKS HOLDINGS LLC") == "BROOKS HOLDINGS"
    assert _norm_name("Simple Quarters L.L.C.") == "SIMPLE QUARTERS" and _norm_name("R&J Investments") == "R&J INVESTMENTS"
    assert _norm_name(None) == "" and _norm_name("EQUITY TRUST CO") == "EQUITY TRUST"
    assert _norm_name("HPMC Real Estate LLC, an Indiana limited liability company") == "HPMC REAL ESTATE"
    assert _norm_name("A Plus LLC") == "A PLUS"
    assert len(TARGET) == 12 and set(NORTH) & set(BUY_BOX) == set()
    assert rule_for("46236") == 0.72 and rule_for("46220") == 0.69 and rule_for("99999") == RULES["NORTH"]
    # בנייה חדשה אינה פליפ: מגרש -> בית, או בית שסומן D1 (physical change) במכירה
    assert not find_flips([
        {**base, "parcel": "D", "date": "2025-01-10", "price": 60_000, "class": "500", "improved": False},
        {**base, "parcel": "D", "date": "2025-09-10", "price": 360_000},
    ])
    assert not find_flips([
        {**base, "parcel": "E", "date": "2025-01-10", "price": 100_000},
        {**base, "parcel": "E", "date": "2025-09-10", "price": 360_000, "newc": True},
    ])
    assert YEARS[0] == 2023 and YEARS[-1] == date.today().year
    assert _geo_key({"address": "1 Main St", "city": "Indy", "zip": "46219"}) \
        == "1 MAIN ST|INDY|46219"
    print("selftest: ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "selftest":
        selftest()
    elif cmd == "refresh":
        refresh()
    else:
        sales = load()
        stamp(sales)
        if cmd == "geo":
            print(f"במטמון: {len(geocode(sales)):,} כתובות עם קואורדינטות")
        else:
            {"report": report, "flips": flips_detail}[cmd](sales)
