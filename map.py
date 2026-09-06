#!/usr/bin/env python3
"""
בונה מפה אינטראקטיבית לאיתור עסקאות — קובץ HTML אחד, נפתח בדפדפן.

    python3 map.py            # יוצר deals_map.html

שכבת הבסיס היא OpenStreetMap, ולכן הכבישים, אזורי התעשייה, המסחר והפארקים
כבר מצוירים — אין טעם לצייר אותם מחדש. מעליהם מונחות רק השכבות שאין בשום
מפה: גבולות ה-BUY BOX, עסקאות המכר בפועל, והפליפים שזוהו.
"""
import json, os, sys, zipfile, io, statistics as st
from collections import defaultdict, Counter
from datetime import date
from urllib.request import urlopen, Request

import sdf
import analyze

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deals_map.html")
ZIP_GEO_URL = ("https://raw.githubusercontent.com/OpenDataDE/State-zip-code-GeoJSON"
               "/master/in_indiana_zip_codes_geo.min.json")
ZIP_GEO_CACHE = os.path.join(sdf.DATA, "in_zips.json")

# הזיפים מטבלת המחקר — 8 ב-BUY BOX, השאר לרקע ולהשוואה
WATCH = ["46250", "46220", "46234", "46227", "46205", "46256", "46260", "46203",
         "46221", "46254", "46235", "46231", "46239", "46201", "46225", "46208",
         "46240", "46259", "46216"]
AVOID = ["46218", "46226", "46241", "46222", "46204", "46202"]

# עוגני אוריינטציה מתוך docs/מחקר_גאוגרפי_אינדיאנפוליס.md
# ⚠️ מיקומים מקורבים. שכבת ה-OSM מתחת היא המקור המדויק.
ANCHORS = [
    ("job", "Allison Transmission", 39.7790, -86.2440, "~2,500 עובדים מקומיים · עוגן 46224"),
    ("job", "Rolls-Royce", 39.7440, -86.2100, "3,500+ עובדים · השקעת $1B הושלמה 8.2026"),
    ("job", "FedEx National Hub", 39.7050, -86.2860, "המרכז השני בגודלו בעולם · ~4,000 עובדים"),
    ("job", "Eli Lilly — קמפוס מרכז", 39.7530, -86.1620, "עוגן פארמה · צווארון לבן"),
    ("job", "Elanco HQ", 39.7790, -86.1780, "מטה חדש · 16 Tech"),
    ("job", "Amtrak Beech Grove Shops", 39.7220, -86.0930, "~570 עובדים · ⚠️ מעסיק פדרלי יחיד"),
    ("job", "Park 100", 39.8720, -86.2450, "2.2% תפוסה פנויה — ההדוק במטרו"),
    ("job", "Mt. Comfort — לוגיסטיקה", 39.7900, -85.9200, "מסדרון I-70 מזרח · עוגן 46229"),
    ("retail", "Fashion Mall at Keystone", 39.9090, -86.1180, "קמעונאות בריאה · תומך 46236/46228"),
    ("retail", "Castleton Square Mall", 39.9070, -86.0640, "צומת צפון-מזרח · משרת 46236"),
    ("retail", "Greenwood Park Mall", 39.6200, -86.1140, "עוגן הדרום · תומך 46217/46237/46107"),
    ("retail", "Speedway Main Street", 39.7930, -86.2480, "התחדשות מוצלחת"),
    ("retail", "Broad Ripple Village", 39.8700, -86.1400, "בילויים · צפון"),
    ("dead", "Washington Square Mall", 39.7790, -86.0370, "🔴 גוסס — 10 חנויות מ-80. פוגע ב-46229"),
    ("dead", "Lafayette Square Mall", 39.8520, -86.2190, "🔴 נסגר 2022, כמעט ריק"),
    ("dead", "Circle Centre Mall", 39.7655, -86.1590, "🔴 נסגר 31.12.2025 · ייפתח כ-Traction Yards 2029-30"),
    ("land", "Indianapolis Motor Speedway", 39.7950, -86.2350, "מורשת · עוגן זהות 46224"),
    ("land", "Monument Circle", 39.7684, -86.1581, "נקודת האפס של רשת הכתובות"),
    ("land", "IND Airport", 39.7173, -86.2944, "10.6M נוסעים 2025 — שיא"),
    ("land", "Geist Reservoir", 39.9200, -85.9700, "פרמיית חזית אגם ב-46236"),
]

# ── סניפי Momentum Title (לשעבר Hocker Title, נרכשה 22.4.2025) ──────────
# Hocker היא חברת הטייטל של גם Brooks (המשפץ) וגם Simple Quarters (הסיטונאי)
# ברישומי המכר — ולכן זו נקודת הכניסה. כתובות מ-momentumclosings.com, 6.9.2026.
BRANCHES = [
    ("Indianapolis Main", "6626 E 75th St, Floor 4, Indianapolis, IN 46250", 39.89075, -86.05247),
    ("Indianapolis East", "6767 E Washington St, Indianapolis, IN 46219", 39.77178, -86.05049),
    ("Indianapolis West", "2629 Waterfront Pkwy E Dr #110, Indianapolis, IN 46214", 39.80352, -86.27918),
    ("Indianapolis South", "3209 W Smith Valley Rd, Greenwood, IN 46142", 39.60575, -86.16356),
    ("Indianapolis Downtown", "516 Lincoln St, Indianapolis, IN 46203", 39.74565, -86.14921),
    ("Carmel", "10333 N Meridian St Ste 101, Carmel, IN 46290", 39.9155, -86.1568),  # ⚠️ מקורב — הגיאוקודר לא מצא
]

# ── מחוזות: מריון מול "הדונאט" ──────────────────────────────────────────
# מ-CLAUDE.md § 5: כל מחוז מס במריון חורג מ-2% ⇒ ניכוי SEA 1 שווה שם $0.
# בדונאט 1.40%-1.99% ⇒ פער החזקה של $285-$515 לעסקה, ומתרחב.
COUNTY_URL = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
              "State_County/MapServer/13/query")
COUNTY_CACHE = os.path.join(sdf.DATA, "in_counties.json")
COUNTIES = {
    "Marion":  ("🎯 שוק היעד", ">2.00%", "כל מחוז מס חורג מ-2% ⇒ ניכוי SEA 1 = $0"),
    "Hamilton": ("דונאט", "1.40-1.99%", "חוצה את 46236 Geist מצפון ל-96th · HSE schools"),
    "Hancock":  ("דונאט", "1.40-1.99%", "חוצה את 46229 Cumberland · Mt. Vernon schools"),
    "Johnson":  ("דונאט", "1.40-1.99%", "Greenwood · דרומית ל-46217/46237"),
    "Hendricks": ("דונאט", "1.40-1.99%", "Plainfield/Avon · לוגיסטיקה"),
    "Boone":    ("דונאט", "1.40-1.99%", "LEAP/Lilly $18B · הצומח ביותר"),
    "Morgan":   ("דונאט", "1.40-1.99%", "כפרי-פרברי דרום"),
    "Shelby":   ("דונאט", "1.40-1.99%", "דרום-מזרח"),
    "Madison":  ("דונאט", "1.40-1.99%", "Anderson · צפון-מזרח"),
}


def county_polygons():
    """גבולות המחוזות במטרו. TIGERweb של לשכת המפקד — חינם, בלי מפתח."""
    if os.path.exists(COUNTY_CACHE):
        with open(COUNTY_CACHE, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        q = ("?where=STATE%3D%2718%27&outFields=NAME&returnGeometry=true"
             "&outSR=4326&f=geojson")
        req = Request(COUNTY_URL + q, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=180) as r:
            raw = json.loads(r.read().decode("utf-8", "replace"))
        os.makedirs(sdf.DATA, exist_ok=True)
        with open(COUNTY_CACHE, "w", encoding="utf-8") as f:
            json.dump(raw, f)
    out = []
    for feat in raw.get("features", []):
        name = (feat.get("properties", {}).get("NAME") or "").replace(" County", "")
        if name not in COUNTIES:
            continue
        tier, tax, note = COUNTIES[name]
        out.append({"name": name, "tier": tier, "tax": tax, "note": note,
                    "geometry": _thin(feat["geometry"], 4)})
    return out


# מסדרון ה-Blue Line על Washington St — שיבוש בנייה עד 2028
BLUE_LINE = [[39.7690, -86.2600], [39.7685, -86.2000], [39.7680, -86.1580],
             [39.7700, -86.1000], [39.7720, -86.0500], [39.7740, -86.0100]]

RULE_70 = 0.70


def zip_polygons(wanted):
    if not os.path.exists(ZIP_GEO_CACHE):
        print("  מוריד גבולות זיפים...", file=sys.stderr)
        req = Request(ZIP_GEO_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=300) as r, open(ZIP_GEO_CACHE, "wb") as f:
            f.write(r.read())
    with open(ZIP_GEO_CACHE, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for feat in data["features"]:
        z = feat["properties"].get("ZCTA5CE10")
        if z in wanted:
            out.append({"zip": z, "geometry": _thin(feat["geometry"])})
    return out


def _thin(geom, places=5):
    """מעגל קואורדינטות ל-5 ספרות (~1 מטר). חוסך ~40% מגודל הקובץ."""
    def walk(x):
        if isinstance(x, list):
            if x and isinstance(x[0], (int, float)):
                return [round(v, places) for v in x]
            return [walk(i) for i in x]
        return x
    return {"type": geom["type"], "coordinates": walk(geom["coordinates"])}


def zip_stats(sales, flips):
    fz = defaultdict(list)
    for f in flips:
        fz[f["zip"]].append(f)
    out = {}
    for z in sdf.BUY_BOX + WATCH + AVOID:
        rows = [s for s in sales if s["zip"] == z]
        if not rows:
            continue
        dom = [r["dom"] for r in rows if 0 < r["dom"] < 800]
        f = fz.get(z, [])
        out[z] = {
            "n": len(rows),
            "median": round(st.median([r["price"] for r in rows])),
            "dom": round(st.median(dom)) if dom else None,
            "owner": round(sum(r["owner_occ"] for r in rows) / len(rows) * 100),
            "distress": round(sum(r["distress"] for r in rows) / len(rows) * 100, 1),
            "flips": len(f),
            "mult": round(st.median([x["mult"] for x in f]), 2) if f else None,
            "spread": round(st.median([x["sell"] - x["buy"] for x in f])) if f else None,
            "tier": ("BUY" if z in sdf.BUY_BOX else "AVOID" if z in AVOID else "WATCH"),
        }
    return out


def title_companies(years=sdf.YEARS):
    """מי סוגר את העסקאות בזיפי ה-BUY BOX. Contact_Type='P' הוא מכין הטופס."""
    counts = Counter()
    for year in years:
        with zipfile.ZipFile(sdf._fetch(year)) as zf:
            names = {n.upper(): n for n in zf.namelist()}
            keep = set()
            parcels = defaultdict(list)
            for p in sdf._rows(zf, names["SALEPARCEL.TXT"]):
                if (p.get("A5_ZipCode") or "").strip()[:5] in sdf.BUY_BOX:
                    parcels[(p.get("SDF_ID") or "").strip()].append(p)
            for s in sdf._rows(zf, names["SALEDISC.TXT"]):
                sid = (s.get("SDF_ID") or "").strip()
                if sid in parcels and (s.get("County_ID") or "").strip() == "49":
                    keep.add(sid)
            for c in sdf._rows(zf, names["SALECONTAC.TXT"]):
                if (c.get("Contact_Type") or "").strip() == "P" \
                        and (c.get("SDF_ID") or "").strip() in keep:
                    name = (c.get("Company") or "").strip()
                    if name:
                        counts[name] += 1
    return counts.most_common(15)


def active_buyers(sales, flips):
    """
    מי קונה כאן שוב ושוב. אלה המתחרים — ובחלקם גם מקורות עסקה.
    שמות הקונים מגיעים מ-SALECONTAC (Contact_Type='B').
    """
    flip_ids = {(f["parcel"], f["buy_date"]) for f in flips}
    counts, flip_counts = Counter(), Counter()
    for year in sdf.YEARS:
        with zipfile.ZipFile(sdf._fetch(year)) as zf:
            names = {n.upper(): n for n in zf.namelist()}
            parcels = {}
            for p in sdf._rows(zf, names["SALEPARCEL.TXT"]):
                if (p.get("A5_ZipCode") or "").strip()[:5] in sdf.BUY_BOX:
                    parcels[(p.get("SDF_ID") or "").strip()] = \
                        (p.get("A1_Parcel_Number") or "").strip()
            dates = {}
            for s in sdf._rows(zf, names["SALEDISC.TXT"]):
                sid = (s.get("SDF_ID") or "").strip()
                if sid in parcels and (s.get("County_ID") or "").strip() == "49":
                    dates[sid] = (s.get("C7_Conveyance_Date") or "").strip()[:10]
            for c in sdf._rows(zf, names["SALECONTAC.TXT"]):
                sid = (c.get("SDF_ID") or "").strip()
                if (c.get("Contact_Type") or "").strip() != "B" or sid not in dates:
                    continue
                name = (c.get("Name") or "").strip()
                if not name or len(name) < 4:
                    continue
                counts[name] += 1
                if (parcels[sid], dates[sid]) in flip_ids:
                    flip_counts[name] += 1
    return [(n, c, flip_counts.get(n, 0)) for n, c in counts.most_common(40) if c >= 4][:20]


# צבע לכל מפעיל בשכבת המלאי החי
RIVAL_COLOR = {
    "SIMPLE QUARTERS": "#f472b6", "BROOKS HOLDINGS": "#facc15",
    "GRISE HOME": "#fb923c", "POWER HOUSE HOLDINGS": "#22d3ee",
    "AMERICAN INTERNATIONAL HOME": "#a3a3a3", "OWNEZ HOLDINGS": "#c084fc",
}


def rival_inventory(sales):
    """
    מה שכל מתחרה מחזיק *עכשיו* לפי רשומות השומה — הצינור הפעיל שלו.
    זו הטיית שורדים הפוכה: מה שנמכר מהר איננו כאן; מה שנשאר הוא מה שנתקע.
    """
    inv = analyze.live_inventory()
    last = {}
    for s in sales:
        k = s["parcel"]
        if k and (k not in last or s["date"] > last[k][0]):
            last[k] = (s["date"], s["price"])
    today = max((s["date"] for s in sales), default=date.today().isoformat())

    out = []
    for rival, feats in inv.items():
        for f in feats:
            ll = analyze._centroid(f.get("geometry"))
            if not ll:
                continue
            p = f.get("properties", {})
            pc = analyze._parcel(p.get("STATEPARCELNUMBER"))
            d, price = last.get(pc, (None, 0))
            out.append({
                "r": rival, "lat": ll[0], "lon": ll[1],
                "z": (p.get("ZIPCODE") or "")[:5],
                "a": f"{p.get('STNUMBER') or ''} {p.get('FULL_STNAME') or ''}".strip()[:40],
                "av": analyze._i(p.get("ASSESSORYEAR_TOTALAV")),
                "d": (d or "")[:7], "p": round(price),
                "mo": round(sdf._months(d, today), 1) if d else None,
                "land": 1 if "VACANT" in (p.get("PROPERTY_SUB_CLASS_DESCRIPTION") or "").upper() else 0,
            })
    return out


def build():
    print("טוען נתונים...", file=sys.stderr)
    # כל 33 הזיפים מטבלת המחקר — לסטטיסטיקה ולגבולות, כדי שיהיה מול מה להשוות
    every = sdf.load(zips=sdf.BUY_BOX + WATCH + AVOID)
    stats = zip_stats(every, sdf.find_flips(every))
    polys = zip_polygons(set(stats))
    # נקודות על המפה רק ל-BUY BOX — אין טעם לגאוקד זיפים שלא קונים בהם
    sales = [s for s in every if s["zip"] in sdf.BUY_BOX]
    flips = sdf.find_flips(sales)
    geo = sdf.geocode(sales)

    pts, seen = [], set()
    for f in flips:
        ll = geo.get(sdf._geo_key(f))
        if not ll:
            continue
        key = (f["parcel"], f["date"])
        if key in seen:
            continue
        seen.add(key)
        pts.append({
            "lat": round(ll[0], 6), "lon": round(ll[1], 6), "z": f["zip"],
            "a": f["address"][:44], "b": round(f["buy"]), "s": round(f["sell"]),
            "m": round(f["months"], 1), "x": round(f["mult"], 2),
            "d": f["date"][:7], "bd": f["buy_date"][:7],
            "r": round(RULE_70 * f["sell"] - f["buy"]),   # תקציב שכלל ה-70% מתיר
            "ds": 1 if f["buy_distress"] else 0,
            "oo": 1 if f["owner_occ"] else 0,
        })

    bg = []
    for s in sales:
        ll = geo.get(sdf._geo_key(s))
        if ll:
            bg.append([round(ll[0], 5), round(ll[1], 5), round(s["price"] / 1000),
                       int(s["date"][:4]), 1 if s["distress"] else 0])

    print("  גבולות מחוזות...", file=sys.stderr)
    counties = county_polygons()
    print("  מלאי חי של המתחרים...", file=sys.stderr)
    inventory = rival_inventory(every)
    print("  חברות טייטל...", file=sys.stderr)
    titles = title_companies()
    print("  קונים חוזרים...", file=sys.stderr)
    buyers = active_buyers(sales, flips)

    payload = {
        "pts": pts, "bg": bg, "stats": stats, "polys": polys,
        "anchors": ANCHORS, "blueline": BLUE_LINE, "titles": titles,
        "counties": counties, "branches": BRANCHES, "inv": inventory,
        "buyers": buyers, "buybox": sdf.BUY_BOX,
        "built": date.today().isoformat(),
        "years": [sdf.YEARS[0], sdf.YEARS[-1]],
        "geo_rate": round(len(pts) / max(len(flips), 1) * 100),
    }
    html = TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    mb = os.path.getsize(OUT) / 1e6
    print(f"\n✅ {OUT}  ({mb:.1f}MB)")
    print(f"   {len(pts):,} פליפים ממופים מתוך {len(flips):,} ({payload['geo_rate']}% גיאוקוד)")
    print(f"   {len(bg):,} מכירות רקע · {len(polys)} גבולות זיפים "
          f"· {len(counties)} מחוזות · {len(BRANCHES)} סניפי טייטל "
          f"· {len(inventory)} חלקות במלאי המתחרים")
    print(f"\n   פתיחה:  open {OUT}")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>דרך חדשה — מפת עסקאות אינדיאנפוליס</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{--bg:#0f1216;--panel:#171b21;--line:#262d36;--txt:#e6e9ee;--dim:#8b97a8;
      --buy:#22c55e;--watch:#eab308;--avoid:#ef4444;--acc:#38bdf8}
*{box-sizing:border-box}
body{margin:0;font:13px/1.5 -apple-system,"Segoe UI",Arial;background:var(--bg);color:var(--txt);overflow:hidden}
#map{position:absolute;inset:0 380px 0 0;background:#0f1216}
.leaflet-tile-pane{filter:invert(1) hue-rotate(180deg) brightness(.72) contrast(1.15) saturate(.55)}
.leaflet-container{background:#0f1216}
.leaflet-control-attribution{background:rgba(15,18,22,.8)!important;color:#5b6675!important;font-size:9px}
.leaflet-control-attribution a{color:#7a8798!important}
#side{position:absolute;top:0;right:0;bottom:0;width:380px;background:var(--panel);
      border-left:1px solid var(--line);overflow-y:auto;padding:14px}
h1{font-size:15px;margin:0 0 2px}
.sub{color:var(--dim);font-size:11px;margin-bottom:12px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--dim);
   margin:18px 0 7px;border-top:1px solid var(--line);padding-top:12px}
h2:first-of-type{border:0;margin-top:8px}
label{display:block;margin:9px 0 3px;font-size:11px;color:var(--dim)}
input[type=range]{width:100%;accent-color:var(--acc)}
select,button{width:100%;background:#0d1014;color:var(--txt);border:1px solid var(--line);
  border-radius:6px;padding:6px;font:inherit}
button{cursor:pointer}button:hover{border-color:var(--acc)}
.row{display:flex;gap:6px}.row>*{flex:1}
.chk{display:flex;align-items:center;gap:6px;margin:5px 0;font-size:12px;color:var(--txt)}
.chk input{accent-color:var(--acc)}
table{width:100%;border-collapse:collapse;font-size:11px}
th,td{padding:4px 3px;text-align:right;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500;font-size:10px}
tr.buy td:first-child{border-right:3px solid var(--buy);padding-right:5px}
tr.watch td:first-child{border-right:3px solid var(--watch);padding-right:5px}
tr.avoid td:first-child{border-right:3px solid var(--avoid);padding-right:5px}
tr:hover{background:#1e242c;cursor:pointer}
.stat{display:flex;justify-content:space-between;padding:3px 0;font-size:12px}
.stat b{color:var(--acc);font-variant-numeric:tabular-nums}
.leaflet-popup-content-wrapper{background:#11151a;color:var(--txt);border-radius:8px}
.leaflet-popup-tip{background:#11151a}
.leaflet-popup-content{margin:11px 13px;direction:rtl;font-size:12px}
.pk{color:var(--dim)} .pv{font-weight:600;font-variant-numeric:tabular-nums}
/* תוויות מחוזות — קבועות על המפה, לא בריחוף */
.colbl{background:none!important;border:none!important;box-shadow:none!important;
  padding:0!important;white-space:nowrap;font-weight:800;letter-spacing:.5px;
  text-transform:uppercase;pointer-events:none}
.colbl::before{display:none!important}
.colbl .n{font-size:15px;text-shadow:0 0 4px #000,0 0 9px #000,0 2px 3px #000}
.colbl .t{font-size:10.5px;font-weight:700;letter-spacing:0;opacity:.95;
  text-transform:none;text-shadow:0 0 4px #000,0 0 8px #000}
.colbl.marion .n{font-size:19px;color:#fbbf24}
.colbl.marion .t{color:#fbbf24}
.colbl.donut .n{color:#cbd5e1} .colbl.donut .t{color:#94a3b8}
.pop-row{display:flex;justify-content:space-between;gap:14px;padding:1px 0}
.legend{position:absolute;bottom:14px;right:394px;background:rgba(15,18,22,.93);
  border:1px solid var(--line);border-radius:8px;padding:9px 11px;font-size:11px;z-index:600}
.legend i{width:11px;height:11px;border-radius:50%;display:inline-block;margin-left:6px}
.note{font-size:10px;color:var(--dim);line-height:1.5;margin-top:5px}
.warn{color:#fbbf24}
.mini{font-size:10px;color:var(--dim);text-align:left;font-variant-numeric:tabular-nums}
</style></head><body>
<div id="map"></div>
<div class="legend" id="legend"></div>
<div id="side">
  <h1>מפת עסקאות — אינדיאנפוליס</h1>
  <div class="sub" id="hdr"></div>

  <h2>סינון</h2>
  <label>זיפ</label>
  <select id="fz"><option value="">כל הזיפים</option>
    <optgroup label="BUY BOX" id="gbuy"></optgroup>
    <optgroup label="WATCH" id="gwatch"></optgroup>
    <optgroup label="AVOID" id="gavoid"></optgroup></select>

  <label>מחיר מכירה עד <b id="lmax"></b></label>
  <input type="range" id="fmax" min="80" max="600" step="10" value="600">
  <label>מרווח גולמי מינימלי <b id="lspr"></b></label>
  <input type="range" id="fspr" min="0" max="150" step="5" value="0">
  <label>תקציב שכלל ה-70% מתיר, מינימום <b id="lroom"></b></label>
  <input type="range" id="froom" min="-50" max="150" step="5" value="-50">
  <label>חודשי החזקה עד <b id="lmon"></b></label>
  <input type="range" id="fmon" min="1" max="18" step="1" value="18">
  <label>משנה <b id="lyr"></b></label>
  <input type="range" id="fyr" min="2023" max="2026" step="1" value="2023">

  <div class="chk"><input type="checkbox" id="fds"><label for="fds" style="margin:0">רק רכישות במצוקה</label></div>
  <div class="chk"><input type="checkbox" id="foo"><label for="foo" style="margin:0">רק יציאה לקונה תופס</label></div>
  <div class="chk"><input type="checkbox" id="fbg"><label for="fbg" style="margin:0">הצג את כל המכירות ברקע</label></div>
  <div class="chk"><input type="checkbox" id="fan" checked><label for="fan" style="margin:0">עוגני תעסוקה ומסחר</label></div>
  <div class="chk"><input type="checkbox" id="fco"><label for="fco" style="margin:0">גבולות מחוזות (מס רכוש)</label></div>
  <div class="chk"><input type="checkbox" id="fbr" checked><label for="fbr" style="margin:0">סניפי Momentum Title</label></div>
  <div class="chk"><input type="checkbox" id="finv"><label for="finv" style="margin:0">🔑 המלאי החי של המתחרים</label></div>
  <button onclick="reset()" style="margin-top:9px">אפס סינון</button>

  <h2>מה מוצג</h2>
  <div id="live"></div>

  <h2>זיפים</h2>
  <table><thead><tr><th>ZIP</th><th>מדיאן</th><th>DOM</th><th>תופס</th><th>פליפים</th><th>מכפיל</th></tr></thead>
  <tbody id="tbl"></tbody></table>
  <div class="note">לחיצה על שורה מזזה את המפה לזיפ.</div>

  <h2>חברות טייטל — לפי נפח ב-BUY BOX</h2>
  <table><tbody id="titles"></tbody></table>
  <div class="note">מתוך <code>Contact_Type='P'</code> — מכין טופס הגילוי, שהוא חברת
  הטייטל בכל עסקה. זו רשימת הסוגרים הפעילים בזיפים שלך.</div>

  <h2>קונים חוזרים — התחרות</h2>
  <table><thead><tr><th>שם</th><th>רכישות</th><th>מהן פליפים</th></tr></thead><tbody id="buyers"></tbody></table>
  <div class="note warn">⚠️ אין שמות סוכני נדל"ן בנתונים הממשלתיים. אלה שמות
  <b>הקונים בפועל</b> מתוך רישומי המכר.</div>

  <h2>מקורות ומגבלות</h2>
  <div class="note">
  נתוני מכר: <b>Indiana Sales Disclosure Form</b> (IC 6-1.1-5.5) — מחירים שנרשמו
  ברשות, לא הערכות. מתעדכן שבועית.<br><br>
  מיקומים: גיאוקודר לשכת המפקד.<br><br>
  <span class="warn">⚠️ עוגני התעסוקה והמסחר ממוקמים בקירוב, לאוריינטציה בלבד.
  שכבת המפה מתחת היא המקור המדויק.</span><br><br>
  <span class="warn">⚠️ "פליפ" = אותו parcel נמכר פעמיים תוך 18 ח' עם עלייה מעל 10%.
  חלקם עשויים להיות העברות בין ישויות ולא שיפוצים.</span><br><br>
  <span class="warn">⚠️ אין שטח בנוי בנתונים ⇒ אין $/sqft. "תקציב שכלל ה-70% מתיר"
  הוא <code>0.70 × מחיר מכירה − מחיר קנייה</code>: כמה נשאר לשיפוץ ולרווח אם
  מתייחסים למכירה בפועל כ-ARV.</span>
  </div>
</div>
<script>
const D = __DATA__;
const map = L.map('map',{preferCanvas:true}).setView([39.79,-86.15],11);
/* OSM סטנדרטי — חופשי ובלי מפתח. הכהות מגיעה מפילטר CSS על אריחי הבסיס בלבד,
   כדי שהשכבות שמעליהם ישמרו על הצבע האמיתי שלהן. */
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:19, attribution:'&copy; OpenStreetMap contributors'}).addTo(map);

const $ = id => document.getElementById(id);
const usd = n => '$' + Math.round(n).toLocaleString('en-US');
const TIER = {BUY:'#22c55e', WATCH:'#eab308', AVOID:'#ef4444'};

$('hdr').textContent = `רישומי מכר ${D.years[0]}–${D.years[1]} · נבנה ${D.built} · `
  + `${D.pts.length.toLocaleString()} פליפים ממופים`;

/* --- גבולות זיפים --- */
const zLayer = L.geoJSON(
  {type:'FeatureCollection', features:D.polys.map(p=>({type:'Feature',
     properties:{zip:p.zip}, geometry:p.geometry}))},
  {style:f=>{const t=(D.stats[f.properties.zip]||{}).tier||'WATCH';
     const buy = t==='BUY';
     return {color:TIER[t], weight:buy?2.5:1, opacity:buy?.95:.35,
             fillColor:TIER[t], fillOpacity:buy?.10:.03};},
   onEachFeature:(f,l)=>{const z=f.properties.zip,s=D.stats[z]||{};
     l.bindTooltip(`<b>${z}</b> · ${s.tier||''}<br>מדיאן ${usd(s.median||0)} · DOM ${s.dom??'—'}
       <br>תופס ${s.owner??'—'}% · ${s.flips||0} פליפים`,{sticky:true});
     l.on('click',()=>{$('fz').value=z; draw();});}
  }).addTo(map);

/* --- מחוזות: מריון מול הדונאט --- */
const coLayer = L.geoJSON(
  {type:'FeatureCollection', features:D.counties.map(c=>({type:'Feature',
     properties:c, geometry:c.geometry}))},
  {style:f=>{const m = f.properties.name==='Marion';
     return {color:m?'#f59e0b':'#94a3b8', weight:m?5:2.5, opacity:m?1:.75,
             fill:true, fillColor:m?'#f59e0b':'#64748b', fillOpacity:m?.05:.02,
             dashArray:m?null:'10,7'};},
   onEachFeature:(f,l)=>{const p=f.properties, m=p.name==='Marion';
     l.bindTooltip(
       `<div class="colbl ${m?'marion':'donut'}">
          <div class="n">${m?'◆ ':''}${p.name}</div>
          <div class="t">מס רכוש ${p.tax}</div>
        </div>`,
       {permanent:true, direction:'center', className:'colbl', opacity:1});
     l.bindPopup(`<b>${p.name} County</b> · ${p.tier}
       <br>תקרת מס רכוש <b>${p.tax}</b><br><span class="pk">${p.note}</span>`);}
  });

/* --- סניפי Momentum Title (לשעבר Hocker) --- */
const brLayer = L.layerGroup(D.branches.map(([name,addr,lat,lon])=>
  L.marker([lat,lon],{icon:L.divIcon({className:'',iconSize:[22,22],
    html:`<div style="background:#22c55e;color:#0b0e12;border-radius:4px;width:22px;
      height:22px;display:flex;align-items:center;justify-content:center;
      font-size:12px;font-weight:700;box-shadow:0 0 0 2px rgba(0,0,0,.6)">T</div>`})})
   .bindPopup(`<b>${name}</b><br><span class="pk">${addr}</span>
     <br><span class="pk" style="font-size:10px">Momentum Title — לשעבר Hocker Title,
     חברת הטייטל של Brooks ו-Simple Quarters</span>`)
)).addTo(map);

/* --- המלאי החי של המתחרים — מה שהם מחזיקים עכשיו --- */
const RC = {"SIMPLE QUARTERS":"#f472b6","BROOKS HOLDINGS":"#facc15",
  "GRISE HOME":"#fb923c","POWER HOUSE HOLDINGS":"#22d3ee",
  "AMERICAN INTERNATIONAL HOME":"#a3a3a3","OWNEZ HOLDINGS":"#c084fc"};
const BB = new Set(D.buybox);
const invLayer = L.layerGroup(D.inv.map(p=>{
  const inBox = BB.has(p.z), stuck = p.mo && p.mo>12 && p.r!=='AMERICAN INTERNATIONAL HOME';
  return L.circleMarker([p.lat,p.lon],{
    radius: inBox?6:3.5, weight: stuck?2.5:1,
    color: stuck?'#ef4444':'#0b0e12', fillColor: RC[p.r]||'#94a3b8',
    fillOpacity: inBox?.92:.4})
   .bindPopup(`<b>${p.a||'—'}</b> · ${p.z}
     <br><span class="pk">${p.r}${p.land?' · ⬜ קרקע ריקה':''}</span>
     <br>שווי שומה <span class="pv">${usd(p.av)}</span>
     ${p.d?`<br>נקנה ${p.d}${p.p?` ב-${usd(p.p)}`:''} · <b>מוחזק ${p.mo} ח'</b>`
          :'<br><span class="pk">נקנה לפני 2023 — מחוץ למדגם</span>'}
     ${stuck?'<br><b style="color:#ef4444">🔴 מוחזק מעל שנה</b>':''}`);
})); 

/* --- עוגנים --- */
const ICO = {job:['#38bdf8','🏭'], retail:['#a78bfa','🛍'], dead:['#ef4444','✖'], land:['#94a3b8','◆']};
const anchors = L.layerGroup(D.anchors.map(([k,name,lat,lon,note])=>{
  const [c,g] = ICO[k];
  return L.marker([lat,lon],{icon:L.divIcon({className:'',iconSize:[20,20],
    html:`<div style="background:${c};color:#0b0e12;border-radius:50%;width:20px;height:20px;
      display:flex;align-items:center;justify-content:center;font-size:11px;
      box-shadow:0 0 0 2px rgba(0,0,0,.55)">${g}</div>`})})
    .bindPopup(`<b>${name}</b><br><span class="pk">${note}</span>
      <br><span class="pk" style="font-size:10px">מיקום מקורב</span>`);
})).addTo(map);

L.polyline(D.blueline,{color:'#3b82f6',weight:4,opacity:.55,dashArray:'9,7'})
 .bindTooltip('IndyGo Blue Line — בבנייה על Washington St. שיבוש עד 2028, נגישות אחריו',{sticky:true})
 .addTo(map);

/* --- שכבות נקודות --- */
let ptLayer = L.layerGroup().addTo(map), bgLayer = L.layerGroup();

function colorFor(p){
  const s = p.s - p.b;
  return s >= 120000 ? '#22c55e' : s >= 80000 ? '#84cc16'
       : s >= 45000  ? '#eab308' : s >= 20000 ? '#f97316' : '#ef4444';
}

function draw(){
  const z=$('fz').value, mx=+$('fmax').value*1000, sp=+$('fspr').value*1000,
        rm=+$('froom').value*1000, mo=+$('fmon').value, yr=+$('fyr').value,
        ds=$('fds').checked, oo=$('foo').checked;
  $('lmax').textContent = mx>=600000?'ללא הגבלה':usd(mx);
  $('lspr').textContent = usd(sp);
  $('lroom').textContent = usd(rm);
  $('lmon').textContent = mo>=18?'ללא הגבלה':mo;
  $('lyr').textContent  = yr<=2023?'הכל':'מ-'+yr;

  const sel = D.pts.filter(p =>
    (!z || p.z===z) && p.s<=mx && (p.s-p.b)>=sp && p.r>=rm && p.m<=mo
    && +p.d.slice(0,4)>=yr && (!ds||p.ds) && (!oo||p.oo));

  ptLayer.clearLayers();
  sel.forEach(p=>{
    const spread=p.s-p.b;
    L.circleMarker([p.lat,p.lon],{radius:Math.min(11,4+spread/26000),
      color:'#0b0e12',weight:.8,fillColor:colorFor(p),fillOpacity:.88})
     .bindPopup(`<b>${p.a}</b><br><span class="pk">${p.z}</span>
       <div class="pop-row"><span class="pk">נקנה ${p.bd}</span><span class="pv">${usd(p.b)}</span></div>
       <div class="pop-row"><span class="pk">נמכר ${p.d}</span><span class="pv">${usd(p.s)}</span></div>
       <div class="pop-row"><span class="pk">מרווח גולמי</span><span class="pv" style="color:${colorFor(p)}">${usd(spread)}</span></div>
       <div class="pop-row"><span class="pk">מכפיל</span><span class="pv">${p.x}×</span></div>
       <div class="pop-row"><span class="pk">החזקה</span><span class="pv">${p.m} ח'</span></div>
       <div class="pop-row"><span class="pk">כלל ה-70% מתיר</span><span class="pv" style="color:${p.r>=52000?'#22c55e':p.r>0?'#eab308':'#ef4444'}">${usd(p.r)}</span></div>
       ${p.ds?'<div class="pk">🔨 נרכש בעסקת מצוקה</div>':''}
       ${p.oo?'<div class="pk">🏠 נמכר לקונה תופס</div>':'<div class="pk">💼 נמכר למשקיע</div>'}`)
     .addTo(ptLayer);
  });

  const spreads = sel.map(p=>p.s-p.b).sort((a,b)=>a-b);
  const med = spreads.length?spreads[Math.floor(spreads.length/2)]:0;
  const pass = sel.filter(p=>p.r>=52000).length;
  $('live').innerHTML =
    `<div class="stat"><span>פליפים מוצגים</span><b>${sel.length.toLocaleString()}</b></div>
     <div class="stat"><span>מרווח גולמי חציוני</span><b>${usd(med)}</b></div>
     <div class="stat"><span>חציון החזקה</span><b>${sel.length?(sel.reduce((a,p)=>a+p.m,0)/sel.length).toFixed(1):0} ח'</b></div>
     <div class="stat"><span>עברו את כלל ה-70% בתקציב $52K</span><b>${pass} (${sel.length?Math.round(pass/sel.length*100):0}%)</b></div>`;

  if($('fbg').checked){
    if(!map.hasLayer(bgLayer)){
      bgLayer.clearLayers();
      D.bg.filter(b=>b[3]>=yr).forEach(b=>L.circleMarker([b[0],b[1]],
        {radius:1.6,stroke:false,fillColor:b[4]?'#f43f5e':'#475569',fillOpacity:.5}).addTo(bgLayer));
      map.addLayer(bgLayer);
    }
  } else map.removeLayer(bgLayer);
  [[anchors,'fan'],[coLayer,'fco'],[brLayer,'fbr'],[invLayer,'finv']].forEach(([L_,id])=>{
    const on = $(id).checked;
    if (on !== map.hasLayer(L_)) on ? map.addLayer(L_) : map.removeLayer(L_);
  });
}

function reset(){
  $('fz').value=''; $('fmax').value=600; $('fspr').value=0; $('froom').value=-50;
  $('fmon').value=18; $('fyr').value=2023;
  ['fds','foo','fbg','fco','finv'].forEach(i=>$(i).checked=false);
  ['fan','fbr'].forEach(i=>$(i).checked=true);
  map.setView([39.79,-86.15],11); draw();
}
['fz','fmax','fspr','froom','fmon','fyr','fds','foo','fbg','fan','fco','fbr','finv']
  .forEach(i=>$(i).addEventListener('input',draw));

/* --- טבלאות --- */
const order = Object.entries(D.stats).sort((a,b)=>
  ({BUY:0,WATCH:1,AVOID:2})[a[1].tier]-({BUY:0,WATCH:1,AVOID:2})[b[1].tier]
  || (b[1].mult||0)-(a[1].mult||0));
$('tbl').innerHTML = order.map(([z,s])=>
  `<tr class="${s.tier.toLowerCase()}" onclick="$('fz').value='${z}';draw();zoomTo('${z}')">
    <td>${z}</td><td>${usd(s.median)}</td><td>${s.dom??'—'}</td>
    <td>${s.owner}%</td><td>${s.flips}</td><td>${s.mult??'—'}</td></tr>`).join('');
order.forEach(([z,s])=>{
  const o=document.createElement('option'); o.value=z; o.textContent=`${z} — ${usd(s.median)}`;
  $({BUY:'gbuy',WATCH:'gwatch',AVOID:'gavoid'}[s.tier]).appendChild(o);
});
window.zoomTo = z => { zLayer.eachLayer(l=>{ if(l.feature.properties.zip===z)
  map.fitBounds(l.getBounds(),{paddingBottomRight:[380,0]}); }); };

$('titles').innerHTML = D.titles.map(([n,c])=>
  `<tr><td style="text-align:right">${n}</td><td class="mini">${c}</td></tr>`).join('');
$('buyers').innerHTML = D.buyers.length ? D.buyers.map(([n,c,f])=>
  `<tr><td style="text-align:right;font-size:10px">${n}</td>
   <td class="mini">${c}</td><td class="mini">${f||'—'}</td></tr>`).join('')
  : '<tr><td class="note">לא נמצאו קונים חוזרים מעל הסף</td></tr>';

$('legend').innerHTML =
  `<div style="margin-bottom:5px;color:#8b97a8">מרווח גולמי</div>
   <div><i style="background:#22c55e"></i>$120K+</div>
   <div><i style="background:#84cc16"></i>$80–120K</div>
   <div><i style="background:#eab308"></i>$45–80K</div>
   <div><i style="background:#f97316"></i>$20–45K</div>
   <div><i style="background:#ef4444"></i>מתחת ל-$20K</div>
   <div style="margin-top:6px;color:#8b97a8">גודל = גודל המרווח</div>`;

draw();
</script></body></html>"""


if __name__ == "__main__":
    build()
