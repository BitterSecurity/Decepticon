---
name: bloodhound-query
description: Analyze authorized AD collection data in BloodHound CE with the active runtime's supported tools and mirrored GUI.
metadata:
  subdomain: active-directory
  when_to_use: "bloodhound collection upload object profile exposure shortest path cypher active directory"
  mitre_attack:
    - T1087.002
    - T1018
    - T1482
---

# BloodHound analysis

BloodHound analyzes collected AD data; discovering a domain or opening its viewer does not populate the graph. Work only with the authorized domain and an existing collection path. Report missing collection access rather than claiming an empty graph proves safety.

## Hosted engagement

1. Use the engagement-isolated BloodHound CE companion. If a compatible SharpHound ZIP already exists under `/workspace`, call `bloodhound_mcp_upload_collection(path=...)`. Check that the returned terminal status is `Complete`; report partial or failed ingest.
2. If no ZIP exists, collect only through an authorized foothold and transfer its ZIP to `/workspace`. Alternatively, where a scoped DC is reachable and a Kerberos cache exists in `/workspace`, use `bloodhound_mcp_collect_domain` with the matching domain, DC, username, and cache. Begin with `DCOnly`; `Default` and `Session` require an explicit computer allowlist. Never pass passwords or hashes as MCP arguments.
3. After a completed upload, check `bloodhound_mcp_data_quality` and `bloodhound_mcp_domain_overview`. Use `bloodhound_mcp_find_objects` and `bloodhound_mcp_object_profile` for specific principals; `bloodhound_mcp_exposure_finder` and `bloodhound_mcp_shortest_path` for bounded questions. Use `bloodhound_mcp_cypher_query` only when semantic tools cannot answer a read-only question. Bound returned rows and path depth.
4. Check GUI follow status before claiming that the operator can see the selected object or path. Distinguish a candidate graph path from an action proven by independent evidence. Record confirmed results through the active engagement's finding and OPPLAN tools.

The hosted MCP graph is separate from any standalone BloodHound instance. Do not call `bhce_*`, `bh_ingest_zip`, `bh_cypher`, or a local `cypher-shell` for hosted analysis.

## Standalone OSS runtime

If the hosted MCP tools are absent and the standalone `bhce_*` tools are registered, use `bhce_status` to check the local instance, `bhce_ingest_zip` for a compatible ZIP, and `bhce_cypher` for bounded read-only queries. Never treat data from another instance as the current engagement's collection.
