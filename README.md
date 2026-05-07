# Sleewave Backend

Бэкенд для музыкального приложения Sleewave. Предоставляет API для поиска, стриминга и скачивания музыки из различных источников.

## Функционал

- Поиск треков по источникам: YouTube, YouTube Music, SoundCloud, Spotify, VK
- Получение прямых ссылок для стриминга
- Скачивание треков с метадатой в MP3
- Временное хранение скачанных файлов

## API Эндпоинты

### Поиск
```
GET /search?source={source}&q={query}&limit={limit}&offset={offset}
```
- `source`: yt, ytm, sc, spotify, vk
- `q`: поисковый запрос
- `limit`: количество результатов (по умолчанию 10)
- `offset`: смещение (по умолчанию 0)

### Стриминг
```
GET /stream?source={source}&track_id={track_id}
```
Возвращает прямую ссылку на аудио поток.

### Скачивание
```
POST /download?source={source}&track_id={track_id}&output_path={path}
```
Скачивает трек в указанный путь.

### Скачивание в temp с метадатой
```
POST /download_temp?source={source}&track_id={track_id}&title={title}&artist={artist}
```
Скачивает трек во временную директорию с добавлением метаданных.

## Запуск

1. Установите зависимости:
```bash
pip install -r requirements.txt
```

2. Запустите сервер:
```bash
uvicorn app.main:app --reload
```

Сервер будет доступен на http://127.0.0.1:8000

## Структура проекта

```
app/
├── main.py              # FastAPI приложение
├── domain/
│   └── models.py        # Pydantic модели
├── interfaces/
│   └── music_provider.py # Интерфейс провайдеров
├── providers/           # Реализации провайдеров
│   ├── youtube.py
│   ├── ytmusic.py
│   ├── soundcloud.py
│   ├── spotify.py
│   └── vk.py
└── services/
    ├── music_manager.py     # Менеджер провайдеров
    └── download_service.py  # Сервис скачивания
```

## Следующие шаги

1. Реализовать полноценные провайдеры (VK API, Spotify OAuth)
2. Добавить аутентификацию пользователей
3. Добавить управление плейлистами
4. Добавить кэширование результатов поиска
5. Интеграция с Flutter приложением
6. Добавить обработку ошибок и логирование
7. Добавить тесты