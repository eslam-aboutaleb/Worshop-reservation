"""Offset pagination helpers shared by list endpoints.

The discovery list (and any future paginated endpoint)
returns the same envelope ``{items, total, limit, offset}``
so clients can render "load more" and know when the
catalogue is exhausted. The clamping rules live here so
every endpoint applies them identically:

* ``limit`` is clamped to ``[1, max_page_size]`` - a
  caller asking for zero or a negative page gets a single
  row, and a caller asking for more than the cap gets the
  cap (never an unbounded query).
* ``offset`` is clamped to ``>= 0`` - a negative offset
  is treated as zero rather than rejected, so a malformed
  client link still returns the first page.

The :class:`Page` model is the generic envelope; a domain
response (e.g. the workshop list) mirrors it with its own
typed ``items`` element.
"""

from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

# Default page size for discovery lists. Small on purpose:
# a catalogue is dozens of sessions, not millions.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class PageParams:
    """Clamped ``limit`` / ``offset`` pair for a list query.

    Attributes:
        limit: Page size, clamped to ``[1, max_page_size]``.
        offset: Row offset, clamped to ``>= 0``.
    """

    limit: int
    offset: int

    @classmethod
    def resolve(
        cls,
        limit: int,
        offset: int,
        *,
        max_page_size: int = MAX_PAGE_SIZE,
    ) -> "PageParams":
        """Clamp raw ``limit`` / ``offset`` query values.

        Args:
            limit: Requested page size.
            offset: Requested row offset.
            max_page_size: Upper bound on the page size.

        Returns:
            A :class:`PageParams` with both values clamped
            to their safe ranges.
        """
        return cls(
            limit=max(1, min(limit, max_page_size)),
            offset=max(0, offset),
        )


# PEP 695 generics (``class Page[T]``) need pydantic >= 2.9;
# ws-core declares a 2.7 floor, so the ``Generic[T]`` form is
# the compatible one.
class Page(BaseModel, Generic[T]):  # noqa: UP046
    """Generic paginated envelope: ``{items, total, limit, offset}``.

    Domain list responses mirror this shape with a typed
    ``items`` element so the wire contract is identical
    across every paginated endpoint.
    """

    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(gt=0)
    offset: int = Field(ge=0)
