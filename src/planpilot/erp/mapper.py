"""Maps validated raw ERP extracts into clean PlanPilot domain models."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from planpilot.domain.models import (
    BOM,
    BOMComponent,
    ChangeoverMatrix,
    Machine,
    Material,
    MaterialFamily,
    Operation,
    Order,
    OrderPriority,
    OrderStatus,
    Plant,
    Routing,
    WorkCenter,
)
from planpilot.erp.models import ColumnMapping, ERPFormat


class ERPMapper:
    """Maps raw source tables to PlanPilot domain models."""

    # Default SAP mappings
    SAP_WORK_CENTER_MAP = {
        "ARBPL": "work_center_id",
        "WERKS": "plant_id",
        "KTEXT": "description",
    }
    SAP_MATERIAL_MAP = {
        "MATNR": "material_id",
        "MAKTX": "name",
        "MTART": "family",
    }
    SAP_ROUTING_MAP = {
        "PLNNR": "routing_id",
        "MATNR": "product_material_id",
        "VORNR": "operation_number",
        "ARBPL": "work_center_id",
        "VGW01": "setup_time",
        "VGE01": "setup_unit",
        "VGW02": "processing_time",
        "VGE02": "processing_unit",
    }
    SAP_BOM_MAP = {
        "STLNR": "bom_id",
        "MATNR": "parent_material_id",
        "IDNRK": "component_material_id",
        "MENGE": "quantity",
    }
    SAP_ORDER_MAP = {
        "AUFNR": "order_id",
        "MATNR": "material_id",
        "GAMNG": "quantity",
        "GSTRP": "release_date",
        "GLTRP": "due_date",
        "DISPO": "priority",
    }

    # Default Legacy mappings
    LEGACY_WORK_CENTER_MAP = {
        "station_id": "work_center_id",
        "station_name": "description",
    }
    LEGACY_MATERIAL_MAP = {
        "item_code": "material_id",
        "item_description": "name",
        "item_type": "family",
    }
    LEGACY_ROUTING_MAP = {
        "routing_id": "routing_id",
        "product_code": "product_material_id",
        "op_seq": "operation_number",
        "station_id": "work_center_id",
        "setup_mins": "setup_time",
        "setup_uom": "setup_unit",
        "run_mins": "processing_time",
        "run_uom": "processing_unit",
    }
    LEGACY_BOM_MAP = {
        "bom_id": "bom_id",
        "parent_item": "parent_material_id",
        "child_item": "component_material_id",
        "quantity": "quantity",
    }
    LEGACY_ORDER_MAP = {
        "order_id": "order_id",
        "product_code": "material_id",
        "target_quantity": "quantity",
        "release_date": "release_date",
        "due_date": "due_date",
        "priority_group": "priority",
    }

    @staticmethod
    def _convert_duration_to_minutes(val: Any, unit: Any) -> int:
        """Convert any duration value and unit into integer minutes (rounded up)."""
        if pd.isna(val):
            return 0
        try:
            num = float(val)
        except (ValueError, TypeError):
            return 0

        u_str = str(unit).strip().upper() if pd.notna(unit) else "MIN"
        if u_str in {"HR", "H", "HRS", "HOUR", "HOURS", "STD"}:
            mins = num * 60.0
        elif u_str in {"SEC", "S", "SECS", "SECOND", "SECONDS"}:
            mins = num / 60.0
        else:  # Default to minutes
            mins = num

        return max(0, math.ceil(mins))

    def map_to_plant(
        self,
        tables: dict[str, pd.DataFrame],
        format_type: ERPFormat = ERPFormat.SAP,
        custom_mappings: list[ColumnMapping] | None = None,
        plant_id: str = "PLANT-01",
        plant_name: str = "Main Factory Plant",
    ) -> Plant:
        """Transform raw tables into a valid PlanPilot Plant instance."""
        is_sap = format_type == ERPFormat.SAP

        wc_tbl_name = "CRHD" if is_sap else "stations"
        mat_tbl_name = "MARA" if is_sap else "items"
        routing_tbl_name = "PLPO" if is_sap else "routings"
        bom_tbl_name = "STPO" if is_sap else "boms"
        order_tbl_name = "AFKO" if is_sap else "work_orders"

        wc_map = self.SAP_WORK_CENTER_MAP if is_sap else self.LEGACY_WORK_CENTER_MAP
        mat_map = self.SAP_MATERIAL_MAP if is_sap else self.LEGACY_MATERIAL_MAP
        rout_map = self.SAP_ROUTING_MAP if is_sap else self.LEGACY_ROUTING_MAP
        bom_map = self.SAP_BOM_MAP if is_sap else self.LEGACY_BOM_MAP
        order_map = self.SAP_ORDER_MAP if is_sap else self.LEGACY_ORDER_MAP

        # Apply custom overrides if any
        if custom_mappings:
            for cm in custom_mappings:
                if cm.table_name == wc_tbl_name:
                    wc_map = cm.mapping
                elif cm.table_name == mat_tbl_name:
                    mat_map = cm.mapping
                elif cm.table_name == routing_tbl_name:
                    rout_map = cm.mapping
                elif cm.table_name == bom_tbl_name:
                    bom_map = cm.mapping
                elif cm.table_name == order_tbl_name:
                    order_map = cm.mapping

        # Inverted maps: domain_field -> raw_col
        inv_wc = {v: k for k, v in wc_map.items()}
        inv_mat = {v: k for k, v in mat_map.items()}
        inv_rout = {v: k for k, v in rout_map.items()}
        inv_bom = {v: k for k, v in bom_map.items()}
        inv_order = {v: k for k, v in order_map.items()}

        # 1. Build Work Centers & Machines
        work_centers: list[WorkCenter] = []
        machines: list[Machine] = []
        if wc_tbl_name in tables:
            df_wc = tables[wc_tbl_name]
            wc_col = inv_wc.get("work_center_id", "ARBPL")
            desc_col = inv_wc.get("description", "KTEXT")

            for _, row in df_wc.iterrows():
                wc_raw = str(row.get(wc_col, "")).strip().upper()
                if not wc_raw:
                    continue
                desc = (
                    str(row.get(desc_col, f"Work Center {wc_raw}"))
                    if desc_col in row
                    else f"Work Center {wc_raw}"
                )
                m_id = f"M-{wc_raw}"
                machines.append(
                    Machine(
                        id=m_id,
                        name=f"Machine {wc_raw}",
                        work_center_id=wc_raw,
                    )
                )
                work_centers.append(
                    WorkCenter(
                        id=wc_raw,
                        name=desc,
                        machine_ids=[m_id],
                    )
                )

        # 2. Build Materials
        materials: list[Material] = []
        if mat_tbl_name in tables:
            df_mat = tables[mat_tbl_name]
            mat_col = inv_mat.get("material_id", "MATNR")
            name_col = inv_mat.get("name", "MAKTX")

            for _, row in df_mat.iterrows():
                mat_raw = str(row.get(mat_col, "")).strip().upper()
                if not mat_raw:
                    continue
                name = (
                    str(row.get(name_col, mat_raw))
                    if name_col in row
                    else mat_raw
                )
                materials.append(
                    Material(
                        id=mat_raw,
                        name=name,
                        family=MaterialFamily.STEEL,  # Default valid family
                    )
                )

        # 3. Build Routings & Operations
        routings: list[Routing] = []
        if routing_tbl_name in tables:
            df_r = tables[routing_tbl_name]
            r_col = inv_rout.get("routing_id", "PLNNR")
            prod_col = inv_rout.get("product_material_id", "MATNR")
            op_col = inv_rout.get("operation_number", "VORNR")
            wc_col = inv_rout.get("work_center_id", "ARBPL")
            s_time_col = inv_rout.get("setup_time", "VGW01")
            s_unit_col = inv_rout.get("setup_unit", "VGE01")
            p_time_col = inv_rout.get("processing_time", "VGW02")
            p_unit_col = inv_rout.get("processing_unit", "VGE02")

            grouped = df_r.groupby(r_col)
            for r_id_raw, group in grouped:
                r_id = str(r_id_raw).strip().upper()
                first_row = group.iloc[0]
                prod_id = str(first_row.get(prod_col, "")).strip().upper()
                if not prod_id:
                    continue

                ops: list[Operation] = []
                for op_idx, (_, row) in enumerate(group.iterrows()):
                    vornr = str(row.get(op_col, "")).strip()
                    op_id = f"OP-{vornr}"
                    wc_raw = str(row.get(wc_col, "")).strip().upper()
                    m_id = f"M-{wc_raw}"

                    s_time = self._convert_duration_to_minutes(
                        row.get(s_time_col, 0), row.get(s_unit_col, "MIN")
                    )
                    p_time = self._convert_duration_to_minutes(
                        row.get(p_time_col, 10), row.get(p_unit_col, "MIN")
                    )
                    # Processing time must be >= 1 min
                    p_time = max(1, p_time)

                    is_valid_num = vornr.isdigit() and int(vornr) > 0
                    seq_val = int(vornr) if is_valid_num else (op_idx + 1) * 10
                    ops.append(
                        Operation(
                            id=op_id,
                            sequence=seq_val,
                            name=f"Operation {vornr}",
                            eligible_machine_ids=[m_id],
                            processing_time=p_time,
                            setup_time=s_time,
                            material_id=prod_id,
                        )
                    )

                routings.append(
                    Routing(
                        id=r_id,
                        product_material_id=prod_id,
                        operations=ops,
                    )
                )

        # 4. Build BOMs
        boms: list[BOM] = []
        if bom_tbl_name in tables:
            df_b = tables[bom_tbl_name]
            b_col = inv_bom.get("bom_id", "STLNR")
            p_col = inv_bom.get("parent_material_id", "MATNR")
            c_col = inv_bom.get("component_material_id", "IDNRK")
            q_col = inv_bom.get("quantity", "MENGE")

            grouped_bom = df_b.groupby(b_col)
            for b_id_raw, group in grouped_bom:
                b_id = str(b_id_raw).strip().upper()
                parent_id = (
                    str(group.iloc[0].get(p_col, "")).strip().upper()
                )
                components: list[BOMComponent] = []
                for _, row in group.iterrows():
                    child_id = str(row.get(c_col, "")).strip().upper()
                    qty = (
                        float(row.get(q_col, 1.0))
                        if pd.notna(row.get(q_col))
                        else 1.0
                    )
                    components.append(
                        BOMComponent(
                            material_id=child_id,
                            quantity=qty,
                        )
                    )
                boms.append(
                    BOM(
                        id=b_id,
                        product_material_id=parent_id,
                        components=components,
                    )
                )

        # 5. Build Orders
        orders: list[Order] = []
        if order_tbl_name in tables:
            df_o = tables[order_tbl_name]
            ord_col = inv_order.get("order_id", "AUFNR")
            prod_col = inv_order.get("material_id", "MATNR")
            qty_col = inv_order.get("quantity", "GAMNG")

            for ord_idx, (_, row) in enumerate(df_o.iterrows()):
                ord_id = str(
                    row.get(ord_col, f"ORD-{ord_idx+1:03d}")
                ).strip().upper()
                prod_id = str(row.get(prod_col, "")).strip().upper()
                qty = (
                    float(row.get(qty_col, 10.0))
                    if pd.notna(row.get(qty_col))
                    else 10.0
                )
                # Find matching routing
                matching_routing = next(
                    (r for r in routings if r.product_material_id == prod_id),
                    None,
                )
                routing_id = (
                    matching_routing.id
                    if matching_routing
                    else (routings[0].id if routings else "ROUT-DEFAULT")
                )

                orders.append(
                    Order(
                        id=ord_id,
                        routing_id=routing_id,
                        quantity=max(1, int(qty)),
                        due_date=300 + (ord_idx * 50),
                        release_time=ord_idx * 20,
                        priority=OrderPriority.NORMAL,
                        status=OrderStatus.PENDING,
                    )
                )

        plant = Plant(
            id=plant_id,
            name=plant_name,
            work_centers=work_centers,
            machines=machines,
            materials=materials,
            routings=routings,
            boms=boms,
            orders=orders,
            changeover_matrix=ChangeoverMatrix(),
        )

        return plant
