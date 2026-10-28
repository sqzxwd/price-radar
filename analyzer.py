from pydantic import BaseModel, Field


class ArbitrageDeal(BaseModel):
    """Pydantic-модель арбитражной сделки по предмету."""

    item_name: str = Field(..., description="Название предмета")
    buy_price: float = Field(..., gt=0, description="Цена покупки в рублях")
    median_price: float = Field(..., gt=0, description="Медианная цена продажи в рублях")
    net_profit: float = Field(..., description="Чистая прибыль в рублях после вычета комиссии")
    profit_percent: float = Field(
        ...,
        description="Процент чистой выгоды относительно цены покупки",
    )
    link: str = Field(..., description="Прямая ссылка на лот или предмет")


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
