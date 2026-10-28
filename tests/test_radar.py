import httpx
import pytest
from pydantic import ValidationError

from analyzer import ArbitrageDeal, calculate_profit, is_liquid
from config import Settings
from database import (
    add_subscriber,
    async_add_subscriber,
    async_get_active_subscribers,
    async_remove_subscriber,
    get_active_subscribers,
    init_db,
    remove_subscriber,
)
from market_client import MarketListing, MockMarketClient
from notifier import TelegramNotifier, format_deal_card, send_telegram_deal_notification


def test_calculate_profit_exact_with_standard_fee() -> None:
    """Точный расчет профита с комиссией 15%:

    Покупка 1000, медиана 1400.
    Выплата = 1400 * 0.85 = 1190.
    Чистый профит = 1190 - 1000 = 190.0 руб.
    Маржа = (190 / 1000) * 100 = 19.0%.
    """
    net_profit, profit_percent = calculate_profit(buy_price=1000.0, median_price=1400.0)
    assert net_profit == 190.0
    assert profit_percent == 19.0


def test_calculate_profit_custom_fee() -> None:
    """Расчет профита с нестандартной комиссией (например, 5%)."""
    # 2000 * 0.95 = 1900. Профит: 1900 - 1500 = 400. Маржа: (400 / 1500) * 100 = 26.67%
    net_profit, profit_percent = calculate_profit(
        buy_price=1500.0,
        median_price=2000.0,
        fee_percent=5.0,
    )
    assert net_profit == 400.0
    assert profit_percent == 26.67


def test_calculate_profit_unprofitable_deal() -> None:
    """Отсечение убыточных лотов: когда после комиссии сделка уходит в минус."""
    # Покупка 1000, медиана 1000. Выплата 850. Профит -150. Маржа -15%
    net_profit, profit_percent = calculate_profit(buy_price=1000.0, median_price=1000.0)
    assert net_profit == -150.0
    assert profit_percent == -15.0


def test_calculate_profit_invalid_inputs() -> None:
    """Проверка валидации входных параметров для calculate_profit."""
    with pytest.raises(ValueError, match="Цена покупки должна быть строго больше нуля"):
        calculate_profit(buy_price=0.0, median_price=100.0)

    with pytest.raises(ValueError, match="Цена покупки должна быть строго больше нуля"):
        calculate_profit(buy_price=-50.0, median_price=100.0)

    with pytest.raises(ValueError, match="Комиссия площадки должна находиться в диапазоне"):
        calculate_profit(buy_price=100.0, median_price=200.0, fee_percent=100.0)

    with pytest.raises(ValueError, match="Комиссия площадки должна находиться в диапазоне"):
        calculate_profit(buy_price=100.0, median_price=200.0, fee_percent=-5.0)


def test_is_liquid_default_and_custom_threshold() -> None:
    """Проверка ликвидности по умолчанию (15) и с кастомным порогом."""
    assert is_liquid(15) is True
    assert is_liquid(50) is True
    assert is_liquid(14) is False
    assert is_liquid(0) is False

    # С кастомным порогом
    assert is_liquid(5, min_sales=5) is True
    assert is_liquid(4, min_sales=5) is False


def test_arbitrage_deal_pydantic_model() -> None:
    """Валидация pydantic-модели ArbitrageDeal."""
    deal = ArbitrageDeal(
        item_name="AK-47 | Redline (FT)",
        buy_price=1200.0,
        median_price=1800.0,
        net_profit=330.0,
        profit_percent=27.5,
        link="https://market.csgo.com/item/1",
    )
    assert deal.item_name == "AK-47 | Redline (FT)"
    assert deal.buy_price == 1200.0
    assert deal.net_profit == 330.0

    # Проверка отклонения некорректных значений
    with pytest.raises(ValidationError):
        ArbitrageDeal(
            item_name="Invalid Item",
            buy_price=-100.0,
            median_price=500.0,
            net_profit=50.0,
            profit_percent=10.0,
            link="https://test.com",
        )


def test_format_deal_card() -> None:
    """Форматирование карточки лота с HTML-экранированием."""
    deal = ArbitrageDeal(
        item_name="M4A1-S | <Printstream> & Co",
        buy_price=5000.0,
        median_price=7000.0,
        net_profit=950.0,
        profit_percent=19.0,
        link="https://market.csgo.com/item?id=42&ref=radar",
    )
    card_html = format_deal_card(deal)
    assert "&lt;Printstream&gt; &amp; Co" in card_html
    assert "5000.00 ₽" in card_html
    assert "+950.00 ₽" in card_html
    assert "+19.00%" in card_html
    assert 'href="https://market.csgo.com/item?id=42&amp;ref=radar"' in card_html


@pytest.mark.asyncio
async def test_notifier_safe_on_missing_token(caplog: pytest.LogCaptureFixture) -> None:
    """Безопасная обработка отсутствия токена без падения воркера."""
    deal = ArbitrageDeal(
        item_name="AWP | Safari Mesh",
        buy_price=100.0,
        median_price=200.0,
        net_profit=70.0,
        profit_percent=70.0,
        link="https://market.csgo.com/item/2",
    )

    settings = Settings(telegram_bot_token=None, telegram_chat_id=None)
    with caplog.at_level("WARNING"):
        success = await send_telegram_deal_notification(deal=deal, settings=settings)

    assert success is False
    assert "Telegram bot token is missing" in caplog.text


@pytest.mark.asyncio
async def test_notifier_safe_on_empty_subscribers(caplog: pytest.LogCaptureFixture) -> None:
    """Безопасная обработка пустого списка получателей/подписчиков."""
    deal = ArbitrageDeal(
        item_name="AWP | Safari Mesh",
        buy_price=100.0,
        median_price=200.0,
        net_profit=70.0,
        profit_percent=70.0,
        link="https://market.csgo.com/item/2",
    )

    settings = Settings(telegram_bot_token="test_token", telegram_chat_id=None)
    with caplog.at_level("INFO"):
        success = await send_telegram_deal_notification(
            deal=deal,
            chat_ids=[],
            settings=settings,
        )

    assert success is False
    assert "No active Telegram subscribers found" in caplog.text


@pytest.mark.asyncio
async def test_notifier_mock_sending_success() -> None:
    """Мок-отправка в Telegram через httpx MockTransport с успешным кодом 200."""
    deal = ArbitrageDeal(
        item_name="Desert Eagle | Blaze",
        buy_price=40000.0,
        median_price=55000.0,
        net_profit=6750.0,
        profit_percent=16.88,
        link="https://market.csgo.com/item/3",
    )

    settings = Settings(
        telegram_bot_token="test_mock_token_12345",
        telegram_chat_id="123456789",
    )

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL(
            "https://api.telegram.org/bottest_mock_token_12345/sendMessage"
        )
        assert b"Desert Eagle | Blaze" in request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 999}})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        notifier = TelegramNotifier(settings=settings, client=client)
        result = await notifier.send(deal)

    assert result is True


@pytest.mark.asyncio
async def test_notifier_mock_broadcast_multiple_subscribers() -> None:
    """Мок-рассылка нескольким подписчикам одновременно."""
    deal = ArbitrageDeal(
        item_name="AK-47 | Vulcan",
        buy_price=15000.0,
        median_price=22000.0,
        net_profit=3700.0,
        profit_percent=24.67,
        link="https://market.csgo.com/item/vulcan",
    )

    sent_chat_ids: list[int] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        sent_chat_ids.append(body["chat_id"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 777}})

    settings = Settings(telegram_bot_token="test_token")
    recipients = [111, 222, 333]

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        notifier = TelegramNotifier(settings=settings, client=client)
        success, details = await notifier.send_with_details(deal, chat_ids=recipients)

    assert success is True
    assert "Успешно доставлено 3/3" in details
    assert sent_chat_ids == [111, 222, 333]


@pytest.mark.asyncio
async def test_notifier_mock_sending_http_error(caplog: pytest.LogCaptureFixture) -> None:
    """Проверка устойчивости к ошибке Telegram API (логирование без краша)."""
    deal = ArbitrageDeal(
        item_name="Glock-18 | Fade",
        buy_price=90000.0,
        median_price=120000.0,
        net_profit=12000.0,
        profit_percent=13.33,
        link="https://market.csgo.com/item/4",
    )

    settings = Settings(
        telegram_bot_token="mock_token",
        telegram_chat_id="12345",
    )

    def failing_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"ok": False, "description": "Internal Server Error"})

    transport = httpx.MockTransport(failing_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with caplog.at_level("WARNING"):
            result = await send_telegram_deal_notification(deal, settings=settings, client=client)

    assert result is False


@pytest.mark.asyncio
async def test_market_client_scan_filters_unprofitable_and_illiquid() -> None:
    """Тестирование сканирования мокового клиента:

    - Отсекаются неликвидные лоты (< 15 продаж)
    - Отсекаются лоты с профитом < min_profit_percent (15.0%)
    - Возвращаются только целевые прибыльные сделки.
    """
    custom_items = [
        MarketListing(
            item_name="Liquid Profitable Skin",
            buy_price=1000.0,
            median_price=1500.0,
            sales_last_24h=30,
            link="https://test.link/1",
        ),
        MarketListing(
            item_name="Liquid Unprofitable Skin",
            buy_price=1000.0,
            median_price=1100.0,
            sales_last_24h=50,
            link="https://test.link/2",
        ),
        MarketListing(
            item_name="Illiquid High Profit Skin",
            buy_price=1000.0,
            median_price=2000.0,
            sales_last_24h=3,
            link="https://test.link/3",
        ),
        MarketListing(
            item_name="Liquid Below Threshold Skin",
            buy_price=1000.0,
            median_price=1300.0,
            sales_last_24h=20,
            link="https://test.link/4",
        ),
    ]

    client = MockMarketClient(mock_items=custom_items)
    settings = Settings(min_profit_percent=15.0, market_fee_percent=15.0)

    deals = await client.scan_profitable_deals(settings)

    assert len(deals) == 1
    assert deals[0].item_name == "Liquid Profitable Skin"
    assert deals[0].profit_percent == 27.5
    assert deals[0].net_profit == 275.0


def test_settings_default_values_and_override() -> None:
    """Проверка дефолтных настроек конфигурации и переопределения."""
    default_settings = Settings(_env_file=None)
    assert default_settings.min_profit_percent == 15.0
    assert default_settings.market_fee_percent == 15.0
    assert default_settings.check_interval_seconds == 2.0
    assert default_settings.telegram_bot_token is None
    assert default_settings.telegram_chat_id is None
    assert default_settings.database_path == "subscribers.db"

    custom = Settings(
        min_profit_percent=20.0,
        market_fee_percent=13.0,
        check_interval_seconds=5.0,
        telegram_bot_token="bot123",
        telegram_chat_id="chat456",
        database_path="custom.db",
    )
    assert custom.min_profit_percent == 20.0
    assert custom.market_fee_percent == 13.0
    assert custom.check_interval_seconds == 5.0
    assert custom.telegram_bot_token == "bot123"
    assert custom.telegram_chat_id == "chat456"
    assert custom.database_path == "custom.db"


def test_database_subscribers_crud(tmp_path: pytest.TempPathFactory) -> None:
    """Тестирование CRUD операций с базой данных подписчиков."""
    db_file = str(tmp_path / "test_subscribers.db")

    init_db(db_file)
    assert get_active_subscribers(db_file) == []

    add_subscriber(1001, db_path=db_file)
    add_subscriber(1002, db_path=db_file)
    assert get_active_subscribers(db_file) == [1001, 1002]

    # Деактивация
    remove_subscriber(1001, db_path=db_file)
    assert get_active_subscribers(db_file) == [1002]

    # Повторная активация
    add_subscriber(1001, db_path=db_file)
    assert get_active_subscribers(db_file) == [1001, 1002]


@pytest.mark.asyncio
async def test_database_async_operations(tmp_path: pytest.TempPathFactory) -> None:
    """Тестирование асинхронных операций с БД."""
    db_file = str(tmp_path / "async_subscribers.db")

    await async_add_subscriber(555, db_path=db_file)
    subs = await async_get_active_subscribers(db_file)
    assert subs == [555]

    await async_remove_subscriber(555, db_path=db_file)
    subs = await async_get_active_subscribers(db_file)
    assert subs == []


@pytest.mark.asyncio
async def test_dashboard_components_and_run_once() -> None:
    """Проверка сборки дашборда и разового запуска run_dashboard(once=True)."""
    from main import build_dashboard, run_dashboard

    settings = Settings()
    client = MockMarketClient()
    listings = await client.fetch_listings()

    dashboard = build_dashboard(
        listings=listings,
        cycles_count=1,
        deals_found=3,
        alerts_sent=0,
        last_check_time="12:00:00",
        settings=settings,
    )
    assert dashboard is not None

    # Проверка выполнения без ошибок в режиме once
    await run_dashboard(once=True)


@pytest.mark.asyncio
async def test_notifier_send_with_details_methods() -> None:
    """Проверка send_with_details при успехе и ошибках."""
    deal = ArbitrageDeal(
        item_name="Test AK",
        buy_price=100.0,
        median_price=200.0,
        net_profit=70.0,
        profit_percent=70.0,
        link="https://test.link",
    )

    # 1. Отсутствие токена
    empty_settings = Settings(_env_file=None)
    notifier_empty = TelegramNotifier(settings=empty_settings)
    success, reason = await notifier_empty.send_with_details(deal)
    assert success is False
    assert "Не настроен TELEGRAM_BOT_TOKEN" in reason

    # 2. Успешный мок
    valid_settings = Settings(
        telegram_bot_token="test_token",
        telegram_chat_id="12345",
    )

    def ok_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    transport = httpx.MockTransport(ok_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        notifier_ok = TelegramNotifier(settings=valid_settings, client=client)
        success, reason = await notifier_ok.send_with_details(deal)
        assert success is True
        assert "Успешно доставлено 1/1" in reason


@pytest.mark.asyncio
async def test_run_test_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка функции run_test_alert."""
    from main import run_test_alert

    async def mock_send_with_details(*args, **kwargs) -> tuple[bool, str]:
        return True, "Тестовое сообщение успешно отправлено (mock_id: 100)"

    monkeypatch.setattr(TelegramNotifier, "send_with_details", mock_send_with_details)
    result = await run_test_alert()
    assert result is True


def test_build_deal_inline_keyboard() -> None:
    """Проверка генерации inline-кнопок под карточкой лота."""
    from notifier import build_deal_inline_keyboard

    deal = ArbitrageDeal(
        item_name="AWP | Hyper Beast",
        buy_price=3000.0,
        median_price=4500.0,
        net_profit=825.0,
        profit_percent=27.50,
        link="https://market.csgo.com/item/hyper-beast",
    )
    kb = build_deal_inline_keyboard(deal)
    assert len(kb.inline_keyboard) == 2

    # Ряд 1: Ссылка на маркет
    assert kb.inline_keyboard[0][0].text == "🔗 Открыть лот на маркете"
    assert kb.inline_keyboard[0][0].url == "https://market.csgo.com/item/hyper-beast"

    # Ряд 2: Напомнить и Детали расчета
    assert kb.inline_keyboard[1][0].text == "⏱ Напомнить через 5 мин"
    assert kb.inline_keyboard[1][0].callback_data == f"remind:{deal.id}"
    assert kb.inline_keyboard[1][1].text == "📊 Детали расчета"
    assert kb.inline_keyboard[1][1].callback_data == f"calc:{deal.id}"


def test_build_settings_keyboard() -> None:
    """Проверка генерации клавиатуры /settings с отметкой активного порога."""
    from bot import build_settings_keyboard

    kb = build_settings_keyboard(current_threshold=20.0)
    buttons = kb.inline_keyboard[0]
    texts = [btn.text for btn in buttons]
    callbacks = [btn.callback_data for btn in buttons]

    assert texts == ["10%", "15%", "✔ 20%", "25%"]
    assert callbacks == [
        "set_threshold:10.0",
        "set_threshold:15.0",
        "set_threshold:20.0",
        "set_threshold:25.0",
    ]


def test_build_deal_breakdown_text() -> None:
    """Проверка текста детальной разбивки расчета."""
    from bot import build_deal_breakdown_text

    deal = ArbitrageDeal(
        item_name="AK-47 | Redline",
        buy_price=1000.0,
        median_price=1500.0,
        net_profit=275.0,
        profit_percent=27.50,
        link="https://test.link",
    )
    text = build_deal_breakdown_text(deal, fee_percent=15.0)
    assert "AK-47 | Redline" in text
    assert "Цена закупки: 1000.00 ₽" in text
    assert "Медианная цена (24ч): 1500.00 ₽" in text
    assert "Комиссия площадки (15.0%): -225.00 ₽" in text
    assert "Выплата продавцу: 1275.00 ₽" in text
    assert "Чистая маржа: +275.00 ₽ (+27.50%)" in text


def test_database_subscriber_threshold(tmp_path: pytest.TempPathFactory) -> None:
    """Проверка сохранения и чтения персонального порога прибыли."""
    from database import get_subscriber_threshold, set_subscriber_threshold

    db_file = str(tmp_path / "thresholds.db")
    assert get_subscriber_threshold(12345, db_path=db_file) == 15.0

    set_subscriber_threshold(12345, 25.0, db_path=db_file)
    assert get_subscriber_threshold(12345, db_path=db_file) == 25.0


@pytest.mark.asyncio
async def test_bot_callback_calc_and_remind() -> None:
    """Тестирование обработки callback-запросов calc: и remind:."""
    from unittest.mock import AsyncMock, MagicMock

    from bot import cb_calc, cb_remind

    deal = ArbitrageDeal(
        item_name="Test Knife",
        buy_price=10000.0,
        median_price=14000.0,
        net_profit=1900.0,
        profit_percent=19.0,
        link="https://test.link",
    )

    # 1. Тест calc:
    cb_calc_query = MagicMock()
    cb_calc_query.data = f"calc:{deal.id}"
    cb_calc_query.answer = AsyncMock()

    await cb_calc(cb_calc_query)
    cb_calc_query.answer.assert_awaited_once()
    assert cb_calc_query.answer.call_args[1].get("show_alert") is True
    assert "Test Knife" in cb_calc_query.answer.call_args[0][0]

    # 2. Тест remind:
    cb_remind_query = MagicMock()
    cb_remind_query.data = f"remind:{deal.id}"
    cb_remind_query.message = MagicMock()
    cb_remind_query.message.chat.id = 999
    cb_remind_query.answer = AsyncMock()

    bot_mock = MagicMock()
    await cb_remind(cb_remind_query, bot=bot_mock)
    cb_remind_query.answer.assert_awaited_once_with(
        "Таймер на 5 минут установлен!",
        show_alert=False,
    )


@pytest.mark.asyncio
async def test_schedule_deal_reminder() -> None:
    """Проверка функции отложенного напоминания с моковым клиентом."""
    from unittest.mock import AsyncMock, MagicMock

    from bot import schedule_deal_reminder

    deal = ArbitrageDeal(
        item_name="AK-47 | Redline (Field-Tested)",
        buy_price=1200.0,
        median_price=1800.0,
        net_profit=330.0,
        profit_percent=27.50,
        link="https://market.csgo.com/item/ak-47-redline-ft",
    )

    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()

    success = await schedule_deal_reminder(
        bot=bot_mock,
        chat_id=12345,
        deal=deal,
        delay_seconds=0,  # без задержки для мгновенного теста
    )
    assert success is True
    bot_mock.send_message.assert_awaited_once()
    assert "Напоминание: лот все еще в продаже!" in bot_mock.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_bot_commands_active_and_settings() -> None:
    """Проверка команд /active и /settings."""
    from unittest.mock import AsyncMock, MagicMock

    from bot import cmd_active, cmd_settings

    # /active
    msg_active = MagicMock()
    msg_active.answer = AsyncMock()
    await cmd_active(msg_active)
    msg_active.answer.assert_awaited_once()
    call_text = msg_active.answer.call_args[0][0]
    assert "Актуальные выгодные лоты" in call_text

    # /settings
    msg_settings = MagicMock()
    msg_settings.chat.id = 777
    msg_settings.answer = AsyncMock()
    await cmd_settings(msg_settings)
    msg_settings.answer.assert_awaited_once()
    assert "Настройки уведомлений" in msg_settings.answer.call_args[0][0]
