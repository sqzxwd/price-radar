from dataclasses import dataclass
from typing import Protocol

from analyzer import ArbitrageDeal, calculate_profit, is_liquid
from config import Settings, get_settings


@dataclass(slots=True, frozen=True)
class MarketListing:
    """Представление лота на торговой площадке."""

    item_name: str
    buy_price: float
    median_price: float
    sales_last_24h: int
    link: str


class MarketClientProtocol(Protocol):
    """Интерфейс клиента торговой площадки."""

    async def fetch_listings(self, tick: int | None = None) -> list[MarketListing]: ...

    async def fetch_item(self, item_name: str) -> MarketListing | None: ...


class MockMarketClient:
    """Асинхронный клиент торговой площадки с моковыми данными для оффлайн-тестирования."""

    def __init__(self, mock_items: list[MarketListing] | None = None) -> None:
        self._items = mock_items if mock_items is not None else self._get_default_mock_items()

    @staticmethod
    def _get_default_mock_items() -> list[MarketListing]:
        """Возвращает набор реалистичных тестовых скинов с разной ликвидностью и маржой."""
        return [
            # 1. Ликвидный и высокоприбыльный лот:
            # median=1800, payout=1800*0.85=1530, buy=1200, profit=330 (+27.50%), sales=55
            MarketListing(
                item_name="AK-47 | Redline (Field-Tested)",
                buy_price=1200.0,
                median_price=1800.0,
                sales_last_24h=55,
                link="https://market.csgo.com/item/ak-47-redline-ft",
            ),
            # 2. Ликвидный и умеренно прибыльный лот:
            # median=12500, payout=12500*0.85=10625, buy=9000, profit=1625 (+18.06%), sales=28
            MarketListing(
                item_name="AWP | Asiimov (Field-Tested)",
                buy_price=9000.0,
                median_price=12500.0,
                sales_last_24h=28,
                link="https://market.csgo.com/item/awp-asiimov-ft",
            ),
            # 3. Ликвидный, но невыгодный лот (убыток после комиссии 15%):
            # median=1500, payout=1500*0.85=1275, buy=1400, profit=-125 (-8.93%), sales=40
            MarketListing(
                item_name="M4A4 | The Emperor (Field-Tested)",
                buy_price=1400.0,
                median_price=1500.0,
                sales_last_24h=40,
                link="https://market.csgo.com/item/m4a4-the-emperor-ft",
            ),
            # 4. Высокая теоретическая маржа, но неликвиден (мало продаж за сутки):
            # median=10000, payout=8500, buy=5000, profit=3500, sales=4 (<15)
            MarketListing(
                item_name="Desert Eagle | Printstream (Factory New)",
                buy_price=5000.0,
                median_price=10000.0,
                sales_last_24h=4,
                link="https://market.csgo.com/item/deagle-printstream-fn",
            ),
            # 5. Ликвидный лот с прибылью ниже порога 15%:
            # median=3000, payout=2550, buy=2400, profit=150 (+6.25%), sales=20
            MarketListing(
                item_name="USP-S | Printstream (Field-Tested)",
                buy_price=2400.0,
                median_price=3000.0,
                sales_last_24h=20,
                link="https://market.csgo.com/item/usps-printstream-ft",
            ),
            # 6. Ликвидный и выгодный ножевой лот:
            # median=26000, payout=22100, buy=18000, profit=4100 (+22.78%), sales=16
            MarketListing(
                item_name="★ Gut Knife | Doppler (Factory New)",
                buy_price=18000.0,
                median_price=26000.0,
                sales_last_24h=16,
                link="https://market.csgo.com/item/gut-knife-doppler-fn",
            ),
            # 7. Ликвидный лот около порога (10-14%):
            # median=1000, payout=850, buy=750, profit=100 (+13.33%), sales=25
            MarketListing(
                item_name="Glock-18 | Water Elemental (Field-Tested)",
                buy_price=750.0,
                median_price=1000.0,
                sales_last_24h=25,
                link="https://market.csgo.com/item/glock-18-water-elemental-ft",
            ),
        ]

    async def fetch_listings(self, tick: int | None = None) -> list[MarketListing]:
        """Асинхронно возвращает список лотов (с поддержкой микро-колебаний цен при tick)."""
        if tick is None or tick == 0:
            return list(self._items)

        # Небольшая симуляция рыночной динамики для живого монитора
        fluctuated: list[MarketListing] = []
        for i, item in enumerate(self._items):
            delta = ((tick + i * 3) % 7 - 3) * 5.0
            new_buy_price = max(10.0, round(item.buy_price + delta, 2))
            fluctuated.append(
                MarketListing(
                    item_name=item.item_name,
                    buy_price=new_buy_price,
                    median_price=item.median_price,
                    sales_last_24h=item.sales_last_24h,
                    link=item.link,
                )
            )
        return fluctuated

    async def fetch_item(self, item_name: str) -> MarketListing | None:
        """Поиск конкретного предмета по названию."""
        for item in self._items:
            if item.item_name == item_name:
                return item
        return None

    async def scan_profitable_deals(
        self,
        settings: Settings | None = None,
        tick: int | None = None,
    ) -> list[ArbitrageDeal]:
        """Сканирует лоты, проверяет ликвидность и маржинальность.

        Возвращает только те лоты, которые проходят фильтр ликвидности
        и имеют чистую прибыль не ниже min_profit_percent.
        """
        cfg = settings or get_settings()
        deals: list[ArbitrageDeal] = []
        listings = await self.fetch_listings(tick=tick)

        for item in listings:
            if not is_liquid(item.sales_last_24h):
                continue

            net_profit, profit_percent = calculate_profit(
                buy_price=item.buy_price,
                median_price=item.median_price,
                fee_percent=cfg.market_fee_percent,
            )

            if profit_percent >= cfg.min_profit_percent:
                deals.append(
                    ArbitrageDeal(
                        item_name=item.item_name,
                        buy_price=item.buy_price,
                        median_price=item.median_price,
                        net_profit=net_profit,
                        profit_percent=profit_percent,
                        link=item.link,
                    )
                )

        return deals
