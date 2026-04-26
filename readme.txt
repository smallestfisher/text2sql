说明：
1. 当前主业务说明来源已经迁移到 `business_knowledge.json`。PromptBuilder 会优先按 `domain / tables / keywords` 选择结构化知识块。

2. 本文件只作为 legacy fallback 文本说明保留。它不是仓库 README，也不是规则库，更不会参与本地 SQL 模板生成。

3. 本文件如需补充内容，优先写简短、稳定、可复用的中文业务说明；表名和字段名保持真实英文命名。

4. 如果遇到新的高频真实业务场景，优先补 `business_knowledge.json`、`tables.json`、few-shot 和 eval case，而不是在这里堆长段落。

5. 需求、库存、计划/实际等现有主线口径，请优先维护到 `business_knowledge.json`。
