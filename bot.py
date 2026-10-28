import asyncio
import contextlib
import html
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from analyzer import ArbitrageDeal, get_all_registered_deals, get_deal_by_id
from config import get_settings
from database import (
    add_subscriber,
    get_subscriber_threshold,
    set_subscriber_threshold,
)
from market_client import MockMarketClient

logger = logging.getLogger(__name__)
router = Router(name="price_radar_router")


def build_settings_keyboard(current_threshold: float = 15.0) -> InlineKeyboardMarkup:
    """Формирует клавиатуру выбора минимального порога прибыли."""
    thresholds = [10.0, 15.0, 20.0, 25.0]
    row: list[InlineKeyboardButton] = []
    for t in thresholds:
        active_mark = "✔ " if abs(t - current_threshold) < 0.1 else ""
        row.append(
            InlineKeyboardButton(
                text=f"{active_mark}{int(t)}%",
                callback_data=f"set_threshold:{t}",
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[row])


def build_deal_breakdown_text(deal: ArbitrageDeal, fee_percent: float = 15.0) -> str:
    """Формирует детальную разбивку математики сделки."""
    fee_amount = round(deal.median_price * (fee_percent / 100.0), 2)
    payout = round(deal.median_price - fee_amount, 2)
    sign = "+" if deal.profit_percent > 0 else ""

    return (
        f"📊 Детали расчета: {deal.item_name}\n\n"
        f"• Цена закупки: {deal.buy_price:.2f} ₽\n"
        f"• Медианная цена (24ч): {deal.median_price:.2f} ₽\n"
        f"• Комиссия площадки ({fee_percent}%): -{fee_amount:.2f} ₽\n"
        f"• Выплата продавцу: {payout:.2f} ₽\n"
        f"• Чистая маржа: {deal.net_profit:+.2f} ₽ ({sign}{deal.profit_percent:.2f}%)"
    )


async def schedule_deal_reminder(
    bot: Bot,
    chat_id: int,
    deal: ArbitrageDeal,
    delay_seconds: int = 300,
    market_client: MockMarketClient | None = None,
) -> bool:
    """Отложенная асинхронная задача проверки статуса лота через N секунд."""
    await asyncio.sleep(delay_seconds)
    client = market_client or MockMarketClient()
    current_item = await client.fetch_item(deal.item_name)

    # Проверяем, что лот все еще актуален (цена покупки не выросла критично)
    if current_item and current_item.buy_price <= deal.buy_price * 1.05:
        text = (
            f"🔔 <b>Напоминание: лот все еще в продаже!</b>\n\n"
            f"📦 <b>{html.escape(deal.item_name)}</b>\n"
            f"💰 Текущая цена покупки: <b>{current_item.buy_price:.2f} ₽</b>\n"
            f"📊 Медиана: <b>{current_item.median_price:.2f} ₽</b>\n\n"
            f'🔗 <a href="{deal.link}">Перейти к покупке</a>'
        )
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=False,
            )
            return True
        except Exception as exc:
            logger.error("Failed to send reminder push to chat %s: %s", chat_id, exc)
            return False
    return False


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Обработчик команды /start: регистрирует подписчика и выводит меню."""
    if message.from_user:
        add_subscriber(message.chat.id)
    await message.answer(
        "👋 <b>Добро пожаловать в Price Radar Bot!</b>\n\n"
        "Я отслеживаю выгодные игровые скины и арбитражные связки в реальном времени.\n\n"
        "Команды:\n"
        "• /active — актуальные выгодные предметы прямо сейчас\n"
        "• /settings — настроить персональный порог профита\n",
        parse_mode="HTML",
    )


@router.message(Command("active"))
async def cmd_active(message: Message) -> None:
    """Выводит список всех найденных выгодных предметов с кнопками перехода."""
    deals = get_all_registered_deals()
    if not deals:
        client = MockMarketClient()
        deals = await client.scan_profitable_deals()

    if not deals:
        await message.answer("🔍 В данный момент выгодных лотов не обнаружено.")
        return

    text = f"🔥 <b>Актуальные выгодные лоты ({len(deals)}):</b>\n\n"
    buttons: list[list[InlineKeyboardButton]] = []

    for deal in deals:
        sign = "+" if deal.profit_percent > 0 else ""
        text += (
            f"• <b>{html.escape(deal.item_name)}</b>\n"
            f"  Покупка: <code>{deal.buy_price:.2f} ₽</code> | "
            f"Маржа: <b>{sign}{deal.profit_percent:.2f}%</b> ({deal.net_profit:+.2f} ₽)\n\n"
        )
        buttons.append([InlineKeyboardButton(text=f"🔗 {deal.item_name}", url=deal.link)])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    """Выводит inline-кнопки настройки порога прибыли (10%, 15%, 20%, 25%)."""
    current_threshold = get_subscriber_threshold(message.chat.id)
    keyboard = build_settings_keyboard(current_threshold)
    await message.answer(
        f"⚙ <b>Настройки уведомлений</b>\n\n"
        f"Текущий минимальный порог профита: <b>{current_threshold:.0f}%</b>.\n"
        f"Выберите желаемый порог для фильтрации алертов:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("set_threshold:"))
async def cb_set_threshold(callback: CallbackQuery) -> None:
    """Обрабатывает смену порога прибыли для подписчика."""
    if not callback.data:
        return
    threshold_val = float(callback.data.split(":", 1)[1])
    chat_id = callback.message.chat.id if callback.message else 0
    set_subscriber_threshold(chat_id, threshold_val)

    keyboard = build_settings_keyboard(threshold_val)
    await callback.answer(f"Порог прибыли обновлен: {int(threshold_val)}%")
    if callback.message:
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                f"⚙ <b>Настройки уведомлений</b>\n\n"
                f"Текущий минимальный порог профита: <b>{threshold_val:.0f}%</b>.\n"
                f"Выберите желаемый порог для фильтрации алертов:",
                reply_markup=keyboard,
                parse_mode="HTML",
            )


@router.callback_query(F.data.startswith("remind:"))
async def cb_remind(callback: CallbackQuery, bot: Bot) -> None:
    """Обрабатывает нажатие 'напомнить через 5 мин'."""
    if not callback.data:
        return
    deal_id = callback.data.split(":", 1)[1]
    deal = get_deal_by_id(deal_id)

    await callback.answer("Таймер на 5 минут установлен!", show_alert=False)

    if deal and callback.message:
        chat_id = callback.message.chat.id
        asyncio.create_task(
            schedule_deal_reminder(
                bot=bot,
                chat_id=chat_id,
                deal=deal,
                delay_seconds=300,
            )
        )


@router.callback_query(F.data.startswith("calc:"))
async def cb_calc(callback: CallbackQuery) -> None:
    """Обрабатывает нажатие 'детали расчета'."""
    if not callback.data:
        return
    deal_id = callback.data.split(":", 1)[1]
    deal = get_deal_by_id(deal_id)

    if not deal:
        await callback.answer(
            "Данные по лоту устарели или не найдены в памяти воркера.",
            show_alert=True,
        )
        return

    cfg = get_settings()
    breakdown = build_deal_breakdown_text(deal, fee_percent=cfg.market_fee_percent)
    await callback.answer(breakdown, show_alert=True)


def create_dispatcher() -> Dispatcher:
    """Создает и настраивает диспетчер aiogram."""
    dp = Dispatcher()
    dp.include_router(router)
    return dp


async def start_bot_polling(token: str | None = None) -> None:
    """Запускает бота в режиме long-polling."""
    cfg = get_settings()
    bot_token = token or cfg.telegram_bot_token
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not provided; cannot start bot polling.")
        return

    bot = Bot(token=bot_token)
    dp = create_dispatcher()
    logger.info("Starting Price Radar Telegram Bot polling...")
    await dp.start_polling(bot)
