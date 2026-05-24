Idea, Forked: https://github.com/Amnam01

Spotify → Discord Status Lyrics

Скрипт который синхронно показывает текст текущей песни в статусе Discord.

Как это выглядит

Пока играет музыка — в твоём Discord статусе автоматически прокручиваются строки текста песни в реальном времени.

Требования

- Windows 10/11
- Python 3.12+
- Приложение [Spotify для Windows](https://spotify.com)
- Discord (десктопное приложение)

Установка

1. Клонируй репозиторий:
```bash
git clone https://github.com/NOVAPBS7/yandex-discord-lyrics.git
cd yandex-discord-lyrics
```

2. Создай виртуальное окружение и установи зависимости:
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

3. Получи Discord токен:

- Открой Discord, нажми `Ctrl+Shift+I`
- Перейди во вкладку **Network**
- Переключи любой канал
- Найди запрос на `discord.com/api` → **Headers** → `Authorization`

4. Вставь токен в `main.py`:
```python
DISCORD_TOKEN = "твой_токен_здесь"
```

Запуск

```bash
.\.venv\Scripts\python.exe main.py
```

Как работает

- Читает текущий трек через **Windows Media Session API** (SMTC) из приложения Spotify
- Загружает синхронизированный текст с **[lrclib.net](https://lrclib.net)** (бесплатно, без ключа)
- Обновляет статус Discord через **HTTP API** каждую секунду
- При паузе — статус очищается автоматически

Зависимости

```
aiohttp
winrt-runtime
winrt-Windows.Media.Control
winrt-Windows.Foundation
winrt-Windows.Foundation.Collections
```

Ограничения

- Работает только на **Windows**
- Текст доступен не для всех треков (зависит от наличия на lrclib.net)
- Использует пользовательский токен Discord — используй на свой риск

Лицензия

MIT
