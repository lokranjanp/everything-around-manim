import pytest

from manim_agent.db import Base as AgentBase
from manim_agent.db import engine as agent_engine


@pytest.fixture(autouse=True)
def clean_databases():
    AgentBase.metadata.drop_all(agent_engine)
    AgentBase.metadata.create_all(agent_engine)
    yield
