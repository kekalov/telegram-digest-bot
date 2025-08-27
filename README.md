# Telegram Digest Bot

Бот для создания сводок новостей из Telegram каналов.

## Функции

- Сбор сообщений из каналов через веб-скрапинг
- Создание сводок новостей
- Управление списком отслеживаемых каналов
- Команды бота в меню Telegram

## Команды

- `/start` - Начать работу с ботом
- `/digest` - Получить сводку новостей
- `/manage_channels` - Управление каналами
- `/collect_messages` - Собрать сообщения
- `/add_channel` - Добавить канал
- `/list_channels` - Список каналов
- `/help` - Справка

## Деплой на Render

### 1. Подготовка

1. Убедитесь, что у вас есть аккаунт на [Render.com](https://render.com)
2. Создайте новый Web Service
3. Подключите ваш GitHub репозиторий

### 2. Настройка переменных окружения

В настройках сервиса добавьте следующие переменные:

- `TELEGRAM_BOT_TOKEN` - токен вашего бота от @BotFather
- `OPENAI_API_KEY` - ключ OpenAI API (опционально)
- `ADMIN_USER_ID` - ID администратора (опционально)

### 3. Настройка команд бота

1. Напишите @BotFather в Telegram
2. Выберите `/setcommands`
3. Выберите вашего бота
4. Добавьте команды:

```
start - Начать работу с ботом
digest - Получить сводку новостей
manage_channels - Управление каналами
collect_messages - Собрать сообщения
add_channel - Добавить канал
list_channels - Список каналов
help - Справка
```

### 4. Деплой

1. Render автоматически обнаружит `render.yaml` и настроит сервис
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `python bot_final.py`

## Локальная разработка

1. Установите зависимости:
```bash
pip install -r requirements.txt
```

2. Создайте файл `.env` с переменными окружения:
```
TELEGRAM_BOT_TOKEN=your_bot_token
OPENAI_API_KEY=your_openai_key
ADMIN_USER_ID=your_user_id
```

3. Запустите бота:
```bash
python bot_final.py
```

## Особенности

- Бот собирает сообщения через веб-интерфейс Telegram каналов
- Не требует прав администратора в каналах
- Создает неформальные сводки "что происходит в мире"
- Убирает ссылки из сводок для лучшей читаемости
