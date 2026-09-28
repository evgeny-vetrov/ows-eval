# Сервер фабул

`ai.gena.services.fabula.server` — HTTP-сервер на FastAPI поверх слоя управления
`manager`. Что он отдаёт:

* **API менеджера** — все 52 операции: сценарии, версии, теги, фабулы, миграции,
  разрешение конфликтов, аудит;
* **`GET /v1/me`** — кто вызывает, что ему можно, что умеет сервер;
* **живые уведомления** — server-sent events;
* **песочницу исполнения** — фабулы исполняются прямо в сервере; кубики, события и
  часы управляются через API.

Сервер нужен, чтобы веб-морду можно было разрабатывать без боевой инфраструктуры.
Хранилище пока в памяти: данные живут до рестарта.

## Запуск

Вне Arcadia:

```bash
pip install fastapi uvicorn pyyaml jsonschema pydantic
python -m ai.gena.services.fabula.server --config ai/gena/services/fabula/server/examples/dev.yaml
```

Другие способы:

* **Arcadia:** `ya make ai/gena/services/fabula/server/bin`, затем
  `fabula-server --config …/examples/dev.yaml`.
* **Фабрика для uvicorn:**
  `FABULA_SERVER_CONFIG=…/dev.yaml uvicorn --factory ai.gena.services.fabula.server.app:app_from_env --no-access-log --timeout-graceful-shutdown 5`.
  `--no-access-log` нужен потому, что журнал доступа uvicorn пишет query-строку, а в
  ней может быть токен потока. Свой журнал сервер пишет без query.

После запуска:

* Swagger UI — `http://127.0.0.1:8080/docs`;
* ReDoc — `/redoc`;
* схема — `/openapi.json`.

Пример `examples/dev.yaml`:

* dev-аутентификация: все пользователи — `admin`;
* CORS для `localhost:5173` и `localhost:3000`;
* каталог демо-кубиков;
* сценарий `demo/order` в трёх версиях, `trunk` → 1.0.0:
  * **1.1.0** переименовывает ожидание отгрузки — фабулы переходят на лету по
    подсказке маппинга;
  * **2.0.0** меняет платёжный кубик — фабулы, ждущие оплату, блокируются;
* встроенный агент разрешения конфликтов.

Жизненный цикл за пять вызовов:

```bash
B=http://127.0.0.1:8080; J='content-type: application/json'
curl -s -X POST $B/v1/fabulas -H "$J" -d '{"request_id":"o-1","scenario":"demo/order","input":{"order":"A-1"}}'
# резерв завершится сам через 2 с; оплату завершает человек:
curl -s "$B/v1/sandbox/invocations?state=pending"
curl -s -X POST $B/v1/sandbox/invocations:complete -H "$J" \
  -d '{"invocation_id":"<id из списка>","outcome":{"status":"ok","output":{"paid":true}}}'
curl -s -X POST $B/v1/sandbox/events -H "$J" -d '{"type":"shop.order.shipped","data":{"order":"A-1"}}'
curl -sN "$B/v1/notifications:stream"        # в соседнем терминале: всё это вживую
```

## Настройки

Настройки — один YAML-файл. Относительные пути в нём считаются от файла. Переменные
окружения `FABULA_SERVER_HOST` и `FABULA_SERVER_PORT` перекрывают адрес, `--host` и
`--port` — тоже. Ошибка в файле останавливает запуск с указанием места.

| Секция | Что задаёт |
|---|---|
| `http` | `host`, `port`, `root_path` (за прокси), `cors_origins`, `max_body_bytes` (4 МиБ), `docs`, `allowed_hosts`, `shutdown_seconds` (5) |
| `auth` | `mode: tokens\|dev`, `dev_actor`, `tokens` (sha256 → актор), `roles`, `grants` |
| `catalog` | `files` с `{capabilities, events}` и такие же списки прямо в файле |
| `sandbox` | `behaviours` кубиков, `expose_api`, `tick_seconds`, `settle_timeout_seconds`, `delivery_attempts` |
| `seed` | `scenarios_dir` — документы, публикуемые при старте в порядке имён |
| `background` | `reconcile_seconds` (починка индекса, 0 — выкл.), `agents` (встроенные агенты) |
| `stream` | `buffer`, `client_queue`, `heartbeat_seconds` |

Без файла сервер безопасен, но бесполезен: режим `tokens`, токенов и выдач нет, так
что прав нет ни у кого.

## Аутентификация и права

* **`tokens`** — `Authorization: Bearer <токен>`. В файле хранится только sha256
  токена:
  `python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <токен>`.
* **`dev`** — актор берётся из заголовка `X-Fabula-Actor: user:alice` (или `agent:…`,
  `service:…`), без заголовка — `dev_actor`. Только для разработки: при старте в лог
  пишется предупреждение. Чужой сайт не может обратиться к dev-серверу от имени
  `dev_actor`:
  * тело должно быть объявлено как JSON, а форма или `no-cors`-запрос этого не умеют;
  * сервер отвечает только своим именам хоста (`http.allowed_hosts`, в dev-режиме по
    умолчанию `localhost`, `127.0.0.1` и `::1`), так что DNS rebinding не проходит.
    Вне dev-режима по умолчанию разрешён любой хост.
* **Боевой адаптер** (OAuth, Blackbox, TVM) — это реализация порта `Authenticator`
  (`auth.py`); HTTP-слой менять не нужно.

Права выдаются ролями. `grants` сопоставляет шаблон актора (`user:alice`, `agent:*`,
`*`) списку ролей; роли всех подходящих шаблонов складываются. Встроенные роли
(`roles` дополняет или переопределяет их):

| Роль | Права |
|---|---|
| `viewer` | `scenario.read`, `fabula.read`, `migration.read`, `audit.read` |
| `author` | `scenario.read`, `scenario.write`, `scenario.publish`, `tag.write` |
| `operator` | `fabula.read`, `fabula.start`, `fabula.control`, `migration.read`, `migration.write`, `resolution.approve` |
| `resolver` | `scenario.read`, `fabula.read`, `migration.read`, `resolution.work` |
| `sandbox` | `sandbox.operate` |
| `admin` | все |

`GET /v1/me` не требует прав. Он возвращает актора, роли, права на `*` и возможности
сервера (`sandbox`, `auth_mode`, `storage`, `expressions`). По этому ответу морда
прячет недоступные действия.

## Соглашения API для веб-морды

* **Тела и ответы — JSON.** Тело обязано иметь `content-type: application/json` (или
  `*+json`), иначе 415. `NaN` и `Infinity` — не JSON, они дают 400 `invalid_json`.
* **Ошибки** — RFC 7807 (`application/problem+json`) с устойчивым `code`:
  `invalid_input`, `forbidden`, `version_exists`, `invalid_scenario` с `diagnostics` и
  т. д. Ошибки транспорта:
  * 400 `invalid_json` и `untrusted_host`;
  * 401 `unauthenticated`;
  * 404 `route_not_found`;
  * 405 `method_not_allowed` с `Allow`;
  * 413 `body_too_large`;
  * 415;
  * 500 `internal` с `trace_id`, без деталей.
* **Идемпотентность.** Мутации принимают `request_id` — идентификатор, который
  выбирает клиент; повтор безопасен. Изменения с блокировкой принимают
  `expected_revision`.
* **Трассировка.** `X-Trace-Id` принимается от клиента или создаётся и возвращается в
  каждом ответе. Это не `request_id`.
* **`X-Fabula-Operation`** в ответе называет `operationId` из схемы, в том числе при
  401, 413 и 415.
* **`HEAD`** работает на всех GET-операциях API, на `/healthz`, `/readyz` и
  `/openapi.json`.
* **Кастомные методы** пишутся через двоеточие: `/v1/migrations/{id}:apply`,
  `/v1/scenarios:validate`.
* **Пагинация** — `page_size` и `page_token`.
* **Сжатие.** Ответы больше 1 КиБ сжимаются gzip; потоки не сжимаются.

## Живые обновления

`GET /v1/notifications:stream` — это server-sent events. Формат одного события:

```
id: 3f2a….42
event: fabula.changed
data: {"kind":"fabula.changed","resource":"fabula:f-…","at":"…","data":{"journal_version":3,"status":"waiting"}}
```

* **Виды событий:**
  * от менеджера — `version.published`, `tag.moved`, `campaign.awaiting_decision`,
    `resolution.task_open`, `proposal.awaiting_approval` и другие;
  * `fabula.changed` после каждого шага фабулы. Данных фабулы в нём нет: клиент
    перечитывает `GET /v1/fabulas/{id}`.
* **Фильтры:** `?kind=fabula.*&kind=tag.moved` (шаблоны) и `?resource=fabula:f-1` (по
  префиксу ресурса).
* **Переподключение.** `EventSource` сам присылает `Last-Event-ID`, и сервер досылает
  пропущенное, пока оно есть в буфере. Позиция клиента двигается, даже когда показывать
  нечего. Для этого сервер шлёт блоки из одного `id:` (событие по ним не срабатывает):
  в начале потока и после уведомлений, которые клиент не получил из-за фильтра или
  прав. Если досылать нечего (буфер переполнен, сервер
  перезапущен, клиент отстал), приходит `event: reset` — нужно перечитать то, что
  показано.
* **Права.** Актор видит только события о ресурсах, которые ему можно читать.
* **Keep-alive.** Пока событий нет, раз в 15 с приходит строка-комментарий.
* **Браузерный `EventSource`** не умеет ставить заголовки. Поэтому поток принимает
  `?access_token=` (режим tokens) или `?actor=` (режим dev).

```js
const events = new EventSource(`${api}/v1/notifications:stream?kind=fabula.*&access_token=${token}`);
events.addEventListener("fabula.changed", (e) => refetchFabula(JSON.parse(e.data).resource));
events.addEventListener("reset", () => refetchEverything());
```

## Песочница

В профиле «в памяти» фабулы исполняет эталонный `Host` движка прямо в сервере. Эффекты
— in-memory реализации движка; результаты доставляет фоновый воркер. Кубики ведут себя
по `sandbox.behaviours`. Ключ — ref кубика или glob; точный ref важнее glob.

| Режим | Что делает |
|---|---|
| `manual` (по умолчанию) | ждёт `POST /v1/sandbox/invocations:complete` |
| `echo` | возвращает вход |
| `ok` | возвращает `output` |
| `error` | падает с `error_type` / `error_title` |
| `after_seconds` | модификатор: результат приходит через столько секунд реального времени |

API песочницы (право `sandbox.operate`; убирается через `sandbox.expose_api: false`):

| Операция | Что делает |
|---|---|
| `GET /v1/sandbox` | часы, сдвиг, очередь доставок, таймеры, подписки, dead letters |
| `GET /v1/sandbox/invocations?state=&fabula_id=&capability=` | вызовы кубиков |
| `POST /v1/sandbox/invocations:complete` | результат вызова. Id вызова содержит `/`, поэтому он передаётся в теле. Повтор с тем же исходом безопасен, с другим — 409 |
| `POST /v1/sandbox/events` / `GET /v1/sandbox/events` | опубликовать внешнее событие (тип проверяется по каталогу) / журнал шины: внешние события и события фабул |
| `POST /v1/sandbox/clock:advance` | `{"by": "PT1H"}`: часы уходят вперёд, наступившие таймеры срабатывают |
| `POST /v1/sandbox:settle` | дождаться, пока доставки дойдут до фабул (для e2e-тестов морды) |

Мутации песочницы перед ответом сами ждут доставки. Поэтому после
`invocations:complete` фабула уже продвинулась. Если доставка не уложилась в
`settle_timeout_seconds`, ответ — 503 `not_settled`. Если хост отверг доставку — 500
`delivery_failed`, а доставка уходит в dead letters. Само изменение в обоих случаях
сделано, и повторять его безопасно.

Выражения. Адаптера к настоящему jq в движке пока нет, поэтому песочница использует
тестовое jq-подмножество движка. В `/v1/me` это видно как
`server.expressions: "test-subset"`. Демо-сценарии укладываются в него.

## Схема и клиент

* **`/openapi.json`** — OpenAPI 3.1: операции менеджера, `/v1/me`, песочница, поток,
  health. Плюс `securitySchemes` (`bearer`, `devActor`) и ответы 401/413/415/500.
* **Снапшот** лежит в `openapi.json` рядом; тест сверяет его с генерацией. Обновить:
  `python -m ai.gena.services.fabula.server.openapi > ai/gena/services/fabula/server/openapi.json`.
* **Клиент для морды** генерируется из снапшота, например
  `npx openapi-typescript openapi.json -o api.d.ts`.

## Устройство

```
settings.py       настройки и каталог
auth.py           порт Authenticator, dev и токены, ConfiguredAuthorizer (роли)
session.py        GET /v1/me
notifications.py  NotificationHub (Notifier менеджера) и NotifyingJournalSink
sandbox/          часы, эффекты и воркер доставок, Sandbox (Host движка), API песочницы
operations.py     таблица операций сервера: менеджер + сессия + песочница
composition.py    сборка профиля «в памяти»
background.py     починка индекса и встроенные агенты
seed.py           публикация сценариев при старте
app.py            FastAPI: lifespan, middleware, маршруты
routes.py         маршруты из таблицы операций → Dispatcher.dispatch
errors.py, middleware.py, dependencies.py, stream.py, service_routes.py
openapi.py        схема сервера
cli.py            fabula-server (uvicorn)
testing/          стенд для тестов: приложение, HTTP-клиент по ASGI, читатель SSE
```

Архитектурный тест проверяет правила:

* FastAPI и Starlette импортируют только HTTP-модули, uvicorn — только `cli`;
* in-memory реализации из `testing`-пакетов импортируют только `composition.py`,
  `sandbox/` и `testing/`: боевой профиль встанет рядом;
* движок и менеджер ничего не знают о сервере.

Правка в менеджере одна, обратно совместимая: `Dispatcher` и `build_openapi`
принимают таблицу операций, а `permission=None` значит «достаточно аутентификации».

## Решения, принятые без спроса

1. **Маршруты FastAPI генерируются из таблицы операций.** Валидация, права и ошибки
   остаются в `Dispatcher`, поэтому HTTP и API без транспорта не расходятся.
   Встроенная генерация OpenAPI в FastAPI выключена: схема одна, наша.
2. **Один маршрут на путь.** 405 отдаётся с полным `Allow`; `HEAD` отвечает на
   `GET`-маршрутах.
3. **Параметр пути не содержит `:`** — двоеточие начинает кастомный метод.
   Исключение — хвостовой `{request_id}`, как в `Dispatcher.route`. Паритет
   проверяет тест.
4. **`X-Trace-Id`, а не `X-Request-Id`** — чтобы не путать с `request_id`
   идемпотентности.
5. **Сбой внутри операции превращается в 500 прямо в маршруте.** Так ответ проходит
   через CORS и трассировку; стек пишется в лог, клиент получает только `trace_id`.
6. **Без настроек никто ничего не может.** Режим `dev` включается только явно.
7. **Токены задаются своим sha256** и сравниваются за постоянное время.
8. **Права пока по ролям на всё** (`resource` не учитывается). Порт `Authorizer`
   позволит потом выдавать права на пространство имён или сценарий.
9. **Живые обновления — SSE, а не WebSocket.** Поток однонаправленный, проходит через
   прокси, `EventSource` сам переподключается.
10. **`fabula.changed` на каждый записанный переход.** Это обёртка над журналом хоста,
    без данных фабулы: клиент их перечитывает.
11. **Id уведомления — `<boot>.<seq>`.** Буфер хранит 1000 уведомлений; при потере
    приходит `reset`, а не молчаливая дыра.
12. **Песочница собрана из `Host` и in-memory эффектов движка.** Они уже проверены
    его тестами. Одна задача доставляет результаты по порядку. Доставку, которую хост
    не принимает 3 раза подряд, песочница откладывает в dead letters, чтобы она не
    держала остальные.
13. **Мутации песочницы дожидаются доставки**, часы песочницы идут только вперёд, а
    событие неизвестного типа даёт 422.
14. **Встроенные агенты — детерминированный `HintResolver`**, без LLM: режимы
    `agent_*` можно показать в морде.
15. **Seed идемпотентен** (`request_id` — хеш текста). Невалидный документ
    останавливает старт.
16. **`/healthz`, `/readyz`, `/openapi.json` и `/docs` доступны без
    аутентификации.**
17. **Тело принимается только с JSON-типом**, а сервер отвечает только разрешённым
    хостам. Это защита dev-режима от чужих сайтов.
18. **При остановке открытые потоки получают `http.shutdown_seconds`** (5 с), потом
    отменяются. Иначе uvicorn ждал бы их бесконечно. Поток сам следит за
    отключением клиента: под ASGI 2.4 Starlette этого не делает.
19. **`/readyz` отвечает 503, пока не закончился старт.** Uvicorn сам не принимает
    соединения до конца старта; проверка нужна для серверов, которые принимают их
    раньше.

## Ограничения и что дальше

* **Хранилище в памяти.** Для базы нужна реализация портов `manager/ports/stores.py`
  и `FabulaStore` движка и новый `build` рядом с `composition.py`.
* **Один инстанс.** Хаб уведомлений и песочница живут в процессе. Для нескольких
  инстансов нужны шина уведомлений и настоящая среда исполнения.
* **Выражения** — тестовое подмножество jq, пока нет адаптера к jq.
* **Боевая аутентификация и права по ресурсам** — реализации портов `Authenticator` и
  `Authorizer`.
* **Долгие операции** (`:apply` большой кампании) выполняются внутри запроса. Их
  размер ограничивает `max_items`.

## Тесты

`server/tests`:

* **architecture** — правила импортов;
* **http** — маршруты и паритет с `Dispatcher.route`, ошибки, CORS, gzip, готовность,
  аутентификация и роли, OpenAPI против снапшота и против маршрутов приложения;
* **live**:
  * сквозной сценарий по HTTP: публикация → старт → песочница → новая версия →
    миграция;
  * песочница: поведения, события, часы, тикер, dead letters;
  * поток: фильтры, продолжение, `reset`, права, heartbeat;
  * фон: пример настроек, seed, встроенный агент, живучесть задач;
* **units** — настройки, правила прав, хаб уведомлений.
