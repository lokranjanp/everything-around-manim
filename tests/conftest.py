import pytest

from manim_agent.db import Base as AgentBase
from manim_agent.db import engine as agent_engine
from manim_renderer.db import Base as RenderBase
from manim_renderer.db import engine as render_engine


@pytest.fixture(autouse=True)
def clean_databases():
    AgentBase.metadata.drop_all(agent_engine)
    RenderBase.metadata.drop_all(render_engine)
    AgentBase.metadata.create_all(agent_engine)
    RenderBase.metadata.create_all(render_engine)
    yield

