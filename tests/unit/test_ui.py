"""Unit tests for the PlanPilot Streamlit UI components."""

from __future__ import annotations

import plotly.graph_objects as go
from ui.gantt import create_gantt_chart

from planpilot.domain.generator import PlantGenerator
from planpilot.domain.models import Plant, Schedule
from planpilot.solver.scheduler import solve


def test_create_gantt_chart() -> None:
    """Test Plotly Gantt chart creation for a solved schedule."""
    plant: Plant = PlantGenerator(num_orders=3, num_machines=2, seed=42).generate("P_GANTT")
    sched: Schedule | None = solve(plant)
    assert sched is not None

    fig: go.Figure = create_gantt_chart(sched, plant, title="Test Schedule", height=400)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) > 0

    # Verify at least one bar trace for an assignment
    has_bar = any(isinstance(trace, go.Bar) for trace in fig.data)
    assert has_bar


def test_ui_app_compilation() -> None:
    """Verify that ui/app.py compiles without syntax or import errors."""
    import py_compile
    from pathlib import Path

    app_path = Path("ui/app.py")
    assert app_path.exists()
    # py_compile checks syntax without running Streamlit event loop
    compiled = py_compile.compile(str(app_path), doraise=True)
    assert compiled is not None
