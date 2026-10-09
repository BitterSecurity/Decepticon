"""Finding export + report generation.

- ``hackerone``   — HackerOne markdown template renderer
- ``bugcrowd``    — Bugcrowd submission CSV writer
- ``executive``   — Engagement-level executive summary composer
- ``timeline``    — Chronological event timeline extractor
- ``sarif``       — SARIF v2.1.0 JSON exporter for GitHub / DefectDojo
- ``export``      — Engagement ZIP bundle, Markdown narrative, and replay import

Renderers operate on ``KnowledgeGraph`` state so the same graph can
feed a bounty submission, an engagement executive summary, a SARIF
upload, and a JSON bundle for further automation.
"""

from __future__ import annotations

from decepticon.tools.reporting.bugcrowd import render_bugcrowd_csv
from decepticon.tools.reporting.cvss import (
    CVSS_TOOLS,
    cvss_score_tool,
    score_vector,
    severity_band,
)
from decepticon.tools.reporting.executive import render_executive_summary
from decepticon.tools.reporting.export import (
    EXPORT_TOOLS,
    export_engagement_markdown,
    export_engagement_zip,
    import_engagement_zip,
)
from decepticon.tools.reporting.hackerone import HackerOneReport, render_hackerone_markdown
from decepticon.tools.reporting.sarif import render_sarif
from decepticon.tools.reporting.timeline import extract_timeline

__all__ = [
    "CVSS_TOOLS",
    "EXPORT_TOOLS",
    "HackerOneReport",
    "cvss_score_tool",
    "export_engagement_markdown",
    "export_engagement_zip",
    "extract_timeline",
    "import_engagement_zip",
    "render_bugcrowd_csv",
    "render_executive_summary",
    "render_hackerone_markdown",
    "render_sarif",
    "score_vector",
    "severity_band",
]
