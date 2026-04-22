import logging
from datetime import datetime
from typing import Dict
import json
import os
import asyncio
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
TOKEN = "8664081816:AAG9fE5nRPK3pb3S6YUxRffxysghIyWg2Nk"
ADMIN_IDS = [667474295]  # ID администраторов/диспетчеров

# Состояния
(
    REG_FULL_NAME, REG_PHONE, REG_CAR_BRAND, REG_CAR_COLOR,
    REG_CAR_NUMBER, MAIN_MENU, WAITING_PASSENGER_CHAT,
    WAITING_DISPATCHER_CHAT
) = range(8)

# ============ НАСТРОЙКА ЛОГИРОВАНИЯ ============
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# ============ ХРАНИЛИЩЕ ДАННЫХ ============
drivers_db: Dict[int, dict] = {}
pending_orders: Dict[str, dict] = {}
active_orders: Dict[int, str] = {}
driver_messages: Dict[int, list] = {}

DRIVERS_FILE = 'drivers.json'
ORDERS_FILE = 'orders.json'

def load_data():
    global drivers_db, pending_orders
    try:
        if os.path.exists(DRIVERS_FILE):
            with open(DRIVERS_FILE, 'r', encoding='utf-8') as f:
                drivers_db = {int(k): v for k, v in json.load(f).items()}
        if os.path.exists(ORDERS_FILE):
            with open(ORDERS_FILE, 'r', encoding='utf-8') as f:
                pending_orders = json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")

def save_data():
    try:
        with open(DRIVERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(drivers_db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

def save_orders():
    try:
        with open(ORDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(pending_orders, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения заказов: {e}")

async def delete_old_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int, keep_last: int = 1):
    if user_id in driver_messages and len(driver_messages[user_id]) > keep_last:
        to_delete = driver_messages[user_id][:-keep_last]
        for msg_id in to_delete:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
            except:
                pass
        driver_messages[user_id] = driver_messages[user_id][-keep_last:]

def add_message_id(user_id: int, message_id: int):
    if user_id not in driver_messages:
        driver_messages[user_id] = []
    driver_messages[user_id].append(message_id)

# ============ КЛАВИАТУРЫ ============
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["🚗 Начать смену", "🍔 На обед"],
        ["⏹️ Завершить смену", "📊 Мой профиль"],
        ["📞 Диспетчер", "🆘 SOS"]
    ], resize_keyboard=True)

def get_order_keyboard(order_id: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"accept_{order_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"decline_{order_id}")
        ],
        [
            InlineKeyboardButton("📞 Позвонить", callback_data=f"call_{order_id}"),
            InlineKeyboardButton("💬 Чат", callback_data=f"chat_{order_id}")
        ],
        [InlineKeyboardButton("🗺️ Навигация", callback_data=f"nav_{order_id}")]
    ])

def get_active_order_keyboard(order_id: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📞 Позвонить", callback_data=f"call_{order_id}"),
            InlineKeyboardButton("💬 Чат", callback_data=f"chat_{order_id}")
        ],
        [
            InlineKeyboardButton("🗺️ Навигация", callback_data=f"nav_{order_id}"),
            InlineKeyboardButton("🚗 На месте", callback_data=f"arrived_{order_id}")
        ],
        [
            InlineKeyboardButton("🟢 Начать поездку", callback_data=f"start_{order_id}"),
            InlineKeyboardButton("✅ Завершить", callback_data=f"complete_{order_id}")
        ],
        [InlineKeyboardButton("❌ Клиент не вышел", callback_data=f"nowshow_{order_id}")]
    ])

def get_contact_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Отправить номер телефона", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# ============ ОТПРАВКА ЗАКАЗА ============
async def send_order_to_driver(driver_id: int, order_data: dict, context: ContextTypes.DEFAULT_TYPE):
    payment_icon = "💵 Наличные" if order_data.get('payment') == 'cash' else "💳 Карта"
    
    message_text = (
        f"🚖 *НОВЫЙ ЗАКАЗ {order_data['id']}*\n\n"
        f"📍 *Подача:* {order_data['pickup']}\n"
        f"🎯 *Назначение:* {order_data['destination']}\n"
        f"💰 *Стоимость:* {order_data['price']} ₽\n"
        f"💳 *Оплата:* {payment_icon}\n"
        f"👤 *Пассажир:* {order_data.get('passenger_name', 'Не указан')}\n"
        f"💬 *Комментарий:* {order_data.get('comment', 'Нет')}"
    )
    
    msg = await context.bot.send_message(
        chat_id=driver_id,
        text=message_text,
        reply_markup=get_order_keyboard(order_data['id']),
        parse_mode='Markdown'
    )
    add_message_id(driver_id, msg.message_id)

# ============ ПРОВЕРКА ЗАКАЗОВ ============
async def check_new_orders(context: ContextTypes.DEFAULT_TYPE):
    try:
        if os.path.exists(ORDERS_FILE):
            with open(ORDERS_FILE, 'r', encoding='utf-8') as f:
                global pending_orders
                pending_orders = json.load(f)
    except Exception as e:
        logger.error(f"Ошибка чтения заказов: {e}")
        return
    
    for order_id, order_data in list(pending_orders.items()):
        if order_data.get('status') != 'searching':
            continue
        
        sent_to = order_data.get('sent_to', [])
        
        for driver_id, driver in drivers_db.items():
            if (driver.get('online') and 
                not driver.get('on_break') and 
                not driver.get('emergency') and
                driver_id not in active_orders and
                driver_id not in sent_to):
                
                await send_order_to_driver(driver_id, order_data, context)
                sent_to.append(driver_id)
                order_data['sent_to'] = sent_to
                save_orders()
                await asyncio.sleep(0.3)

# ============ ОБРАБОТЧИКИ ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "Водитель"
    
    if user_id in drivers_db:
        driver = drivers_db[user_id]
        rating = driver.get('rating', 5.0)
        stars = '⭐' * int(rating) if rating > 0 else ''
        
        emergency_status = "⚠️ *АКТИВЕН РЕЖИМ ЧП*" if driver.get('emergency') else ""
        
        msg = await update.message.reply_text(
            f"👋 *С возвращением, {driver['full_name']}!*\n\n"
            f"🚗 *Автомобиль:* {driver['car_brand']} {driver['car_color']}\n"
            f"🔢 *Номер:* {driver['car_number']}\n"
            f"⭐ *Рейтинг:* {rating} {stars}\n"
            f"📊 *Статус:* {'🟢 На линии' if driver.get('online') else '🔴 Не на линии'}\n"
            f"{emergency_status}\n\n"
            f"👇 *Выберите действие:*",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        add_message_id(user_id, msg.message_id)
        return MAIN_MENU
    else:
        msg = await update.message.reply_text(
            f"✨ *ДОБРО ПОЖАЛОВАТЬ В ВАШЕ TAXI!* ✨\n\n"
            f"🚕 *{user_name}, рады видеть вас в нашей команде!*\n\n"
            f"Давайте создадим ваш профиль водителя — это займёт меньше минуты.\n\n"
            f"✏️ *Введите ваше ФИО:*\n"
            f"_Например: Иванов Иван Иванович_",
            parse_mode='Markdown'
        )
        add_message_id(user_id, msg.message_id)
        return REG_FULL_NAME

async def reg_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data['full_name'] = update.message.text
    add_message_id(user_id, update.message.message_id)
    
    msg = await update.message.reply_text(
        f"📱 *Отлично! Теперь укажите номер телефона:*\n\n"
        f"• Нажмите кнопку ниже чтобы отправить контакт Telegram\n"
        f"• Или введите номер вручную\n\n"
        f"_Пример: +7 999 123-45-67_",
        reply_markup=get_contact_keyboard(),
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
    
    context.user_data['phone'] = phone
    add_message_id(user_id, update.message.message_id)
    
    msg = await update.message.reply_text(
        f"🚗 *Введите марку автомобиля:*\n"
        f"_Например: Toyota Camry, Kia Rio, Hyundai Solaris_",
        parse_mode='Markdown'
    )
    add_message_id(user_id, msg.message_id)
    return REG_CAR_BRAND

async def reg_car_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data['car_brand'] = update.message.text
    add_message_id(user_id, update.message.message_id)
    
    msg = await update.message.reply_text(
        f"🎨 *Введите цвет автомобиля:*\n"
        f"_Например: Белый, Чёрный, Серебристый_",
        parse_mode='Markdown'
    )
    add_message_id(user_id, msg.message_id)
    return REG_CAR_COLOR

async def reg_car_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data['car_color'] = update.message.text
    add_message_id(user_id, update.message.message_id)
    
    msg = await update.message.reply_text(
        f"🔢 *Последний шаг! Введите гос. номер:*\n"
        f"_Формат: А123БВ177 или A123BC177_",
        parse_mode='Markdown'
    )
    add_message_id(user_id, msg.message_id)
    return REG_CAR_NUMBER

async def reg_car_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data['car_number'] = update.message.text.upper()
    add_message_id(user_id, update.message.message_id)
    
    drivers_db[user_id] = {
        'full_name': context.user_data['full_name'],
        'phone': context.user_data['phone'],
        'car_brand': context.user_data['car_brand'],
        'car_color': context.user_data['car_color'],
        'car_number': context.user_data['car_number'],
        'rating': 5.0,
        'total_ratings': 1,
        'online': False,
        'on_break': False,
        'emergency': False,
        'rides_today': 0,
        'total_rides': 0,
        'earnings_today': 0
    }
    save_data()
    
    msg = await update.message.reply_text(
        f"🎉 *РЕГИСТРАЦИЯ УСПЕШНО ЗАВЕРШЕНА!*\n\n"
        f"👤 *Водитель:* {context.user_data['full_name']}\n"
        f"📱 *Телефон:* {context.user_data['phone']}\n"
        f"🚗 *Автомобиль:* {context.user_data['car_brand']} {context.user_data['car_color']}\n"
        f"🔢 *Госномер:* {context.user_data['car_number']}\n\n"
        f"✅ Теперь вы можете начать смену и принимать заказы!\n\n"
        f"*Удачных поездок и хороших пассажиров!* 🍀",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )
    add_message_id(user_id, msg.message_id)
    await delete_old_messages(context, user_id, keep_last=3)
    return MAIN_MENU

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    add_message_id(user_id, update.message.message_id)
    
    if user_id not in drivers_db:
        msg = await update.message.reply_text("Пройдите регистрацию /start")
        add_message_id(user_id, msg.message_id)
        return MAIN_MENU
    
    driver = drivers_db[user_id]
    
    if text == "🚗 Начать смену":
        if driver.get('emergency'):
            await update.message.reply_text(
                "⚠️ *НЕВОЗМОЖНО НАЧАТЬ СМЕНУ*\n\n"
                "У вас активен режим ЧП.\n"
                "Свяжитесь с диспетчером для снятия.",
                parse_mode='Markdown'
            )
            return MAIN_MENU
        
        driver['online'] = True
        driver['on_break'] = False
        save_data()
        
        msg = await update.message.reply_text(
            "🟢 *СМЕНА НАЧАТА!*\n\n"
            "Ожидайте новые заказы.",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        add_message_id(user_id, msg.message_id)
    
    elif text == "🍔 На обед":
        driver['on_break'] = True
        save_data()
        msg = await update.message.reply_text("🍔 Вы на обеде. Заказы не поступают.", reply_markup=get_main_keyboard())
        add_message_id(user_id, msg.message_id)
    
    elif text == "⏹️ Завершить смену":
        driver['online'] = False
        driver['on_break'] = False
        save_data()
        msg = await update.message.reply_text(
            f"📊 *СМЕНА ЗАВЕРШЕНА*\n\n"
            f"Заказов: {driver.get('rides_today', 0)}\n"
            f"Заработано: {driver.get('earnings_today', 0)} ₽",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        add_message_id(user_id, msg.message_id)
        driver['rides_today'] = 0
        driver['earnings_today'] = 0
        save_data()
    
    elif text == "📊 Мой профиль":
        rating = driver.get('rating', 5.0)
        stars = '⭐' * int(rating) if rating > 0 else 'Нет оценок'
        msg = await update.message.reply_text(
            f"👤 *ПРОФИЛЬ*\n\n"
            f"ФИО: {driver['full_name']}\n"
            f"📱 {driver['phone']}\n"
            f"🚗 {driver['car_brand']} {driver['car_color']} ({driver['car_number']})\n"
            f"⭐ {rating} {stars}\n"
            f"📊 Всего поездок: {driver.get('total_rides', 0)}",
            parse_mode='Markdown'
        )
        add_message_id(user_id, msg.message_id)
    
    elif text == "📞 Диспетчер":
        await update.message.reply_text(
            "📞 *СВЯЗЬ С ДИСПЕТЧЕРОМ*\n\n"
            "Введите сообщение для диспетчера\n"
            "(или /cancel для отмены):",
            parse_mode='Markdown'
        )
        return WAITING_DISPATCHER_CHAT
    
    elif text == "🆘 SOS":
        driver['emergency'] = True
        driver['online'] = False
        driver['on_break'] = False
        save_data()
        
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🆘 *ТРЕВОГА! SOS ОТ ВОДИТЕЛЯ!*\n\n"
                         f"👤 *Водитель:* {driver.get('full_name', '—')}\n"
                         f"📱 *Телефон:* {driver.get('phone', '—')}\n"
                         f"🚗 *Авто:* {driver.get('car_brand', '—')} {driver.get('car_number', '—')}\n\n"
                         f"🕐 *Время:* {datetime.now().strftime('%H:%M:%S')}\n\n"
                         f"⚠️ *СРОЧНО СВЯЖИТЕСЬ С ВОДИТЕЛЕМ!*",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Ошибка отправки SOS админу {admin_id}: {e}")
        
        msg = await update.message.reply_text(
            "🆘 *СИГНАЛ SOS ОТПРАВЛЕН!*\n\n"
            "🚨 Режим ЧП активирован.\n"
            "📞 Диспетчер уже уведомлён и скоро свяжется с вами.\n\n"
            "🚑 *Экстренные службы: 112*\n\n"
            "Ожидайте помощи!",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        add_message_id(user_id, msg.message_id)
    
    await delete_old_messages(context, user_id, keep_last=5)
    return MAIN_MENU

async def handle_dispatcher_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message.text
    driver = drivers_db.get(user_id, {})
    
    sent = 0
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"📞 *СООБЩЕНИЕ ОТ ВОДИТЕЛЯ*\n\n"
                     f"👤 {driver.get('full_name', '—')}\n"
                     f"📱 {driver.get('phone', '—')}\n"
                     f"🚗 {driver.get('car_number', '—')}\n\n"
                     f"💬 *Сообщение:*\n{message}",
                parse_mode='Markdown'
            )
            sent += 1
        except Exception as e:
            logger.error(f"Ошибка отправки диспетчеру {admin_id}: {e}")
    
    msg = await update.message.reply_text(
        f"✅ *Сообщение отправлено диспетчеру*\n\n"
        f"📨 Доставлено: {sent} из {len(ADMIN_IDS)}\n\n"
        f"Ожидайте ответа.",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )
    add_message_id(user_id, msg.message_id)
    return MAIN_MENU

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    
    if user_id in drivers_db and drivers_db[user_id].get('emergency'):
        await query.answer("⚠️ Режим ЧП активен. Свяжитесь с диспетчером.", show_alert=True)
        return MAIN_MENU
    
    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, 'r', encoding='utf-8') as f:
            global pending_orders
            pending_orders = json.load(f)
    
    if data.startswith('accept_'):
        order_id = data.replace('accept_', '')
        
        if user_id in active_orders:
            await query.answer("❌ У вас уже есть активный заказ!", show_alert=True)
            return MAIN_MENU
        
        if order_id not in pending_orders:
            await query.edit_message_text("❌ Заказ неактуален.")
            return MAIN_MENU
        
        order_data = pending_orders[order_id]
        
        if order_data.get('status') != 'searching':
            await query.edit_message_text("❌ Заказ уже взят.")
            return MAIN_MENU
        
        active_orders[user_id] = order_id
        order_data['status'] = 'accepted'
        order_data['driver_id'] = user_id
        order_data['driver_name'] = drivers_db[user_id]['full_name']
        order_data['driver_car'] = drivers_db[user_id]['car_brand']
        order_data['driver_color'] = drivers_db[user_id]['car_color']
        order_data['driver_number'] = drivers_db[user_id]['car_number']
        order_data['driver_phone'] = drivers_db[user_id]['phone']
        order_data['driver_rating'] = drivers_db[user_id].get('rating', 5.0)
        
        save_orders()
        
        await query.edit_message_text(
            f"✅ *Заказ {order_id} принят!*\n\n"
            f"📍 Подача: {order_data['pickup']}\n"
            f"🎯 Назначение: {order_data['destination']}\n"
            f"👤 Пассажир: {order_data['passenger_name']}\n"
            f"📞 {order_data['passenger_phone']}\n"
            f"💬 Комментарий: {order_data.get('comment', 'Нет')}",
            reply_markup=get_active_order_keyboard(order_id),
            parse_mode='Markdown'
        )
        
        drivers_db[user_id]['rides_today'] = drivers_db[user_id].get('rides_today', 0) + 1
        drivers_db[user_id]['total_rides'] = drivers_db[user_id].get('total_rides', 0) + 1
        drivers_db[user_id]['earnings_today'] = drivers_db[user_id].get('earnings_today', 0) + order_data['price']
        save_data()
    
    elif data.startswith('decline_'):
        order_id = data.replace('decline_', '')
        await query.edit_message_text(f"❌ Заказ {order_id} отклонен.")
    
    elif data.startswith('call_'):
        order_id = data.replace('call_', '')
        if order_id in pending_orders:
            phone = pending_orders[order_id].get('passenger_phone', 'Не указан')
            await query.answer(f"📞 Телефон пассажира: {phone}", show_alert=True)
        else:
            await query.answer("📞 Номер не найден", show_alert=True)
    
    elif data.startswith('chat_'):
        order_id = data.replace('chat_', '')
        await query.message.reply_text("💬 Введите сообщение для пассажира:")
        context.user_data['chat_order_id'] = order_id
        return WAITING_PASSENGER_CHAT
    
    elif data.startswith('nav_'):
        await query.answer("🗺️ Откройте Яндекс Навигатор", show_alert=True)
    
    elif data.startswith('arrived_'):
        order_id = data.replace('arrived_', '')
        
        if order_id in pending_orders:
            pending_orders[order_id]['status'] = 'arrived'
            save_orders()
        
        await query.edit_message_text(
            f"🚗 *Вы на месте!*\n\n"
            f"Ожидайте пассажира.\n"
            f"Когда пассажир сядет, нажмите *«🟢 Начать поездку»*.",
            reply_markup=get_active_order_keyboard(order_id),
            parse_mode='Markdown'
        )
        await query.answer("✅ Пассажир уведомлен о вашем прибытии!")
    
    elif data.startswith('start_'):
        order_id = data.replace('start_', '')
        
        if order_id in pending_orders:
            pending_orders[order_id]['status'] = 'in_progress'
            save_orders()
        
        await query.edit_message_text(
            f"🟢 *ПОЕЗДКА НАЧАЛАСЬ!*\n\n"
            f"Следуйте по маршруту.\n"
            f"После завершения нажмите *«✅ Завершить»*.",
            reply_markup=get_active_order_keyboard(order_id),
            parse_mode='Markdown'
        )
    
    elif data.startswith('complete_'):
        order_id = data.replace('complete_', '')
        if user_id in active_orders:
            del active_orders[user_id]
        
        if order_id in pending_orders:
            pending_orders[order_id]['status'] = 'completed'
            save_orders()
        
        await query.edit_message_text(
            f"✅ *Заказ {order_id} завершен!*\n\n"
            f"💰 Спасибо за работу!",
            parse_mode='Markdown'
        )
    
    elif data.startswith('nowshow_'):
        order_id = data.replace('nowshow_', '')
        if user_id in active_orders:
            del active_orders[user_id]
        
        if order_id in pending_orders:
            pending_orders[order_id]['status'] = 'cancelled'
            save_orders()
        
        await query.edit_message_text(f"⚠️ Клиент не вышел.\n💰 Начислено: 99 ₽")
        drivers_db[user_id]['earnings_today'] = drivers_db[user_id].get('earnings_today', 0) + 99
        save_data()
    
    return MAIN_MENU

async def handle_passenger_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = await update.message.reply_text("💬 Сообщение отправлено пассажиру")
    add_message_id(user_id, msg.message_id)
    return MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = await update.message.reply_text("❌ Отменено.", reply_markup=get_main_keyboard())
    add_message_id(user_id, msg.message_id)
    return MAIN_MENU

# ============ ЗАПУСК ============
def main():
    load_data()
    
    application = Application.builder().token(TOKEN).build()
    
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(check_new_orders, interval=5, first=3)
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            REG_FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_full_name)],
            REG_PHONE: [
                MessageHandler(filters.CONTACT, reg_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_phone)
            ],
            REG_CAR_BRAND: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_car_brand)],
            REG_CAR_COLOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_car_color)],
            REG_CAR_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_car_number)],
            MAIN_MENU: [
                MessageHandler(filters.Regex(r'^(🚗 Начать смену|🍔 На обед|⏹️ Завершить смену|📊 Мой профиль|📞 Диспетчер|🆘 SOS)$'), main_menu),
                CallbackQueryHandler(handle_callback),
            ],
            WAITING_PASSENGER_CHAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_passenger_chat),
            ],
            WAITING_DISPATCHER_CHAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_dispatcher_chat),
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False
    )
    
    application.add_handler(conv_handler)
    
    logger.info("🚀 Бот водителей запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()