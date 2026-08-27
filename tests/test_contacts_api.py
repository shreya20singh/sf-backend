import base64

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Address
from app.schemas import MAX_PHOTO_BYTES

BASE = "/api/v1/contacts"
PHOTO = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8AAAAMBAQDJ"
    "/pLvAAAAAElFTkSuQmCC"
)
TRUNCATED_WEBP = (
    "data:image/webp;base64,"
    + base64.b64encode(b"RIFF\x00\x00\x00\x00WEBP").decode()
)
MULTIPLE_ADDRESSES = [
    {
        "type": "Home",
        "address": "12 Home St",
        "city": "Oakland",
        "state": "CA",
        "postal_code": "94612",
        "country": "USA",
    },
    {
        "type": "Work",
        "address": "1 Market St",
        "city": "San Francisco",
        "state": "CA",
        "postal_code": "94105",
        "country": "USA",
    },
]


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "sqlite"


def test_create_contact(client, payload):
    response = client.post(BASE, json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["email"] == "ada@example.com"
    assert body["full_name"] == "Ada Lovelace"
    assert body["created_at"] and body["updated_at"]


def test_create_contact_with_photo(client, payload):
    response = client.post(BASE, json={**payload, "photo": PHOTO})
    assert response.status_code == 201
    contact_id = response.json()["id"]
    assert response.json()["photo"] == PHOTO
    assert client.get(f"{BASE}/{contact_id}").json()["photo"] == PHOTO


def test_create_contact_stores_multiple_addresses_as_child_rows(client, payload):
    response = client.post(BASE, json={**payload, "addresses": MULTIPLE_ADDRESSES})
    assert response.status_code == 201
    body = response.json()

    assert [address["type"] for address in body["addresses"]] == ["Home", "Work"]
    assert len({address["id"] for address in body["addresses"]}) == 2

    with SessionLocal() as db:
        rows = db.scalars(
            select(Address)
            .where(Address.contact_id == body["id"])
            .order_by(Address.position)
        ).all()
        assert len(rows) == 2
        assert [row.type.value for row in rows] == ["Home", "Work"]
        assert [row.address for row in rows] == ["12 Home St", "1 Market St"]


def test_create_rejects_invalid_address(client, payload):
    invalid_type = client.post(
        BASE,
        json={
            **payload,
            "addresses": [{"type": "Vacation", "address": "1 Beach Rd"}],
        },
    )
    assert invalid_type.status_code == 422

    blank_street = client.post(
        BASE,
        json={
            **payload,
            "email": "blank@example.com",
            "addresses": [{"type": "Other", "address": "   "}],
        },
    )
    assert blank_street.status_code == 422


def test_create_rejects_unsupported_photo_type(client, payload):
    response = client.post(
        BASE,
        json={**payload, "photo": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="},
    )
    assert response.status_code == 422


def test_create_rejects_mismatched_photo_content(client, payload):
    response = client.post(
        BASE,
        json={**payload, "photo": "data:image/png;base64,aGVsbG8="},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "photo",
    [
        "data:image/jpeg;base64,/9j/",
        "data:image/png;base64,iVBORw0KGgo=",
        "data:image/gif;base64,R0lGODlh",
        TRUNCATED_WEBP,
    ],
)
def test_create_rejects_truncated_photo(client, payload, photo):
    response = client.post(BASE, json={**payload, "photo": photo})
    assert response.status_code == 422


def test_create_rejects_oversized_photo(client, payload):
    content = b"\x89PNG\r\n\x1a\n" + b"\0" * (MAX_PHOTO_BYTES - 7)
    photo = f"data:image/png;base64,{base64.b64encode(content).decode()}"
    response = client.post(BASE, json={**payload, "photo": photo})
    assert response.status_code == 422


def test_create_requires_valid_email(client, payload):
    response = client.post(BASE, json={**payload, "email": "not-an-email"})
    assert response.status_code == 422


def test_create_requires_names(client, payload):
    response = client.post(BASE, json={**payload, "first_name": ""})
    assert response.status_code == 422


def test_duplicate_email_conflicts(client, payload):
    assert client.post(BASE, json=payload).status_code == 201
    response = client.post(BASE, json={**payload, "email": "ADA@example.com"})
    assert response.status_code == 409


def test_get_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.get(f"{BASE}/{contact_id}")
    assert response.status_code == 200
    assert response.json()["id"] == contact_id


def test_get_missing_contact_returns_404(client):
    assert client.get(f"{BASE}/9999").status_code == 404


def test_list_pagination_and_total(client, payload):
    for index in range(5):
        client.post(BASE, json={**payload, "email": f"user{index}@example.com"})

    response = client.get(BASE, params={"limit": 2, "offset": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2 and body["offset"] == 2


def test_list_omits_photo(client, payload):
    client.post(BASE, json={**payload, "photo": PHOTO})

    item = client.get(BASE).json()["items"][0]

    assert "photo" not in item


def test_list_search(client, payload):
    client.post(BASE, json=payload)
    client.post(
        BASE,
        json={**payload, "first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com", "company": "US Navy"},
    )

    hits = client.get(BASE, params={"search": "hopper"}).json()
    assert hits["total"] == 1
    assert hits["items"][0]["last_name"] == "Hopper"

    by_company = client.get(BASE, params={"search": "navy"}).json()
    assert by_company["total"] == 1

    misses = client.get(BASE, params={"search": "nobody"}).json()
    assert misses["total"] == 0


def test_list_sorting(client, payload):
    client.post(BASE, json={**payload, "last_name": "Zhang", "email": "z@example.com"})
    client.post(BASE, json={**payload, "last_name": "Adams", "email": "a@example.com"})

    names = [
        item["last_name"]
        for item in client.get(BASE, params={"sort_by": "last_name", "order": "asc"}).json()["items"]
    ]
    assert names == ["Adams", "Zhang"]


def test_list_rejects_bad_sort_field(client):
    assert client.get(BASE, params={"sort_by": "; DROP TABLE contacts"}).status_code == 422


def test_patch_updates_only_sent_fields(client, payload):
    created = client.post(BASE, json=payload).json()
    contact_id = created["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"phone": "+1-000-000-0000"})
    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == "+1-000-000-0000"
    assert body["first_name"] == "Ada"
    assert body["company"] == "Analytical Engines"
    assert body["addresses"] == created["addresses"]


def test_patch_can_add_and_remove_photo(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"photo": PHOTO})
    assert response.status_code == 200
    assert response.json()["photo"] == PHOTO

    response = client.patch(f"{BASE}/{contact_id}", json={"photo": None})
    assert response.status_code == 200
    assert response.json()["photo"] is None


def test_patch_replaces_and_clears_addresses(client, payload):
    created = client.post(
        BASE,
        json={**payload, "addresses": MULTIPLE_ADDRESSES},
    ).json()

    response = client.patch(
        f"{BASE}/{created['id']}",
        json={
            "addresses": [
                {
                    "type": "Other",
                    "address": "500 Conference Ave",
                    "city": "San Jose",
                }
            ]
        },
    )
    assert response.status_code == 200
    assert len(response.json()["addresses"]) == 1
    assert response.json()["addresses"][0]["type"] == "Other"
    assert response.json()["addresses"][0]["address"] == "500 Conference Ave"

    with SessionLocal() as db:
        rows = db.scalars(
            select(Address).where(Address.contact_id == created["id"])
        ).all()
        assert len(rows) == 1
        assert rows[0].address == "500 Conference Ave"

    response = client.patch(f"{BASE}/{created['id']}", json={"addresses": []})
    assert response.status_code == 200
    assert response.json()["addresses"] == []


def test_patch_duplicate_email_conflicts(client, payload):
    first = client.post(BASE, json=payload).json()["id"]
    client.post(BASE, json={**payload, "email": "grace@example.com"})
    response = client.patch(f"{BASE}/{first}", json={"email": "grace@example.com"})
    assert response.status_code == 409


def test_patch_same_email_is_allowed(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"email": payload["email"]})
    assert response.status_code == 200


def test_put_replaces_contact(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PHOTO}).json()["id"]
    response = client.put(
        f"{BASE}/{contact_id}",
        json={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
            "photo": PHOTO,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Grace Hopper"
    assert body["photo"] == PHOTO
    assert body["company"] is None  # omitted fields are cleared by PUT
    assert body["addresses"] == []


def test_put_missing_contact_returns_404(client):
    response = client.put(
        f"{BASE}/9999",
        json={"first_name": "A", "last_name": "B", "email": "ab@example.com"},
    )
    assert response.status_code == 404


def test_delete_contact(client, payload):
    contact_id = client.post(
        BASE,
        json={**payload, "addresses": MULTIPLE_ADDRESSES},
    ).json()["id"]
    with SessionLocal() as db:
        assert len(db.scalars(select(Address)).all()) == 2

    assert client.delete(f"{BASE}/{contact_id}").status_code == 204
    assert client.get(f"{BASE}/{contact_id}").status_code == 404
    assert client.delete(f"{BASE}/{contact_id}").status_code == 404

    with SessionLocal() as db:
        assert db.scalars(select(Address)).all() == []


def test_root_lists_entrypoints(client):
    body = client.get("/").json()
    assert body["contacts"] == BASE
