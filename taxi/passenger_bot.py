import logging
from datetime import datetime
from typing import Dict
import json
import os
import math
import random
from warnings import filterwarnings

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from telegram.warnings import PTBUserWarning

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

# ============ КОНФИГУРАЦИЯ ============
PASSENGER_BOT_TOKEN = "8428023628:AAFTCo7iN9c8dH_xZdEp3pgnxvHoHBIYdtA"
ADMIN_IDS = [123456789]

# Состояния
(
    REG_NAME, REG_PHONE, MAIN_MENU,
    ENTER_PICKUP, ENTER_DESTINATION, SELECT_PAYMENT,
    ENTER_COMMENT, RATE_RIDE
) = range(8)

# ============ НАСТРОЙКА ЛОГИРОВАНИЯ ============
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ============ ХРАНИЛИЩЕ ДАННЫХ ============
passengers_db: Dict[int, dict] = {}
passenger_messages: Dict[int, list] = {}

ORDERS_FILE = 'orders.json'
ORDER_COUNTER_FILE = 'order_counter.json'

def get_next_order_id() -> str:
    try:
        if os.path.exists(ORDER_COUNTER_FILE):
            with open(ORDER_COUNTER_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                counter = data.get('counter', 0)
        else:
            counter = 0
        
        counter += 1
        with open(ORDER_COUNTER_FILE, 'w', encoding='utf-8') as f:
            json.dump({'counter': counter}, f)
        
        return f"#{counter:04d}"
    except:
        return f"#{1:04d}"

def load_data():
    global passengers_db
    try:
        if os.path.exists('passengers.json'):
            with open('passengers.json', 'r', encoding='utf-8') as f:
                passengers_db = {int(k): v for k, v in json.load(f).items()}
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")

def save_data():
    try:
        with open('passengers.json', 'w', encoding='utf-8') as f:
            json.dump(passengers_db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

async def delete_old_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int, keep_last: int = 3):
    if user_id in passenger_messages and len(passenger_messages[user_id]) > keep_last:
        to_delete = passenger_messages[user_id][:-keep_last]
        for msg_id in to_delete:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
            except:
                pass
        passenger_messages[user_id] = passenger_messages[user_id][-keep_last:]

def add_message_id(user_id: int, message_id: int):
    if user_id not in passenger_messages:
        passenger_messages[user_id] = []
    passenger_messages[user_id].append(message_id)

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return round(R * c, 1)

def calculate_price(distance_km: float) -> int:
    return int(100 + distance_km * 25)

def save_order_for_drivers(order_data: dict):
    try:
        orders = {}
        if os.path.exists(ORDERS_FILE):
            with open(ORDERS_FILE, 'r', encoding='utf-8') as f:
                orders = json.load(f)
        orders[str(order_data['id'])] = order_data
        with open(ORDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
        logger.info(f"Заказ {order_data['id']} сохранен")
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

def generate_share_text(order_data: dict) -> str:
    status_emoji = {
        'searching': '🔍',
        'accepted': '✅',
        'arrived': '🚗',
        'in_progress': '🟢',
        'completed': '🏁'
    }
    emoji = status_emoji.get(order_data.get('status', 'in_progress'), '🟢')
    
    return (
        f"{emoji} *Ваше Taxi — Поездка*\n\n"
        f"📍 *Откуда:* {order_data.get('pickup', '—')}\n"
        f"🎯 *Куда:* {order_data.get('destination', '—')}\n"
        f"🚗 *Водитель:* {order_data.get('driver_name', '—')}\n"
        f"🔢 *Авто:* {order_data.get('driver_car', '—')} {order_data.get('driver_number', '')}\n"
        f"💰 *Стоимость:* {order_data.get('price', 0)} ₽"
    )

def generate_map_url(pickup_coords: tuple, dest_coords: tuple) -> str:
    return f"https://yandex.ru/maps/?rtext={pickup_coords[0]},{pickup_coords[1]}~{dest_coords[0]},{dest_coords[1]}&rtt=auto"

async def send_receipt(user_id: int, order_data: dict, context: ContextTypes.DEFAULT_TYPE):
    receipt = (
        f"🧾 *ЧЕК О ПОЕЗДКЕ*\n\n"
        f"📋 Заказ № {order_data.get('id', 'N/A')}\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y')} в {datetime.now().strftime('%H:%M')}\n\n"
        f"📍 *Маршрут*\n"
        f"🚩 {order_data.get('pickup', '')}\n"
        f"🏁 {order_data.get('destination', '')}\n\n"
        f"👤 *Водитель*\n"
        f"{order_data.get('driver_name', 'Не указан')}\n"
        f"🚗 {order_data.get('driver_car', '')} ({order_data.get('driver_color', '')})\n"
        f"🔢 {order_data.get('driver_number', '')}\n\n"
        f"💰 *Оплата*\n"
        f"Сумма: *{order_data.get('price', 0)} ₽*\n"
        f"Способ: 💵 Наличными\n\n"
        f"✨ *Спасибо за поездку!*"
    )
    
    msg = await context.bot.send_message(chat_id=user_id, text=receipt, parse_mode='Markdown')
    add_message_id(user_id, msg.message_id)
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=receipt, parse_mode='Markdown')
        except:
            pass

def clear_order_data(context: ContextTypes.DEFAULT_TYPE):
    keys = ['pickup', 'destination', 'price', 'distance', 'comment', 
            'pickup_coords', 'dest_coords', 'map_url']
    for key in keys:
        if key in context.user_data:
            del context.user_data[key]

# ============ КЛАВИАТУРЫ ============
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["🚖 Заказать такси", "📊 Мои поездки"],
        ["👤 Профиль", "ℹ️ О боте"],
        ["🆘 Помощь"]
    ], resize_keyboard=True)

def get_phone_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Отправить номер", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )

def get_payment_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Наличные", callback_data="pay_cash")],
        [InlineKeyboardButton("✅ Подтвердить заказ", callback_data="confirm_order")]
    ])

def get_rating_keyboard(order_id: str, driver_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐", callback_data=f"rate_{order_id}_{driver_id}_1"),
         InlineKeyboardButton("⭐⭐", callback_data=f"rate_{order_id}_{driver_id}_2"),
         InlineKeyboardButton("⭐⭐⭐", callback_data=f"rate_{order_id}_{driver_id}_3"),
         InlineKeyboardButton("⭐⭐⭐⭐", callback_data=f"rate_{order_id}_{driver_id}_4"),
         InlineKeyboardButton("⭐⭐⭐⭐⭐", callback_data=f"rate_{order_id}_{driver_id}_5")]
    ])

def get_share_keyboard(order_id: str, order_data: dict):
    """Клавиатура для шеринга поездки — только для статуса in_progress"""
    share_text = generate_share_text(order_data)
    map_url = order_data.get('map_url', generate_map_url(
        order_data.get('pickup_coords', (55.7558, 37.6176)),
        order_data.get('dest_coords', (55.765, 37.605))
    ))
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Поделиться поездкой", switch_inline_query=share_text)],
        [InlineKeyboardButton("🗺️ Открыть в Яндекс.Картах", url=map_url)]
    ])

def get_about_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👨‍💻 Написать разработчику", url="https://t.me/dnrdev")]
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Вернуться в меню", callback_data="back_to_main")]
    ])

# ============ ОБРАБОТЧИКИ ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "Гость"
    
    welcome = (
        f"✨ *Ваше Taxi* ✨\n\n"
        f"• ⚡ Быстрая подача\n"
        f"• 💰 Честные цены\n"
        f"• ⭐ Проверенные водители\n"
        f"• 💵 Оплата наличными"
    )
    
    if user_id in passengers_db:
        msg = await update.message.reply_text(
            f"👋 *С возвращением, {passengers_db[user_id]['name']}!*\n\n" + welcome,
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        add_message_id(user_id, msg.message_id)
        return MAIN_MENU
    else:
        msg = await update.message.reply_text(
            f"✨ *Ваше Taxi* ✨\n\n"
            f"👋 *{user_name}*, рады видеть вас!\n\n"
            f"• ⚡ Быстрая подача\n"
            f"• 💰 Честные цены\n"
            f"• ⭐ Проверенные водители\n"
            f"• 💵 Оплата наличными\n\n"
            f"📝 *Введите ваше имя:*",
            parse_mode='Markdown'
        )
        add_message_id(user_id, msg.message_id)
        return REG_NAME

async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data['name'] = update.message.text
    add_message_id(user_id, update.message.message_id)
    
    msg = await update.message.reply_text(
        "📱 *Отправьте номер телефона:*\n\n"
        "_Нажмите кнопку или введите вручную_",
        reply_markup=get_phone_keyboard(),
        parse_mode='Markdown'
    )
    add_message_id(user_id, msg.message_id)
    return REG_PHONE

async def reg_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text
    add_message_id(user_id, update.message.message_id)
    
    passengers_db[user_id] = {
        'name': context.user_data['name'],
        'phone': phone,
        'rides_count': 0,
        'total_spent': 0,
        'registered_at': datetime.now().isoformat()
    }
    save_data()
    
    msg = await update.message.reply_text(
        f"🎉 *Регистрация завершена!*\n\n"
        f"👤 {context.user_data['name']}\n"
        f"📱 {phone}\n\n"
        f"✅ Теперь вы можете заказать такси!",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )
    add_message_id(user_id, msg.message_id)
    await delete_old_messages(context, user_id, keep_last=5)
    return MAIN_MENU

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    add_message_id(user_id, update.message.message_id)
    
    if user_id not in passengers_db:
        msg = await update.message.reply_text("Пожалуйста, зарегистрируйтесь /start")
        add_message_id(user_id, msg.message_id)
        return MAIN_MENU
    
    if text == "🚖 Заказать такси":
        clear_order_data(context)
        msg = await update.message.reply_text(
            "📍 *ОТКУДА ПОЕДЕМ?*\n\n"
            "Введите адрес подачи:\n"
            "_Например: ул. Тверская, 10_",
            parse_mode='Markdown'
        )
        add_message_id(user_id, msg.message_id)
        return ENTER_PICKUP
    
    elif text == "👤 Профиль":
        p = passengers_db[user_id]
        profile = (
            f"👤 *ПРОФИЛЬ*\n\n"
            f"*Имя:* {p['name']}\n"
            f"*Телефон:* {p['phone']}\n\n"
            f"———\n\n"
            f"🚖 *Статистика*\n"
            f"Поездок: *{p.get('rides_count', 0)}*\n"
            f"Потрачено: *{p.get('total_spent', 0)} ₽*\n\n"
            f"📅 С нами с {p.get('registered_at', '')[:10]}"
        )
        msg = await update.message.reply_text(profile, parse_mode='Markdown')
        add_message_id(user_id, msg.message_id)
    
    elif text == "ℹ️ О боте":
        about_text = (
            f"🚕 *Ваше Taxi*\n\n"
            f"📦 Версия 1.0.0\n"
            f"🟢 Статус: Работает\n\n"
            f"———\n\n"
            f"✨ *Возможности:*\n"
            f"• 🚖 Быстрый заказ\n"
            f"• 📍 Отслеживание\n"
            f"• 💵 Оплата наличными\n"
            f"• ⭐ Оценка водителей\n\n"
            f"———\n\n"
            f"👨‍💻 *Разработчик:* @dnrdev\n"
            f"© 2026 Ваше Taxi"
        )
        msg = await update.message.reply_text(
            about_text,
            reply_markup=get_about_keyboard(),
            parse_mode='Markdown'
        )
        add_message_id(user_id, msg.message_id)
    
    elif text == "📊 Мои поездки":
        rides = passengers_db[user_id].get('rides_count', 0)
        spent = passengers_db[user_id].get('total_spent', 0)
        
        if rides == 0:
            stats = (
                f"📊 *СТАТИСТИКА*\n\n"
                f"😢 Пока нет завершённых поездок.\n\n"
                f"🚖 *Закажите первую поездку!*"
            )
        else:
            avg_price = spent / rides if rides > 0 else 0
            stats = (
                f"📊 *СТАТИСТИКА*\n\n"
                f"🚖 Всего поездок: *{rides}*\n"
                f"💰 Потрачено: *{spent} ₽*\n"
                f"📊 Средний чек: *{avg_price:.0f} ₽*\n\n"
                f"✨ Спасибо, что вы с нами!"
            )
        msg = await update.message.reply_text(stats, parse_mode='Markdown')
        add_message_id(user_id, msg.message_id)
    
    elif text == "🆘 Помощь":
        help_text = (
            f"🆘 *ПОМОЩЬ*\n\n"
            f"📞 *Поддержка:*\n"
            f"+7 (XXX) XXX-XX-XX\n\n"
            f"👨‍💻 *Разработчик:*\n"
            f"@dnrdev\n\n"
            f"———\n\n"
            f"❓ *Частые вопросы:*\n"
            f"• Как заказать такси?\n"
            f"• Как отменить заказ?\n"
            f"• Как связаться с водителем?\n\n"
            f"💚 *Мы всегда на связи!*"
        )
        msg = await update.message.reply_text(
            help_text,
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )
        add_message_id(user_id, msg.message_id)
    
    await delete_old_messages(context, user_id, keep_last=5)
    return MAIN_MENU

async def enter_pickup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data['pickup'] = update.message.text
    context.user_data['pickup_coords'] = (55.7558, 37.6176)
    add_message_id(user_id, update.message.message_id)
    
    msg = await update.message.reply_text(
        "🎯 *КУДА ЕДЕМ?*\n\n"
        "Введите адрес назначения:\n"
        "_Например: Киевский вокзал_",
        parse_mode='Markdown'
    )
    add_message_id(user_id, msg.message_id)
    return ENTER_DESTINATION

async def enter_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data['destination'] = update.message.text
    context.user_data['dest_coords'] = (55.765, 37.605)
    add_message_id(user_id, update.message.message_id)
    
    pickup = context.user_data.get('pickup_coords', (55.7558, 37.6176))
    dest = context.user_data.get('dest_coords', (55.765, 37.605))
    distance = calculate_distance(pickup[0], pickup[1], dest[0], dest[1])
    price = calculate_price(distance)
    
    context.user_data['distance'] = distance
    context.user_data['price'] = price
    context.user_data['map_url'] = generate_map_url(pickup, dest)
    
    msg = await update.message.reply_text(
        f"📏 *РАСЧЁТ СТОИМОСТИ*\n\n"
        f"🛣️ Расстояние: ~{distance} км\n"
        f"💰 Стоимость: *{price} ₽*\n\n"
        f"———\n\n"
        f"💬 *Комментарий для водителя:*\n"
        f"(нажмите /skip чтобы пропустить)\n\n"
        f"_Пример: Позвоните за 5 минут_",
        parse_mode='Markdown'
    )
    add_message_id(user_id, msg.message_id)
    return ENTER_COMMENT

async def enter_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if update.message.text == '/skip':
        context.user_data['comment'] = ''
    else:
        context.user_data['comment'] = update.message.text
        add_message_id(user_id, update.message.message_id)
    
    price = context.user_data.get('price', 0)
    
    msg = await update.message.reply_text(
        f"✅ *ПОДТВЕРЖДЕНИЕ*\n\n"
        f"💰 Сумма: *{price} ₽*\n"
        f"💵 Оплата: Наличными\n\n"
        f"Нажмите *«Подтвердить»* для заказа:",
        reply_markup=get_payment_keyboard(),
        parse_mode='Markdown'
    )
    add_message_id(user_id, msg.message_id)
    return SELECT_PAYMENT

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    
    if data == "pay_cash":
        context.user_data['payment'] = 'cash'
        context.user_data['payment_method'] = 'Наличные'
        await query.answer("✅ Оплата наличными")
    
    elif data == "back_to_main":
        await query.edit_message_text("🏠 *Главное меню*", parse_mode='Markdown')
        msg = await context.bot.send_message(
            chat_id=user_id,
            text="Выберите действие:",
            reply_markup=get_main_keyboard()
        )
        add_message_id(user_id, msg.message_id)
        return MAIN_MENU
    
    elif data == "confirm_order":
        if 'price' not in context.user_data:
            await query.answer("❌ Сначала рассчитайте стоимость!", show_alert=True)
            return MAIN_MENU
        
        context.user_data['payment'] = 'cash'
        context.user_data['payment_method'] = 'Наличные'
        
        order_id = get_next_order_id()
        map_url = context.user_data.get('map_url', '')
        
        order_data = {
            'id': order_id,
            'passenger_id': user_id,
            'passenger_name': passengers_db[user_id]['name'],
            'passenger_phone': passengers_db[user_id]['phone'],
            'pickup': context.user_data.get('pickup', 'Не указан'),
            'destination': context.user_data.get('destination', 'Не указан'),
            'pickup_coords': context.user_data.get('pickup_coords', (55.7558, 37.6176)),
            'dest_coords': context.user_data.get('dest_coords', (55.765, 37.605)),
            'price': context.user_data.get('price', 0),
            'distance': context.user_data.get('distance', 0),
            'payment': 'cash',
            'payment_method': 'Наличные',
            'comment': context.user_data.get('comment', ''),
            'status': 'searching',
            'sent_to': [],
            'map_url': map_url,
            'created_at': datetime.now().isoformat()
        }
        
        save_order_for_drivers(order_data)
        context.user_data['current_order_id'] = order_id
        context.user_data['current_order_data'] = order_data
        
        await query.edit_message_text(
            f"🚖 *ЗАКАЗ {order_id}*\n\n"
            f"📍 Откуда: {order_data['pickup']}\n"
            f"🎯 Куда: {order_data['destination']}\n"
            f"💰 Стоимость: {order_data['price']} ₽\n"
            f"💵 Оплата: Наличными\n\n"
            f"🔍 *Ищем водителя...*\n"
            f"⏳ Ожидайте",
            parse_mode='Markdown'
        )
        
        context.job_queue.run_repeating(
            check_order_status,
            interval=3,
            first=2,
            chat_id=user_id,
            data={'order_id': order_id, 'order_data': order_data}
        )
        
        clear_order_data(context)
        return MAIN_MENU
    
    elif data.startswith('rate_'):
        parts = data.split('_')
        order_id = parts[1]
        driver_id = int(parts[2])
        rating = int(parts[3])
        
        try:
            if os.path.exists('drivers.json'):
                with open('drivers.json', 'r', encoding='utf-8') as f:
                    drivers = json.load(f)
                
                if str(driver_id) in drivers:
                    d = drivers[str(driver_id)]
                    curr = d.get('rating', 5.0)
                    total = d.get('total_ratings', 1)
                    d['rating'] = round((curr * total + rating) / (total + 1), 1)
                    d['total_ratings'] = total + 1
                    
                    with open('drivers.json', 'w', encoding='utf-8') as f:
                        json.dump(drivers, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка обновления рейтинга: {e}")
        
        await query.edit_message_text(
            f"⭐ *СПАСИБО ЗА ОЦЕНКУ!*\n\n"
            f"Ваша оценка: {'⭐' * rating}\n\n"
            f"*И помните:* хороший водитель — это тот,\n"
            f"кто не включил «Газманова» в 6 утра 😄\n\n"
            f"🚕 До новых поездок!",
            parse_mode='Markdown'
        )
    
    return MAIN_MENU

async def check_order_status(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.chat_id
    order_id = job.data['order_id']
    order_data = job.data.get('order_data', {})
    
    try:
        if not os.path.exists(ORDERS_FILE):
            return
        
        with open(ORDERS_FILE, 'r', encoding='utf-8') as f:
            orders = json.load(f)
        
        order = orders.get(order_id)
        if not order:
            return
        
        status = order.get('status')
        
        if status == 'accepted' and not job.data.get('accepted_notified'):
            eta = random.randint(5, 12)
            
            msg = await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ *ВОДИТЕЛЬ НАЗНАЧЕН!*\n\n"
                f"🚗 {order.get('driver_car', '')}\n"
                f"🎨 {order.get('driver_color', '')}\n"
                f"🔢 {order.get('driver_number', '')}\n"
                f"👤 {order.get('driver_name', '')}\n"
                f"⭐ Рейтинг: {order.get('driver_rating', 5.0)}\n\n"
                f"⏱️ Прибудет через: ~{eta} минут\n"
                f"📞 {order.get('driver_phone', '')}\n\n"
                f"🚖 *Водитель уже выехал!*",
                parse_mode='Markdown'
            )
            add_message_id(user_id, msg.message_id)
            job.data['accepted_notified'] = True
            job.data['driver_id'] = order.get('driver_id', 0)
        
        elif status == 'arrived' and not job.data.get('arrived_notified'):
            msg = await context.bot.send_message(
                chat_id=user_id,
                text=f"🚗 *ВОДИТЕЛЬ ПРИБЫЛ!*\n\n"
                f"{order.get('driver_car', '')} • {order.get('driver_color', '')}\n"
                f"🔢 {order.get('driver_number', '')}\n\n"
                f"📍 Ожидает вас на месте подачи\n"
                f"📞 {order.get('driver_phone', '')}\n\n"
                f"🏃 *Пожалуйста, выходите!*",
                parse_mode='Markdown'
            )
            add_message_id(user_id, msg.message_id)
            job.data['arrived_notified'] = True
        
        elif status == 'in_progress' and not job.data.get('started_notified'):
            # ТОЛЬКО ЗДЕСЬ ДОБАВЛЯЕМ КНОПКИ ШЕРИНГА И КАРТЫ
            msg = await context.bot.send_message(
                chat_id=user_id,
                text=f"🟢 *ПОЕЗДКА НАЧАЛАСЬ!*\n\n"
                f"🚗 {order.get('driver_car', '')} {order.get('driver_number', '')}\n"
                f"👤 {order.get('driver_name', '')}\n\n"
                f"🎵 *Приятной поездки!* 🚖\n"
                f"⏱️ В пути: ~{order.get('distance', 0) * 2} мин",
                reply_markup=get_share_keyboard(order_id, order),
                parse_mode='Markdown'
            )
            add_message_id(user_id, msg.message_id)
            job.data['started_notified'] = True
        
        elif status == 'completed' and not job.data.get('completed_notified'):
            job.schedule_removal()
            
            if user_id in passengers_db:
                passengers_db[user_id]['rides_count'] = passengers_db[user_id].get('rides_count', 0) + 1
                passengers_db[user_id]['total_spent'] = passengers_db[user_id].get('total_spent', 0) + order.get('price', 0)
                save_data()
            
            driver_id = job.data.get('driver_id', 0)
            
            msg = await context.bot.send_message(
                chat_id=user_id,
                text=f"🏁 *ВЫ ПРИБЫЛИ!*\n\n"
                f"📍 {order.get('destination', '')}\n\n"
                f"💰 Стоимость: {order.get('price', 0)} ₽\n"
                f"💵 Оплата: Наличными\n\n"
                f"⭐ *Оцените поездку:*",
                reply_markup=get_rating_keyboard(order_id, driver_id),
                parse_mode='Markdown'
            )
            add_message_id(user_id, msg.message_id)
            
            await send_receipt(user_id, order, context)
            
            job.data['completed_notified'] = True
        
        await delete_old_messages(context, user_id, keep_last=5)
            
    except Exception as e:
        logger.error(f"Ошибка проверки статуса: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_order_data(context)
    msg = await update.message.reply_text("❌ Действие отменено.", reply_markup=get_main_keyboard())
    add_message_id(user_id, msg.message_id)
    return MAIN_MENU

# ============ ЗАПУСК ============
def main():
    load_data()
    
    application = Application.builder().token(PASSENGER_BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
            REG_PHONE: [
                MessageHandler(filters.CONTACT, reg_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_phone)
            ],
            MAIN_MENU: [
                MessageHandler(filters.Regex(r'^(🚖 Заказать такси|📊 Мои поездки|👤 Профиль|ℹ️ О боте|🆘 Помощь)$'), main_menu),
                CallbackQueryHandler(handle_callback),
            ],
            ENTER_PICKUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_pickup)],
            ENTER_DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_destination)],
            ENTER_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_comment),
                CommandHandler('skip', enter_comment)
            ],
            SELECT_PAYMENT: [CallbackQueryHandler(handle_callback)],
            RATE_RIDE: [CallbackQueryHandler(handle_callback)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False
    )
    
    application.add_handler(conv_handler)
    
    logger.info("🚀 Бот пассажиров запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()