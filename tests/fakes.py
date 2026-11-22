"""A scripted stand-in for the Anthropic client (no network in tests)."""

from __future__ import annotations

from types import SimpleNamespace

from ducan.agent.client import Agent


def response(blocks, stop="tool_use"):
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop,
        model="test-model",
        usage=SimpleNamespace(input_tokens=50, output_tokens=20, cache_read_input_tokens=0),
    )


def tool_use(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)


def fake_agent(app_config, responses):
    app_config.env.anthropic_model = "test-model"
    client = SimpleNamespace(messages=FakeMessages(responses))
    return Agent(app_config.env, client=client), client
