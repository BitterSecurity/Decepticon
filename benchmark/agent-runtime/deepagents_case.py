"""One actual Deep Agents tool loop; imports belong to startup measurement."""

import json

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

print('{"stage":"ready"}', flush=True)
c = json.loads(input())
count = 0


def echo(text: str) -> str:
    global count
    if text != c["marker"] or count:
        raise ValueError("unexpected/repeated tool arguments")
    count += 1
    return json.dumps({"text": text}, separators=(",", ":"))


register_harness_profile(
    f"openai:{c['model']}",
    HarnessProfile(
        base_system_prompt="",
        system_prompt_suffix="",
        excluded_tools=frozenset(
            {
                "write_todos",
                "ls",
                "read_file",
                "write_file",
                "edit_file",
                "delete",
                "glob",
                "grep",
                "execute",
                "task",
            }
        ),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ),
)
model = ChatOpenAI(
    model=c["model"],
    base_url=c["base_url"],
    api_key=SecretStr("local-benchmark"),
    max_retries=0,
    timeout=120,
    use_responses_api=False,
)
tool = StructuredTool.from_function(
    echo, name="echo", description="Return the supplied text.", args_schema=c["schema"]
)
agent = create_deep_agent(model=model, tools=[tool], system_prompt=c["system"])
result = agent.invoke(
    {"messages": [{"role": "user", "content": c["prompt"]}]}, config={"recursion_limit": 12}
)
print(json.dumps({"output": result["messages"][-1].content, "tool_calls": count}), flush=True)
input()
