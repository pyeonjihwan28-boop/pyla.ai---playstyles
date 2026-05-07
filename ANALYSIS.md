# PylaAI — анализ кода и план улучшений

Анализ форка `mistaste/PylaAI` (склонировано из upstream `PylaAI/PylaAI`).
Дата: 2026-05-07. Версия на момент анализа: `0.6.5`.

---

## Архитектура: что есть на самом деле

**Главный цикл** (`main.py:101`) — один поток, синхронный pipeline на каждый кадр:

```
screenshot → state_check → main_info_detect → tile_detect → movement → input
```

Никаких потоков, никаких очередей. Захват кадров от scrcpy идёт в фоне
(`window_controller.py:71` `on_frame`), но всё остальное — последовательно.

**FSM** размазан между `stage_manager.states` (dict из 9 состояний → методы) и
`state_finder.get_state()` (template matching по 9 PNG в регионах).
Переходов как таковых нет — каждый кадр заново классифицируется с нуля.

**Vision**: 4 ONNX модели в `models/`, но в коде грузятся только 2
(`mainInGameModel.onnx` для player/enemy/teammate и `tileDetector.onnx` для стен).
Остальные две (`brawlersInGame.onnx`, `startingScreenModel.onnx`) лежат мёртвым грузом.

**Управление** через scrcpy + adbutils → виртуальный тач по фиксированным координатам
под 1920×1080.

---

## Конкретные проблемы (по убыванию серьёзности)

### 1. Производительность

- **`detect.py:122`**: `torch.from_numpy(outputs[0])` + `non_max_suppression` из
  `ultralytics` тянет весь PyTorch ради одной функции NMS. На CPU-провайдере это
  удваивает время. Решение: `cv2.dnn.NMSBoxes` или ручной NMS на numpy → можно
  вообще убрать `torch` из inference-пути и из requirements (оставить только
  под обучение).
- **`detect.py:36-40`**: буфер `_padded_img_buffer` хороший, но **не сбрасывается**
  между кадрами. Если предыдущий кадр был меньше — в нём останутся «хвосты»
  прошлой картинки за пределами `:new_h, :new_w`. Тихий баг, влияющий на детект.
- **`state_finder.py:131`**: `cv2.cvtColor(screenshot, COLOR_RGB2BGR)` каждый кадр +
  до 9 `matchTemplate` подряд. На каждом кадре. Кэш шаблонов есть
  (`cached_templates`), но сам matchTemplate × 9 — дорого.
- **`main.py:127`**: `screenshot()` блокирующий с busy-wait в 100мс при отсутствии
  кадра (`window_controller.py:119-127`). Должен быть event-based на `frame_lock`
  + Condition.
- **`utils.py:63`**: `easyocr.Reader(['en'])` инициализируется при импорте
  `utils.py` — а это первый импорт **везде**. Каждый запуск тянет ~500MB модели
  в память, даже если OCR нужен только для `select_brawler`. Должен быть lazy.
- **Каждый `is_*` чек в state_finder делает `is_template_in_region` отдельно**,
  что внутри ещё раз ресайзит `cropped_image`. Один-единственный ресайз кадра
  до канонических 1920×1080 в начале — и убрать `width_ratio/height_ratio`
  арифметику из 20 мест.

### 2. FSM реализован неверно

- **`stage_manager.py:174`**: `while current_state.startswith("end") and time.time() - end_screen_time < 25` —
  внутри обработчика состояния крутится свой собственный цикл, блокируя главный
  loop на 25 секунд. За это время бот **не дышит**: не реагирует на новые кадры,
  не двигается, scrcpy frames копятся.
- **`stage_manager.py:142-148`**: ещё один блокирующий `while` с `time.sleep(1) × 30`
  = **до 30 секунд блокировки**.
- **`main.py:64-80`** `restart_brawl_stars` создаёт новый event_loop каждый раз —
  `asyncio.new_event_loop` + `set_event_loop` + `close()`. Должен быть один общий
  loop в фоновом потоке.
- Состояние `match` в `states` dict = `lambda: 0` — мёртвый код, фактически ветка
  обрабатывается отдельно через `play.main`. Это нечитаемо.

### 3. Реальные баги

- **`utils.py:15-17`**: `import easyocr` + `reader = DefaultEasyOCR()` на module
  level → импорт `utils` ≈ 5–15 секунд на холодном старте. И валится, если
  `easyocr` не установлен, даже когда OCR не нужен.
- **`state_finder.py:34`**: строка `current_height, current_width = image.shape[:2]`
  дублируется (уже была на строке 27) и переопределяет переменные после
  использования — leftover.
- **`state_finder.py:39`** порог 0.7 для template matching хардкоден, разные
  шаблоны должны иметь разные пороги. Например, иконки победы/поражения светятся
  анимированно — там 0.7 ловит ложняки.
- **`play.py:466-475`** `for...else` с inner `for...else` — если все альтернативные
  направления заблокированы, переменная `movement` определится только при попадании
  в `else` внешнего for; во внутреннем `for` если ни один не пройдёт —
  `movement = move_horizontal + move_vertical` записывается (двойной else).
  Логика рабочая, но трудночитаемая.
- **`play.py:492-506`**: super activation: `is_super_ready` сбрасывается **сразу**
  после `use_super()`, но без подтверждения что суперудар реально был использован
  (анимация супера ещё не началась). Если фрейм пришёл в момент когда супер ещё
  доступен — бот его не нажмёт следующие `super_treshold` секунд. Должно быть
  подтверждение через повторный pixel check.
- **`stage_manager.py:69-71`**: `'end_draw'`, `'end_victory'`, `'end_defeat'` все
  вызывают `self.end_game`, но `end_game` внутри **снова** вызывает
  `find_game_result(screenshot)` и парсит результат из строки
  `current_state.split("_")[1]` — двойная работа.
- **`window_controller.py:51`**: при поиске устройств сканит порты `5565..5755`
  шагом 10 + захардкоженные → может зависать на блокирующих `adb.connect`
  до 20+ секунд.

### 4. Безопасность / приватность

- **`utils.py:148, 209-241`**: webhook URL и discord_id хранятся в
  `cfg/general_config.toml` в открытую — если пользователь зашерит конфиг,
  у любого будет возможность пинговать его и видеть его id. Должно быть либо
  в keyring, либо хотя бы предупреждение в README.
- **`utils.py:130-185`** `update_missing_brawlers_info` молча скачивает PNG
  с `api.brawlapi.com` без проверки content-type/размера. Не уязвимость,
  но нет лимитов.
- **`api/api.py`** не читался — отдельная проверка.

### 5. Конфиг и DX

- **`cfg/general_config.toml`** парсится через `load_toml_as_dict` 30+ раз с
  runtime кэшем по path — но сам кэш не thread-safe и не реагирует на изменения
  файла. Конфиг должен быть один объект, переданный через DI.
- **`detect.py:17`**: debug-флаг читается на module level — изменить его без
  перезапуска нельзя.
- **`requirements.txt` vs `setup.py`** содержат **разные версии** opencv
  (`<4.10.0` в setup, `~=4.11.0.86` в requirements) — это ломает воспроизводимость.
- **Нет логгера**, везде `print()`. Нет уровней, нет ротации, в release-сборке
  UI забивается стдаутом.
- **Нет тестов** реально работающих с моделями: `tests/lobby_automation/` —
  только OCR на сохранённых ассетах, никаких golden-тестов на детектор.

### 6. Vision-специфика

- **`play.py:533-541`**: walls детектятся раз в `walls_treshold` секунд (по умолчанию 1с),
  но в перерывах используются **старые координаты стен** даже если игрок (и стены
  относительно него) переместились. Стены в Brawl Stars статичны → нужно детектить
  **карту один раз** в начале матча и кэшировать, а не каждую секунду гонять модель.
- **Нет tracking'а врагов**. Если враг на 1 кадр пропал из детектов
  (ушёл в куст / occlusion) — бот мгновенно переходит в `no_enemy_movement`.
  ByteTrack/SORT с persistance ~10 кадров решит это.
- **`play.py:219-236`** `walls_block_line_of_sight`: `cv2.clipLine` по bbox стены —
  но стена это не прямоугольник, это тайл с возможной формой. Точность OK,
  но false positives на диагональном огне.
- **`play.py:447-460`**: game_mode хардкоден как 3 / 5 (что это?). Магические числа
  без enum.

---

## Топ-10 квик-винов (порядок по соотношению эффект/трудозатраты)

1. **Lazy import easyocr** — 1 строка, экономит 5–15 сек на каждый запуск.
2. **Убрать torch из inference-пути** — заменить ultralytics NMS на
   `cv2.dnn.NMSBoxes`. -300MB зависимостей, +20–30% FPS на CPU.
3. **Кэшировать карту стен на матч** вместо детекта каждую секунду —
   десятки процентов FPS.
4. **Вынести 25-секундные блокирующие циклы из end_game в state-machine**
   через флаги `awaiting_end_screen` + main loop tick.
5. **Перевести `screenshot()` на Condition.wait()** вместо busy-wait.
6. **Один общий asyncio event loop в фоне** для webhook'ов.
7. **`logging` вместо `print`** — день работы, окупается навсегда.
8. **Pydantic-конфиг** загружается один раз, передаётся в DI.
9. **ByteTrack** на детекциях врагов — стабильность, +winrate без обучения
   новой модели.
10. **Bbox padding bug fix** в `_padded_img_buffer`.

---

## План для v2 (на основании реального кода)

Не «переписать всё в Rust». Реалистично:

1. **Шаг 1: рефакторинг в пакет** + тесты + logging + pydantic-config.
   Без смены логики.
2. **Шаг 2: производительность** — async pipeline (capture thread → infer thread →
   action thread с asyncio.Queue), убрать torch из инференса, кэш стен на матч.
3. **Шаг 3: tracking + memory** — ByteTrack для врагов, мини-карта матча в памяти.
4. **Шаг 4: vision unification** — заменить 9 template-matching чеков на одну
   классификационную голову модели состояний экрана.
5. **Шаг 5 (опционально, R&D)** — imitation learning policy network, заменяющий
   `play.get_movement` эвристики.

---

## Глобальная v2 — амбициозная пересборка

### 1. Сменить парадигму: от scripted FSM к RL-агенту

Сейчас бот — это «if вижу X → жми Y». Глобальный апгрейд:

- **Imitation learning** на записях геймплея топ-игроков (захват экрана + инпуты
  → датасет state→action).
- **Поверх — RL fine-tune** (PPO/DreamerV3) в self-play против самого себя
  в эмуляторе.
- Один end-to-end policy-network вместо десятка эвристик в `play.py` /
  `stage_manager.py`. Это даёт скачок по адаптивности к новым бойцам/картам
  без ручного кода.

### 2. Перенести inference-ядро на нативный рантайм

Python остаётся как «оркестратор и GUI», но горячий путь
(capture → preprocess → inference → action) — на C++/Rust:

- **Rust core** с `ort` (ONNX Runtime) + `windows-rs` для DXGI capture и SendInput.
- IPC к Python через shared memory или ZeroMQ.
- Цель: стабильные 120+ FPS pipeline и <10 ms end-to-end latency.
  Текущий чистый Python такого не даст.

### 3. Vision: единая мульти-задачная модель

Сейчас отдельные модели/эвристики на детект бойцов, состояний меню, трофеев.
Заменить на:

- **Один backbone (RT-DETR / YOLOv11 / RTMDet)** с несколькими головами:
  detection (units), segmentation (walls/bushes), classification (game state),
  OCR (счёт/трофеи).
- Снимает зоопарк моделей в `models/`, упрощает деплой и квантизацию.

### 4. Картовая память и планирование

Большой пробел в external-ботах — отсутствие памяти.

- **SLAM-подобный модуль**: строить мини-карту (стены/кусты/спавны) из потока кадров.
- **Tracking через кусты** (Kalman/ByteTrack) — помнить, где скрылся враг.
- **A\*** / потенциальные поля поверх карты для движения вместо реактивного
  «иди на врага».

### 5. Инфраструктура «облачного» бота

- **Headless-агенты в облаке**: Windows Server + GPU + эмулятор в Docker,
  оркестрация через k8s/Nomad.
- **Telemetry pipeline**: ClickHouse + Grafana по матчам, винрейту, ошибкам детектора.
- **Live model registry** (MLflow / W&B) с A/B между версиями policy.
- **Auto-retrain loop**: новые матчи → датасет → дообучение → канареечный rollout.

### 6. Современный GUI

Папка `gui/` на чистом Tk/PyQt — заменить на:

- **Tauri (Rust + web)** или **Electron + React** с дашбордом: live-overlay
  поверх эмулятора, графики, hot-reload конфигов, маркетплейс пресетов
  «стилей игры» под бойца.
- WebSocket-канал из Rust core → UI.

### 7. Plugin-API и эко-система

- Публичный Python/Lua SDK: `on_state_change`, `on_enemy_detected`, `override_action` —
  чтобы коммьюнити писало стратегии под конкретных бойцов как плагины.
- Маркет/репозиторий плагинов с подписью (учитывая «No Selling» лицензию — бесплатный).

### 8. Кросс-платформа всерьёз

Сейчас «Windows 10/11 + WSL опционально». Глобальная версия:

- **Android-нативно** через ADB-скриншоты + Frida-инжект инпутов (без эмулятора).
- **macOS** на Apple Silicon с CoreML-бэкендом.
- Эмулятор-агностик через абстракцию `WindowController` → `InputBackend`.

### 9. Anti-detection и стелс

Раз это external-бот, рано или поздно Supercell начнёт детектить:

- Ввод через драйвер уровня ядра (Interception) вместо SendInput.
- Рандомизация таймингов на основе человеческих распределений
  (логнормаль реакции, не равномерный шум).
- Захват не GDI/DXGI, а через виртуальную камеру эмулятора.

### 10. Лицензия и юр-структура

Перед v2 — нормальный SPDX (PolyForm Noncommercial 1.0.0), CLA для контрибьюторов,
и отделение «open core» (vision+framework) от «closed strategies» если хочется.

---

## Порядок работы в форке

1. Бамп версии `pyla_version` в `cfg/general_config.toml` перед каждым commit.
2. Атомарные коммиты (один логический change на коммит).
3. Push в `origin` (mistaste/PylaAI) после каждого зелёного коммита.
4. Ветки: `refactor/v2-foundation`, `feat/lazy-easyocr`, `perf/no-torch-nms` и т.д.
