#!/usr/bin/env python3
"""
חיתום בכמות — כל המודעות הפעילות ב-BUY BOX דרך מנוע ה-ARV, בהרצה אחת.

`sdf.py` טוען את השוק · `arv.py` חותם נכס אחד · הקובץ הזה חותם רשימה שלמה.
זו מחצית הסינון שהייתה חסרה: המערכת עבדה רק על נתוני עבר.

מקור א' — Redfin, בלי מפתח, ידני (~2 דקות לשבוע):
    redfin.com → חיפוש זיפ → בתחתית התוצאות "Download All" → CSV.
    עד 350 מודעות לחיפוש, מינימום 20 תוצאות, דורש התחברות (חשבון חינמי).
    זו פונקציה שהאתר מציע למשתמש — לא גרידה. הקובץ כולל sqft, שנת בנייה,
    DOM וקואורדינטות — כל מה שהחיתום צריך.
מקור ב' — RentCast, אוטומטי, רישוי נקי, 50 בקשות בחודש חינם (8 זיפים × 4 = 32):
    מפתח ב-RENTCAST_API_KEY (סביבה או .env). כולל היסטוריית מחירים ⇒ הורדות מחיר.
    ⚠️ נכתב לפי התיעוד הרשמי ולא הורץ — אין מפתח. לבדוק בהרצה הראשונה.

    python3 listings.py                    # אוסף redfin_*.csv מ-Downloads (30 יום) אל data/redfin/ ומריץ הכל
    python3 listings.py --dom 30           # רק מודעות שיושבות מעל 30 יום
    python3 listings.py --comps "8058 CHERRYBARK"     # הקומפס של מודעה אחת, לעין אנושית
    python3 listings.py some.csv other.csv # קבצים מפורשים
    python3 listings.py --rentcast         # 12 זיפי היעד מ-RentCast
    python3 listings.py selftest

רק 12 זיפי היעד (`sdf.TARGET`) נחתמים. מודעה מחוץ להם נספרת ומדולגת — קומפס לזיפ
חדש דורשים גיאוקוד של כל המכירות בו (דקות). להוסיף זיפ = להוסיף אותו ל-`sdf.NORTH`.

הפלט ממוין לפי הפער בין המחיר המבוקש להצעת כלל ההצעה (sdf.RULE): פער קטן = עסקה קרובה.
שווי השומה לכל מודעה נשלף מרשומות השומה (xmaps) לפי מספר בית + רחוב, ונשמר
במטמון. 🔴 חיפוש לפי נקודה נפסל: קואורדינטות מגאוקוד נוחתות על החלקה השכנה
(6259 Chadworth החזיר את 6251 — נמדד 6.9.2026).
"""
import csv, glob, json, os, re, shutil, sys, time, importlib, tempfile
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError

import sdf, arv, analyze

_m = importlib.import_module("map")
KNOWN = sdf.TARGET + _m.WATCH + _m.AVOID
AV_CACHE = os.path.join(sdf.DATA, "av_cache.json")
RENTCAST_URL = "https://api.rentcast.io/v1/listings/sale"
# מילים שמדלגים עליהן בתחילת שם הרחוב: כיוונים, ו-"St. Paul"/"Mt. Vernon" (ST כאן = Saint)
SKIP = {"N", "S", "E", "W", "NE", "NW", "SE", "SW", "NORTH", "SOUTH", "EAST", "WEST",
        "ST", "SAINT", "MT", "MOUNT", "FT", "FORT"}


# ─────────────────────────── מקור א': Redfin CSV ───────────────────────────

def _num(v):
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _col(header, *starts):
    """אינדקס העמודה ששמה מתחיל באחד מ-`starts`. Redfin משנה כותרות — לכן תחילית."""
    for i, h in enumerate(header):
        if any(h.strip().upper().startswith(s) for s in starts):
            return i
    return None


def read_redfin(paths):
    """
    CSV של Redfin -> רשימת מודעות. שומר רק בתים צמודי קרקע במודעה פעילה;
    קונדו, בנייה, PAST SALE ושורות בלי קואורדינטות נספרות ומדולגות.
    """
    out, skipped = [], Counter()
    for path in paths:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        if not rows:
            continue
        h = rows[0]
        idx = {k: _col(h, *v) for k, v in {
            "address": ("ADDRESS",), "zip": ("ZIP",), "price": ("PRICE",), "sqft": ("SQUARE FEET",),
            "year": ("YEAR BUILT",), "dom": ("DAYS ON MARKET",), "lat": ("LATITUDE",),
            "lon": ("LONGITUDE",), "url": ("URL",), "type": ("PROPERTY TYPE",),
            "status": ("STATUS",), "sale": ("SALE TYPE",), "mls": ("MLS",)}.items()}
        missing = [k for k in ("address", "zip", "price", "lat", "lon") if idx[k] is None]
        if missing:
            sys.exit(f"⚠️ {path}: חסרות עמודות {missing}.\n   הכותרות שנמצאו: {h}")
        for r in rows[1:]:
            if len(r) <= 2:            # שורת הערה של Redfin ("some MLS listings are not included")
                continue
            def g(k):
                i = idx[k]
                return r[i].strip() if i is not None and i < len(r) else ""
            lat, lon = _num(g("lat")), _num(g("lon"))
            if lat is None or lon is None:
                skipped["בלי קואורדינטות"] += 1
                continue
            if g("type") and "SINGLE FAMILY" not in g("type").upper():
                skipped[g("type")] += 1
                continue
            if g("sale") and "MLS" not in g("sale").upper():   # PAST SALE — ייצוא של מכירות
                skipped[g("sale")] += 1
                continue
            out.append({"address": g("address"), "zip": g("zip")[:5], "price": _num(g("price")),
                        "sqft": _num(g("sqft")), "year": _num(g("year")), "dom": _num(g("dom")),
                        "lat": lat, "lon": lon, "url": g("url"), "status": g("status"),
                        "mls": g("mls"), "cuts": None})
    seen, uniq = set(), []                      # חיפושים חופפים / שבועות עוקבים -> אותה מודעה פעמיים. הראשון = החדש
    for L in out:
        if (L["address"].upper(), L["zip"]) not in seen:
            seen.add((L["address"].upper(), L["zip"]))
            uniq.append(L)
    if len(out) - len(uniq):
        skipped["כפולים"] += len(out) - len(uniq)
    return uniq, skipped


def collect_downloads(days=30):
    """
    redfin_*.csv שירדו ל-Downloads ב-`days` הימים האחרונים מועתקים ל-data/redfin/,
    ששם נשמרת ההיסטוריה השבועית (בעתיד: הורדות מחיר בין שבועות). מחזיר את כל
    הקבצים, החדש קודם — כך שכפילות בין שבועות משאירה את המחיר וה-DOM העדכניים.
    """
    dst = os.path.join(sdf.DATA, "redfin")
    os.makedirs(dst, exist_ok=True)
    copied = 0
    for f in glob.glob(os.path.expanduser("~/Downloads/redfin_*.csv")):
        if time.time() - os.path.getmtime(f) < days * 86400 and not os.path.exists(os.path.join(dst, os.path.basename(f))):
            shutil.copy2(f, dst)
            copied += 1
    return copied, sorted(glob.glob(os.path.join(dst, "redfin_*.csv")), reverse=True)


# ─────────────────────────── מקור ב': RentCast ───────────────────────────

def _api_key():
    key = os.environ.get("RENTCAST_API_KEY")
    env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not key and os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.startswith("RENTCAST_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        sys.exit("⚠️ חסר RENTCAST_API_KEY. חינם ב-rentcast.io/api (50 בקשות בחודש).\n"
                 "   export RENTCAST_API_KEY=...  או שורה ב-.env (ראה .env.example)")
    return key


def read_rentcast(zips=sdf.TARGET):
    """⚠️ לפי developers.rentcast.io/reference/sale-listings. לא הורץ — אין מפתח."""
    key, out = _api_key(), []
    for z in zips:
        q = urlencode({"zipCode": z, "status": "Active", "propertyType": "Single Family", "limit": 500})
        req = Request(f"{RENTCAST_URL}?{q}", headers={"X-Api-Key": key, "Accept": "application/json"})
        try:
            with urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
        except HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code == 403 and "subscription-inactive" in body:
                sys.exit("⚠️ המפתח קיים אבל התוכנית לא הופעלה. ב-app.rentcast.io/app/api לבחור את תוכנית "
                         "Developer (חינם, 50 בקשות בחודש) ולהפעיל אותה — ואז להריץ שוב. הבקשה לא נספרה במכסה.")
            if e.code == 429:
                sys.exit("⚠️ נגמרה המכסה החודשית של RentCast (50 בקשות). מתחדשת בתחילת החודש, או להעלות תוכנית.")
            sys.exit(f"⚠️ RentCast החזיר {e.code}: {body}")
        out.extend(_from_rentcast(L) for L in data)
        print(f"  RentCast {z}: {len(data)} מודעות", file=sys.stderr)
    return out, Counter()


def _from_rentcast(L):
    hist = sorted((L.get("history") or {}).items())
    prices = [h.get("price") for _, h in hist if h.get("price")]
    cuts = sum(1 for a, b in zip(prices, prices[1:]) if b < a) if len(prices) > 1 else None
    return {"address": L.get("formattedAddress") or L.get("addressLine1") or "",
            "zip": str(L.get("zipCode") or "")[:5], "price": L.get("price"),
            "sqft": L.get("squareFootage"), "year": L.get("yearBuilt"), "dom": L.get("daysOnMarket"),
            "lat": L.get("latitude"), "lon": L.get("longitude"), "url": "",
            "status": L.get("status"), "mls": L.get("mlsNumber"), "cuts": cuts}


# ──────────────────────── שווי שומה מרשומות השומה ────────────────────────

def _street(address):
    """'5302 N ARLINGTON AVE' -> ('5302', 'ARLINGTON'). מדלג על קידומת כיוון."""
    m = re.match(r"\s*(\d+)\s+(.*)", address or "")
    if not m:
        return None, None
    words = [w for w in re.split(r"[\s,]+", m.group(2).upper().replace(".", "")) if w]
    return m.group(1), next((w for w in words if w not in SKIP), None)


def parcel_av(address, zip5, cache):
    """שווי שומה + בעלים לפי מספר בית ורחוב. תוצאה אחת בדיוק — אחרת ריק, לא ניחוש."""
    key = f"{address}|{zip5}".upper()
    if key in cache:
        return cache[key]
    num, street = _street(address)
    if not num or not street:
        return {}
    where = (f"STNUMBER='{num}' AND UPPER(FULL_STNAME) LIKE '%{street.replace(chr(39), chr(39) * 2)}%'"
             + (f" AND ZIPCODE LIKE '{zip5}%'" if zip5 else ""))
    q = urlencode({"where": where, "returnGeometry": "false", "f": "json",
                   "outFields": "STATEPARCELNUMBER,FULLOWNERNAME,PROPERTY_SUB_CLASS,ASSESSORYEAR_TOTALAV"})
    req = Request(f"{analyze.PARCEL_URL}?{q}", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=60) as r:
            feats = json.loads(r.read().decode("utf-8", "replace")).get("features") or []
    except Exception as e:                       # לא נשמר במטמון — ננסה שוב בהרצה הבאה
        print(f"  ⚠️ xmaps נכשל עבור {address}: {e}", file=sys.stderr)
        return {}
    a = feats[0].get("attributes", {}) if len(feats) == 1 else {}
    if not a:                                    # לא נמצא / לא חד-משמעי: לא נשמר, כדי ששיפור בפרסור ינסה שוב
        return {}
    cache[key] = {"av": analyze._i(a.get("ASSESSORYEAR_TOTALAV")),
                  "parcel": analyze._parcel(a.get("STATEPARCELNUMBER")),
                  "owner": (a.get("FULLOWNERNAME") or "")[:40], "sub": a.get("PROPERTY_SUB_CLASS")}
    return cache[key]


# ─────────────────────────────── החיתום ───────────────────────────────

def score(L, band, reno, parcel):
    """כל המספרים של שורה אחת. פונקציה טהורה — זו שנבדקת ב-selftest."""
    rule = sdf.rule_for(L["zip"])
    offer = rule * band["arv"] - reno
    return {**L, "arv": band["arv"], "low": band["low"], "n": band["n"], "reno": reno, "rule": rule,
            "offer_rule": offer, "gap": (L["price"] - offer) / L["price"],
            "profit_rule": arv.deal(band["low"], offer, reno, zip5=L["zip"])["profit"],
            "profit_ask": arv.deal(band["low"], L["price"], reno, zip5=L["zip"])["profit"],
            "av": parcel.get("av", 0), "owner": parcel.get("owner", ""),
            "rrp": bool(L["year"] and L["year"] < 1978)}


def underwrite(listings, dom_min=0, comps_for=None):
    zips = set(sdf.TARGET)
    sales, loc = arv.market()
    today = max(s["date"] for s in loc)
    cache = json.load(open(AV_CACHE, encoding="utf-8")) if os.path.exists(AV_CACHE) else {}
    rows, skipped = [], Counter()
    todo = [L for L in listings if L["zip"] in zips and L["price"] and L["lat"]
            and (not dom_min or (L["dom"] or 0) >= dom_min)
            and f"{L['address']}|{L['zip']}".upper() not in cache]
    if todo:                                   # שווי שומה: 4 במקביל, ~0.5 שנייה למודעה
        print(f"  שולף שווי שומה ל-{len(todo)} מודעות חדשות...", file=sys.stderr)
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(lambda L: parcel_av(L["address"], L["zip"], cache), todo))
    for L in listings:
        if L["zip"] not in zips:
            skipped["מחוץ ל-12 זיפי היעד"] += 1
            continue
        if dom_min and (L["dom"] or 0) < dom_min:
            skipped[f"DOM < {dom_min:.0f}"] += 1
            continue
        if not L["price"] or not L["lat"]:
            skipped["בלי מחיר/קואורדינטות"] += 1
            continue
        p = parcel_av(L["address"], L["zip"], cache)
        comps = arv.find_comps(loc, (L["lat"], L["lon"]), today, p.get("av", 0))
        b = arv.arv_band(comps, L["zip"])
        if not b:
            skipped["בלי קומפס"] += 1
            continue
        if comps_for and comps_for.upper() in L["address"].upper():
            _print_comps(L, comps, b, p)
        rows.append(score(L, b, arv.reno_budget(L["sqft"], L["year"]), p))
    with open(AV_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    return rows, skipped, today


def _print_comps(L, comps, b, p):
    print(f"\n▌ {L['address']}  ·  {L['zip']}  ·  מבוקש ${L['price']:,.0f}"
          f"  ·  שומה {'$' + format(p['av'], ',') if p.get('av') else '—'}"
          f"{'  ·  בעלים: ' + p['owner'] if p.get('owner') else ''}")
    print(f"{'תאריך':<12}{'מרחק':>7}{'מחיר':>11}{'שומה':>11}  כתובת")
    for c in comps:
        print(f"{c['date']:<12}{arv._km((L['lat'], L['lon']), c['ll']):>6.2f}ק\"מ"
              f"{c['price']:>11,.0f}{c['av']:>11,.0f}  {c['address'][:34]}")
    arv._print_band(b)


def _f(v, w, money=False):
    if not v:
        return f"{'—':>{w}}"
    return f"{v:>{w},.0f}"


def print_table(rows, skipped, today, show_all=False):
    rows.sort(key=lambda r: r["gap"])
    R = f"BUY BOX {sdf.RULES['BUY']:.0%} · צפון {sdf.RULES['NORTH']:.0%}"
    print(f"\n{len(rows)} מודעות נחתמו · נתוני מכר עד {today} · ממוין לפי הפער בין המבוקש להצעת הכלל ({R})")
    if skipped:
        print("דולגו: " + " · ".join(f"{k} {v}" for k, v in skipped.most_common()))
    hdr = (f"{'ZIP':<7}{'כלל':>5}{'מבוקש':>10}{'DOM':>5}{'sqft':>7}{'שנה':>6}{'ARV':>10}{'הכלל מתיר':>10}"
           f"{'פער':>6}{'רווח@כלל':>10}{'רווח@מבוקש':>12}{'שומה':>11}{'n':>3}  כתובת")
    print("\n" + hdr)
    print("-" * 111)
    for r in rows if show_all else rows[:40]:
        tag = (" ⚠️RRP" if r["rrp"] else "") + (f" ↓{r['cuts']}" if r.get("cuts") else "")
        print(f"{r['zip']:<7}{r['rule']:>5.0%}{r['price']:>10,.0f}{_f(r['dom'], 5)}{_f(r['sqft'], 7)}{_f(r['year'], 6)}"
              f"{r['arv']:>10,.0f}{r['offer_rule']:>10,.0f}{r['gap']:>6.0%}{r['profit_rule']:>10,.0f}"
              f"{r['profit_ask']:>12,.0f}{_f(r['av'], 11)}{r['n']:>3}  {r['address'][:28]}{tag}")
    if not show_all and len(rows) > 40:
        print(f"... ועוד {len(rows) - 40}. --all להכל.")
    print(f"""
פער      = כמה מתחת למבוקש צריך לקנות כדי לעמוד בכלל ({R}). מתחת ל-15% על מודעה תקועה — יש שיחה.
רווח     = תרחיש תחתון (ARV − לפי שכבה: BUY BOX {arv.ERR['BUY'][0]:.1%}, צפון {arv.ERR['NORTH'][0]:.1%}), מזומן מלא, {arv.MONTHS_DEFAULT} ח'. @כלל = בהצעת הכלל; @מבוקש = במחיר מלא.
שיפוץ    = sqft × ${arv.RENO_SQFT} (+${arv.RENO_PRE78} לפני 1978, ⚠️RRP) × {1 + arv.RENO_RESERVE:.2f}. בלי sqft: ${arv.RENO_DEFAULT:,}.
שומה '—' = לא נמצאה ברשומות השומה ⇒ קומפס בלי סינון גודל, טווח רחב יותר.
🔴 ARV הוא חציון קומפס, לא מספר. לפני הצעה: --comps "<כתובת>" ולהסתכל בעיניים.""")
    links = [r for r in rows[:15] if r.get("url")]
    if links:
        print("\nקישורים — 15 הראשונים:")
        for r in links:
            print(f"  {r['address'][:30]:<32} {r['url']}")


# ─────────────────────────────── בדיקה עצמית ───────────────────────────────

def selftest():
    head = ("SALE TYPE,SOLD DATE,PROPERTY TYPE,ADDRESS,CITY,STATE OR PROVINCE,ZIP OR POSTAL CODE,PRICE,"
            "BEDS,BATHS,LOCATION,SQUARE FEET,LOT SIZE,YEAR BUILT,DAYS ON MARKET,$/SQUARE FEET,HOA/MONTH,"
            "STATUS,NEXT OPEN HOUSE START TIME,NEXT OPEN HOUSE END TIME,"
            "URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING),"
            "SOURCE,MLS#,FAVORITE,INTERESTED,LATITUDE,LONGITUDE\n")
    body = ('MLS Listing,,Single Family Residential,8058 Cherrybark Dr,Indianapolis,IN,46236,"$289,900",3,2,'
            'Geist,"1,442",8712,1961,112,201,,Active,,,https://www.redfin.com/IN/x/1,MIBOR,22011111,N,N,39.902098,-85.949132\n'
            'MLS Listing,,Condo/Co-op,1 Main St #2,Indianapolis,IN,46236,150000,2,2,,900,,1990,5,167,,Active,,,u,MIBOR,1,N,N,39.9,-85.9\n'
            'PAST SALE,2026-06-01,Single Family Residential,9 Old Rd,Indianapolis,IN,46236,200000,3,2,,1200,,1980,0,167,,Sold,,,u,MIBOR,2,N,N,39.9,-85.9\n'
            'MLS Listing,,Single Family Residential,No Coords Ln,Indianapolis,IN,46236,200000,3,2,,1200,,1980,0,167,,Active,,,u,MIBOR,3,N,N,,\n')
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(head + body)
    try:
        rows, skipped = read_redfin([f.name])
    finally:
        os.remove(f.name)
    assert len(rows) == 1, rows
    assert skipped["Condo/Co-op"] == 1 and skipped["PAST SALE"] == 1 and skipped["בלי קואורדינטות"] == 1, skipped
    # שורת ההערה של Redfin (תא אחד) לא נספרת כלל
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f3:
        f3.write(head + "In accordance with local MLS rules, some MLS listings are not included in the download\n" + body.splitlines()[0] + "\n")
    try:
        rows3, skipped3 = read_redfin([f3.name])
    finally:
        os.remove(f3.name)
    assert len(rows3) == 1 and not skipped3, (rows3, skipped3)
    L = rows[0]
    assert L["price"] == 289_900 and L["sqft"] == 1_442 and L["year"] == 1961 and L["dom"] == 112
    assert L["zip"] == "46236" and abs(L["lat"] - 39.902098) < 1e-6 and L["url"].startswith("https://")

    # ציון: הצעת הכלל על האומדן, פער מהמבוקש, רווח בתרחיש התחתון
    band = {"arv": 301_000, "low": 301_000 * (1 - arv.ERR["BUY"][0]), "n": 10}
    reno = arv.reno_budget(L["sqft"], L["year"])
    assert abs(reno - 1_442 * 40 * 1.17) < 1              # לפני 1978 ⇒ $40/sqft
    s = score(L, band, reno, {"av": 297_200})
    assert s["rule"] == 0.72 and abs(s["offer_rule"] - (0.72 * 301_000 - reno)) < 1
    assert abs(s["gap"] - (289_900 - s["offer_rule"]) / 289_900) < 1e-9
    assert s["profit_ask"] < s["profit_rule"] and s["rrp"] and s["av"] == 297_200
    s2 = score(dict(L, zip="46220"), band, reno, {})                # צפון: 69%
    assert s2["rule"] == 0.69 and abs(s2["offer_rule"] - (0.69 * 301_000 - reno)) < 1 and s2["av"] == 0
    # רחוב: מדלגים על קידומת כיוון; כתובת בלי מספר לא נשלחת לשרת
    assert _street("5302 N ARLINGTON AVE") == ("5302", "ARLINGTON")
    assert _street("8058 Cherrybark Dr.") == ("8058", "CHERRYBARK")
    assert _street("Main St") == (None, None)
    assert _street("3110 St. Paul Street") == ("3110", "PAUL")          # ST = Saint, לא Street
    assert _street("1904 West 57th Street") == ("1904", "57TH")
    # כפולים (מכירה רב-חלקתית) נספרים פעם אחת
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f2:
        f2.write(head + body.splitlines()[0] + "\n" + body.splitlines()[0] + "\n")
    try:
        rows2, skipped2 = read_redfin([f2.name])
    finally:
        os.remove(f2.name)
    assert len(rows2) == 1 and skipped2["כפולים"] == 1, (len(rows2), skipped2)
    # RentCast: מיפוי שדות והורדות מחיר מההיסטוריה
    R = _from_rentcast({"formattedAddress": "1 A St, Indianapolis, IN 46236", "zipCode": 46236,
                        "price": 250000, "squareFootage": 1500, "yearBuilt": 1985, "daysOnMarket": 95,
                        "latitude": 39.9, "longitude": -85.9,
                        "history": {"2026-05-01": {"price": 270000}, "2026-06-01": {"price": 260000},
                                    "2026-07-01": {"price": 250000}}})
    assert R["cuts"] == 2 and R["price"] == 250000 and R["zip"] == "46236" and R["dom"] == 95
    assert _from_rentcast({"price": 1})["cuts"] is None
    print("selftest: ok")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    if args and args[0] == "selftest":
        selftest()
        sys.exit()
    dom_min = float(args[args.index("--dom") + 1]) if "--dom" in args else 0
    comps_for = args[args.index("--comps") + 1] if "--comps" in args else None
    if "--rentcast" in args:
        listings, skipped = read_rentcast()
    else:
        paths = [a for a in args if a.lower().endswith(".csv")]
        if not paths:
            copied, paths = collect_downloads()
            print(f"  {copied} קבצים חדשים מ-Downloads · {len(paths)} קבצים ב-data/redfin/", file=sys.stderr)
        if not paths:
            sys.exit("אין קבצי redfin_*.csv — להוריד מ-Redfin (\"Download All\" בתחתית החיפוש) ל-Downloads ולהריץ שוב.\n"
                     "שימוש: python3 listings.py [קבצים.csv] [--dom 30] [--comps \"כתובת\"] [--all]  |  --rentcast")
        listings, skipped = read_redfin(paths)
    rows, more, today = underwrite(listings, dom_min, comps_for)
    print_table(rows, skipped + more, today, "--all" in args)
