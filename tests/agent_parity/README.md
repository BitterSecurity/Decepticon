# 동일 기능 에이전트 비교와 Python / Rust 아키텍처 대조군

이 테스트는 프레임워크를 비교하는 실험과 언어 구현을 비교하는 실험을 구분한다.
기존 echo 실험(#1203)은 기본 기능이 달라 프레임워크 선정이나 언어별 배수의 근거로 사용할 수 없다.
프로덕션 코드를 변경하지 않는 독립적인 실험이며, 자체 엔진은 이 시나리오를 위한 구현이다.
LangGraph 전체를 Rust로 포팅했거나 기존 프레임워크와 같은 성숙도를 달성했다는 뜻은 아니다.

| 후보 | 실제 실행 엔진 | 기능 계층 |
| --- | --- | --- |
| Deep Agents 0.7.32 | LangGraph 1.2.17 | 선택한 기본 미들웨어 + 명시적 TodoListMiddleware |
| LangChain 1.4.7 직접 구성 | 같은 LangGraph | 위와 같은 Deep Agents 기능 미들웨어를 직접 구성 |
| Rig 0.46.0 | 공식 AgentRun / ModelTurn / next_step | 공통 Rust 도구, 저장, sandbox 계층 |
| 자체 Rust | 직접 구현한 비동기 상태 머신 | 위와 같은 공통 Rust 기능 계층 |
| Python 대조군 | 자체 Rust와 대응하는 비동기 상태 머신 | 같은 도구 동작, 대화 상태, SQLite schema와 저장 경계 |

직접 LangChain 후보도 Deep Agents 기능 계층을 사용한다. 가벼운 LangChain 기본 루프와
완성된 Deep Agents를 비교하는 실험이 아니다. LangChain과 Deep Agents는 여기서 같은
실행 엔진이므로 결과를 독립적인 두 그래프 런타임의 차이로 해석하지 않는다.
Rig는 응답만 HTTP로 가져오는 이름표가 아니라 공식 실행 상태 머신을 실제 사용한다.

언어 비교는 **Python 대조군 ↔ 자체 Rust**에만 적용한다. 이번에는 Rust 후보에 대응하는
Python 대조 구현을 만들어 상태 머신을 맞췄다. 기존 LangGraph 소스 전체를 번역한 구현은 아니다. asyncio/HTTPX와 Tokio/reqwest,
JSON 구현, 인터프리터 시작 비용도 함께 바뀐다. 같은 SQLite 공유 라이브러리와 논리적
알고리즘을 사용하지만 컴파일러 또는 언어 문법만의 효과를 분리한 실험은 아니다.
LangGraph Python과 LangGraph의 완전한 Rust 포트 사이의 비교도 아니다.

## 최소 태스크와 통과 조건

`scenario.json`의 고정된 세 레코드(A=10, B=20, C=-5)를 검사한다.
부모 하나와 inspector 서브에이전트 하나만 사용한다.

1. 부모가 inspect / remember / report 세 todo를 실제 도구로 만들고 갱신한다.
2. 부모가 고정된 동일 입력으로 inspector를 한 번 호출한다.
3. inspector가 `/input.json`을 읽고 실제 Docker 안에서 고정 프로그램을 한 번 실행한다.
4. 부모가 결과를 별도 영구 경로 `/memories/result.json`에 기록한다.
5. 메모리 읽기 직전의 durable interrupt를 확인하고 드라이버가 부모 프로세스를 SIGKILL한다.
6. 새 PID가 SQLite의 대기 작업과 todo를 복원하고, 저장된 메모리를 실제 읽는다.
7. `/report.json`을 기록하고 세 todo를 완료한다. 재시작 후 검사나 sandbox를 반복하면 실패다.

독립적으로 명시한 정답은 `{"valid_count":2,"total":30,"invalid_ids":["C"]}`이다.
결과만 맞는 것은 통과가 아니다. 실제 도구 호출, 최초 계획의 순서, 계획 복원,
메모리의 전후 내용, sandbox 설정, PID 변경, DB 무결성도 확인한다.
이것은 합의된 정지점에서의 프로세스 장애 복구 검사다. 도구 실행 중 임의 시점 장애,
전원 장애, 취소 전파, 병렬 서브에이전트, 사용자 승인 UI까지 검증하지 않는다.
전체 API 호환성이나 임의의 도구 오류 복구도 동등성 인증 대상이 아니다.

기본 기능인 계획, 서브에이전트, 메모리, 파일시스템 제어, sandbox는 모두 실행한다.
압축/요약, skills 검색, 병렬 실행, RAG는 이 작은 태스크의 범위 밖이다. 요약 미들웨어는
양쪽 Python 후보에서 동일하게 끈다. 이 결과를 전체 기능의 동등성 인증으로 사용하지 않는다.
LangGraph의 내부 대기 작업 때문에 Python 후보의 worker 한도는 모두 2다.
태스크는 순차적으로 실행되며, 실제 worker/thread 개수는 자원 측정에 포함된다.

Rust HTTP 서버를 추가하거나 포팅할 때는 Axum을 사용한다. 현재 Rust 후보는 독립 실행
프로세스와 HTTP 클라이언트로 구성되며 서버 transport 성능을 측정하지 않는다. 공통 Python
relay는 기존 인증 adapter를 사용하는 실험 장치로서 모든 후보의 측정 대상 밖에 있다.

## 조건과 관측

- 모델은 `gpt-6.1-sol`, reasoning은 low다. 후보별 공개 시스템 프롬프트, 부모/자식 입력,
  도구 schema, sandbox 이미지와 옵션, 메모리 지침을 맞춘다. SDK provider retry는 끈다.
- `profile.json`은 실제 Deep Agents 요청에서 얻은 최종 합성 프롬프트/schema다.
  Rust와 Python 대조군도 이 최종 형태를 사용한다. 원래의 짧은 시스템 프롬프트만 같다고
  주장하지 않는다. 출처와 라이선스는 [NOTICE.md](NOTICE.md)에 있다.
- 모든 후보는 같은 로컬 provider relay를 사용한다. relay만 기존 Codex 인증을 읽으며,
  후보 프로세스에는 로컬 더미 키만 준다. 인증 헤더, 토큰, 비공개 reasoning은 저장하지 않는다.
- provider에 보내기 전에 모든 후보의 function-call argument JSON을 같은 방식으로
  정렬/압축한다. 원본 HTTP 요청과 정규화된 실제 upstream 요청을 모두 기록한다.
  문자열 내 의미 있는 내용을 후보별로 수정하거나 도구 결과를 대신 만들어 주지 않는다.
- SQLite는 Python 3.12.3 표준 sqlite3와 Rust rusqlite가 같은 시스템 SQLite 3.45.1을 사용한다.
  SQLite backend를 맞추기 위해 이 실험은 시스템 Python 3.12.3을 사용한다. 제품의 Python
  3.13 환경 전체를 재현한 수치가 아니며 Python 버전별 차이는 측정하지 않았다.
  모두 WAL/FULL이며 LangGraph는 `durability="sync"`다. 자체 엔진도 각 모델/도구 경계에서
  동기 commit하고 전체 history와 최신 pointer를 보관한다. LangGraph 고유 채널/노드의
  추가 저장량은 프레임워크 비교의 일부이며, 언어 대조군의 history는 별도로 전수 대조한다.
- sandbox는 같은 고정 이미지, network=none, read-only rootfs/입력 bind, uid=65534,
  cap-drop=ALL, no-new-privileges, 128 MiB, 1 CPU, 32 PID다. 종료 후 컨테이너를 제거한다.
  실행 프로그램도 모든 후보에서 같은 Python 프로그램이다. 비교 대상은 에이전트 엔진이다.

relay 기록은 모델 요청/공개 응답을 매 턴 보존한다. Python은 graph update와 도구 callback,
Rig/자체 엔진은 상태 머신과 도구 이벤트를 남긴다. 도구 인자/결과, 부모/자식 관계,
checkpoint 복원, sandbox inspect와 실제 stdout을 확인할 수 있다. 비공개 사고 과정은 관측하지 않는다.

## 측정 방식

`live`는 실제 원격 LLM을 호출한다. `replay`는 별도로 실제로 완료된 한 live 실행의 공개
응답을 재사용한다. 매 단계의 **전체 upstream 요청**이 그 기록과 정확히 같아야 응답한다.
따라서 replay도 도구, SQLite, 실제 Docker, 프로세스 종료/복원을 실행하지만 새로운 LLM
호출은 하지 않는다. live의 모델 결정과 네트워크 편차가 로컬 엔진 비교를 가리는지 확인하는
보조 실험이다. replay 시간을 실제 LLM 사용자의 완료 시간으로 제시하지 않는다.

후보당 live 3회, replay 5회다. 여기에 언어 대조군만 CPU 17에 고정하여 가까운 시간에
순서를 번갈아 실행한 5쌍을 추가했다. `paired.py`는 허용 CPU 중 마지막 CPU를 선택하고,
같은 warm 계측 relay 프로세스를 재사용하며 후보 프로세스는 매번 새로 시작한다.
CPU affinity는 전용 코어 할당이나 Windows 호스트 자원 경쟁 제거를 뜻하지 않는다. 각 실행은 prepare/resume 두 새 프로세스를 사용한다.
replay 다섯 라운드는 실행 순서를 회전하여 각 후보가 각 순서에 한 번씩 위치한다.
중앙값과 범위를 보고하며 작은 표본에서 p95, 유의성, 보편적 배수를 주장하지 않는다.
모든 CPU/메모리 수치는 실행 관측을 켠 상태다. 프레임워크별 내부 이벤트 수가 달라
로깅 비용을 제거한 순수 커널 비용은 아니다. 이벤트 개수와 기록량도 함께 보존한다.
언어 대조군은 같은 모델/도구/체크포인트 경계를 기록한다.
OS page cache를 비우지 않으므로 "cold process"는 cold disk/cache를 뜻하지 않는다.
공유 WSL 호스트에서 측정하고, 전체 suite 동안 shared build slot을 잡아 다른 full build를 막는다.
다른 사용자 작업과 Docker daemon의 자원 경쟁까지 제거한 전용 머신 실험은 아니다.

- **에이전트 CPU**: 각 프로세스의 CPU clock. import/시작, setup, 실행 구간을 나눈다.
  prepare의 pause와 resume의 완료 시점까지이며 마지막 로깅/종료 cleanup CPU 일부는 제외된다.
- **로컬 process-tree CPU**: wait된 자식의 user+system CPU. Docker CLI도 포함한다.
  공통 relay, Docker daemon, 컨테이너 내부 CPU는 포함하지 않는다.
- **메모리**: 해당 프로세스의 `/proc/self/status` VmHWM으로 peak RSS를 구한다.
  두 프로세스 중 큰 값을 사용하며, 동시에 살지 않는 두 프로세스의 RSS를 더하지 않는다.
  PSS/RSS snapshot도 기록한다. 컨테이너/relay를 합친 전체 시스템 RAM은 아니다.
- **OS 자원**: 외부에서 10 ms마다 RSS/thread/FD peak와 `/proc/PID/io`, context switch를
  관측한다. 짧은 peak를 놓칠 수 있으며 마지막 sample은 최종 누적값보다 작을 수 있다.
  프로세스 소멸/권한 변경으로 sampling이 종료되면 그 이유도 기록한다.
- **wall time**: 새 프로세스 생성부터 pause에서 kill/reap 또는 정상 종료까지.
  live에서는 LLM/네트워크/Docker 대기, 시작과 복원 비용을 모두 포함한다.

## 재현

로컬 Docker와 위의 이미지가 필요하다. 다른 이미지는 `run.py --image`로 지정할 수 있으나
비교 시 모든 후보의 digest를 동일하게 유지해야 한다. 로컬 Codex 인증은 기존
`config/codex_chatgpt_handler.py`가 읽는다. 인증 파일을 결과에 넣지 않는다.

```bash
uv sync --project tests/agent_parity --python /usr/bin/python3
bash /home/catow/.local/bin/with-build-slot bash -c '
  cargo +1.97.1 build --locked --release --manifest-path tests/agent_parity/rust/Cargo.toml --no-default-features --features native &&
  cp tests/agent_parity/rust/target/release/agent-parity tests/agent_parity/rust/target/native &&
  cargo +1.97.1 build --locked --release --manifest-path tests/agent_parity/rust/Cargo.toml --no-default-features --features rig &&
  cp tests/agent_parity/rust/target/release/agent-parity tests/agent_parity/rust/target/rig'
bash /home/catow/.local/bin/with-build-slot tests/agent_parity/.venv/bin/python \
  tests/agent_parity/suite.py --output-dir /absolute/fresh/output
bash /home/catow/.local/bin/with-build-slot tests/agent_parity/.venv/bin/python \
  tests/agent_parity/paired.py --golden /absolute/fresh/output/live-0-native.json \
  --output-dir /absolute/fresh/paired
```

전체 suite는 live 15개 실행에서 실제 LLM을 호출한다. 결과 디렉터리는 새 경로를 사용한다.
`run.py --framework ... --work-dir ... --output ... [--binary ...]`로 단일 live 실행도 가능하다.
`--replay <qualified-live.json>`은 별도 재생 모드다. lockfile을 유지하며 Python 실행 파일이
SQLite 3.45.1에 연결되는지 확인한다. 다른 머신의 절대 수치를 이 머신의 수치와 직접 섞지 않는다.

동작/성능 결과와 실패 진단 기록은 [RESULTS.md](RESULTS.md)에 기록한다.

결과 보존/집계는 다음과 같이 실행한다. 먼저 다섯 후보의 의도적인 replay 요청 불일치
실험도 실행하여 `negative-<framework>.json`을 diagnostics 경로에 남겨야 한다.
`reject.sh`는 원본 live 기록을 복사해 첫 `upstream_request.instructions`를 바꾼다.
실제 LLM 호출 없이 다섯 후보가 요청을 거부하고 도구를 실행하지 않는지 확인한다.
완료되지 않은 suite, 요청/프롬프트 차이, SQLite 차이, checkpoint 차이는 집계를 중단한다.

```bash
bash /home/catow/.local/bin/with-build-slot bash tests/agent_parity/reject.sh \
  /absolute/fresh/output/live-0-native.json /absolute/diagnostics
tests/agent_parity/.venv/bin/python tests/agent_parity/summarize.py \
  --results-dir /absolute/fresh/output \
  --paired-results-dir /absolute/fresh/paired \
  --diagnostics-dir /absolute/diagnostics \
  --output-dir /absolute/archive
```

`summary.json`은 구간별 중앙값과 min/max다. `evidence.json.gz`는 전체 모델 요청/공개 응답,
도구·그래프 이벤트, 50개 trial의 원시 지표, 양 언어의 checkpoint history, 실패 진단을
보존한 JSON이다. 압축은 보관 용도이며 실행에서 관측을 끄거나 trace를 생략하지 않는다.

## Review correction — fail-closed replay input

The original fifty measurements and source hashes correspond to commit
`e308a34128dfaaa382398c699876f1fb779e9992`; their archive is unchanged.
Review found that an empty replay capture could select the live provider branch.
Replay mode now remains explicit independently of the replay dictionary, and
unqualified, non-live or empty input is rejected before Docker/provider work.
Six regressions fail before the correction and pass afterward without paid calls.
The root tests pass43cases, with no skips. Five fresh complete replay runs
(one per implementation,60captured responses) pass actual Docker execution,
SIGKILL and SQLite recovery with provider completion forcibly disabled.
These are functional review checks, not a new performance ranking.

[Review evidence](artifacts/review-20261117.json.gz) SHA-256:
`c04bebb64de1b67e92b5bae56aca382946b04062b82e32f63946991307be955e`. Initial fixture environment failures are retained separately;
the pinned repository Ruff0.15.14check/format and configured fixture type checks pass.
The canonical errors-only `make ci-lint` gate passes with0errors and964reported
warnings (none in the new review test). An initial unpinned newer formatter
also inspected Markdown code blocks and flagged57unrelated files; those files
were preserved and the repository-pinned gate was run instead.
