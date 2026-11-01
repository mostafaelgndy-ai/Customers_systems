"""Dataset ingestion & validation models (PRD §7.2, §10)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import ForeignKey, UniqueConstraint, orm
from sqlalchemy.orm import Mapped

from app.db.base import Base, TimestampMixin

# --- statuses ----------------------------------------------------------------
# DatasetFile.status
FILE_UPLOADED = "uploaded"
FILE_MAPPED = "mapped"

# DatasetVersion.status — dataset version lifecycle (DATA-12):
VERSION_VALIDATING = "validating"
VERSION_VALID = "valid"
VERSION_INVALID = "invalid"
VERSION_FAILED = "failed"
VERSION_CANCELLED = "cancelled"

FILE_KIND_CUSTOMERS = "customers"
FILE_KIND_ORDERS = "orders"
FILE_KINDS = (FILE_KIND_CUSTOMERS, FILE_KIND_ORDERS)


class Dataset(Base, TimestampMixin):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("id", "organization_id", name="uq_dataset_id_org"),)

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True)
    name: Mapped[str] = orm.mapped_column(sa.String(200), nullable=False)
    description: Mapped[str | None] = orm.mapped_column(sa.Text(), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), nullable=False)

    files: Mapped[list[DatasetFile]] = orm.relationship(back_populates="dataset")
    versions: Mapped[list[DatasetVersion]] = orm.relationship(back_populates="dataset")


class DatasetFile(Base, TimestampMixin):
    """An immutable uploaded CSV attached to a dataset.

    Column mapping lives here: raw file is never rewritten; canonical
    columns are produced at validation time via the stored mapping.
    """

    __tablename__ = "dataset_files"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), ForeignKey("datasets.id"), index=True)
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True)
    kind: Mapped[str] = orm.mapped_column(sa.String(20), nullable=False)  # customers | orders
    original_filename: Mapped[str] = orm.mapped_column(sa.String(500), nullable=False)
    storage_path: Mapped[str] = orm.mapped_column(sa.String(1000), nullable=False)  # UPLOAD_DIR-relative
    size_bytes: Mapped[int] = orm.mapped_column(sa.BigInteger, nullable=False)
    sha256: Mapped[str] = orm.mapped_column(sa.String(64), nullable=False)
    headers: Mapped[list] = orm.mapped_column(sa.JSON, nullable=False, default=list)
    mapping: Mapped[dict | None] = orm.mapped_column(sa.JSON, nullable=True)  # canonical -> source
    status: Mapped[str] = orm.mapped_column(sa.String(20), nullable=False, default=FILE_UPLOADED)
    created_by_user_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), nullable=False)

    dataset: Mapped[Dataset] = orm.relationship(back_populates="files")


class DatasetVersion(Base, TimestampMixin):
    """Immutable analysis view of a dataset (one validated file set)."""

    __tablename__ = "dataset_versions"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), ForeignKey("datasets.id"), index=True)
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True)
    version_number: Mapped[int] = orm.mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = orm.mapped_column(sa.String(20), nullable=False, default=VERSION_VALIDATING)
    report: Mapped[dict | None] = orm.mapped_column(sa.JSON, nullable=True)
    error_summary: Mapped[dict | None] = orm.mapped_column(sa.JSON, nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), nullable=False)

    dataset: Mapped[Dataset] = orm.relationship(back_populates="versions")
    file_links: Mapped[list[DatasetVersionFile]] = orm.relationship(back_populates="version")


class DatasetVersionFile(Base):
    """Pins (file, mapping-snapshot) pairs into a version. Immutable by convention."""

    __tablename__ = "dataset_version_files"
    __table_args__ = (UniqueConstraint("dataset_version_id", "dataset_file_id", name="uq_version_file"),)

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    dataset_version_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), ForeignKey("dataset_versions.id"), index=True)
    dataset_file_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), ForeignKey("dataset_files.id"))
    file_kind: Mapped[str] = orm.mapped_column(sa.String(20), nullable=False)

    version: Mapped[DatasetVersion] = orm.relationship(back_populates="file_links")
