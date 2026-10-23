from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

import agentscope
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse
from anolisa_tokenless import ContentOrigin, TokenlessConfig, TokenlessError
from tokenless_agentscope import TokenlessAgentScope, ToolContract

MODE = sys.argv[1]
OUTPUT = Path(sys.argv[2])


async def main():
    with tempfile.TemporaryDirectory(prefix="tokenless-stash-failure-") as temporary:
        config = TokenlessConfig(data_dir=temporary, rtk_enabled=False)
        integration = TokenlessAgentScope(config, tool_contracts={"api": ToolContract(ContentOrigin.API_RESPONSE)})
        payload = (
            "<!doctype html><html><head><title>Storage failure audit</title></head>"
            "<body><main>"
            + (
                '<section class="article-body" data-layout="wide">'
                '<p class="content">Retain this article when the stash is unavailable.</p>'
                "</section>"
            ) * 80
            + "</main></body></html>"
        )
        if agentscope.__version__.startswith("1."):
            toolkit = integration.create_toolkit()
            agent = SimpleNamespace(name="native-audit", toolkit=toolkit, model=object(), register_instance_hook=lambda *args: None)
            integration.install(agent, session_id="native-stash-failure")
            response = ToolResponse(content=[TextBlock(type="text", text=payload)])

            async def invoke():
                return await integration._after_tool(toolkit, {"name": "api", "id": "real-call", "input": {}}, response)

            def text(result):
                return result.content[0]["text"]
        else:
            from agentscope.message import ToolResultState
            response = ToolResponse(content=[TextBlock(text=payload)], state=ToolResultState.SUCCESS)
            agent = SimpleNamespace(name="native-audit", state=SimpleNamespace(session_id="native-stash-failure"))
            call = SimpleNamespace(name="api", id="real-call", input="{}")

            async def handler(**kwargs):
                yield response

            async def invoke():
                items = [item async for item in integration.middleware.on_acting(agent, {"tool_call": call}, handler)]
                assert len(items) == 1
                return items[0]

            def text(result):
                return result.content[0].text

        healthy = await invoke()
        assert text(healthy) != payload, "healthy control did not exercise compression"
        assert text(response) == payload, "input was mutated"
        database = Path(temporary) / "stash.db"
        assert database.is_file()
        with sqlite3.connect(database) as check:
            healthy_stash_entries = check.execute("SELECT COUNT(*) FROM stash").fetchone()[0]
        check.close()
        assert healthy_stash_entries > 0, "healthy control did not exercise the stash backend"
        # Use a fast backend/migration failure, separately from the pipeline
        # deadline that can affect a long-running writer-lock experiment.
        with sqlite3.connect(database) as change:
            change.execute("ALTER TABLE stash RENAME TO unavailable_stash")
        change.close()
        error = None
        result = None
        try:
            result = await invoke()
        except TokenlessError as failure:
            error = str(failure)
        finally:
            with sqlite3.connect(database) as restore:
                restore.execute("ALTER TABLE unavailable_stash RENAME TO stash")
            restore.close()
        observation = {
            "mode": MODE,
            "agentscope": agentscope.__version__,
            "core_error": error,
            "original_response_retained": result is response,
            "healthy_stash_entries": healthy_stash_entries,
        }
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.with_name(OUTPUT.stem + "-observation.json").write_text(json.dumps(observation, indent=2))
        print(json.dumps(observation), flush=True)
        if MODE == "baseline":
            assert error is not None, "original adapter did not expose the backend failure"
        else:
            assert error is None and result is response
            assert text(result) == payload
        recovered = await invoke()
        assert text(recovered) != payload, "compression did not recover after repairing the schema"
        report = {
            "mode": MODE,
            "agentscope": agentscope.__version__,
            "native_sdk": True,
            "backend_failure": "real SQLite stash table temporarily missing after runtime initialization",
            "healthy_control_compressed": True,
            "healthy_stash_entries": healthy_stash_entries,
            "original_mutated": False,
            "core_error": error,
            "original_response_retained": result is response,
            "recovered_after_schema_repair": True,
        }
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(report, indent=2))
        print(json.dumps(report))


asyncio.run(main())
