# email-parser

Собирает **все заведения общепита на выбранной территории** (город или прямоугольник
на карте), ищет их e-mail и складывает проверяемый отчёт в **Excel**.

Основной сценарий: выделил область на карте → выбрал категории (рестораны, кофейни,
мороженое, шаурма, бары, фуд-траки…) → получил `final_contacts.xlsx`, где у каждой
строки есть кликабельные ссылки на сайт, Facebook и Google Maps, чтобы вручную
проверить, откуда взялся адрес.

## Как это работает

1. **Google Maps** через Apify (или бесплатный Playwright-скрейпер) собирает заведения.
   Чтобы обойти лимит Google Maps (~120 результатов на один запрос), один и тот же
   район опрашивается по **53 категориям** — от «ресторан» и «кофейня» до «мороженое»,
   «шаурма», «фуд-трак» и «коктейль-бар».
2. **Сайты**: главная и `/contact`, `/about`, `/reservations`, `mailto:`, JSON-LD,
   `og:email`, inline-JS, обфускация (`[at]`, `%40`, `\u0040`). Заведения **без сайта
   не выбрасываются** — именно у них почта чаще всего есть только в Facebook.
3. **Поиск Google через Apify** (тот же токен): сниппеты `site:facebook.com "<название>"
   "<город>"` дают ссылку на страницу Facebook и иногда сам адрес. Один прогон актора
   обрабатывает десятки запросов сразу, поэтому это дешево и быстро.
4. **Страница Facebook в браузере**: Playwright открывает найденную страницу и достаёт
   e-mail из блока «О себе» даже за стеной логина (это работает не для всех страниц).
5. **JS-рендер** (флаг `--js`) для тяжёлых React/Vue-сайтов.
6. **Подбор по MX** (флаг `--guess`): `info@домен` / `contact@домен`, только если домен
   реально принимает почту. Это **непроверенная догадка**, в отчёте она помечена.

## Установка

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1   # если блокирует политика: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
```

Впиши в `.env` токен Apify. **Ключи в `.env.example` не кладём** — этот файл
коммитится, а `.env` в `.gitignore`.

## Настройка `.env`

| Переменная | Назначение |
|---|---|
| `MAPS_PROVIDER` | `apify` (рекомендуется), `outscraper` или `playwright` (бесплатно, медленно и хрупко) |
| `APIFY_TOKEN` | токен из https://console.apify.com/account/integrations |
| `APIFY_ACTOR` | по умолчанию `compass/crawler-google-places` |
| `APIFY_SEARCH_ACTOR` | `apify/google-search-scraper` — поиск Google для Facebook/e-mail |
| `OUTSCRAPER_API_KEY` | альтернатива Apify |
| `SEARCH_BACKEND` | `auto` / `apify` / `serpapi` / `ddg` |
| `SEARCH_MAX_QUERIES` | предохранитель для авто-бюджета поиска (по умолчанию 400 запросов) |
| `SEARCH_TIMEOUT` | сколько ждать один прогон Apify-поиска, сек (420) |
| `SEARCH_DELAY` / `SEARCH_COOLDOWN` | пауза между запросами / отдых после блокировки (для DDG) |
| `SERPAPI_KEY` | необязателен; если есть, используется первым |
| `HTTP_TIMEOUT` / `HTTP_WORKERS` | сеть и параллельность обхода сайтов |
| `HEADLESS` | `0` — показывать окно браузера (отладка) |

При `SEARCH_BACKEND=auto` порядок такой: SerpAPI (если ключ) → **Apify Google**
(если токен) → DuckDuckGo. DuckDuckGo и прямой Google с обычного IP сейчас часто
отдают капчу, поэтому основной надёжный путь — Apify.

## UI с выбором области на карте

```powershell
.\ui.cmd
# или
.\.venv\Scripts\python.exe ui.py
```

Откроется `http://127.0.0.1:5000/`:

- нажми **«Выделить область»** и протяни мышью прямоугольник на карте;
- или введи город и нажми «Показать город»;
- отметь категории (кнопки групп: «Всё», «Рестораны», «Напитки», «Сладости»,
  «Уличная еда», «Кухни»);
- задай лимит, при необходимости включи JS-рендер / подбор по MX;
- нажми «Запустить поиск» — журнал идёт внизу, по завершении появятся кнопки
  «Скачать Excel» и «Скачать CSV».

Если выбрана область, категории ищутся **внутри полигона** (без «in City»), поэтому
не важно, насколько точно ты назвал город текстом.

## CLI

```powershell
# весь общепит города
python main.py -q "Ocean City, MD" --all-food --limit 300 --provider apify -v

# конкретные категории
python main.py -q "Ocean City, MD" --categories "ice cream,shawarma,cocktail bar" --limit 100

# прямоугольник на карте: south,west,north,east
python main.py --bbox "38.32,-75.10,38.40,-75.03" --all-food --limit 300

# только собрать базу
python main.py -q "cafes in Austin, TX" --limit 200 --stage maps

# только почты по готовому списку + JS + подбор
python main.py --stage emails --input data/restaurants_list.json --js --guess -v

# бесплатный скрейпер Maps вместо Apify
python main.py -q "pizza in Boston, MA" --limit 20 --provider playwright --headed

# список категорий и групп
python main.py --list-categories
```

Основные флаги:

| Флаг | Что делает |
|---|---|
| `-q/--query` | город/район или обычный запрос Maps |
| `--all-food` | все 53 категории |
| `--categories "a,b"` | категории или группы (`restaurants`, `drinks`, `sweets`, `street`, `cuisines`) |
| `--bbox "s,w,n,e"` | прямоугольник вместо текстового города |
| `--limit` | максимум заведений в отчёте |
| `--provider` | `apify` / `playwright` / `outscraper` |
| `--stage` | `all` / `maps` / `emails` |
| `--only-with-website` | старое поведение: выбросить заведения без сайта |
| `--no-fb-page` | не открывать Facebook в браузере (по умолчанию открывается) |
| `--fb-limit` | сколько страниц Facebook максимум открыть (60) |
| `--no-search` | не искать в Google/Facebook |
| `--search-limit` | максимум поисковых запросов (0 = 2 на заведение, но не больше `SEARCH_MAX_QUERIES`) |
| `--search-provider` | `auto` / `apify` / `serpapi` / `ddg` / `none` |
| `--js` | рендерить сайты без почты в Chromium |
| `--guess` | добавить `info@`/`contact@` после проверки MX |
| `--headed` | показать браузер |
| `-v` / `-vv` | INFO / DEBUG |

## Excel-отчёт

`data/final_contacts.xlsx` (или `data/ui-*/final_contacts.xlsx`) состоит из двух листов.

**«Контакты»**: №, Название, Категория, Рейтинг, Отзывов, Цена, Сайт, E-mail,
Основной e-mail, Facebook, Телефон, Адрес, Статус, Источник, Google Maps, Примечание,
Широта, Долгота, Запрос.

- ссылки на сайт, Facebook и Google Maps **кликабельны**;
- основной e-mail — ссылка `mailto:`;
- включены фильтры по столбцам и закреплена шапка;
- статус подсвечен: зелёный — найден, жёлтый — догадка, красный — не найден.

**«Сводка»**: дата, запросы, область и ссылка на неё, провайдер, число категорий,
сколько заведений/адресов, разбивка по источникам, длительность, легенда статусов и
напоминание, как проверять адреса.

Статусы:

| Статус | Значение |
|---|---|
| `Найден на сайте` | адрес в HTML/контактах сайта |
| `Найден на Facebook` | сниппет или страница Facebook |
| `Найден в поиске` | сниппет обычной выдачи |
| `Найден после рендера` | сайт отрендерен в Chromium |
| `Предположение (не проверено)` | `info@`/`contact@` по MX, **проверять вручную** |
| `Не найден` | адреса нет в доступных источниках |

Дополнительно сохраняются `final_contacts.csv` (UTF-8 BOM, английские значения —
удобно для скриптов) и `final_contacts.json`, а также `restaurants_raw.json` и
`restaurants_list.json` с этапа сбора.

## Сколько это стоит

- **Apify Google Maps**: зависит от числа заведений и категорий; лимит делится между
  запросами. Для района на 300 заведений это заметная, но предсказуемая сумма —
  смотри тариф актора `compass/crawler-google-places`.
- **Apify Google Search**: около **$0.00105 за результат**. Один запрос — обычно
  до 10 результатов, то есть ~$0.01; 20 запросов в живом прогоне дали 167 результатов
  и ~$0.18. Авто-бюджет — 2 запроса на заведение без почты.
- **Playwright** (Maps/сайты) — бесплатно, но медленнее и может упираться в капчу.

Управлять расходом: `--limit`, `--search-limit`, `--fb-limit`, `--no-search`,
`--no-fb-page`.

## Ограничения

- Facebook часто требует логин: часть страниц не отдаёт контакты даже браузеру.
  Тогда в отчёте останется ссылка на страницу для ручной проверки.
- Google Maps отдаёт максимум ~120 результатов на запрос, поэтому «весь город»
  собирается категориями; очень большие города имеет смысл бить на районы (`--bbox`).
- Одинаковые названия в разных районах не всегда различимы, если у заведения нет
  `place_id`; дедуп идёт по `place_id`, домену сайта или паре «название + адрес».
- `Предположение (не проверено)` — это гипотеза, а не найденный адрес.
- Прямой скрейпинг выдачи Google/DDG с домашнего IP нестабилен — для объёмов
  используй Apify-поиск.
- Google Sheets (TODO 2.4.3) не подключён; экспорт — Excel/CSV/JSON.

## Структура

```
main.py                    # CLI: python main.py --help
ui.py                      # карта: python ui.py
run.cmd / run.ps1          # CLI через .venv без активации
ui.cmd                     # UI через .venv без активации
email_parser/
  cli.py                   # argparse, валидация, итоги
  config.py                # .env -> Settings
  models.py                # Restaurant / ContactResult / статусы
  categories.py            # 53 категории, группы, запросы Maps
  utils.py                 # bbox, Area/GeoJSON, URL, город
  http_client.py           # httpx: UA-ротация, ретраи, https->http
  extractors.py            # mailto, regex, JSON-LD, JS-эскейпы, соц-ссылки
  website.py               # обход сайта и contact-страниц
  playwright_site.py       # JS-рендер и страницы Facebook
  serp.py                  # Apify Google, SerpAPI, DDG, батчи
  facebook.py              # план запросов, разбор выдачи, фильтр адресов
  guess.py                 # info@/contact@ + MX-проверка
  pipeline.py              # оркестрация, дедуп, поиск, FB, экспорт
  exporter.py              # CSV/JSON/XLSX (2 листа, ссылки, статусы)
  webui.py                 # Flask API + шаблон карты
  templates/index.html     # Leaflet UI
  maps/
    base.py                # протокол провайдера
    playwright_provider.py # бесплатный скрейпер Maps
    apify.py               # Apify Maps + customGeolocation (полигон)
    outscraper.py
tests/                     # 50 тестов без сети
```

## Тесты

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Если `ModuleNotFoundError: No module named 'dotenv'`

Значит `python` в терминале — системный (3.14), а зависимости стоят в `.venv`.
Запускай через обёртку или напрямую:

```powershell
.\run.cmd -q "restaurants in Ocean City, MD" --limit 50 -v
.\ui.cmd
# или
.\.venv\Scripts\python.exe main.py -q "restaurants in Ocean City, MD" --limit 50 -v
```

`.\run.ps1` требует разрешённых скриптов; на один сеанс:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\run.ps1 -q "restaurants in Ocean City, MD" --limit 50 -v
```
