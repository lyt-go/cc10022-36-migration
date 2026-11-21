"""黄金版本映射领域服务。

负责把存量以浮点数递增的版本号（9.0、10.0，以及同一条记录在旧环境里的
不同写法，例如 "10" 与 "10.0"）迁移为精确的整数版本身份，建立并维护
黄金版本映射数据。

设计约定：
- 映射表是版本身份的唯一权威来源；黄金记录通过「当前版本身份」关联映射。
- 同一黄金ID下，存量浮点版本按数值归并到同一身份（"10" 与 "10.0" 是同
  一条版本的两种旧写法，落同一条映射，绝不拆成两条）。
- 新分配的身份在该黄金ID内永不重用已分配过的版本身份：新序列从存量最大
  身份之后继续递增。
- 旧编号/新序列的依赖边界可查询；旧编号解析遇到一对多歧义时显式报错，
  绝不把歧义修订静默落到另一版。
- 迁移只回填映射与版本身份，保留 mdm_version.版本号 与 golden_record.
  记录版本 原字段，供按原参数核对和失败回切。
"""
from __future__ import annotations

import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class VersionMappingError(ValueError):
    """版本映射无法唯一定位（目标缺失或存在歧义）时抛出。"""


class VersionMappingService(BaseModuleService):
    MODULE = "version_mapping"
    ENTITY = "黄金版本映射"
    OPERATION_TERM = "映射"
    LIST_FIELDS = [
        "映射编号", "黄金ID", "版本身份", "规范版本编号", "原版本号",
        "旧编号写法", "编号体系", "迁移批次", "映射时间",
    ]
    REQUIRED_FIELDS = ["映射编号", "黄金ID", "版本身份"]
    STATUS_ORDER = ["草稿", "当前版本", "历史版本", "已回滚"]
    ACTION_RULES = {"设为当前": "当前版本", "标记历史": "历史版本", "执行回滚": "已回滚"}
    NEGATIVE_ACTIONS = []

    # ───────────────────────── 旧编号解析（依赖边界） ─────────────────────────

    @staticmethod
    def parse_legacy_float(value: Any) -> float | None:
        """把旧版号解析为浮点数；非浮点递增体系（新整数编号、文本）返回 None。"""
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def canonical_code(identity: int) -> str:
        """新编号体系下的精确版本编号，如身份 9 -> "V9"。"""
        return f"V{identity}"

    def _rows_for(self, golden_id: str) -> list[dict[str, Any]]:
        return [r for r in store.rows(self.MODULE) if r.get("黄金ID") == golden_id]

    def _next_identity(self, golden_id: str) -> int:
        """分配该黄金ID内从未使用过的下一个版本身份。

        同时考虑映射表身份与 mdm_version 已回填的身份，保证新编号不重用
        同一黄金ID已分配的任何版本身份。
        """
        used = {int(r["版本身份"]) for r in self._rows_for(golden_id)}
        for row in store.rows("mdm_version"):
            if row.get("黄金ID") == golden_id and row.get("版本身份") not in (None, ""):
                used.add(int(row["版本身份"]))
        return (max(used) + 1) if used else 1

    # ───────────────────────── 存量迁移 ─────────────────────────

    def migrate_legacy_versions(self, batch: str | None = None) -> dict[str, Any]:
        """把存量浮点版本数据迁移到精确版本编号，建立黄金版本映射。

        数据来源（同一黄金ID的两种旧版本号写法都会被纳入同一条映射）：
        1. mdm_version 中可解析为浮点数的「版本号」；
        2. golden_record 中可解析为浮点数的「记录版本」。

        幂等：已迁移的版本（身份或别名已登记）跳过，可重复执行。
        迁移不覆盖任何原字段，只新增映射行并回填版本身份/规范版本编号。
        """
        batch = batch or "MIG-legacy"
        migrated_rows: list[dict[str, Any]] = []
        touched_golden_ids: set[str] = set()

        # golden_id -> float值 -> 映射行，先把同一数值的不同写法归并
        numeric_index: dict[str, dict[float, dict[str, Any]]] = {}
        # golden_id -> 非数值旧写法 -> 映射行
        textual_index: dict[str, dict[str, dict[str, Any]]] = {}

        def existing_alias(golden_id: str, raw_text: str) -> dict[str, Any] | None:
            for row in self._rows_for(golden_id):
                if raw_text in self._aliases(row):
                    return row
            return None

        def ensure_numeric(golden_id: str, raw_text: str) -> dict[str, Any]:
            num = float(raw_text)
            per_golden = numeric_index.setdefault(golden_id, {})
            if num in per_golden:
                row = per_golden[num]
            else:
                row = self._find_row_by_identity_value(golden_id, num)
                if row is None:
                    identity = self._allocate_identity_for(golden_id, num)
                    row = self._new_mapping_row(golden_id, identity, raw_text, "legacy-float", batch)
                    store.rows(self.MODULE).append(row)
                    migrated_rows.append(row)
                per_golden[num] = row
            self._append_alias(row, raw_text)
            touched_golden_ids.add(golden_id)
            return row

        def ensure_textual(golden_id: str, raw_text: str) -> dict[str, Any]:
            per_golden = textual_index.setdefault(golden_id, {})
            if raw_text in per_golden:
                return per_golden[raw_text]
            row = existing_alias(golden_id, raw_text)
            if row is None:
                identity = self._next_identity(golden_id)
                row = self._new_mapping_row(golden_id, identity, raw_text, "legacy-text", batch)
                store.rows(self.MODULE).append(row)
                migrated_rows.append(row)
            self._append_alias(row, raw_text)
            per_golden[raw_text] = row
            touched_golden_ids.add(golden_id)
            return row

        def ingest(golden_id: str, raw_value: Any) -> None:
            if golden_id in (None, "") or raw_value in (None, ""):
                return
            raw_text = str(raw_value).strip()
            if existing_alias(golden_id, raw_text):
                touched_golden_ids.add(golden_id)
                return
            num = self.parse_legacy_float(raw_text)
            if num is None:
                ensure_textual(golden_id, raw_text)
            else:
                ensure_numeric(golden_id, raw_text)

        # 来源1：mdm_version 存量版本快照
        for ver_row in store.rows("mdm_version"):
            ingest(ver_row.get("黄金ID"), ver_row.get("版本号"))

        # 来源2：golden_record 记录版本（运营仍用原版号核对的另一写法）
        for golden_row in store.rows("golden_record"):
            ingest(golden_row.get("黄金ID"), golden_row.get("记录版本"))

        # 回填 mdm_version：原「版本号」保留不动，补版本身份与规范版本编号
        backfilled = 0
        for ver_row in store.rows("mdm_version"):
            golden_id = ver_row.get("黄金ID")
            raw_text = str(ver_row.get("版本号", "")).strip()
            mapping = existing_alias(golden_id, raw_text)
            if mapping is None:
                num = self.parse_legacy_float(raw_text)
                if num is not None:
                    mapping = self._find_row_by_identity_value(golden_id, num)
            if mapping is not None:
                ver_row["版本身份"] = int(mapping["版本身份"])
                ver_row["规范版本编号"] = mapping["规范版本编号"]
                ver_row["编号体系"] = mapping["编号体系"]
                backfilled += 1

        # 黄金记录关联映射：保存当前版本身份，原「记录版本」字段保留
        linked = 0
        for golden_row in store.rows("golden_record"):
            golden_id = golden_row.get("黄金ID")
            raw_text = str(golden_row.get("记录版本", "")).strip()
            mapping = existing_alias(golden_id, raw_text)
            if mapping is None:
                num = self.parse_legacy_float(raw_text)
                if num is not None:
                    mapping = self._find_row_by_identity_value(golden_id, num)
            if mapping is not None:
                golden_row["当前版本身份"] = int(mapping["版本身份"])
                golden_row["规范版本编号"] = mapping["规范版本编号"]
                linked += 1

        return {
            "ok": True,
            "batch": batch,
            "mapping_created": len(migrated_rows),
            "version_rows_backfilled": backfilled,
            "golden_records_linked": linked,
            "golden_ids": sorted(t for t in touched_golden_ids if t),
        }

    def _allocate_identity_for(self, golden_id: str, num: float) -> int:
        """为存量浮点版本分配精确身份。

        整数浮点（9.0、10.0）直接取整；非整数浮点（如 9.5）没有安全整数
        身份可映射，显式报错而不是静默四舍五入到另一版。
        """
        if not float(num).is_integer():
            raise VersionMappingError(
                f"黄金ID {golden_id} 的旧版号 {num} 不是整数递增版本，"
                "无法安全映射到精确版本身份，请人工修订后再迁移"
            )
        identity = int(num)
        used = {int(r["版本身份"]) for r in self._rows_for(golden_id)}
        if identity in used:
            raise VersionMappingError(
                f"黄金ID {golden_id} 版本身份 {identity} 已分配，禁止重用"
            )
        return identity

    def _find_row_by_identity_value(self, golden_id: str, num: float) -> dict[str, Any] | None:
        if not float(num).is_integer():
            return None
        identity = int(num)
        return next(
            (r for r in self._rows_for(golden_id) if int(r["版本身份"]) == identity),
            None,
        )

    def _new_mapping_row(
        self, golden_id: str, identity: int, raw_text: str, scheme: str, batch: str,
    ) -> dict[str, Any]:
        rows = store.rows(self.MODULE)
        return {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "映射编号": f"VMAP-{golden_id}-{identity}",
            "黄金ID": golden_id,
            "版本身份": identity,
            "规范版本编号": self.canonical_code(identity),
            "原版本号": raw_text,
            "旧编号写法": raw_text,
            "编号体系": scheme,
            "迁移批次": batch,
            "映射时间": datetime.datetime.utcnow().isoformat(),
            "status": "历史版本",
            "pending": False,
            "abnormal": False,
        }

    @staticmethod
    def _aliases(row: dict[str, Any]) -> list[str]:
        return [a for a in str(row.get("旧编号写法", "")).split("|") if a]

    def _append_alias(self, row: dict[str, Any], raw_text: str) -> None:
        aliases = self._aliases(row)
        if raw_text not in aliases:
            aliases.append(raw_text)
        row["旧编号写法"] = "|".join(aliases)
        # 保留首次见到的写法作为原版本号
        if not row.get("原版本号"):
            row["原版本号"] = raw_text

    # ───────────────────────── 旧编号 / 新编号统一解析 ─────────────────────────

    def resolve_mapping(self, golden_id: str, version_ref: Any) -> dict[str, Any]:
        """按版本引用唯一定位映射行。

        支持三类引用：新规范编号（V10）、精确身份（10 / "10"）、旧编号
        （9.0 / 10.0 / "10" 等历史写法）。一对多歧义时抛错，绝不落到另一版。
        """
        if version_ref in (None, ""):
            raise VersionMappingError("版本引用为空，无法定位版本身份")
        text = str(version_ref).strip()
        rows = self._rows_for(golden_id)
        if not rows:
            raise VersionMappingError(f"黄金ID {golden_id} 不存在版本映射")

        # 1) 新规范编号 V<n>
        if text.startswith("V") and text[1:].isdigit():
            identity = int(text[1:])
            matched = [r for r in rows if int(r["版本身份"]) == identity]
            return self._unique(matched, golden_id, text)

        # 2) 精确身份（纯整数，且该身份已分配）——新序列边界
        if text.isdigit():
            identity = int(text)
            matched = [r for r in rows if int(r["版本身份"]) == identity]
            if matched:
                return self._unique(matched, golden_id, text)

        # 3) 旧编号写法（含 "10"、"10.0"、9.0 等历史标识）
        matched = [r for r in rows if text in self._aliases(r)]
        if matched:
            return self._unique(matched, golden_id, text)

        # 4) 浮点数值兜底（旧体系依赖边界）
        num = self.parse_legacy_float(text)
        if num is not None and float(num).is_integer():
            matched = [r for r in rows if int(r["版本身份"]) == int(num)]
            if matched:
                return self._unique(matched, golden_id, text)

        raise VersionMappingError(
            f"黄金ID {golden_id} 的版本引用 {text} 无法定位到任何版本映射"
        )

    @staticmethod
    def _unique(matched: list[dict[str, Any]], golden_id: str, ref: str) -> dict[str, Any]:
        if len(matched) > 1:
            identities = sorted(int(r["版本身份"]) for r in matched)
            raise VersionMappingError(
                f"黄金ID {golden_id} 的旧编号 {ref} 命中多个版本身份 {identities}，"
                "存在歧义，拒绝静默修订到另一版"
            )
        return matched[0]

    def resolve_identity(self, golden_id: str, version_ref: Any) -> int:
        return int(self.resolve_mapping(golden_id, version_ref)["版本身份"])

    # ───────────────────────── 依赖边界查询 ─────────────────────────

    def version_dependency_boundary(self, golden_id: str) -> dict[str, Any]:
        """输出旧编号体系与新序列的依赖边界，供审计核对。"""
        rows = sorted(self._rows_for(golden_id), key=lambda r: int(r["版本身份"]))
        legacy = [
            {
                "版本身份": int(r["版本身份"]),
                "规范版本编号": r["规范版本编号"],
                "旧编号写法": self._aliases(r),
                "编号体系": r["编号体系"],
            }
            for r in rows if r.get("编号体系", "").startswith("legacy")
        ]
        legacy_identities = [int(r["版本身份"]) for r in rows
                             if r.get("编号体系", "").startswith("legacy")]
        new_identities = [int(r["版本身份"]) for r in rows
                          if r.get("编号体系") == "new-sequence"]
        return {
            "golden_id": golden_id,
            "legacy_count": len(legacy),
            "legacy_identities": sorted(legacy_identities),
            "legacy_max_identity": max(legacy_identities, default=None),
            "new_sequence_identities": sorted(new_identities),
            "allocated_identities": [int(r["版本身份"]) for r in rows],
            "next_identity": self._next_identity(golden_id),
            "legacy_aliases": {r["规范版本编号"]: r["旧编号写法"] for r in legacy},
        }

    # ───────────────────────── 新序列分配与当前版本维护 ─────────────────────────

    def allocate_new_identity(self, golden_id: str, raw_ref: str | None = None) -> dict[str, Any]:
        """新提交分配精确版本身份：严格接续且不重用任何已分配身份。"""
        identity = self._next_identity(golden_id)
        rows = store.rows(self.MODULE)
        row = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "映射编号": f"VMAP-{golden_id}-{identity}",
            "黄金ID": golden_id,
            "版本身份": identity,
            "规范版本编号": self.canonical_code(identity),
            "原版本号": raw_ref or "",
            "旧编号写法": raw_ref or "",
            "编号体系": "new-sequence",
            "迁移批次": "",
            "映射时间": datetime.datetime.utcnow().isoformat(),
            "status": "当前版本",
            "pending": False,
            "abnormal": False,
        }
        rows.append(row)
        return row

    def mark_previous_history(self, golden_id: str) -> None:
        """把同一黄金ID此前的当前版本映射标记为历史版本。"""
        for row in self._rows_for(golden_id):
            if row.get("status") == "当前版本":
                row["status"] = "历史版本"

    def promote_current(self, golden_id: str, identity: int) -> dict[str, Any]:
        """把指定身份提升为当前版本，并同步黄金记录接口的当前版本关联。"""
        target = next(
            (r for r in self._rows_for(golden_id) if int(r["版本身份"]) == int(identity)),
            None,
        )
        if target is None:
            raise VersionMappingError(
                f"黄金ID {golden_id} 版本身份 {identity} 未在映射中登记"
            )
        self.mark_previous_history(golden_id)
        target["status"] = "当前版本"
        # 黄金记录接口关联映射保存当前版本；原「记录版本」字段不动
        for golden_row in store.rows("golden_record"):
            if golden_row.get("黄金ID") == golden_id:
                golden_row["当前版本身份"] = int(identity)
                golden_row["规范版本编号"] = target["规范版本编号"]
        return target
