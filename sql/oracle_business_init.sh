#!/usr/bin/env bash
set -euo pipefail

sqlplus -L admin/admin123@//localhost:1521/FREEPDB1 @/opt/text2sql/sql/oracle_business_schema.sql
