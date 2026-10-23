# email-parser

Собирает **все заведения общепита на выбранной территории** (город или прямоугольник
на карте), ищет их e-mail и складывает проверяемый отчёт в **Excel**.

Основной сценарий: выделил область на карте → выбрал категории (рестораны, кофейни,
мороженое, шаурма, бары, фуд-траки…) → получил `final_contacts.xlsx`, где у каждой
строки есть кликабельные ссылки на сайт, Facebook и Google Maps, чтобы вручную
проверить, откуда взялся адрес. Затем `python main.py send` строит персональные
письма по этой таблице (по умолчанию — только превью, без отправки).

## Как это работает

1. **OpenStreetMap/Overpass** (бесплатно, по умолчанию) собирает заведения внутри
   выбранной области: рестораны, кафе, бары, пабы, фастфуд, фуд-корты, мороженое,
   пивные сады, пекарни, дели, кофейни, винные/алкогольные магазины и т.п. Если
   у объекта в OSM уже есть `email`/`contact:email`, адрес попадает в отчёт сразу.
   Ссылка на Facebook тоже берётся из карт: OSM-теги `contact:facebook`, ссылки
   в карточке Google Maps, поля `facebook`/`socialLinks` у Apify/Outscraper.
   Альтернативы: бесплатный **Playwright**-скрейпер Google Maps или платные
   Apify/Outscraper — только если явно выбрать их по имени.
2. **Сайты**: главная и `/contact`, `/about`, `/reservations`, `mailto:`, JSON-LD,
   `og:email`, inline-JS, обфускация (`[at]`, `%40`, `\u0040`). Заведения **без сайта
   не выбрасываются** — именно у них почта чаще всего есть только в Facebook.
3. **Бесплатный поиск** через пакет `ddgs` (DuckDuckGo, Brave, Yahoo, Startpage и
   другие движки) и HTML-версию DuckDuckGo. Широкий запрос
   `<название> <город> facebook` находит страницу Facebook, даже если сам сайт
   закрыт Cloudflare. `SEARCH_BACKEND=auto` **не тратит деньги** и не использует
   Apify/SerpAPI, даже если их ключи лежат в `.env`.
4. **Страница Facebook в браузере**: Playwright открывает найденную страницу и достаёт
   e-mail из блока «О себе» даже за стеной логина (это работает не для всех страниц).
   Если сайт не отдаёт HTML (Cloudflare/403), дополнительно пробуется
   `facebook.com/<домен>` — например, `bigpeckersoc.com` →
   `facebook.com/bigpeckersoc`. Страница принимается только после проверки,
   что найденный e-mail действительно относится к заведению.
   Для страниц с ограничением «18+» или обязательным логином можно один раз
   выполнить `python main.py facebook-login`: откроется Chromium, вы входите в
   Facebook, а профиль сохраняется в `data/facebook-profile` и используется
   следующими запусками.
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
- данные карт: `contact:facebook` в OSM, ссылки в карточке Google Maps и поля
  `facebook`/`socialLinks` в ответах Apify/Outscraper;
- бесплатные поисковые сниппеты (`ddgs`/DuckDuckGo), если на сайте ссылки нет;
- страница Facebook открывается в Chromium, раскрывается блок «See more / About»
  и вкладка «Contact info», откуда часто виден e-mail менеджера.

Ссылки на `sharer`, `plugins`, `login`, `help`, посты и служебные разделы
отбрасываются; из `facebook.com/venue/posts/123` остаётся `facebook.com/venue`.

Важно: страница города/турофиса (`facebook.com/OceanCityMD`) и её почта
(`menuocity@gmail.com`) не считаются контактом ресторана. Сниппет принимается,
только если и страница, и адрес привязаны к названию заведения или его сайту.

**Проверка, что адрес принадлежит именно этому заведению.** Facebook-поиск любит
подсовывать страницы-каталоги вида
`facebook.com/pages/category/Food-Beverage`, в сниппете которых перечислены почты
десятков чужих заведений. Такие адреса теперь отбрасываются:

- сниппеты с агрегатных и категорийных страниц не принимаются;
- для обычной выдачи ссылка должна совпадать по домену со **сайтом заведения** или с
  найденной **страницей Facebook** (проверяется slug страницы и токены названия);
- если сайта и страницы нет, адрес из общего поиска не принимается вообще —
  вместо выдуманной почты в примечании появится «search snippets belonged to
  other venues; checking the page in a browser»;
- страница Facebook после загрузки сверяется со slug: если в адресах нет ни одного
  слова из названия страницы, они не попадают в отчёт;
- заблокированная или пустая страница Facebook открывается повторно (`--fb-limit`),
  потому что первый заход часто упирается в интерстициал логина;
- строка, где поиск нашёл только чужой адрес, всё равно идёт на рендер страницы —
  живой контактный блок надёжнее сниппета.

## Сети (Dunkin', Subway, McDonald's и т.п.)

Сетевые заведения остаются в отчёте, но отдельно:

- в основной таблице у них заполнен столбец **«Сеть»**;
- создаётся отдельный лист Excel **«Сети»**;
- если конкретной точки нет, статус — **«Сеть — заявка на сайте»**;
- поиск по Facebook и подбор `info@`/`contact@` для сетей не запускаются:
  у них заявка идёт через сайт бренда;
- корпоративные ящики (`info@dunkindonuts.com`) отбрасываются;
- адрес конкретной точки (`store1234@dunkindonuts.com`, локальный Gmail франшизы)
  сохраняется, и строка получает обычный статус найденного e-mail.

Список брендов и правил — в `email_parser/chains.py`; его можно расширять.

## Установка

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1   # если блокирует политика: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
```

**Токен Apify не нужен.** После `Copy-Item .env.example .env` там уже
`MAPS_PROVIDER=osm` и `SEARCH_BACKEND=auto` — это бесплатный путь. Ключи в
`.env.example` не кладём: файл коммитится, а `.env` в `.gitignore`.

## Настройка `.env`

| Переменная | Назначение |
|---|---|
| `MAPS_PROVIDER` | `osm` (бесплатно, по умолчанию), `playwright` (бесплатный Google Maps), `apify` / `outscraper` (платно) |
| `APIFY_TOKEN` | нужен только для `MAPS_PROVIDER=apify` или `SEARCH_BACKEND=apify` |
| `APIFY_ACTOR` | по умолчанию `compass/crawler-google-places` |
| `APIFY_SEARCH_ACTOR` | `apify/google-search-scraper` — платный поиск, только по явному выбору |
| `OUTSCRAPER_API_KEY` | альтернатива Apify, только по явному выбору |
| `SEARCH_BACKEND` | `auto` / `ddgs` / `ddg` / `apify` / `serpapi` / `none` |
| `SEARCH_MAX_QUERIES` | предохранитель авто-бюджета поиска (по умолчанию 250 запросов) |
| `SEARCH_TIMEOUT` | таймаут одного платного Apify-поиска и OSM-запроса, сек |
| `SEARCH_DELAY` / `SEARCH_COOLDOWN` | пауза между запросами / отдых после блокировки (для DDG) |
| `SERPAPI_KEY` | нужен только для `SEARCH_BACKEND=serpapi` (платно) |
| `HTTP_TIMEOUT` / `HTTP_WORKERS` | сеть и параллельность обхода сайтов |
| `HEADLESS` | `0` — показывать окно браузера (отладка) |
| `FACEBOOK_PROFILE_DIR` | профиль Chromium с логином Facebook (по умолчанию `data/facebook-profile`, если он создан через `facebook-login`) |
| `FACEBOOK_STORAGE_STATE` | альтернатива профилю: JSON с cookies, если вы экспортировали его сами |
| `SMTP_HOST` / `SMTP_PORT` | SMTP-сервер и порт (587 для STARTTLS, 465 для SSL) |
| `SMTP_USER` / `SMTP_PASSWORD` | логин и пароль приложения (не обычный пароль почты) |
| `SMTP_SSL` / `SMTP_STARTTLS` | `SMTP_SSL=1` для порта 465; по умолчанию STARTTLS |
| `MAIL_FROM` / `MAIL_FROM_NAME` | видимый адрес и имя отправителя |
| `MAIL_REPLY_TO` / `MAIL_BCC` | необязательные Reply-To и копия себе |
| `MAIL_DELAY` / `MAIL_DAILY_LIMIT` | пауза между письмами (30 с) и лимит в день (50) |

`SEARCH_BACKEND=auto` — это **только бесплатные** `ddgs` + DuckDuckGo HTML.
Платные бэкенды включаются лишь явно:

```powershell
# бесплатно (по умолчанию)
python main.py -q "Ocean City, MD" --all-food --limit 300 --provider osm

# платно и осознанно
python main.py -q "Ocean City, MD" --all-food --limit 300 --provider apify --search-provider apify
```

Если нужные страницы Facebook требуют логин или показывают «18+», один раз
сохраните сессию:

```powershell
python main.py facebook-login
# войдите в Facebook в открывшемся окне и нажмите Enter в терминале
```

Профиль автоматически подхватится при следующем запуске.

OSM-провайдер сначала геокодирует город через Nominatim, затем делает два запроса
Overpass на плитку области (еда/напитки и продуктовые магазины), повторяет запрос
при 502/504 и переключается на второй публичный сервер Overpass. Большие области
автоматически делятся на плитки.

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
# весь общепит города: бесплатный OpenStreetMap
python main.py -q "Ocean City, MD" --all-food --limit 300 --provider osm -v

# конкретные категории
python main.py -q "Ocean City, MD" --categories "ice cream,shawarma,cocktail bar" --limit 100

# прямоугольник на карте: south,west,north,east (для OSM самый точный вариант)
python main.py --bbox "38.32,-75.10,38.40,-75.03" --all-food --limit 300

# только собрать базу
python main.py -q "cafes in Austin, TX" --limit 200 --stage maps

# только почты по готовому списку + JS + подбор
python main.py --stage emails --input data/restaurants_list.json --js --guess -v

# бесплатный Google Maps вместо OSM (может упереться в captcha)
python main.py -q "pizza in Boston, MA" --limit 20 --provider playwright --headed

# бесплатный поиск по умолчанию; явный выбор для платного Apify
python main.py -q "Ocean City, MD" --limit 100 --search-provider ddgs
python main.py -q "Ocean City, MD" --limit 100 --search-provider apify

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
| `--provider` | `osm` (бесплатно) / `playwright` (бесплатно) / `apify` / `outscraper` (платно) |
| `--stage` | `all` / `maps` / `emails` |
| `--only-with-website` | старое поведение: выбросить заведения без сайта |
| `--no-fb-page` | не открывать Facebook в браузере (по умолчанию открывается) |
| `--fb-limit` | сколько страниц Facebook максимум открыть (60) |
| `--no-search` | не искать в интернете/Facebook |
| `--search-limit` | максимум поисковых запросов (0 = 1 запрос для заведения без адреса или с общим `info@`, но не больше `SEARCH_MAX_QUERIES`) |
| `--search-provider` | `auto` / `ddgs` / `ddg` / `apify` / `serpapi` / `none` |
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
- «Откуда основной e-mail» показывает, выбран ли адрес с сайта, Facebook, OpenStreetMap или поиска;
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
| `Найден в OpenStreetMap` | адрес указан в тегах OSM (`email` / `contact:email`) |
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

**По умолчанию — 0 ₽ / $0.** Основной путь:

- **OpenStreetMap/Overpass + Nominatim** — бесплатно, без ключа.
- **`ddgs` + DuckDuckGo HTML** — бесплатно, без ключа; могут throttling-ить,
  поэтому между запросами стоит пауза, а при 202/429 бэкенд временно отключается.
- **Playwright** (сайты, Facebook, Google Maps) — бесплатно, но медленнее и может
  упираться в капчу.

Платные бэкенды запускаются **только** при явном выборе:

- **Apify Google Maps** — `MAPS_PROVIDER=apify` / `--provider apify`.
- **Apify Google Search** — `SEARCH_BACKEND=apify` / `--search-provider apify`,
  около **$0.00105 за результат** (один запрос обычно до 10 результатов).
- **Outscraper** — `MAPS_PROVIDER=outscraper`.
- **SerpAPI** — `SEARCH_BACKEND=serpapi`.

`SEARCH_BACKEND=auto` **никогда** не выбирает платный бэкенд, даже если
`APIFY_TOKEN` или `SERPAPI_KEY` заполнены. Управлять объёмом:
`--limit`, `--search-limit`, `--fb-limit`, `--no-search`, `--no-fb-page`.
Перед большим прогоном проверь `.env`: `MAPS_PROVIDER=osm`, `SEARCH_BACKEND=auto`.

## Ограничения

- Facebook часто требует логин, а страницы с алкоголем/18+ вообще не показывают
  контент анонимному браузеру. Один раз выполните `python main.py facebook-login`,
  чтобы сохранить сессию; без неё в отчёте останется ссылка для ручной проверки.
- Сайты под Cloudflare отдают боту `403` и страницу «Just a moment», поэтому
  иконки в футере могут быть не видны. В этом случае Facebook ищется через
  поиск и через догадку `facebook.com/<домен>`; адрес принимается только после
  проверки принадлежности заведению.
- OpenStreetMap покрывает меньше заведений, чем Google Maps, и не у всех есть сайт
  или телефон. Зато это бесплатно и стабильно; для более полного охвата можно
  сравнить с `--provider playwright` или выбрать конкретный район через `--bbox`.
- Overpass иногда отвечает 502/504 или таймаутом. Провайдер повторяет запрос,
  переключается на второй сервер и делит большие области на плитки; при полной
  недоступности OSM лучше повторить позже или взять `--provider playwright`.
- `ddgs` — метапоиск: выдача может быть неполной, а отдельные движки (Brave,
  Google, Mojeek) отдают 403/429 с домашнего IP. Это не ошибка пайплайна;
  `auto` продолжает работать на других бесплатных движках и HTML-DuckDuckGo.
- Одинаковые названия в разных районах не всегда различимы, если у заведения нет
  `place_id`; дедуп идёт по `place_id`, домену сайта или паре «название + адрес».
- `Предположение (не проверено)` — это гипотеза, а не найденный адрес.
- Google Sheets не подключён; экспорт — Excel/CSV/JSON.

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
  serp.py                  # бесплатные DDGS/DDG + опциональные Apify/SerpAPI
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
    osm.py                 # бесплатный OpenStreetMap/Overpass + Nominatim
    playwright_provider.py # бесплатный скрейпер Google Maps
    apify.py               # Apify Maps + customGeolocation (полигон)
    outscraper.py
templates/
  application_email.txt    # текст письма, редактируется без кода
tests/                     # тесты без сети
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
