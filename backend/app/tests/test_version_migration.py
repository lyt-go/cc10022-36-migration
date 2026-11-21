"""黄金版本号体系迁移验收测试。

覆盖需求场景：
1. 存量浮点递增版本（9.0、10.0）迁移到精确整数版本身份，建立黄金版本映射；
2. 同一条存量记录有两种旧版本号写法（"10" 与 "10.0"），运营仍用原版号
   核对地址历史——两种写法必须归并到同一版本身份；
3. 新编号不能重用同一黄金ID已分配的版本身份；
4. 既有真实可定位的版本仍按原请求读到原字段值（版本号/记录版本不被覆盖）；
5. 黄金记录接口关联映射保存当前版本，并按原编号提供历史数据；
6. 旧编号与新序列的依赖边界可查；歧义修订不能落到另一版；
7. 升级上线后分别读 9.0、10.0 和新编号数据，模拟一次回切，再核对原历史
   及新提交的字段版本身份。
"""
from __future__ import annotations

import os
import sys

_backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.services.golden_record import GoldenRecordService
from app.services.mdm_version import MdmVersionService
from app.services.version_mapping import (
    VersionMappingError,
    VersionMappingService,
)
from app.store import store


def _reset_legacy_scenario() -> str:
    """构造迁移前的存量环境：同一黄金记录的 9.0 / 10.0 两种旧写法。"""
    golden_id = "GR-LEGACY-ADDR"
    store._tables["mdm_version"] = [
        {
            "id": 101,
            "版本编号": f"VER-{golden_id}-9.0",
            "黄金ID": golden_id,
            # mdm_version 里该版写作 "10"（无小数点写法）
            "版本号": "10",
            "变更字段": "地址",
            "旧值摘要": "{'地址': '旧址A'}",
            "新值摘要": "{'地址': '新址B'}",
            "变更批次": golden_id,
            "版本时间": "2026-09-01T09:00:00",
            "status": "历史版本",
            "pending": False,
            "abnormal": False,
        },
        {
            "id": 100,
            "版本编号": f"VER-{golden_id}-9.0",
            "黄金ID": golden_id,
            "版本号": "9.0",
            "变更字段": "名称",
            "旧值摘要": "{}",
            "新值摘要": "{'名称': '华北仓'}",
            "变更批次": golden_id,
            "版本时间": "2026-08-01T09:00:00",
            "status": "历史版本",
            "pending": False,
            "abnormal": False,
        },
    ]
    store._tables["golden_record"] = [
        {
            "id": 200,
            "黄金ID": golden_id,
            "实体模型": "address",
            "关联来源": "SRC-ADDR-1",
            "关键字段": "地址",
            "代表字段": "{'地址': '新址B'}",
            "置信度": "0.90",
            "最后合并": "2026-09-01T09:00:00",
            # 黄金记录原字段里该版写作 "10.0"（另一种旧写法）
            "记录版本": "10.0",
            "status": "已生效",
            "pending": False,
            "abnormal": False,
        },
    ]
    store._tables["version_mapping"] = []
    return golden_id


class TestVersionMigration:
    def setup_method(self) -> None:
        self.golden_id = _reset_legacy_scenario()
        self.mapping_svc = VersionMappingService()
        self.version_svc = MdmVersionService()
        self.golden_svc = GoldenRecordService()

    # ── 1. 迁移建立映射，两种旧写法归并到同一身份 ──
    def test_migration_builds_mapping_and_merges_dual_spellings(self):
        report = self.mapping_svc.migrate_legacy_versions(batch="MIG-TEST")
        assert report["ok"] is True
        assert report["mapping_created"] == 2, report
        assert report["golden_records_linked"] == 1

        mappings = self.mapping_svc._rows_for(self.golden_id)
        identities = sorted(int(r["版本身份"]) for r in mappings)
        assert identities == [9, 10]

        # "10"（mdm_version 写法）与 "10.0"（golden_record 写法）归并到身份10
        map10 = self.mapping_svc.resolve_mapping(self.golden_id, "10")
        map10_decimal = self.mapping_svc.resolve_mapping(self.golden_id, "10.0")
        assert int(map10["版本身份"]) == int(map10_decimal["版本身份"]) == 10
        assert set(self.mapping_svc._aliases(map10)) == {"10", "10.0"}
        assert map10["规范版本编号"] == "V10"
        assert map10["编号体系"] == "legacy-float"

        map9 = self.mapping_svc.resolve_mapping(self.golden_id, "9.0")
        assert int(map9["版本身份"]) == 9 and map9["规范版本编号"] == "V9"

    # ── 2. 原字段保留：按原参数读到原字段值 ──
    def test_legacy_fields_remain_readable_by_original_params(self):
        self.mapping_svc.migrate_legacy_versions()

        ver_rows = {r["版本号"]: r for r in store.rows("mdm_version")
                    if r.get("黄金ID") == self.golden_id}
        # 原「版本号」字段一字未动
        assert ver_rows["9.0"]["版本号"] == "9.0"
        assert ver_rows["10"]["版本号"] == "10"
        # 同时回填了身份与规范编号
        assert ver_rows["9.0"]["版本身份"] == 9
        assert ver_rows["10"]["规范版本编号"] == "V10"

        golden = self.golden_svc.query_golden_record(self.golden_id)
        assert golden["记录版本"] == "10.0", "原记录版本字段不得被覆盖"
        assert golden["当前版本身份"] == 10
        assert golden["规范版本编号"] == "V10"

    # ── 3. 幂等：重复迁移不产生重复映射/身份 ──
    def test_migration_is_idempotent(self):
        first = self.mapping_svc.migrate_legacy_versions()
        second = self.mapping_svc.migrate_legacy_versions()
        assert first["mapping_created"] == 2
        assert second["mapping_created"] == 0
        mappings = self.mapping_svc._rows_for(self.golden_id)
        assert len(mappings) == 2

    # ── 4. 依赖边界可查 ──
    def test_dependency_boundary_is_queryable(self):
        self.mapping_svc.migrate_legacy_versions()
        boundary = self.mapping_svc.version_dependency_boundary(self.golden_id)
        assert boundary["legacy_identities"] == [9, 10]
        assert boundary["legacy_max_identity"] == 10
        assert boundary["allocated_identities"] == [9, 10]
        assert boundary["next_identity"] == 11
        assert boundary["legacy_aliases"]["V10"] == ["10", "10.0"]

    # ── 5. 新编号不重用已分配身份，并接续存量序列 ──
    def test_new_sequence_never_reuses_identity(self):
        self.mapping_svc.migrate_legacy_versions()
        snap = self.version_svc.snapshot_golden_version(
            self.golden_id, {"地址": "新址C", "联系人": "王五"},
        )
        assert snap["version_identity"] == 11
        assert snap["canonical_version"] == "V11"

        boundary = self.mapping_svc.version_dependency_boundary(self.golden_id)
        assert boundary["new_sequence_identities"] == [11]
        assert boundary["next_identity"] == 12

        # 再提交一版，身份继续递增，11 不被重用
        snap2 = self.version_svc.snapshot_golden_version(
            self.golden_id, {"地址": "新址D"},
        )
        assert snap2["version_identity"] == 12
        all_identities = [
            int(r["版本身份"])
            for r in self.mapping_svc._rows_for(self.golden_id)
        ]
        assert sorted(all_identities) == [9, 10, 11, 12]
        assert len(set(all_identities)) == len(all_identities)

        # 新快照黄金记录当前版本关联到新身份，原字段仍保留
        golden = self.golden_svc.query_golden_record(self.golden_id)
        assert golden["当前版本身份"] == 12
        assert golden["记录版本"] == "10.0"

    # ── 6. 上线后分别读 9.0、10.0 和新编号 ──
    def test_read_legacy_and_new_versions_after_upgrade(self):
        self.mapping_svc.migrate_legacy_versions()
        self.version_svc.snapshot_golden_version(
            self.golden_id, {"地址": "新址C"},
        )

        for ref, expected_identity in [("9.0", 9), ("10.0", 10), ("10", 10),
                                       ("V11", 11), ("11", 11)]:
            result = self.golden_svc.query_record_by_version(self.golden_id, ref)
            assert result["ok"] is True, f"{ref} 读取失败: {result}"
            assert result["version_identity"] == expected_identity
            assert result["legacy_record_version"] == "10.0"

        # 9.0 是真实可定位版本，返回其原始快照
        v9 = self.golden_svc.query_record_by_version(self.golden_id, "9.0")
        assert v9["snapshot"]["版本号"] == "9.0"
        assert v9["snapshot"]["变更字段"] == "名称"

        # 不存在的版本显式失败
        missing = self.golden_svc.query_record_by_version(self.golden_id, "99.0")
        assert missing["ok"] is False

    # ── 7. 歧义修订不能落到另一版 ──
    def test_ambiguous_alias_refuses_to_land_on_another_version(self):
        self.mapping_svc.migrate_legacy_versions()
        # 人为制造一对多歧义：把旧写法 "10.0" 再挂到身份 9 的映射
        map9 = self.mapping_svc.resolve_mapping(self.golden_id, "9.0")
        map9["旧编号写法"] = map9["旧编号写法"] + "|10.0"
        try:
            self.mapping_svc.resolve_mapping(self.golden_id, "10.0")
        except VersionMappingError as exc:
            assert "歧义" in str(exc)
        else:
            raise AssertionError("歧义旧编号必须报错，不得静默落到另一版")

        # 回退接口同样拒绝歧义参数
        rollback = self.version_svc.rollback_to_version(self.golden_id, "10.0")
        assert rollback["ok"] is False

    # ── 8. 模拟回切：新编号 -> 10.0，再核对历史与新提交身份 ──
    def test_rollback_simulation_and_recheck(self):
        self.mapping_svc.migrate_legacy_versions()
        snap11 = self.version_svc.snapshot_golden_version(
            self.golden_id, {"地址": "新址C"},
        )
        snap12 = self.version_svc.snapshot_golden_version(
            self.golden_id, {"地址": "新址D"},
        )
        assert snap11["version_identity"] == 11
        assert snap12["version_identity"] == 12

        # 用旧编号 10.0 发起回切（运营按原参数核对）
        rollback = self.version_svc.rollback_to_version(self.golden_id, "10.0")
        assert rollback["ok"] is True
        assert rollback["restored_identity"] == 10
        assert rollback["canonical_version"] == "V10"
        # 回切依据的原字段值原样回传
        assert rollback["legacy_version_field"] == "10"

        # 黄金记录当前版本关联回到身份10；原「记录版本」字段不变
        golden = self.golden_svc.query_golden_record(self.golden_id)
        assert golden["当前版本身份"] == 10
        assert golden["规范版本编号"] == "V10"
        assert golden["记录版本"] == "10.0"

        # 映射侧身份10为当前，11/12 历史可查且身份不丢失、不重用
        statuses = {
            int(r["版本身份"]): r["status"]
            for r in self.mapping_svc._rows_for(self.golden_id)
        }
        assert statuses[10] == "当前版本"
        assert statuses[11] in ("历史版本", "已回滚")
        assert statuses[12] in ("历史版本", "已回滚")

        # 原历史仍可按原编号读取
        v9 = self.golden_svc.query_record_by_version(self.golden_id, "9.0")
        assert v9["ok"] is True and v9["version_identity"] == 9
        v10 = self.golden_svc.query_record_by_version(self.golden_id, "10.0")
        assert v10["ok"] is True and v10["version_identity"] == 10
        # 新提交的字段版本身份仍可定位
        v11 = self.golden_svc.query_record_by_version(self.golden_id, "V11")
        assert v11["ok"] is True and v11["version_identity"] == 11

        # 回切后再提交，新身份接续为 13，绝不重用 9/10/11/12
        snap13 = self.version_svc.snapshot_golden_version(
            self.golden_id, {"地址": "新址E"},
        )
        assert snap13["version_identity"] == 13
        boundary = self.mapping_svc.version_dependency_boundary(self.golden_id)
        assert boundary["allocated_identities"] == [9, 10, 11, 12, 13]

    # ── 9. 新旧编号混传的版本对比 ──
    def test_compare_mixed_legacy_and_new_refs(self):
        self.mapping_svc.migrate_legacy_versions()
        self.version_svc.snapshot_golden_version(
            self.golden_id, {"地址": "新址C"},
        )
        report = self.version_svc.compare_versions(
            self.golden_id, "9.0", "V11",
        )
        assert report["ok"] is True
        assert report["identity_a"] == 9
        assert report["identity_b"] == 11

    # ── 10. 非整数浮点旧版号无法安全映射时显式报错 ──
    def test_non_integral_legacy_version_rejected(self):
        store.rows("mdm_version").append({
            "id": 999,
            "版本编号": f"VER-{self.golden_id}-9.5",
            "黄金ID": self.golden_id,
            "版本号": "9.5",
            "变更字段": "联系人",
            "旧值摘要": "",
            "新值摘要": "{'联系人': '赵六'}",
            "变更批次": self.golden_id,
            "版本时间": "2026-09-02T09:00:00",
            "status": "历史版本",
            "pending": False,
            "abnormal": False,
        })
        try:
            self.mapping_svc.migrate_legacy_versions()
        except VersionMappingError as exc:
            assert "9.5" in str(exc)
        else:
            raise AssertionError("非整数浮点版本必须显式报错，不得四舍五入到另一版")


if __name__ == "__main__":
    runner = TestVersionMigration()
    test_names = sorted(name for name in dir(runner) if name.startswith("test_"))
    passed = failed = 0
    for name in test_names:
        runner.setup_method()
        try:
            getattr(runner, name)()
            print(f"  PASS: {name}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {name} -> {exc}")
            failed += 1
    print(f"\n{'='*60}")
    print(f"结果: {passed}/{len(test_names)} 通过, {failed} 失败")
    assert failed == 0, f"{failed} 条迁移验收测试失败！"
