import argparse
import asyncio
import logging
from datetime import datetime

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from analyzer import ArbitrageDeal, calculate_profit, is_liquid
from config import Settings, get_settings
from database import add_subscriber, get_active_subscribers
from market_client import MarketListing, MockMarketClient
from notifier import TelegramNotifier

logging.basicConfig(level=logging.ERROR)
console = Console()


def create_metrics_panel(
    cycles_count: int,
    deals_found: int,
    alerts_sent: int,
    last_check_time: str,
    settings: Settings,
) -> Panel:
    """Формирует панель ключевых метрик работы радара."""
    grid = Table.grid(expand=True)
    grid.add_column(justify="center", ratio=1)
    grid.add_column(justify="center", ratio=1)
    grid.add_column(justify="center", ratio=1)
    grid.add_column(justify="center", ratio=1)

    grid.add_row(
        f"[bold white]Циклы:[/bold white] [bold cyan]{cycles_count}[/bold cyan]",
        f"[bold white]Выгодных сделок:[/bold white] [bold green]{deals_found}[/bold green]",
        f"[bold white]Отправлено алертов:[/bold white] [bold magenta]{alerts_sent}[/bold magenta]",
        f"[bold white]Время проверки:[/bold white] [yellow]{last_check_time}[/yellow]",
    )

    threshold_info = (
        f"Порог прибыли: [green]>={settings.min_profit_percent}%[/green] | "
        f"Комиссия: [yellow]{settings.market_fee_percent}%[/yellow] | "
        f"Интервал: [cyan]{settings.check_interval_seconds}s[/cyan]"
    )

    return Panel(
        grid,
        title="[bold cyan]📡 Price Radar — Live Market Monitor[/bold cyan]",
        subtitle=f"[dim]{threshold_info}[/dim]",
        border_style="cyan",
    )


def create_items_table(
    listings: list[MarketListing],
    settings: Settings,
) -> Table:
    """Формирует таблицу предметов с цветовой индикацией и ликвидностью."""
    table = Table(
        title="📋 Мониторинг лотов площадки",
        title_style="bold white",
        header_style="bold magenta",
        expand=True,
        border_style="blue",
    )

    table.add_column("Название", style="white", ratio=3)
    table.add_column("Текущая цена", justify="right", style="cyan", ratio=1)
    table.add_column("Медиана", justify="right", style="cyan", ratio=1)
    table.add_column("Чистый профит", justify="right", ratio=2)
    table.add_column("Статус ликвидности", justify="center", ratio=2)

    for item in listings:
        net_profit, profit_percent = calculate_profit(
            buy_price=item.buy_price,
            median_price=item.median_price,
            fee_percent=settings.market_fee_percent,
        )
        liquid = is_liquid(item.sales_last_24h)

        # Статус ликвидности
        if liquid:
            liquidity_status = f"[green]🟢 Ликвиден ({item.sales_last_24h}/24ч)[/green]"
        else:
            liquidity_status = f"[red]🔴 Неликвиден ({item.sales_last_24h}/24ч)[/red]"

        # Цветовая индикация
        sign = "+" if profit_percent > 0 else ""
        profit_str = f"{net_profit:+.2f} ₽ ({sign}{profit_percent:.2f}%)"

        if profit_percent >= settings.min_profit_percent:
            profit_cell = f"[bold green]{profit_str}[/bold green]"
            name_cell = f"[green]{item.item_name}[/green]"
        elif 10.0 <= profit_percent < settings.min_profit_percent:
            profit_cell = f"[bold yellow]{profit_str}[/bold yellow]"
            name_cell = f"[yellow]{item.item_name}[/yellow]"
        else:
            profit_cell = f"[dim]{profit_str}[/dim]"
            name_cell = f"[dim]{item.item_name}[/dim]"

        table.add_row(
            name_cell,
            f"{item.buy_price:.2f} ₽",
            f"{item.median_price:.2f} ₽",
            profit_cell,
            liquidity_status,
        )

    return table


def build_dashboard(
    listings: list[MarketListing],
    cycles_count: int,
    deals_found: int,
    alerts_sent: int,
    last_check_time: str,
    settings: Settings,
) -> Group:
    """Объединяет панель метрик и таблицу предметов в единую группу для отрисовки."""
    metrics_panel = create_metrics_panel(
        cycles_count=cycles_count,
        deals_found=deals_found,
        alerts_sent=alerts_sent,
        last_check_time=last_check_time,
        settings=settings,
    )
    items_table = create_items_table(listings=listings, settings=settings)
    return Group(metrics_panel, items_table)


async def run_dashboard(once: bool = False) -> None:
    """Основной цикл или разовый запуск монитора."""
    settings = get_settings()
    client = MockMarketClient()
    notifier = TelegramNotifier(settings=settings)

    cycles_count = 0
    total_deals_found = 0
    alerts_sent = 0
    notified_deals: set[str] = set()

    if once:
        cycles_count = 1
        listings = await client.fetch_listings()
        now_str = datetime.now().strftime("%H:%M:%S")

        current_profitable = 0
        for item in listings:
            if not is_liquid(item.sales_last_24h):
                continue
            net_profit, profit_percent = calculate_profit(
                buy_price=item.buy_price,
                median_price=item.median_price,
                fee_percent=settings.market_fee_percent,
            )
            if profit_percent >= settings.min_profit_percent:
                current_profitable += 1
                deal = ArbitrageDeal(
                    item_name=item.item_name,
                    buy_price=item.buy_price,
                    median_price=item.median_price,
                    net_profit=net_profit,
                    profit_percent=profit_percent,
                    link=item.link,
                )
                if await notifier.send(deal):
                    alerts_sent += 1

        total_deals_found = current_profitable
        dashboard = build_dashboard(
            listings=listings,
            cycles_count=cycles_count,
            deals_found=total_deals_found,
            alerts_sent=alerts_sent,
            last_check_time=now_str,
            settings=settings,
        )
        console.print(dashboard)
        return

    # Режим живого терминального монитора (rich.live.Live)
    listings = await client.fetch_listings()
    now_str = datetime.now().strftime("%H:%M:%S")
    dashboard = build_dashboard(
        listings=listings,
        cycles_count=0,
        deals_found=0,
        alerts_sent=0,
        last_check_time=now_str,
        settings=settings,
    )

    with Live(dashboard, console=console, refresh_per_second=4, screen=False) as live:
        while True:
            cycles_count += 1
            listings = await client.fetch_listings(tick=cycles_count)
            now_str = datetime.now().strftime("%H:%M:%S")

            for item in listings:
                if not is_liquid(item.sales_last_24h):
                    continue
                net_profit, profit_percent = calculate_profit(
                    buy_price=item.buy_price,
                    median_price=item.median_price,
                    fee_percent=settings.market_fee_percent,
                )
                if profit_percent >= settings.min_profit_percent:
                    total_deals_found += 1
                    if item.item_name not in notified_deals:
                        deal = ArbitrageDeal(
                            item_name=item.item_name,
                            buy_price=item.buy_price,
                            median_price=item.median_price,
                            net_profit=net_profit,
                            profit_percent=profit_percent,
                            link=item.link,
                        )
                        if await notifier.send(deal):
                            alerts_sent += 1
                        notified_deals.add(item.item_name)

            live.update(
                build_dashboard(
                    listings=listings,
                    cycles_count=cycles_count,
                    deals_found=total_deals_found,
                    alerts_sent=alerts_sent,
                    last_check_time=now_str,
                    settings=settings,
                )
            )
            await asyncio.sleep(settings.check_interval_seconds)


async def run_test_alert() -> bool:
    """Отправляет тестовую карточку в Telegram подписчикам и выводит статус в консоль."""
    settings = get_settings()
    console.print("[bold cyan]🔔 Проверка связи с Telegram...[/bold cyan]")

    masked_token = (
        f"{settings.telegram_bot_token[:10]}..."
        if settings.telegram_bot_token
        else "[red]не задан[/red]"
    )
    active_subs = get_active_subscribers(settings.database_path)
    subs_info = f"{len(active_subs)} чел." if active_subs else "[red]нет (база пуста)[/red]"

    console.print(f"• Токен бота: [yellow]{masked_token}[/yellow]")
    console.print(f"• Подписчиков в БД: [yellow]{subs_info}[/yellow]")
    if settings.telegram_chat_id:
        console.print(f"• Fallback Chat ID: [yellow]{settings.telegram_chat_id}[/yellow]")

    test_deal = ArbitrageDeal(
        item_name="AK-47 | Redline (Field-Tested) [TEST ALERT]",
        buy_price=1200.0,
        median_price=1800.0,
        net_profit=330.0,
        profit_percent=27.50,
        link="https://market.csgo.com/item/ak-47-redline-ft",
    )

    notifier = TelegramNotifier(settings=settings)
    success, reason = await notifier.send_with_details(test_deal)

    if success:
        console.print(f"[bold green]✔ УСПЕХ:[/bold green] {reason}")
    else:
        console.print(f"[bold red]✖ СТАТУС:[/bold red] {reason}")

    return success


def main() -> None:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description="Price Radar — живой терминальный монитор выгодных скинов"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Выполнить разовую проверку и вывести один снимок таблицы",
    )
    parser.add_argument(
        "--test-alert",
        action="store_true",
        help="Отправить тестовую карточку в Telegram для проверки связи и вывести статус",
    )
    parser.add_argument(
        "--add-subscriber",
        type=int,
        metavar="CHAT_ID",
        help="Добавить Chat ID подписчика в базу данных",
    )
    parser.add_argument(
        "--subscribers",
        action="store_true",
        help="Показать список всех активных подписчиков",
    )
    args = parser.parse_args()

    settings = get_settings()

    if args.add_subscriber:
        add_subscriber(args.add_subscriber, db_path=settings.database_path)
        msg = f"[bold green]✔ Подписчик {args.add_subscriber} успешно добавлен.[/bold green]"
        console.print(msg)
        return

    if args.subscribers:
        subs = get_active_subscribers(settings.database_path)
        console.print(f"[bold cyan]Список подписчиков ({len(subs)}):[/bold cyan] {subs}")
        return

    try:
        if args.test_alert:
            asyncio.run(run_test_alert())
        else:
            asyncio.run(run_dashboard(once=args.once))
    except KeyboardInterrupt, asyncio.CancelledError:
        console.print("\n[bold yellow]👋 Мониторинг остановлен пользователем.[/bold yellow]")


if __name__ == "__main__":
    main()
