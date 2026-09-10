# Marketing Intelligence Monitor — رسمیو

یک سیستم نظارت بر اخبار (News Monitoring / Market Intelligence Feed) برای تیم مارکتینگ رسمیو.
هر چند ساعت اخبار را از منابع رایگان می‌گیرد، هر خبر را بر اساس ارتباطش با رسمیو امتیازدهی می‌کند،
نتایج را در گروه تلگرام می‌فرستد و در این ریپوی خصوصی ذخیره می‌کند.

## نحوه کار

- اجرا با **GitHub Actions** هر ۳ ساعت (بدون نیاز به سرور/کارت اعتباری).
- منابع: Google News RSS (رایگان)، صفحات عمومی تلگرام (`t.me/s/...`)، وب‌سرچ برای `x.com`/`instagram.com`/`linkedin.com`.
- هر خبر:
  - در **دسته‌ها** دسته‌بندی می‌شود (Rasmio, Competitors, Product & AI, Market Intelligence, Business Data, Iran Business Ecosystem)
  - امتیاز **Relevance Score 0–100** می‌گیرد
  - **Priority** می‌گیرد: بحرانی ≥75 / بالا ≥55 / متوسط ≥30 / کم‌اهمیت
  - **نوع** می‌گیرد: خبر مستقیم رسمیو / خبر رقیب / ترند مهم بازار / عمومی
  - **دلیل کوتاه** برای امتیاز، همراه با کلیدواژه‌های matched دارد
- گزارش در تلگرام بر اساس اولویت مرتب می‌شود؛ داده کامل Markdown + JSONL در `data/` ذخیره و push می‌شود.

## فایل‌ها

- `config.json` — منابع، تنظیمات تلگرام و git (بدون سکرت)
- `rules.json` — **کاملاً قابل تنظیم**: وزن دسته‌ها، کلیدواژه‌ها با وزن، `cap` هر دسته، ضرایب Relevance، آستانه‌های Priority
- `monitor.py` — موتور (جستجو، امتیازدهی، گزارش، ذخیره)
- `.github/workflows/monitor.yml` — زمان‌بندی هر ۳ ساعت + ارسال به تلگرام + push
- `data/` — خروجی‌ها (Markdown روزانه، JSONL کامل، `seen.json` برای dedup پایدار)

## تنظیم وزن‌ها و کلیدواژه‌ها

وزن‌ها و کلیدواژه‌ها در `rules.json` هستند و بدون دست زدن به `monitor.py` قابل تغییرند:

| پارامتر | محل | توضیح |
|---|---|---|
| وزن دسته | `categories[].weight` | وزن ۰ تا ۱ در امتیازدهی |
| سقف امتیاز | `categories[].cap` | مانع بحرانی شدن غیررسمیوها |
| وزن کلیدواژه | `categories[].keywords[].weight` | شدت هر کلیدواژه |
| کلیدواژه‌های جستجو | `categories[].search` | عبارات ارسالی به Google News |
| divisor/title/bonus | `scoring.*` | ضرایب فرمول Relevance |
| آستانه‌ها | `priorities.*` | مرزهای Priority |
| حداقل امتیاز | `scoring.min_score` | فیلتر نویز |

## Secrets (تنظیمات GitHub → Actions secrets)

- `TELEGRAM_BOT_TOKEN` — توکن بات تلگرام
- `TELEGRAM_CHAT_ID` — شناسه گروه (مثلاً `-1004486302229`)

## اجرای دستی

```
python monitor.py --once            # یک چرخه (گزارش به تلگرام اگر چت تعیین شده باشد)
python monitor.py --once --send-report
```

یافتن chat_id گروه: بات را به گروه اضافه کنید، یک پیام بفرستید، سپس
`python tools/find_chat_id.py` را با `TELEGRAM_BOT_TOKEN=<token>` اجرا کنید.