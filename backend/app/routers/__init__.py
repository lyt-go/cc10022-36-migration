"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource, version_mapping

# 专用迁移路由必须先注册，固定前缀 /api/version-mapping 不会被
# /api/{module} 动态资源路由抢占。
ROUTERS = [version_mapping, resource]
