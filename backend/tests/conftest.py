import os

# Tests never touch the real data file or send real alerts. These must be set before the app is imported.
os.environ["PLANLAB_DB"] = ""
os.environ["ALERT_WEBHOOK_URL"] = ""
os.environ["ALERT_EMAIL_TO"] = ""

import pytest  # noqa: E402

from app import memory as memory_mod  # noqa: E402
from app import save_agent  # noqa: E402
from app.store import store  # noqa: E402


@pytest.fixture(autouse=True)
def reset_state():
    memory_mod.memory.records.clear()
    save_agent.save_log.actions.clear()
    save_agent.save_log.rules = save_agent.SaveRules()
    save_agent.save_log.executor = save_agent.dry_run_executor
    store._exps.clear()
    store.seen_webhooks.clear()
    yield
