"""
Host aliases API endpoints for IP to Hostname mapping management.
"""

import datetime
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, run_db_query
from app.models import HostAliasCreate, HostAliasResponse, MessageResponse

router = APIRouter(prefix="/aliases", tags=["Host Aliases"])


@router.get("", response_model=list[HostAliasResponse])
async def list_aliases(user: dict = Depends(get_current_user)) -> list[HostAliasResponse]:
    """List all registered IP-to-hostname alias mappings."""
    def _get_all(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT ip, alias, notes, created_at FROM host_aliases ORDER BY ip ASC")
        rows = cursor.fetchall()
        return [
            HostAliasResponse(
                ip=r["ip"],
                alias=r["alias"],
                notes=r["notes"],
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    return await run_db_query(_get_all)


@router.post("", response_model=HostAliasResponse)
async def create_or_update_alias(
    req: HostAliasCreate,
    user: dict = Depends(get_current_user),
) -> HostAliasResponse:
    """Create or update an IP-to-hostname alias mapping."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _upsert(conn):
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO host_aliases (ip, alias, notes, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ip) DO UPDATE SET
                alias = excluded.alias,
                notes = excluded.notes
            """,
            (req.ip, req.alias, req.notes, now),
        )
        conn.commit()

        cursor.execute("SELECT ip, alias, notes, created_at FROM host_aliases WHERE ip = ?", (req.ip,))
        row = cursor.fetchone()
        return HostAliasResponse(
            ip=row["ip"],
            alias=row["alias"],
            notes=row["notes"],
            created_at=str(row["created_at"]),
        )

    return await run_db_query(_upsert)


@router.delete("/{ip}", response_model=MessageResponse)
async def delete_alias(
    ip: str,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    """Delete a host alias mapping by IP address."""
    def _delete(conn):
        cursor = conn.cursor()
        cursor.execute("DELETE FROM host_aliases WHERE ip = ?", (ip,))
        conn.commit()
        return cursor.rowcount > 0

    deleted = await run_db_query(_delete)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Host alias for IP '{ip}' not found.",
        )

    return MessageResponse(status="ok")
