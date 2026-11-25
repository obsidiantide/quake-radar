# Quake Radar — землетрясения Кавказа и Каспия

Семестровый проект по курсу **Data Engineering**. Пайплайн каждые 30 минут собирает
землетрясения региона из двух сейсмологических агентств (EMSC, USGS), сводит
дубли между ними, обогащает данными о ближайших городах (GeoNames), проверяет
качество и публикует сайт: обзор, тревоги о заметных толчках рядом с Баку, карточки событий и мониторинг пайплайна.

**Пользователь:** семья и друзья в Баку, которые хотят быстро понять, что это был
за толчок: где, какой силы и насколько далеко от дома.

- Дашборд: https://quake-radar-baku.vercel.app
- Data lake: ветка [`data`](../../tree/data)
- Запуски: вкладка **Actions** → `pipeline`

## Архитектура

```
 EMSC WebSocket (поток) ──┐                    ┌──────────── GitHub Actions cron */30 ───────────┐
 EMSC FDSN REST (батч) ───┼─> raw/ (gzip JSON, │ extract_* ─> transform ─> quality_checks ─> publish│
 USGS FDSN REST (батч) ───┘   как есть +       │                 │ fail: блок, продукт не меняется │
 GeoNames cities15000.zip ──> sha256, params)  └─────────────────────────────────────────────────┘
                                   │
                stage/<src>/month=YYYY-MM.parquet   (типизировано, последняя ревизия события)
                                   │  sql/unify_events.sql: сведение EMSC↔USGS + ближайший город
                final/events/month=YYYY-MM.parquet  (итоговая таблица)
                                   │
                serve/*.json ──> сайт на Vercel       сбой ──> GitHub issue
```

| Слой | Где | Формат | Кто пишет |
|---|---|---|---|
| raw | `raw/<source>/dt=YYYY-MM-DD/HHMMSS_<run>.json.gz` + `.meta.json` | ответ источника без изменений | extract, stream |
| stage | `stage/<source>/month=YYYY-MM.parquet`, `stage/cities.parquet` | типизированные строки | transform |
| final | `final/events/month=YYYY-MM.parquet` | одна строка на землетрясение | transform → publish |
| serve | `serve/{events,daily,health,runs}.json` | маленькие JSON для дашборда | export |
| meta | `meta/state.json`, `meta/runs.jsonl`, `meta/quarantine.jsonl` | состояние, журнал запусков | pipeline |

## Быстрый старт

Нужен Docker.

```bash
docker compose up --build                                  # весь стек: планировщик, поток, дашборд на :8765
docker compose run --rm pipeline run                       # один прогон пайплайна от сырых данных
```

Без Docker (Python 3.11):

```bash
pip install -r requirements.txt
python -m quake run                                        # инкрементальный прогон
python -m quake serve                                      # дашборд: http://localhost:8080
pytest -q                                                  # тесты логики преобразований
```

## Команды

| Команда | Что делает |
|---|---|
| `python -m quake run` | инкрементальный прогон (его же вызывает cron) |
| `python -m quake run --inject-bad` | демо: подмешивает запись с `mag=42, lat=999`, проверка `value_ranges` блокирует публикацию |
| `python -m quake run --break-source usgs` | демо: эмулирует падение USGS, запуск `partial`, данные EMSC публикуются |
| `python -m quake backfill --start 2026-08-01 --end 2026-11-01` | перезагрузка прошлого периода, идемпотентно |
| `python -m quake stream` | слушатель EMSC WebSocket, микробатчи в raw каждые 60 с |
| `python -m quake status` | свежесть источников, объёмы, сбои за 14 дней |
| `python -m quake rebuild` | пересобрать stage и final из raw (например, после смены схемы) |

## Как устроена инкрементальность

Два указателя в `meta/state.json`:

1. **watermark источника**: максимальное `updated` из последнего ответа. Следующий запрос идёт с
   `updatedafter = watermark − 6 ч`, потому что агентства пересматривают магнитуду уже после публикации.
   Watermark двигается сразу после записи в raw: данные уже сохранены и не потеряются.
2. **указатель трансформации**: последний обработанный raw-файл. Имена файлов сортируются по времени,
   поэтому достаточно одной строки. Двигается только после `publish`: если проверки упали, следующий
   запуск обработает те же файлы ещё раз.

Перекрытие окон даёт повторные записи. Stage делает upsert по `source_event_id` и оставляет последнюю
ревизию, поэтому повторный запуск и повторный backfill дублей не создают (это покрыто тестами).

## Проверки качества (блокируют публикацию)

| Проверка | Правило |
|---|---|
| `not_null_keys` | `event_id`, `event_time`, `lat`, `lon`, `mag` заполнены |
| `value_ranges` | координаты внутри региона, магнитуда −1…10, глубина −10…800 км |
| `unique_event_id` | одна строка на событие (сведение отработало) |
| `no_future_events` | нет событий из будущего (ошибки часовых поясов) |
| `daily_volume` | нет дня с числом событий больше 10× медианы (минимум 200), иначе сломан фильтр или двойная загрузка |
| `enrichment_complete` | у каждого события есть ближайший город и расстояние до дома |

Структурно битые записи (нет id или времени) уходят в `meta/quarantine.jsonl` и не роняют запуск.

## Наблюдаемость

- Каждый запуск пишет строку в `meta/runs.jsonl`: статус каждой задачи, длительность, строки и байты по источникам, результаты проверок, ошибки.
- `serve/health.json` и дашборд показывают последний запуск, свежесть каждого источника (устарел, если нет успешной загрузки 3 часа), объёмы хранилища, сбои за 14 дней.
- Алерт оператору: issue с меткой `pipeline-alert` открывается при переходе ok → сбой и закрывается при восстановлении (через `GITHUB_TOKEN` в Actions), без спама каждые 30 минут.

## Деплой

- **Оркестрация:** GitHub Actions, `.github/workflows/pipeline.yml`, cron `*/30`, ручной запуск с параметрами для демо.
- **Хранилище:** ветка `data` этого репозитория, файлы с понятной структурой; история git = история данных.
- **Дашборд:** статический сайт `web/` на Vercel, данные читает из ветки `data`.
- **Секреты** не нужны: в Actions используется встроенный `GITHUB_TOKEN`.

## Источники данных

| Источник | Тип | Доступ | Лицензия | Дата подключения |
|---|---|---|---|---|
| [EMSC FDSN event](https://www.seismicportal.eu/fdsn-wsevent.html) | REST API, GeoJSON | без ключа | открытые данные EMSC | 2026-11-25 |
| [EMSC WebSocket](https://www.seismicportal.eu/realtime.html) | поток | без ключа | открытые данные EMSC | 2026-11-25 |
| [USGS FDSN event](https://earthquake.usgs.gov/fdsnws/event/1/) | REST API, GeoJSON | без ключа | public domain | 2026-11-25 |
| [GeoNames cities15000](https://download.geonames.org/export/dump/) | файл (zip/TSV) | без ключа | CC BY 4.0 | 2026-11-25 |

Персональных данных пайплайн не собирает. Запросы к API идут с собственным User-Agent и не чаще раза в 30 минут.

## Библиотеки

DuckDB 2.0 (SQL-трансформации, Parquet), requests, websockets, pytest; на фронтенде Leaflet 1.9, Chart.js 4.4, подложка карты Esri.

## Использование ИИ

Код пайплайна, тесты, дашборд и черновик отчёта написаны с помощью Claude (Anthropic) по моему
дизайну и проверены мной. Данные и факты в отчёте взяты только из запусков пайплайна, не из ИИ.
