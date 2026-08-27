import base64
import binascii
import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Annotated

from PIL import Image
from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator

from app.models import AddressType

MAX_PHOTO_BYTES = 2 * 1024 * 1024
MAX_PHOTO_DATA_URL_LENGTH = ((MAX_PHOTO_BYTES + 2) // 3) * 4 + 32
PHOTO_DATA_URL_PATTERN = re.compile(
    r"^data:(image/(?:jpeg|png|webp|gif));base64,([A-Za-z0-9+/]+={0,2})$"
)


def _matches_image_type(media_type: str, content: bytes) -> bool:
    expected_formats = {
        "image/jpeg": "JPEG",
        "image/png": "PNG",
        "image/webp": "WEBP",
        "image/gif": "GIF",
    }
    expected_format = expected_formats.get(media_type)
    if expected_format is None:
        return False

    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != expected_format:
                return False
            image.verify()
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        return False
    return True


def _validate_photo_data_url(value: str) -> str:
    match = PHOTO_DATA_URL_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("Photo must be a base64-encoded JPG, PNG, WebP, or GIF image")

    media_type, encoded = match.groups()
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Photo contains invalid base64 data") from error

    if len(content) > MAX_PHOTO_BYTES:
        raise ValueError("Photo must be 2 MB or smaller")
    if not _matches_image_type(media_type, content):
        raise ValueError("Photo content does not match its declared image type")
    return value


PhotoDataUrl = Annotated[str, AfterValidator(_validate_photo_data_url)]


class AddressBase(BaseModel):
    """A postal address owned by a contact."""

    type: AddressType = Field(
        description="Address category.",
        examples=[AddressType.HOME],
    )
    address: str = Field(
        min_length=1,
        max_length=300,
        description="Street address, including unit or suite.",
        examples=["1 Market St, Suite 400"],
    )
    city: str | None = Field(
        default=None,
        max_length=120,
        description="City or locality.",
        examples=["San Francisco"],
    )
    state: str | None = Field(
        default=None,
        max_length=120,
        description="State, province, or region.",
        examples=["CA"],
    )
    postal_code: str | None = Field(
        default=None,
        max_length=20,
        description="Postal or ZIP code.",
        examples=["94105"],
    )
    country: str | None = Field(
        default=None,
        max_length=120,
        description="Country name.",
        examples=["USA"],
    )

    @field_validator("address")
    @classmethod
    def _strip_required_address(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Street address must not be blank")
        return value

    @field_validator("city", "state", "postal_code", "country")
    @classmethod
    def _strip_optional_address_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class AddressCreate(AddressBase):
    """An address submitted while creating or updating a contact."""


class AddressRead(AddressBase):
    """A stored address row nested under its contact."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Server-assigned address identifier.", examples=[1])


class ContactBase(BaseModel):
    """Fields shared by every contact request and response."""

    first_name: str = Field(
        min_length=1,
        max_length=100,
        description="Given name. Required, must not be blank.",
        examples=["Ada"],
    )
    last_name: str = Field(
        min_length=1,
        max_length=100,
        description="Family name. Required, must not be blank.",
        examples=["Lovelace"],
    )
    email: EmailStr = Field(
        max_length=320,
        description=(
            "Primary email address. Required and unique across all contacts; "
            "compared case-insensitively and stored lowercased."
        ),
        examples=["ada@example.com"],
    )
    photo: PhotoDataUrl | None = Field(
        default=None,
        max_length=MAX_PHOTO_DATA_URL_LENGTH,
        description=(
            "Optional profile photo as a base64 data URL. JPG, PNG, WebP, and GIF "
            "images up to 2 MB are accepted."
        ),
    )
    phone: str | None = Field(
        default=None,
        max_length=40,
        description="Phone number. Stored verbatim — any format is accepted.",
        examples=["+1-415-555-0101"],
    )
    company: str | None = Field(
        default=None,
        max_length=200,
        description="Employer or organisation name.",
        examples=["Analytical Engines"],
    )
    job_title: str | None = Field(
        default=None,
        max_length=200,
        description="Role held at the company.",
        examples=["Mathematician"],
    )
    notes: str | None = Field(
        default=None,
        description="Free-form notes about the contact. No length limit.",
        examples=["Met at the SF hackathon."],
    )


_FULL_EXAMPLE = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@example.com",
    "phone": "+1-415-555-0101",
    "company": "Analytical Engines",
    "job_title": "Mathematician",
    "addresses": [
        {
            "type": "Work",
            "address": "1 Market St, Suite 400",
            "city": "San Francisco",
            "state": "CA",
            "postal_code": "94105",
            "country": "USA",
        }
    ],
    "notes": "Met at the SF hackathon.",
}
_MINIMAL_EXAMPLE = {"first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com"}


class ContactCreate(ContactBase):
    """Body of `POST /api/v1/contacts`. Only the two names and email are required."""

    model_config = ConfigDict(json_schema_extra={"examples": [_FULL_EXAMPLE, _MINIMAL_EXAMPLE]})

    addresses: list[AddressCreate] = Field(
        default_factory=list,
        description="Ordered postal addresses owned by the contact.",
    )


class ContactReplace(ContactBase):
    """
    Body of `PUT /api/v1/contacts/{contact_id}`.

    This is a full replacement: any optional scalar field you omit is set back
    to `null`, and omitted addresses replace the collection with an empty list.
    Use `PATCH` if you only want to change some fields.
    """

    model_config = ConfigDict(json_schema_extra={"examples": [_FULL_EXAMPLE]})

    addresses: list[AddressCreate] = Field(
        default_factory=list,
        description="Complete ordered replacement for the contact's addresses.",
    )


class ContactUpdate(BaseModel):
    """
    Body of `PATCH /api/v1/contacts/{contact_id}`.

    Every field is optional. Only the fields actually present in the request are
    written; omitted fields keep their current value. Sending an explicit `null`
    clears that field.
    """

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"phone": "+1-415-555-0199", "job_title": "Chief Engineer"}]}
    )

    first_name: str | None = Field(default=None, min_length=1, max_length=100, description="New given name.")
    last_name: str | None = Field(default=None, min_length=1, max_length=100, description="New family name.")
    email: EmailStr | None = Field(
        default=None,
        max_length=320,
        description="New email address. Must not belong to another contact.",
    )
    photo: PhotoDataUrl | None = Field(
        default=None,
        max_length=MAX_PHOTO_DATA_URL_LENGTH,
        description="New profile photo data URL. Send null to remove the photo.",
    )
    phone: str | None = Field(default=None, max_length=40, description="New phone number.")
    company: str | None = Field(default=None, max_length=200, description="New company.")
    job_title: str | None = Field(default=None, max_length=200, description="New job title.")
    addresses: list[AddressCreate] | None = Field(
        default=None,
        description=(
            "Complete ordered replacement for the contact's addresses. "
            "Send an empty list or null to remove every address."
        ),
    )
    notes: str | None = Field(default=None, description="New notes; replaces the existing text.")


class ContactRead(ContactBase):
    """A stored contact, as returned by single-contact and mutation endpoints."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    **_FULL_EXAMPLE,
                    "id": 1,
                    "full_name": "Ada Lovelace",
                    "created_at": "2026-08-19T16:22:58.189507Z",
                    "updated_at": "2026-08-19T16:22:58.189511Z",
                }
            ]
        },
    )

    addresses: list[AddressRead] = Field(
        default_factory=list,
        description="Stored address rows in the order supplied by the client.",
    )
    id: int = Field(description="Server-assigned identifier.", examples=[1])
    created_at: datetime = Field(
        description="UTC timestamp of when the contact was created.",
        examples=["2026-08-19T16:22:58.189507Z"],
    )
    updated_at: datetime = Field(
        description="UTC timestamp of the last modification.",
        examples=["2026-08-19T16:22:58.189511Z"],
    )

    @field_validator("created_at", "updated_at")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        # SQLite discards tzinfo on write; the stored values are UTC, so label
        # them as such rather than emitting an ambiguous naive timestamp.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    @computed_field(
        description="Convenience concatenation of first and last name.",
        examples=["Ada Lovelace"],
    )
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class ContactListItem(BaseModel):
    """A contact list item without the full photo payload."""

    model_config = ConfigDict(from_attributes=True)

    first_name: str = Field(
        description="Given name.",
        examples=["Ada"],
    )
    last_name: str = Field(
        description="Family name.",
        examples=["Lovelace"],
    )
    email: EmailStr = Field(
        description="Primary email address.",
        examples=["ada@example.com"],
    )
    phone: str | None = Field(
        default=None,
        description="Phone number.",
        examples=["+1-415-555-0101"],
    )
    company: str | None = Field(
        default=None,
        description="Employer or organisation name.",
        examples=["Analytical Engines"],
    )
    job_title: str | None = Field(
        default=None,
        description="Role held at the company.",
        examples=["Mathematician"],
    )
    notes: str | None = Field(
        default=None,
        description="Free-form notes about the contact.",
        examples=["Met at the SF hackathon."],
    )
    addresses: list[AddressRead] = Field(
        default_factory=list,
        description="Stored address rows in the order supplied by the client.",
    )
    id: int = Field(description="Server-assigned identifier.", examples=[1])
    created_at: datetime = Field(
        description="UTC timestamp of when the contact was created.",
        examples=["2026-08-19T16:22:58.189507Z"],
    )
    updated_at: datetime = Field(
        description="UTC timestamp of the last modification.",
        examples=["2026-08-19T16:22:58.189511Z"],
    )

    @field_validator("created_at", "updated_at")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    @computed_field(description="Convenience concatenation of first and last name.", examples=["Ada Lovelace"])
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class ContactPage(BaseModel):
    """One page of contacts plus the totals a client needs to paginate."""

    items: list[ContactListItem] = Field(
        description="Contacts on this page, ordered by the requested sort."
    )
    total: int = Field(
        description="Total contacts matching the query, ignoring `limit` and `offset`.",
        examples=[42],
    )
    limit: int = Field(description="Page size that was applied.", examples=[50])
    offset: int = Field(description="Number of records skipped.", examples=[0])


class HealthResponse(BaseModel):
    """Result of the liveness probe."""

    status: str = Field(description="Always `ok` when the service can serve traffic.", examples=["ok"])
    database: str = Field(description="Active SQLAlchemy dialect.", examples=["sqlite"])
    contacts: int = Field(description="Number of contacts currently stored.", examples=[3])


class RootResponse(BaseModel):
    """Discovery document listing the API's entry points."""

    name: str = Field(description="Human-readable service name.", examples=["Contacts API"])
    version: str = Field(description="Service version.", examples=["0.1.0"])
    docs: str = Field(description="Path to the Swagger UI.", examples=["/docs"])
    redoc: str = Field(description="Path to the ReDoc UI.", examples=["/redoc"])
    openapi: str = Field(description="Path to the OpenAPI 3.1 document.", examples=["/openapi.json"])
    contacts: str = Field(description="Base path of the contacts collection.", examples=["/api/v1/contacts"])
    health: str = Field(description="Path to the liveness probe.", examples=["/health"])


class ErrorResponse(BaseModel):
    """Shape of every non-validation error returned by the API."""

    detail: str = Field(
        description="Human-readable explanation of the failure.",
        examples=["Contact 42 not found"],
    )
