"""Organization management endpoints.

Routes under the ``/organizations`` prefix:

* ``POST   /``                  - create an organization
                                    (authenticated).
* ``POST   /{organization_id}/follow``   - follow an
                                    organization (authenticated).
* ``DELETE /{organization_id}/follow``   - unfollow an
                                    organization (authenticated).

The router is built by :func:`create_router`, which closes
over the dependency :class:`~ws_core.container.Container`.

Creating an organization is the entry point into the
organizer platform (roadmap 2.1): the creating account
becomes the organization's first ``owner`` and its
platform role is promoted to ``organizer``, which
unlocks the workshop mutation routes for workshops the
organization owns.

Following (roadmap 2.3) is the attendee-side surface:
any signed-in account may follow an organization to
see its workshops in the discovery list's
``following=true`` filter. A member cannot follow
their own organization (they already hold a stronger
relationship), which is a 409 with the stable code
``cannot_follow_own_organization``. Both endpoints are
idempotent: re-following returns the existing follow
(HTTP 200 instead of 201) and unfollowing an
organization the caller never followed is a no-op.

There is deliberately no "add member" endpoint yet -
memberships beyond the creator are provisioned directly
until the membership-management plan lands.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from ws_core.auth import get_current_user
from ws_core.auth.models import User
from ws_core.container import Container
from ws_core.db.engine import get_db

from ws_reservation.schemas.organization import OrganizationCreate, OrganizationResponse
from ws_reservation.services import organization_service


def create_router(container: Container) -> APIRouter:
    """Build the ``/organizations`` router bound to ``container``.

    Args:
        container: The dependencies the handlers need.

    Returns:
        The configured ``APIRouter``.
    """
    router = APIRouter(prefix="/organizations", tags=["organizations"])

    @router.post(
        "",
        response_model=OrganizationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_organization(
        payload: OrganizationCreate,
        session: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[User, Depends(get_current_user)],
    ) -> OrganizationResponse:
        """Create an organization and make the caller its owner.

        The creator receives an ``owner`` membership and is
        promoted to the ``organizer`` platform role in the
        same transaction, so the new organization is
        immediately manageable by its owner.

        Args:
            payload: Validated creation payload (name and
                optional slug).
            session: Active async database session.
            user: The signed-in account creating the
                organization.

        Returns:
            The new organization together with the creator's
            ``owner`` membership.
        """
        return await organization_service.create_organization(session, payload, user)

    @router.post(
        "/{organization_id}/follow",
        response_model=OrganizationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def follow_organization(
        response: Response,
        organization_id: Annotated[uuid.UUID, Path()],
        session: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[User, Depends(get_current_user)],
    ) -> OrganizationResponse:
        """Follow an organization (roadmap 2.3).

        Requires a signed-in account. Following is
        idempotent: a caller who already follows the
        organization gets the existing follow back with
        HTTP 200 instead of 201, so client retries are
        safe. The response carries the organization with
        its refreshed ``followers_count``.

        Args:
            response: Active response, mutated to 200 (from
                the default 201) on an idempotent replay.
            organization_id: Organization to follow.
            session: Active async database session.
            user: The signed-in account following.

        Returns:
            The followed organization with its
            ``followers_count``.

        Raises:
            OrganizationNotFoundError: 404 if no organization
                has this id.
            CannotFollowOwnOrganizationError: 409 if the
                caller is a member of the organization.
        """
        organization, replayed = await organization_service.follow_organization(
            session, organization_id, user
        )
        if replayed:
            response.status_code = status.HTTP_200_OK
        return organization

    @router.delete(
        "/{organization_id}/follow",
        response_model=OrganizationResponse,
    )
    async def unfollow_organization(
        organization_id: Annotated[uuid.UUID, Path()],
        session: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[User, Depends(get_current_user)],
    ) -> OrganizationResponse:
        """Unfollow an organization (roadmap 2.3).

        Requires a signed-in account. Unfollowing is
        idempotent: a caller who does not follow the
        organization still gets a successful response with
        the (unchanged) ``followers_count``, so client
        retries are safe.

        Args:
            organization_id: Organization to unfollow.
            session: Active async database session.
            user: The signed-in account unfollowing.

        Returns:
            The organization with its refreshed
            ``followers_count``.

        Raises:
            OrganizationNotFoundError: 404 if no organization
                has this id.
        """
        return await organization_service.unfollow_organization(
            session, organization_id, user
        )

    return router
