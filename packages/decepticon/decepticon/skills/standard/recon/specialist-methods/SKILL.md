---
name: recon-specialist-methods
description: Evidence-led recon technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: reconnaissance
  upstream_ref: "Decepticon standard recon core prompt and skills/standard/recon technique references"
  when_to_use: "recon investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Surface inventory semantics

A useful endpoint record includes URL, method, observed parameters, authentication
context, response status and an evidence locator (request capture, source path/line,
or browser observation). Discovered object IDs need their source and ownership
context. A wildcard denotes a namespace, not a resolvable host; use observed DNS,
certificate, redirect or script references as candidates and distinguish resolution
from actual service reachability. Missing guessed names do not imply the whole
namespace is exhausted. Follow redirects only within the permitted target boundary.

## Authentication, data flow and proxy evidence

Record authentication steps, cookie names and protected credential references;
compare relevant behavior with and without session state. For observed trust
boundaries, distinguish a login or admin route existing from that boundary failing.
For data flows, record controlled source, destination and the response/trace tying
them together; reflection is not execution. For concurrency hypotheses, record
observable operation ordering and measured timing, labeling internal behavior as
inferred where appropriate. For proxy hypotheses, preserve frontend/backend
fingerprints and exact request/response traces. Parser status differences are not
proof of desynchronization.

## Collector limitations

A fetched HTTP page is not proof of an interactive browser flow. When browser tools
are exposed, rendered navigation/authentication evidence should include the actual
state and observed UI references. Preserve network timeout, DNS failure, rate limit
and access refusal separately; none proves the service is safe. Use bounded I/O
and injected bash/background semantics. Existing passive, active and web recon
skills provide tool-specific collection recipes.
