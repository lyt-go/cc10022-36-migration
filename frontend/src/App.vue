<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">主数据清洗与黄金记录平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向多源主数据的匹配识别、冲突消解、合并清洗与黄金记录管理的主数据治理平台。</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "来源系统注册", path: "/source_system" }, { label: "实体模型定义", path: "/entity_model" }, { label: "匹配规则引擎", path: "/match_rule" }, { label: "疑似重复簇", path: "/dup_cluster" }, { label: "合并追溯日志", path: "/merge_log" }, { label: "黄金记录管理", path: "/golden_record" }, { label: "字段标准化清洗", path: "/field_cleanse" }, { label: "优胜策略配置", path: "/survivorship_rule" }, { label: "数据入库规则", path: "/data_import_rule" }, { label: "批次导入管理", path: "/import_batch" }, { label: "记录级血缘", path: "/record_lineage" }, { label: "主数据分发同步", path: "/mdm_sync" }, { label: "黄金记录版本", path: "/mdm_version" }, { label: "黄金版本映射", path: "/version_mapping" }, { label: "自动匹配任务", path: "/auto_match_job" }, { label: "主数据质量统计", path: "/mdm_quality" }, { label: "参考数据管理", path: "/mdm_reference" }, { label: "主数据查询接口", path: "/mdm_api" }, { label: "主数据治理看板", path: "/mdm_dashboard" }]
</script>
