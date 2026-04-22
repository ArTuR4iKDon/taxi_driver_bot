import logging
from datetime import datetime, timedelta
import json
import os
from warnings import filterwarnings

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from telegram.warnings import PTBUserWarning

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

# ============ КОНФИГУРАЦИЯ ============
ADMIN_BOT_TOKEN = "8697194695:AAEQ5QOp44Ppgblqs4W5jGljnR2JoE9j-TI"
ADMIN_IDS = [667474295]

# Состояния только для многошаговых действий
EDIT_PRICE, DRIVER_CHAT, BROADCAST_INPUT = range(3)

# ============ ЛОГИРОВАНИЕ ============
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ============ ФАЙЛЫ ============
DRIVERS_FILE    = 'drivers.json'
ORDERS_FILE     = 'orders.json'
PASSENGERS_FILE = 'passengers.json'
COMPLAINTS_FILE = 'complaints.json'
CHAT_LOG_FILE   = 'chat_log.json'

# ============ УТИЛИТЫ ============
def load_json(filename: str) -> dict:
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка чтения {filename}: {e}")
    return {}

def save_json(filename: str, data: dict):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения {filename}: {e}")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def fmt_num(num) -> str:
    return f"{int(num):,}".replace(",", " ")

def fmt_time(s: str) -> str:
    try:
        return datetime.fromisoformat(s).strftime("%d.%m.%Y %H:%M")
    except:
        return s or "—"

def _parse_dt(s):
    try:    return datetime.fromisoformat(s)
    except: return datetime.min

# ============ КЛАВИАТУРЫ ============
def main_kb():
    return ReplyKeyboardMarkup([
        ["📊 Статистика",      "🚗 Водители"],
        ["📋 Заказы",          "👤 Пассажиры"],
        ["⚠️ Жалобы",         "📢 Рассылка"],
        ["🗺️ Карта водителей", "🔄 Обновить"],
    ], resize_keyboard=True)

def drivers_kb(page: int = 0, per_page: int = 5):
    drivers = load_json(DRIVERS_FILE)
    items   = list(drivers.items())
    total   = max(1, (len(items) + per_page - 1) // per_page)
    chunk   = items[page * per_page : (page + 1) * per_page]

    rows = []
    for did, d in chunk:
        icon = "🟢" if d.get('online') else "🔴"
        if d.get('on_break'):   icon = "🟡"
        if d.get('emergency'):  icon = "⚠️"
        name = d.get('full_name', 'Без имени')[:20]
        rows.append([InlineKeyboardButton(
            f"{icon} {name} ({d.get('car_number','—')})",
            callback_data=f"drv_{did}"
        )])

    nav = []
    if page > 0:           nav.append(InlineKeyboardButton("◀️", callback_data=f"dpage_{page-1}"))
    nav.append(           InlineKeyboardButton(f"{page+1}/{total}", callback_data="noop"))
    if page < total - 1:  nav.append(InlineKeyboardButton("▶️", callback_data=f"dpage_{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="main")])
    return InlineKeyboardMarkup(rows)

def orders_kb(page: int = 0, per_page: int = 5):
    orders = load_json(ORDERS_FILE)
    items  = sorted(orders.items(), key=lambda x: x[1].get('created_at',''), reverse=True)
    total  = max(1, (len(items) + per_page - 1) // per_page)
    chunk  = items[page * per_page : (page + 1) * per_page]

    icons = {'searching':'🔍','accepted':'✅','arrived':'🚗',
             'in_progress':'🟢','completed':'🏁','cancelled':'❌'}
    rows = []
    for oid, o in chunk:
        ic = icons.get(o.get('status',''), '⏳')
        rows.append([InlineKeyboardButton(
            f"{ic} {oid} | {o.get('price',0)}₽ | {o.get('passenger_name','—')[:14]}",
            callback_data=f"ord_{oid}"
        )])

    nav = []
    if page > 0:           nav.append(InlineKeyboardButton("◀️", callback_data=f"opage_{page-1}"))
    nav.append(           InlineKeyboardButton(f"{page+1}/{total}", callback_data="noop"))
    if page < total - 1:  nav.append(InlineKeyboardButton("▶️", callback_data=f"opage_{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="main")])
    return InlineKeyboardMarkup(rows)

def driver_detail_kb(did: str):
    d = load_json(DRIVERS_FILE).get(did, {})
    rows = [
        [InlineKeyboardButton("📞 Позвонить", callback_data=f"calldrv_{did}"),
         InlineKeyboardButton("💬 Написать",  callback_data=f"chatdrv_{did}")],
    ]
    if d.get('latitude') and d.get('longitude'):
        rows.append([InlineKeyboardButton("📍 На карте", callback_data=f"mapdrv_{did}")])

    lbl = "🔴 Отключить" if d.get('online') else "🟢 Включить"
    rows.append([InlineKeyboardButton(lbl, callback_data=f"toggleonline_{did}")])

    lbl2 = "✅ Снять ЧП" if d.get('emergency') else "⚠️ Режим ЧП"
    rows.append([InlineKeyboardButton(lbl2, callback_data=f"toggleemerg_{did}")])

    rows.append([InlineKeyboardButton("❌ Удалить", callback_data=f"deldrv_{did}")])
    rows.append([InlineKeyboardButton("🔙 К списку", callback_data="show_drivers")])
    return InlineKeyboardMarkup(rows)

def order_detail_kb(oid: str):
    o      = load_json(ORDERS_FILE).get(oid, {})
    status = o.get('status', '')
    rows   = []

    if status == 'searching':
        rows.append([InlineKeyboardButton("❌ Отменить", callback_data=f"cancelord_{oid}")])
    elif status in ('accepted', 'arrived', 'in_progress'):
        rows.append([InlineKeyboardButton("📞 Водитель",  callback_data=f"callorddrv_{oid}"),
                     InlineKeyboardButton("📞 Пассажир",  callback_data=f"callordpas_{oid}")])
        rows.append([InlineKeyboardButton("❌ Отменить",  callback_data=f"cancelord_{oid}")])

    if status not in ('completed', 'cancelled'):
        rows.append([InlineKeyboardButton("✏️ Изменить цену", callback_data=f"editprice_{oid}")])

    rows.append([InlineKeyboardButton("🔙 К списку", callback_data="show_orders")])
    return InlineKeyboardMarkup(rows)

def stats_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Сегодня",  callback_data="stats_today"),
         InlineKeyboardButton("📈 Неделя",   callback_data="stats_week"),
         InlineKeyboardButton("📉 Месяц",    callback_data="stats_month")],
        [InlineKeyboardButton("🔄 Обновить", callback_data="stats_today")],
        [InlineKeyboardButton("🔙 Назад",    callback_data="main")],
    ])

def broadcast_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 Всем водителям",     callback_data="bc_drivers")],
        [InlineKeyboardButton("🟢 Активным водителям", callback_data="bc_active")],
        [InlineKeyboardButton("👥 Всем пассажирам",    callback_data="bc_passengers")],
        [InlineKeyboardButton("🔙 Назад",               callback_data="main")],
    ])

# ============ /start ============
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    name = update.effective_user.first_name or "Администратор"
    await update.message.reply_text(
        f"👨‍💼 *ПАНЕЛЬ АДМИНИСТРАТОРА*\n\n"
        f"👋 *{name}*, добро пожаловать!\n\n"
        f"Выберите раздел:",
        reply_markup=main_kb(),
        parse_mode='Markdown'
    )

# ============ ГЛАВНОЕ МЕНЮ ============
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    if not is_admin(uid): return
    text = update.message.text

    if text == "📊 Статистика":
        await _show_stats(update, context, send_new=True)
    elif text == "🚗 Водители":
        await _show_drivers(update, context, send_new=True)
    elif text == "📋 Заказы":
        await _show_orders(update, context, send_new=True)
    elif text == "👤 Пассажиры":
        await _show_passengers(update, context)
    elif text == "⚠️ Жалобы":
        await _show_complaints(update, context)
    elif text == "📢 Рассылка":
        await update.message.reply_text(
            "📢 *РАССЫЛКА*\n\nВыберите получателей:",
            reply_markup=broadcast_kb(), parse_mode='Markdown'
        )
    elif text == "🗺️ Карта водителей":
        await _show_map(update, context)
    elif text == "🔄 Обновить":
        await update.message.reply_text("✅ Обновлено!", reply_markup=main_kb())

# ============ ПОКАЗ ДАННЫХ ============
async def _show_drivers(update, context, page=0, send_new=False):
    drivers = load_json(DRIVERS_FILE)
    online  = sum(1 for d in drivers.values() if d.get('online'))
    on_brk  = sum(1 for d in drivers.values() if d.get('on_break'))
    emerg   = sum(1 for d in drivers.values() if d.get('emergency'))
    geo     = sum(1 for d in drivers.values() if d.get('latitude'))

    text = (
        f"🚗 *ВОДИТЕЛИ*\n\n"
        f"👥 Всего: *{len(drivers)}*\n"
        f"🟢 На линии: *{online}*\n"
        f"🟡 На обеде: *{on_brk}*\n"
        f"⚠️ В ЧП: *{emerg}*\n"
        f"📍 С геолокацией: *{geo}*"
    ) if drivers else "🚗 *ВОДИТЕЛИ*\n\nНет водителей."

    kb = drivers_kb(page)
    if send_new:
        await update.message.reply_text(text, reply_markup=kb, parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')

async def _show_orders(update, context, page=0, send_new=False):
    orders    = load_json(ORDERS_FILE)
    active    = sum(1 for o in orders.values() if o.get('status') in ('searching','accepted','arrived','in_progress'))
    completed = sum(1 for o in orders.values() if o.get('status') == 'completed')
    cancelled = sum(1 for o in orders.values() if o.get('status') == 'cancelled')
    revenue   = sum(o.get('price',0) for o in orders.values() if o.get('status') == 'completed')

    text = (
        f"📋 *ЗАКАЗЫ*\n\n"
        f"📊 Всего: *{len(orders)}*\n"
        f"🟢 Активных: *{active}*\n"
        f"✅ Завершено: *{completed}*\n"
        f"❌ Отменено: *{cancelled}*\n"
        f"💰 Выручка: *{fmt_num(revenue)} ₽*"
    ) if orders else "📋 *ЗАКАЗЫ*\n\nНет заказов."

    kb = orders_kb(page)
    if send_new:
        await update.message.reply_text(text, reply_markup=kb, parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')

async def _show_stats(update, context, period="today", send_new=False):
    orders     = load_json(ORDERS_FILE)
    drivers    = load_json(DRIVERS_FILE)
    passengers = load_json(PASSENGERS_FILE)
    now = datetime.now()

    starts = {
        "today": now.replace(hour=0, minute=0, second=0, microsecond=0),
        "week":  now - timedelta(days=7),
        "month": now - timedelta(days=30)
    }
    start_dt = starts.get(period, starts["today"])
    done     = [o for o in orders.values()
                if o.get('status') == 'completed' and _parse_dt(o.get('created_at','')) >= start_dt]
    revenue  = sum(o.get('price',0) for o in done)
    online   = sum(1 for d in drivers.values() if d.get('online'))
    active   = sum(1 for o in orders.values() if o.get('status') in ('searching','accepted','arrived','in_progress'))
    pnames   = {"today":"СЕГОДНЯ","week":"НЕДЕЛЯ","month":"МЕСЯЦ"}

    text = (
        f"📊 *СТАТИСТИКА — {pnames.get(period,'СЕГОДНЯ')}*\n\n"
        f"🚗 Водителей онлайн: *{online}*\n"
        f"📋 Активных заказов: *{active}*\n"
        f"✅ Завершено: *{len(done)}*\n"
        f"💰 Выручка: *{fmt_num(revenue)} ₽*\n"
        f"👥 Пассажиров: *{len(passengers)}*\n\n"
        f"🕐 {now.strftime('%H:%M:%S')}"
    )
    kb = stats_kb()
    if send_new:
        await update.message.reply_text(text, reply_markup=kb, parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')

async def _show_passengers(update, context):
    p = load_json(PASSENGERS_FILE)
    if not p:
        await update.message.reply_text("👤 *ПАССАЖИРЫ*\n\nНет пассажиров.", parse_mode='Markdown')
        return
    rides = sum(x.get('rides_count',0) for x in p.values())
    spent = sum(x.get('total_spent',0) for x in p.values())
    text  = (f"👤 *ПАССАЖИРЫ*\n\n"
             f"👥 Всего: *{len(p)}*\n"
             f"🚖 Поездок: *{rides}*\n"
             f"💰 Потрачено: *{fmt_num(spent)} ₽*\n\n———\n")
    for _, pp in sorted(p.items(), key=lambda x: x[1].get('registered_at',''), reverse=True)[:10]:
        text += f"• {pp.get('name','—')}: {pp.get('rides_count',0)} поездок\n"
    await update.message.reply_text(text, parse_mode='Markdown')

async def _show_complaints(update, context):
    c = load_json(COMPLAINTS_FILE)
    if not c:
        await update.message.reply_text("⚠️ *ЖАЛОБЫ*\n\nНет жалоб.", parse_mode='Markdown')
        return
    text = f"⚠️ *ЖАЛОБЫ* — всего *{len(c)}*\n\n———\n\n"
    for _, complaint in list(c.items())[:5]:
        text += (f"📋 *{complaint.get('category','—')}*\n"
                 f"👤 {complaint.get('passenger_name','—')}\n"
                 f"💬 {complaint.get('text','—')[:100]}\n\n")
    await update.message.reply_text(text, parse_mode='Markdown')

async def _show_map(update, context):
    drivers  = load_json(DRIVERS_FILE)
    with_geo = {did: d for did, d in drivers.items()
                if d.get('online') and d.get('latitude') and d.get('longitude')}

    if not with_geo:
        await update.message.reply_text(
            "🗺️ *КАРТА ВОДИТЕЛЕЙ*\n\n"
            "Нет водителей с активной геолокацией.\n\n"
            "💡 Водитель должен отправить геолокацию в боте водителя.",
            parse_mode='Markdown'
        )
        return

    await update.message.reply_text(
        f"🗺️ *КАРТА ВОДИТЕЛЕЙ*\n\nОнлайн с геолокацией: *{len(with_geo)}*",
        parse_mode='Markdown'
    )
    for _, d in with_geo.items():
        try:
            await context.bot.send_venue(
                chat_id=update.effective_user.id,
                latitude=d['latitude'], longitude=d['longitude'],
                title=d.get('full_name','—'),
                address=f"{d.get('car_brand','')} {d.get('car_number','')}".strip()
            )
        except:
            await context.bot.send_location(
                chat_id=update.effective_user.id,
                latitude=d['latitude'], longitude=d['longitude']
            )

# ============ ДЕТАЛИ ============
async def _driver_detail(update, context, did: str):
    d = load_json(DRIVERS_FILE).get(did)
    if not d:
        await update.callback_query.edit_message_text("❌ Водитель не найден")
        return
    status = "🟢 На линии" if d.get('online') else "🔴 Не на линии"
    if d.get('on_break'):  status = "🟡 На обеде"
    if d.get('emergency'): status = "⚠️ Режим ЧП"
    geo = ""
    if d.get('latitude'):
        upd = d.get('location_updated','')
        geo = f"\n📍 Геолокация: {upd[:16] if upd else '—'}"
    text = (
        f"🚗 *ВОДИТЕЛЬ*\n\n"
        f"👤 {d.get('full_name','—')}\n"
        f"📱 {d.get('phone','—')}\n"
        f"🚘 {d.get('car_brand','—')} {d.get('car_color','—')} / {d.get('car_number','—')}\n"
        f"⭐ Рейтинг: {d.get('rating',5.0)}\n"
        f"📊 {status}{geo}\n\n"
        f"🚖 Сегодня: {d.get('rides_today',0)} поездок | {d.get('earnings_today',0)} ₽\n"
        f"📋 Всего: {d.get('total_rides',0)} поездок"
    )
    await update.callback_query.edit_message_text(
        text, reply_markup=driver_detail_kb(did), parse_mode='Markdown'
    )

async def _order_detail(update, context, oid: str):
    o = load_json(ORDERS_FILE).get(oid)
    if not o:
        await update.callback_query.edit_message_text("❌ Заказ не найден")
        return
    snames = {'searching':'🔍 Поиск','accepted':'✅ Назначен','arrived':'🚗 На месте',
              'in_progress':'🟢 В пути','completed':'🏁 Завершён','cancelled':'❌ Отменён'}
    hist = ""
    for rec in o.get('price_history',[])[-3:]:
        hist += f"\n  {rec['old']}₽→{rec['new']}₽ ({rec['time'][:16]})"
    text = (
        f"📋 *ЗАКАЗ {oid}*\n\n"
        f"📊 {snames.get(o.get('status',''),'—')}\n\n"
        f"👤 {o.get('passenger_name','—')}  📱 {o.get('passenger_phone','—')}\n\n"
        f"🚩 {o.get('pickup','—')}\n"
        f"🏁 {o.get('destination','—')}\n\n"
        f"🚗 {o.get('driver_name','Не назначен')}  {o.get('driver_car','')}\n\n"
        f"💰 *{o.get('price',0)} ₽*  |  {o.get('payment_method','—')}"
        f"{hist}\n\n"
        f"🕐 {fmt_time(o.get('created_at',''))}"
    )
    await update.callback_query.edit_message_text(
        text, reply_markup=order_detail_kb(oid), parse_mode='Markdown'
    )

# ============ CALLBACK HANDLER ============
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    data = q.data
    uid  = update.effective_user.id

    if not is_admin(uid):
        await q.edit_message_text("⛔ Нет доступа")
        return

    if data == "noop":   return
    if data == "main":
        await q.edit_message_text("👨‍💼 Используйте кнопки меню ниже.")
        return

    if data == "show_drivers":
        await _show_drivers(update, context); return
    if data == "show_orders":
        await _show_orders(update, context);  return

    if data.startswith("dpage_"):
        await _show_drivers(update, context, int(data[6:])); return
    if data.startswith("opage_"):
        await _show_orders(update, context,  int(data[6:])); return

    if data in ("stats_today","stats_week","stats_month"):
        await _show_stats(update, context, data.replace("stats_","")); return

    if data.startswith("drv_"):
        await _driver_detail(update, context, data[4:]); return

    if data.startswith("ord_"):
        await _order_detail(update, context, data[4:]); return

    if data.startswith("calldrv_"):
        phone = load_json(DRIVERS_FILE).get(data[8:],{}).get('phone','Не указан')
        await q.answer(f"📞 {phone}", show_alert=True); return

    if data.startswith("mapdrv_"):
        d = load_json(DRIVERS_FILE).get(data[7:],{})
        if d.get('latitude') and d.get('longitude'):
            await context.bot.send_venue(
                chat_id=uid, latitude=d['latitude'], longitude=d['longitude'],
                title=d.get('full_name','—'),
                address=f"{d.get('car_brand','')} {d.get('car_number','')}".strip()
            )
        else:
            await q.answer("❌ Геолокация недоступна", show_alert=True)
        return

    if data.startswith("toggleonline_"):
        did = data[13:]
        drivers = load_json(DRIVERS_FILE)
        if did in drivers:
            drivers[did]['online'] = not drivers[did].get('online', False)
            save_json(DRIVERS_FILE, drivers)
        await _driver_detail(update, context, did); return

    if data.startswith("toggleemerg_"):
        did = data[12:]
        drivers = load_json(DRIVERS_FILE)
        if did in drivers:
            drivers[did]['emergency'] = not drivers[did].get('emergency', False)
            if drivers[did]['emergency']:
                drivers[did]['online'] = False
            save_json(DRIVERS_FILE, drivers)
        await _driver_detail(update, context, did); return

    if data.startswith("deldrv_"):
        did = data[7:]
        drivers = load_json(DRIVERS_FILE)
        drivers.pop(did, None)
        save_json(DRIVERS_FILE, drivers)
        await q.answer("✅ Удалён")
        await _show_drivers(update, context); return

    if data.startswith("cancelord_"):
        oid = data[10:]
        orders = load_json(ORDERS_FILE)
        if oid in orders:
            orders[oid]['status'] = 'cancelled'
            save_json(ORDERS_FILE, orders)
        await q.answer("✅ Отменён")
        await _order_detail(update, context, oid); return

    if data.startswith("callorddrv_"):
        phone = load_json(ORDERS_FILE).get(data[11:],{}).get('driver_phone','Не указан')
        await q.answer(f"📞 Водитель: {phone}", show_alert=True); return

    if data.startswith("callordpas_"):
        phone = load_json(ORDERS_FILE).get(data[11:],{}).get('passenger_phone','Не указан')
        await q.answer(f"📞 Пассажир: {phone}", show_alert=True); return

    # ── Изменение цены ──────────────────────────────────────────
    if data.startswith("editprice_"):
        oid = data[10:]
        o   = load_json(ORDERS_FILE).get(oid, {})
        context.user_data['edit_price_oid'] = oid
        await q.edit_message_text(
            f"✏️ *ИЗМЕНЕНИЕ ЦЕНЫ*\n\n"
            f"📋 Заказ: *{oid}*\n"
            f"💰 Текущая цена: *{o.get('price',0)} ₽*\n\n"
            f"Введите новую цену числом\n(или /cancel для отмены):",
            parse_mode='Markdown'
        )
        return EDIT_PRICE

    # ── Чат с водителем ─────────────────────────────────────────
    if data.startswith("chatdrv_"):
        did = data[8:]
        d   = load_json(DRIVERS_FILE).get(did, {})
        context.user_data['chat_did']  = did
        context.user_data['chat_name'] = d.get('full_name','—')
        await q.edit_message_text(
            f"💬 *ЧАТ С ВОДИТЕЛЕМ*\n\n"
            f"👤 {d.get('full_name','—')}\n"
            f"🚘 {d.get('car_brand','')} {d.get('car_number','')}\n\n"
            f"Введите сообщение\n(или /cancel для отмены):",
            parse_mode='Markdown'
        )
        return DRIVER_CHAT

    # ── Рассылка ────────────────────────────────────────────────
    if data in ("bc_drivers","bc_active","bc_passengers"):
        labels = {"bc_drivers":"ВОДИТЕЛЯМ","bc_active":"АКТИВНЫМ ВОДИТЕЛЯМ","bc_passengers":"ПАССАЖИРАМ"}
        context.user_data['bc_target'] = data
        await q.edit_message_text(
            f"📢 *РАССЫЛКА — {labels[data]}*\n\n"
            f"Введите текст\n(или /cancel для отмены):",
            parse_mode='Markdown'
        )
        return BROADCAST_INPUT

# ============ CONV: ИЗМЕНЕНИЕ ЦЕНЫ ============
async def edit_price_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    oid  = context.user_data.get('edit_price_oid')
    text = update.message.text.strip()

    try:
        new_price = int(text)
        if new_price <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Введите целое число больше 0.\nНапример: `350`", parse_mode='Markdown'
        )
        return EDIT_PRICE

    orders = load_json(ORDERS_FILE)
    if oid not in orders:
        await update.message.reply_text("❌ Заказ не найден.", reply_markup=main_kb())
        context.user_data.clear()
        return ConversationHandler.END

    old_price = orders[oid].get('price', 0)
    orders[oid].setdefault('price_history', []).append({
        'old': old_price, 'new': new_price,
        'time': datetime.now().isoformat(),
        'by': update.effective_user.id
    })
    orders[oid]['price'] = new_price
    save_json(ORDERS_FILE, orders)

    driver_id = orders[oid].get('driver_id')
    notified  = False
    if driver_id:
        try:
            await context.bot.send_message(
                chat_id=int(driver_id),
                text=f"💰 *ЦЕНА ИЗМЕНЕНА*\n\nЗаказ: {oid}\n{old_price} ₽ → *{new_price} ₽*",
                parse_mode='Markdown'
            )
            notified = True
        except: pass

    await update.message.reply_text(
        f"✅ *Цена обновлена*\n\n📋 {oid}\n💰 {old_price} ₽ → *{new_price} ₽*"
        + ("\n✉️ Водитель уведомлён." if notified else ""),
        reply_markup=main_kb(), parse_mode='Markdown'
    )
    context.user_data.clear()
    return ConversationHandler.END

# ============ CONV: ЧАТ С ВОДИТЕЛЕМ ============
async def driver_chat_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    did   = context.user_data.get('chat_did')
    dname = context.user_data.get('chat_name','—')
    msg   = update.message.text

    log = load_json(CHAT_LOG_FILE)
    log.setdefault(did, []).append({
        'from': 'dispatcher', 'text': msg,
        'time': datetime.now().isoformat()
    })
    save_json(CHAT_LOG_FILE, log)

    try:
        await context.bot.send_message(
            chat_id=int(did),
            text=(f"💬 *СООБЩЕНИЕ ОТ ДИСПЕТЧЕРА*\n\n{msg}\n\n———\n"
                  f"🕐 {datetime.now().strftime('%H:%M')}"),
            parse_mode='Markdown'
        )
        await update.message.reply_text(
            f"✅ Доставлено → *{dname}*\n\n«{msg[:80]}{'...' if len(msg)>80 else ''}»",
            reply_markup=main_kb(), parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Не удалось доставить.\n{e}", reply_markup=main_kb()
        )

    context.user_data.clear()
    return ConversationHandler.END

# ============ CONV: РАССЫЛКА ============
async def broadcast_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = context.user_data.get('bc_target','')
    msg    = update.message.text

    if target == 'bc_drivers':
        ids  = list(load_json(DRIVERS_FILE).keys())
        name = "водителям"
    elif target == 'bc_active':
        ids  = [did for did, d in load_json(DRIVERS_FILE).items() if d.get('online')]
        name = "активным водителям"
    elif target == 'bc_passengers':
        ids  = list(load_json(PASSENGERS_FILE).keys())
        name = "пассажирам"
    else:
        await update.message.reply_text("❌ Ошибка", reply_markup=main_kb())
        return ConversationHandler.END

    ok = fail = 0
    for rid in ids:
        try:
            await context.bot.send_message(
                chat_id=int(rid),
                text=f"📢 *ОТ АДМИНИСТРАЦИИ*\n\n{msg}",
                parse_mode='Markdown'
            )
            ok += 1
        except:
            fail += 1

    await update.message.reply_text(
        f"✅ *Рассылка завершена* — {name}\n\n✅ Доставлено: *{ok}*\n❌ Ошибок: *{fail}*",
        reply_markup=main_kb(), parse_mode='Markdown'
    )
    context.user_data.clear()
    return ConversationHandler.END

# ============ /cancel ============
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Отменено.", reply_markup=main_kb())
    return ConversationHandler.END

# ============ ЗАПУСК ============
def main():
    app = Application.builder().token(ADMIN_BOT_TOKEN).build()

    # ConversationHandler — только для многошаговых действий
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(callback_handler)],
        states={
            EDIT_PRICE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_price_input)],
            DRIVER_CHAT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_chat_input)],
            BROADCAST_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_input)],
        },
        fallbacks=[CommandHandler('cancel', cmd_cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler('start',  cmd_start))
    app.add_handler(CommandHandler('cancel', cmd_cancel))

    # Кнопки главного меню — вне ConversationHandler, ВСЕГДА работают
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(
            r'^(📊 Статистика|🚗 Водители|📋 Заказы|👤 Пассажиры'
            r'|⚠️ Жалобы|📢 Рассылка|🗺️ Карта водителей|🔄 Обновить)$'
        ),
        menu_handler
    ))

    app.add_handler(conv)

    # Остальные callback'и (вне conv)
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("🚀 Бот администратора запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()