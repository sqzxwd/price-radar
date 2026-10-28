import asyncio
import sqlite3
from collections.abc import Sequence
from pathlib import Path

DEFAULT_DB_PATH = "subscribers.db"


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Инициализирует таблицу подписчиков в базе данных SQLite (идемпотентно)."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                is_active BOOLEAN DEFAULT 1,
                min_profit_percent REAL DEFAULT 15.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Обратная совместимость, если таблица была создана ранее без min_profit_percent
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(subscribers)")
        columns = [col[1] for col in cursor.fetchall()]
        if "min_profit_percent" not in columns:
            conn.execute("ALTER TABLE subscribers ADD COLUMN min_profit_percent REAL DEFAULT 15.0")
        conn.commit()


def add_subscriber(
    chat_id: int,
    db_path: str = DEFAULT_DB_PATH,
    min_profit_percent: float = 15.0,
) -> None:
    """Добавляет нового подписчика или повторно активирует существующего."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO subscribers (chat_id, is_active, min_profit_percent, created_at)
            VALUES (?, 1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET is_active = 1
            """,
            (int(chat_id), float(min_profit_percent)),
        )
        conn.commit()


def remove_subscriber(chat_id: int, db_path: str = DEFAULT_DB_PATH) -> None:
    """Деактивирует подписку пользователя (is_active = 0)."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE subscribers SET is_active = 0 WHERE chat_id = ?",
            (int(chat_id),),
        )
        conn.commit()


def get_active_subscribers(db_path: str = DEFAULT_DB_PATH) -> list[int]:
    """Возвращает список ID всех активных подписчиков."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id FROM subscribers WHERE is_active = 1 ORDER BY chat_id")
        rows: Sequence[tuple[int]] = cursor.fetchall()
        return [row[0] for row in rows]


def set_subscriber_threshold(
    chat_id: int,
    threshold: float,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Устанавливает индивидуальный порог прибыли для пользователя."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO subscribers (chat_id, is_active, min_profit_percent, created_at)
            VALUES (?, 1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                min_profit_percent = excluded.min_profit_percent,
                is_active = 1
            """,
            (int(chat_id), float(threshold)),
        )
        conn.commit()


def get_subscriber_threshold(chat_id: int, db_path: str = DEFAULT_DB_PATH) -> float:
    """Возвращает персональный порог прибыли пользователя (по умолчанию 15.0)."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT min_profit_percent FROM subscribers WHERE chat_id = ?",
            (int(chat_id),),
        )
        row = cursor.fetchone()
        if row and row[0] is not None:
            return float(row[0])
        return 15.0


async def async_init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Асинхронная инициализация БД."""
    await asyncio.to_thread(init_db, db_path)


async def async_add_subscriber(
    chat_id: int,
    db_path: str = DEFAULT_DB_PATH,
    min_profit_percent: float = 15.0,
) -> None:
    """Асинхронное добавление подписчика."""
    await asyncio.to_thread(add_subscriber, chat_id, db_path, min_profit_percent)


async def async_remove_subscriber(chat_id: int, db_path: str = DEFAULT_DB_PATH) -> None:
    """Асинхронное удаление/деактивация подписчика."""
    await asyncio.to_thread(remove_subscriber, chat_id, db_path)


async def async_get_active_subscribers(db_path: str = DEFAULT_DB_PATH) -> list[int]:
    """Асинхронное получение списка активных подписчиков."""
    return await asyncio.to_thread(get_active_subscribers, db_path)


async def async_set_subscriber_threshold(
    chat_id: int,
    threshold: float,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Асинхронное сохранение порога прибыли."""
    await asyncio.to_thread(set_subscriber_threshold, chat_id, threshold, db_path)


async def async_get_subscriber_threshold(
    chat_id: int,
    db_path: str = DEFAULT_DB_PATH,
) -> float:
    """Асинхронное чтение порога прибыли."""
    return await asyncio.to_thread(get_subscriber_threshold, chat_id, db_path)
