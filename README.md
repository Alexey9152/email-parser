# email-parser

Собирает **все заведения общепита на выбранной территории** (город или прямоугольник
на карте), ищет их e-mail и складывает проверяемый отчёт в **Excel**.

Основной сценарий: выделил область на карте → выбрал категории (рестораны, кофейни,
мороженое, шаурма, бары, фуд-траки…) → получил `final_contacts.xlsx`, где у каждой
строки есть кликабельные ссылки на сайт, Facebook и Google Maps, чтобы вручную
проверить, откуда взялся адрес. Затем `python main.py send` строит персональные
письма по этой таблице (по умолчанию — только превью, без отправки).

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
7. **Выбор лучшего адреса**: все найденные адреса не перезаписывают друг друга, а
   складываются в один список с пометкой источника. Для отклика выбирается
   `manager@` / `hiring@` / `hr@` / `jobs@` / `careers@`, а не первый попавшийся
   `info@`. Если на сайте только общий ящик, пайплайн дополнительно ищет страницу
   Facebook и менеджерский адрес там.

## Как выбирается основной e-mail

Для каждой строки адреса ранжируются по вероятности найти человека, который нанимает
персонал:

- **высший приоритет** — `manager`, `hiring`, `hr`, `recruit`, `jobs`, `careers`,
  `employment`, `owner`, `apply`;
- **хороший запасной вариант** — `info`, `contact`, `hello`, `office`, `admin`;
- **низкий приоритет** — `reservations`, `booking`, `events`, `sales`, `marketing`,
  `press`, `support`;
- адрес с домена самого заведения чуть предпочтительнее чужого;
- личный Gmail на странице Facebook ценится выше безликого `info@` на сайте;
- `webmaster@`, `privacy@`, `billing@` и подобные отодвигаются вниз;
- `Guessed (unverified)` всегда в конце и по умолчанию **не используется** для рассылки.

Порядок и источник каждого адреса видны в Excel: столбцы «Основной e-mail»,
«Откуда основной e-mail» и «Источники e-mail».

## Поиск Facebook

Facebook ищется не только по ссылке в подвале главной страницы:

- все ссылки в HTML, включая `facebook.com`, `fb.com`, `fb.me`;
- JSON-LD `sameAs`, `<link rel="me">`, inline-JS;
- экранированные URL (`https:\/\/facebook.com\/...`) и HTML-сущности;
- страницы `/contact`, `/about`, `/find-us`, `/locations`, `/links` и другие
  контактные разделы (обход не останавливается на первом найденном e-mail);
- поисковые сниппеты Google через Apify, если на сайте ссылки нет;
- страница Facebook открывается в Chromium, раскрывается блок «See more / About»,
  откуда часто виден e-mail менеджера.

Ссылки на `sharer`, `plugins`, `login`, `help`, посты и служебные разделы
отбрасываются; из `facebook.com/venue/posts/123` остаётся `facebook.com/venue`.

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
| `SMTP_HOST` / `SMTP_PORT` | SMTP-сервер и порт (587 для STARTTLS, 465 для SSL) |
| `SMTP_USER` / `SMTP_PASSWORD` | логин и пароль приложения (не обычный пароль почты) |
| `SMTP_SSL` / `SMTP_STARTTLS` | `SMTP_SSL=1` для порта 465; по умолчанию STARTTLS |
| `MAIL_FROM` / `MAIL_FROM_NAME` | видимый адрес и имя отправителя |
| `MAIL_REPLY_TO` / `MAIL_BCC` | необязательные Reply-To и копия себе |
| `MAIL_DELAY` / `MAIL_DAILY_LIMIT` | пауза между письмами (30 с) и лимит в день (50) |

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
- нажми «Запустить поиск» — сверху виден **конкретный прогресс**: этап, сколько
  сайтов/запросов/страниц Facebook обработано и сколько осталось, сколько e-mail уже
  найдено; журнал идёт внизу, по завершении появятся кнопки «Скачать Excel» и
  «Скачать CSV».

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

# превью писем по готовой таблице (ничего не отправляется)
python main.py send --input data/final_contacts.xlsx

# реальная отправка: SMTP в .env, резюме и явный флаг --send
python main.py send --input data/final_contacts.xlsx --resume data/Alex_Los_Resume.pdf --send

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
| `--search-limit` | максимум поисковых запросов (0 = 2 для заведения без адреса, 1 для `info@`, но не больше `SEARCH_MAX_QUERIES`) |
| `--search-provider` | `auto` / `apify` / `serpapi` / `ddg` / `none` |
| `--js` | рендерить сайты без почты в Chromium |
| `--guess` | добавить `info@`/`contact@` после проверки MX |
| `--headed` | показать браузер |
| `-v` / `-vv` | INFO / DEBUG |

## Excel-отчёт

`data/final_contacts.xlsx` (или `data/ui-*/final_contacts.xlsx`) состоит из двух листов.

**«Контакты»**: №, Название, Категория, Рейтинг, Отзывов, Цена, Сайт, E-mail,
Основной e-mail, Facebook, Телефон, Адрес, Статус, Источник, Google Maps, Примечание,
Широта, Долгота, Запрос, Откуда основной e-mail, Источники e-mail.

- ссылки на сайт, Facebook и Google Maps **кликабельны**;
- основной e-mail — ссылка `mailto:`;
- «Откуда основной e-mail» показывает, выбран ли адрес с сайта, Facebook или поиска;
- «Источники e-mail» перечисляет **все** найденные адреса и их происхождение;
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

## Рассылка Work & Travel

`send` читает Excel-отчёт (или JSON), берёт **основной e-mail** из каждой строки и
подставляет в письмо название заведения и место, где адрес был найден.

Сначала — только превью, без сети:

```powershell
python main.py send --input data/final_contacts.xlsx
```

Будет создан `data/email_preview.txt`; в консоль выводятся первые 3 письма. Проверь
текст, адреса и ссылки. Затем настрой SMTP в `.env` (для Gmail/Yandex нужен
«пароль приложения», а не обычный пароль), положи резюме в `data/resume.pdf` и
запусти реальную отправку:

```powershell
python main.py send --input data/final_contacts.xlsx --resume data/Alex_Los_Resume.pdf --send
```

Что уже встроено:

- письмо начинается с названия заведения и фразы вида «I found this e-mail address
  (manager@...) on your Facebook page (ссылка)»;
- текст письма — `templates/application_email.txt`, его можно править без кода;
  доступны `{restaurant}`, `{email}`, `{source}`, `{source_link}`, `{found_note}`,
  `{website}`, `{facebook}`, `{maps}`, `{address}`, `{phone}`;
- тема по умолчанию: `Work & Travel application — {restaurant}`;
- резюме прикрепляется к каждому письму;
- адреса `Guessed (unverified)` по умолчанию **пропускаются**;
- дубли e-mail удаляются, уже отправленные адреса берутся из `data/sent_log.json`;
- между письмами пауза `MAIL_DELAY` (30 с), действует дневной лимит
  `MAIL_DAILY_LIMIT` (50);
- `--limit 5` — попробовать на пяти адресах; `--status` / `--source` — фильтры;
- `--include-sent` — отправить повторно, `--include-guessed` — включить догадки,
  `--no-attach` — без резюме.

Без флага `--send` команда **физически ничего не отправляет**. Журнал успешных
отправок: `data/sent_log.json`.

## Сколько это стоит

- **Apify Google Maps**: зависит от числа заведений и категорий; лимит делится между
  запросами. Для района на 300 заведений это заметная, но предсказуемая сумма —
  смотри тариф актора `compass/crawler-google-places`.
- **Apify Google Search**: около **$0.00105 за результат**. Один запрос — обычно
  до 10 результатов, то есть ~$0.01; 20 запросов в живом прогоне дали 167 результатов
  и ~$0.18. Авто-бюджет — 2 запроса на заведение без почты и 1 запрос на
  заведение, где найден только общий `info@` (чтобы поискать менеджера).
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
  config.py                # .env -> Settings (включая SMTP)
  models.py                # Restaurant / ContactResult / рейтинг e-mail
  categories.py            # 53 категории, группы, запросы Maps
  utils.py                 # bbox, Area/GeoJSON, URL, город
  http_client.py           # httpx: UA-ротация, ретраи, https->http
  extractors.py            # mailto, regex, JSON-LD, JS-эскейпы, Facebook
  website.py               # обход сайта и contact-страниц
  playwright_site.py       # JS-рендер и страницы Facebook
  serp.py                  # Apify Google, SerpAPI, DDG, батчи
  facebook.py              # план запросов, разбор выдачи, фильтр адресов
  guess.py                 # info@/contact@ + MX-проверка
  progress.py              # потокобезопасные счётчики для UI
  pipeline.py              # оркестрация, слияние адресов, поиск, FB, экспорт
  exporter.py              # CSV/JSON/XLSX + чтение отчёта обратно
  mailer.py                # send: персонализация, SMTP, превью, журнал
  webui.py                 # Flask API + шаблон карты
  templates/index.html     # Leaflet UI
  maps/
    base.py                # протокол провайдера
    playwright_provider.py # бесплатный скрейпер Maps
    apify.py               # Apify Maps + customGeolocation (полигон)
    outscraper.py
templates/
  application_email.txt    # текст письма, редактируется без кода
tests/                     # 68 тестов без сети
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
