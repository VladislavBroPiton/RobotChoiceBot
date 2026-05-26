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

# ==================== ЭКСПОРТ CSV ====================
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

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
