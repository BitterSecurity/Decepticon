"""Workflow boundaries and real LangChain/checkpointer execution."""


def source(workflow_id: str = "red-team", stages: str = "[]", body: str = "") -> str:
    return (
        f"---\nid: {workflow_id}\nversion: '1'\ndescription: Test\nstages: {stages}\n---\n"
        f"## Common\n{body}\n## Agent: recon\nrecon context\n## Agent: exploit\nexploit context\n"
    )
