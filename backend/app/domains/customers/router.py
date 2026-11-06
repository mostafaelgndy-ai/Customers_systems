"""Customer detail API (API-09).

GET /api/v1/customers/{customer_id}?dataset_version_id=... — unified
score + explanation + segment assignment view, organization-scoped.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domains.customers import service as customers_service
from app.domains.identity.deps import require_membership
from app.domains.identity.models import Membership

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("/{customer_id}")
def customer_detail_endpoint(
    customer_id: str,
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require_membership),  # api-conventions §3: reads any role
    dataset_version_id: uuid.UUID,
) -> dict[str, Any]:
    """Unified customer view scoped to one dataset version (API-09)."""
    return customers_service.get_customer_detail(
        db,
        org_id=membership.organization_id,
        dataset_version_id=dataset_version_id,
        customer_id=customer_id,
    )
