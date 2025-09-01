import os
import logging
import json
import time
import asyncio
import requests
import schedule
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from collections import defaultdict
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import openai
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', 0))
DIGEST_CHANNEL_ID = os.getenv('DIGEST_CHANNEL_ID', '')  # ID канала для публикации дайджестов

# Настройка часового пояса для Португалии
# Португалия: WET (UTC+0) зимой, WEST (UTC+1) летом
PORTUGAL_TIMEZONE = timezone(timedelta(hours=1))  # Используем UTC+1 как основной

# Инициализация OpenAI
openai.api_key = OPENAI_API_KEY

# Хранилище данных
class MessageStore:
    def __init__(self):
        self.messages = defaultdict(list)  # channel_id -> messages
        self.channels = {}  # channel_id -> channel_info
        self.monitored_channels = set()  # каналы для мониторинга
        self.user_states = {}  # состояния пользователей для интерфейса
    
    def add_message(self, channel_id: str, message_data: dict):
        """Добавляет сообщение в хранилище"""
        self.messages[channel_id].append(message_data)
    
    def get_messages_for_period(self, hours: int = 24) -> Dict[str, List[dict]]:
        """Получает сообщения за указанный период"""
        # Используем португальское время
        now = datetime.now(PORTUGAL_TIMEZONE)
        cutoff_time = now - timedelta(hours=hours)
        filtered_messages = {}
        
        for channel_id, messages in self.messages.items():
            if channel_id in self.monitored_channels:
                recent_messages = []
                for msg in messages:
                    try:
                        # Парсим время и приводим к naive datetime
                        msg_time = datetime.fromisoformat(msg['timestamp'])
                        if msg_time.tzinfo is not None:
                            msg_time = msg_time.replace(tzinfo=None)
                        
                        # Конвертируем в португальское время для сравнения
                        if msg_time > cutoff_time.replace(tzinfo=None):
                            recent_messages.append(msg)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Ошибка парсинга времени для сообщения: {e}")
                        # Если не можем распарсить время, включаем сообщение
                        recent_messages.append(msg)
                
                if recent_messages:
                    filtered_messages[channel_id] = recent_messages
        
        return filtered_messages
    
    def add_channel(self, channel_id: str, channel_info: dict):
        """Добавляет канал для мониторинга"""
        self.channels[channel_id] = channel_info
        self.monitored_channels.add(channel_id)
    
    def remove_channel(self, channel_id: str):
        """Удаляет канал из мониторинга"""
        self.monitored_channels.discard(channel_id)
    
    def get_monitored_channels(self) -> List[dict]:
        """Возвращает список отслеживаемых каналов"""
        return [self.channels.get(ch_id, {'id': ch_id, 'title': 'Unknown'}) 
                for ch_id in self.monitored_channels]
    
    def get_all_channels(self) -> List[dict]:
        """Возвращает все каналы"""
        return list(self.channels.values())
    
    def set_user_state(self, user_id: int, state: str, data: dict = None):
        """Устанавливает состояние пользователя"""
        self.user_states[user_id] = {'state': state, 'data': data or {}}
    
    def get_user_state(self, user_id: int) -> dict:
        """Получает состояние пользователя"""
        return self.user_states.get(user_id, {'state': 'idle', 'data': {}})

# Глобальное хранилище
message_store = MessageStore()

# Предустановленные каналы с веб-ссылками
PREDEFINED_CHANNELS = {
    'meduza': {
        'id': 'meduza',
        'title': 'Meduza',
        'username': 'meduzaproject',
        'type': 'channel',
        'web_url': 'https://t.me/meduzaproject'
    },
    'rbc': {
        'id': 'rbc',
        'title': 'РБК',
        'username': 'rbc_news',
        'type': 'channel',
        'web_url': 'https://t.me/rbc_news'
    },
    'tass': {
        'id': 'tass',
        'title': 'ТАСС',
        'username': 'tass_agency',
        'type': 'channel',
        'web_url': 'https://t.me/tass_agency'
    },
    'interfax': {
        'id': 'interfax',
        'title': 'Интерфакс',
        'username': 'interfax_news',
        'type': 'channel',
        'web_url': 'https://t.me/interfax_news'
    },
    'ria': {
        'id': 'ria',
        'title': 'РИА Новости',
        'username': 'rian_ru',
        'type': 'channel',
        'web_url': 'https://t.me/rian_ru'
    },
    'bbbreaking': {
        'id': 'bbbreaking',
        'title': 'BB Breaking',
        'username': 'bbbreaking',
        'type': 'channel',
        'web_url': 'https://t.me/bbbreaking'
    },
    'kontext': {
        'id': 'kontext',
        'title': 'Контекст',
        'username': 'kontext_channel',
        'type': 'channel',
        'web_url': 'https://t.me/kontext_channel'
    },
    'meduzalive': {
        'id': 'meduzalive',
        'title': 'Meduza Live',
        'username': 'meduzalive',
        'type': 'channel',
        'web_url': 'https://t.me/meduzalive'
    },
    'superslowflow': {
        'id': 'superslowflow',
        'title': 'Super Slow Flow',
        'username': 'superslowflow',
        'type': 'channel',
        'web_url': 'https://t.me/superslowflow'
    },
    'vcnews': {
        'id': 'vcnews',
        'title': 'VC News',
        'username': 'vcnews',
        'type': 'channel',
        'web_url': 'https://t.me/vcnews'
    },
    'mediazzzona': {
        'id': 'mediazzzona',
        'title': 'Mediazzzona',
        'username': 'mediazzzona',
        'type': 'channel',
        'web_url': 'https://t.me/mediazzzona'
    }
}

async def scrape_channel_messages(channel_username: str) -> List[dict]:
    """Скрапит сообщения из канала через веб-интерфейс"""
    try:
        # Формируем URL для веб-версии канала
        web_url = f"https://t.me/s/{channel_username}"
        
        logger.info(f"Пытаюсь получить сообщения из: {web_url}")
        
        # Отправляем запрос
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        response = requests.get(web_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Парсим HTML
        html_content = response.text
        
        logger.info(f"Получен HTML размером {len(html_content)} символов")
        
        # Ищем сообщения (несколько паттернов)
        messages = []
        
        # Паттерн 1: основной паттерн для сообщений
        message_pattern = r'<div class="tgme_widget_message_text js-message_text" dir="auto">(.*?)</div>'
        time_pattern = r'<time datetime="([^"]+)"'
        
        # Паттерн 2: альтернативный паттерн
        message_pattern2 = r'<div class="tgme_widget_message_text[^"]*">(.*?)</div>'
        
        # Паттерн 3: более общий паттерн
        message_pattern3 = r'<div[^>]*class="[^"]*message_text[^"]*"[^>]*>(.*?)</div>'
        
        # Находим все сообщения с разными паттернами
        message_matches = re.findall(message_pattern, html_content, re.DOTALL)
        if not message_matches:
            message_matches = re.findall(message_pattern2, html_content, re.DOTALL)
        if not message_matches:
            message_matches = re.findall(message_pattern3, html_content, re.DOTALL)
        
        time_matches = re.findall(time_pattern, html_content)
        
        logger.info(f"Найдено {len(message_matches)} сообщений и {len(time_matches)} временных меток")
        
        # Ограничиваем количество сообщений
        max_messages = min(5, len(message_matches))
        
        for i in range(max_messages):
            if i < len(message_matches):
                message_text = message_matches[i]
                message_time = time_matches[i] if i < len(time_matches) else datetime.now(PORTUGAL_TIMEZONE).strftime('%Y-%m-%dT%H:%M:%S')
                
                # Очищаем HTML теги
                clean_text = re.sub(r'<[^>]+>', '', message_text)
                clean_text = re.sub(r'&nbsp;', ' ', clean_text)
                clean_text = re.sub(r'&amp;', '&', clean_text)
                clean_text = re.sub(r'&lt;', '<', clean_text)
                clean_text = re.sub(r'&gt;', '>', clean_text)
                clean_text = re.sub(r'&quot;', '"', clean_text)
                clean_text = re.sub(r'&#39;', "'", clean_text)
                
                # Убираем лишние пробелы
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                
                if clean_text and len(clean_text) > 10:  # Минимальная длина сообщения
                    messages.append({
                        'text': clean_text,
                        'from_user': 'Channel',
                        'timestamp': message_time,
                        'message_id': i + 1
                    })
        
        logger.info(f"Собрано {len(messages)} сообщений из канала {channel_username}")
        
        # Если сообщений нет, попробуем другой подход
        if not messages:
            logger.warning(f"Не удалось найти сообщения в канале {channel_username}")
            # Добавим тестовое сообщение для демонстрации
            messages.append({
                'text': f'Тестовое сообщение из канала {channel_username}: Важные новости дня',
                'from_user': 'Channel',
                'timestamp': datetime.now(PORTUGAL_TIMEZONE).strftime('%Y-%m-%dT%H:%M:%S'),
                'message_id': 1
            })
        
        return messages
        
    except Exception as e:
        logger.error(f"Ошибка при скрапинге канала {channel_username}: {e}")
        # Возвращаем тестовое сообщение в случае ошибки
        return [{
            'text': f'Тестовое сообщение из канала {channel_username}: Важные новости дня',
            'from_user': 'Channel',
            'timestamp': datetime.now(PORTUGAL_TIMEZONE).strftime('%Y-%m-%dT%H:%M:%S'),
            'message_id': 1
        }]

async def collect_real_messages():
    """Собирает реальные сообщения из каналов"""
    for channel_id in message_store.monitored_channels:
        channel_info = message_store.channels.get(channel_id)
        if channel_info and channel_info.get('username'):
            messages = await scrape_channel_messages(channel_info['username'])
            
            # Очищаем старые сообщения для этого канала
            message_store.messages[channel_id] = []
            
            # Добавляем новые сообщения
            for msg in messages:
                message_store.add_message(channel_id, msg)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    logger.info(f"Получена команда /start от пользователя {update.effective_user.id}")
    
    welcome_text = """
🤖 Telegram Digest Bot

Привет! Я помогу вам создавать ежедневные сводки важных сообщений из каналов.

Доступные команды:
• /digest - получить сводку сейчас
• /manage_channels - управление каналами
• /add_channel @username - добавить канал по username
• /collect_messages - собрать свежие сообщения из каналов
• /help - справка

Как использовать:
1. Используйте /manage_channels для выбора каналов
2. Или добавьте свои каналы командой /add_channel @username
3. Включите нужные каналы в мониторинг
4. Используйте /collect_messages для сбора свежих сообщений
5. Получайте сводки командой /digest

Примечание: Бот собирает сообщения через веб-интерфейс Telegram. Автоматические дайджесты отправляются в канал каждые 2 часа (7:00 - 21:00) по португальскому времени
    """
    
    await update.message.reply_text(welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
📚 **Справка по командам**

**Основные команды:**
• `/start` - начать работу с ботом
• `/digest` - получить сводку сейчас
• `/manage_channels` - управление каналами
• `/add_channel @username` - добавить канал по username
• `/collect_messages` - собрать свежие сообщения из каналов
• `/status` - показать статус бота
• `/version` - показать версию и время следующего дайджеста

**Как добавить канал:**
1. Используйте `/manage_channels` для выбора предустановленных каналов
2. Или добавьте свой канал: `/add_channel @channel_username`
3. Включите нужные каналы в мониторинг
4. Используйте `/collect_messages` для сбора сообщений
5. Получайте сводки командой `/digest`

**Сбор сообщений:**
Бот собирает сообщения через веб-интерфейс Telegram каналов. Автоматические дайджесты отправляются в канал каждые 2 часа (7:00 - 21:00) по португальскому времени
    """
    
    await update.message.reply_text(help_text)

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /add_channel"""
    if not context.args:
        await update.message.reply_text("❌ Укажите канал: /add_channel @channel_name")
        return
    
    channel_username = context.args[0].lstrip('@')
    
    # Создаем информацию о канале
    channel_info = {
        'id': channel_username,
        'title': f"@{channel_username}",
        'username': channel_username,
        'type': 'channel',
        'web_url': f"https://t.me/{channel_username}"
    }
    
    # Добавляем канал в хранилище
    message_store.channels[channel_username] = channel_info
    
    await update.message.reply_text(
        f"✅ Канал @{channel_username} добавлен!\n\n"
        f"Используйте /manage_channels для включения его в анализ."
    )

async def manage_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /manage_channels - показывает интерфейс управления каналами"""
    user_id = update.effective_user.id
    
    # Добавляем предустановленные каналы в хранилище
    for channel_id, channel_info in PREDEFINED_CHANNELS.items():
        message_store.channels[channel_id] = channel_info
    
    all_channels = message_store.get_all_channels()
    monitored_channels = message_store.get_monitored_channels()
    monitored_ids = {channel['id'] for channel in monitored_channels}
    
    if not all_channels:
        await update.message.reply_text(
            "📭 Пока нет каналов для анализа.\n\n"
            "Используйте `/add_channel @username` для добавления каналов!"
        )
        return
    
    # Создаем клавиатуру с каналами
    keyboard = []
    for channel in all_channels:
        channel_id = channel['id']
        channel_title = channel['title']
        is_monitored = channel_id in monitored_ids
        
        # Создаем кнопку с индикатором статуса
        status_emoji = "✅" if is_monitored else "❌"
        button_text = f"{status_emoji} {channel_title}"
        
        keyboard.append([InlineKeyboardButton(
            button_text, 
            callback_data=f"toggle_channel:{channel_id}"
        )])
    
    # Добавляем кнопки управления
    keyboard.append([
        InlineKeyboardButton("🔄 Обновить", callback_data="refresh_channels"),
        InlineKeyboardButton("✅ Выбрать все", callback_data="select_all_channels"),
        InlineKeyboardButton("❌ Снять все", callback_data="deselect_all_channels")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    status_text = f"📋 Управление каналами для анализа\n\n"
    status_text += f"Отслеживается: {len(monitored_channels)} из {len(all_channels)} каналов\n\n"
    status_text += "Нажмите на канал, чтобы включить/выключить его анализ:"
    
    # Проверяем, откуда вызвана функция
    if update.callback_query:
        await update.callback_query.edit_message_text(status_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(status_text, reply_markup=reply_markup)

async def collect_messages_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /collect_messages"""
    await update.message.reply_text("🔄 Собираю свежие сообщения из каналов...")
    
    try:
        await collect_real_messages()
        
        # Подсчитываем результаты
        total_messages = sum(len(messages) for messages in message_store.messages.values())
        monitored_channels = message_store.get_monitored_channels()
        
        result_text = f"✅ Сбор сообщений завершен!\n\n"
        result_text += f"📋 Отслеживаемых каналов: {len(monitored_channels)}\n"
        result_text += f"📨 Всего сообщений: {total_messages}\n\n"
        
        if monitored_channels:
            result_text += "📊 По каналам:\n"
            for channel in monitored_channels:
                channel_id = channel['id']
                message_count = len(message_store.messages.get(channel_id, []))
                result_text += f"• {channel['title']}: {message_count} сообщений\n"
        else:
            result_text += "❌ Нет отслеживаемых каналов\n"
            result_text += "Используйте `/manage_channels` для добавления каналов"
        
        await update.message.reply_text(result_text)
        
    except Exception as e:
        logger.error(f"Ошибка при сборе сообщений: {e}")
        await update.message.reply_text("❌ Ошибка при сборе сообщений")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки управления"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id
    
    # Обработка каналов
    if data.startswith("toggle_channel:"):
        channel_id = data.split(":")[1]
        channel_info = message_store.channels.get(channel_id)
        
        if not channel_info:
            await query.edit_message_text("❌ Канал не найден")
            return
        
        # Переключаем статус канала
        if channel_id in message_store.monitored_channels:
            message_store.remove_channel(channel_id)
            status = "❌ отключен"
        else:
            message_store.add_channel(channel_id, channel_info)
            status = "✅ включен"
        
        await query.edit_message_text(f"Канал {channel_info['title']} {status} для анализа")
        
        # Показываем обновленный интерфейс
        await manage_channels(update, context)
    
    elif data == "refresh_channels":
        await manage_channels(update, context)
    
    elif data == "select_all_channels":
        # Включаем все каналы
        for channel_id, channel_info in message_store.channels.items():
            message_store.add_channel(channel_id, channel_info)
        
        await query.edit_message_text("✅ Все каналы включены для анализа")
        await manage_channels(update, context)
    
    elif data == "deselect_all_channels":
        # Отключаем все каналы
        message_store.monitored_channels.clear()
        
        await query.edit_message_text("❌ Все каналы отключены от анализа")
        await manage_channels(update, context)
    
    # Обработка новых кнопок
    elif data == "digest":
        await query.edit_message_text("🔄 Создаю сводку...")
        try:
            digest_text = await create_digest()
            if digest_text:
                await query.edit_message_text(digest_text)
            else:
                await query.edit_message_text("📭 Нет новых сообщений для создания сводки")
        except Exception as e:
            logger.error(f"Ошибка при создании сводки: {e}")
            await query.edit_message_text(f"❌ Ошибка при создании сводки: {str(e)}")
    
    elif data == "manage_channels":
        await manage_channels(update, context)
    
    elif data == "collect_messages":
        await query.edit_message_text("🔄 Собираю свежие сообщения из каналов...")
        try:
            await collect_real_messages()
            monitored_channels = message_store.get_monitored_channels()
            total_messages = sum(len(message_store.messages.get(channel['id'], [])) for channel in monitored_channels)
            
            response = f"✅ Сбор сообщений завершен!\n"
            response += f"📋 Отслеживаемых каналов: {len(monitored_channels)}\n"
            response += f"📨 Всего сообщений: {total_messages}\n\n"
            
            if monitored_channels:
                response += f"📊 По каналам:\n"
                for channel in monitored_channels:
                    message_count = len(message_store.messages.get(channel['id'], []))
                    response += f"• {channel['title']}: {message_count} сообщений\n"
            else:
                response += f"❌ Нет отслеживаемых каналов\n"
                response += f"Используйте кнопку 'Управление каналами' для добавления каналов\n"
            
            await query.edit_message_text(response)
        except Exception as e:
            logger.error(f"Ошибка при сборе сообщений: {e}")
            await query.edit_message_text(f"❌ Ошибка при сборе сообщений: {str(e)}")
    
    elif data == "list_channels":
        channels = message_store.get_monitored_channels()
        if not channels:
            await query.edit_message_text("📋 Список отслеживаемых каналов пуст")
            return
        
        response_text = "📋 Отслеживаемые каналы:\n\n"
        for i, channel in enumerate(channels, 1):
            username = f"@{channel.get('username', 'private')}" if channel.get('username') else "Приватный канал"
            message_count = len(message_store.messages.get(channel['id'], []))
            response_text += f"{i}. {channel['title']} ({username}) - {message_count} сообщений\n"
        
        await query.edit_message_text(response_text)
    
    elif data == "help":
        help_text = """
📚 Справка по командам

Основные команды:
• /start - начать работу с ботом
• /digest - получить сводку сейчас
• /manage_channels - управление каналами для анализа
• /add_channel @username - добавить канал по username
• /collect_messages - собрать свежие сообщения из каналов
• /status - показать статус бота
• /list_channels - список отслеживаемых каналов

Как добавить канал:
1. Используйте /manage_channels для выбора предустановленных каналов
2. Или добавьте свой канал: /add_channel @channel_username
3. Включите нужные каналы в мониторинг
4. Используйте /collect_messages для сбора сообщений
5. Получайте сводки командой /digest

Сбор сообщений:
Бот собирает сообщения через веб-интерфейс Telegram каналов
        """
        await query.edit_message_text(help_text)

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /list_channels"""
    channels = message_store.get_monitored_channels()
    
    if not channels:
        await update.message.reply_text("📋 Список отслеживаемых каналов пуст")
        return
    
    response_text = "📋 **Отслеживаемые каналы:**\n\n"
    for i, channel in enumerate(channels, 1):
        username = f"@{channel.get('username', 'private')}" if channel.get('username') else "Приватный канал"
        message_count = len(message_store.messages.get(channel['id'], []))
        response_text += f"{i}. {channel['title']} ({username}) - {message_count} сообщений\n"
    
    await update.message.reply_text(response_text)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status - показывает статус бота"""
    monitored_channels = message_store.get_monitored_channels()
    all_messages = message_store.get_messages_for_period(24)
    
    # Получаем текущее время по португальскому времени
    now = datetime.now(PORTUGAL_TIMEZONE)
    
    status_text = f"📊 Статус бота:\n\n"
    status_text += f"🕐 Время (Португалия): {now.strftime('%d.%m.%Y %H:%M')}\n"
    status_text += f"📋 Каналов в мониторинге: {len(monitored_channels)}\n"
    status_text += f"📨 Каналов с сообщениями: {len(all_messages)}\n"
    status_text += f"💬 Всего сообщений: {sum(len(msgs) for msgs in all_messages.values())}\n\n"
    
    # Информация о расписании
    status_text += f"⏰ Расписание дайджестов:\n"
    status_text += f"Каждые 2 часа: 7:00, 9:00, 11:00, 13:00, 15:00, 17:00, 19:00, 21:00\n"
    status_text += f"(по португальскому времени)\n\n"
    
    # Информация о канале
    if DIGEST_CHANNEL_ID:
        status_text += f"📢 Канал для публикации: {DIGEST_CHANNEL_ID}\n"
        status_text += f"📤 Автоматические дайджесты отправляются только в канал\n"
    else:
        status_text += f"📢 Канал для публикации: не настроен\n"
    status_text += f"\n"
    
    if monitored_channels:
        status_text += f"✅ Отслеживаемые каналы:\n"
        for i, channel in enumerate(monitored_channels, 1):
            message_count = len(message_store.messages.get(channel['id'], []))
            status_text += f"{i}. {channel['title']} ({message_count} сообщений)\n"
    else:
        status_text += f"❌ Нет отслеживаемых каналов\n"
        status_text += f"Используйте /manage_channels для добавления каналов\n"
    
    await update.message.reply_text(status_text)

async def version_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /version - показывает версию и время следующего дайджеста"""
    now = datetime.now(PORTUGAL_TIMEZONE)
    current_hour = now.hour
    
    # Определяем время следующего дайджеста
    digest_times = [7, 9, 11, 13, 15, 17, 19, 21]
    next_digest = None
    
    for time in digest_times:
        if time > current_hour:
            next_digest = time
            break
    
    if next_digest is None:
        # Если сейчас после 21:00, следующий дайджест завтра в 7:00
        next_digest = 7
        next_digest_date = (now + timedelta(days=1)).strftime('%d.%m.%Y')
    else:
        next_digest_date = now.strftime('%d.%m.%Y')
    
    version_text = f"🤖 Версия бота: v2.0 (обновлено 28.08.2024)\n\n"
    version_text += f"🕐 Текущее время (Португалия): {now.strftime('%d.%m.%Y %H:%M')}\n"
    version_text += f"⏰ Следующий дайджест: {next_digest:02d}:00 {next_digest_date}\n\n"
    version_text += f"📅 Расписание: каждые 2 часа (7:00-21:00)\n"
    version_text += f"🌍 Часовой пояс: Португалия (UTC+1)\n"
    version_text += f"📊 Статус: Активен и работает\n\n"
    version_text += f"💡 Используйте /status для подробной информации"
    
    await update.message.reply_text(version_text)

async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /digest"""
    await update.message.reply_text("🔄 Создаю сводку...")
    
    try:
        digest_text = await create_digest()
        if digest_text:
            # Отправляем в канал (если настроен)
            if DIGEST_CHANNEL_ID:
                try:
                    await application_global.bot.send_message(
                        chat_id=DIGEST_CHANNEL_ID,
                        text=f"📰 СВОДКА ПО ЗАПРОСУ\n\n{digest_text}"
                    )
                    await update.message.reply_text(f"✅ Сводка отправлена в канал {DIGEST_CHANNEL_ID}")
                except Exception as e:
                    logger.error(f"Ошибка отправки в канал {DIGEST_CHANNEL_ID}: {e}")
                    await update.message.reply_text(f"❌ Ошибка отправки в канал: {str(e)}")
            else:
                # Если канал не настроен, отправляем лично
                await update.message.reply_text(digest_text)
        else:
            await update.message.reply_text("📭 Нет новых сообщений для создания сводки")
    except Exception as e:
        logger.error(f"Ошибка при создании сводки: {e}")
        await update.message.reply_text(f"❌ Ошибка при создании сводки: {str(e)}")

async def create_digest() -> str:
    """Создает сводку в стиле 'что происходит в мире' для человека, который только проснулся"""
    # Получаем все сообщения без фильтрации по времени
    all_messages = []
    
    # Добавляем отладочную информацию
    logger.info(f"Создание сводки. Мониторинг каналов: {list(message_store.monitored_channels)}")
    logger.info(f"Все каналы с сообщениями: {list(message_store.messages.keys())}")
    logger.info(f"Всего каналов в хранилище: {len(message_store.channels)}")
    
    # Проверяем все каналы в мониторинге
    for channel_id in message_store.monitored_channels:
        messages = message_store.messages.get(channel_id, [])
        channel_info = message_store.channels.get(channel_id, {})
        channel_title = channel_info.get('title', f'Channel {channel_id}')
        
        logger.info(f"Канал {channel_id}: {len(messages)} сообщений")
        
        for msg in messages:
            all_messages.append({
                'channel': channel_title,
                'text': msg.get('text', ''),
                'author': msg.get('from_user', 'Unknown')
            })
    
    # Если сообщений нет, попробуем получить их по-другому
    if not all_messages:
        logger.info("Сообщений в мониторинге нет, пробуем все каналы")
        # Попробуем получить все сообщения из всех каналов
        for channel_id, messages in message_store.messages.items():
            channel_info = message_store.channels.get(channel_id, {})
            channel_title = channel_info.get('title', f'Channel {channel_id}')
            
            logger.info(f"Канал {channel_id}: {len(messages)} сообщений")
            
            for msg in messages:
                all_messages.append({
                    'channel': channel_title,
                    'text': msg.get('text', ''),
                    'author': msg.get('from_user', 'Unknown')
                })
    
    logger.info(f"Всего собрано сообщений для сводки: {len(all_messages)}")
    
    if not all_messages:
        return "📭 Нет сообщений для создания сводки. Попробуйте сначала собрать сообщения командой /collect_messages"
    
    # Создаем неформальную сводку "что происходит в мире"
    digest_text = "🌍 ЧТО ПРОИСХОДИТ В МИРЕ\n"
    digest_text += f"📅 {datetime.now(PORTUGAL_TIMEZONE).strftime('%d.%m.%Y %H:%M')}\n\n"
    digest_text += "💡 Смотрю все самые важные новостные источники и делюсь с тобой, чтобы тебе не пришлось. Занимайся делами! 👊\n\n"
    
    # Собираем все тексты сообщений
    all_texts = []
    for msg in all_messages:
        text = msg['text'].strip()
        if text and len(text) > 10:  # Минимальная длина
            all_texts.append(text)
    
    # Создаем топ-10 самых важных новостей
    digest_text += "🔥 ТОП-10 ГЛАВНЫХ НОВОСТЕЙ:\n\n"
    
    # Берем первые 10 уникальных сообщений с источниками
    unique_messages = []
    seen_texts = set()
    
    for msg in all_messages:
        text = msg['text'].strip()
        channel = msg['channel']
        
        # Убираем дубликаты и очень похожие тексты
        clean_text = text[:100].lower()  # Первые 100 символов для сравнения
        if clean_text not in seen_texts and len(text) > 10:
            seen_texts.add(clean_text)
            unique_messages.append({'text': text, 'channel': channel})
            if len(unique_messages) >= 10:
                break
    
    # Формируем список новостей в неформальном стиле
    used_channels = set()  # Для отслеживания использованных каналов
    
    for i, msg_data in enumerate(unique_messages, 1):
        text = msg_data['text']
        channel = msg_data['channel']
        
        # Пропускаем, если канал уже использован (для разнообразия источников)
        if channel in used_channels and len(used_channels) < len(set(msg['channel'] for msg in all_messages)):
            continue
            
        used_channels.add(channel)
        
        # Убираем ссылки и лишние элементы из текста
        text = re.sub(r'https?://[^\s]+', '', text)  # Убираем HTTP ссылки
        text = re.sub(r'www\.[^\s]+', '', text)      # Убираем www ссылки
        text = re.sub(r't\.me/[^\s]+', '', text)     # Убираем Telegram ссылки
        text = re.sub(r'Подписаться на.*?\.', '', text)  # Убираем "Подписаться на..."
        text = re.sub(r'Читать далее.*?\.', '', text)    # Убираем "Читать далее..."
        text = re.sub(r'Источник:.*?\.', '', text)       # Убираем "Источник:..."
        
        # Делаем текст короче - только суть
        if len(text) > 100:
            # Ищем естественное место для обрезания (конец предложения)
            sentences = text.split('.')
            if len(sentences) > 1:
                # Берем первое полное предложение
                short_text = sentences[0].strip() + '.'
                if len(short_text) > 120:
                    # Если все еще длинное, берем по словам
                    words = text.split()
                    short_text = ' '.join(words[:15])  # Первые 15 слов для краткости
                    if not short_text.endswith('.'):
                        short_text += '.'
            else:
                # Если нет точек, берем по словам
                words = text.split()
                short_text = ' '.join(words[:15])  # Первые 15 слов
                if not short_text.endswith('.'):
                    short_text += '.'
        else:
            short_text = text
        
        # Убираем лишние пробелы и переносы
        short_text = ' '.join(short_text.split())
        
        # Добавляем неформальные префиксы
        prefixes = ["💥", "📰", "🔥", "⚡", "🎯", "💡", "🚨", "📢", "🎪", "🌟"]
        prefix = prefixes[i-1] if i <= len(prefixes) else "📌"
        
        # Добавляем источник
        digest_text += f"{prefix} {short_text}\n"
        digest_text += f"   📍 {channel}\n\n"
    
    # Добавляем статистику в неформальном стиле
    total_channels = len(set(msg['channel'] for msg in all_messages))
    total_messages = len(all_messages)
    
    digest_text += f"---\n"
    digest_text += f"📊 Источники: {total_channels} каналов\n"
    digest_text += f"📨 Обработано сообщений: {total_messages}\n"
    digest_text += f"⏰ Сводка создана: {datetime.now(PORTUGAL_TIMEZONE).strftime('%H:%M')}\n"
    
    return digest_text

# Глобальная переменная для приложения
application_global = None

async def send_scheduled_digest():
    """Отправляет автоматическую сводку в 19:00"""
    if not application_global:
        logger.error("Приложение не инициализировано")
        return
    
    try:
        # Собираем свежие сообщения
        await collect_real_messages()
        
        # Создаем сводку
        digest_text = await create_digest()
        
        # Отправляем дайджест в канал (если настроен)
        if DIGEST_CHANNEL_ID:
            try:
                await application_global.bot.send_message(
                    chat_id=DIGEST_CHANNEL_ID,
                    text=f"🌅 ЕЖЕДНЕВНАЯ СВОДКА\n\n{digest_text}"
                )
                logger.info(f"Автоматическая сводка отправлена в канал {DIGEST_CHANNEL_ID}")
            except Exception as e:
                logger.error(f"Ошибка отправки в канал {DIGEST_CHANNEL_ID}: {e}")
        
        # Отправляем только в канал (не дублируем в бота)
        if DIGEST_CHANNEL_ID:
            logger.info(f"Автоматическая сводка отправлена в канал {DIGEST_CHANNEL_ID}")
        else:
            logger.warning("DIGEST_CHANNEL_ID не настроен, автоматическая сводка не отправлена")
            
    except Exception as e:
        logger.error(f"Ошибка при отправке автоматической сводки: {e}")

async def send_test_digest():
    """Отправляет тестовую сводку"""
    if not application_global:
        logger.error("Приложение не инициализировано")
        return
    
    try:
        # Собираем свежие сообщения
        await collect_real_messages()
        
        # Создаем сводку
        digest_text = await create_digest()
        
        # Отправляем тестовую сводку
        if ADMIN_USER_ID:
            await application_global.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=f"🧪 **ТЕСТОВАЯ СВОДКА** (проверка работы)\n\n{digest_text}"
            )
            logger.info(f"Тестовая сводка отправлена пользователю {ADMIN_USER_ID}")
        else:
            logger.warning("ADMIN_USER_ID не настроен, тестовая сводка не отправлена")
            
    except Exception as e:
        logger.error(f"Ошибка при отправке тестовой сводки: {e}")
            
    except Exception as e:
        logger.error(f"Ошибка при отправке автоматической сводки: {e}")

def run_scheduler():
    """Запускает планировщик задач"""
    # Сводки каждые 2 часа с 7:00 до 21:00 по португальскому времени
    schedule.every().day.at("07:00").do(lambda: asyncio.run(send_scheduled_digest()))
    schedule.every().day.at("09:00").do(lambda: asyncio.run(send_scheduled_digest()))
    schedule.every().day.at("11:00").do(lambda: asyncio.run(send_scheduled_digest()))
    schedule.every().day.at("13:00").do(lambda: asyncio.run(send_scheduled_digest()))
    schedule.every().day.at("15:00").do(lambda: asyncio.run(send_scheduled_digest()))
    schedule.every().day.at("17:00").do(lambda: asyncio.run(send_scheduled_digest()))
    schedule.every().day.at("19:00").do(lambda: asyncio.run(send_scheduled_digest()))
    schedule.every().day.at("21:00").do(lambda: asyncio.run(send_scheduled_digest()))
    
    # Тестовая сводка через 2 минуты после запуска (только для проверки)
    # schedule.every(2).minutes.do(lambda: asyncio.run(send_test_digest()))
    
    while True:
        schedule.run_pending()
        time.sleep(60)  # Проверяем каждую минуту

def main():
    """Основная функция"""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не настроен")
        return
    
    # Создаем приложение
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Сохраняем глобальную ссылку на приложение
    global application_global
    application_global = application
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("digest", digest_command))
    application.add_handler(CommandHandler("manage_channels", manage_channels))
    application.add_handler(CommandHandler("add_channel", add_channel))
    application.add_handler(CommandHandler("collect_messages", collect_messages_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("list_channels", list_channels))
    application.add_handler(CommandHandler("version", version_command))
    
    # Обработчик callback'ов для кнопок (только для manage_channels)
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Запускаем планировщик в отдельном потоке
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("Планировщик автоматических сводок запущен (7:00, 9:00, 11:00, 13:00, 15:00, 17:00, 19:00, 21:00 каждый день по португальскому времени)")
    
    # Запускаем бота с обработкой ошибок
    logger.info("Бот запущен")
    try:
        application.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        # Пробуем перезапустить через 5 секунд
        time.sleep(5)
        application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
