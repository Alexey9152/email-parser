# email-parser

Парсер контактов ресторанов: **Google Maps → сайты и соцсети → e-mail**.
Закрывает TODO-лист из двух частей: сбор базы заведений и извлечение почт.

Работает **без платных поисковых API**: Google Maps через Apify (или бесплатный
Playwright-скрейпер), почты — с сайтов, из поисковой выдачи и по MX-записям.

## Пять путей получения e-mail

| # | Путь | Статус в отчёте |
|---|---|---|
| 1 | `mailto:` в HTML | `Found on site` |
| 2 | Регулярка по тексту, JSON-LD, `og:email`, inline-JS, обфускация (`[at]`, `%40`, `\u0040`) | `Found on site` |
| 3 | Страницы `/contact`, `/about`, `/reservations`, `/impressum` | `Found on site` |
| 4 | Сниппеты DuckDuckGo: `site:facebook.com "<название>" "<город>"`, затем `"<название>" email` | `Found on FB` / `Found via web search` |
| 5 | Подбор `info@домен` / `contact@домен` с проверкой MX | `Guessed (unverified)` |

Дополнительно: `--js` рендерит тяжёлые React/JS-сайты в headless Chromium и
пытается «раскрыть» контакты кликом. `--fb-browser` открывает страницу Facebook
в браузере (работает редко — FB требует логин).

## Установка

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1   # если блокирует политика: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
```

Дальше впиши в `.env` токен Apify. **Никогда не клади ключи в `.env.example`** —
этот файл коммитится, а `.env` в `.gitignore`.

## Настройка `.env`

| Переменная | Назначение |
|---|---|
| `MAPS_PROVIDER` | `apify` (рекомендуется), `outscraper` или `playwright` (бесплатно, но хрупко) |
| `APIFY_TOKEN` | токен из https://console.apify.com/account/integrations |
| `APIFY_ACTOR` | по умолчанию `compass/crawler-google-places` |
| `OUTSCRAPER_API_KEY` | альтернатива Apify |
| `SEARCH_BACKEND` | `auto` / `duckduckgo` / `serpapi` |
| `SEARCH_DELAY` | пауза между поисковыми запросами, сек (по умолчанию 4) |
| `SEARCH_COOLDOWN` | сколько «отдыхать» после блокировки, сек (120) |
| `SERPAPI_KEY` | **необязателен**; если есть — используется вместо DDG |
| `HTTP_TIMEOUT` / `HTTP_WORKERS` | сеть и параллельность |
| `HEADLESS` | `0` — показывать окно браузера |

## Запуск

```powershell
# полный цикл: Maps (Apify) + сайты + поиск почт
python main.py -q "restaurants in Ocean City, MD" --limit 100 -v

# то же без активации venv (обёртка сама берёт .venv\Scripts\python.exe)
.\run.cmd -q "restaurants in Ocean City, MD" --limit 100 -v

# с подбором info@/contact@ для тех, у кого почты нет нигде
python main.py -q "bars in Miami, FL" --limit 50 --guess -v

# только собрать базу
python main.py -q "cafes in Austin, TX" --limit 200 --stage maps

# только почты по готовому списку, с JS-рендером
python main.py --stage emails --input data/restaurants_list.json --js --guess -v

# бесплатный скрейпер Maps вместо Apify
python main.py -q "pizza in Boston, MA" --limit 20 --provider playwright --headed
```

Флаги: `--provider`, `--stage`, `--limit`, `--workers`, `--timeout`,
`--no-search`, `--search-limit`, `--guess`, `--js`, `--fb-browser`,
`--headed`, `--out-dir`, `-v` / `-vv`.

`--search-limit` — это **количество поисковых запросов** на прогон (по умолчанию
30, до 3 на заведение). DuckDuckGo быстро включает тормоз, поэтому для больших
списков лучше `--search-limit 10` и запуск в несколько заходов.

### Если `ModuleNotFoundError: No module named 'dotenv'`

Значит `python` в терминале — системный (3.14), а зависимости стоят в `.venv`.
Проверь, что в приглашении есть `(.venv)`, либо запускай так:

```powershell
.\run.cmd -q "restaurants in Ocean City, MD" --limit 50 -v
# или напрямую
.\.venv\Scripts\python.exe main.py -q "restaurants in Ocean City, MD" --limit 50 -v
```

`.cmd` работает в PowerShell и cmd без настроек. Вариант `.\run.ps1` требует
разрешённых скриптов; если PowerShell ругается — на один сеанс:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\run.ps1 -q "restaurants in Ocean City, MD" --limit 50 -v
```

## Результаты

| Файл | Содержимое |
|---|---|
| `data/restaurants_raw.json` | всё, что вернул Maps |
| `data/restaurants_list.json` | после фильтрации (только с сайтом, без дублей) |
| `data/final_contacts.csv` | финальная таблица (UTF-8 BOM — открывается в Excel) |
| `data/final_contacts.json` | то же в JSON |

Колонки: Name, Website, Emails, Primary Email, Facebook, Phone, Address,
Status, Sources, Google Maps URL, Note.

Статусы: `Found on site`, `Found on FB`, `Found via web search`,
`Found via JS render`, `Guessed (unverified)`, `Not found`.

## Структура

```
main.py                    # точка входа
run.cmd / run.ps1          # запуск через .venv без активации
email_parser/
  cli.py                   # argparse, логирование
  config.py                # .env -> Settings
  models.py                # Restaurant / ContactResult / статусы
  utils.py                 # URL, домены, город из запроса
  http_client.py           # httpx: UA-ротация, ретраи, https->http, POST-формы
  extractors.py            # mailto, regex, JSON-LD, inline-JS, соц-ссылки
  website.py               # обход сайта и contact-страниц
  playwright_site.py       # JS-рендер и клики по «показать контакты»
  serp.py                  # DuckDuckGo (бесплатно) + SerpAPI (опционально)
  facebook.py              # поиск почт в сниппетах, фильтр мусора
  guess.py                 # info@/contact@ + MX-проверка
  pipeline.py              # оркестрация, дедуп, статусы
  exporter.py              # JSON/CSV
  maps/
    base.py                # протокол провайдера
    playwright_provider.py # бесплатный скрейпер Maps
    apify.py
    outscraper.py
tests/                     # 22 теста, без сети
```

## Ограничения

- **DuckDuckGo троттлит**: после 2–3 быстрых запросов отдаёт HTTP 202. Код это
  распознаёт, ставит бэкенд на паузу и не долбит его. Если нужно много запросов —
  добавь `SERPAPI_KEY` (100/мес бесплатно) или разбей прогон на части.
- Bing, Google и Startpage без JS/капчи результатов не отдают — поэтому их нет
  в цепочке; Google-выдача доступна только через SerpAPI.
- `Guessed (unverified)` — это **не найденный адрес**, а гипотеза: домен принимает
  почту (MX есть), но ящик не проверен. Перед рассылкой проверяй.
- Бесплатный Playwright-скрейпер Maps ломается при смене DOM и капче — для
  объёмов от сотни заведений используй Apify.
- Google Sheets (пункт 2.4.3) не подключён — экспорт в CSV/JSON.

## Соответствие TODO

| Пункт | Где |
|---|---|
| 1.1 параметры (`query`, `limit`, output) | `cli.py` |
| 1.2 сбор с Maps + 5 полей | `maps/*` |
| 1.2.3 дедуп и фильтр без сайта | `pipeline.filter_restaurants` |
| 1.3 `restaurants_list.json` | `exporter.save_restaurants_json` |
| 2.1.1 UA-ротация, таймаут, http-fallback | `http_client.py` |
| 2.1.2 mailto, regex, фильтр мусора | `extractors.py` |
| 2.1.3 contact-страницы и Facebook-ссылки | `website.py`, `extractors.py` |
| 2.2 Facebook fallback | `facebook.py` + `serp.py` |
| 2.2.3 FB через Playwright | `--fb-browser` |
| 2.3 JS-сайты | `playwright_site.py`, `--js` |
| 2.4 слияние, дедуп, CSV | `pipeline.enrich_restaurants`, `exporter.py` |

## Тесты

```powershell
pip install -r requirements-dev.txt
pytest -q
```
