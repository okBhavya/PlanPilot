"""Plotly Gantt Chart Visualizer for PlanPilot Production Schedules."""

from __future__ import annotations

import plotly.graph_objects as go

from planpilot.domain.models import Plant, Schedule

# Qualitative color palette for orders
PALETTE = [
    "#2E86AB",  # Blue
    "#F24236",  # Red
    "#00A878",  # Green
    "#F39237",  # Orange
    "#8B5FBF",  # Purple
    "#048A81",  # Teal
    "#E85D04",  # Coral
    "#D90429",  # Crimson
    "#3A86FF",  # Neon Blue
    "#8338EC",  # Violet
    "#FF006E",  # Magenta
    "#FB5607",  # Amber
]


def create_gantt_chart(
    schedule: Schedule,
    plant: Plant,
    title: str = "Production Schedule Gantt Chart",
    height: int = 450,
) -> go.Figure:
    """Create an interactive Plotly Gantt chart for a production schedule."""
    fig = go.Figure()

    machine_ids = [m.id for m in plant.machines]
    # Build a color map for orders
    order_ids = sorted({a.order_id for a in schedule.assignments})
    order_color_map = {oid: PALETTE[i % len(PALETTE)] for i, oid in enumerate(order_ids)}

    # 1. Plot Planned Downtime Windows (Gray textured bars)
    for dt in plant.downtimes:
        m = plant.machine_by_id(dt.machine_id)
        m_name = m.name if m else dt.machine_id
        dt_duration = dt.end - dt.start
        hover_text = (
            f"<b>Planned Downtime</b><br>"
            f"Machine: {dt.machine_id} ({m_name})<br>"
            f"Start: {dt.start} min<br>"
            f"End: {dt.end} min<br>"
            f"Duration: {dt_duration} min<br>"
            f"Reason: {dt.reason or 'Scheduled Maintenance'}"
        )
        fig.add_trace(
            go.Bar(
                name="Downtime",
                x=[dt_duration],
                y=[dt.machine_id],
                base=[dt.start],
                orientation="h",
                marker=dict(
                    color="#4A5568",
                    pattern=dict(shape="/", fillmode="replace", fgcolor="#CBD5E0"),
                    line=dict(color="#2D3748", width=1),
                ),
                hoverinfo="text",
                hovertext=hover_text,
                showlegend=False,
            )
        )

    # 2. Plot Sequence-Dependent Changeover Setups (Amber hatched bars)
    for a in schedule.assignments:
        if a.setup_time > 0 and a.setup_start is not None:
            setup_hover = (
                f"<b>Changeover Setup</b><br>"
                f"Machine: {a.machine_id}<br>"
                f"Order: {a.order_id} ({a.operation_id})<br>"
                f"Setup Duration: {a.setup_time} min<br>"
                f"Setup Window: {a.setup_start} -> {a.start} min"
            )
            fig.add_trace(
                go.Bar(
                    name="Setup",
                    x=[a.setup_time],
                    y=[a.machine_id],
                    base=[a.setup_start],
                    orientation="h",
                    marker=dict(
                        color="#ED8936",
                        pattern=dict(shape="\\", fillmode="replace", fgcolor="#FEEBC8"),
                        line=dict(color="#C05621", width=1),
                    ),
                    hoverinfo="text",
                    hovertext=setup_hover,
                    showlegend=False,
                )
            )

    # 3. Plot Operations (Colored by Order)
    seen_orders: set[str] = set()
    for a in schedule.assignments:
        show_legend = a.order_id not in seen_orders
        seen_orders.add(a.order_id)

        color = order_color_map.get(a.order_id, "#4A5568")
        ord_obj = plant.order_by_id(a.order_id)
        mat_id = ord_obj.material_id if ord_obj else "N/A"
        mat_obj = plant.material_by_id(mat_id) if ord_obj else None
        mat_info = mat_obj.family.value if (mat_obj and mat_obj.family) else mat_id

        if a.tight_constraints:
            constraint_info = ", ".join(tc.value for tc in a.tight_constraints)
        else:
            constraint_info = "NONE"

        op_hover = (
            f"<b>Order: {a.order_id}</b><br>"
            f"Operation: {a.operation_id}<br>"
            f"Machine: {a.machine_id}<br>"
            f"Material: {mat_info}<br>"
            f"Start: {a.start} min | End: {a.end} min<br>"
            f"Duration: {a.processing_time} min<br>"
            f"Tight Constraint: {constraint_info}"
        )

        fig.add_trace(
            go.Bar(
                name=f"Order {a.order_id}",
                x=[a.processing_time],
                y=[a.machine_id],
                base=[a.start],
                orientation="h",
                marker=dict(
                    color=color,
                    line=dict(color="#1A202C", width=1.2),
                ),
                text=f"{a.order_id}:{a.operation_id}",
                textposition="inside",
                insidetextanchor="middle",
                textfont=dict(color="white", size=11),
                hoverinfo="text",
                hovertext=op_hover,
                legendgroup=a.order_id,
                showlegend=show_legend,
            )
        )

    # Layout configuration
    max_time = max(schedule.kpis.makespan, 60)
    kpi_sub = f"Makespan: {schedule.kpis.makespan}m, OTD: {schedule.kpis.on_time_pct:.1f}%"
    fig.update_layout(
        title=dict(
            text=f"<b>{title}</b> ({kpi_sub})",
            font=dict(size=15),
        ),
        barmode="overlay",
        xaxis=dict(
            title="Time (Minutes)",
            range=[0, max_time * 1.05],
            showgrid=True,
            gridcolor="#EDF2F7",
            zeroline=True,
        ),
        yaxis=dict(
            title="Work Center / Machine",
            categoryorder="array",
            categoryarray=list(reversed(machine_ids)),
            showgrid=True,
            gridcolor="#EDF2F7",
        ),
        height=height,
        margin=dict(l=70, r=30, t=50, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10),
        ),
    )

    return fig
