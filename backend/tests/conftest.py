import pytest

from app import memory as memory_mod
from app import save_agent
from app.store import store


@pytest.fixture(autouse=True)
def reset_state():
    memory_mod.memory.records.clear()
    save_agent.save_log.actions.clear()
    save_agent.save_log.rules = save_agent.SaveRules()
    save_agent.save_log.executor = save_agent.dry_run_executor
    store._exps.clear()
    yield
