"""版本管理领域服务：黄金记录版本快照、变更对比与版本回退。

版本号体系改造后：
- 版本身份（精确整数）与规范版本编号（V<n>）由黄金版本映射
  （version_mapping）统一分配，本服务不再用浮点数自增。
- 存量行的「版本号」原字段保留旧浮点写法（9.0/10.0），按原参数查询仍
  读到原字段值；新提交行的「版本号」写规范版本编号。
- 回退支持旧编号（9.0/10.0）与新编号（V11/身份11）两种参数，统一通过
  映射解析到唯一版本身份，歧义修订不会落到另一版。
"""
from __future__ import annotations

import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.services.version_mapping import VersionMappingError, VersionMappingService
from app.store import store


class MdmVersionService(BaseModuleService):
    MODULE = "mdm_version"
    ENTITY = "版本记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = [
        "版本编号", "黄金ID", "版本身份", "规范版本编号", "版本号", "编号体系",
        "变更字段", "旧值摘要", "新值摘要", "变更批次", "版本时间",
    ]
    REQUIRED_FIELDS = ["版本编号", "黄金ID", "版本号"]
    STATUS_ORDER = ["草稿", "当前版本", "历史版本", "已回滚"]
    ACTION_RULES = {"设为当前": "当前版本", "标记历史": "历史版本", "执行回滚": "已回滚"}
    NEGATIVE_ACTIONS = []

    def __init__(self) -> None:
        self.mapping_svc = VersionMappingService()

    def snapshot_golden_version(self, golden_id: str,
                                  record_data: dict[str, Any]) -> dict[str, Any]:
        """存储黄金记录版本快照，记录字段变更的新旧值对比。

        读取当前黄金记录数据，对比新值生成变更字段列表；新版本身份通过
        黄金版本映射分配（同一黄金ID内永不重用已分配身份），映射行与版本
        快照双向关联，并把黄金记录接口的当前版本指向新身份。
        """
        from app.services.golden_record import GoldenRecordService
        golden_svc = GoldenRecordService()
        current = golden_svc.query_golden_record(golden_id)

        # 新序列身份由映射统一分配，杜绝与存量 9.0/10.0 身份冲突或重用
        mapping = self.mapping_svc.allocate_new_identity(golden_id)
        identity = int(mapping["版本身份"])
        canonical = mapping["规范版本编号"]

        changed_fields = []
        old_summary: dict[str, str] = {}
        new_summary: dict[str, str] = {}
        if current:
            for key in record_data:
                if key.startswith("_"):
                    continue
                old_val = str(current.get(key, ""))
                new_val = str(record_data.get(key, ""))
                if old_val != new_val:
                    changed_fields.append(key)
                    old_summary[key] = old_val
                    new_summary[key] = new_val

        rows = store.rows(self.MODULE)
        version_id = f"VER-{golden_id}-{identity}"
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "版本编号": version_id,
            "黄金ID": golden_id,
            # 版本身份与规范编号关联映射；新行「版本号」直接写规范编号
            "版本身份": identity,
            "规范版本编号": canonical,
            "版本号": canonical,
            "编号体系": "new-sequence",
            "变更字段": ",".join(changed_fields),
            "旧值摘要": str(old_summary) if old_summary else "",
            "新值摘要": str(new_summary) if new_summary else "",
            "变更批次": golden_id,
            "版本时间": datetime.datetime.utcnow().isoformat(),
            "status": "当前版本",
            "pending": False,
            "abnormal": False,
        }
        # 存储版本快照：旧当前版本转历史，映射侧同步提升新身份为当前
        self._set_previous_history(golden_id)
        self.mapping_svc.mark_previous_history(golden_id)
        rows.append(entry)
        self.mapping_svc.promote_current(golden_id, identity)
        return {
            "ok": True,
            "version": entry,
            "version_identity": identity,
            "canonical_version": canonical,
            "changed_fields": changed_fields,
        }

    def _set_previous_history(self, golden_id: str) -> None:
        """将之前当前版本标记为历史版本。"""
        rows = store.rows(self.MODULE)
        for row in rows:
            if row.get("黄金ID") == golden_id and row.get("status") == "当前版本":
                row["status"] = "历史版本"

    def _resolve_identity(self, golden_id: str, version_ref: Any) -> int:
        """把任意版本引用（旧编号/新编号/身份）解析为唯一版本身份。"""
        return self.mapping_svc.resolve_identity(golden_id, version_ref)

    def _find_by_identity(self, golden_id: str, identity: int) -> dict[str, Any] | None:
        return next(
            (r for r in store.rows(self.MODULE)
             if r.get("黄金ID") == golden_id and int(r.get("版本身份", -1)) == int(identity)),
            None,
        )

    def query_version_history(self, golden_id: str) -> list[dict[str, Any]]:
        """查询版本历史：获取黄金记录的所有版本变更记录。

        按版本身份降序排列；未迁移的存量行（没有身份）按原浮点版本号
        兜底排序，保证迁移前数据也能正常展示。
        """
        rows = store.rows(self.MODULE)
        history = [row for row in rows if row.get("黄金ID") == golden_id]

        def sort_key(row: dict[str, Any]) -> tuple[int, float]:
            identity = row.get("版本身份")
            if identity not in (None, ""):
                return (1, float(identity))
            legacy = self.mapping_svc.parse_legacy_float(row.get("版本号"))
            return (0, legacy if legacy is not None else 0.0)

        history.sort(key=sort_key, reverse=True)
        return history

    def rollback_to_version(self, golden_id: str,
                              target_version: str) -> dict[str, Any]:
        """版本回退：将黄金记录回退到指定历史版本。

        target_version 既可以是旧编号（9.0/10.0，按原参数核对），也可以
        是新规范编号（V11）或版本身份。统一经映射解析到唯一身份后执行，
        解析失败或有歧义时返回失败，绝不回退到另一版。
        """
        try:
            identity = self._resolve_identity(golden_id, target_version)
        except VersionMappingError as exc:
            return {"ok": False, "reason": str(exc)}

        target = self._find_by_identity(golden_id, identity)
        if not target:
            return {"ok": False, "reason": f"版本身份 {identity} 的快照不存在"}

        rows = store.rows(self.MODULE)
        # 标记当前版本为已回滚，设置目标版本为当前
        for row in rows:
            if row.get("黄金ID") == golden_id and row.get("status") == "当前版本":
                row["status"] = "已回滚"
                row["rolled_back_at"] = datetime.datetime.utcnow().isoformat()
        target["status"] = "当前版本"
        # 映射与黄金记录接口同步当前版本身份（原「记录版本」字段保留）
        self.mapping_svc.promote_current(golden_id, identity)
        return {
            "ok": True,
            "golden_id": golden_id,
            "restored_to": target_version,
            "restored_identity": identity,
            "canonical_version": target.get("规范版本编号"),
            "legacy_version_field": target.get("版本号"),
        }

    def compare_versions(self, golden_id: str,
                           version_a: str, version_b: str) -> dict[str, Any]:
        """对比两个版本的黄金记录差异：逐字段展示变更内容。

        两个入参都支持旧编号与新编号混传，经映射解析到唯一身份后取快照，
        输出字段级变更对比报告供审计使用。
        """
        try:
            identity_a = self._resolve_identity(golden_id, version_a)
            identity_b = self._resolve_identity(golden_id, version_b)
        except VersionMappingError as exc:
            return {"ok": False, "reason": str(exc)}

        ver_a = self._find_by_identity(golden_id, identity_a)
        ver_b = self._find_by_identity(golden_id, identity_b)
        if not ver_a or not ver_b:
            return {"ok": False, "reason": "版本不存在"}
        # 对比版本差异：逐字段展示两个版本的新旧值变更内容
        return {
            "ok": True,
            "golden_id": golden_id,
            "version_a": version_a,
            "version_b": version_b,
            "identity_a": identity_a,
            "identity_b": identity_b,
            "a_time": ver_a.get("版本时间"),
            "b_time": ver_b.get("版本时间"),
            "a_changes": ver_a.get("变更字段"),
            "b_changes": ver_b.get("变更字段"),
        }
