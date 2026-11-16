"""Deterministic SQL and graph-based validation engine for ERP extracts."""

from __future__ import annotations

import re

import duckdb
import pandas as pd

from planpilot.erp.models import (
    ErrorSeverity,
    InjectedErrorType,
    ValidationError,
    ValidationReport,
)


class DeterministicValidator:
    """Validates raw or mapped ERP tables using DuckDB SQL and graph algorithms."""

    VALID_TIME_UNITS = {
        "MIN",
        "MINS",
        "MINUTE",
        "MINUTES",
        "HR",
        "H",
        "HRS",
        "HOUR",
        "HOURS",
        "STD",
        "S",
        "SEC",
        "SECS",
        "SECOND",
        "SECONDS",
    }

    def __init__(self) -> None:
        pass

    def validate(
        self, tables: dict[str, pd.DataFrame], is_sap: bool | None = None
    ) -> ValidationReport:
        """Run all deterministic validations against the provided tables."""
        if is_sap is None:
            is_sap = "CRHD" in tables or "PLPO" in tables

        con = duckdb.connect(database=":memory:")
        for name, df in tables.items():
            if len(df.columns) > 0:
                con.register(name, df)

        errors: list[ValidationError] = []

        # Determine table names
        wc_table = "CRHD" if is_sap else "stations"
        mat_table = "MARA" if is_sap else "items"
        routing_table = "PLPO" if is_sap else "routings"
        bom_table = "STPO" if is_sap else "boms"
        order_table = "AFKO" if is_sap else "work_orders"

        # Determine column names
        wc_id_col = "ARBPL" if is_sap else "station_id"
        mat_id_col = "MATNR" if is_sap else "item_code"
        routing_id_col = "PLNNR" if is_sap else "routing_id"
        op_seq_col = "VORNR" if is_sap else "op_seq"
        routing_wc_col = "ARBPL" if is_sap else "station_id"
        setup_time_col = "VGW01" if is_sap else "setup_mins"
        setup_unit_col = "VGE01" if is_sap else "setup_uom"
        run_time_col = "VGW02" if is_sap else "run_mins"
        run_unit_col = "VGE02" if is_sap else "run_uom"

        bom_parent_col = "MATNR" if is_sap else "parent_item"
        bom_child_col = "IDNRK" if is_sap else "child_item"

        order_id_col = "AUFNR" if is_sap else "order_id"
        order_start_col = "GSTRP" if is_sap else "release_date"
        order_due_col = "GLTRP" if is_sap else "due_date"

        # 1. Check Missing Mandatory Fields
        mandatory_checks = [
            (wc_table, [wc_id_col]),
            (mat_table, [mat_id_col]),
            (routing_table, [routing_id_col, op_seq_col, routing_wc_col]),
            (bom_table, [bom_parent_col, bom_child_col]),
            (order_table, [order_id_col]),
        ]
        for tbl, cols in mandatory_checks:
            if tbl in tables:
                df = tables[tbl]
                for col in cols:
                    if col in df.columns:
                        for row_idx, val in enumerate(df[col]):
                            if pd.isna(val) or str(val).strip() == "":
                                errors.append(
                                    ValidationError(
                                        error_type=InjectedErrorType.MISSING_MANDATORY_FIELD,
                                        severity=ErrorSeverity.CRITICAL,
                                        table=tbl,
                                        row_idx=row_idx,
                                        column=col,
                                        message=f"Mandatory field '{col}' is missing or blank",
                                        value=val,
                                    )
                                )

        # 2. Check Whitespace / Case Inconsistencies
        id_columns = [
            (mat_table, mat_id_col),
            (wc_table, wc_id_col),
            (routing_table, routing_wc_col),
            (bom_table, bom_parent_col),
            (bom_table, bom_child_col),
        ]
        for tbl, col in id_columns:
            if tbl in tables and col in tables[tbl].columns:
                df = tables[tbl]
                for row_idx, val in enumerate(df[col]):
                    if isinstance(val, str) and (val != val.strip() or re.search(r"\s{2,}", val)):
                        errors.append(
                            ValidationError(
                                error_type=InjectedErrorType.WHITESPACE_CASE_INCONSISTENT,
                                severity=ErrorSeverity.INFO,
                                table=tbl,
                                row_idx=row_idx,
                                column=col,
                                message=f"Value '{val}' contains leading/trailing whitespace",
                                value=val,
                            )
                        )

        # 3. Check Dangling Work Centers in Routings
        if (
            routing_table in tables
            and wc_table in tables
            and routing_wc_col in tables[routing_table].columns
            and wc_id_col in tables[wc_table].columns
        ):
            # Known work centers
            known_wcs = {
                str(w).strip().upper()
                for w in tables[wc_table][wc_id_col]
                if pd.notna(w) and str(w).strip() != ""
            }
            df_r = tables[routing_table]
            for row_idx, val in enumerate(df_r[routing_wc_col]):
                if pd.notna(val) and str(val).strip() != "":
                    cleaned = str(val).strip().upper()
                    if cleaned not in known_wcs:
                        errors.append(
                            ValidationError(
                                error_type=InjectedErrorType.DANGLING_WORK_CENTER,
                                severity=ErrorSeverity.CRITICAL,
                                table=routing_table,
                                row_idx=row_idx,
                                column=routing_wc_col,
                                message=f"Work center '{val}' does not exist in '{wc_table}'",
                                value=val,
                            )
                        )

        # 4. Check Orphan Components in BOM
        if (
            bom_table in tables
            and mat_table in tables
            and bom_child_col in tables[bom_table].columns
            and mat_id_col in tables[mat_table].columns
        ):
            known_mats = {
                str(m).strip().upper()
                for m in tables[mat_table][mat_id_col]
                if pd.notna(m) and str(m).strip() != ""
            }
            df_b = tables[bom_table]
            for row_idx, val in enumerate(df_b[bom_child_col]):
                if pd.notna(val) and str(val).strip() != "":
                    cleaned = str(val).strip().upper()
                    if cleaned not in known_mats:
                        errors.append(
                            ValidationError(
                                error_type=InjectedErrorType.ORPHAN_COMPONENT,
                                severity=ErrorSeverity.CRITICAL,
                                table=bom_table,
                                row_idx=row_idx,
                                column=bom_child_col,
                                message=f"BOM component '{val}' does not exist in '{mat_table}'",
                                value=val,
                            )
                        )

        # 5. Check Zero / Negative Durations
        if routing_table in tables:
            df_r = tables[routing_table]
            if setup_time_col in df_r.columns:
                for row_idx, val in enumerate(df_r[setup_time_col]):
                    if pd.notna(val) and float(val) < 0:
                        errors.append(
                            ValidationError(
                                error_type=InjectedErrorType.ZERO_NEGATIVE_DURATION,
                                severity=ErrorSeverity.CRITICAL,
                                table=routing_table,
                                row_idx=row_idx,
                                column=setup_time_col,
                                message=f"Setup time cannot be negative ({val})",
                                value=val,
                            )
                        )
            if run_time_col in df_r.columns:
                for row_idx, val in enumerate(df_r[run_time_col]):
                    if pd.notna(val) and float(val) <= 0:
                        errors.append(
                            ValidationError(
                                error_type=InjectedErrorType.ZERO_NEGATIVE_DURATION,
                                severity=ErrorSeverity.CRITICAL,
                                table=routing_table,
                                row_idx=row_idx,
                                column=run_time_col,
                                message=f"Processing run time must be strictly positive ({val})",
                                value=val,
                            )
                        )

        # 6. Check Unit Mismatches
        if routing_table in tables:
            df_r = tables[routing_table]
            for u_col in [setup_unit_col, run_unit_col]:
                if u_col in df_r.columns:
                    for row_idx, val in enumerate(df_r[u_col]):
                        if pd.notna(val):
                            cleaned_unit = str(val).strip().upper()
                            if cleaned_unit not in self.VALID_TIME_UNITS:
                                errors.append(
                                    ValidationError(
                                        error_type=InjectedErrorType.UNIT_MISMATCH,
                                        severity=ErrorSeverity.WARNING,
                                        table=routing_table,
                                        row_idx=row_idx,
                                        column=u_col,
                                        message=f"Invalid time duration unit '{val}'",
                                        value=val,
                                    )
                                )

        # 7. Check Duplicate Keys
        if routing_table in tables and {routing_id_col, op_seq_col}.issubset(
            tables[routing_table].columns
        ):
            seen_keys: set[tuple[str, str]] = set()
            for r_idx, (_, row) in enumerate(tables[routing_table].iterrows()):
                key = (
                    str(row[routing_id_col]).strip(),
                    str(row[op_seq_col]).strip(),
                )
                if key in seen_keys:
                    errors.append(
                        ValidationError(
                            error_type=InjectedErrorType.DUPLICATE_KEY,
                            severity=ErrorSeverity.CRITICAL,
                            table=routing_table,
                            row_idx=r_idx,
                            column=f"{routing_id_col}, {op_seq_col}",
                            message=f"Duplicate operation sequence key {key}",
                            value=key,
                        )
                    )
                else:
                    seen_keys.add(key)

        # 8. Check Circular BOMs (Graph cycle detection)
        if bom_table in tables and {bom_parent_col, bom_child_col}.issubset(
            tables[bom_table].columns
        ):
            adj: dict[str, list[tuple[str, int]]] = {}
            for r_idx, (_, row) in enumerate(tables[bom_table].iterrows()):
                p = str(row[bom_parent_col]).strip().upper()
                c = str(row[bom_child_col]).strip().upper()
                if p and c:
                    adj.setdefault(p, []).append((c, r_idx))

            visited: dict[str, int] = {}  # 0 = unvisited, 1 = visiting, 2 = done
            cycle_found: list[str] = []
            cycle_row_idx: int | None = None

            def dfs(node: str, path: list[str]) -> bool:
                nonlocal cycle_found, cycle_row_idx
                visited[node] = 1
                for neighbor, r_idx in adj.get(node, []):
                    if visited.get(neighbor, 0) == 1:
                        # Cycle detected!
                        cycle_found = path + [neighbor]
                        cycle_row_idx = r_idx
                        return True
                    if visited.get(neighbor, 0) == 0 and dfs(neighbor, path + [neighbor]):
                        return True
                visited[node] = 2
                return False

            for parent_node in list(adj.keys()):
                if visited.get(parent_node, 0) == 0 and dfs(parent_node, [parent_node]):
                    cycle_str = " -> ".join(cycle_found)
                    errors.append(
                        ValidationError(
                            error_type=InjectedErrorType.CIRCULAR_BOM,
                            severity=ErrorSeverity.CRITICAL,
                            table=bom_table,
                            row_idx=cycle_row_idx,
                            column=bom_child_col,
                            message=f"Circular BOM dependency detected: {cycle_str}",
                            value=cycle_str,
                        )
                    )
                    break

        # 9. Check Mixed Date Formats
        if order_table in tables:
            df_o = tables[order_table]
            date_cols = [c for c in [order_start_col, order_due_col] if c in df_o.columns]
            iso_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
            for col in date_cols:
                for row_idx, val in enumerate(df_o[col]):
                    if pd.notna(val) and str(val).strip() != "":
                        s_val = str(val).strip()
                        if not iso_pattern.match(s_val):
                            # Try parsing to see if it's an alternate format
                            errors.append(
                                ValidationError(
                                    error_type=InjectedErrorType.MIXED_DATE_FORMAT,
                                    severity=ErrorSeverity.WARNING,
                                    table=order_table,
                                    row_idx=row_idx,
                                    column=col,
                                    message=f"Date '{val}' is not in ISO format YYYY-MM-DD",
                                    value=val,
                                )
                            )

        con.close()

        # Compute summary counts & readiness score
        critical_count = sum(1 for e in errors if e.severity == ErrorSeverity.CRITICAL)
        warning_count = sum(1 for e in errors if e.severity == ErrorSeverity.WARNING)
        info_count = sum(1 for e in errors if e.severity == ErrorSeverity.INFO)

        score = max(
            0.0,
            1.0 - (0.25 * critical_count) - (0.05 * warning_count) - (0.01 * info_count),
        )

        return ValidationReport(
            is_valid=(critical_count == 0),
            errors=errors,
            critical_count=critical_count,
            warning_count=warning_count,
            info_count=info_count,
            readiness_score=round(score, 4),
        )
