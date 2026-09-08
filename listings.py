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
מקור ב' — RentCast, אוטומטי, רישוי נקי, 50 בקשות בחודש חינם. 12 זיפים = 12 בקשות.
    ה-snapshot של כל זיפ (data/rentcast/{יום}_{זיפ}.json) משמש שוב עד 6 ימים ⇒ ריצה שבועית = 12
    בקשות, וכל ריצה נוספת באותו שבוע חינם. --fresh מושך מחדש. מונה הבקשות ב-rentcast_quota.json
    (שורש הריפו) עוצר לפני הבקשה שתחרוג מ-50; מעל זה = תשלום ⇒ רק אחרי שדרוג ב-rentcast.io
    ועם --limit N מפורש. מפתח ב-RENTCAST_API_KEY (.env). אומת 6.9.2026: 913 מודעות ב-12 זיפים.
    🔴 `history` של RentCast = רישומים חוזרים (כל עלייה מחדש ל-MLS), לא הורדות מחיר בתוך מודעה —
    כאלה אין ב-API. ↓N = נרשם מחדש N פעמים במחיר נמוך יותר · DOM מצטבר = כל הרישומים יחד
    (ה-DOM הנוכחי מתאפס ברישום מחדש ומסתיר את התקועות באמת: 2442 Guilford, DOM 14, מצטבר 408).

    python3 listings.py                    # אוסף redfin_*.csv מ-Downloads (30 יום) אל data/redfin/ ומריץ הכל
    python3 listings.py --rentcast --dom 90 --cuts 2   # השבועי: DOM מצטבר ≥ 90 ונרשם מחדש פעמיים בזול יותר
    python3 listings.py --min-arv 0 --max-offer 9e9   # לבטל את ברירות המחדל: ARV ≥ $200K, הצעה ≤ $350K
    python3 listings.py --comps "8058 CHERRYBARK"     # הקומפס של מודעה אחת, לעין אנושית
    python3 listings.py some.csv other.csv # קבצים מפורשים
    python3 listings.py --rentcast         # 12 זיפי היעד מ-RentCast (snapshot מהשבוע = 0 בקשות)
    python3 listings.py --rentcast --fresh --limit 500   # למשוך מחדש בתוך השבוע · --limit רק אחרי שדרוג בתשלום
    python3 listings.py selftest

רק 12 זיפי היעד (`sdf.TARGET`) נחתמים. מודעה מחוץ להם נספרת ומדולגת — קומפס לזיפ
חדש דורשים גיאוקוד של כל המכירות בו (דקות). להוסיף זיפ = להוסיף אותו ל-`sdf.NORTH`.

הפלט ממוין לפי הפער בין המחיר המבוקש להצעת כלל ההצעה (sdf.RULE): פער קטן = עסקה קרובה.
שווי השומה לכל מודעה נשלף מרשומות השומה (xmaps) לפי מספר בית + רחוב, ונשמר
במטמון. 🔴 חיפוש לפי נקודה נפסל: קואורדינטות מגאוקוד נוחתות על החלקה השכנה
(6259 Chadworth החזיר את 6251 — נמדד 6.9.2026).
"""
import csv, glob, json, os, re, shutil, sys, time, importlib, tempfile, statistics as st
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, defaultdict
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
            dom = _num(g("dom"))
            out.append({"address": g("address"), "zip": g("zip")[:5], "price": _num(g("price")),
                        "sqft": _num(g("sqft")), "year": _num(g("year")), "dom": dom,
                        "lat": lat, "lon": lon, "url": g("url"), "status": g("status"),
                        "mls": g("mls"), "cuts": None, "lists": 1, "dom_total": dom or 0, "drop": None})
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


QUOTA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rentcast_quota.json")
RENTCAST_FREE = 50          # התוכנית החינמית (Developer). מעל זה — שדרוג בתשלום ב-rentcast.io
SNAPSHOT_DAYS = 6           # snapshot צעיר מזה = אותו שבוע ⇒ משמש שוב בלי בקשה. "לא יותר מפעם בשבוע"


def _quota(add=0):
    """מונה בקשות RentCast לחודש הנוכחי. בשורש הריפו, לא ב-data/ (שנמחק בבטחה). add = לרשום בקשות."""
    q = json.load(open(QUOTA_FILE, encoding="utf-8")) if os.path.exists(QUOTA_FILE) else {}
    m = time.strftime("%Y-%m")
    if add:
        q[m] = q.get(m, 0) + add
        with open(QUOTA_FILE, "w", encoding="utf-8") as f:
            json.dump(q, f, indent=1)
    return q.get(m, 0)


def _guard(need, limit=RENTCAST_FREE):
    """עוצר לפני הבקשה הראשונה אם הריצה תחרוג מהמכסה. מעל 50 = תשלום ⇒ רק עם --limit מפורש."""
    used, m = _quota(), time.strftime("%Y-%m")
    print(f"  RentCast: {used}/{limit} בקשות ב-{m}"
          + (f" · הריצה הזו {need} ⇒ {used + need}/{limit}" if need else " · הכל מה-snapshot, 0 בקשות"),
          file=sys.stderr)
    if used + need > limit:
        sys.exit(f"🛑 RentCast: {used}/{limit} בקשות ב-{m}, הריצה הזו צריכה עוד {need} ⇒ {used + need} > {limit}. "
                 f"עצרתי לפני הבקשה הראשונה.\n"
                 f"   בלי --fresh, snapshot מהשבוע האחרון משמש חינם. המכסה החינמית מתחדשת ב-1 לחודש.\n"
                 f"   תשלום: לשדרג תוכנית ב-app.rentcast.io (~$74/ח' לפי decisions.md) ורק אז --limit N.")


def read_rentcast(zips=sdf.TARGET, fresh=False, limit=RENTCAST_FREE):
    """
    12 זיפים = 12 בקשות. התשובה הגולמית נשמרת ב-data/rentcast/{יום}_{זיפ}.json, וה-snapshot האחרון
    של כל זיפ משמש שוב עד SNAPSHOT_DAYS ימים — הריצה השבועית עולה 12 בקשות, וכל ריצה נוספת
    באותו שבוע חינם. --fresh מושך מחדש. המונה נבדק לפני הבקשה הראשונה ונרשם לפני כל שליחה.
    """
    snap = os.path.join(sdf.DATA, "rentcast")
    os.makedirs(snap, exist_ok=True)
    have = {}
    for z in zips:
        files = sorted(glob.glob(os.path.join(snap, f"*_{z}.json")))
        if files and not fresh:
            day = os.path.basename(files[-1])[:10]
            if time.time() - time.mktime(time.strptime(day, "%Y-%m-%d")) < SNAPSHOT_DAYS * 86400:
                have[z] = files[-1]
    _guard(len(zips) - len(have), limit)
    key = _api_key() if len(have) < len(zips) else None
    out = []
    for z in zips:
        if z in have:
            with open(have[z], encoding="utf-8") as f:
                data = json.load(f)
            print(f"  RentCast {z}: {len(data)} מודעות (snapshot {os.path.basename(have[z])[:10]})", file=sys.stderr)
        else:
            q = urlencode({"zipCode": z, "status": "Active", "propertyType": "Single Family", "limit": 500})
            req = Request(f"{RENTCAST_URL}?{q}", headers={"X-Api-Key": key, "Accept": "application/json"})
            _quota(1)                                    # נרשם לפני השליחה — שמרני
            try:
                with urlopen(req, timeout=60) as r:
                    data = json.loads(r.read().decode("utf-8"))
            except HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:300]
                if e.code == 403 and "subscription-inactive" in body:
                    _quota(-1)
                    sys.exit("⚠️ המפתח קיים אבל התוכנית לא הופעלה. ב-app.rentcast.io/app/api לבחור את תוכנית "
                             "Developer (חינם, 50 בקשות בחודש) ולהפעיל אותה — ואז להריץ שוב. הבקשה לא נספרה במכסה.")
                if e.code == 429:
                    sys.exit("⚠️ נגמרה המכסה החודשית של RentCast (50 בקשות). מתחדשת בתחילת החודש, או להעלות תוכנית.")
                sys.exit(f"⚠️ RentCast החזיר {e.code}: {body}")
            with open(os.path.join(snap, f"{time.strftime('%Y-%m-%d')}_{z}.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            print(f"  RentCast {z}: {len(data)} מודעות (חדש)", file=sys.stderr)
        out.extend(_from_rentcast(L) for L in data)
    return out, Counter()


def _from_rentcast(L):
    """
    🔴 `history` = אירועי רישום ("Sale Listing"), אחד לכל עלייה ל-MLS — לא הורדות מחיר. אומת 7.9.2026:
    641 E 33rd St נרשם 7/2024 ($259K, 270 יום), 5/2025 ($242.5K, 206), 4/2026 ($238K, 154). לכן:
    cuts = רישומים חוזרים במחיר נמוך יותר · dom_total = DOM של הרישומים שנסגרו + הנוכחי · drop = מהמבוקש הראשון.
    """
    hist = sorted((L.get("history") or {}).items())
    prices = [h.get("price") for _, h in hist if h.get("price")]
    cuts = sum(1 for a, b in zip(prices, prices[1:]) if b < a) if len(prices) > 1 else None
    dom, price = L.get("daysOnMarket"), L.get("price")
    past = sum(h.get("daysOnMarket") or 0 for _, h in hist if h.get("removedDate"))
    return {"address": L.get("formattedAddress") or L.get("addressLine1") or "",
            "zip": str(L.get("zipCode") or "")[:5], "price": price,
            "sqft": L.get("squareFootage"), "year": L.get("yearBuilt"), "dom": dom,
            "lat": L.get("latitude"), "lon": L.get("longitude"), "url": "",
            "status": L.get("status"), "mls": L.get("mlsNumber"), "cuts": cuts,
            "lists": max(1, len(hist)), "dom_total": past + (dom or 0),
            "drop": (price / prices[0] - 1) if price and prices and prices[0] else None}


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

def score(L, band, reno, parcel, ppsf_zip=None, nearest=None, street_med=None):
    """
    כל המספרים של שורה אחת. פונקציה טהורה — זו שנבדקת ב-selftest.
    דגלים (מהריצה האמיתית הראשונה, 6.9.2026): 'ARV' — ARV לרגל גבוה פי 1.7+ מהמבוקש
    החציוני בזיפ ⇒ הקומפס גדולים מהבית (בית של 842 sqft קיבל $285K). 'שומה' — שומה
    מתחת ל-$40K = מגרש: בית הרוס, או שהקומפס היו בנייה חדשה.
    """
    rule = sdf.rule_for(L["zip"])
    offer = rule * band["arv"] - reno
    flags = []
    if L["sqft"] and ppsf_zip and band["arv"] / L["sqft"] > 1.7 * ppsf_zip:
        flags.append("ARV")
    if parcel.get("av") and parcel["av"] < 40_000:
        flags.append("שומה")
    # הרחוב אומר פחות מהאומדן: השכן הצמוד 20%+ מתחת (Village Oak, 6.9), או חציון הקומפס
    # מאותו רחוב 10%+ מתחת (3236 Central: קומפס מ-New Jersey/Delaware ניפחו ב-16%, 7.9).
    if ((nearest and nearest[0] <= 0.15 and nearest[1] < 0.8 * band["arv"])
            or (street_med and street_med < 0.90 * band["arv"])):
        flags.append("רחוב")
    return {**L, "arv": band["arv"], "low": band["low"], "n": band["n"], "reno": reno, "rule": rule, "flags": flags,
            "offer_rule": offer, "gap": (L["price"] - offer) / L["price"],
            "profit_rule": arv.deal(band["low"], offer, reno, zip5=L["zip"])["profit"],
            "profit_ask": arv.deal(band["low"], L["price"], reno, zip5=L["zip"])["profit"],
            "av": parcel.get("av", 0), "owner": parcel.get("owner", ""),
            "rrp": bool(L["year"] and L["year"] < 1978)}


def _not_stuck(L, dom_min, cuts_min):
    """למה המודעה לא "תקועה" לפי הסף — או None. DOM מצטבר על כל הרישומים; cuts = רישומים חוזרים בזול יותר."""
    if dom_min and (L.get("dom_total") or L["dom"] or 0) < dom_min:
        return f"DOM מצטבר < {dom_min:.0f}"
    if cuts_min and (L["cuts"] or 0) < cuts_min:
        return f"פחות מ-{cuts_min:.0f} רישומים חוזרים בזול יותר"
    return None


def underwrite(listings, dom_min=0, comps_for=None, min_arv=200_000, max_offer=350_000, cuts_min=0):
    zips = set(sdf.TARGET)
    sales, loc = arv.market()
    today = max(s["date"] for s in loc)
    cache = json.load(open(AV_CACHE, encoding="utf-8")) if os.path.exists(AV_CACHE) else {}
    rows, skipped = [], Counter()
    ppsf = defaultdict(list)                   # מבוקש לרגל, חציון לכל זיפ — לבדיקת סבירות של ה-ARV
    for L in listings:
        if L["zip"] in zips and L["price"] and L["sqft"]:
            ppsf[L["zip"]].append(L["price"] / L["sqft"])
    ppsf = {z: st.median(v) for z, v in ppsf.items()}
    todo = [L for L in listings if L["zip"] in zips and L["price"] and L["lat"]
            and not _not_stuck(L, dom_min, cuts_min)
            and f"{L['address']}|{L['zip']}".upper() not in cache]
    if todo:                                   # שווי שומה: 4 במקביל, ~0.5 שנייה למודעה
        print(f"  שולף שווי שומה ל-{len(todo)} מודעות חדשות (~{len(todo) * 0.45 / 60:.0f} דקות, פעם אחת)...",
              file=sys.stderr)
        with ThreadPoolExecutor(4) as ex:
            for i, _ in enumerate(ex.map(lambda L: parcel_av(L["address"], L["zip"], cache), todo), 1):
                if i % 50 == 0 or i == len(todo):
                    print(f"    {i}/{len(todo)}", file=sys.stderr)
                if i % 100 == 0:                  # שמירה חלקית — הפסקה באמצע לא מאבדת את מה שנשלף
                    with open(AV_CACHE, "w", encoding="utf-8") as f:
                        json.dump(cache, f, ensure_ascii=False)
    for L in listings:
        if L["zip"] not in zips:
            skipped["מחוץ ל-12 זיפי היעד"] += 1
            continue
        why = _not_stuck(L, dom_min, cuts_min)
        if why:
            skipped[why] += 1
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
        nearest = (arv._km((L["lat"], L["lon"]), comps[0]["ll"]), comps[0]["price"])
        same = [c["price"] for c in comps if _street(c["address"])[1] == _street(L["address"])[1]]
        street_med = st.median(same) if same else None
        if comps_for and comps_for.upper() in L["address"].upper():
            _print_comps(L, comps, b, p)
            row = score(L, b, arv.reno_budget(L["sqft"], L["year"]), p, ppsf.get(L["zip"]), nearest, street_med)
            arv._log({"address": L["address"].split(",")[0], "zip": L["zip"], "parcel": p.get("parcel", "")},
                     b, row["offer_rule"], row["reno"], f"{L['sqft']:,.0f} sqft" if L["sqft"] else "ברירת מחדל",
                     arv.MONTHS_DEFAULT, arv.deal(b["low"], row["offer_rule"], row["reno"], zip5=L["zip"]),
                     arv.deal(b["arv"], row["offer_rule"], row["reno"], zip5=L["zip"]))
        if b["arv"] < min_arv:                 # מתחת ל-$200K ATTOM מודדת הפסד — לא הפרודקט
            skipped[f"ARV מתחת ל-${min_arv/1e3:.0f}K"] += 1
            continue
        row = score(L, b, arv.reno_budget(L["sqft"], L["year"]), p, ppsf.get(L["zip"]), nearest, street_med)
        if row["offer_rule"] > max_offer:      # מעל ההון לסלוט אחד
            skipped[f"הצעת הכלל מעל ${max_offer/1e3:.0f}K"] += 1
            continue
        rows.append(row)
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
    d0, p0 = arv._km((L["lat"], L["lon"]), comps[0]["ll"]), comps[0]["price"]
    if d0 <= 0.15 and p0 < 0.8 * b["arv"]:
        print(f"🚩 השכן הצמוד ({d0*1000:.0f} מ') נמכר ב-${p0:,.0f} — {1 - p0/b['arv']:.0%} מתחת לאומדן. "
              f"כיס של בתים קטנים? התקרה כנראה קרובה ל-${p0:,.0f}, לא ל-${b['arv']:,.0f}.")
    same = [c["price"] for c in comps if _street(c["address"])[1] == _street(L["address"])[1]]
    if same and st.median(same) < 0.90 * b["arv"]:
        print(f"🚩 {len(same)} קומפס מאותו רחוב: חציון ${st.median(same):,.0f} — "
              f"{1 - st.median(same)/b['arv']:.0%} מתחת לאומדן שנשען על הרחובות השכנים. "
              f"בשכונה שמתומחרת לפי רחוב, זה המספר.")


def _f(v, w, money=False):
    if not v:
        return f"{'—':>{w}}"
    return f"{v:>{w},.0f}"


def print_table(rows, skipped, today, show_all=False):
    rows.sort(key=lambda r: (bool(r["flags"]), r["gap"]))      # מסומנים בסוף
    R = f"BUY BOX {sdf.RULES['BUY']:.0%} · צפון {sdf.RULES['NORTH']:.0%}"
    print(f"\n{len(rows)} מודעות נחתמו · נתוני מכר עד {today} · ממוין לפי הפער בין המבוקש להצעת הכלל ({R})")
    if skipped:
        print("דולגו: " + " · ".join(f"{k} {v}" for k, v in skipped.most_common()))
    hdr = (f"{'ZIP':<7}{'כלל':>5}{'מבוקש':>10}{'DOM':>5}{'מצטבר':>7}{'sqft':>7}{'שנה':>6}{'ARV':>10}{'הכלל מתיר':>10}"
           f"{'פער':>6}{'רווח@כלל':>10}{'רווח@מבוקש':>12}{'שומה':>11}{'n':>3}  כתובת")
    print("\n" + hdr)
    print("-" * 118)
    for r in rows if show_all else rows[:40]:
        tag = ((" ⚠️RRP" if r["rrp"] else "")
               + (f" ↓{r['cuts']}" + (f" {r['drop']:.0%}" if r.get("drop") else "") if r.get("cuts") else "")
               + (" 🚩" + "/".join(r["flags"]) if r["flags"] else ""))
        print(f"{r['zip']:<7}{r['rule']:>5.0%}{r['price']:>10,.0f}{_f(r['dom'], 5)}{_f(r.get('dom_total'), 7)}{_f(r['sqft'], 7)}{_f(r['year'], 6)}"
              f"{r['arv']:>10,.0f}{r['offer_rule']:>10,.0f}{r['gap']:>6.0%}{r['profit_rule']:>10,.0f}"
              f"{r['profit_ask']:>12,.0f}{_f(r['av'], 11)}{r['n']:>3}  {r['address'].split(',')[0][:28]}{tag}")
    if not show_all and len(rows) > 40:
        print(f"... ועוד {len(rows) - 40}. --all להכל.")
    print(f"""
פער      = כמה מתחת למבוקש צריך לקנות כדי לעמוד בכלל ({R}). מתחת ל-15% על מודעה תקועה — יש שיחה.
רווח     = תרחיש תחתון (ARV − לפי שכבה: BUY BOX {arv.ERR['BUY'][0]:.1%}, צפון {arv.ERR['NORTH'][0]:.1%}), מזומן מלא, {arv.MONTHS_DEFAULT} ח'. @כלל = בהצעת הכלל; @מבוקש = במחיר מלא.
שיפוץ    = sqft × ${arv.RENO_SQFT} (+${arv.RENO_PRE78} לפני 1978, ⚠️RRP) × {1 + arv.RENO_RESERVE:.2f}. בלי sqft: ${arv.RENO_DEFAULT:,}.
מצטבר   = DOM על כל הרישומים החוזרים יחד. ה-DOM הנוכחי מתאפס ברישום מחדש ומסתיר תקועות — --dom מסנן על המצטבר.
↓N       = נרשם מחדש N פעמים במחיר נמוך יותר, ואחריו הירידה מהמבוקש הראשון. (history של RentCast = רישומים, לא הורדות מחיר)
שומה '—' = לא נמצאה ברשומות השומה ⇒ קומפס בלי סינון גודל, טווח רחב יותר.
🚩ARV     = ARV לרגל גבוה פי 1.7+ מהמבוקש החציוני בזיפ — הקומפס גדולים מהבית. 🚩שומה = שומה של מגרש, בית הרוס.
🚩רחוב    = הרחוב אומר פחות: השכן הצמוד (≤150 מ') 20%+ מתחת לאומדן, או חציון הקומפס מאותו רחוב 10%+ מתחת. כל המסומנים ממוינים לסוף.
מסוננים  = ARV מתחת ל-$200K (מדרגת ההפסד של ATTOM) והצעת כלל מעל $350K (ההון לסלוט אחד). --min-arv / --max-offer לשנות.
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
    assert L["dom_total"] == 112 and L["lists"] == 1 and L["cuts"] is None and L["drop"] is None

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
    # דגלים: ARV $209/sqft מול מבוקש $100/sqft בזיפ = פי 2.1 ⇒ 🚩ARV; מול $150 ⇒ נקי; שומה $30K ⇒ 🚩שומה
    assert score(L, band, reno, {"av": 297_200}, ppsf_zip=100)["flags"] == ["ARV"]
    assert score(L, band, reno, {"av": 297_200}, ppsf_zip=150)["flags"] == []
    assert score(L, band, reno, {"av": 30_000}, ppsf_zip=150)["flags"] == ["שומה"]
    assert s["flags"] == []
    # השכן הצמוד: 50 מ' ו-$190K מול אומדן $301K ⇒ 🚩רחוב; $280K ⇒ נקי; 500 מ' ⇒ לא שכן
    assert score(L, band, reno, {}, 150, nearest=(0.05, 190_000))["flags"] == ["רחוב"]
    assert score(L, band, reno, {}, 150, nearest=(0.05, 280_000))["flags"] == []
    assert score(L, band, reno, {}, 150, nearest=(0.5, 190_000))["flags"] == []
    # ...או חציון הקומפס מאותו רחוב 10%+ מתחת לאומדן (3236 Central: $294K מול $342.5K = −14%)
    assert score(L, band, reno, {}, 150, street_med=294_000 / 342_500 * 301_000)["flags"] == ["רחוב"]
    assert score(L, band, reno, {}, 150, street_med=0.95 * 301_000)["flags"] == []
    assert score(L, band, reno, {}, 150, nearest=(0.5, 190_000), street_med=None)["flags"] == []
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
    # RentCast: history = רישומים חוזרים. cuts = כמה פעמים נרשם מחדש בזול יותר, DOM מצטבר = שנסגרו + הנוכחי
    R = _from_rentcast({"formattedAddress": "1 A St, Indianapolis, IN 46236", "zipCode": 46236,
                        "price": 250000, "squareFootage": 1500, "yearBuilt": 1985, "daysOnMarket": 95,
                        "latitude": 39.9, "longitude": -85.9,
                        "history": {"2026-05-01": {"price": 270000, "daysOnMarket": 20, "removedDate": "2026-05-21"},
                                    "2026-06-01": {"price": 260000, "daysOnMarket": 25, "removedDate": "2026-06-26"},
                                    "2026-07-01": {"price": 250000, "daysOnMarket": 95, "removedDate": None}}})
    assert R["cuts"] == 2 and R["price"] == 250000 and R["zip"] == "46236" and R["dom"] == 95
    assert R["lists"] == 3 and R["dom_total"] == 140 and abs(R["drop"] - (250000 / 270000 - 1)) < 1e-9
    R1 = _from_rentcast({"price": 1})
    assert R1["cuts"] is None and R1["lists"] == 1 and R1["dom_total"] == 0 and R1["drop"] is None
    # "תקוע" נמדד על המצטבר: DOM נוכחי 14 עם 408 מצטבר עובר; 60 מצטבר לא; רישום חוזר אחד לא מספיק ל---cuts 2
    assert _not_stuck({"dom": 14, "dom_total": 408, "cuts": 2}, 90, 2) is None
    assert _not_stuck({"dom": 14, "dom_total": 60, "cuts": 2}, 90, 2).startswith("DOM")
    assert _not_stuck({"dom": 200, "dom_total": 200, "cuts": 1}, 90, 2).startswith("פחות")
    assert _not_stuck({"dom": 200, "cuts": None}, 90, 0) is None      # Redfin בלי dom_total
    # מונה המכסה: נצבר לחודש, ו-_guard עוצר לפני הבקשה שתחרוג מ-50 — אלא אם הועלה --limit
    global QUOTA_FILE
    orig, QUOTA_FILE = QUOTA_FILE, os.path.join(tempfile.mkdtemp(), "q.json")
    try:
        assert _quota() == 0 and _quota(12) == 12 and _quota(12) == 24 and _quota() == 24
        _guard(12, 50)                                   # 36 ≤ 50
        try:
            _guard(27, 50)
            assert False, "היה צריך לעצור: 51 > 50"
        except SystemExit:
            pass
        _guard(27, 100)                                  # אחרי שדרוג, עם --limit
        assert _quota() == 24                            # _guard לא רושם — רק השליחה
    finally:
        QUOTA_FILE = orig
    print("selftest: ok")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    if args and args[0] == "selftest":
        selftest()
        sys.exit()
    def opt(flag, default):
        return float(args[args.index(flag) + 1]) if flag in args else default
    dom_min, cuts_min = opt("--dom", 0), opt("--cuts", 0)
    min_arv, max_offer = opt("--min-arv", 200_000), opt("--max-offer", 350_000)
    comps_for = args[args.index("--comps") + 1] if "--comps" in args else None
    if "--rentcast" in args:
        listings, skipped = read_rentcast(fresh="--fresh" in args, limit=int(opt("--limit", RENTCAST_FREE)))
    else:
        paths = [a for a in args if a.lower().endswith(".csv")]
        if not paths:
            copied, paths = collect_downloads()
            print(f"  {copied} קבצים חדשים מ-Downloads · {len(paths)} קבצים ב-data/redfin/", file=sys.stderr)
        if not paths:
            sys.exit("אין קבצי redfin_*.csv — להוריד מ-Redfin (\"Download All\" בתחתית החיפוש) ל-Downloads ולהריץ שוב.\n"
                     "שימוש: python3 listings.py [קבצים.csv] [--dom 30] [--comps \"כתובת\"] [--all]  |  --rentcast")
        listings, skipped = read_redfin(paths)
    rows, more, today = underwrite(listings, dom_min, comps_for, min_arv, max_offer, cuts_min)
    print_table(rows, skipped + more, today, "--all" in args)
