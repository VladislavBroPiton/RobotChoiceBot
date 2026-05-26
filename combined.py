import asyncio
import json
import os
import logging
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
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [5683729883]
DATABASE_URL = "postgresql://neondb_owner:npg_ZS6yvHDEwa1G@ep-winter-dust-al1pbd4y.c-3.eu-central-1.aws.neon.tech/neondb?sslmode=require"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
            # Таблица пользователей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    first_seen TIMESTAMP DEFAULT NOW(),
                    last_active TIMESTAMP DEFAULT NOW()
                )
            ''')
            # Таблица чатов (диалогов)
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
            # Таблица сообщений
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
            # Таблица статистики (для отчётов)
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
            logger.info("✅ Таблицы созданы")

    async def get_or_create_chat(self, user_id: int, username: str = None, full_name: str = None):
        async with self.pool.acquire() as conn:
            # Добавляем/обновляем пользователя
            await conn.execute('''
                INSERT INTO users (user_id, username, full_name)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE
                SET username = EXCLUDED.username, last_active = NOW()
            ''', user_id, username, full_name)
            
            # Получаем или создаём чат
            row = await conn.fetchrow('''
                SELECT * FROM chats WHERE user_id = $1
            ''', user_id)
            if row:
                return dict(row)
            else:
                row = await conn.fetchrow('''
                    INSERT INTO chats (user_id)
                    VALUES ($1)
                    RETURNING *
                ''', user_id)
                return dict(row)

    async def save_message(self, chat_id: int, sender_type: str, message_text: str = None, file_id: str = None):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO messages (chat_id, sender_type, message_text, file_id)
                VALUES ($1, $2, $3, $4)
            ''', chat_id, sender_type, message_text, file_id)
            await conn.execute('''
                UPDATE chats SET last_message_at = NOW() WHERE id = $1
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

    async def get_all_chats(self, limit: int = 50, offset: int = 0, status_filter: str = None):
        async with self.pool.acquire() as conn:
            query = '''
                SELECT c.*, u.username, u.full_name 
                FROM chats c
                JOIN users u ON c.user_id = u.user_id
                WHERE c.is_blocked = FALSE
            '''
            params = []
            if status_filter:
                query += ' AND c.dialog_status = $' + str(len(params) + 1)
                params.append(status_filter)
            query += ' ORDER BY c.last_message_at DESC LIMIT $' + str(len(params) + 1) + ' OFFSET $' + str(len(params) + 2)
            params.extend([limit, offset])
            rows = await conn.fetch(query, *params)
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
                SELECT c.*, u.username, u.full_name 
                FROM chats c
                JOIN users u ON c.user_id = u.user_id
                WHERE c.id = $1
            ''', chat_id)
            return dict(row) if row else None

# ==================== БОТ ====================
db = Database()
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class Form(StatesGroup):
    waiting_for_answer = State()

# Команда /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    chat_data = await db.get_or_create_chat(user.id, user.username, user.full_name)
    
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

# Обработка callback-кнопок
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
        
        # Уведомление админу
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

# Обработка текстовых сообщений
@dp.message()
async def handle_message(message: types.Message):
    user = message.from_user
    chat_data = await db.get_or_create_chat(user.id, user.username, user.full_name)
    
    # Сохраняем сообщение пользователя
    await db.save_message(chat_data['id'], 'user', message.text, message.document.file_id if message.document else None)
    
    # Если включён авторежим — отвечаем автоматически
    if chat_data['auto_mode']:
        response = (
            "✅ *Ваше сообщение получено!*\n\n"
            "Менеджер скоро свяжется с вами. Ожидайте ответа в течение 15 минут.\n\n"
            "А пока вы можете:\n"
            "• Посмотреть наших роботов → /start\n"
            "• Изучить статистику"
        )
        await message.answer(response, parse_mode="Markdown")
        await db.save_message(chat_data['id'], 'bot', response)
    else:
        # Уведомляем админов о новом сообщении
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
    await db.connect()
    yield
    if db.pool:
        await db.pool.close()

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# Эндпоинт для вебхука
@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    telegram_update = types.Update(**update)
    await dp.feed_webhook_update(bot, telegram_update)
    return {"ok": True}

# Эндпоинт для проверки здоровья
@app.get("/health")
async def health():
    return {"status": "ok"}

# ==================== CRM API ====================

@app.get("/api/chats")
async def get_chats(limit: int = 50, offset: int = 0, status: str = None):
    """Получить список чатов"""
    chats = await db.get_all_chats(limit, offset, status)
    return {"chats": chats, "total": len(chats)}

@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: int):
    """Получить информацию о чате"""
    chat = await db.get_chat_by_id(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat

@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: int, limit: int = 100):
    """Получить сообщения чата"""
    messages = await db.get_messages(chat_id, limit)
    return {"messages": messages}

@app.post("/api/chats/{chat_id}/send")
async def send_message(chat_id: int, request: Request):
    """Отправить сообщение от оператора"""
    data = await request.json()
    message_text = data.get("message")
    
    if not message_text:
        raise HTTPException(status_code=400, detail="Message is required")
    
    chat = await db.get_chat_by_id(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    # Отправляем сообщение в Telegram
    try:
        await bot.send_message(chat['user_id'], message_text)
        await db.save_message(chat_id, 'operator', message_text)
        return {"status": "sent", "message": message_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chats/{chat_id}/status")
async def update_status(chat_id: int, request: Request):
    """Обновить статус диалога"""
    data = await request.json()
    status = data.get("status")
    if not status:
        raise HTTPException(status_code=400, detail="Status is required")
    
    await db.update_dialog_status(chat_id, status)
    return {"status": "updated"}

@app.post("/api/chats/{chat_id}/toggle-auto")
async def toggle_auto(chat_id: int):
    """Переключить режим автообработки"""
    await db.toggle_auto_mode(chat_id)
    return {"status": "toggled"}

@app.post("/api/chats/{chat_id}/mark-read")
async def mark_read(chat_id: int):
    """Отметить чат как прочитанный"""
    return {"status": "ok"}

# ==================== ЭКСПОРТ CSV (без pandas) ====================
@app.get("/api/export/csv")
async def export_csv(chat_id: int = None):
    """Экспорт чатов в CSV"""
    from io import StringIO
    from datetime import datetime
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID чата", "ID пользователя", "Имя", "Статус", "Авторежим", "Дата создания", "Последнее сообщение"])
    
    if chat_id:
        chat = await db.get_chat_by_id(chat_id)
        if chat:
            writer.writerow([
                chat['id'], chat['user_id'], chat.get('full_name', ''),
                chat.get('dialog_status', ''), chat.get('auto_mode', ''),
                chat.get('created_at', ''), chat.get('last_message_at', '')
            ])
    else:
        chats = await db.get_all_chats(limit=1000)
        for chat in chats:
            writer.writerow([
                chat['id'], chat['user_id'], chat.get('full_name', ''),
                chat.get('dialog_status', ''), chat.get('auto_mode', ''),
                chat.get('created_at', ''), chat.get('last_message_at', '')
            ])
    
    response = StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=chats_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"}
    )
    return response

# ==================== ЭКСПОРТ PDF ====================
@app.get("/api/export/pdf")
async def export_pdf(chat_id: int = None):
    """Экспорт чатов в PDF"""
    from datetime import datetime
    from io import BytesIO
    
    buffer = BytesIO()
    
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    # Заголовок
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=16, textColor=colors.HexColor('#2b5278'))
    story.append(Paragraph("RobotChoiceBot CRM - Отчёт по чатам", title_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Получаем данные
    if chat_id:
        chat = await db.get_chat_by_id(chat_id)
        data = [[chat['id'], chat['user_id'], chat.get('full_name', ''), chat.get('dialog_status', '')]]
    else:
        chats = await db.get_all_chats(limit=500)
        data = [[c['id'], c['user_id'], c.get('full_name', ''), c.get('dialog_status', '')] for c in chats]
    
    # Таблица
    table = Table([["ID чата", "ID пользователя", "Имя", "Статус"]] + data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))
    story.append(table)
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph(f"Всего записей: {len(data)}", styles['Normal']))
    
    doc.build(story)
    buffer.seek(0)
    
    response = StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=chats_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"}
    )
    return response

# ==================== ВЕБ-ДАШБОРД ====================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

# Создаём папку для шаблонов
os.makedirs("templates", exist_ok=True)

# Сохраняем HTML-шаблон дашборда
with open("templates/dashboard.html", "w") as f:
    f.write('''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>RobotChoiceBot CRM</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f0f2f5;
            height: 100vh;
            overflow: hidden;
        }
        .header {
            background: #2b5278;
            color: white;
            padding: 16px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .main-content {
            display: flex;
            height: calc(100vh - 65px);
        }
        .chats-sidebar {
            width: 350px;
            background: white;
            border-right: 1px solid #e1e4e8;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .chats-header {
            padding: 16px;
            border-bottom: 1px solid #e1e4e8;
            font-weight: 600;
        }
        .chats-list {
            flex: 1;
            overflow-y: auto;
        }
        .chat-item {
            padding: 14px 16px;
            border-bottom: 1px solid #f0f2f5;
            cursor: pointer;
            transition: background 0.15s;
        }
        .chat-item:hover {
            background: #f8f9fa;
        }
        .chat-item.active {
            background: #e8f0f9;
        }
        .chat-name {
            font-weight: 600;
            color: #333;
            margin-bottom: 4px;
        }
        .chat-status {
            font-size: 12px;
            color: #8e8e93;
        }
        .messages-area {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: #f0f2f5;
        }
        .messages-header {
            background: white;
            padding: 14px 24px;
            border-bottom: 1px solid #e1e4e8;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .messages-container {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .message {
            max-width: 65%;
            padding: 10px 14px;
            border-radius: 12px;
            word-wrap: break-word;
            font-size: 14px;
        }
        .message.user {
            align-self: flex-start;
            background: white;
            color: #333;
        }
        .message.bot {
            align-self: flex-end;
            background: #2b5278;
            color: white;
        }
        .message.operator {
            align-self: flex-end;
            background: #34c759;
            color: white;
        }
        .message-time {
            font-size: 10px;
            margin-top: 4px;
            opacity: 0.7;
        }
        .message-input-container {
            background: white;
            border-top: 1px solid #e1e4e8;
            padding: 16px 24px;
            display: flex;
            gap: 12px;
        }
        .message-input {
            flex: 1;
            padding: 12px 16px;
            border: 2px solid #e1e4e8;
            border-radius: 20px;
            font-size: 14px;
            resize: none;
            font-family: inherit;
        }
        .send-button {
            background: #2b5278;
            color: white;
            border: none;
            border-radius: 50%;
            width: 44px;
            height: 44px;
            cursor: pointer;
            font-size: 20px;
        }
        .status-select {
            padding: 6px 12px;
            border-radius: 6px;
            border: 1px solid #ddd;
            background: white;
        }
        .empty-state {
            text-align: center;
            padding: 40px;
            color: #999;
        }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #ccc; border-radius: 3px; }
        .toggle-auto-btn {
            background: #2b5278;
            color: white;
            border: none;
            padding: 6px 12px;
            border-radius: 6px;
            cursor: pointer;
        }
        .toggle-auto-btn.off {
            background: #888;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 RobotChoiceBot CRM</h1>
        <div>Панель управления чатами</div>
    </div>
    
    <div class="main-content">
        <div class="chats-sidebar">
            <div class="chats-header">📋 Чаты</div>
            <div class="chats-list" id="chatsList">
                <div class="empty-state">Загрузка...</div>
            </div>
        </div>
        
        <div class="messages-area" id="messagesArea" style="display: none;">
            <div class="messages-header">
                <div><strong id="chatName"></strong></div>
                <div style="display: flex; gap: 12px; align-items: center;">
                    <select id="statusSelect" class="status-select">
                        <option value="первое сообщение">первое сообщение</option>
                        <option value="ожидание менеджера">ожидание менеджера</option>
                        <option value="в работе">в работе</option>
                        <option value="закрыт">закрыт</option>
                    </select>
                    <button id="toggleAutoBtn" class="toggle-auto-btn">Авторежим ВКЛ</button>
                    <a href="/api/export/csv" class="status-select" style="text-decoration: none;">📥 CSV</a>
                    <a href="/api/export/pdf" class="status-select" style="text-decoration: none;">📄 PDF</a>
                </div>
            </div>
            <div class="messages-container" id="messagesContainer"></div>
            <div class="message-input-container">
                <textarea class="message-input" id="messageInput" placeholder="Написать сообщение..." rows="1"></textarea>
                <button class="send-button" id="sendBtn">➤</button>
            </div>
        </div>
    </div>

    <script>
        let currentChatId = null;
        let currentAutoMode = true;
        
        async function loadChats() {
            try {
                const res = await fetch('/api/chats');
                const data = await res.json();
                const container = document.getElementById('chatsList');
                
                if (!data.chats.length) {
                    container.innerHTML = '<div class="empty-state">Нет чатов</div>';
                    return;
                }
                
                container.innerHTML = data.chats.map(chat => `
                    <div class="chat-item" onclick="selectChat(${chat.id})" data-chat-id="${chat.id}">
                        <div class="chat-name">${chat.full_name || chat.username || 'User'}</div>
                        <div class="chat-status">${chat.dialog_status || 'новый'}</div>
                        <div class="chat-status">ID: ${chat.user_id}</div>
                    </div>
                `).join('');
            } catch(e) {
                console.error(e);
            }
        }
        
        async function selectChat(chatId) {
            currentChatId = chatId;
            
            // Подсветка
            document.querySelectorAll('.chat-item').forEach(el => el.classList.remove('active'));
            document.querySelector(`.chat-item[data-chat-id="${chatId}"]`).classList.add('active');
            
            // Загружаем информацию о чате
            const chatRes = await fetch(`/api/chats/${chatId}`);
            const chat = await chatRes.json();
            document.getElementById('chatName').innerText = chat.full_name || chat.username || 'User';
            document.getElementById('statusSelect').value = chat.dialog_status || 'первое сообщение';
            
            currentAutoMode = chat.auto_mode;
            const autoBtn = document.getElementById('toggleAutoBtn');
            autoBtn.textContent = currentAutoMode ? 'Авторежим ВКЛ' : 'Авторежим ВЫКЛ';
            autoBtn.className = currentAutoMode ? 'toggle-auto-btn' : 'toggle-auto-btn off';
            
            document.getElementById('messagesArea').style.display = 'flex';
            
            // Загружаем сообщения
            await loadMessages(chatId);
            
            // Отмечаем чат как прочитанный
            await fetch(`/api/chats/${chatId}/mark-read`, { method: 'POST' });
        }
        
        async function loadMessages(chatId) {
            const res = await fetch(`/api/chats/${chatId}/messages`);
            const data = await res.json();
            const container = document.getElementById('messagesContainer');
            
            if (!data.messages.length) {
                container.innerHTML = '<div class="empty-state">Нет сообщений</div>';
                return;
            }
            
            container.innerHTML = data.messages.map(msg => `
                <div class="message ${msg.sender_type}">
                    ${escapeHtml(msg.message_text || '[Сообщение]')}
                    <div class="message-time">${new Date(msg.timestamp).toLocaleTimeString()}</div>
                </div>
            `).join('');
            container.scrollTop = container.scrollHeight;
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        async function sendMessage() {
            const input = document.getElementById('messageInput');
            const text = input.value.trim();
            if (!text || !currentChatId) return;
            
            const res = await fetch(`/api/chats/${currentChatId}/send`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text })
            });
            
            if (res.ok) {
                input.value = '';
                await loadMessages(currentChatId);
            }
        }
        
        async function updateStatus() {
            const status = document.getElementById('statusSelect').value;
            if (!currentChatId) return;
            
            await fetch(`/api/chats/${currentChatId}/status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status })
            });
        }
        
        async function toggleAutoMode() {
            if (!currentChatId) return;
            
            const res = await fetch(`/api/chats/${currentChatId}/toggle-auto`, {
                method: 'POST'
            });
            
            if (res.ok) {
                currentAutoMode = !currentAutoMode;
                const autoBtn = document.getElementById('toggleAutoBtn');
                autoBtn.textContent = currentAutoMode ? 'Авторежим ВКЛ' : 'Авторежим ВЫКЛ';
                autoBtn.className = currentAutoMode ? 'toggle-auto-btn' : 'toggle-auto-btn off';
            }
        }
        
        document.getElementById('statusSelect')?.addEventListener('change', updateStatus);
        document.getElementById('toggleAutoBtn')?.addEventListener('click', toggleAutoMode);
        document.getElementById('sendBtn')?.addEventListener('click', sendMessage);
        document.getElementById('messageInput')?.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        
        // Автообновление чатов и сообщений
        loadChats();
        setInterval(() => {
            loadChats();
            if (currentChatId) loadMessages(currentChatId);
        }, 5000);
    </script>
</body>
</html>
    ''')

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
