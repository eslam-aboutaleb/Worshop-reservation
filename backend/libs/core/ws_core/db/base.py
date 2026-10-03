"""SQLAlchemy declarative base shared by every model.

The ``Base`` class is the registry Alembic reads when running
``alembic revision --autogenerate``: every mapped class must be
imported (directly or transitively) into ``Base.metadata`` for its
table to appear in the generated migration.

ws-core owns the ``Base`` so the application and every plugin
register their models on the same metadata - a single
``Base.metadata`` is what lets one Alembic migration set
describe the whole composed schema.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Common ancestor for all ORM models in the service.

    Subclassing ``DeclarativeBase`` (rather than the older
    ``declarative_base()`` function) is the SQLAlchemy 2.0 idiom and
    gives us typed ``Mapped[...]`` annotations throughout the project.
    """
