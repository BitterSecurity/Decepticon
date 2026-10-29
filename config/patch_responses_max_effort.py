"""Build-time compatibility for LiteLLM 1.88.1's Responses effort mapper.

That mapper silently returns None for "max". Extend only its existing xhigh
branch, retaining the library's auto-summary semantics. Fail the image build
if the reviewed source shape changes. Remove after a qualified SDK upgrade
provides this branch natively. No provider or model admission changes here.
"""

import sysconfig
from pathlib import Path

RELATIVE = Path("completion_extras/litellm_responses_transformation/transformation.py")
BRANCH = '''        elif reasoning_effort == "xhigh":
            return Reasoning(effort="xhigh", summary="detailed") if auto_summary_enabled else Reasoning(effort="xhigh")  # type: ignore[typeddict-item]
'''
MAX_BRANCH = BRANCH.replace('"xhigh"', '"max"')


def patch(path: Path) -> None:
    source = path.read_text()
    if MAX_BRANCH in source:
        return
    if source.count(BRANCH) != 1:
        raise RuntimeError(f"Unreviewed LiteLLM Responses effort mapper: {path}")
    path.write_text(source.replace(BRANCH, BRANCH + MAX_BRANCH))


if __name__ == "__main__":
    candidates = {
        Path("/app/litellm") / RELATIVE,
        Path(sysconfig.get_paths()["purelib"]) / "litellm" / RELATIVE,
    }
    files = sorted({path.resolve() for path in candidates if path.is_file()})
    if not files:
        raise RuntimeError("LiteLLM Responses effort mapper not found")
    for path in files:
        patch(path)
