"""Create tables without a migration (dev convenience; production uses alembic).

Usage: python -m scripts.init_db
"""

from __future__ import annotations

from app.db.base import Base
from app.db.session import engine
from app.domains.datasets import models as _datasets  # noqa: F401
from app.domains.identity import models as _identity  # noqa: F401
from app.domains.jobs import models as _jobs  # noqa: F401


def main() -> None:
    Base.metadata.create_all(engine)
    print("Tables created in", engine.url)


if __name__ == "__main__":
    main()
