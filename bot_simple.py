#!/usr/bin/env python3
"""
Telegram Digest Bot - Простая версия с кнопками
"""

import os
import re
import time
import logging
import requests
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получаем токены
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Предустановленные каналы
PREDEFINED_CHANNELS = {
    'bbbreaking': {
        'id': 'bbbreaking',
        'title': 'BB Breaking',
        'username': 'bbbreaking',
        'type': 'channel',
        'web_url': 'https://t.me/bbbreaking'
    },
    'kontext_channel': {
        'id': 'kontext_channel',
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
    }
}

class MessageStore:
    def __init__(self):
        self.messages = defaultdict(list)
        self.channels = {}
        self.monitored_channels = set()
    
    def add_channel(self, channel_id: str, channel_info: dict):
        self.channels[channel_id] = channel_info
        self.monitored_channels.add(channel_id)
    
    def remove_channel(self, channel_id: str):
        if channel_id in self.monitored_channels:
            self.monitored_channels.remove(channel_id)
    
    def get_monitored_channels(self):
        return [self.channels.get(channel_id, {}) for channel_id in self.monitored_channels]
    
    def get_all_channels(self):
        return list(self.channels.values())

# Глобальное хранилище сообщений
message_store = MessageStore()

def scrape_channel_messages(channel_username: str) -> list:
    """Скрапит сообщения из канала через веб-интерфейс"""
    try:
        url = f"https://t.me/s/{channel_username}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        html_content = response.text
        messages = []
        
        # Паттерны для извлечения сообщений
        patterns = [
            r'<div class="tgme_widget_message_text js-message_text" dir="auto">(.*?)</div>',
            r'<div class="tgme_widget_message_text" dir="auto">(.*?)</div>',
            r'<div class="js-message_text" dir="auto">(.*?)</div>'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html_content, re.DOTALL)
            for match in matches:
                # Очищаем HTML теги
                clean_text = re.sub(r'<[^>]+>', '', match)
                clean_text = clean_text.strip()
                
                if clean_text and len(clean_text) > 10:
                    messages.append({
                        'text': clean_text,
                        'from_user': 'Channel',
                        'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
                    })
        
        # Если не нашли сообщений, возвращаем тестовое
        if not messages:
            messages.append({
                'text': f'Тестовое сообщение из канала {channel_username}',
                'from_user': 'Channel',
                'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
            })
        
        return messages[:10]  # Ограничиваем 10 сообщениями
        
    except Exception as e:
        logger.error(f"Ошибка при скрапинге канала {channel_username}: {e}")
        return [{
            'text': f'Ошибка получения сообщений из {channel_username}',
            'from_user': 'Error',
            'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        }]

async def collect_real_messages():
    """Собирает реальные сообщения из каналов"""
    monitored_channels = message_store.get_monitored_channels()
    
    for channel in monitored_channels:
        channel_id = channel['id']
        username = channel.get('username', channel_id)
        
        logger.info(f"Собираю сообщения из канала {username}")
        messages = scrape_channel_messages(username)
        
        # Очищаем старые сообщения и добавляем новые
        message_store.messages[channel_id] = messages
        logger.info(f"Собрано {len(messages)} сообщений из {username}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome_text = """
🤖 Telegram Digest Bot

Привет! Я помогу вам создавать ежедневные сводки важных сообщений из каналов.

Доступные команды:
• /digest - получить сводку сейчас
• /manage_channels - управление каналами для анализа
• /add_channel @username - добавить канал по username
• /collect_messages - собрать свежие сообщения из каналов
• /status - показать статус бота
• /list_channels - список отслеживаемых каналов
• /help - справка

Как использовать:
1. Используйте /manage_channels для выбора каналов
2. Или добавьте свои каналы командой /add_channel @username
3. Включите нужные каналы в мониторинг
4. Используйте /collect_messages для сбора свежих сообщений
5. Получайте сводки командой /digest

Примечание: Бот собирает сообщения через веб-интерфейс Telegram.
    """
    
    # Создаем кнопки
    keyboard = [
        [InlineKeyboardButton("📊 Получить сводку", callback_data="digest")],
        [InlineKeyboardButton("⚙️ Управление каналами", callback_data="manage_channels")],
        [InlineKeyboardButton("📥 Собрать сообщения", callback_data="collect_messages")],
        [InlineKeyboardButton("📋 Список каналов", callback_data="list_channels")],
        [InlineKeyboardButton("ℹ️ Справка", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "digest":
        await query.edit_message_text("🔄 Создаю сводку...")
        try:
            digest_text = await create_digest()
            if digest_text:
                await query.edit_message_text(digest_text)
            else:
                await query.edit_message_text("📭 Нет сообщений для создания сводки")
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
            message_count = len(message_store.messages.get(channel['id'], []))
            response_text += f"{i}. {channel['title']} - {message_count} сообщений\n"
        
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
        """
        await query.edit_message_text(help_text)
    
    # Обработка переключения каналов
    elif data.startswith("toggle_channel:"):
        channel_id = data.split(":")[1]
        channel_info = message_store.channels.get(channel_id)
        
        if not channel_info:
            await query.edit_message_text("❌ Канал не найден")
            return
        
        if channel_id in message_store.monitored_channels:
            message_store.remove_channel(channel_id)
            status = "❌ отключен"
        else:
            message_store.add_channel(channel_id, channel_info)
            status = "✅ включен"
        
        await query.edit_message_text(f"Канал {channel_info['title']} {status} для анализа")
        await manage_channels(update, context)

async def manage_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Управление каналами"""
    # Добавляем предустановленные каналы
    for channel_id, channel_info in PREDEFINED_CHANNELS.items():
        message_store.channels[channel_id] = channel_info
    
    all_channels = message_store.get_all_channels()
    monitored_channels = message_store.get_monitored_channels()
    monitored_ids = {channel['id'] for channel in monitored_channels}
    
    if not all_channels:
        await update.callback_query.edit_message_text("📭 Пока нет каналов для анализа")
        return
    
    # Создаем клавиатуру с каналами
    keyboard = []
    for channel in all_channels:
        channel_id = channel['id']
        channel_title = channel['title']
        is_monitored = channel_id in monitored_ids
        
        status_emoji = "✅" if is_monitored else "❌"
        button_text = f"{status_emoji} {channel_title}"
        
        keyboard.append([InlineKeyboardButton(
            button_text, 
            callback_data=f"toggle_channel:{channel_id}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    status_text = f"📋 Управление каналами для анализа\n\n"
    status_text += f"Отслеживается: {len(monitored_channels)} из {len(all_channels)} каналов\n\n"
    status_text += "Нажмите на канал, чтобы включить/выключить его анализ:"
    
    await update.callback_query.edit_message_text(status_text, reply_markup=reply_markup)

async def create_digest() -> str:
    """Создает неформальную сводку"""
    all_messages = []
    
    # Собираем сообщения из всех каналов
    for channel_id, messages in message_store.messages.items():
        channel_info = message_store.channels.get(channel_id, {})
        channel_title = channel_info.get('title', f'Channel {channel_id}')
        
        for msg in messages:
            all_messages.append({
                'channel': channel_title,
                'text': msg.get('text', ''),
                'author': msg.get('from_user', 'Unknown')
            })
    
    if not all_messages:
        return "📭 Нет сообщений для создания сводки. Попробуйте сначала собрать сообщения командой /collect_messages"
    
    # Создаем неформальную сводку
    digest_text = "🌍 ЧТО ПРОИСХОДИТ В МИРЕ\n"
    digest_text += f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
    
    # Собираем все тексты сообщений
    all_texts = []
    for msg in all_messages:
        text = msg['text'].strip()
        if text and len(text) > 10:
            all_texts.append(text)
    
    # Создаем топ-10 самых важных новостей
    digest_text += "🔥 ТОП-10 ГЛАВНЫХ НОВОСТЕЙ:\n\n"
    
    # Берем первые 10 уникальных сообщений
    unique_texts = []
    seen_texts = set()
    
    for text in all_texts:
        clean_text = text[:100].lower()
        if clean_text not in seen_texts:
            seen_texts.add(clean_text)
            unique_texts.append(text)
            if len(unique_texts) >= 10:
                break
    
    # Формируем список новостей в неформальном стиле
    for i, text in enumerate(unique_texts, 1):
        short_text = text[:150] + "..." if len(text) > 150 else text
        short_text = ' '.join(short_text.split())
        
        prefixes = ["💥", "📰", "🔥", "⚡", "🎯", "💡", "🚨", "📢", "🎪", "🌟"]
        prefix = prefixes[i-1] if i <= len(prefixes) else "📌"
        
        digest_text += f"{prefix} {short_text}\n\n"
    
    # Добавляем статистику
    total_channels = len(set(msg['channel'] for msg in all_messages))
    total_messages = len(all_messages)
    
    digest_text += f"---\n"
    digest_text += f"📊 Источники: {total_channels} каналов\n"
    digest_text += f"📨 Обработано сообщений: {total_messages}\n"
    digest_text += f"⏰ Сводка создана: {datetime.now().strftime('%H:%M')}\n"
    
    return digest_text

async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /digest"""
    await update.message.reply_text("🔄 Создаю сводку...")
    
    try:
        digest_text = await create_digest()
        if digest_text:
            await update.message.reply_text(digest_text)
        else:
            await update.message.reply_text("📭 Нет новых сообщений для создания сводки")
    except Exception as e:
        logger.error(f"Ошибка при создании сводки: {e}")
        await update.message.reply_text(f"❌ Ошибка при создании сводки: {str(e)}")

async def collect_messages_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /collect_messages"""
    await update.message.reply_text("🔄 Собираю свежие сообщения из каналов...")
    
    try:
        await collect_real_messages()
        
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
            result_text += "Используйте /manage_channels для добавления каналов\n"
        
        await update.message.reply_text(result_text)
    except Exception as e:
        logger.error(f"Ошибка при сборе сообщений: {e}")
        await update.message.reply_text(f"❌ Ошибка при сборе сообщений: {str(e)}")

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /add_channel"""
    if not context.args:
        await update.message.reply_text("❌ Укажите канал: /add_channel @channel_name")
        return
    
    channel_username = context.args[0].lstrip('@')
    
    channel_info = {
        'id': channel_username,
        'title': f"@{channel_username}",
        'username': channel_username,
        'type': 'channel',
        'web_url': f"https://t.me/{channel_username}"
    }
    
    message_store.channels[channel_username] = channel_info
    
    await update.message.reply_text(
        f"✅ Канал @{channel_username} добавлен!\n\n"
        f"Используйте /manage_channels для включения его в анализ."
    )

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /list_channels"""
    channels = message_store.get_monitored_channels()
    
    if not channels:
        await update.message.reply_text("📋 Список отслеживаемых каналов пуст")
        return
    
    response_text = "📋 Отслеживаемые каналы:\n\n"
    for i, channel in enumerate(channels, 1):
        message_count = len(message_store.messages.get(channel['id'], []))
        response_text += f"{i}. {channel['title']} - {message_count} сообщений\n"
    
    await update.message.reply_text(response_text)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status"""
    monitored_channels = message_store.get_monitored_channels()
    total_messages = sum(len(messages) for messages in message_store.messages.values())
    
    status_text = f"📊 Статус бота:\n\n"
    status_text += f"📋 Каналов в мониторинге: {len(monitored_channels)}\n"
    status_text += f"📨 Всего сообщений: {total_messages}\n\n"
    
    if monitored_channels:
        status_text += f"✅ Отслеживаемые каналы:\n"
        for i, channel in enumerate(monitored_channels, 1):
            message_count = len(message_store.messages.get(channel['id'], []))
            status_text += f"{i}. {channel['title']} ({message_count} сообщений)\n"
    else:
        status_text += f"❌ Нет отслеживаемых каналов\n"
        status_text += f"Используйте /manage_channels для добавления каналов\n"
    
    await update.message.reply_text(status_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
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
    """
    
    await update.message.reply_text(help_text)

def main():
    """Основная функция"""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не настроен")
        return
    
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY не настроен")
        return
    
    # Создаем приложение
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("digest", digest_command))
    application.add_handler(CommandHandler("manage_channels", manage_channels))
    application.add_handler(CommandHandler("add_channel", add_channel))
    application.add_handler(CommandHandler("collect_messages", collect_messages_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("list_channels", list_channels))
    
    # Обработчик callback'ов для кнопок
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Запускаем бота
    logger.info("Бот запущен")
    try:
        application.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        time.sleep(5)
        application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()

