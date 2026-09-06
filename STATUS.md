# דרך חדשה — מערכת החיתום

**Last update:** 2026-09-06
**Changed:** סשן ארוך אחד (6.9.2026): ביקורת מקצה לקצה, ואז נבנו ואומתו — `listings.py` עם
RentCast (913 מודעות פעילות ב-12 זיפי יעד), שכבה צפונית, כלל 72%/69% לפי שכבה, שגיאת ARV לפי
שכבה, יומן חיתום, מתחרים מהנתונים, סינון בנייה חדשה, 6 תיקוני אקסל; הליד הראשון נבדק ונדחה.
**איפה ממשיכים:** `CLAUDE.md` § "📍 איפה אנחנו" · `tasks/todo.md` § "▶ להמשיך מכאן".

| | |
|---|---|
| **Status** | 🟢 הכל עובד על 12 זיפי היעד. ARV לפי שכבה: BUY BOX ±12.4%, צפון ±18.7% · כלל 72%/69% לפי שכבה · `listings.py --rentcast` אומת על 913 מודעות · Redfin CSV כגיבוי. פוש ידני ע"י תומר |
| **Where** | `~/Projects/New-Way` |
| **Running** | ❌ כלום לא רץ ברקע. הכל ידני, לפי דרישה |
| **Depends on** | Python 3 (ספריית תקן בלבד). `RENTCAST_API_KEY` ב-`.env` (חינם, 50 בקשות/חודש) ל-`listings.py --rentcast`; בלעדיו — CSV של Redfin |

## פקודות

```bash
cd ~/Projects/New-Way

open deals_map.html        # 👈 המפה. נפתחת ישירות בדפדפן, בלי שרת

python3 map.py             # בונה מחדש את המפה
python3 sdf.py report      # תמונת מצב לכל זיפ ב-BUY BOX
python3 sdf.py flips       # הפליפים שנמצאו, לפי גודל הרווח
python3 analyze.py filter  # למה כלל ה-70% תופס 16%, ואיך מרחיבים
python3 sdf.py refresh     # מוריד מחדש רק את השנה הנוכחית (~25 שניות)
python3 analyze.py rivals  # מי עושה את העסק שלך (15 הגדולים, מהנתונים) + רשימת הייחוס

# 👇 חיתום נכס בודד
python3 arv.py comps "6259 Chadworth Court"                 # קומפס + טווח ARV
python3 arv.py deal "6259 Chadworth Court" --offer 241469   # טווח הרווח — ונרשם ב-underwriting_log.csv
python3 arv.py backtest    # מוכיח את הדיוק — ~18 שניות

# 👇 חיתום בכמות — מודעות פעילות מ-Redfin ("Download All" בתחתית החיפוש → Downloads)
python3 listings.py --rentcast --dom 90 --cuts 2             # 👈 השבועי: תקועות עם 2 הורדות מחיר. 12 בקשות מתוך 50 בחודש
python3 listings.py --rentcast --comps "6376 Village Oak"    # הקומפס של ליד, בעיניים. חינם באותו יום (snapshot)
python3 listings.py                                          # או מ-CSV של Redfin ב-Downloads
python3 listings.py --dom 30                                 # רק מודעות שיושבות מעל 30 יום
python3 listings.py --comps "646 E 51ST ST"                  # הקומפס של מודעה אחת

python3 sdf.py selftest    # בדיקה עצמית — חייב להדפיס "selftest: ok"
python3 analyze.py selftest
python3 arv.py selftest
python3 listings.py selftest
```

**Check it's alive:** `python3 sdf.py selftest && python3 analyze.py selftest &&
python3 arv.py selftest && python3 listings.py selftest` — ארבעה `ok` = הלוגיקה תקינה.
**Start / Stop / Restart:** לא רלוונטי — אין תהליך רקע. המפה היא קובץ סטטי.
**נתונים ישנים?** כל הרצה מדפיסה "נתונים עד <תאריך>" ומזהירה מעל 14 יום.
`python3 sdf.py refresh` מוריד מחדש רק את השנה הנוכחית (~25 שניות; המדינה
מעדכנת כל שישי, מריון מגישה בפיגור ~חודשיים). `rm -rf data/` = הכל מחדש, ~4 דקות.

## מה יש כאן

- `deals_map.html` — **המפה.** 1.1MB, נפתחת בדפדפן. צריך אינטרנט לאריחי המפה
- `map.py` — בונה את המפה
- `sdf.py` — מנוע הנתונים: הורדה, פרסור, זיהוי פליפים, גיאוקודינג
- `analyze.py` — ניתוח המסנן והמתחרים
- `arv.py` — **חיתום נכס בודד.** קומפס גאוגרפיים + טווח רווח, לא מספר בודד
- `listings.py` — **חיתום בכמות.** CSV של Redfin (או RentCast) → כל המודעות
  ב-12 זיפי היעד ממוינות לפי הפער מהצעת ה-72%. `.env.example` — שם המפתח האופציונלי
- `underwriting_log.csv` — **יומן החיתום.** כל הרצת `arv.py deal`. עמודת `outcome` ידנית. לא למחוק
- `docs/בדיקה_זיפים_צפוניים.md` — **למה 46220/46205/46260/46240 נוספו**, ומה שונה שם בפרודקט
- `docs/מסנן_החיתום_ומתחרים.md` — **התשובות המלאות.** למה 16%, איך מרחיבים,
  המודל העסקי של כל מתחרה, ואזהרות הנתונים
- `מודל_חיתום_פליפ_אינדיאנפוליס.xlsx` — מודל החיתום, 6 גיליונות
- `CLAUDE.md` — קונטקסט העסק + הוראות לקוד
- `docs/מחקר_גאוגרפי_אינדיאנפוליס.md` — פרופיל 5 חלקים של המטרו
- `docs/פניות_חוסמי_ההצעה.md` — **שלוש הפניות לסגירת חוסמי ההצעה**, מוכנות
  להעתקה, כולל למי לפנות מאפס ומה לחפש בתשובה
- `docs/ביקורת_מודל_וכלים.md` — **ביקורת מקצה לקצה (6.9.2026):** זרימת
  עסקאות מדודה לכל זיפ, זיהום בנייה חדשה, 27 וריאציות ARV, ורשימת הכלים
  החסרים לפי עדיפות. `docs/experiments/` — הסקריפטים שמשחזרים כל טבלה
- `decisions.md` — למה זילו נפסל, למה לא בונים סורק, למה ARV קודם
- `tasks/todo.md` — תוכנית + תיקונים פתוחים
- `data/` — קבצי SDF וגיאוקוד, ~140MB. לא ב-git, נמחק בבטחה
