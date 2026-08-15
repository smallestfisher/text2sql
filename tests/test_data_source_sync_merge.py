from __future__ import annotations

import unittest

from backend.app.services.oracle_schema_introspector import merge_tables_metadata


SOURCE_ID = "ds_abc"


def _physical(name: str, *, columns: list[str], pk: str = "", fks: dict | None = None) -> dict:
    # Real callers (the sync endpoint) tag each introspected table with the
    # source id, dialect and workspace/domain before merging — mirror that here.
    return {
        "schema": "ADMIN",
        "table_name": name.split(".")[-1],
        "description": "",
        "columns": columns,
        "MAIN_KEY": pk,
        "relationships": fks or {},
        "source": "oracle_introspection",
        "data_source_id": SOURCE_ID,
        "dialect": "oracle",
    }


def _business(name: str, *, data_source_id: str = SOURCE_ID) -> dict:
    return {
        "description": f"{name} 业务说明（人工）",
        "columns": ["id", "MONTH (需求起始月份)", "QTY (需求数量)"],
        "MAIN_KEY": "id,MONTH",
        "time_fields": {"MONTH": {"grain": "month", "format": "YYYYMM"}},
        "month_col": "MONTH",
        "version_col": "PM_VERSION",
        "relationships": {"QTY": "product_attributes.product_ID"},
        "data_source_id": data_source_id,
    }


class MergeTablesMetadataTests(unittest.TestCase):
    def test_new_table_becomes_placeholder_with_empty_business_fields(self) -> None:
        existing: dict = {}
        introspected = {f"{SOURCE_ID}.v_demand": _physical("v_demand", columns=["id", "MONTH"], pk="id")}
        merged, warnings = merge_tables_metadata(existing, introspected, SOURCE_ID)

        for field in ("description", "time_fields", "month_col", "version_col", "relationships"):
            self.assertNotIn(field, merged[f"{SOURCE_ID}.v_demand"])
        self.assertEqual(merged["ds_abc.v_demand"]["columns"], ["id", "MONTH"])
        self.assertEqual(merged["ds_abc.v_demand"]["MAIN_KEY"], "id")
        self.assertEqual(merged["ds_abc.v_demand"]["data_source_id"], SOURCE_ID)
        self.assertTrue(any("新增表" in w for w in warnings))

    def test_existing_business_fields_are_never_overwritten(self) -> None:
        name = f"{SOURCE_ID}.v_demand"
        existing = {name: _business(name)}
        introspected = {
            name: _physical("v_demand", columns=["id", "MONTH", "NEW_COL"], pk="id", fks={"FK": "other.t.x"})
        }
        merged, warnings = merge_tables_metadata(existing, introspected, SOURCE_ID)
        entry = merged[name]

        # Human authored business content survives untouched.
        self.assertEqual(entry["description"], "ds_abc.v_demand 业务说明（人工）")
        self.assertEqual(entry["columns"], ["id", "MONTH (需求起始月份)", "QTY (需求数量)"])
        self.assertEqual(entry["MAIN_KEY"], "id,MONTH")  # human PK kept
        self.assertEqual(entry["time_fields"], {"MONTH": {"grain": "month", "format": "YYYYMM"}})
        self.assertEqual(entry["month_col"], "MONTH")
        self.assertEqual(entry["version_col"], "PM_VERSION")
        self.assertEqual(entry["relationships"], {"QTY": "product_attributes.product_ID"})

        # Physical facts were refreshed.
        self.assertEqual(entry["schema"], "ADMIN")
        self.assertEqual(entry["table_name"], "v_demand")
        self.assertEqual(entry["source"], "oracle_introspection")

        # Drift surfaced as warnings instead of mutating the asset.
        self.assertTrue(any("新增列" in w and "NEW_COL" in w for w in warnings))
        self.assertTrue(any("物理外键" in w for w in warnings))

    def test_main_key_is_backfilled_only_when_human_left_empty(self) -> None:
        name = f"{SOURCE_ID}.t"
        existing = {name: {"columns": ["a", "b"], "data_source_id": SOURCE_ID, "MAIN_KEY": ""}}
        introspected = {name: _physical("t", columns=["a", "b"], pk="a,b")}
        merged, _ = merge_tables_metadata(existing, introspected, SOURCE_ID)
        self.assertEqual(merged[name]["MAIN_KEY"], "a,b")

        # When human already set it, introspection does not override.
        existing[name]["MAIN_KEY"] = "a"
        merged, _ = merge_tables_metadata(existing, introspected, SOURCE_ID)
        self.assertEqual(merged[name]["MAIN_KEY"], "a")

    def test_physical_foreign_keys_do_not_become_relationships(self) -> None:
        name = f"{SOURCE_ID}.t"
        existing = {name: {"columns": ["a"], "data_source_id": SOURCE_ID, "relationships": {"a": "biz.table"}}}
        introspected = {name: _physical("t", columns=["a"], fks={"a": "other.t.a"})}
        merged, warnings = merge_tables_metadata(existing, introspected, SOURCE_ID)

        self.assertEqual(merged[name]["relationships"], {"a": "biz.table"})
        self.assertTrue(any("物理外键" in w and "other.t.a" in w for w in warnings))

    def test_tables_no_longer_introspected_are_warned_not_deleted(self) -> None:
        removed = f"{SOURCE_ID}.dropped"
        kept = f"{SOURCE_ID}.kept"
        existing = {removed: _business(removed), kept: _business(kept)}
        introspected = {kept: _physical("kept", columns=["id"])}
        merged, warnings = merge_tables_metadata(existing, introspected, SOURCE_ID)

        self.assertIn(removed, merged)  # preserved
        self.assertTrue(any("未在本次同步中读到" in w and "dropped" in w for w in warnings))

    def test_tables_owned_by_other_sources_untouched(self) -> None:
        mine = f"{SOURCE_ID}.mine"
        others = "ds_other.theirs"
        existing = {
            mine: _business(mine),
            others: {"description": "other source table", "data_source_id": "ds_other"},
        }
        introspected = {mine: _physical("mine", columns=["id"], pk="id")}
        merged, _ = merge_tables_metadata(existing, introspected, SOURCE_ID)

        self.assertEqual(merged[others], {"description": "other source table", "data_source_id": "ds_other"})
        # My table got physical refresh + business preserved.
        self.assertEqual(merged[mine]["schema"], "ADMIN")
        self.assertEqual(merged[mine]["columns"], ["id", "MONTH (需求起始月份)", "QTY (需求数量)"])

    def test_refresh_readds_business_neighbors_when_existing_empty_columns(self) -> None:
        name = f"{SOURCE_ID}.t"
        existing = {name: {"data_source_id": SOURCE_ID, "columns": []}}
        introspected = {name: _physical("t", columns=["a", "b"], pk="a")}
        merged, _ = merge_tables_metadata(existing, introspected, SOURCE_ID)
        self.assertEqual(merged[name]["columns"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
