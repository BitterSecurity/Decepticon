# Desktop browser startup page

This component correction starts from freshly fetched OSS main `df879550544d36b6b1e69062f05adae28537adf2`. The desktop fallback previously launched a persistent Chromium context, which already opens a blank page, then unconditionally created another page. Reuse an open `about:blank` page belonging to that newly created context. When none exists, keep `newPage()`. A navigated or restored nonblank page is never selected. Install the existing URL safety guard before navigating the selected page.

There is no browser pool, cross-context sharing, profile-path change, new privilege, browser flag, policy change or dependency change. Desktop/mobile profiles remain separate. The mobile template is unchanged. Context closure, per-host profile persistence, response/cookie envelope and request/final-URL guards retain their existing owners.

## Actual browser observations

Cloud Build `f02dfc56-c776-45ab-b879-01b51faaac40` completed successfully. Ten evidence objects were downloaded with generation, size, MD5 and SHA256 verification. The existing guest userland image `sha256:b6a440a898556f124e4f7bbd7d02e1ac9c1f73cc63fe9594c06c60940a332e01` was pulled only on the remote worker. The candidate template and maintained fixture were mounted read-only; this is source correction evidence, not a newly built/released sandbox image.

The application containers ran as UID1500, network `none`, two CPUs/four GiB, read-only root, all capabilities dropped and no-new-privileges. Only an owned HTTP listener inside each container was used. No model, target, provider sandbox, license or secret was accessed. All three owned containers were removed and independently absent afterward.

The fixture observes the actual selected `playwright-extra` automation path without changing launch flags or the product safety guard. It checks returned HTTP status, final URL, rendered JavaScript, cookies and localStorage across separate launches. Real empty and navigated startup-page controls exercise fallback selection. A private-address negative leaves the owned HTTP listener's request count unchanged.

| Case | Observed product `newPage()` calls | Result |
| --- | ---: | --- |
| Original template, first/second visit | 1 / 1 | Original behavior passes; context contains two pages |
| Original template, new zero-extra-page assertion | 1 | Expected regression exit1, exact assertion `1 !== 0` |
| Candidate, first/second visit | 0 / 0 | HTTP200, rendered DOM, cookie/localStorage1→2 |
| Candidate, empty startup context | 1 | HTTP200 and rendered DOM |
| Candidate, nonblank startup page | 1 | Nonblank page retained separately; HTTP200 |
| Candidate, private address without override | 0 | Child exit1 as expected, no server request; fixture overall exit0 |

Original normal visits took4.600/4.530s; candidate normal visits4.309/4.159s. These are two descriptive observations per variant, not an SLA, robust benchmark or an E2B speedup claim. The maintained fixture is `packages/decepticon/tests/integration/browser_startup_fixture.cjs`; inside the sandbox run `node <fixture> <absolute-desktop-template> 0`. Argument1 instead of0 checks the original baseline behavior. The actual records and source hashes are in [the machine-readable evidence](browser-startup-page-20261109.json).

## Scope and verification

All73 sandbox-web unit cases pass, process exit0, with three existing compatibility deprecation warnings. Both changed JavaScript files pass `node --check`; `git diff --check` passes. No full image or dependency build is needed for this JavaScript-only source change. Hosted checks, when present, are recorded separately; local/remote process results do not stand for a hosted CI pass.

The separate native E2B investigation remains open in [SaaS #1633](https://github.com/BitterSecurity/decepticon-saas/pull/1633). Before this correction, native browser command measurements were74–109s, while a same-userland ordinary-container reference completed in2.913s. Host, kernel, storage and confinement differ, so these observations cannot isolate the exact emulation contribution. Reusing one blank page does not establish that overall E2B latency is solved. Actual stock native confinement, fresh E2B performance, normal SaaS tenancy and a coherent published artifact cohort still need their own acceptance. This PR does not publish wheels/images, update SaaS pins, switch VM architecture or deploy production.
