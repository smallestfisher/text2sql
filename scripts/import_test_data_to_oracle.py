from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import ZipFile
import argparse
import re
import xml.etree.ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCEL_PATH = REPO_ROOT / "test_data.xlsx"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "sql" / "oracle_test_data.sql"

XML_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

SHEET_TO_TABLE = {
    "V_Demand": "V_DEMAND",
    "P_Demand": "P_DEMAND",
    "Product_map": "PRODUCT_MAPPING",
    "Product_attributes": "PRODUCT_ATTRIBUTES",
    "Daily_plan": "DAILY_PLAN",
    "monthly_plan_approved": "MONTHLY_PLAN_APPROVED",
    "weekly_rolling_plan": "WEEKLY_ROLLING_PLAN",
    "sales_financial_perf": "SALES_FINANCIAL_PERF",
    "Production_actuals": "PRODUCTION_ACTUALS",
}

TABLE_COLUMNS = {
    "V_DEMAND": [
        "PM_VERSION",
        "FGCODE",
        "SBU_DESC",
        "CUSTOMER",
        "MONTH",
        "REQUIREMENT_QTY",
        "NEXT_REQUIREMENT",
        "LAST_REQUIREMENT",
        "MONTH4",
        "MONTH5",
        "MONTH6",
        "MONTH7",
    ],
    "P_DEMAND": [
        "PM_VERSION",
        "FGCODE",
        "SBU_DESC",
        "BU_DESC",
        "CUSTOMER",
        "MONTH",
        "REQUIREMENT_QTY",
        "NEXT_REQUIREMENT",
        "LAST_REQUIREMENT",
        "MONTH4",
        "MONTH5",
        "MONTH6",
        "MONTH7",
    ],
    "PRODUCT_MAPPING": ["FGCODE", "CELL_NO", "ARRAY_NO", "CF_NO"],
    "PRODUCT_ATTRIBUTES": [
        "PRODUCT_ID",
        "APPLICATION",
        "CUT_NUM",
        "COMMON_CATEGORIES",
        "IS_OXIDE",
        "IS_XPS",
        "IS_SLOC",
        "IS_COATER",
        "IS_OA",
        "IS_NOTCH",
    ],
    "DAILY_PLAN": [
        "PLAN_DATE",
        "FACTORY_CODE",
        "PRODUCT_ID",
        "TARGET_IN_GLASS_QTY",
        "TARGET_IN_PANEL_QTY",
        "TARGET_OUT_GLASS_QTY",
        "TARGET_OUT_PANEL_QTY",
        "TARGET_OUT_TTL_PANEL_QTY",
    ],
    "MONTHLY_PLAN_APPROVED": [
        "PLAN_MONTH",
        "PLAN_DATE",
        "FACTORY_CODE",
        "PRODUCT_ID",
        "TARGET_IN_GLASS_QTY",
        "TARGET_IN_PANEL_QTY",
        "TARGET_OUT_GLASS_QTY",
        "TARGET_OUT_PANEL_QTY",
        "TARGET_OUT_TTL_PANEL_QTY",
    ],
    "WEEKLY_ROLLING_PLAN": ["PM_VERSION", "PLAN_DATE", "FACTORY", "PRODUCT_ID", "PLAN_QTY"],
    "SALES_FINANCIAL_PERF": [
        "REPORT_MONTH",
        "SBU_DESC",
        "BU_DESC",
        "CUSTOMER",
        "FGCODE",
        "SALES_QTY",
        "FINANCIAL_QTY",
    ],
    "PRODUCTION_ACTUALS": [
        "WORK_DATE",
        "FACTORY",
        "PRODUCT_ID",
        "ACT_TYPE",
        "GLS_QTY",
        "PANEL_QTY",
        "DEFECT_QTY",
    ],
}

HEADER_ALIASES = {
    "CELL NO": "CELL_NO",
    "ARRAY NO": "ARRAY_NO",
    "CF NO": "CF_NO",
    "GLS_QTY": "GLS_QTY",
}

SHEET_HEADER_ROW = {"Daily_plan": 2}


@dataclass(frozen=True)
class Worksheet:
    name: str
    rows: list[list[object | None]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Oracle SQLPlus inserts from test_data.xlsx.")
    parser.add_argument("--excel", default=str(DEFAULT_EXCEL_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    return parser.parse_args()


def column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter.upper()) - ord("A") + 1
    return index - 1


def excel_serial_to_yyyymmdd(value: object | None) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        return text
    try:
        serial = int(Decimal(text))
    except (InvalidOperation, ValueError):
        return text
    date_value = datetime(1899, 12, 30) + timedelta(days=serial)
    return date_value.strftime("%Y%m%d")


def normalize_header(value: object | None) -> str:
    header = re.sub(r"\s+", " ", str(value or "").strip())
    normalized = header.upper().replace(" ", "_")
    return HEADER_ALIASES.get(header.upper(), normalized)


def normalize_value(table_name: str, column_name: str, value: object | None) -> object | None:
    if value in (None, ""):
        return None
    if column_name in {"PLAN_DATE", "WORK_DATE"} or (table_name == "WEEKLY_ROLLING_PLAN" and column_name == "PLAN_DATE"):
        return excel_serial_to_yyyymmdd(value)
    if column_name in {
        "TARGET_IN_GLASS_QTY",
        "TARGET_IN_PANEL_QTY",
        "TARGET_OUT_GLASS_QTY",
        "TARGET_OUT_PANEL_QTY",
        "TARGET_OUT_TTL_PANEL_QTY",
        "REQUIREMENT_QTY",
        "NEXT_REQUIREMENT",
        "LAST_REQUIREMENT",
        "MONTH4",
        "MONTH5",
        "MONTH6",
        "MONTH7",
        "CUT_NUM",
        "PLAN_QTY",
        "SALES_QTY",
        "FINANCIAL_QTY",
        "GLS_QTY",
        "PANEL_QTY",
        "DEFECT_QTY",
    }:
        return int(Decimal(str(value)).to_integral_value())
    return str(value).strip()


def load_workbook(path: Path) -> list[Worksheet]:
    with ZipFile(path) as xlsx:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in xlsx.namelist():
            shared_root = ET.fromstring(xlsx.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("main:si", XML_NS):
                shared_strings.append("".join(text.text or "" for text in item.findall(".//main:t", XML_NS)))

        workbook_root = ET.fromstring(xlsx.read("xl/workbook.xml"))
        rels_root = ET.fromstring(xlsx.read("xl/_rels/workbook.xml.rels"))
        rels = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels_root.findall("pkgrel:Relationship", XML_NS)}

        worksheets: list[Worksheet] = []
        for sheet in workbook_root.findall("main:sheets/main:sheet", XML_NS):
            name = sheet.attrib["name"]
            rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = rels[rel_id]
            sheet_path = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
            root = ET.fromstring(xlsx.read(sheet_path))
            rows: list[list[object | None]] = []
            for xml_row in root.findall(".//main:sheetData/main:row", XML_NS):
                values: list[object | None] = []
                for cell in xml_row.findall("main:c", XML_NS):
                    ref = cell.attrib.get("r", "A1")
                    while len(values) <= column_index(ref):
                        values.append(None)
                    value_node = cell.find("main:v", XML_NS)
                    inline_node = cell.find("main:is/main:t", XML_NS)
                    if value_node is None:
                        value: object | None = inline_node.text if inline_node is not None else None
                    else:
                        raw = value_node.text or ""
                        value = shared_strings[int(raw)] if cell.attrib.get("t") == "s" else raw
                    values[column_index(ref)] = value
                rows.append(values)
            worksheets.append(Worksheet(name=name, rows=rows))
    return worksheets


def rows_for_table(worksheet: Worksheet) -> tuple[str, list[str], list[dict[str, object | None]]]:
    table_name = SHEET_TO_TABLE[worksheet.name]
    header_row_number = SHEET_HEADER_ROW.get(worksheet.name, 1)
    header_row_index = header_row_number - 1
    headers = [normalize_header(value) for value in worksheet.rows[header_row_index]]

    if worksheet.name == "Daily_plan":
        headers = ["PLAN_DATE", "FACTORY_CODE", "PRODUCT_ID", "TARGET_IN_GLASS_QTY", "TARGET_IN_PANEL_QTY", "TARGET_OUT_GLASS_QTY", "TARGET_OUT_PANEL_QTY", "TARGET_OUT_TTL_PANEL_QTY"]
    if worksheet.name == "monthly_plan_approved":
        headers[0] = "PLAN_MONTH"

    target_columns = TABLE_COLUMNS[table_name]
    records: list[dict[str, object | None]] = []
    for raw_row in worksheet.rows[header_row_index + 1 :]:
        raw_mapping = {headers[index]: raw_row[index] if index < len(raw_row) else None for index in range(len(headers))}
        record = {
            column: normalize_value(table_name, column, raw_mapping.get(column))
            for column in target_columns
        }
        if any(value not in (None, "") for value in record.values()):
            records.append(record)
    return table_name, target_columns, records


def sql_literal(value: object | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    return "'" + text.replace("'", "''") + "'"


def render_insert(table_name: str, columns: list[str], record: dict[str, object | None]) -> str:
    column_expr = ", ".join(columns)
    value_expr = ", ".join(sql_literal(record[column]) for column in columns)
    return f"INSERT INTO {table_name} ({column_expr}) VALUES ({value_expr});"


def main() -> None:
    args = parse_args()
    worksheets = load_workbook(Path(args.excel))
    mapped = [rows_for_table(sheet) for sheet in worksheets if sheet.name in SHEET_TO_TABLE]
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    statements = [
        "SET DEFINE OFF",
        "WHENEVER SQLERROR EXIT SQL.SQLCODE",
        "",
    ]
    inserted_counts: dict[str, int] = {}
    for table_name, columns, records in mapped:
        inserted_counts[table_name] = len(records)
        statements.append(f"PROMPT Loading {table_name}")
        statements.extend(render_insert(table_name, columns, record) for record in records)
        statements.append("")
    statements.extend(["COMMIT;", "EXIT", ""])
    output_path.write_text("\n".join(statements), encoding="utf-8")

    print(f"wrote {output_path}")
    for table_name, count in inserted_counts.items():
        print(f"{table_name}: {count} rows")


if __name__ == "__main__":
    main()
