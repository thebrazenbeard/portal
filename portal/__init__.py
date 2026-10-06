"""P.O.R.T.A.L. — Portfolio Orchestration & Repository Tracking Access Layer."""

from .adapters import (
    PortalDispatchRecord,
    PortalReconciliationRecord,
    PortalRouteBinding,
)
from .coordinator import plan_portal_wave
from .execution_router import CapabilityExecutionAdapter
from .discovery import (
    GitHubRepositoryCatalog,
    RepositoryInventoryItem,
    build_live_project_registry,
    discover_live_project_registry,
    write_project_registry,
)
from .models import (
    ExecutionNode,
    PortalAssignment,
    PortalNodeDeferral,
    PortalPlan,
)
from .process_adapter import (
    PortalProposalProcessAdapter,
    build_process_proposal_execution_adapter,
)
from .route_resolver import (
    PortalRouteAdvertisement,
    PortalRouteRequest,
    resolve_portal_route,
)
from .runtime import (
    PortalCycleResult,
    PortalLaneResult,
    PortalRunResult,
    PortalRunStore,
    run_portal_once,
    run_portal_until_idle,
)
from .session import (
    PortalCommandSession,
    PortalRefillResult,
    PortalSessionResult,
)
from .wave_runtime import (
    PortalWavePacket,
    PortalWavePreparationResult,
    PortalWaveStore,
    prepare_portal_wave,
)

__all__ = [
    "resolve_portal_route",
    "build_process_proposal_execution_adapter",
    "PortalRouteRequest",
    "PortalRouteBinding",
    "PortalRouteAdvertisement",
    "PortalReconciliationRecord",
    "PortalProposalProcessAdapter",
    "PortalDispatchRecord",
    "CapabilityExecutionAdapter",
    "ExecutionNode",
    "GitHubRepositoryCatalog",
    "PortalAssignment",
    "PortalCommandSession",
    "PortalCycleResult",
    "PortalLaneResult",
    "PortalNodeDeferral",
    "PortalPlan",
    "PortalRefillResult",
    "PortalRunResult",
    "PortalRunStore",
    "PortalSessionResult",
    "PortalWavePacket",
    "PortalWavePreparationResult",
    "PortalWaveStore",
    "RepositoryInventoryItem",
    "build_live_project_registry",
    "discover_live_project_registry",
    "plan_portal_wave",
    "prepare_portal_wave",
    "run_portal_once",
    "run_portal_until_idle",
    "write_project_registry",
]
