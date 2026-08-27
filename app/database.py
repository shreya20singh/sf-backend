from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _engine_kwargs(database_url: str) -> dict:
    if not database_url.startswith("sqlite"):
        return {}

    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if ":memory:" in database_url or "mode=memory" in database_url:
        # A plain in-memory SQLite database lives and dies with its connection.
        # StaticPool keeps a single connection alive so every request — and every
        # thread FastAPI hands work to — sees the same data for the process's lifetime.
        kwargs["poolclass"] = StaticPool
    return kwargs


settings = get_settings()

engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    **_engine_kwargs(settings.database_url),
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db() -> None:
    """Create tables. Called on startup; safe to call repeatedly."""
    from app import models  # noqa: F401  (register models on Base.metadata)

    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        _run_migrations(connection)


def _run_migrations(connection: Connection) -> None:
    """Apply idempotent schema changes that create_all cannot make."""
    columns = {column["name"] for column in inspect(connection).get_columns("contacts")}
    if "photo" not in columns:
        connection.execute(text("ALTER TABLE contacts ADD COLUMN photo TEXT"))
    _migrate_legacy_addresses(connection, columns)


def _migrate_legacy_addresses(connection: Connection, contact_columns: set[str]) -> None:
    """Move legacy inline address fields into ordered child rows once."""
    legacy_columns = tuple(
        column
        for column in ("address", "city", "state", "postal_code", "country")
        if column in contact_columns
    )
    if not legacy_columns or not inspect(connection).has_table("addresses"):
        return

    selected_columns = ", ".join(("id", *legacy_columns))
    rows = connection.execute(text(f"SELECT {selected_columns} FROM contacts")).mappings().all()
    clear_columns = ", ".join(f"{column} = NULL" for column in legacy_columns)
    insert_address = text(
        """
        INSERT INTO addresses (
            contact_id, type, address, city, state, postal_code, country, position
        ) VALUES (
            :contact_id, :address_type, :address, :city, :state, :postal_code, :country,
            COALESCE(
                (SELECT MAX(position) + 1 FROM addresses WHERE contact_id = :contact_id),
                0
            )
        )
        """
    )
    clear_legacy = text(f"UPDATE contacts SET {clear_columns} WHERE id = :contact_id")

    for row in rows:
        values = {
            column: _clean_legacy_value(row.get(column))
            for column in legacy_columns
        }
        if not any(values.values()):
            continue

        address = values.get("address") or next(
            (
                values[column]
                for column in ("city", "state", "postal_code", "country")
                if values.get(column)
            ),
            "Legacy address",
        )
        connection.execute(
            insert_address,
            {
                "contact_id": row["id"],
                "address_type": "Other",
                "address": address,
                "city": values.get("city"),
                "state": values.get("state"),
                "postal_code": values.get("postal_code"),
                "country": values.get("country"),
            },
        )
        connection.execute(clear_legacy, {"contact_id": row["id"]})


def _clean_legacy_value(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session that is always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
