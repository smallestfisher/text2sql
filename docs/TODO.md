# 工程待办

本文只记录当前代码尚未完成的增强项和验证缺口。已放弃的多数据源、MySQL runtime、文件语义配置和兼容层不会恢复。

状态核对日期：2026-08-16。

## 已完成的架构收敛

- [x] 单实例只创建一个由 `BUSINESS_DATABASE_URL` 配置的 Oracle 业务连接器。
- [x] 删除 workspace/domain/data source 多租户模型、连接凭据存储和“创建数据源”UI。
- [x] runtime store 收敛为 SQLite/WAL，部署配置只来自环境变量或 Secret。
- [x] 物理目录支持 Oracle Schema 同步、catalog hash、漂移提示和幂等合并，不覆盖人工语义。
- [x] 四类语义草稿保存到 SQLite，并使用 optimistic version 防止并发覆盖。
- [x] 发布生成包含 catalog hash、draft version 和 asset schema version 的不可变 snapshot。
- [x] 发布执行结构、引用和样例 SQL 校验；检索语料准备完成后才原子激活，失败不切换 active release。
- [x] 会话绑定 `semantic_release_id`，retrieval、prompt、validator、example 和向量缓存都按该 release 加载。
- [x] 删除 `semantic_assets`、`app_config`、数据库向量表以及仓库语义 JSON 的生产双读/fallback。
- [x] 删除生产前后端中的固定业务域枚举、表名推断和具体业务表名分支。
- [x] 时间字段的 `grain`、`format` 和 `semantic_names` 由发布版本配置，并可在 Semantic Studio 中编辑。
- [x] 空 runtime 首次启动不会导入模拟资产，必须 Schema 同步后由用户显式发布 v1。

## 语义建模增强

- [ ] 为指标公式、聚合方式、去重粒度和默认过滤增加独立的结构化 schema 与编辑器。目前可通过业务知识和样例表达，但缺少专门模型。
- [ ] 为枚举值、自然语言同义词和数据库值映射增加结构化编辑器与引用校验。
- [ ] 为表/字段启停和“允许查询范围”提供显式开关，代替当前通过草稿内容增删控制。
- [ ] 把结构漂移从同步 warning 升级为可逐项确认的 UI 工作流。
- [ ] 增加高风险语义缺口提示，例如事实表无时间语义、多表关系无 join pattern、关键知识无关键词。

## 发布与评测

- [ ] 在发布流程中自动运行选定 eval 集，并把结果写入 release；当前 eval 由管理员独立触发。
- [ ] 增加 release diff API 和 UI，展示表字段、知识、join pattern、样例及 catalog hash 的变化。
- [ ] 增加发布原子性、失败不影响 active release、并发发布和会话版本隔离的专门集成测试。
- [ ] 在 UI 中关联发布版本、评测运行和失败原因，支持从发布历史直接重跑。

## 查询准确率

- [ ] 为真实业务上线前建立不含模拟名称的客户验收集，覆盖其表结构、业务词、时间口径和高频问题。
- [ ] 配置可用的 embedding 服务并验证 keyword/vector 融合分数；未配置时系统会降级到 BM25。
- [ ] 持续补充“错误问题 -> trace 定位 -> draft 修复 -> 发布 -> replay/eval”的回归闭环。
- [ ] 建立按 subject domain、问题类型和失败层级统计的准确率看板。

## 最终验证

- [x] 后端全量单元测试通过：183 tests，2 skipped。
- [x] `python3 backend/domain_config_lint.py` 通过。
- [x] 前端 `npm run build` 通过。
- [x] Compose 后端镜像可构建，`GET /health` 返回正常。
- [x] README、架构、项目指南和维护约束已按 release-only 运行时更新。
- [ ] 自动化覆盖“同步 -> 配置 -> 发布 -> 新建会话 -> 查询”的完整端到端流程。
- [ ] 自动化覆盖 Oracle 连接切换后的全新 runtime 初始化流程。
- [ ] 补充桌面和移动视口的浏览器回归，确保 Semantic Studio 表单、历史列表和错误信息不重叠。

## 暂不实施

- 多业务数据库动态注册和请求级切换。
- workspace/data source 多租户权限模型。
- 跨数据库或跨实例联邦查询。
- 在线迁移旧会话和旧语义版本。
- 为旧 API、旧 runtime schema 或仓库 JSON 配置提供兼容适配器。
