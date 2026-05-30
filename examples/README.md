# NL2SQL 样例

完整维护规范见 [../docs/CONTENT_GUIDELINES.md](../docs/CONTENT_GUIDELINES.md)。这里保留样例文件的字段速查。

`nl2sql_examples.template.json` 使用轻量样例格式。必填字段只有：

- `question`: 用户自然语言问题。
- `sql`: 对应的 Oracle 只读查询，建议包含 `FETCH FIRST n ROWS ONLY`。

可选字段用于覆盖自动推断结果：

- `id`: 稳定样例 ID。不填时后端会生成。
- `subject_domain`: 业务域。不填时后端会先按问题推断，再按 SQL 表名反推。
- `metrics`: 该样例覆盖的指标名列表。
- `dimensions`: 该样例覆盖的维度列表。
- `tags`: 检索标签，例如 `approved_vs_actual`、`aggregate_then_join`。
- `notes`: 给 SQL 生成模型看的简短业务提醒。
- `result_shape`: 结果形态。不填时按维度自动推断。

示例：

```json
{
  "question": "2026年2月Array工厂审批版投入物量与实际物量Gap和达成率",
  "sql": "WITH approved_agg AS (...) SELECT ... FETCH FIRST 200 ROWS ONLY",
  "metrics": ["approved_input_panel_qty", "actual_input_panel_qty", "input_panel_gap_qty", "input_panel_achievement_rate"],
  "dimensions": ["factory"],
  "tags": ["approved_vs_actual", "aggregate_then_join"],
  "notes": "审批和实际先各自聚合，再按工厂合并，避免明细 join 放大量。"
}
```

后端会在加载时自动派生 `question_type`、`tables`、`filters`、`join_path` 等运行字段。样例 SQL 进入 prompt 前会做安全检查；非 Oracle 方言或非单条 `SELECT` / `WITH ... SELECT` 查询不会作为 few-shot SQL 暴露给模型。
