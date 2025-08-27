import os
import logging
import json
import schedule
import time
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import openai
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, User

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
DIGEST_TIME = os.getenv('DIGEST_TIME', '19:00')

# MTProto конфигурация
API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE_NUMBER = os.getenv('TELEGRAM_PHONE_NUMBER')

# Инициализация OpenAI
openai.api_key = OPENAI_API_KEY

# Хранилище данных
class MessageStore:
    def __init__(self):
        self.messages = defaultdict(list)  # chat_id -> messages
        self.channels = {}  # chat_id -> channel_info
        self.monitored_channels = set()  # каналы для мониторинга
        self.user_states = {}  # состояния пользователей для интерфейса
        self.client = None  # Telethon client
    
    def set_client(self, client):
        """Устанавливает Telethon клиент"""
        self.client = client
    
    def add_message(self, chat_id: str, message_data: dict):
        """Добавляет сообщение в хранилище"""
        self.messages[chat_id].append(message_data)
    
    def get_messages_for_period(self, hours: int = 24) -> Dict[str, List[dict]]:
        """Получает сообщения за указанный период"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        filtered_messages = {}
        
        for chat_id, messages in self.messages.items():
            if chat_id in self.monitored_channels:
                recent_messages = [
                    msg for msg in messages 
                    if datetime.fromisoformat(msg['timestamp']) > cutoff_time
                ]
                if recent_messages:
                    filtered_messages[chat_id] = recent_messages
        
        return filtered_messages
    
    def add_channel(self, chat_id: str, channel_info: dict):
        """Добавляет канал для мониторинга"""
        self.channels[chat_id] = channel_info
        self.monitored_channels.add(chat_id)
    
    def remove_channel(self, chat_id: str):
        """Удаляет канал из мониторинга"""
        self.monitored_channels.discard(chat_id)
    
    def get_monitored_channels(self) -> List[dict]:
        """Возвращает список отслеживаемых каналов"""
        return [self.channels.get(ch_id, {'id': ch_id, 'title': 'Unknown'}) 
                for ch_id in self.monitored_channels]
    
    async def get_all_channels(self) -> List[dict]:
        """Получает все доступные каналы через MTProto"""
        if not self.client:
            return []
        
        try:
            channels = []
            async for dialog in self.client.iter_dialogs():
                if isinstance(dialog.entity, (Channel, Chat)) and not isinstance(dialog.entity, User):
                    channel_info = {
                        'id': str(dialog.entity.id),
                        'title': dialog.name,
                        'username': getattr(dialog.entity, 'username', None),
                        'type': 'channel' if isinstance(dialog.entity, Channel) else 'group'
                    }
                    channels.append(channel_info)
                    # Сохраняем информацию о канале
                    self.channels[str(dialog.entity.id)] = channel_info
            
            return channels
        except Exception as e:
            logger.error(f"Ошибка при получении каналов: {e}")
            return []
    
    def set_user_state(self, user_id: int, state: str, data: dict = None):
        """Устанавливает состояние пользователя"""
        self.user_states[user_id] = {'state': state, 'data': data or {}}
    
    def get_user_state(self, user_id: int) -> dict:
        """Получает состояние пользователя"""
        return self.user_states.get(user_id, {'state': 'idle', 'data': {}})

# Глобальное хранилище
message_store = MessageStore()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    logger.info(f"Получена команда /start от пользователя {update.effective_user.id}")
    
    welcome_text = """
🤖 **Telegram Digest Bot (MTProto)**

Привет! Я помогу вам создавать ежедневные сводки важных сообщений из каналов, на которые вы подписаны.

**Доступные команды:**
• `/digest` - получить сводку сейчас
• `/manage_channels` - управление каналами для анализа
• `/list_channels` - список отслеживаемых каналов
• `/help` - справка

**Как использовать:**
1. Настройте список каналов для анализа через интерфейс
2. Получайте ежедневные сводки в 19:00 вечера
    """
    
    try:
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
        logger.info("Ответ на /start отправлен успешно")
    except Exception as e:
        logger.error(f"Ошибка при отправке ответа на /start: {e}")
        await update.message.reply_text("Привет! Бот работает, но возникла ошибка с форматированием.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
📚 **Справка по командам**

**Основные команды:**
• `/start` - начать работу с ботом
• `/digest` - получить сводку сейчас
• `/manage_channels` - управление каналами для анализа
• `/list_channels` - список отслеживаемых каналов

**Как настроить анализ каналов:**
1. Используйте команду `/manage_channels` для настройки
2. Выберите каналы для анализа через удобный интерфейс
3. Бот начнет собирать сообщения из выбранных каналов

**Ежедневные сводки:**
Бот автоматически отправляет сводки каждый день в 19:00 вечера
    """
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def manage_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /manage_channels - показывает интерфейс управления каналами"""
    user_id = update.effective_user.id
    
    await update.message.reply_text("🔄 Загружаю список каналов...")
    
    # Получаем все каналы через MTProto
    all_channels = await message_store.get_all_channels()
    monitored_channels = message_store.get_monitored_channels()
    monitored_ids = {channel['id'] for channel in monitored_channels}
    
    if not all_channels:
        await update.message.edit_text(
            "📭 Не удалось получить список каналов.\n\n"
            "Убедитесь, что MTProto настроен правильно."
        )
        return
    
    # Создаем клавиатуру с каналами
    keyboard = []
    for channel in all_channels[:20]:  # Ограничиваем 20 каналами для удобства
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
    
    status_text = f"📋 **Управление каналами для анализа**\n\n"
    status_text += f"Отслеживается: {len(monitored_channels)} из {len(all_channels)} каналов\n\n"
    status_text += "Нажмите на канал, чтобы включить/выключить его анализ:"
    
    await update.message.edit_text(status_text, reply_markup=reply_markup, parse_mode='Markdown')

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
        
        await query.edit_message_text(f"Канал **{channel_info['title']}** {status} для анализа", parse_mode='Markdown')
        
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

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /list_channels"""
    channels = message_store.get_monitored_channels()
    
    if not channels:
        await update.message.reply_text("📋 Список отслеживаемых каналов пуст")
        return
    
    response_text = "📋 **Отслеживаемые каналы:**\n\n"
    for i, channel in enumerate(channels, 1):
        username = f"@{channel.get('username', 'private')}" if channel.get('username') else "Приватный канал"
        response_text += f"{i}. **{channel['title']}** ({username})\n"
    
    await update.message.reply_text(response_text, parse_mode='Markdown')

async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /digest"""
    await update.message.reply_text("🔄 Создаю сводку...")
    
    try:
        digest_text = await create_digest()
        if digest_text:
            await update.message.reply_text(digest_text, parse_mode='Markdown')
        else:
            await update.message.reply_text("📭 Нет новых сообщений для создания сводки")
    except Exception as e:
        logger.error(f"Ошибка при создании сводки: {e}")
        await update.message.reply_text("❌ Ошибка при создании сводки")

async def create_digest() -> str:
    """Создает сводку с помощью OpenAI"""
    messages = message_store.get_messages_for_period(24)
    
    if not messages:
        return None
    
    # Подготавливаем данные для анализа
    digest_data = []
    for chat_id, chat_messages in messages.items():
        channel_info = message_store.channels.get(chat_id, {})
        channel_title = channel_info.get('title', 'Unknown Channel')
        
        for msg in chat_messages:
            digest_data.append({
                'channel': channel_title,
                'text': msg['text'],
                'timestamp': msg['timestamp'],
                'author': msg['from_user']
            })
    
    # Создаем промпт для OpenAI
    prompt = f"""
Создай краткую сводку важных сообщений из Telegram каналов за последние 24 часа.

Сообщения:
{json.dumps(digest_data, ensure_ascii=False, indent=2)}

Требования к сводке:
1. Структурированный формат с разделами по темам
2. Выдели самые важные новости и события
3. Укажи источники (каналы)
4. Используй эмодзи для лучшей читаемости
5. Общий объем не более 1000 слов
6. Формат Markdown

Создай сводку на русском языке.
"""
    
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Ты помощник для создания кратких сводок новостей из Telegram каналов. Создавай структурированные, информативные сводки на русском языке."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500,
            temperature=0.7
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        logger.error(f"Ошибка OpenAI API: {e}")
        return "❌ Ошибка при создании сводки через AI"

async def send_daily_digest(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет ежедневную сводку"""
    if not ADMIN_USER_ID:
        logger.warning("ADMIN_USER_ID не настроен, пропускаем отправку сводки")
        return
    
    try:
        digest_text = await create_digest()
        if digest_text:
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=digest_text,
                parse_mode='Markdown'
            )
            logger.info("Ежедневная сводка отправлена")
        else:
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text="📭 Нет новых сообщений для создания сводки"
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке ежедневной сводки: {e}")

async def setup_mtproto():
    """Настройка MTProto клиента"""
    if not all([API_ID, API_HASH, PHONE_NUMBER]):
        logger.error("MTProto не настроен. Добавьте TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE_NUMBER в .env")
        return None
    
    try:
        client = TelegramClient('session_name', int(API_ID), API_HASH)
        await client.start(phone=PHONE_NUMBER)
        logger.info("MTProto клиент подключен")
        return client
    except Exception as e:
        logger.error(f"Ошибка подключения MTProto: {e}")
        return None

async def collect_messages():
    """Сбор сообщений из каналов"""
    if not message_store.client:
        return
    
    try:
        for channel_id in message_store.monitored_channels:
            try:
                entity = await message_store.client.get_entity(int(channel_id))
                messages = await message_store.client.get_messages(entity, limit=50)
                
                for msg in messages:
                    if msg.text:
                        message_data = {
                            'message_id': msg.id,
                            'text': msg.text,
                            'timestamp': msg.date.isoformat(),
                            'chat_title': getattr(entity, 'title', 'Unknown'),
                            'chat_username': getattr(entity, 'username', None),
                            'from_user': 'Channel'
                        }
                        message_store.add_message(channel_id, message_data)
                
                logger.info(f"Собрано {len(messages)} сообщений из канала {getattr(entity, 'title', 'Unknown')}")
                
            except Exception as e:
                logger.error(f"Ошибка при сборе сообщений из канала {channel_id}: {e}")
                
    except Exception as e:
        logger.error(f"Ошибка при сборе сообщений: {e}")

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
    application.add_handler(CommandHandler("list_channels", list_channels))
    
    # Обработчик callback'ов для кнопок
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Настраиваем ежедневную отправку сводки
    schedule.every().day.at(DIGEST_TIME).do(
        lambda: asyncio.create_task(send_daily_digest(application))
    )
    
    # Запускаем планировщик в отдельном потоке
    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(60)
    
    import threading
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Запускаем MTProto и сбор сообщений
    async def run_bot():
        # Настраиваем MTProto
        client = await setup_mtproto()
        if client:
            message_store.set_client(client)
            
            # Запускаем периодический сбор сообщений
            async def collect_periodic():
                while True:
                    await collect_messages()
                    await asyncio.sleep(300)  # Каждые 5 минут
            
            asyncio.create_task(collect_periodic())
        
        # Запускаем бота
        await application.run_polling()
    
    # Запускаем бота
    logger.info("Бот запущен")
    asyncio.run(run_bot())

if __name__ == '__main__':
    main()

