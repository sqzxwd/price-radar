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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def add_subscriber(chat_id: int, db_path: str = DEFAULT_DB_PATH) -> None:
    """Добавляет нового подписчика или повторно активирует существующего."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO subscribers (chat_id, is_active, created_at)
            VALUES (?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET is_active = 1
            """,
            (int(chat_id),),
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


async def async_init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Асинхронная инициализация БД."""
    await asyncio.to_thread(init_db, db_path)


async def async_add_subscriber(chat_id: int, db_path: str = DEFAULT_DB_PATH) -> None:
    """Асинхронное добавление подписчика."""
    await asyncio.to_thread(add_subscriber, chat_id, db_path)


async def async_remove_subscriber(chat_id: int, db_path: str = DEFAULT_DB_PATH) -> None:
    """Асинхронное удаление/деактивация подписчика."""
    await asyncio.to_thread(remove_subscriber, chat_id, db_path)


async def async_get_active_subscribers(db_path: str = DEFAULT_DB_PATH) -> list[int]:
    """Асинхронное получение списка активных подписчиков."""
    return await asyncio.to_thread(get_active_subscribers, db_path)
