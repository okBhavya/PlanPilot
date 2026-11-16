"""Deterministic error injector with ground-truth error labels."""

from __future__ import annotations

import copy
import random

import pandas as pd

from planpilot.erp.models import InjectedError, InjectedErrorType


class ErrorInjector:
    """Injects realistic manufacturing data defects into clean extracts with ground truth."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self.rng = random.Random(seed)

    def inject_all_errors(
        self, datasets: dict[str, pd.DataFrame]
    ) -> tuple[dict[str, pd.DataFrame], list[InjectedError]]:
        """Inject one of each error type into the provided dataset."""
        return self.inject_errors(datasets, list(InjectedErrorType))

    def inject_errors(
        self,
        datasets: dict[str, pd.DataFrame],
        error_types: list[InjectedErrorType],
    ) -> tuple[dict[str, pd.DataFrame], list[InjectedError]]:
        """Inject specified error types into the dataset with exact ground truth."""
        corrupted = {name: df.copy(deep=True) for name, df in datasets.items()}
        ground_truth: list[InjectedError] = []

        is_sap = "CRHD" in corrupted
        wc_table = "CRHD" if is_sap else "stations"
        mat_table = "MARA" if is_sap else "items"
        routing_table = "PLPO" if is_sap else "routings"
        bom_table = "STPO" if is_sap else "boms"
        order_table = "AFKO" if is_sap else "work_orders"

        # 1. Dangling Work Center
        if InjectedErrorType.DANGLING_WORK_CENTER in error_types and routing_table in corrupted:
            df = corrupted[routing_table]
            row_idx = 0
            col = "ARBPL" if is_sap else "station_id"
            orig_wc = df.at[row_idx, col]
            injected_wc = "WC-GHOST-999"
            df.at[row_idx, col] = injected_wc
            ground_truth.append(
                InjectedError(
                    error_type=InjectedErrorType.DANGLING_WORK_CENTER,
                    table=routing_table,
                    row_idx=row_idx,
                    column=col,
                    details=f"Routing references non-existent work center '{injected_wc}'",
                    expected_value=orig_wc,
                    injected_value=injected_wc,
                )
            )

        # 2. Circular BOM
        if InjectedErrorType.CIRCULAR_BOM in error_types and bom_table in corrupted:
            df = corrupted[bom_table]
            p_col = "MATNR" if is_sap else "parent_item"
            c_col = "IDNRK" if is_sap else "child_item"
            id_col = "STLNR" if is_sap else "bom_id"
            parent = str(df.at[0, p_col])
            child = str(df.at[0, c_col])
            cycle_row = {
                id_col: f"BOM-CYCLE-{child}",
                p_col: child,
                c_col: parent,
                "MENGE" if is_sap else "quantity": 1.0,
                "MEINS" if is_sap else "unit": "PCE",
            }
            new_row_idx = len(df)
            corrupted[bom_table] = pd.concat(
                [df, pd.DataFrame([cycle_row])], ignore_index=True
            )
            ground_truth.append(
                InjectedError(
                    error_type=InjectedErrorType.CIRCULAR_BOM,
                    table=bom_table,
                    row_idx=new_row_idx,
                    column=c_col,
                    details=f"Circular dependency injected: {child} -> {parent} -> {child}",
                    expected_value=None,
                    injected_value=parent,
                )
            )

        # 3. Zero / Negative Duration
        if InjectedErrorType.ZERO_NEGATIVE_DURATION in error_types and routing_table in corrupted:
            df = corrupted[routing_table]
            row_idx = 1 if len(df) > 1 else 0
            col = "VGW02" if is_sap else "run_mins"
            orig_dur = df.at[row_idx, col]
            injected_dur = -15.0
            df.at[row_idx, col] = injected_dur
            ground_truth.append(
                InjectedError(
                    error_type=InjectedErrorType.ZERO_NEGATIVE_DURATION,
                    table=routing_table,
                    row_idx=row_idx,
                    column=col,
                    details=f"Operation run time is negative ({injected_dur})",
                    expected_value=orig_dur,
                    injected_value=injected_dur,
                )
            )

        # 4. Unit Mismatch
        if InjectedErrorType.UNIT_MISMATCH in error_types and routing_table in corrupted:
            df = corrupted[routing_table]
            row_idx = 2 if len(df) > 2 else 0
            col = "VGE02" if is_sap else "run_uom"
            orig_uom = df.at[row_idx, col]
            injected_uom = "KILOGRAMS"
            df.at[row_idx, col] = injected_uom
            ground_truth.append(
                InjectedError(
                    error_type=InjectedErrorType.UNIT_MISMATCH,
                    table=routing_table,
                    row_idx=row_idx,
                    column=col,
                    details=f"Invalid duration unit '{injected_uom}' (expected MIN, HR, SEC)",
                    expected_value=orig_uom,
                    injected_value=injected_uom,
                )
            )

        # 5. Duplicate Key
        if InjectedErrorType.DUPLICATE_KEY in error_types and routing_table in corrupted:
            df = corrupted[routing_table]
            row_idx = 3 if len(df) > 3 else 0
            dup_row = copy.deepcopy(df.iloc[row_idx].to_dict())
            new_row_idx = len(df)
            corrupted[routing_table] = pd.concat(
                [df, pd.DataFrame([dup_row])], ignore_index=True
            )
            key_cols = "PLNNR, VORNR" if is_sap else "routing_id, op_seq"
            ground_truth.append(
                InjectedError(
                    error_type=InjectedErrorType.DUPLICATE_KEY,
                    table=routing_table,
                    row_idx=new_row_idx,
                    column=key_cols,
                    details="Duplicate routing step key",
                    expected_value=None,
                    injected_value=dup_row,
                )
            )

        # 6. Orphan Component in BOM
        if InjectedErrorType.ORPHAN_COMPONENT in error_types and bom_table in corrupted:
            df = corrupted[bom_table]
            row_idx = 1 if len(df) > 1 else 0
            col = "IDNRK" if is_sap else "child_item"
            orig_child = df.at[row_idx, col]
            injected_child = "MAT-ORPHAN-NONEXISTENT"
            df.at[row_idx, col] = injected_child
            ground_truth.append(
                InjectedError(
                    error_type=InjectedErrorType.ORPHAN_COMPONENT,
                    table=bom_table,
                    row_idx=row_idx,
                    column=col,
                    details=f"BOM references non-existent material '{injected_child}'",
                    expected_value=orig_child,
                    injected_value=injected_child,
                )
            )

        # 7. Mixed Date Formats
        if InjectedErrorType.MIXED_DATE_FORMAT in error_types and order_table in corrupted:
            df = corrupted[order_table]
            row_idx = 0
            col = "GSTRP" if is_sap else "release_date"
            orig_date = df.at[row_idx, col]
            injected_date = "11/25/2026"
            df.at[row_idx, col] = injected_date
            ground_truth.append(
                InjectedError(
                    error_type=InjectedErrorType.MIXED_DATE_FORMAT,
                    table=order_table,
                    row_idx=row_idx,
                    column=col,
                    details=f"Date '{injected_date}' does not follow ISO-8601 (YYYY-MM-DD)",
                    expected_value=orig_date,
                    injected_value=injected_date,
                )
            )

        # 8. Whitespace / Case Inconsistent
        if InjectedErrorType.WHITESPACE_CASE_INCONSISTENT in error_types and mat_table in corrupted:
            df = corrupted[mat_table]
            row_idx = 0
            col = "MATNR" if is_sap else "item_code"
            orig_mat = df.at[row_idx, col]
            injected_mat = f"  {str(orig_mat).lower()}  "
            df.at[row_idx, col] = injected_mat
            ground_truth.append(
                InjectedError(
                    error_type=InjectedErrorType.WHITESPACE_CASE_INCONSISTENT,
                    table=mat_table,
                    row_idx=row_idx,
                    column=col,
                    details="Material ID contains untrimmed whitespace and inconsistent casing",
                    expected_value=orig_mat,
                    injected_value=injected_mat,
                )
            )

        # 9. Missing Mandatory Field
        if InjectedErrorType.MISSING_MANDATORY_FIELD in error_types and wc_table in corrupted:
            df = corrupted[wc_table]
            row_idx = len(df) - 1
            col = "ARBPL" if is_sap else "station_id"
            orig_blank = df.at[row_idx, col]
            injected_blank = ""
            df.at[row_idx, col] = injected_blank
            ground_truth.append(
                InjectedError(
                    error_type=InjectedErrorType.MISSING_MANDATORY_FIELD,
                    table=wc_table,
                    row_idx=row_idx,
                    column=col,
                    details="Work center ID is blank or null",
                    expected_value=orig_blank,
                    injected_value=injected_blank,
                )
            )

        return corrupted, ground_truth
