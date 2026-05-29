# 文档目录

这里放当前事实文档。根目录 [README.md](../README.md) 只保留快速启动和导航。

## 当前文档

- [ARCHITECTURE.md](ARCHITECTURE.md)：系统架构、核心对象、查询链路、retrieval、SQL 治理、runtime 落库。
- [DEBUG_PLAYBOOK.md](DEBUG_PLAYBOOK.md)：真实问题答错时的排查路径、trace/replay/materialize/eval 使用方式。
- [CONTENT_GUIDELINES.md](CONTENT_GUIDELINES.md)：`examples` 样例和 `business_knowledge` 知识库内容的编写规范。
- [../backend/README.md](../backend/README.md)：后端运行、配置、API 分组、runtime 存储。
- [../frontend/README.md](../frontend/README.md)：前端工作台、数据加载方式、主要 API 依赖。

## 维护规则

- 当前事实只写在当前文档里；过期阶段计划直接删除。
- 根 README 不放架构细节和完整 API 清单。
- 后端 API 与运行方式放 `backend/README.md`。
- 调试方法放 `DEBUG_PLAYBOOK.md`，不要散落在多个文档里。
