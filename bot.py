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

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', 0))
DIGEST_TIME = os.getenv('DIGEST_TIME', '19:00')

# Инициализация OpenAI
openai.api_key = OPENAI_API_KEY

# Хранилище данных (в памяти, для простоты)
class MessageStore:
    def __init__(self):
        self.messages = defaultdict(list)  # chat_id -> messages
        self.channels = {}  # chat_id -> channel_info
        self.monitored_channels = set()  # каналы для мониторинга
        self.user_states = {}  # состояния пользователей для интерфейса
        self.folders = {}  # folder_id -> folder_info (для папок/вкладок)
        self.monitored_folders = set()  # папки для мониторинга
    
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
    
    def get_all_channels(self) -> List[dict]:
        """Возвращает все каналы, где есть сообщения"""
        return list(self.channels.values())
    
    def add_folder(self, folder_id: str, folder_info: dict):
        """Добавляет папку для мониторинга"""
        self.folders[folder_id] = folder_info
        self.monitored_folders.add(folder_id)
    
    def remove_folder(self, folder_id: str):
        """Удаляет папку из мониторинга"""
        self.monitored_folders.discard(folder_id)
    
    def get_monitored_folders(self) -> List[dict]:
        """Возвращает список отслеживаемых папок"""
        return [self.folders.get(f_id, {'id': f_id, 'title': 'Unknown'}) 
                for f_id in self.monitored_folders]
    
    def get_all_folders(self) -> List[dict]:
        """Возвращает все папки"""
        return list(self.folders.values())
    
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
🤖 **Telegram Digest Bot**

Привет! Я помогу вам создавать ежедневные сводки важных сообщений из каналов и папок.

**Доступные команды:**
• `/digest` - получить сводку сейчас
• `/manage_channels` - управление каналами для анализа
• `/manage_folders` - управление папками/вкладками
• `/list_channels` - список отслеживаемых каналов
• `/help` - справка

**Как использовать:**
1. Добавьте меня в нужные каналы
2. Настройте список каналов и папок для анализа
3. Получайте ежедневные сводки в 19:00 вечера
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
• `/manage_folders` - управление папками/вкладками
• `/list_channels` - список отслеживаемых каналов

**Как настроить анализ каналов:**
1. Добавьте бота в нужные каналы как администратора
2. Используйте команду `/manage_channels` для настройки
3. Выберите каналы для анализа через удобный интерфейс
4. Бот начнет собирать сообщения из выбранных каналов

**Анализ папок/вкладок:**
• Используйте `/manage_folders` для выбора папок
• Бот будет анализировать все каналы в выбранных папках

**Ежедневные сводки:**
Бот автоматически отправляет сводки каждый день в 19:00 вечера
    """
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def manage_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /manage_channels - показывает интерфейс управления каналами"""
    user_id = update.effective_user.id
    
    # Получаем все каналы, где есть сообщения
    all_channels = message_store.get_all_channels()
    monitored_channels = message_store.get_monitored_channels()
    monitored_ids = {channel['id'] for channel in monitored_channels}
    
    if not all_channels:
        await update.message.reply_text(
            "📭 Пока нет каналов для анализа.\n\n"
            "Добавьте меня в каналы как администратора, и я начну собирать сообщения!"
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
    
    status_text = f"📋 **Управление каналами для анализа**\n\n"
    status_text += f"Отслеживается: {len(monitored_channels)} из {len(all_channels)} каналов\n\n"
    status_text += "Нажмите на канал, чтобы включить/выключить его анализ:"
    
    await update.message.reply_text(status_text, reply_markup=reply_markup, parse_mode='Markdown')

async def manage_folders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /manage_folders - показывает интерфейс управления папками"""
    user_id = update.effective_user.id
    
    # Получаем все папки
    all_folders = message_store.get_all_folders()
    monitored_folders = message_store.get_monitored_folders()
    monitored_ids = {folder['id'] for folder in monitored_folders}
    
    if not all_folders:
        await update.message.reply_text(
            "📁 Пока нет папок для анализа.\n\n"
            "Папки будут добавлены автоматически при обнаружении."
        )
        return
    
    # Создаем клавиатуру с папками
    keyboard = []
    for folder in all_folders:
        folder_id = folder['id']
        folder_title = folder['title']
        is_monitored = folder_id in monitored_ids
        
        # Создаем кнопку с индикатором статуса
        status_emoji = "✅" if is_monitored else "❌"
        button_text = f"{status_emoji} {folder_title}"
        
        keyboard.append([InlineKeyboardButton(
            button_text, 
            callback_data=f"toggle_folder:{folder_id}"
        )])
    
    # Добавляем кнопки управления
    keyboard.append([
        InlineKeyboardButton("🔄 Обновить", callback_data="refresh_folders"),
        InlineKeyboardButton("✅ Выбрать все", callback_data="select_all_folders"),
        InlineKeyboardButton("❌ Снять все", callback_data="deselect_all_folders")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    status_text = f"📁 **Управление папками для анализа**\n\n"
    status_text += f"Отслеживается: {len(monitored_folders)} из {len(all_folders)} папок\n\n"
    status_text += "Нажмите на папку, чтобы включить/выключить анализ всех каналов в ней:"
    
    await update.message.reply_text(status_text, reply_markup=reply_markup, parse_mode='Markdown')

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
    
    # Обработка папок
    elif data.startswith("toggle_folder:"):
        folder_id = data.split(":")[1]
        folder_info = message_store.folders.get(folder_id)
        
        if not folder_info:
            await query.edit_message_text("❌ Папка не найдена")
            return
        
        # Переключаем статус папки
        if folder_id in message_store.monitored_folders:
            message_store.remove_folder(folder_id)
            status = "❌ отключена"
        else:
            message_store.add_folder(folder_id, folder_info)
            status = "✅ включена"
        
        await query.edit_message_text(f"Папка **{folder_info['title']}** {status} для анализа", parse_mode='Markdown')
        
        # Показываем обновленный интерфейс
        await manage_folders(update, context)
    
    elif data == "refresh_folders":
        await manage_folders(update, context)
    
    elif data == "select_all_folders":
        # Включаем все папки
        for folder_id, folder_info in message_store.folders.items():
            message_store.add_folder(folder_id, folder_info)
        
        await query.edit_message_text("✅ Все папки включены для анализа")
        await manage_folders(update, context)
    
    elif data == "deselect_all_folders":
        # Отключаем все папки
        message_store.monitored_folders.clear()
        
        await query.edit_message_text("❌ Все папки отключены от анализа")
        await manage_folders(update, context)

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /list_channels"""
    channels = message_store.get_monitored_channels()
    folders = message_store.get_monitored_folders()
    
    if not channels and not folders:
        await update.message.reply_text("📋 Список отслеживаемых каналов и папок пуст")
        return
    
    response_text = ""
    
    if channels:
        response_text += "📋 **Отслеживаемые каналы:**\n\n"
        for i, channel in enumerate(channels, 1):
            username = f"@{channel.get('username', 'private')}" if channel.get('username') else "Приватный канал"
            response_text += f"{i}. **{channel['title']}** ({username})\n"
        response_text += "\n"
    
    if folders:
        response_text += "📁 **Отслеживаемые папки:**\n\n"
        for i, folder in enumerate(folders, 1):
            response_text += f"{i}. **{folder['title']}**\n"
    
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех сообщений"""
    logger.info(f"Получено сообщение: тип={update.message.chat.type}, от={update.effective_user.id if update.effective_user else 'Unknown'}, текст='{update.message.text[:50] if update.message.text else 'None'}...'")
    
    # Сохраняем сообщения только из каналов и групп
    if update.message.chat.type in ['channel', 'supergroup', 'group']:
        chat_id = str(update.message.chat.id)
        
        # Сохраняем информацию о чате, если её еще нет
        if chat_id not in message_store.channels:
            chat_info = {
                'id': chat_id,
                'title': update.message.chat.title,
                'username': update.message.chat.username,
                'type': update.message.chat.type
            }
            message_store.channels[chat_id] = chat_info
        
        # Сохраняем сообщение
        message_data = {
            'message_id': update.message.message_id,
            'text': update.message.text or update.message.caption or '',
            'timestamp': update.message.date.isoformat(),
            'chat_title': update.message.chat.title,
            'chat_username': update.message.chat.username,
            'from_user': update.message.from_user.full_name if update.message.from_user else 'Unknown'
        }
        
        message_store.add_message(chat_id, message_data)
        logger.info(f"Сохранено сообщение из {update.message.chat.type} {update.message.chat.title}")
    else:
        logger.info(f"Сообщение из личного чата, игнорируем")

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

def main():
    """Основная функция"""
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не настроен")
        return
    
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY не настроен")
        return
    
    # Создаем приложение
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("digest", digest_command))
    application.add_handler(CommandHandler("manage_channels", manage_channels))
    application.add_handler(CommandHandler("manage_folders", manage_folders))
    application.add_handler(CommandHandler("list_channels", list_channels))
    
    # Обработчик callback'ов для кнопок
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Обработчик всех сообщений
    application.add_handler(MessageHandler(filters.ALL, handle_message))
    
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
    
    # Запускаем бота
    logger.info("Бот запущен")
    application.run_polling()

if __name__ == '__main__':
    main()
