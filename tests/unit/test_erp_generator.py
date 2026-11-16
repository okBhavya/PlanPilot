"""Tests for SAP and Legacy ERP extract generators."""

import pandas as pd

from planpilot.erp.generator import LegacyExtractGenerator, SAPExtractGenerator


def test_sap_generator_clean_dataset() -> None:
    """Verify SAPExtractGenerator produces complete, coherent tables."""
    gen = SAPExtractGenerator(seed=123)
    data = gen.generate_clean_dataset(
        num_products=4,
        num_components=6,
        num_work_centers=3,
        num_orders=5,
    )

    assert set(data.keys()) == {"CRHD", "MARA", "PLPO", "STPO", "AFKO"}

    # Work centers
    df_crhd = data["CRHD"]
    assert len(df_crhd) == 3
    assert "ARBPL" in df_crhd.columns
    assert "KTEXT" in df_crhd.columns

    # Materials
    df_mara = data["MARA"]
    assert len(df_mara) == 10  # 4 products + 6 components
    assert "MATNR" in df_mara.columns
    assert "MTART" in df_mara.columns

    # Routings
    df_plpo = data["PLPO"]
    assert len(df_plpo) >= 8
    assert "PLNNR" in df_plpo.columns
    assert "VORNR" in df_plpo.columns
    assert "ARBPL" in df_plpo.columns
    assert (df_plpo["VGW02"] > 0).all()

    # BOMs
    df_stpo = data["STPO"]
    assert len(df_stpo) >= 4
    assert "STLNR" in df_stpo.columns
    assert "MATNR" in df_stpo.columns
    assert "IDNRK" in df_stpo.columns

    # Orders
    df_afko = data["AFKO"]
    assert len(df_afko) == 5
    assert "AUFNR" in df_afko.columns


def test_sap_generator_determinism() -> None:
    """Verify same seed produces identical datasets."""
    gen1 = SAPExtractGenerator(seed=42)
    data1 = gen1.generate_clean_dataset()

    gen2 = SAPExtractGenerator(seed=42)
    data2 = gen2.generate_clean_dataset()

    for tbl in data1:
        pd.testing.assert_frame_equal(data1[tbl], data2[tbl])


def test_legacy_generator() -> None:
    """Verify LegacyExtractGenerator produces tables with legacy headers."""
    gen = LegacyExtractGenerator(seed=99)
    data = gen.generate_clean_dataset(
        num_products=3,
        num_components=4,
        num_work_centers=2,
        num_orders=4,
    )

    expected_tables = {"stations", "items", "routings", "boms", "work_orders"}
    assert set(data.keys()) == expected_tables

    assert "station_id" in data["stations"].columns
    assert "item_code" in data["items"].columns
    assert "op_seq" in data["routings"].columns
    assert "child_item" in data["boms"].columns
    assert "order_id" in data["work_orders"].columns
