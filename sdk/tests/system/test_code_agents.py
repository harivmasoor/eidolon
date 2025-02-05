from typing import Annotated

import httpx
import pytest
import pytest_asyncio
from fastapi import Body, HTTPException

from eidolon_ai_client.client import Agent, Process, ProcessStatus
from eidolon_ai_client.events import (
    ErrorEvent,
    AgentStateEvent,
    StringOutputEvent,
    StartStreamContextEvent,
    EndStreamContextEvent,
    SuccessEvent,
)
from eidolon_ai_client.util.aiohttp import AgentError
from eidolon_ai_sdk.agent.agent import register_program, AgentState, register_action
from eidolon_ai_sdk.util.stream_collector import stream_manager


async def run_program(agent, program, **kwargs) -> ProcessStatus:
    process = await Agent.get(agent).create_process()
    return await process.action(program, **kwargs)


class HelloWorld:
    created_processes = set()

    @classmethod
    async def create_process(cls, process_id):
        HelloWorld.created_processes.add(process_id)

    @classmethod
    async def delete_process(cls, process_id):
        HelloWorld.created_processes.remove(process_id)

    @register_program()
    async def idle(self, name: Annotated[str, Body()]):
        if name.lower() == "hello":
            raise HTTPException(418, "hello is not a name")
        if name.lower() == "error":
            raise Exception("big bad server error")
        return f"Hello, {name}!"

    @register_program()
    async def idle_streaming(self, name: Annotated[str, Body()]):
        if name.lower() == "hello":
            raise HTTPException(418, "hello is not a name")
        if name.lower() == "error":
            raise Exception("big bad server error")
        yield StringOutputEvent(content=f"Hello, {name}!")

    @register_program()
    async def lots_o_context(self):
        yield StringOutputEvent(content="1")
        yield StringOutputEvent(content="2")
        async for e in _m(_s(3, 4), context="c1"):
            yield e
        async for e in _m(_s(5, 6, after=_m(_s(7, 8), context="c3")), context="c2"):
            yield e


async def _s(*_args, after=None):
    for a in _args:
        yield StringOutputEvent(content=str(a))
    if after:
        async for a in after:
            yield a


def _m(stream, context: str):
    return stream_manager(stream, StartStreamContextEvent(context_id=context, title=context))


@pytest.fixture(autouse=True)
def manage_hello_world_state():
    HelloWorld.created_processes = set()
    yield
    HelloWorld.created_processes = set()


class TestHelloWorld:
    @pytest_asyncio.fixture(scope="class")
    async def server(self, run_app):
        async with run_app(HelloWorld) as ra:
            yield ra

    @pytest_asyncio.fixture(scope="function")
    async def client(self, server):
        with httpx.Client(base_url=server, timeout=httpx.Timeout(60)) as client:
            yield client

    @pytest.fixture(scope="function")
    def agent(self, server) -> Agent:
        return Agent.get("HelloWorld")

    def test_can_start(self, client):
        docs = client.get("/docs")
        assert docs.status_code == 200

    async def test_hello_world(self, agent):
        process = await agent.create_process()
        post = await process.action("idle", "world")
        assert post.data == "Hello, world!"

    async def test_automatic_state_transition(self, agent):
        process = await agent.create_process()
        post = await process.action("idle", "world")
        assert post.state == "terminated"

    @pytest.mark.parametrize("program", ["idle", "idle_streaming"])
    async def test_http_error(self, server, program):
        with pytest.raises(AgentError) as exc:
            process = await Agent.get("HelloWorld").create_process()
            await process.action(program, "hello")
        assert exc.value.response.status_code == 418
        assert exc.value.response.json()["data"] == "hello is not a name"

    @pytest.mark.parametrize("program", ["idle", "idle_streaming"])
    async def test_streaming_http_error(self, server, program):
        agent = Agent.get("HelloWorld")
        stream = (await agent.create_process()).stream_action(program, "hello")
        events = {type(e): e async for e in stream}
        assert ErrorEvent in events
        assert events[ErrorEvent].reason == "hello is not a name"
        assert events[ErrorEvent].details["status_code"] == 418
        assert events[AgentStateEvent].state == "http_error"

        found = await Process.get(stream).status()
        assert found.state == "http_error"

    @pytest.mark.parametrize("program", ["idle", "idle_streaming"])
    async def test_unhandled_error(self, server, program):
        with pytest.raises(AgentError) as exc:
            process = await Agent.get("HelloWorld").create_process()
            await process.action(program, "error")
        assert exc.value.response.status_code == 500
        assert exc.value.response.json() == {
            "available_actions": [],
            "data": "big bad server error",
            "process_id": f"test_unhandled_error[{program}]_0",
            "state": "unhandled_error",
        }

    @pytest.mark.parametrize("program", ["idle", "idle_streaming"])
    async def test_streaming_unhandled_error(self, agent, program):
        stream = (await agent.create_process()).stream_action(program, "error")
        events = {type(e): e async for e in stream}
        assert ErrorEvent in events
        assert events[ErrorEvent].reason == "big bad server error"
        assert events[AgentStateEvent].state == "unhandled_error"

        found = await Process.get(stream).status()
        assert found.state == "unhandled_error"

    async def test_lots_o_context(self, agent):
        process = await agent.create_process()
        resp = await process.action("lots_o_context")
        assert resp.data == "12"

    async def test_lots_o_context_streaming(self, agent):
        events = [e async for e in (await agent.create_process()).stream_action("lots_o_context")]
        assert events[2:-1] == [
            StringOutputEvent(content="1"),
            StringOutputEvent(content="2"),
            StartStreamContextEvent(context_id="c1", title="c1"),
            StringOutputEvent(content="3", stream_context="c1"),
            StringOutputEvent(content="4", stream_context="c1"),
            SuccessEvent(stream_context="c1"),
            EndStreamContextEvent(context_id="c1"),
            StartStreamContextEvent(context_id="c2", title="c2"),
            StringOutputEvent(content="5", stream_context="c2"),
            StringOutputEvent(content="6", stream_context="c2"),
            StartStreamContextEvent(context_id="c3", stream_context="c2", title="c3"),
            StringOutputEvent(content="7", stream_context="c2.c3"),
            StringOutputEvent(content="8", stream_context="c2.c3"),
            SuccessEvent(stream_context="c2.c3"),
            EndStreamContextEvent(stream_context="c2", context_id="c3"),
            SuccessEvent(stream_context="c2"),
            EndStreamContextEvent(context_id="c2"),
            AgentStateEvent(state="terminated", available_actions=[]),
        ]

    async def test_creating_processes_without_program(self, agent):
        process = await agent.create_process()
        status = await process.status()
        assert status.state == "initialized"
        assert "idle" in status.available_actions
        action = await process.action("idle", "Luke")
        assert action.data == "Hello, Luke!"

    async def test_delete_process(self, agent):
        process = await agent.create_process()
        deleted = await process.delete()
        assert deleted.process_id == process.process_id
        assert deleted.deleted == 1
        with pytest.raises(AgentError) as exc:
            await process.status()
        assert exc.value.response.status_code == 404

    async def test_agent_create_delete_hooks(self, agent):
        assert not HelloWorld.created_processes

        # we expect to observe the process being created as a side effect of calling create_process
        process = await agent.create_process()
        assert HelloWorld.created_processes

        # and we should see it cleaned up as part of process deletion
        await process.delete()
        assert not HelloWorld.created_processes


class StateMachine:
    @register_action("ap")
    @register_program()
    async def action_program(self):
        return AgentState[str](name="ap", data="default response")

    @register_program()
    # async def idle(self, desired_state: Annotated[str, Body()], response: Annotated[str, Body()] = "default response"):
    async def idle(self, desired_state: Annotated[str, Body()], response: Annotated[str, Body()]):
        return AgentState(name=desired_state, data=response)

    @register_action("foo", "bar")
    async def to_bar(self):
        return AgentState(name="bar", data="heading to the bar")

    @register_action("foo")
    async def to_church(self):
        return AgentState(name="church", data="man of god")

    @register_action("church")
    async def terminate(self):
        return "Only God can terminate me"


class StateMachine2(StateMachine):
    pass


class TestStateMachine:
    @pytest_asyncio.fixture(scope="class")
    async def server(self, run_app):
        async with run_app(StateMachine, StateMachine2) as ra:
            yield ra

    @pytest_asyncio.fixture(scope="function")
    async def client(self, server):
        async with httpx.AsyncClient(base_url=server, timeout=httpx.Timeout(60)) as client:
            yield client

    async def test_can_list_processes(self, client):
        first = (
            await run_program("StateMachine", "idle", json=dict(desired_state="church", response="blurb"))
        ).process_id
        second = (await run_program("StateMachine", "idle", json=dict(desired_state="foo", response="blurb"))).process_id
        third = (await run_program("StateMachine", "idle", json=dict(desired_state="foo", response="blurb"))).process_id

        processes = await client.get("/agents/StateMachine/processes")
        assert processes.json()["total"] == 3
        assert {p["process_id"] for p in processes.json()["processes"]} == {first, second, third}

        # update the first process: it should be at end of list now
        assert first == (await Agent.get("StateMachine").process(first).action("terminate")).process_id

        processes = await client.get("/agents/StateMachine/processes")
        assert processes.json()["total"] == 3
        assert [p["process_id"] for p in processes.json()["processes"]] == [second, third, first]

    async def test_can_start(self):
        post = await run_program(
            "StateMachine", "idle", json=dict(desired_state="bar", response="low man on the totem pole")
        )
        assert post.state == "bar"
        assert post.data == "low man on the totem pole"

    async def test_can_transition_state(self):
        init = await run_program(
            "StateMachine", "idle", json=dict(desired_state="foo", response="low man on the totem pole")
        )
        assert init.state == "foo"

        to_bar = await init.action("to_bar")
        assert to_bar.state == "bar"

    async def test_allowed_actions(self):
        process = await Agent.get("StateMachine").create_process()
        init = await process.action("idle", json=dict(desired_state="foo", response="low man on the totem pole"))
        assert "to_church" in init.available_actions

        to_bar = await process.action("to_bar")
        assert "to_church" not in to_bar.available_actions

        # now test that this action throws a AgentError and assert that the status_code is 409
        with pytest.raises(AgentError) as exc:
            await process.action("to_church")
        assert exc.value.response.status_code == 409

    @pytest.mark.skip(reason="un comment idle signature when bug is fixed")
    async def test_default_in_body(self):
        init = await run_program(
            "StateMachine",
            "idle",
            json=dict(desired_state="foo", response="low man on the totem pole"),
        )
        init.raise_for_status()
        assert init.data == "default response"

    async def test_state_machine_termination(self):
        process = await Agent.get("StateMachine").create_process()
        init = await process.action("idle", json=dict(desired_state="church", response="blurb"))
        assert init.state == "church"

        terminated = await process.action("terminate")
        assert terminated.state == "terminated"
        assert terminated.data == "Only God can terminate me"
        assert terminated.available_actions == []

    async def test_can_register_function_as_action_and_program(self):
        process = await Agent.get("StateMachine").create_process()
        await process.action("action_program")
        await process.action("action_program")

    async def test_agents_are_separate(self):
        process = await Agent.get("StateMachine").create_process()
        init = await process.action("idle", json=dict(desired_state="church", response="blurb"))
        assert init.state == "church"

        with pytest.raises(AgentError) as exc:
            await Agent.get("StateMachine2").process(process.process_id).action("terminate")
        assert exc.value.response.status_code == 404
