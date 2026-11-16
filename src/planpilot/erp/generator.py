"""Realistic SAP-style and legacy ERP extract generators."""

from __future__ import annotations

import random
from typing import Any

import pandas as pd


class SAPExtractGenerator:
    """Generates synthetic but structurally realistic SAP ERP data extracts."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self.rng = random.Random(seed)

    def generate_clean_dataset(
        self,
        num_products: int = 5,
        num_components: int = 8,
        num_work_centers: int = 4,
        num_orders: int = 6,
    ) -> dict[str, pd.DataFrame]:
        """Generate a coherent, clean set of SAP tables."""
        # 1. Work Centers (CRHD)
        wc_types = ["MILL", "LATHE", "ASSY", "INSPECT", "DRILL", "WELD"]
        work_centers: list[dict[str, Any]] = []
        for i in range(num_work_centers):
            wc_type = wc_types[i % len(wc_types)]
            arbpl = f"WC-{wc_type}-{i + 1:02d}"
            work_centers.append(
                {
                    "ARBPL": arbpl,
                    "WERKS": "1000",
                    "KTEXT": f"Machining Center {wc_type} #{i + 1}",
                    "KAPAR": "001",
                    "ANZMA": 1,
                }
            )
        df_crhd = pd.DataFrame(work_centers)

        # 2. Material Master (MARA)
        materials: list[dict[str, Any]] = []
        product_ids: list[str] = []
        component_ids: list[str] = []

        # Finished Goods (FERT)
        for i in range(num_products):
            matnr = f"MAT-PROD-{i + 1:03d}"
            product_ids.append(matnr)
            materials.append(
                {
                    "MATNR": matnr,
                    "WERKS": "1000",
                    "MTART": "FERT",
                    "MEINS": "PCE",
                    "MAKTX": f"Finished Product Alpha {i + 1}",
                }
            )

        # Raw Materials (ROH) / Components (HALB)
        for i in range(num_components):
            matnr = f"MAT-COMP-{i + 1:03d}"
            component_ids.append(matnr)
            mtart = "HALB" if i % 2 == 0 else "ROH"
            materials.append(
                {
                    "MATNR": matnr,
                    "WERKS": "1000",
                    "MTART": mtart,
                    "MEINS": "PCE" if mtart == "HALB" else "KG",
                    "MAKTX": f"Component Material {i + 1}",
                }
            )
        df_mara = pd.DataFrame(materials)

        # 3. Routings & Operations (PLKO / PLPO)
        routings: list[dict[str, Any]] = []
        for i, prod_id in enumerate(product_ids):
            plnnr = f"ROUT-{i + 1:04d}"
            # Each product has 2 to 3 sequential operations
            num_ops = 2 + (i % 2)
            for op_idx in range(num_ops):
                vornr = f"{(op_idx + 1) * 10:04d}"
                # Choose eligible work center
                wc = work_centers[op_idx % len(work_centers)]["ARBPL"]
                setup_min = self.rng.choice([15.0, 20.0, 30.0, 45.0])
                machine_min = self.rng.choice([30.0, 45.0, 60.0, 90.0, 120.0])
                routings.append(
                    {
                        "PLNNR": plnnr,
                        "MATNR": prod_id,
                        "VORNR": vornr,
                        "ARBPL": wc,
                        "VGW01": setup_min,
                        "VGE01": "MIN",
                        "VGW02": machine_min,
                        "VGE02": "MIN",
                        "LTXA1": f"Operation {vornr} for {prod_id}",
                    }
                )
        df_plpo = pd.DataFrame(routings)

        # 4. Bill of Materials (MAST / STPO)
        boms: list[dict[str, Any]] = []
        for i, prod_id in enumerate(product_ids):
            stlnr = f"BOM-{i + 1:04d}"
            # Each product requires 1 to 2 components (strictly acyclic)
            assigned_comps = [component_ids[(i + c) % len(component_ids)] for c in range(2)]
            for comp_id in assigned_comps:
                boms.append(
                    {
                        "STLNR": stlnr,
                        "MATNR": prod_id,
                        "IDNRK": comp_id,
                        "MENGE": float(self.rng.choice([1, 2, 4])),
                        "MEINS": "PCE",
                    }
                )
        df_stpo = pd.DataFrame(boms)

        # 5. Production Orders (AFKO / AFPO)
        orders: list[dict[str, Any]] = []
        for i in range(num_orders):
            aufnr = f"ORD-{i + 1:05d}"
            prod_id = product_ids[i % len(product_ids)]
            qty = self.rng.choice([10.0, 25.0, 50.0, 100.0])
            start_day = i * 2
            due_day = start_day + 10
            orders.append(
                {
                    "AUFNR": aufnr,
                    "MATNR": prod_id,
                    "GAMNG": qty,
                    "GMEIN": "PCE",
                    "GSTRP": f"2026-11-{start_day + 1:02d}",
                    "GLTRP": f"2026-11-{due_day + 1:02d}",
                    "DISPO": "100",  # MRP controller / priority
                }
            )
        df_afko = pd.DataFrame(orders)

        return {
            "CRHD": df_crhd,
            "MARA": df_mara,
            "PLPO": df_plpo,
            "STPO": df_stpo,
            "AFKO": df_afko,
        }


class LegacyExtractGenerator:
    """Generates synthetic data from a non-SAP legacy ERP with custom column names."""

    def __init__(self, seed: int = 42) -> None:
        self.sap_gen = SAPExtractGenerator(seed=seed)

    def generate_clean_dataset(
        self,
        num_products: int = 5,
        num_components: int = 8,
        num_work_centers: int = 4,
        num_orders: int = 6,
    ) -> dict[str, pd.DataFrame]:
        """Generate legacy ERP tables corresponding to clean manufacturing data."""
        sap_data = self.sap_gen.generate_clean_dataset(
            num_products=num_products,
            num_components=num_components,
            num_work_centers=num_work_centers,
            num_orders=num_orders,
        )

        # Map CRHD -> stations
        df_stations = sap_data["CRHD"].rename(
            columns={
                "ARBPL": "station_id",
                "WERKS": "plant_id",
                "KTEXT": "station_name",
                "KAPAR": "station_type",
                "ANZMA": "capacity_count",
            }
        )

        # Map MARA -> items
        df_items = sap_data["MARA"].rename(
            columns={
                "MATNR": "item_code",
                "WERKS": "plant_id",
                "MTART": "item_type",
                "MEINS": "uom",
                "MAKTX": "item_description",
            }
        )

        # Map PLPO -> routings
        df_routings = sap_data["PLPO"].rename(
            columns={
                "PLNNR": "routing_id",
                "MATNR": "product_code",
                "VORNR": "op_seq",
                "ARBPL": "station_id",
                "VGW01": "setup_mins",
                "VGE01": "setup_uom",
                "VGW02": "run_mins",
                "VGE02": "run_uom",
                "LTXA1": "op_description",
            }
        )

        # Map STPO -> boms
        df_boms = sap_data["STPO"].rename(
            columns={
                "STLNR": "bom_id",
                "MATNR": "parent_item",
                "IDNRK": "child_item",
                "MENGE": "quantity",
                "MEINS": "unit",
            }
        )

        # Map AFKO -> work_orders
        df_orders = sap_data["AFKO"].rename(
            columns={
                "AUFNR": "order_id",
                "MATNR": "product_code",
                "GAMNG": "target_quantity",
                "GMEIN": "quantity_uom",
                "GSTRP": "release_date",
                "GLTRP": "due_date",
                "DISPO": "priority_group",
            }
        )

        return {
            "stations": df_stations,
            "items": df_items,
            "routings": df_routings,
            "boms": df_boms,
            "work_orders": df_orders,
        }
