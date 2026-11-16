"""Tests for DuckDB-based in-memory profiling engine."""

from planpilot.erp.generator import SAPExtractGenerator
from planpilot.erp.profiler import DuckDBProfiler


def test_duckdb_profiler_basic() -> None:
    """Verify DuckDB profiler extracts correct summary statistics and candidate keys."""
    gen = SAPExtractGenerator(seed=42)
    data = gen.generate_clean_dataset()

    profiler = DuckDBProfiler()
    db_profile = profiler.profile_all(data)

    assert db_profile.total_tables == 5
    assert db_profile.total_rows > 0

    # Test CRHD profile
    assert "CRHD" in db_profile.tables
    crhd_prof = db_profile.tables["CRHD"]
    assert crhd_prof.row_count == len(data["CRHD"])
    assert "ARBPL" in crhd_prof.columns
    assert "ARBPL" in crhd_prof.candidate_keys  # ARBPL is unique in CRHD

    arbpl_col = crhd_prof.columns["ARBPL"]
    assert arbpl_col.null_count == 0
    assert arbpl_col.null_fraction == 0.0
    assert arbpl_col.is_unique is True
    assert len(arbpl_col.sample_values) > 0

    profiler.close()
