# MultiTrade VPS Command Runbook

מסמך קצר לפקודות תחזוקה ותפעול נפוצות בשרת ה-Hostinger VPS.

כל הפקודות כאן מיועדות להרצה ב-VPS Console או ב-SSH על השרת, מתוך תיקיית
האפליקציה:

```bash
cd /opt/multitrade/app
```

## עדכון גרסה מה-GitHub

מעדכן את הקוד, בונה מחדש את הקונטיינרים ומעלה את המערכת:

```bash
cd /opt/multitrade/app
bash ops/update.sh
```

בדיקת גרסה אחרי העדכון:

```bash
docker compose run --rm --no-deps engine multitrade doctor
```

באתר, הגרסה מופיעה בדרך כלל בפינה/תגית הגרסה. אם האתר עדיין מציג גרסה ישנה,
נסה רענון חזק בדפדפן או בדוק שהקונטיינרים אכן נבנו מחדש.

## עריכת קובץ הגדרות

פתיחת קובץ הסביבה:

```bash
cd /opt/multitrade/app
nano .env
```

שמירה ב-nano:

```text
Ctrl+O
Enter
Ctrl+X
```

לאחר שינוי `.env`, הפעל מחדש:

```bash
docker compose up -d --build
```

בדיקת תקינות הגדרות:

```bash
docker compose run --rm --no-deps engine multitrade doctor
```

## סטטוס מערכת

מצב כל הקונטיינרים:

```bash
cd /opt/multitrade/app
docker compose ps
```

לוגים כלליים אחרונים:

```bash
docker compose logs --tail=120
```

לוגים של מנוע המסחר:

```bash
docker compose logs --tail=120 engine
```

לוגים של האוטומציה:

```bash
docker compose logs --tail=120 automation
```

לוגים של הדשבורד:

```bash
docker compose logs --tail=120 dashboard
```

לוגים של Caddy/HTTPS:

```bash
docker compose logs --tail=120 caddy
```

מעקב חי אחרי לוגים:

```bash
docker compose logs -f --tail=80
```

## בדיקות בריאות

בדיקת תצורת המערכת:

```bash
docker compose run --rm --no-deps engine multitrade doctor
```

בדיקת heartbeat של החשבון/ברוקר:

```bash
docker compose run --rm --no-deps engine multitrade healthcheck
```

בדיקת מחזור אסטרטגיות:

```bash
docker compose run --rm --no-deps engine multitrade automation-healthcheck
```

בדיקת Strategy Lab:

```bash
docker compose run --rm --no-deps engine multitrade strategy-lab-healthcheck
```

בדיקת Asset Universe:

```bash
docker compose run --rm --no-deps engine multitrade asset-universe-healthcheck
```

בדיקת Option Evidence:

```bash
docker compose run --rm --no-deps engine multitrade option-evidence-healthcheck
```

## Dashboard data export

After signing in to the dashboard, open:

```text
https://trade.p-y.co.il/
```

Go to:

```text
Management -> Data Export -> Download full analyst snapshot
```

The downloaded file is named:

```text
multitrade-analyst-snapshot.json
```

Upload that file to Codex when you want a full review of the latest strategy
validation, Paper execution, risk, health, and audit evidence.

## הרצת בדיקת אסטרטגיות מהממשק

החל מגרסה `0.35.11`, ניתן להריץ בדיקה מואצת גם מהאתר:

```text
Management -> Research Runs -> Start accelerated validation
```

בחר חשבון, Timeframes, מספר Workers והאם להפעיל Optimization. הפעולה נרשמת
ב-Audit, רצה ברקע, ואינה מאפשרת מסחר או קידום אוטומטי של אסטרטגיה.

פקודת הטרמינל המקבילה, אם עדיין צריך:

```bash
cd /opt/multitrade/app
docker compose run --rm --no-deps engine \
  multitrade accelerated-validation --workers 1 --timeframes 1Day
```

Equivalent terminal command, if needed:

```bash
cd /opt/multitrade/app
set -a
. ./.env
set +a
curl -sS \
  -H "Authorization: Bearer $ANALYST_API_TOKEN" \
  "https://trade.p-y.co.il/api/analyst/v1/snapshot?limit=1000" \
  > analyst-snapshot.json
```

בדיקת הדשבורד המקומי בתוך השרת:

```bash
docker compose run --rm --no-deps engine multitrade dashboard-healthcheck
```

## הרצת מחזור מסחר ידני

מריץ מחזור אסטרטגיות אחד בלבד. מתאים לבדיקה אחרי שינוי הגדרות:

```bash
cd /opt/multitrade/app
docker compose run --rm --no-deps engine multitrade automate --once
```

שים לב: ביצוע פקודות למסחר Paper תלוי בכל מנגנוני ההרשאה:

- `TRADING_AUTOMATION_ENABLED=true`
- `TRADING_ENABLE_PAPER_ORDERS=true`
- `TRADING_EMERGENCY_STOP=false`
- אישור Paper execution לכל אסטרטגיה בדשבורד/הגדרות

## הרצת בדיקת אסטרטגיות מלאה

בדיקה מלאה מחדש לכל האסטרטגיות וטווחי הזמן, כולל אופטימיזציה:

```bash
cd /opt/multitrade/app
docker compose run --rm --no-deps engine multitrade accelerated-validation \
  --workers 2 \
  --timeframes 5Min,1Hour,4Hour,1Day \
  --optimize \
  --force-all \
  --max-candidates 80
```

אם Alpaca מחזירה שגיאות pagination/rate-limit, הרץ לפי טווח זמן אחד:

```bash
docker compose run --rm --no-deps engine multitrade accelerated-validation \
  --workers 2 \
  --timeframes 1Day \
  --optimize \
  --force-all \
  --max-candidates 80
```

דוגמאות לטווחים נפרדים:

```bash
docker compose run --rm --no-deps engine multitrade accelerated-validation --workers 2 --timeframes 4Hour --optimize --force-all --max-candidates 80
docker compose run --rm --no-deps engine multitrade accelerated-validation --workers 2 --timeframes 1Hour --optimize --force-all --max-candidates 80
docker compose run --rm --no-deps engine multitrade accelerated-validation --workers 2 --timeframes 5Min --optimize --force-all --max-candidates 80
```

לאחר סיום הריצה, רענן את האתר וגש אל:

```text
Strategy Lab -> Accelerated Validation
```

## בדיקת אסטרטגיה אחת ב-backtest

דוגמה ל-Retest על AAPL בגרף יומי:

```bash
docker compose run --rm --no-deps engine multitrade backtest \
  --strategy confirmed_breakout_retest_v3 \
  --symbol AAPL \
  --timeframe 1Day \
  --validate
```

דוגמה ל-Retest על 4 שעות:

```bash
docker compose run --rm --no-deps engine multitrade backtest \
  --strategy confirmed_breakout_retest_v3 \
  --symbol AAPL \
  --timeframe 4Hour \
  --validate
```

דוגמה ל-Put income:

```bash
docker compose run --rm --no-deps engine multitrade backtest \
  --strategy support_delta_put_income_v21 \
  --symbol AAPL \
  --timeframe 1Day \
  --validate
```

## בדיקת שרשרת אופציות

בדיקה קריאה בלבד של option chain, בלי שליחת עסקה:

```bash
docker compose run --rm --no-deps engine multitrade option-scan \
  --underlying AAPL \
  --minimum-dte 30 \
  --maximum-dte 60
```

## הפעלה/כיבוי מחדש

העלאת המערכת:

```bash
docker compose up -d
```

בנייה מחדש והעלאה:

```bash
docker compose up -d --build
```

עצירת המערכת:

```bash
docker compose down
```

אתחול קונטיינרים בלי למחוק מידע:

```bash
docker compose restart
```

אתחול שירות ספציפי:

```bash
docker compose restart dashboard
docker compose restart automation
docker compose restart engine
docker compose restart caddy
```

## בדיקת Git על השרת

הגרסה הנוכחית שהשרת מכיר:

```bash
git log --oneline -1
```

בדיקת branch:

```bash
git branch --show-current
```

בדיקת שינויים מקומיים לא מחויבים:

```bash
git status --short
```

## התחברות לדשבורד

כתובת:

```text
https://trade.p-y.co.il/
```

אם login נכשל למרות שם משתמש וסיסמה נכונים:

```bash
cd /opt/multitrade/app
bash ops/update.sh
docker compose restart dashboard caddy
```

לאחר מכן נסה חלון Incognito או רענון חזק.

## תזכורת בטיחות

- לא להדפיס `.env` מלא למסך.
- לא לשלוח API keys בצ'אט.
- לא להפעיל Live trading לפני שנחליט במפורש לעבור מ-Paper.
- Strategy Lab ו-Accelerated Validation הם מחקריים בלבד; הם לא אמורים לאשר מסחר לבד.
- כל מסחר כרגע צריך להישאר Paper-only.
