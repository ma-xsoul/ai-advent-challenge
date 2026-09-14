"""Агент как отдельная сущность: конфиг, транспорт и диалог под одной крышей."""

from .agent import (
    Agent,
    AgentReply,
    AgentUsage,
    CompressionFailed,
    CompressionRecord,
    CompressionStarted,
    ContextOverflowError,
    Message,
    Summary,
    TurnStats,
)
from .config import AgentConfig, CompressionPolicy
from .registry import AgentRegistry
from .scenarios import SCENARIOS, Fact, FactCheck, Scenario, get_scenario
from .store import JsonStore, StoreError, StoredSession
from .tokens import estimate_tokens
from .transport import DeepSeekTransport, Transport, TransportError

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentRegistry",
    "AgentReply",
    "AgentUsage",
    "CompressionFailed",
    "CompressionPolicy",
    "CompressionRecord",
    "CompressionStarted",
    "ContextOverflowError",
    "DeepSeekTransport",
    "Fact",
    "FactCheck",
    "JsonStore",
    "Message",
    "SCENARIOS",
    "Scenario",
    "StoreError",
    "StoredSession",
    "Summary",
    "TurnStats",
    "estimate_tokens",
    "get_scenario",
    "Transport",
    "TransportError",
]
