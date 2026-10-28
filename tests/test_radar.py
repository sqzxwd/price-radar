import httpx
import pytest
from pydantic import ValidationError

from analyzer import ArbitrageDeal, calculate_profit, is_liquid
from config import Settings
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
            buy_price=-100.0,  # buy_price должен быть > 0
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
    assert "https://market.csgo.com/item?id=42&amp;ref=radar" in card_html


@pytest.mark.asyncio
async def test_notifier_safe_on_missing_token_or_chat_id(caplog: pytest.LogCaptureFixture) -> None:
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
    assert "Telegram bot token or chat ID is missing" in caplog.text


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
async def test_notifier_mock_sending_http_error(caplog: pytest.LogCaptureFixture) -> None:
    """Проверка устойчивости к сетевой ошибке/500 ошибке Telegram API (логирование без краша)."""
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
        with caplog.at_level("ERROR"):
            result = await send_telegram_deal_notification(deal, settings=settings, client=client)

    assert result is False
    assert "HTTP error while sending Telegram notification" in caplog.text


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
            median_price=1500.0,  # 1500*0.85 = 1275 -> profit +275 (+27.5%)
            sales_last_24h=30,
            link="https://test.link/1",
        ),
        MarketListing(
            item_name="Liquid Unprofitable Skin",
            buy_price=1000.0,
            median_price=1100.0,  # 1100*0.85 = 935 -> profit -65 (-6.5%)
            sales_last_24h=50,
            link="https://test.link/2",
        ),
        MarketListing(
            item_name="Illiquid High Profit Skin",
            buy_price=1000.0,
            median_price=2000.0,  # 2000*0.85 = 1700 -> +70% profit, but only 3 sales!
            sales_last_24h=3,
            link="https://test.link/3",
        ),
        MarketListing(
            item_name="Liquid Below Threshold Skin",
            buy_price=1000.0,
            median_price=1300.0,  # 1300*0.85 = 1105 -> profit +105 (+10.5% < 15%)
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

    custom = Settings(
        min_profit_percent=20.0,
        market_fee_percent=13.0,
        check_interval_seconds=5.0,
        telegram_bot_token="bot123",
        telegram_chat_id="chat456",
    )
    assert custom.min_profit_percent == 20.0
    assert custom.market_fee_percent == 13.0
    assert custom.check_interval_seconds == 5.0
    assert custom.telegram_bot_token == "bot123"
    assert custom.telegram_chat_id == "chat456"


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
    assert "Не настроены TELEGRAM_BOT_TOKEN" in reason

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
        assert "message_id: 42" in reason


@pytest.mark.asyncio
async def test_run_test_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка функции run_test_alert."""
    from main import run_test_alert

    # Мокаем TelegramNotifier.send_with_details, чтобы тест был изолирован
    async def mock_send_with_details(*args, **kwargs) -> tuple[bool, str]:
        return True, "Тестовое сообщение успешно отправлено (mock_id: 100)"

    monkeypatch.setattr(TelegramNotifier, "send_with_details", mock_send_with_details)
    result = await run_test_alert()
    assert result is True
