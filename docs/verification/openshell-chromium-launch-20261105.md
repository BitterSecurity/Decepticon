# Chromium launch inside stock OpenShell

Proposed source correction, stacked on #1163, from freshly fetched main `44eb9032651c18df3f40e9f19a09e860d74ab43e`. No release, production deployment, traffic switch or SaaS equivalence claim. The measured scope is the sandbox open-web Node launch path inside actual stock OpenShell0.1.6.

The existing Chromium156 baseline aborts with exit134/credential EPERM. Selected stock OpenShell blocks `capset`; Chromium's zygote-child path attempts a capability drop. A separate native local diagnostic reproduces the abort in1.207s and renders the owned local HTML in0.861s with no-zygote and memory-backed toolkit settings. The runtime seccomp/Landlock/capability policy is unchanged. Existing no-sandbox/dev-shm flags remain; child processes still run inside OpenShell confinement.

The actual normal Playwright template revealed another defect: its native clean environment does not inherit the image's Chromium-path ENV, so it selected an absent Chrome channel. A shared `chromium_options.js` now prefers an explicit configured path, otherwise the image's installed `/usr/bin/chromium`, with the historical Chrome-channel fallback outside that image. Both existing Node templates use this helper. `GSETTINGS_BACKEND=memory` concerns toolkit settings; browser profile storage remains persistent. The native boundary and web URL safety guard are unchanged.

## Actual desktop flow

The final canonical full sandbox image is `sha256:85711442a1cc62458baaa9e383783644c6c3c7a3f4ce6dff9bf4faf8e8526cfb`. Its normal browser/Exploit-DB tools remain enabled. The shared build-slot build succeeds. Installed-wheel, source and immutable image bytes match for both launch templates and the new helper; the private wheel SHA is in [the measured record](openshell-chromium-launch-20261105.json). No package was published.

Run the maintained `packages/decepticon/tests/validation/managed_native.py` against a dedicated mTLS gateway with `--browser`, following #1163's setup and explicit Docker host alias. The real OSS resolver verifies the admitted native policy/ACK/binding before the daemon executes the immutable desktop template. Owned HTTP fixtures only; no model or external target.

The actual `playwright-extra+stealth` flow succeeds twice, in8.299s and7.368s, with HTTP200, executed DOM JavaScript, the returned fixture cookie and localStorage visit1→2 across separate persistent-context launches. Two workspaces retain file/RoE isolation and tmux state. An approved owned listener receives the request; the unapproved port receives zero. The observer requires this exact sandbox's native synthetic-DNS mapping and Supervisor policy-denial record, rather than treating a timeout as enforcement. Four concurrent daemon reattachments retain process/data; changed approved bytes refuse future execution. The whole lane passes in40.450s, both sandbox records are deleted, the final SDK listing is empty and the owned gateway exits0.

## Gates and retained failures

73 affected sandbox-web cases pass in9.14s. Full fast Python has6,230passes,3Windows-only skips and17warnings in71.03s. Repository lint passes:895formatted files,0type errors and972inherited warnings. All three JavaScript files pass Node syntax. No hosted CI pass or protection bypass is claimed.

Preserve earlier failed measurements: the first fixture gateway omitted the explicit Docker host alias, producing no listener traffic; the corrected setup then exposed the missing Chrome-path issue. A subsequent empty output element was hidden to Playwright's default visible-selector wait, which legitimately reloaded the fixture and incremented its counter. Give the owned proof element visible text and bound its denied fetch; keep the product's normal selector/reload behavior and policy checks. Final successful observation includes both actual visits, not a weakened assertion or widened product readiness timeout. Each native attempt removes its own resources.

This qualifies local Docker29.6.1/WSL2 Linux6.18.54.1 and the desktop Node path. Mobile shares the helper and passes syntax; no repeated mobile/resolution browser sweep was performed. Actual E2B/newer-inner-kernel validation continues separately in SaaS #1604. This does not qualify the Python `browser_action` host path, other tool images, commercial entitlement, production multi-tenancy/billing, aggregate VM recovery or OSS publication.

Primary implementation references: [selected stock child seccomp](https://github.com/NVIDIA/OpenShell/blob/ac7f615cd12d5cc49cb16eb1f5f10ffeb1e90f47/crates/openshell-sandbox/src/linux/child_seccomp.rs), [Chromium156 credential service](https://chromium.googlesource.com/chromium/src/+/refs/tags/156.0.8078.47/sandbox/linux/services/credentials.cc). The observed packaged fatal-line number may differ from the unpatched upstream source; no exact line equivalence is asserted.
