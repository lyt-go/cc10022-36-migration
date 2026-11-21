"""黄金版本映射专用接口：迁移、按编号读取、依赖边界与回切。

通用资源接口（/api/version_mapping、/api/mdm_version、/api/golden_record）
继续提供列表/登记/动作能力；本路由承载版本号体系迁移的领域动作。
路径前缀 /api/version-mapping 与通用资源路由 /api/{module} 不冲突。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.golden_record import GoldenRecordService
from app.services.mdm_version import MdmVersionService
from app.services.version_mapping import VersionMappingError, VersionMappingService

router = APIRouter(prefix="/api/version-mapping", tags=["黄金版本映射"])


class MigratePayload(BaseModel):
    batch: str | None = None


class VersionRefPayload(BaseModel):
    golden_id: str
    version: str = Field(description="旧编号(9.0/10.0)、新规范编号(V11)或版本身份")


class SnapshotPayload(BaseModel):
    golden_id: str
    record_data: dict[str, Any] = Field(default_factory=dict)


@router.post("/migrate")
def migrate_legacy_versions(payload: MigratePayload) -> dict[str, Any]:
    """存量浮点版本迁移：建立黄金版本映射并回填版本身份（幂等）。"""
    svc = VersionMappingService()
    try:
        return svc.migrate_legacy_versions(batch=payload.batch)
    except VersionMappingError as exc:
        return {"ok": False, "reason": str(exc)}


@router.get("/boundary/{golden_id}")
def dependency_boundary(golden_id: str) -> dict[str, Any]:
    """查询旧编号体系与新序列的依赖边界。"""
    return VersionMappingService().version_dependency_boundary(golden_id)


@router.post("/resolve")
def resolve_version(payload: VersionRefPayload) -> dict[str, Any]:
    """把旧编号/新编号解析为唯一版本身份（歧义时显式报错）。"""
    svc = VersionMappingService()
    try:
        mapping = svc.resolve_mapping(payload.golden_id, payload.version)
    except VersionMappingError as exc:
        return {"ok": False, "reason": str(exc)}
    return {
        "ok": True,
        "golden_id": payload.golden_id,
        "requested_version": payload.version,
        "version_identity": int(mapping["版本身份"]),
        "canonical_version": mapping["规范版本编号"],
        "legacy_aliases": svc._aliases(mapping),
        "scheme": mapping.get("编号体系"),
    }


@router.get("/golden/{golden_id}")
def read_golden_by_version(golden_id: str, version: str) -> dict[str, Any]:
    """按原编号或新编号读取黄金记录版本数据（原字段值原样返回）。"""
    return GoldenRecordService().query_record_by_version(golden_id, version)


@router.post("/snapshot")
def create_snapshot(payload: SnapshotPayload) -> dict[str, Any]:
    """新提交：分配不重用身份的新版本快照并关联黄金记录。"""
    return MdmVersionService().snapshot_golden_version(
        payload.golden_id, payload.record_data,
    )


@router.post("/rollback")
def rollback_version(payload: VersionRefPayload) -> dict[str, Any]:
    """模拟回切：按旧编号或新编号回退，并同步映射与黄金记录当前版本。"""
    return MdmVersionService().rollback_to_version(
        payload.golden_id, payload.version,
    )
