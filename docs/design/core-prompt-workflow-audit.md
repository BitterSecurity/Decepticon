# Core prompt separation audit

The audit covers every file in agents/prompts/standard. Core owns capability,
authorization, evidence honesty and actual tool restrictions. Domain methods are
reusable Skillogy leaves; business sequencing is in runtime WORKFLOW.md catalogs.

| Standard role | Decision and preserved capability |
| --- | --- |
| decepticon | Removed fixed recon→exploit and universal final-report process. Kept fresh-context delegation, live task/OPPLAN schema, fault handling, credential preservation and optional workload lifecycle. |
| recon | Removed first-observation exit and arbitrary file caps. Kept observation fidelity, bounded enumeration, workspace anchoring, session/proxy evidence and SUMMARY.md handoff. |
| exploit | Removed foothold-only success, fixed terminal templates and automatic beacon handoff. Kept bounded proof/controls, version-specific research, productive harness state, and session provenance. |
| postexploit | Removed mandatory expanding credential→escalation→lateral loop. Kept capability, proof per access hop, credential handling, kill-switch and cleanup evidence. |
| analyst | Moved hunting lanes to methods; removed severity quotas and unconditional crown-jewel graph goals. Kept taint/diff/fuzz/dependency expertise and real KG write/provenance contracts. |
| cloud_hunter | Moved cloud lanes and removed fixed collect→audit→scan→metadata sequence. Kept IAM/cloud/k8s capability and non-destructive scope restrictions. |
| ad_operator | Moved AD hunting lanes and removed mandatory roast/DCSync/domain-admin sequence. Kept KG versus BloodHound distinction, replication authorization and evidence requirements. |
| reverser | Extracted binary-analysis prerequisites and evidence limits into methods; removed the fixed triage loop. Kept REA discovery/evidence-manifest contract, Radare2 fallback and bounded decompilation. |
| contract_auditor | Moved audit lanes; removed bounty-specific reporting and invented severity-to-CVSS mapping. Kept contract mechanisms, Foundry evidence and Slither/KG distinction. |
| finding_verifier | Retained independent single-finding adjudication, controls, exact verdict mutation and evidence return. Expanded evidence judgment and verdict contract with independent positive/negative adjudication; engagement sequencing stays in workflow. |
| finding_reporter | Retained existing finding-report skill, cvss_score/check_finding_report contracts and canonical-file semantics. Renamed local procedure block; selected workflow decides dispatch. |
| forensicator | Extracted DFIR source/correlation methods; removed the startup/handoff loop. Kept analysis-only boundary, evidence custody, specialist catalog and structured handoff. |
| ics_operator | Extracted industrial protocol/state interpretation into methods. Kept industrial safety/scope restrictions and hardware/tool requirements. |
| iot_operator | Extracted firmware/radio prerequisites and evidence interpretation into methods. Kept device/write/radio scope and evidence handoff. |
| mobile_operator | Extracted static/runtime evidence interpretation into methods. Kept device/data restrictions, platform catalogs and handoff. |
| osint_operator | Extracted public-source provenance/correlation into methods. Kept passive-only boundary, open-web tool contracts and protected secret handling. |
| phisher | Extracted artifact/event attribution into methods; rollout belongs to workflow. Kept authorization/deconfliction, user exclusions, data handling and campaign handoff. |
| supply_chain_operator | Extracted dependency/build resolution and harmless validation methods. Kept harmless proof boundary, upstream scope and no secret/backdoor publication. |
| wireless_operator | Extracted capture context and protocol evidence methods. Kept hardware deployment modes, regulatory domain and per-network authorization. |
| soundwave | Unchanged: intentional interview/planning adapter. Its planning fragment remains explicitly loaded only for this role. |
| autohunt | Unchanged: intentional conservative planning bootstrap adapter, not a generic capability specialist. |
| blue_cell | Unchanged: independent read-only defensive runtime with sensor/event contracts, not an offensive business workflow. |

The 16 new leaves are `skills/standard/<domain>/specialist-methods/SKILL.md` for
ad, analyst, cloud, contracts, dfir, exploit, ics, iot, mobile, osint, phisher,
post-exploit, recon, reverser, supply-chain and wireless. Existing technique leaves
remain authoritative and are reused. Removed seven operational role workflow files
(decepticon, recon, exploit, postexploit, mobile_operator, phisher, wireless_operator)
after preserving useful session, harness, access-proof and domain methods. No generic
role-name workflow loader remains. Adding another role fragment cannot silently
change a core prompt.

APT campaign personas are workflow/preset adapters, not new stable capability roles.
No APT standard prompt exists in this inventory; adapter registration is outside
this prompt assignment. The runtime workflow interface supports an APT-style
objective template without altering recon/exploit/analyst identity.

Builtin workflows preserve production coverage and reporting differences:
bug-bounty adds program eligibility and submission constraints; red-team adds
mission paths, conditional access expansion and cleanup; pentest tracks explicit
coverage and remediation; vulnerability-research tracks versions, root cause,
minimal reproducers and research limitations. All retain independent verification,
report completeness checks, negative results, false positives and uncertainty.

BugClaw extraction analysis and rejected assumptions: see bugclaw-core-extraction.md.
