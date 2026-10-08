---
name: bloodhound-bhce
description: BloodHound CE runtime selection and collection analysis for hosted MCP or standalone OSS tools.
metadata:
  subdomain: active-directory
  when_to_use: "bloodhound bhce sharphound zip ingest domain users groups attack paths"
  mitre_attack:
    - T1078.002
    - T1558.003
    - T1649
    - T1003.006
  upstream_url: https://bloodhound.specterops.io/
---

# BloodHound CE runtime

First identify which BloodHound tools the active agent actually has. The hosted engagement uses its own isolated `bloodhound_mcp_*` companion and live GUI; standalone OSS may expose `bhce_*` tools for a different local instance. Never mix their data or credentials. Load `/skills/standard/ad/bloodhound-query/SKILL.md` only when its detailed procedure is relevant; the path is not an automatic prerequisite.

In hosted runs, `bloodhound_mcp_upload_collection` ingests an authorized ZIP under `/workspace`. Verify terminal `Complete`, then use `bloodhound_mcp_data_quality`, `bloodhound_mcp_domain_overview`, `bloodhound_mcp_find_objects`, `bloodhound_mcp_object_profile`, `bloodhound_mcp_exposure_finder`, and `bloodhound_mcp_shortest_path`. Use `bloodhound_mcp_cypher_query` only for bounded read-only questions. The resulting GUI follow status is evidence that the remote viewer moved; opening the viewer by itself does not collect or analyze AD data.

In standalone OSS, when `bhce_*` tools are actually registered, use `bhce_status` to check the local instance, `bhce_ingest_zip` to import a compatible collection, and `bhce_cypher` to inspect its graph. The retired in-house `bh_ingest_zip` and related wrappers are deprecated. Do not assume a fixed BloodHound version or schema; use the actual instance's reported capabilities.
