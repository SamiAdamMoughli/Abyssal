"""VesselRepository — async spatial queries against PostGIS.

All heavy lifting goes through SQLAlchemy's async session. Spatial predicates
use PostGIS functions via SQLAlchemy's func namespace so they compile to
native SQL without any Python geometry processing in the query path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from geoalchemy2 import Geography
from geoalchemy2.functions import ST_MakeEnvelope, ST_Within
from sqlalchemy import cast, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from spyhop.db.models import IUUBlacklist, SanctionedVessel, VesselPosition
from spyhop.logging_config import get_logger

log = get_logger(__name__)


class VesselRepository:
    """Encapsulates all DB access for vessel data.

    One instance per request (injected via FastAPI Depends), sharing the same
    AsyncSession from the connection pool.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -----------------------------------------------------------------------
    # Spatial reads
    # -----------------------------------------------------------------------

    async def get_vessels_in_bbox(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
    ) -> Sequence[VesselPosition]:
        """ST_Within — returns all vessels inside the given bounding box.

        Uses the GiST index on ``position`` for sub-10ms performance even
        with millions of rows. SRID 4326 (WGS-84).
        """
        envelope = ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
        stmt = select(VesselPosition).where(
            ST_Within(VesselPosition.position, envelope)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_vessels_near_point(
        self,
        lat: float,
        lon: float,
        radius_m: float,
    ) -> Sequence[VesselPosition]:
        """ST_DWithin — vessels within *radius_m* metres of (lon, lat).

        ``ST_DWithin`` on a geography column uses metres; we cast to geography
        inline so the GiST index is still used.
        """
        geo = Geography(srid=4326)
        point = cast(
            func.ST_SetSRID(func.ST_Point(lon, lat), 4326), geo
        )
        stmt = select(VesselPosition).where(
            func.ST_DWithin(
                cast(VesselPosition.position, geo),
                point,
                radius_m,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_top_targets(self, limit: int = 10) -> Sequence[VesselPosition]:
        """Top vessels by pre-computed risk_score, descending.

        Backed by ``idx_vessel_positions_score``; returns in microseconds.
        Excludes zero-score vessels (no triggered rules).
        """
        stmt = (
            select(VesselPosition)
            .where(VesselPosition.risk_score > 0)
            .order_by(VesselPosition.risk_score.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_mmsi(self, mmsi: str) -> VesselPosition | None:
        stmt = select(VesselPosition).where(VesselPosition.mmsi == mmsi)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # -----------------------------------------------------------------------
    # Upsert — native PostgreSQL ON CONFLICT DO UPDATE
    # -----------------------------------------------------------------------

    async def upsert_vessel(
        self,
        vessel_data: dict[str, Any],
    ) -> None:
        """Insert or update a vessel row on MMSI conflict.

        Uses PostgreSQL ``INSERT ... ON CONFLICT (mmsi) DO UPDATE SET ...``
        so the operation is atomic and race-condition-free. The geometry is
        built from ``lat``/``lon`` keys in *vessel_data* using
        ``ST_SetSRID(ST_Point(lon, lat), 4326)``.
        """
        lat: float = vessel_data.pop("lat")
        lon: float = vessel_data.pop("lon")

        reasons = vessel_data.pop("reasons", [])
        top_reason_label = vessel_data.pop("top_reason_label", None)

        stmt = pg_insert(VesselPosition).values(
            position=func.ST_SetSRID(func.ST_Point(lon, lat), 4326),
            reasons_json=reasons,
            top_reason_label=top_reason_label,
            updated_at=datetime.now(timezone.utc),
            **vessel_data,
        )
        update_dict = {
            col.name: stmt.excluded[col.name]
            for col in VesselPosition.__table__.columns
            if col.name not in ("id", "mmsi", "created_at")
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_vessel_positions_mmsi",
            set_=update_dict,
        )
        await self.session.execute(stmt)

    async def upsert_vessels_batch(
        self, vessels: list[dict[str, Any]]
    ) -> None:
        """Upsert a list of vessel dicts in a single transaction."""
        for v in vessels:
            await self.upsert_vessel(v)
        await self.session.commit()

    # -----------------------------------------------------------------------
    # IUU Blacklist
    # -----------------------------------------------------------------------

    async def replace_iuu_blacklist(
        self, entries: list[dict[str, Any]]
    ) -> int:
        """Atomically replace all IUU records (truncate + insert)."""
        await self.session.execute(delete(IUUBlacklist))
        for e in entries:
            self.session.add(
                IUUBlacklist(
                    listing_source=e.get("source", "CCAMLR"),
                    mmsi=e.get("mmsi"),
                    imo=e.get("imo"),
                    vessel_name=e.get("name"),
                    aliases_json=e.get("aliases", []),
                    flag=e.get("flag"),
                    listing_year=e.get("year"),
                    raw_json=e,
                )
            )
        await self.session.commit()
        return len(entries)

    # -----------------------------------------------------------------------
    # Sanctions
    # -----------------------------------------------------------------------

    async def replace_sanctioned_vessels(
        self, entries: list[dict[str, Any]]
    ) -> int:
        """Atomically replace all sanction records (truncate + insert)."""
        await self.session.execute(delete(SanctionedVessel))
        for e in entries:
            self.session.add(
                SanctionedVessel(
                    opensanctions_id=e["id"],
                    vessel_name=e.get("name"),
                    aliases_json=e.get("aliases", []),
                    mmsi=e.get("mmsi"),
                    imo=e.get("imo"),
                    flag=e.get("flag"),
                    sanctions_datasets=e.get("sanctions", []),
                    source_url=e.get("source_url"),
                )
            )
        await self.session.commit()
        return len(entries)
