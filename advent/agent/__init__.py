"""Агент как отдельная сущность: конфиг, транспорт и диалог под одной крышей."""

from .agent import (
    Agent,
    AgentReply,
    AgentUsage,
    CompressionFailed,
    CompressionRecord,
    CompressionStarted,
    ContextOverflowError,
    FactsFailed,
    FactsRecord,
    FactsStarted,
    Message,
    Summary,
    TurnStats,
)
from .branches import Branch, Checkpoint, DialogTree, TreeStore
from .config import AgentConfig, CompressionPolicy, FactsPolicy
from .registry import AgentRegistry
from .scenarios import (
    BRANCH_SCENARIOS,
    SCENARIOS,
    BranchScenario,
    BranchTail,
    Fact,
    FactCheck,
    Scenario,
    get_branch_scenario,
    get_scenario,
)
from .store import JsonStore, StoreError, StoredSession
from .tokens import estimate_tokens
from .transport import DeepSeekTransport, Transport, TransportError

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentRegistry",
    "AgentReply",
    "AgentUsage",
    "BRANCH_SCENARIOS",
    "Branch",
    "BranchScenario",
    "BranchTail",
    "Checkpoint",
    "CompressionFailed",
    "CompressionPolicy",
    "CompressionRecord",
    "CompressionStarted",
    "ContextOverflowError",
    "DeepSeekTransport",
    "DialogTree",
    "Fact",
    "FactCheck",
    "FactsFailed",
    "FactsPolicy",
    "FactsRecord",
    "FactsStarted",
    "JsonStore",
    "Message",
    "SCENARIOS",
    "Scenario",
    "StoreError",
    "StoredSession",
    "Summary",
    "TreeStore",
    "TurnStats",
    "estimate_tokens",
    "get_branch_scenario",
    "get_scenario",
    "Transport",
    "TransportError",
]
