import html
import logging

import httpx

from analyzer import ArbitrageDeal
from config import Settings, get_settings

logger = logging.getLogger(__name__)


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


async def send_telegram_deal_notification_with_result(
    deal: ArbitrageDeal,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[bool, str]:
    """Асинхронно отправляет карточку сделки в Telegram с возвратом статуса и описания.

    Возвращает (success: bool, reason: str).
    """
    cfg = settings or get_settings()

    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        reason = "Telegram bot token or chat ID is missing."
        logger.warning(
            "%s Notification skipped for '%s'.",
            reason,
            deal.item_name,
        )
        return False, "Не настроены TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID в .env"

    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": cfg.telegram_chat_id,
        "text": format_deal_card(deal),
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        if client is not None:
            response = await client.post(url, json=payload)
        else:
            async with httpx.AsyncClient(timeout=10.0) as default_client:
                response = await default_client.post(url, json=payload)

        if response.status_code == 200:
            data = response.json()
            msg_id = data.get("result", {}).get("message_id", "N/A")
            logger.info("Telegram notification successfully sent for '%s'", deal.item_name)
            return True, f"Карточка успешно доставлена в Telegram (message_id: {msg_id})"

        # Обработка ошибки со стороны Telegram API
        try:
            error_data = response.json()
            description = error_data.get("description", response.text)
        except Exception:
            description = response.text

        error_msg = f"HTTP {response.status_code}: {description}"
        logger.error(
            "HTTP error while sending Telegram notification for '%s': %s",
            deal.item_name,
            error_msg,
        )
        return False, error_msg

    except httpx.HTTPError as exc:
        error_msg = f"Сетевая ошибка: {exc}"
        logger.error(
            "HTTP error while sending Telegram notification for '%s': %s",
            deal.item_name,
            exc,
        )
        return False, error_msg


async def send_telegram_deal_notification(
    deal: ArbitrageDeal,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Асинхронно отправляет карточку сделки в Telegram (булевый флаг для воркера)."""
    success, _ = await send_telegram_deal_notification_with_result(
        deal=deal,
        settings=settings,
        client=client,
    )
    return success


class TelegramNotifier:
    """Класс-обертка для отправки уведомлений."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client

    def format_card(self, deal: ArbitrageDeal) -> str:
        return format_deal_card(deal)

    async def send(self, deal: ArbitrageDeal) -> bool:
        return await send_telegram_deal_notification(
            deal=deal,
            settings=self.settings,
            client=self.client,
        )

    async def send_with_details(self, deal: ArbitrageDeal) -> tuple[bool, str]:
        return await send_telegram_deal_notification_with_result(
            deal=deal,
            settings=self.settings,
            client=self.client,
        )
