"""黄金记录领域服务：黄金记录创建、关联来源、置信度计算与版本管理。"""
from __future__ import annotations

import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class GoldenRecordService(BaseModuleService):
    MODULE = "golden_record"
    ENTITY = "黄金记录"
    OPERATION_TERM = "关联"
    LIST_FIELDS = [
        "黄金ID", "实体模型", "关联来源", "关键字段", "代表字段", "置信度",
        "最后合并", "记录版本", "当前版本身份", "规范版本编号",
    ]
    REQUIRED_FIELDS = ["黄金ID", "实体模型", "关联来源"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用记录": "已停用"}
    NEGATIVE_ACTIONS = ["停用记录"]

    def create_golden_record(self, entity_model: str, merged_data: dict[str, Any],
                               source_ids: list[str]) -> dict[str, Any]:
        """创建黄金记录：将合并后的代表记录写入黄金记录存储。

        写入黄金记录到golden_records存储，关联来源批次与合并追溯信息，
        设置置信度、关联来源和最后合并时间，生成唯一黄金ID。
        """
        rows = store.rows(self.MODULE)
        golden_id = f"GR-{entity_model}-{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        confidence = self._compute_confidence(len(source_ids))
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "黄金ID": golden_id,
            "实体模型": entity_model,
            "关联来源": "|".join(source_ids),
            "关键字段": ",".join(k for k in merged_data if not k.startswith("_")),
            "代表字段": str(merged_data.get("representative", {})),
            "置信度": f"{confidence:.2f}",
            "最后合并": datetime.datetime.utcnow().isoformat(),
            "记录版本": "1.0",
            "status": "已生效",
            "pending": False,
            "abnormal": False,
            "source_count": len(source_ids),
        }
        for key, val in merged_data.items():
            if not key.startswith("_"):
                entry[key] = val
        rows.append(entry)
        # 写入黄金记录到golden_records存储，关联来源批次与合并追溯信息
        return {"ok": True, "golden_record": entry}

    def _compute_confidence(self, source_count: int) -> float:
        """计算黄金记录置信度：基于来源数量和质量指标综合评估。"""
        base = 0.5
        source_bonus = min(source_count * 0.1, 0.3)
        return min(round(base + source_bonus, 2), 0.99)

    def query_golden_record(self, golden_id: str) -> dict[str, Any] | None:
        """查询黄金记录：按黄金ID检索golden_records存储。

        查询黄金记录的完整字段信息、关联来源列表和置信度评分，
        返回黄金记录的完整快照数据。
        """
        rows = store.rows(self.MODULE)
        for row in rows:
            if row.get("黄金ID") == golden_id:
                # 查询黄金记录完整信息：包含关联来源和置信度
                return dict(row)
        return None

    def link_source_to_golden(self, golden_id: str, new_source_id: str) -> dict[str, Any]:
        """关联新来源记录到已有黄金记录。

        将新增来源记录关联到已有黄金记录，更新关联来源列表，
        重新计算置信度评分并更新最后合并时间。
        """
        record = self.query_golden_record(golden_id)
        if not record:
            return {"ok": False, "reason": "黄金记录不存在"}
        sources = record.get("关联来源", "").split("|")
        if "" in sources:
            sources.remove("")
        if new_source_id in sources:
            return {"ok": False, "reason": "来源已关联"}
        sources.append(new_source_id)
        record["关联来源"] = "|".join(sources)
        record["source_count"] = len(sources)
        record["置信度"] = f"{self._compute_confidence(len(sources)):.2f}"
        # 关联新来源记录：更新黄金记录的来源列表和置信度
        return {"ok": True, "golden_id": golden_id, "source_count": len(sources)}

    def validate_golden_completeness(self, golden_id: str,
                                      required_fields: list[str]) -> dict[str, Any]:
        """校验黄金记录的字段完整性：检查所有必填字段是否已填充。

        查询黄金记录数据，读取golden_records存储中的字段值，
        校验必填字段完整性并输出完备率统计。
        """
        record = self.query_golden_record(golden_id)
        if not record:
            return {"ok": False, "reason": "黄金记录不存在"}
        missing = [f for f in required_fields if not str(record.get(f, "")).strip()]
        complete_count = len(required_fields) - len(missing)
        return {
            "golden_id": golden_id,
            "total_required": len(required_fields),
            "filled": complete_count,
            "missing": missing,
            "completeness": round(complete_count / max(len(required_fields), 1) * 100, 2),
        }

    def search_golden_records(self, entity_model: str | None = None,
                                min_confidence: float = 0.0) -> list[dict[str, Any]]:
        """搜索黄金记录：按实体模型和最低置信度筛选。

        查询golden_records存储，按实体模型筛选并过滤置信度
        低于阈值的记录，返回符合条件的黄金记录列表。
        """
        rows = store.rows(self.MODULE)
        results = [
            row for row in rows
            if row.get("status") == "已生效"
        ]
        if entity_model:
            results = [r for r in results if r.get("实体模型") == entity_model]
        results = [r for r in results if float(r.get("置信度", "0")) >= min_confidence]
        # 查询黄金记录：筛选已生效、匹配实体模型且置信度达标的记录
        results.sort(key=lambda r: float(r.get("置信度", "0")), reverse=True)
        return results

    def export_golden_records(self, entity_model: str) -> list[dict[str, Any]]:
        """导出黄金记录：将指定实体模型的黄金记录导出供下游系统消费。

        查询golden_records存储中已生效的黄金记录，导出完整的
        记录数据，包含关键字段、代表字段和置信度信息。
        """
        records = self.search_golden_records(entity_model=entity_model, min_confidence=0.5)
        # 导出黄金记录：将已生效记录数据导出供下游系统分发同步
        exported = []
        for r in records:
            exported.append({
                "golden_id": r.get("黄金ID"),
                "entity_model": r.get("实体模型"),
                "confidence": float(r.get("置信度", "0")),
                "sources": (r.get("关联来源", "")).split("|"),
                "key_fields": (r.get("关键字段", "")).split(","),
            })
        return exported

    def query_record_by_version(self, golden_id: str,
                                  version_ref: str) -> dict[str, Any]:
        """按原编号或新编号读取黄金记录的版本数据。

        运营仍用原版号（9.0、10.0，含 "10" 与 "10.0" 两种旧写法）核对地址
        历史，新系统同时支持新规范编号（V11）和版本身份。版本引用统一经
        黄金版本映射解析到唯一身份，再返回对应版本快照；原「记录版本」
        字段保持原值返回，保证既有真实可定位的版本按原请求读到原字段值。
        """
        from app.services.mdm_version import MdmVersionService
        from app.services.version_mapping import VersionMappingError, VersionMappingService

        record = self.query_golden_record(golden_id)
        if not record:
            return {"ok": False, "reason": "黄金记录不存在"}

        mapping_svc = VersionMappingService()
        try:
            mapping = mapping_svc.resolve_mapping(golden_id, version_ref)
        except VersionMappingError as exc:
            # 歧义或无法定位：显式失败，不把修订静默落到另一版
            return {"ok": False, "reason": str(exc)}

        identity = int(mapping["版本身份"])
        version_svc = MdmVersionService()
        snapshot = version_svc._find_by_identity(golden_id, identity)
        return {
            "ok": True,
            "golden_id": golden_id,
            "requested_version": version_ref,
            "version_identity": identity,
            "canonical_version": mapping["规范版本编号"],
            "legacy_aliases": mapping_svc._aliases(mapping),
            "snapshot": snapshot,
            # 原字段版本：历史标识按原值回传，供按原参数核对
            "legacy_record_version": record.get("记录版本"),
            "current_version_identity": record.get("当前版本身份"),
        }

    def version_dependency_boundary(self, golden_id: str) -> dict[str, Any]:
        """查询黄金记录的旧编号/新序列依赖边界，委托黄金版本映射。"""
        from app.services.version_mapping import VersionMappingService

        return VersionMappingService().version_dependency_boundary(golden_id)