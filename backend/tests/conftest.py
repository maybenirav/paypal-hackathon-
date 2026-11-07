import os

# Must run before any app import: isolated in-memory DB, no network, no seeded demo.
os.environ["PLANLAB_DB"] = ":memory:"
os.environ["GEMINI_API_KEY"] = ""
os.environ["SEED_DEMO"] = "false"
os.environ["SAVE_AGENT_LIVE"] = "false"

import pytest  # noqa: E402

from app import memory as memory_mod  # noqa: E402
from app import save_agent  # noqa: E402
from app.store import store  # noqa: E402


@pytest.fixture(autouse=True)
def reset_state():
    store.reset()
    memory_mod.memory.records.clear()
    save_agent.save_log.reset()
    save_agent.save_log.executor = save_agent.dry_run_executor
    yield
