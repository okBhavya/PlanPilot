"""Handcrafted benchmark instances with known, ground-truth root causes of lateness.

Implements the 5 canonical APS lateness scenarios:
1. competing_priority: High-priority competing order occupied the bottleneck machine
2. supply_delay: Upstream raw material supply arrival / release time delay
3. cleanout_gap: Sequence-dependent material changeover setup pushed job past deadline
4. downtime_overlap: Planned machine maintenance window overlapped operation window
5. routing_cascade: Cumulative multi-operation precedence along routing exceeded deadline
"""

from __future__ import annotations

from planpilot.domain.models import (
    ChangeoverEntry,
    ChangeoverMatrix,
    DowntimeWindow,
    Machine,
    Material,
    MaterialFamily,
    Operation,
    Order,
    OrderPriority,
    Plant,
    Routing,
    WorkCenter,
)


def create_competing_priority_instance() -> tuple[Plant, str]:
    """Instance 1: High-priority order takes machine, delaying low-priority order."""
    m1 = Machine(id="M1", name="Primary Mill", work_center_id="WC1")
    wc = WorkCenter(id="WC1", machine_ids=["M1"])
    mat = Material(id="MAT1", family=MaterialFamily.STEEL)

    op_rush = Operation(id="OP10", sequence=10, eligible_machine_ids=["M1"], processing_time=30)
    rtg_rush = Routing(id="RTG_RUSH", product_material_id="MAT1", operations=[op_rush])

    op_late = Operation(id="OP10", sequence=10, eligible_machine_ids=["M1"], processing_time=20)
    rtg_late = Routing(id="RTG_LATE", product_material_id="MAT1", operations=[op_late])

    ord_rush = Order(
        id="ORD_RUSH",
        routing_id="RTG_RUSH",
        priority=OrderPriority.HIGH,
        due_date=30,
        release_time=0,
    )
    ord_late = Order(
        id="ORD_LATE",
        routing_id="RTG_LATE",
        priority=OrderPriority.LOW,
        due_date=35,
        release_time=0,
    )

    plant = Plant(
        id="PLANT_COMPETING",
        name="Competing Priority Scenario",
        machines=[m1],
        work_centers=[wc],
        materials=[mat],
        routings=[rtg_rush, rtg_late],
        orders=[ord_rush, ord_late],
        horizon=500,
    )
    return plant, "ORD_LATE"


def create_supply_delay_instance() -> tuple[Plant, str]:
    """Instance 2: Order released late due to upstream raw material supply delay."""
    m1 = Machine(id="M1", work_center_id="WC1")
    wc = WorkCenter(id="WC1", machine_ids=["M1"])
    mat = Material(id="MAT1", family=MaterialFamily.STEEL)

    op = Operation(id="OP10", sequence=10, eligible_machine_ids=["M1"], processing_time=20)
    rtg = Routing(id="RTG_LATE", product_material_id="MAT1", operations=[op])

    # Due at 35, but raw material arrives at 25. Earliest finish is 45 (10m late).
    ord_late = Order(
        id="ORD_LATE",
        routing_id="RTG_LATE",
        due_date=35,
        release_time=25,
    )

    plant = Plant(
        id="PLANT_SUPPLY_DELAY",
        name="Supply Delay Scenario",
        machines=[m1],
        work_centers=[wc],
        materials=[mat],
        routings=[rtg],
        orders=[ord_late],
        horizon=500,
    )
    return plant, "ORD_LATE"


def create_cleanout_gap_instance() -> tuple[Plant, str]:
    """Instance 3: Sequence-dependent cleanout setup pushes order past due date."""
    m1 = Machine(id="M1", work_center_id="WC1")
    wc = WorkCenter(id="WC1", machine_ids=["M1"])

    mat_ti = Material(id="MAT_TI", family=MaterialFamily.TITANIUM)
    mat_al = Material(id="MAT_AL", family=MaterialFamily.ALUMINIUM)

    op_ti = Operation(
        id="OP10",
        sequence=10,
        eligible_machine_ids=["M1"],
        processing_time=20,
        material_id="MAT_TI",
    )
    rtg_ti = Routing(id="RTG_TI", product_material_id="MAT_TI", operations=[op_ti])

    op_al = Operation(
        id="OP10",
        sequence=10,
        eligible_machine_ids=["M1"],
        processing_time=15,
        material_id="MAT_AL",
    )
    rtg_al = Routing(id="RTG_AL", product_material_id="MAT_AL", operations=[op_al])

    # Titanium must run first (due at 20, critical priority)
    ord_ti = Order(
        id="ORD_TI",
        routing_id="RTG_TI",
        material_id="MAT_TI",
        due_date=20,
        priority=OrderPriority.CRITICAL,
        weight=100.0,
    )
    # Aluminium due at 50, normal priority
    ord_late = Order(
        id="ORD_LATE",
        routing_id="RTG_AL",
        material_id="MAT_AL",
        due_date=50,
        priority=OrderPriority.NORMAL,
        weight=1.0,
    )

    # Cleanout of 30m when switching Titanium <-> Aluminium
    co_matrix = ChangeoverMatrix(
        entries=[
            ChangeoverEntry(
                from_material=MaterialFamily.TITANIUM,
                to_material=MaterialFamily.ALUMINIUM,
                machine_id="M1",
                setup_time=30,
            ),
            ChangeoverEntry(
                from_material=MaterialFamily.ALUMINIUM,
                to_material=MaterialFamily.TITANIUM,
                machine_id="M1",
                setup_time=30,
            ),
        ]
    )

    plant = Plant(
        id="PLANT_CLEANOUT",
        name="Cleanout Gap Scenario",
        machines=[m1],
        work_centers=[wc],
        materials=[mat_ti, mat_al],
        routings=[rtg_ti, rtg_al],
        orders=[ord_ti, ord_late],
        changeover_matrix=co_matrix,
        horizon=500,
    )
    return plant, "ORD_LATE"


def create_downtime_overlap_instance() -> tuple[Plant, str]:
    """Instance 4: Planned maintenance blackout overlaps operation window."""
    m1 = Machine(id="M1", work_center_id="WC1")
    wc = WorkCenter(id="WC1", machine_ids=["M1"])
    mat = Material(id="MAT1", family=MaterialFamily.STEEL)

    op = Operation(id="OP10", sequence=10, eligible_machine_ids=["M1"], processing_time=25)
    rtg = Routing(id="RTG_LATE", product_material_id="MAT1", operations=[op])

    ord_late = Order(id="ORD_LATE", routing_id="RTG_LATE", due_date=35, release_time=0)

    # Machine downtime from 10 to 30
    downtime = DowntimeWindow(
        machine_id="M1", start=10, end=30, reason="Spindle bearing replacement"
    )

    plant = Plant(
        id="PLANT_DOWNTIME",
        name="Downtime Overlap Scenario",
        machines=[m1],
        work_centers=[wc],
        materials=[mat],
        routings=[rtg],
        orders=[ord_late],
        downtimes=[downtime],
        horizon=500,
    )
    return plant, "ORD_LATE"


def create_routing_cascade_instance() -> tuple[Plant, str]:
    """Instance 5: Sequential operations along routing exceed due date."""
    m1 = Machine(id="M1", work_center_id="WC1")
    m2 = Machine(id="M2", work_center_id="WC1")
    m3 = Machine(id="M3", work_center_id="WC1")
    wc = WorkCenter(id="WC1", machine_ids=["M1", "M2", "M3"])
    mat = Material(id="MAT1", family=MaterialFamily.STEEL)

    # 4 operations of 15m each = 60m total duration
    op1 = Operation(id="OP10", sequence=10, eligible_machine_ids=["M1"], processing_time=15)
    op2 = Operation(id="OP20", sequence=20, eligible_machine_ids=["M2"], processing_time=15)
    op3 = Operation(id="OP30", sequence=30, eligible_machine_ids=["M3"], processing_time=15)
    op4 = Operation(id="OP40", sequence=40, eligible_machine_ids=["M1"], processing_time=15)

    rtg = Routing(id="RTG_CASCADE", product_material_id="MAT1", operations=[op1, op2, op3, op4])
    # Due date is 45, but cumulative routing time is 60 -> 15m late
    ord_late = Order(id="ORD_LATE", routing_id="RTG_CASCADE", due_date=45, release_time=0)

    plant = Plant(
        id="PLANT_CASCADE",
        name="Routing Cascade Scenario",
        machines=[m1, m2, m3],
        work_centers=[wc],
        materials=[mat],
        routings=[rtg],
        orders=[ord_late],
        horizon=500,
    )
    return plant, "ORD_LATE"
