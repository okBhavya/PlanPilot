"""In-memory DuckDB profiling engine for raw ERP data tables."""

from __future__ import annotations

from typing import Any

import duckdb
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


class ColumnProfile(BaseModel):
    """Profiling statistics for a single column."""

    model_config = ConfigDict(frozen=True)

    name: str
    data_type: str
    total_rows: int
    null_count: int
    null_fraction: float
    distinct_count: int
    is_unique: bool
    sample_values: list[Any] = Field(default_factory=list)
    min_value: Any = None
    max_value: Any = None


class TableProfile(BaseModel):
    """Profiling summary for a single table."""

    model_config = ConfigDict(frozen=True)

    table_name: str
    row_count: int
    column_count: int
    columns: dict[str, ColumnProfile]
    candidate_keys: list[str] = Field(default_factory=list)


class DatabaseProfile(BaseModel):
    """Overall profiling results across all registered tables."""

    model_config = ConfigDict(frozen=True)

    tables: dict[str, TableProfile]
    total_tables: int
    total_rows: int


class DuckDBProfiler:
    """Profiles raw data tables in-memory using DuckDB for speed and flexibility."""

    def __init__(self) -> None:
        self.con = duckdb.connect(database=":memory:")

    def register_tables(self, tables: dict[str, pd.DataFrame]) -> None:
        """Register a dictionary of DataFrames into DuckDB."""
        for name, df in tables.items():
            if len(df.columns) > 0:
                self.con.register(name, df)

    def profile_all(self, tables: dict[str, pd.DataFrame]) -> DatabaseProfile:
        """Profile all tables and return comprehensive statistics."""
        self.register_tables(tables)
        profiles: dict[str, TableProfile] = {}
        total_rows = 0

        for table_name, df in tables.items():
            profile = self.profile_table(table_name, df)
            profiles[table_name] = profile
            total_rows += profile.row_count

        return DatabaseProfile(
            tables=profiles,
            total_tables=len(profiles),
            total_rows=total_rows,
        )

    def profile_table(self, table_name: str, df: pd.DataFrame) -> TableProfile:
        """Profile an individual table using DuckDB SQL."""
        row_count = len(df)
        if row_count == 0:
            return TableProfile(
                table_name=table_name,
                row_count=0,
                column_count=len(df.columns),
                columns={},
                candidate_keys=[],
            )

        col_profiles: dict[str, ColumnProfile] = {}
        candidate_keys: list[str] = []

        for col in df.columns:
            # Query column statistics via DuckDB
            # We escape the column name in double quotes for safe SQL
            safe_col = f'"{col}"'
            query = f"""
                SELECT
                    count({safe_col}) as non_null_count,
                    count(DISTINCT {safe_col}) as distinct_count,
                    min({safe_col}) as min_val,
                    max({safe_col}) as max_val
                FROM "{table_name}"
            """
            res = self.con.execute(query).fetchone()
            non_null = res[0] if res else 0
            distinct = res[1] if res else 0
            min_val = str(res[2]) if res and res[2] is not None else None
            max_val = str(res[3]) if res and res[3] is not None else None

            null_count = row_count - non_null
            null_fraction = round(null_count / row_count, 4)
            is_unique = distinct == row_count and null_count == 0
            if is_unique:
                candidate_keys.append(col)

            # Fetch up to 5 distinct non-null sample values
            sample_query = f"""
                SELECT DISTINCT {safe_col}
                FROM "{table_name}"
                WHERE {safe_col} IS NOT NULL
                LIMIT 5
            """
            sample_rows = self.con.execute(sample_query).fetchall()
            sample_values = [str(r[0]) for r in sample_rows]

            col_profiles[col] = ColumnProfile(
                name=col,
                data_type=str(df[col].dtype),
                total_rows=row_count,
                null_count=null_count,
                null_fraction=null_fraction,
                distinct_count=distinct,
                is_unique=is_unique,
                sample_values=sample_values,
                min_value=min_val,
                max_value=max_val,
            )

        return TableProfile(
            table_name=table_name,
            row_count=row_count,
            column_count=len(df.columns),
            columns=col_profiles,
            candidate_keys=candidate_keys,
        )

    def close(self) -> None:
        """Close DuckDB connection."""
        self.con.close()
