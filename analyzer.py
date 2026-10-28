import hashlib
from typing import Any

from pydantic import BaseModel, Field


class ArbitrageDeal(BaseModel):
    """Pydantic-модель арбитражной сделки по предмету."""

    id: str | None = Field(default=None, description="Уникальный идентификатор сделки")
    item_name: str = Field(..., description="Название предмета")
    buy_price: float = Field(..., gt=0, description="Цена покупки в рублях")
    median_price: float = Field(..., gt=0, description="Медианная цена продажи в рублях")
    net_profit: float = Field(..., description="Чистая прибыль в рублях после вычета комиссии")
    profit_percent: float = Field(
        ...,
        description="Процент чистой выгоды относительно цены покупки",
    )
    link: str = Field(..., description="Прямая ссылка на лот или предмет")

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            raw = f"{self.item_name}_{self.buy_price:.2f}_{self.median_price:.2f}"
            self.id = hashlib.md5(raw.encode()).hexdigest()[:10]
            # Автоматически регистрируем в реестре
            DEAL_REGISTRY[self.id] = self


DEAL_REGISTRY: dict[str, ArbitrageDeal] = {}


def register_deal(deal: ArbitrageDeal) -> ArbitrageDeal:
    """Регистрирует сделку в глобальном хранилище активных предложений."""
    if deal.id:
        DEAL_REGISTRY[deal.id] = deal
    return deal


def get_deal_by_id(deal_id: str) -> ArbitrageDeal | None:
    """Возвращает зарегистрированную сделку по её идентификатору."""
    return DEAL_REGISTRY.get(deal_id)


def get_all_registered_deals() -> list[ArbitrageDeal]:
    """Возвращает все активные зарегистрированные лоты."""
    return list(DEAL_REGISTRY.values())


def calculate_profit(
    buy_price: float,
    median_price: float,
    fee_percent: float = 15.0,
) -> tuple[float, float]:
    """Рассчитывает чистую прибыль в рублях и процент маржи.

    Выплата продавцу = median_price * (1 - fee_percent / 100)
    Чистый профит = выплата - buy_price
    Процент выгоды = (чистый профит / buy_price) * 100

    :param buy_price: Цена приобретения лота (> 0).
    :param median_price: Ожидаемая цена продажи (медианная).
    :param fee_percent: Комиссия торговой площадки в процентах [0, 100).
    :return: Кортеж (чистая прибыль в рублях, процент маржи).
    """
    if buy_price <= 0:
        raise ValueError("Цена покупки должна быть строго больше нуля")
    if fee_percent < 0 or fee_percent >= 100:
        raise ValueError("Комиссия площадки должна находиться в диапазоне [0, 100)")

    payout = median_price * (1.0 - (fee_percent / 100.0))
    net_profit = round(payout - buy_price, 2)
    profit_percent = round((net_profit / buy_price) * 100.0, 2)
    return net_profit, profit_percent


def is_liquid(sales_last_24h: int, min_sales: int = 15) -> bool:
    """Фильтр ликвидности: проверяет, достаточно ли сделок совершено за последние 24 часа.

    :param sales_last_24h: Количество продаж предмета за последние 24 часа.
    :param min_sales: Минимальный порог продаж (по умолчанию 15).
    :return: True, если предмет ликвиден, иначе False.
    """
    return sales_last_24h >= min_sales
