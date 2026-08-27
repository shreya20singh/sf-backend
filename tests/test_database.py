from sqlalchemy import inspect, select, text

from app.database import Base, SessionLocal, engine, init_db
from app.models import Address


def test_init_db_migrates_legacy_contacts_without_data_loss():
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE contacts (
                id INTEGER PRIMARY KEY,
                first_name VARCHAR(100) NOT NULL,
                last_name VARCHAR(100) NOT NULL,
                email VARCHAR(320) NOT NULL,
                phone VARCHAR(40),
                company VARCHAR(200),
                job_title VARCHAR(200),
                address VARCHAR(300),
                city VARCHAR(120),
                state VARCHAR(120),
                postal_code VARCHAR(20),
                country VARCHAR(120),
                notes TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.execute(
            text(
                """
                INSERT INTO contacts (
                    id, first_name, last_name, email, city, country, created_at, updated_at
                ) VALUES (
                    1, 'Ada', 'Lovelace', 'ada@example.com', 'San Francisco', 'USA',
                    :created_at, :updated_at
                )
                """
            ),
            {"created_at": "2026-08-26 00:00:00", "updated_at": "2026-08-26 00:00:00"},
        )

    init_db()
    init_db()

    columns = {column["name"] for column in inspect(engine).get_columns("contacts")}
    assert "photo" in columns
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT first_name, last_name, email, photo FROM contacts WHERE id = 1")
        ).one()
    assert tuple(row) == ("Ada", "Lovelace", "ada@example.com", None)

    with SessionLocal() as db:
        addresses = db.scalars(
            select(Address).where(Address.contact_id == 1)
        ).all()
    assert len(addresses) == 1
    assert addresses[0].type.value == "Other"
    assert addresses[0].address == "San Francisco"
    assert addresses[0].city == "San Francisco"
    assert addresses[0].country == "USA"
    assert addresses[0].position == 0
