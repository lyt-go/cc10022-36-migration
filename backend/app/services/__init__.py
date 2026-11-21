"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.source_system import SourceSystemService
from app.services.entity_model import EntityModelService
from app.services.match_rule import MatchRuleService
from app.services.dup_cluster import DupClusterService
from app.services.merge_log import MergeLogService
from app.services.golden_record import GoldenRecordService
from app.services.field_cleanse import FieldCleanseService
from app.services.survivorship_rule import SurvivorshipRuleService
from app.services.data_import_rule import DataImportRuleService
from app.services.import_batch import ImportBatchService
from app.services.record_lineage import RecordLineageService
from app.services.mdm_sync import MdmSyncService
from app.services.mdm_version import MdmVersionService
from app.services.version_mapping import VersionMappingService
from app.services.auto_match_job import AutoMatchJobService
from app.services.mdm_quality import MdmQualityService
from app.services.mdm_reference import MdmReferenceService
from app.services.mdm_api import MdmApiService
from app.services.mdm_dashboard import MdmDashboardService

SERVICES: dict[str, BaseModuleService] = {
    "source_system": SourceSystemService(),
    "entity_model": EntityModelService(),
    "match_rule": MatchRuleService(),
    "dup_cluster": DupClusterService(),
    "merge_log": MergeLogService(),
    "golden_record": GoldenRecordService(),
    "field_cleanse": FieldCleanseService(),
    "survivorship_rule": SurvivorshipRuleService(),
    "data_import_rule": DataImportRuleService(),
    "import_batch": ImportBatchService(),
    "record_lineage": RecordLineageService(),
    "mdm_sync": MdmSyncService(),
    "mdm_version": MdmVersionService(),
    "version_mapping": VersionMappingService(),
    "auto_match_job": AutoMatchJobService(),
    "mdm_quality": MdmQualityService(),
    "mdm_reference": MdmReferenceService(),
    "mdm_api": MdmApiService(),
    "mdm_dashboard": MdmDashboardService(),
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
