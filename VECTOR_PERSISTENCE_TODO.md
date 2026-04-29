# 向量持久化待办

## 目标

把 retrieval corpus 的 embedding 从“服务启动时全量向量化并只保存在内存”改成“增量向量化并持久化到 runtime 库”，避免每次重启重复调用 embedding 模型。

当前阶段不做：

- 不引入独立向量数据库
- 不改检索排序逻辑
- 不改成 ANN 检索
- 不改 RetrievalService 的召回策略

## 现状

- `RetrievalService._refresh_indexes()` 会构建 `corpus_documents`
- `VectorRetriever.index_documents()` 会对全部文档调用 `_embed()`
- 向量结果只存在 `VectorRetriever.documents` 内存里
- 进程重启后需要重新对全部 corpus 做 embedding

## 实现原则

- 持久化的是 corpus embedding，不是 query embedding
- 启动时优先复用 runtime 库里未失效的向量
- 只有文档内容、embedding model 或 dimensions 变化时才重建
- 检索阶段继续使用内存 brute-force cosine search
- 先保证正确性和可维护性，再考虑向量库替换

## 待办项

### 1. Runtime 表结构

- 在 `sql/runtime_store.sql` 增加 `vector_corpus_documents` 表
- 表字段至少包含：
  - `document_id`
  - `source_type`
  - `source_id`
  - `summary`
  - `text_content`
  - `metadata_json`
  - `content_hash`
  - `embedding_provider`
  - `embedding_model`
  - `embedding_dimensions`
  - `vector_json`
  - `created_at`
  - `updated_at`
- 为 `source_type, source_id` 增加索引

### 2. Schema 初始化与升级

- 在 `RuntimeStoreInitializer` 中补 `vector_corpus_documents` 的建表和索引保证
- 如果后续字段调整，沿用现有 `_ensure_column()` / `_ensure_index()` 方式做增量升级

### 3. Repository 层

- 新增 `backend/app/repositories/db_vector_document_repository.py`
- 提供最少能力：
  - `list_all()`
  - `find_by_document_ids(document_ids)`
  - `upsert_documents(documents)`
  - `delete_missing(document_ids)`

### 4. 持久化同步服务

- 新增 `backend/app/services/vector_corpus_store_service.py`
- 职责：
  - 计算 `document_id`
  - 计算 `content_hash`
  - 对比库中已有文档
  - 识别 `new / changed / unchanged`
  - 仅对 `new / changed` 调用 embedding
  - 回写 runtime 库
  - 返回带 `vector` 的完整文档列表给内存检索层

### 5. VectorRetriever 职责收口

- 把 `VectorRetriever` 从“索引文档时顺便生成 embedding”改成两个明确职责：
  - `embed_text()` 或等价方法：只负责生成单条向量
  - `load_documents()`：只负责加载已有向量到内存
- 避免 `index_documents()` 同时承担 embedding 和内存索引装载

### 6. RetrievalService 接入

- 改 `RetrievalService._refresh_indexes()`
- 当前流程：
  - 生成 `corpus_documents`
  - 直接调用 `vector_retriever.index_documents(self.corpus_documents)`
- 目标流程：
  - 生成 `corpus_documents`
  - 调 `vector_corpus_store_service.sync(self.corpus_documents)`
  - 用 sync 返回结果装载内存检索文档

### 7. 文档失效策略

- `content_hash` 需要至少覆盖：
  - 文档 text
  - metadata_json
  - embedding model
  - embedding dimensions
- 任一项变化都要触发重建
- 已不存在于当前 corpus 的历史文档需要从持久化表删除

### 8. Reload 行为

- `RetrievalService.reload()` 也要走同一套增量同步逻辑
- 保证以下两种场景一致：
  - 服务启动
  - metadata / retrieval reload

### 9. 可观测性

- 在 `retrieval_service.health()` 或 admin runtime status 中补充：
  - persisted document count
  - reused document count
  - rebuilt document count
  - vector sync last updated time
- 同步阶段记录日志，至少包含：
  - corpus total
  - reused count
  - rebuilt count
  - deleted count

### 10. 验证项

- 首次启动时：
  - 全量 embedding
  - 持久化成功
- 二次启动时：
  - 无内容变化则不重复调用 embedding
- 修改 examples / business knowledge / tables metadata 后：
  - 仅增量重建受影响文档
- retrieval 结果在改造前后保持基本一致

## 预期收益

- 降低启动耗时
- 降低 embedding API 调用次数和成本
- 让多次重启、reload 的行为可预测
- 为后续切换到更大规模 corpus 或向量库保留演进空间

## 暂不做

- pgvector / Milvus / ES / FAISS 接入
- query embedding 缓存
- 多副本之间的分布式索引协调
- ANN 检索优化
