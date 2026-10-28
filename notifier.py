import html
import logging
from collections.abc import Sequence
from typing import Any

import httpx
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from analyzer import ArbitrageDeal
from config import Settings, get_settings
from database import get_active_subscribers, get_subscriber_threshold

logger = logging.getLogger(__name__)


def build_deal_inline_keyboard(deal: ArbitrageDeal) -> InlineKeyboardMarkup:
    """Создает интерактивную inline-клавиатуру под карточкой лота:

    - Кнопка-ссылка: прямой переход на маркет
    - Кнопка напоминания через 5 минут
    - Кнопка деталей расчета
    """
    deal_id = deal.id or "unknown"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Открыть лот на маркете",
                    url=deal.link,
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏱ Напомнить через 5 мин",
                    callback_data=f"remind:{deal_id}",
                ),
                InlineKeyboardButton(
                    text="📊 Детали расчета",
                    callback_data=f"calc:{deal_id}",
                ),
            ],
        ]
    )


def format_deal_card(deal: ArbitrageDeal) -> str:
    """Формирует форматированное HTML-сообщение с карточкой лота."""
    safe_name = html.escape(deal.item_name)
    safe_link = html.escape(deal.link)
    sign = "+" if deal.profit_percent > 0 else ""

    return (
        "🎯 <b>Выгодный лот обнаружен!</b>\n\n"
        f"📦 <b>Название:</b> {safe_name}\n"
        f"💰 <b>Покупка:</b> {deal.buy_price:.2f} ₽\n"
        f"📊 <b>Медиана:</b> {deal.median_price:.2f} ₽\n"
        f"💵 <b>Чистый профит:</b> {deal.net_profit:+.2f} ₽\n"
        f"📈 <b>Выгода:</b> {sign}{deal.profit_percent:.2f}%\n\n"
        f'🔗 <a href="{safe_link}">Прямая ссылка</a>'
    )


async def send_to_single_chat(
    client: httpx.AsyncClient,
    bot_token: str,
    chat_id: int | str,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Отправляет сообщение конкретному пользователю/чату в Telegram."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        response = await client.post(url, json=payload)
        if response.status_code == 200:
            data = response.json()
            msg_id = data.get("result", {}).get("message_id", "N/A")
            return True, f"message_id: {msg_id}"

        try:
            description = response.json().get("description", response.text)
        except Exception:
            description = response.text
        return False, f"HTTP {response.status_code}: {description}"
    except httpx.HTTPError as exc:
        return False, f"Сетевая ошибка: {exc}"


async def send_telegram_deal_notification_with_result(
    deal: ArbitrageDeal,
    chat_ids: Sequence[int | str] | None = None,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Асинхронно рассылает карточку сделки активным подписчикам с inline-кнопками."""
    cfg = settings or get_settings()

    if not cfg.telegram_bot_token:
        logger.warning(
            "Telegram bot token is missing. Notification skipped for '%s'.",
            deal.item_name,
        )
        return False, "Не настроен TELEGRAM_BOT_TOKEN в .env"

    recipients: list[int | str] = []
    if chat_ids is not None:
        recipients = list(chat_ids)
    else:
        # Извлекаем подписчиков из SQLite базы данных с учетом их персонального порога
        active_db_subscribers = get_active_subscribers(cfg.database_path)
        if active_db_subscribers:
            for sub_id in active_db_subscribers:
                user_threshold = get_subscriber_threshold(sub_id, cfg.database_path)
                if deal.profit_percent >= user_threshold:
                    recipients.append(sub_id)
        elif cfg.telegram_chat_id:
            recipients = [cfg.telegram_chat_id]

    if not recipients:
        logger.info(
            "No active Telegram subscribers found for '%s'. Notification skipped.",
            deal.item_name,
        )
        return False, "Список подходящих активных подписчиков пуст"

    text = format_deal_card(deal)
    markup = (
        reply_markup
        if reply_markup is not None
        else build_deal_inline_keyboard(deal).model_dump(exclude_none=True)
    )
    successful_deliveries = 0
    errors: list[str] = []

    async def _send_all(http_client: httpx.AsyncClient) -> None:
        nonlocal successful_deliveries
        for cid in recipients:
            ok, detail = await send_to_single_chat(
                client=http_client,
                bot_token=cfg.telegram_bot_token,  # type: ignore[arg-type]
                chat_id=cid,
                text=text,
                reply_markup=markup,
            )
            if ok:
                successful_deliveries += 1
                logger.info("Alert sent to chat %s: %s", cid, detail)
            else:
                errors.append(f"Chat {cid}: {detail}")
                logger.warning("Failed to send alert to chat %s: %s", cid, detail)

    if client is not None:
        await _send_all(client)
    else:
        async with httpx.AsyncClient(timeout=10.0) as default_client:
            await _send_all(default_client)

    if successful_deliveries > 0:
        summary = (
            f"Успешно доставлено {successful_deliveries}/{len(recipients)} подписчикам "
            f"для '{deal.item_name}'"
        )
        return True, summary

    return False, f"Не удалось доставить: {'; '.join(errors)}"


async def send_telegram_deal_notification(
    deal: ArbitrageDeal,
    chat_ids: Sequence[int | str] | None = None,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """Асинхронно отправляет карточку сделки подписчикам (булевый флаг для воркера)."""
    success, _ = await send_telegram_deal_notification_with_result(
        deal=deal,
        chat_ids=chat_ids,
        settings=settings,
        client=client,
        reply_markup=reply_markup,
    )
    return success


class TelegramNotifier:
    """Класс-обертка для отправки уведомлений подписчикам."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client

    def format_card(self, deal: ArbitrageDeal) -> str:
        return format_deal_card(deal)

    def get_keyboard(self, deal: ArbitrageDeal) -> InlineKeyboardMarkup:
        return build_deal_inline_keyboard(deal)

    async def send(
        self,
        deal: ArbitrageDeal,
        chat_ids: Sequence[int | str] | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        return await send_telegram_deal_notification(
            deal=deal,
            chat_ids=chat_ids,
            settings=self.settings,
            client=self.client,
            reply_markup=reply_markup,
        )

    async def send_with_details(
        self,
        deal: ArbitrageDeal,
        chat_ids: Sequence[int | str] | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        return await send_telegram_deal_notification_with_result(
            deal=deal,
            chat_ids=chat_ids,
            settings=self.settings,
            client=self.client,
            reply_markup=reply_markup,
        )
