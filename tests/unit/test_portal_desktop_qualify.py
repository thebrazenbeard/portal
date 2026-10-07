from __future__ import annotations

import portal.desktop_qualify as desktop_qualify


def test_qualification_accepts_only_bound_pre_active_target_routes() -> None:
    select = getattr(desktop_qualify, "_admissible_resident_routes", None)
    assert callable(select)

    routes = [
        {
            "route_id": "preactive-target:vera-base",
            "provider": "pre_active_target",
            "local": True,
            "available": True,
            "current": True,
            "incremental_paid_compute": False,
            "auto_admissible": True,
        },
        {
            "route_id": "ollama:vera-local:latest",
            "provider": "ollama",
            "local": True,
            "available": True,
            "current": True,
            "incremental_paid_compute": False,
            "auto_admissible": True,
        },
    ]

    assert select(routes) == [routes[0]]
