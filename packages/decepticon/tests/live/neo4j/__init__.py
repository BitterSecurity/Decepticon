"""KGStore + migration runner + KGMiddleware (PR-B) integration tests.

Require a reachable Neo4j 5.x — typically the ``decepticon-neo4j``
container from ``docker-compose.yml``. The
:func:`kgstore` fixture in ``conftest.py`` calls ``pytest.skip`` when
the env vars are unset or the driver fails to connect, so this
package never fails on a stack that does not have Neo4j up.
"""

from __future__ import annotations
