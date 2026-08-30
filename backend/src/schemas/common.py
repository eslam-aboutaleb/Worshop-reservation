"""Schema types shared across API request models."""

from typing import Annotated

from pydantic import AfterValidator, EmailStr


def normalize_email(value: str) -> str:
    """Normalize an email consistently before it is persisted or queried."""
    return value.strip().lower()


NormalizedEmail = Annotated[EmailStr, AfterValidator(normalize_email)]
