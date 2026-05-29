import asyncio
import json
import os
import logging
import signal
import time
import requests
import psutil
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncpg
import csv
from io import StringIO, BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
import aiogram
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# ==================== НАСТРОЙКИ ====================
DEFAULT_BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [5683729883]
DATABASE_URL = "postgresql://neondb_owner:npg_ZS6yvHDEwa1G@ep-winter-dust-al1pbd4y.c-3.eu-central-1.aws.neon.tech/neondb?sslmode=require"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Глобальные переменные для отслеживания времени запуска
start_time = time.time()

# Глобальные переменные для текущего бота
current_bot_token = DEFAULT_BOT_TOKEN
current_bot_name = "RobotChoiceBot"
bot = None
dp = None

# Флаг для остановки polling
polling_task = None
is_polling_running = False

# ==================== ОБРАБОТКА КОНФЛИКТА ЭКЗЕМПЛЯРОВ ====================
import sys as _sys
import signal as _signal

_original_excepthook = _sys.excepthook
def _conflict_handler(exc_type, exc_value, exc_traceback):
    error_msg = str(exc_value)
    if "Conflict" in error_msg and "terminated by other getUpdates" in error_msg:
        logger.critical("🔴 Обнаружен конфликт с другим экземпляром бота.")
        logger.critical("🔴 Завершаем этот процесс — Render перезапустит с чистого листа.")
        os.kill(os.getpid(), _signal.SIGTERM)
    else:
        _original_excepthook(exc_type, exc_value, exc_traceback)

_sys.excepthook = _conflict_handler

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def parse_utm_from_start_param(start_param: str) -> dict:
    """Парсит UTM-метки из параметра start"""
    if not start_param:
        return {}
    
    utm_data = {}
    
    if '__' in start_param:
        parts = start_param.split('__')
        utm_data['start_param'] = parts[0] if len(parts) > 0 else ''
        utm_data['utm_source'] = parts[1] if len(parts) > 1 else ''
        utm_data['utm_medium'] = parts[2] if len(parts) > 2 else ''
        utm_data['utm_campaign'] = parts[3] if len(parts) > 3 else ''
        utm_data['utm_term'] = parts[4] if len(parts) > 4 else ''
        utm_data['utm_content'] = parts[5] if len(parts) > 5 else ''
        
        logger.info(f"✅ UTM parsed from '{start_param}': {utm_data}")
        return utm_data
    
    utm_data['start_param'] = start_param
    logger.info(f"⚠️ No UTM found in '{start_param}', using as simple start_param")
    return utm_data

# ==================== БАЗА ДАННЫХ ====================
class Database:
    def __init__(self):
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL)
        await self.init_tables()
        logger.info("✅ База данных подключена")

    async def init_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS bot_instances (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    bot_token TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            await conn.execute('ALTER TABLE bot_instances ADD COLUMN IF NOT EXISTS managed_by_crm BOOLEAN DEFAULT TRUE')
            
            await conn.execute('''
                DELETE FROM bot_instances 
                WHERE id NOT IN (SELECT MIN(id) FROM bot_instances GROUP BY name)
            ''')
            
            row = await conn.fetchrow('SELECT COUNT(*) FROM bot_instances')
            if row['count'] == 0:
                await conn.execute('''
                    INSERT INTO bot_instances (name, bot_token, is_active)
                    VALUES ('RobotChoiceBot', $1, TRUE)
                ''', DEFAULT_BOT_TOKEN)
            else:
                active = await conn.fetchval('SELECT COUNT(*) FROM bot_instances WHERE is_active = TRUE')
                if active == 0:
                    await conn.execute('UPDATE bot_instances SET is_active = TRUE ORDER BY id LIMIT 1')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    first_seen TIMESTAMP DEFAULT NOW(),
                    last_active TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS bot_id INTEGER REFERENCES bot_instances(id)')
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS utm_source TEXT')
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS utm_medium TEXT')
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS utm_campaign TEXT')
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS utm_content TEXT')
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS utm_term TEXT')
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer TEXT')
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS start_param TEXT')
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS gclid TEXT')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS chats (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    dialog_status TEXT DEFAULT 'первое сообщение',
                    auto_mode BOOLEAN DEFAULT TRUE,
                    is_blocked BOOLEAN DEFAULT FALSE,
                    tags TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    last_message_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            await conn.execute('ALTER TABLE chats ADD COLUMN IF NOT EXISTS bot_id INTEGER REFERENCES bot_instances(id)')
            
            active_bot = await conn.fetchrow('SELECT id FROM bot_instances WHERE is_active = TRUE LIMIT 1')
            if active_bot:
                await conn.execute('UPDATE chats SET bot_id = $1 WHERE bot_id IS NULL', active_bot['id'])
                await conn.execute('UPDATE users SET bot_id = $1 WHERE bot_id IS NULL', active_bot['id'])
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    chat_id INTEGER REFERENCES chats(id),
                    sender_type TEXT,
                    message_text TEXT,
                    file_id TEXT,
                    timestamp TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    bot_id INTEGER REFERENCES bot_instances(id),
                    utm_source TEXT,
                    utm_medium TEXT,
                    utm_campaign TEXT,
                    utm_content TEXT,
                    utm_term TEXT,
                    referrer TEXT,
                    start_param TEXT,
                    gclid TEXT,
                    first_interaction TIMESTAMP DEFAULT NOW(),
                    last_interaction TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS crm_stats (
                    id SERIAL PRIMARY KEY,
                    date DATE UNIQUE,
                    new_chats INTEGER DEFAULT 0,
                    active_chats INTEGER DEFAULT 0,
                    closed_chats INTEGER DEFAULT 0,
                    avg_response_time INTEGER DEFAULT 0
                )
            ''')
            
            await conn.execute('ALTER TABLE crm_stats ADD COLUMN IF NOT EXISTS bot_id INTEGER REFERENCES bot_instances(id)')
            
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_bot_id ON users(bot_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_chats_bot_id ON chats(bot_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_utm_source ON users(utm_source)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_utm_campaign ON users(utm_campaign)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON user_sessions(user_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_sessions_created ON user_sessions(first_interaction)')
            
            logger.info("✅ Таблицы созданы")

    async def get_active_bot(self):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM bot_instances WHERE is_active = TRUE LIMIT 1')
            return dict(row) if row else None

    async def set_active_bot(self, bot_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE bot_instances SET is_active = FALSE')
            await conn.execute('UPDATE bot_instances SET is_active = TRUE WHERE id = $1', bot_id)
            row = await conn.fetchrow('SELECT * FROM bot_instances WHERE id = $1', bot_id)
            return dict(row) if row else None

    async def get_all_bots(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT id, name, bot_token, is_active, managed_by_crm FROM bot_instances ORDER BY id')
            return [dict(r) for r in rows]

    async def get_or_create_chat(self, user_id: int, username: str = None, 
                                  full_name: str = None, start_param: str = None):
        async with self.pool.acquire() as conn:
            active_bot = await self.get_active_bot()
            bot_id = active_bot['id'] if active_bot else 1
            
            utm_data = parse_utm_from_start_param(start_param)
            logger.info(f"📊 Parsed UTM for user {user_id}: {utm_data}")
            
            existing_user = await conn.fetchrow('SELECT user_id FROM users WHERE user_id = $1', user_id)
            
            if existing_user:
                await conn.execute('''
                    UPDATE users 
                    SET username = $1, full_name = $2, last_active = NOW()
                    WHERE user_id = $3
                ''', username, full_name, user_id)
                logger.info(f"🔄 Updated existing user {user_id}")
            else:
                await conn.execute('''
                    INSERT INTO users (user_id, username, full_name, bot_id,
                                       utm_source, utm_medium, utm_campaign, 
                                       utm_content, utm_term, referrer, start_param, gclid)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ''', user_id, username, full_name, bot_id,
                   utm_data.get('utm_source'),
                   utm_data.get('utm_medium'),
                   utm_data.get('utm_campaign'),
                   utm_data.get('utm_content'),
                   utm_data.get('utm_term'),
                   utm_data.get('referrer'),
                   utm_data.get('start_param', start_param),
                   utm_data.get('gclid'))
                logger.info(f"✅ Created new user {user_id} with UTM: {utm_data}")
            
            should_add_session = False
            
            existing_session = await conn.fetchrow('''
                SELECT id FROM user_sessions 
                WHERE user_id = $1 
                AND COALESCE(utm_source, '') = $2
                AND COALESCE(utm_medium, '') = $3
                AND COALESCE(utm_campaign, '') = $4
                AND COALESCE(utm_term, '') = $5
                AND COALESCE(utm_content, '') = $6
                LIMIT 1
            ''', user_id,
               utm_data.get('utm_source', ''),
               utm_data.get('utm_medium', ''),
               utm_data.get('utm_campaign', ''),
               utm_data.get('utm_term', ''),
               utm_data.get('utm_content', ''))
            
            if not existing_session:
                should_add_session = True
                if any([utm_data.get('utm_source'), utm_data.get('utm_medium'), 
                        utm_data.get('utm_campaign'), utm_data.get('utm_term'), 
                        utm_data.get('utm_content')]):
                    logger.info(f"📊 New unique UTM combination for user {user_id}: {utm_data}")
                else:
                    logger.info(f"📊 First visit without UTM for user {user_id}")
            else:
                logger.info(f"⏭️ Skipping duplicate UTM for user {user_id}: {utm_data}")
            
            if should_add_session:
                await conn.execute('''
                    INSERT INTO user_sessions (user_id, bot_id, utm_source, utm_medium, 
                                               utm_campaign, utm_content, utm_term, 
                                               referrer, start_param, gclid)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ''', user_id, bot_id,
                   utm_data.get('utm_source'),
                   utm_data.get('utm_medium'),
                   utm_data.get('utm_campaign'),
                   utm_data.get('utm_content'),
                   utm_data.get('utm_term'),
                   utm_data.get('referrer'),
                   utm_data.get('start_param', start_param),
                   utm_data.get('gclid'))
                logger.info(f"✅ Added new session for user {user_id}")
            
            row = await conn.fetchrow('SELECT * FROM chats WHERE user_id = $1 AND bot_id = $2', user_id, bot_id)
            if row:
                return dict(row)
            else:
                row = await conn.fetchrow('''
                    INSERT INTO chats (user_id, bot_id)
                    VALUES ($1, $2)
                    RETURNING *
                ''', user_id, bot_id)
                logger.info(f"💬 Created new chat for user {user_id}")
                return dict(row)

    async def save_message(self, chat_id: int, sender_type: str, message_text: str = None, file_id: str = None):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO messages (chat_id, sender_type, message_text, file_id)
                VALUES ($1, $2, $3, $4)
            ''', chat_id, sender_type, message_text, file_id)
            await conn.execute('''
                UPDATE chats SET last_message_at = NOW() AT TIME ZONE 'Europe/Moscow' WHERE id = $1
            ''', chat_id)

    async def get_messages(self, chat_id: int, limit: int = 100):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT * FROM messages 
                WHERE chat_id = $1 
                ORDER BY timestamp DESC 
                LIMIT $2
            ''', chat_id, limit)
            return [dict(r) for r in reversed(rows)]

    async def get_client_message_count(self, chat_id: int) -> int:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT sender_type FROM messages 
                WHERE chat_id = $1 
                ORDER BY timestamp DESC 
                LIMIT 5
            ''', chat_id)
            
            count = 0
            for row in rows:
                if row['sender_type'] == 'user':
                    count += 1
                else:
                    break
            return count

    async def get_all_chats(self, limit: int = 50, offset: int = 0, status_filter: str = None):
        async with self.pool.acquire() as conn:
            active_bot = await self.get_active_bot()
            bot_id = active_bot['id'] if active_bot else 1
            
            query = '''
                SELECT c.*, u.username, u.full_name, u.utm_source, u.utm_campaign
                FROM chats c
                JOIN users u ON c.user_id = u.user_id
                WHERE c.bot_id = $1 AND c.is_blocked = FALSE
            '''
            params = [bot_id]
            if status_filter:
                query += ' AND c.dialog_status = $' + str(len(params) + 1)
                params.append(status_filter)
            query += ' ORDER BY c.last_message_at DESC LIMIT $' + str(len(params) + 1) + ' OFFSET $' + str(len(params) + 2)
            params.extend([limit, offset])
            rows = await conn.fetch(query, *params)
            return [dict(r) for r in rows]

    async def search_chats(self, search_query: str = None, status_filter: str = None, limit: int = 100):
        async with self.pool.acquire() as conn:
            active_bot = await self.get_active_bot()
            bot_id = active_bot['id'] if active_bot else 1
            
            query = '''
                SELECT DISTINCT c.*, u.username, u.full_name, u.utm_source, u.utm_campaign, u.utm_medium, u.utm_term, u.utm_content, u.user_id,
                       (SELECT message_text FROM messages WHERE chat_id = c.id ORDER BY timestamp DESC LIMIT 1) as last_message_text
                FROM chats c
                JOIN users u ON c.user_id = u.user_id
                LEFT JOIN messages m ON c.id = m.chat_id
                WHERE c.bot_id = $1 AND c.is_blocked = FALSE
            '''
            params = [bot_id]
            param_counter = 2
            
            if status_filter and status_filter != 'all':
                query += f' AND c.dialog_status = ${param_counter}'
                params.append(status_filter)
                param_counter += 1
            
            if search_query and search_query.strip():
                search_term = f'%{search_query.strip()}%'
                query += f''' AND (
                    u.full_name ILIKE ${param_counter} 
                    OR u.username ILIKE ${param_counter}
                    OR CAST(u.user_id AS TEXT) = ${param_counter+1}
                    OR m.message_text ILIKE ${param_counter}
                )'''
                params.append(search_term)
                params.append(search_query.strip())
                param_counter += 2
            
            query += f' ORDER BY c.last_message_at DESC LIMIT ${param_counter}'
            params.append(limit)
            
            rows = await conn.fetch(query, *params)
            return [dict(r) for r in rows]

    async def get_chat_with_utm(self, chat_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT c.*, u.username, u.full_name, u.user_id,
                       u.utm_source, u.utm_medium, u.utm_campaign, u.utm_term, u.utm_content,
                       u.referrer, u.start_param, u.gclid
                FROM chats c
                JOIN users u ON c.user_id = u.user_id
                WHERE c.id = $1
            ''', chat_id)
            return dict(row) if row else None
    
    async def get_user_sessions(self, user_id: int):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                       referrer, start_param, gclid, first_interaction
                FROM user_sessions
                WHERE user_id = $1
                ORDER BY first_interaction DESC
            ''', user_id)
            return [dict(r) for r in rows]

    async def update_dialog_status(self, chat_id: int, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE chats SET dialog_status = $1 WHERE id = $2
            ''', status, chat_id)

    async def toggle_auto_mode(self, chat_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE chats SET auto_mode = NOT auto_mode WHERE id = $1
            ''', chat_id)

    async def get_chat_by_id(self, chat_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT c.*, u.username, u.full_name, u.utm_source, u.utm_campaign, u.utm_medium
                FROM chats c
                JOIN users u ON c.user_id = u.user_id
                WHERE c.id = $1
            ''', chat_id)
            return dict(row) if row else None
    
    async def get_utm_stats(self):
        async with self.pool.acquire() as conn:
            active_bot = await self.get_active_bot()
            bot_id = active_bot['id'] if active_bot else 1
            
            sources = await conn.fetch('''
                SELECT utm_source, COUNT(*) as count 
                FROM users 
                WHERE utm_source IS NOT NULL AND bot_id = $1
                GROUP BY utm_source 
                ORDER BY count DESC
            ''', bot_id)
            
            campaigns = await conn.fetch('''
                SELECT utm_campaign, COUNT(*) as count 
                FROM users 
                WHERE utm_campaign IS NOT NULL AND bot_id = $1
                GROUP BY utm_campaign 
                ORDER BY count DESC
            ''', bot_id)
            
            conversions = await conn.fetch('''
                SELECT u.utm_source, COUNT(DISTINCT u.user_id) as count
                FROM users u
                JOIN chats c ON u.user_id = c.user_id
                WHERE c.bot_id = $1 AND (c.dialog_status = 'ожидание менеджера' OR c.dialog_status = 'в работе')
                  AND u.utm_source IS NOT NULL
                GROUP BY u.utm_source
                ORDER BY count DESC
            ''', bot_id)
            
            return {
                "sources": [dict(r) for r in sources],
                "campaigns": [dict(r) for r in campaigns],
                "conversions": [dict(r) for r in conversions]
            }

# ==================== БОТ ====================
db = Database()

def create_bot_and_dispatcher():
    global bot, dp
    if not DEFAULT_BOT_TOKEN:
        logger.error("❌ Cannot create bot: BOT_TOKEN not set")
        return False
    
    bot = Bot(token=DEFAULT_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    register_handlers()
    logger.info(f"🤖 Bot instance created for token: {DEFAULT_BOT_TOKEN[:10]}...")
    return True

async def update_bot_instance(new_token: str, new_name: str):
    global bot, dp, current_bot_token, current_bot_name, polling_task, is_polling_running
    
    # Останавливаем текущий polling если он запущен
    if polling_task and not polling_task.done():
        is_polling_running = False
        if bot:
            try:
                await bot.delete_webhook(drop_pending_updates=True)
                await bot.session.close()
                logger.info("✅ Old bot session closed")
            except Exception as e:
                logger.error(f"❌ Error closing old bot: {e}")
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    
    current_bot_token = new_token
    current_bot_name = new_name
    
    # Принудительно сбрасываем соединения перед созданием нового бота
    try:
        url = f"https://api.telegram.org/bot{new_token}/deleteWebhook"
        requests.post(url, json={"drop_pending_updates": True}, timeout=10)
        logger.info(f"✅ Webhook deleted for new bot: {new_name}")
        await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"❌ Failed to reset connections for new bot: {e}")
    
    # Создаём нового бота
    bot = Bot(token=current_bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    register_handlers()
    
    # Перезапускаем polling с новым ботом
    if is_polling_running:
        async def run_polling():
            try:
                await bot.delete_webhook(drop_pending_updates=True)
                logger.info(f"✅ Webhook deleted, starting polling for {new_name}")
                await asyncio.sleep(1)
                await dp.start_polling(bot)
            except asyncio.CancelledError:
                logger.info("Polling task cancelled")
            except Exception as e:
                logger.error(f"Polling error for {new_name}: {e}")
        
        polling_task = asyncio.create_task(run_polling())
    
    logger.info(f"✅ Бот переключён на: {current_bot_name}")

def register_handlers():
    @dp.message(Command("start"))
    async def cmd_start(message: types.Message):
        user = message.from_user
        start_param = message.text.replace('/start', '').strip()
        if start_param.startswith(' '):
            start_param = start_param[1:]
        if start_param == '':
            start_param = None
        
        chat_data = await db.get_or_create_chat(
            user.id, user.username, user.full_name, start_param
        )
        
        if start_param:
            logger.info(f"New user {user.id} from: {start_param}")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Посмотреть услуги", callback_data="services")],
            [InlineKeyboardButton(text="💬 Связаться с менеджером", callback_data="contact_manager")]
        ])
        
        await message.answer(
            "🤖 *Добро пожаловать в RobotChoiceBot!*\n\n"
            "Я помогу вам подобрать и настроить торгового робота для биржи.\n\n"
            "📌 *Что вы можете сделать:*\n"
            "• Посмотреть список доступных роботов\n"
            "• Получить консультацию\n"
            "• Задать вопрос менеджеру\n\n"
            "👇 *Выберите действие:*",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        
        await db.save_message(chat_data['id'], 'user', f"/start от {user.username or user.full_name}")

    @dp.callback_query()
    async def handle_callback(callback: types.CallbackQuery):
        chat_data = await db.get_or_create_chat(callback.from_user.id)
        
        if callback.data == "services":
            text = (
                "📊 *Наши торговые роботы:*\n\n"
                "1. 🤖 *Gold Master* — робот для торговли золотом. Доходность: +15-25% в месяц.\n"
                "2. 🤖 *Profit Hunter* — агрессивная стратегия. Доходность: +20-35%.\n"
                "3. 🤖 *Smart Prime* — консервативный. Доходность: +8-12%.\n\n"
                "💰 *Условия:*\n"
                "• Пополнение от $100\n"
                "• VPS в подарок\n"
                "• Бонус до 100% на депозит\n\n"
                "👉 *Хотите подключить робота?* Напишите менеджеру!"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📞 Связаться с менеджером", callback_data="contact_manager")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back")]
            ])
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
            await db.save_message(chat_data['id'], 'user', callback.data)
            await db.save_message(chat_data['id'], 'bot', text)
            
        elif callback.data == "contact_manager":
            await db.update_dialog_status(chat_data['id'], 'ожидание менеджера')
            text = (
                "📞 *Менеджер скоро свяжется с вами!*\n\n"
                "А пока вы можете:\n"
                "• Посмотреть статистику роботов\n"
                "• Задать вопрос в этом чате\n\n"
                "Ожидайте ответа в течение 15 минут."
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 Статистика роботов", callback_data="stats")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back")]
            ])
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
            await db.save_message(chat_data['id'], 'user', callback.data)
            await db.save_message(chat_data['id'], 'bot', text)
            
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"🆕 *Новый клиент!*\n\n"
                        f"Пользователь: @{callback.from_user.username or callback.from_user.full_name}\n"
                        f"ID: {callback.from_user.id}\n"
                        f"Статус: ожидает менеджера",
                        parse_mode="Markdown"
                    )
                except:
                    pass
                    
        elif callback.data == "stats":
            text = (
                "📈 *Живая статистика роботов:*\n\n"
                "• GBPUSD — +5,61% — $8,198\n"
                "• PROFIT HUNTER — +2,87% — $2,052\n"
                "• GOLD MASTER — +1,90% — $2,756\n"
                "• EA Smart Prime — +1,73% — $1,902\n\n"
                "🔗 [Смотреть полную статистику](https://www.myfxbook.com/)\n\n"
                "*Как подключить робота?*\n"
                "1. Откройте счёт в NPB Markets\n"
                "2. Я подключаю робота сегодня\n"
                "3. Протестируйте и решите сами"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📞 Хочу подключить", callback_data="contact_manager")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="services")]
            ])
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
            await db.save_message(chat_data['id'], 'user', callback.data)
            await db.save_message(chat_data['id'], 'bot', text)
            
        elif callback.data == "back":
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 Посмотреть услуги", callback_data="services")],
                [InlineKeyboardButton(text="💬 Связаться с менеджером", callback_data="contact_manager")]
            ])
            await callback.message.edit_text(
                "🤖 *Добро пожаловать в RobotChoiceBot!*\n\n"
                "👇 *Выберите действие:*",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            await db.save_message(chat_data['id'], 'user', callback.data)
            await db.save_message(chat_data['id'], 'bot', "Возврат в главное меню")
        
        await callback.answer()

    @dp.message()
    async def handle_message(message: types.Message):
        # Находим бота в базе по токену
        async with db.pool.acquire() as conn:
            bot_row = await conn.fetchrow(
                "SELECT * FROM bot_instances WHERE bot_token = $1",
                DEFAULT_BOT_TOKEN
            )
        
        if not bot_row:
            return
        
        bot_id = bot_row['id']
        
        if not bot_row.get('managed_by_crm', True):
            return
        
        user = message.from_user
        
        # Сохраняем пользователя и получаем/создаём чат
        async with db.pool.acquire() as conn:
            existing_user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", user.id)
            
            if existing_user:
                await conn.execute(
                    "UPDATE users SET username = $1, full_name = $2, last_active = NOW() WHERE user_id = $3",
                    user.username, user.full_name, user.id
                )
            else:
                await conn.execute(
                    """INSERT INTO users (user_id, username, full_name, bot_id)
                    VALUES ($1, $2, $3, $4)""",
                    user.id, user.username, user.full_name, bot_id
                )
            
            chat_row = await conn.fetchrow(
                "SELECT * FROM chats WHERE user_id = $1 AND bot_id = $2",
                user.id, bot_id
            )
            
            if not chat_row:
                chat_row = await conn.fetchrow(
                    "INSERT INTO chats (user_id, bot_id) VALUES ($1, $2) RETURNING *",
                    user.id, bot_id
                )
            
            chat_id = chat_row['id']
        
        # Сохраняем сообщение через db.save_message (уже с правильным chat_id)
        await db.save_message(chat_id, 'user', message.text, 
                              message.document.file_id if message.document else None)
        
        # Получаем данные чата для автоответа
        chat_data = await db.get_chat_by_id(chat_id)
        
        if chat_data['auto_mode']:
            client_message_count = await db.get_client_message_count(chat_id)
            
            if client_message_count >= 3:
                response = (
                    "✅ *Ваше сообщение получено!*\n\n"
                    "Менеджер скоро свяжется с вами. Ожидайте ответа в течение 15 минут.\n\n"
                    "А пока вы можете:\n"
                    "• Посмотреть наших роботов → /start\n"
                    "• Изучить статистику"
                )
                await message.answer(response, parse_mode="Markdown")
                await db.save_message(chat_id, 'bot', response)
        else:
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"💬 *Новое сообщение от пользователя!*\n\n"
                        f"Пользователь: @{user.username or user.full_name}\n"
                        f"ID: {user.id}\n"
                        f"Сообщение: {message.text[:200]}",
                        parse_mode="Markdown"
                    )
                except:
                    pass

# ==================== API ДЛЯ CRM ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global polling_task, is_polling_running, bot, dp
    
    logger.info("⏳ Waiting for old processes to terminate...")
    await asyncio.sleep(5)
    
    await db.connect()
    logger.info("✅ База данных подключена")
    
    if not bot:
        if create_bot_and_dispatcher():
            logger.info("🤖 Bot created in lifespan")
        else:
            logger.error("❌ Failed to create bot in lifespan")
    
    active_bot = await db.get_active_bot()
    if active_bot and active_bot.get('managed_by_crm', True) and bot and not is_polling_running:
        logger.info(f"🤖 Активный бот '{active_bot['name']}' управляется CRM — запускаем polling")
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            logger.info("✅ Webhook deleted before polling start")
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"❌ Failed to delete webhook: {e}")
        
        is_polling_running = True
        async def run_polling():
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    logger.info(f"🔄 Starting polling (attempt {attempt + 1}/{max_retries})")
                    await dp.start_polling(bot)
                    break
                except Exception as e:
                    logger.error(f"❌ Polling error (attempt {attempt + 1}): {e}")
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        logger.info(f"⏳ Waiting {wait_time}s before retry...")
                        await asyncio.sleep(wait_time)
                        try:
                            await bot.delete_webhook(drop_pending_updates=True)
                            await asyncio.sleep(1)
                        except:
                            pass
                    else:
                        logger.error("❌ All polling attempts failed")
        
        polling_task = asyncio.create_task(run_polling())
        logger.info("🤖 Бот запущен в режиме Long Polling")
    elif active_bot and not active_bot.get('managed_by_crm', True):
        logger.info(f"👁️ Активный бот '{active_bot['name']}' в режиме только просмотр — polling не запущен")
    
    yield
    
    logger.info("🔄 Shutting down...")
    is_polling_running = False
    if polling_task and not polling_task.done():
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    if bot:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await bot.session.close()
            logger.info("✅ Bot session closed")
        except Exception as e:
            logger.error(f"❌ Error during bot shutdown: {e}")
    if db.pool:
        await db.pool.close()
    logger.info("✅ Shutdown complete")

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
@app.head("/")
async def root():
    return RedirectResponse(url="/dashboard")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    return {"ok": False, "description": "Webhook disabled, use polling"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/metrics")
async def get_metrics():
    process = psutil.Process()
    memory_info = process.memory_info()
    
    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory_mb = memory_info.rss / 1024 / 1024
    memory_percent = process.memory_percent()
    uptime_seconds = time.time() - start_time
    
    uptime_days = int(uptime_seconds // 86400)
    uptime_hours = int((uptime_seconds % 86400) // 3600)
    uptime_minutes = int((uptime_seconds % 3600) // 60)
    
    uptime_str = ""
    if uptime_days > 0:
        uptime_str += f"{uptime_days}д "
    if uptime_hours > 0 or uptime_days > 0:
        uptime_str += f"{uptime_hours}ч "
    uptime_str += f"{uptime_minutes}м"
    
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "cpu": {"percent": round(cpu_percent, 2), "cores": psutil.cpu_count()},
        "memory": {"used_mb": round(memory_mb, 2), "percent": round(memory_percent, 2), "limit_mb": 512, "warning": memory_mb > 400},
        "uptime": {"seconds": round(uptime_seconds, 0), "human": uptime_str},
        "database": {"connected": db.pool is not None},
        "bot": {"token_configured": bool(DEFAULT_BOT_TOKEN), "polling_active": is_polling_running}
    }

@app.get("/api/bots")
async def get_bots():
    bots = await db.get_all_bots()
    return {"bots": bots}

@app.post("/api/bots/switch")
async def switch_bot(request: Request):
    data = await request.json()
    bot_id = data.get("bot_id")
    
    bot_data = await db.set_active_bot(bot_id)
    if not bot_data:
        raise HTTPException(status_code=404, detail="Bot not found")
    
    if bot_data.get('managed_by_crm', True):
        await update_bot_instance(bot_data['bot_token'], bot_data['name'])
    else:
        global current_bot_name
        current_bot_name = bot_data['name']
        # НЕ трогаем polling/вебхук для наблюдаемых ботов
        logger.info(f"👁️ Переключён контекст на наблюдаемого бота: {current_bot_name}")
    
    return {
        "status": "switched", 
        "bot_name": bot_data['name'],
        "managed_by_crm": bot_data.get('managed_by_crm', True)
    }

@app.get("/api/chats")
async def get_chats(limit: int = 50, offset: int = 0, status: str = None):
    chats = await db.get_all_chats(limit, offset, status)
    return {"chats": chats, "total": len(chats)}

@app.get("/api/chats/search")
async def search_chats_endpoint(
    q: str = Query(None),
    status: str = Query(None),
    limit: int = Query(100)
):
    chats = await db.search_chats(search_query=q, status_filter=status, limit=limit)
    return {"chats": chats, "total": len(chats)}

@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: int):
    chat = await db.get_chat_by_id(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat

@app.get("/api/chats/{chat_id}/full")
async def get_chat_full(chat_id: int):
    chat = await db.get_chat_with_utm(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat

@app.get("/api/users/{user_id}/sessions")
async def get_user_sessions(user_id: int):
    sessions = await db.get_user_sessions(user_id)
    return {"sessions": sessions}

@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: int, limit: int = 100):
    messages = await db.get_messages(chat_id, limit)
    return {"messages": messages}

@app.post("/api/chats/{chat_id}/send")
async def send_message(chat_id: int, request: Request):
    data = await request.json()
    message_text = data.get("message")
    
    if not message_text:
        raise HTTPException(status_code=400, detail="Message is required")
    
    chat = await db.get_chat_by_id(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    try:
        await bot.send_message(chat['user_id'], message_text)
        await db.save_message(chat_id, 'operator', message_text)
        return {"status": "sent", "message": message_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chats/{chat_id}/status")
async def update_status(chat_id: int, request: Request):
    data = await request.json()
    status = data.get("status")
    if not status:
        raise HTTPException(status_code=400, detail="Status is required")
    
    await db.update_dialog_status(chat_id, status)
    return {"status": "updated"}

@app.post("/api/chats/{chat_id}/toggle-auto")
async def toggle_auto(chat_id: int):
    await db.toggle_auto_mode(chat_id)
    return {"status": "toggled"}

@app.post("/api/chats/{chat_id}/mark-read")
async def mark_read(chat_id: int):
    return {"status": "ok"}

@app.post("/api/messages/{message_id}/delete")
async def delete_message(message_id: int):
    async with db.pool.acquire() as conn:
        result = await conn.execute('DELETE FROM messages WHERE id = $1', message_id)
        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Message not found")
        return {"status": "deleted"}

@app.get("/api/utm_stats")
async def get_utm_stats():
    stats = await db.get_utm_stats()
    return stats

@app.get("/api/export/csv")
async def export_csv(chat_id: int = None):
    from io import StringIO
    from datetime import datetime
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID чата", "ID пользователя", "Имя", "Статус", "Авторежим", "UTM Source", "UTM Campaign", "Дата создания", "Последнее сообщение"])
    
    if chat_id:
        chat = await db.get_chat_by_id(chat_id)
        if chat:
            writer.writerow([chat['id'], chat['user_id'], chat.get('full_name', ''), chat.get('dialog_status', ''), chat.get('auto_mode', ''), chat.get('utm_source', ''), chat.get('utm_campaign', ''), chat.get('created_at', ''), chat.get('last_message_at', '')])
    else:
        chats = await db.get_all_chats(limit=1000)
        for chat in chats:
            writer.writerow([chat['id'], chat['user_id'], chat.get('full_name', ''), chat.get('dialog_status', ''), chat.get('auto_mode', ''), chat.get('utm_source', ''), chat.get('utm_campaign', ''), chat.get('created_at', ''), chat.get('last_message_at', '')])
    
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=chats_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"})

@app.get("/api/export/pdf")
async def export_pdf(chat_id: int = None):
    from datetime import datetime
    from io import BytesIO
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=16, textColor=colors.HexColor('#2b5278'))
    story.append(Paragraph("RobotChoiceBot CRM - Отчёт по чатам", title_style))
    story.append(Spacer(1, 0.2*inch))
    
    if chat_id:
        chat = await db.get_chat_by_id(chat_id)
        data = [[chat['id'], chat['user_id'], chat.get('full_name', ''), chat.get('dialog_status', '')]]
    else:
        chats = await db.get_all_chats(limit=500)
        data = [[c['id'], c['user_id'], c.get('full_name', ''), c.get('dialog_status', '')] for c in chats]
    
    table = Table([["ID чата", "ID пользователя", "Имя", "Статус"]] + data)
    table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.grey), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('GRID', (0,0), (-1,-1), 1, colors.black)]))
    story.append(table)
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph(f"Всего записей: {len(data)}", styles['Normal']))
    
    doc.build(story)
    buffer.seek(0)
    
    return StreamingResponse(buffer, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=chats_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"})

@app.get("/api/export/period")
async def export_period(from_date: str, to_date: str):
    from io import StringIO
    from datetime import datetime
    
    try:
        date_from = datetime.strptime(from_date, "%Y-%m-%d")
        date_to = datetime.strptime(to_date, "%Y-%m-%d")
    except:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID чата", "ID пользователя", "Имя", "Статус", "Авторежим", "UTM Source", "UTM Campaign", "Создан", "Последнее сообщение"])
    
    chats = await db.get_all_chats(limit=5000)
    filtered_chats = []
    
    for chat in chats:
        created_at = chat.get('created_at')
        if created_at:
            if isinstance(created_at, datetime):
                created_date = created_at.date()
            else:
                created_date = datetime.fromisoformat(str(created_at)).date()
            if date_from.date() <= created_date <= date_to.date():
                filtered_chats.append(chat)
    
    for chat in filtered_chats:
        writer.writerow([chat['id'], chat['user_id'], chat.get('full_name', ''), chat.get('dialog_status', ''), chat.get('auto_mode', ''), chat.get('utm_source', ''), chat.get('utm_campaign', ''), chat.get('created_at', ''), chat.get('last_message_at', '')])
    
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=chats_{from_date}_{to_date}.csv"})

templates_storage = []

@app.get("/api/templates")
async def get_templates():
    return {"templates": templates_storage}

@app.post("/api/templates")
async def create_template(request: Request):
    data = await request.json()
    title = data.get("title")
    text = data.get("text")
    
    if not title or not text:
        raise HTTPException(status_code=400, detail="Title and text are required")
    
    template_id = len(templates_storage) + 1
    templates_storage.append({"id": template_id, "title": title, "text": text, "created_at": datetime.now().isoformat()})
    return {"id": template_id, "title": title, "text": text}

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
