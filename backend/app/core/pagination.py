"""Pagination contract (ENG-06): bounded query params + list envelope."""

from __future__ import annotations

import math
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

MAX_PAGE_SIZE = 200

T = TypeVar("T")


class PageParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(25, ge=1, le=MAX_PAGE_SIZE)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    pages: int
    total: int


def build_page(items: list[Any], params: PageParams, total: int) -> dict[str, Any]:
    return {
        "items": items,
        "page": params.page,
        "page_size": params.page_size,
        "pages": math.ceil(total / params.page_size) if total else 0,
        "total": total,
    }
