# 에이전트 기능 동등성 및 Python/Rust 성능 측정 — 2026-11-17 KST

**같은 아키텍처의 언어 대조군에서는 Rust의 메모리와 실행 CPU 비용이 낮았다.**
다만 이것은 특정 상태 머신과 SDK 조합의 측정이다. LangGraph 전체를 Rust로 포팅한 결과나
언어 전체의 고정된 속도 배수가 아니다. 공유 호스트의 편차와 시작 비용을 구분한다.
설계와 재현 명령은 [README.md](README.md)에 있다.

## 언어 대조군: CPU 고정, 인접 실행 5쌍

공통 relay를 한 프로세스에서 유지하고 두 후보를 같은 CPU 17에 고정했다. 실행 순서는
쌍마다 바꾸며 후보는 매번 새 프로세스로 시작한다. 실제 live 응답을 재생하며 Docker와
checkpoint 저장/강제 종료/복원은 실제 실행한다. 아래는 각 열의 독립적인 중앙값이다.
CPU는 prepare와 resume를 합친 ms, 메모리는 두 프로세스 중 큰 peak RSS의 MiB다.
열별 중앙값은 더해도 total의 중앙값과 정확히 같지 않을 수 있다.

| 구현 | 시작/import CPU | setup CPU | 실행 CPU | 전체 에이전트 CPU | peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| Python 대조군 | 406.4 | 208.3 | 181.8 | 796.4 | 35.55 |
| 자체 Rust | 4.3 | 22.6 | 45.6 | 76.1 | 7.22 |

중앙값끼리 비교하면 시작을 포함한 CPU는 약 10.5배,
시작/setup을 제외한 실행 CPU는 약 4.0배,
메모리는 약 4.9배 차이다.
각 쌍의 Python/Rust CPU 비율은 전체 10.5–21.1배, 실행 구간 3.8–7.8배로 흔들렸다.
따라서 정밀한 고정 배수나 통계적 유의성을 주장하지 않는다. CPU affinity도 전용 머신이나
Windows 호스트 자원 경쟁 제거를 뜻하지 않는다.

동일한 SQLite 공유 라이브러리 3.45.1, WAL/FULL, 같은 table schema와 transaction 경계를
사용한다. 본 실험의 Python은 3.12.3이고 Rust는 1.97.1 release다. Python 3.13을 요구하는
Decepticon 제품 환경 전체의 재현 결과가 아니다. asyncio/HTTPX, Tokio/reqwest, JSON과
인터프리터 초기화도 함께 달라진다. 같은 알고리즘의 구현 비용 비교로 해석한다.

## 실제 LLM 실행: 후보당 3회

총 15개 실행에서 실제 `gpt-6.1-sol`을 **180회** 호출했다. 모든 실행이 12회씩 호출했고
정해진 기능 경로를 통과했다. 최초 부모/자식의 실제 upstream 요청은 모두 동일하다.
이후 live 응답/ID/내용의 동일성을 강제하지 않으며 모든 공개 응답과 도구 결과를 보존한다.
아래는 중앙값 [min–max]다. 초기 진단 호출은 이 반복 측정의 180회에 포함하지 않는다.

| 후보 | 실제 완료 시간 s | 에이전트 CPU ms | peak RSS MiB |
| --- | ---: | ---: | ---: |
| 자체 Rust | 37.4 [36.5–37.9] | 99.4 [49.4–102.7] | 7.58 |
| Python 대조군 | 40.5 [38.6–41.0] | 1022.2 [390.4–1893.4] | 35.54 |
| Rig | 38.6 [38.1–39.8] | 152.8 [141.5–255.6] | 18.82 |
| LangChain + 공통 기능 계층 | 55.2 [50.4–60.4] | 12551.4 [10622.3–20124.9] | 122.25 |
| Deep Agents | 52.9 [50.8–57.7] | 12045.1 [11357.8–18091.1] | 123.36 |

완료 시간은 원격 LLM/네트워크와 Docker 대기, 두 프로세스 시작/복원 비용을 포함한다.
이 차이를 언어만으로 발생한 지연 차이라고 해석하지 않는다. 모델 이름과 전달 옵션은
같지만 provider 내부 실행 환경, sampling seed, cache hit 또는 모델 build는 고정할 수 없다.

## 전체 프레임워크 비교: 같은 live 기록 재생, 후보당 5회

12개 모델 요청의 전체 upstream 내용이 실제 live 기록과 정확히 일치할 때만 다음 응답을
반환했다. 도구 동작을 runner가 대신 실행하지 않는다. 새로운 LLM 호출은 0회다.
추가한 CPU 고정 실험과 원래의 이 실험을 섞어 집계하지 않는다.

| 후보 | 에이전트 CPU ms 중앙값 [범위] | 실행 CPU ms | peak RSS MiB | threads / FDs | 로컬 process-tree CPU ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 자체 Rust | 114.3 [96.9–120.8] | 66.4 | 7.58 | 1 / 15 | 534.8 |
| Python 대조군 | 1930.8 [1370.6–6600.6] | 454.8 | 35.56 | 1 / 12 | 2495.2 |
| Rig | 244.0 [178.1–520.0] | 198.1 | 18.80 | 1 / 15 | 678.4 |
| LangChain + 공통 기능 계층 | 17659.1 [11447.5–48845.1] | 1017.5 | 122.24 | 7 / 12 | 19438.7 |
| Deep Agents | 14665.3 [10289.2–61471.6] | 1060.5 | 123.30 | 7 / 11 | 16486.6 |

로컬 process-tree CPU에는 wait된 Docker CLI를 포함한다. 에이전트 CPU, peak RSS에는
relay, Docker daemon, 컨테이너 내부 자원을 합산하지 않는다. 실제 컨테이너 사용량을
128 MiB라는 제한값으로 대신 제시하지 않는다. IO/context switch 마지막 sample, DB 크기,
thread/FD 10 ms sample 및 각 구간의 PSS/RSS도 원시 결과에 남겼다.

Deep Agents와 직접 LangChain 후보는 같은 LangGraph와 같은 기능 미들웨어를 사용하고
공통 case의 import 코드도 같다. 둘 사이의 이 수치 차이로 우열을 판정하지 않는다.
한 공유 호스트에서 동일 코드의 CPU 시간도 크게 흔들렸다. 최초 batch의 큰 편차를
제거하거나 좋은 trial만 고르지 않고 전부 보존한 뒤 위의 인접 실행 대조군을 추가했다.
Rig/자체 엔진과 Python 두 후보는 외부 기능 계약이 같아도 내부 그래프/채널 구조는 다르다.
이를 언어 효과로 묶지 않는다. 모든 수치는 관측 로깅 비용을 포함한다. 언어 대조군은
둘 다 83개 이벤트와 약 19 KiB 로그를 기록했고 Python 그래프 후보는 약 30 KiB였다.

## 실제로 관측한 과정과 동등성 근거

모든 50개 실행이 계획, 서브에이전트, 메모리, 파일시스템, 실제 sandbox 및 복원을 통과했다.
각 실행의 도구 호출은 write_todos 4회, task 1회, read_file 2회, execute 1회, write_file 2회다.
부모 대화와 자식 대화는 분리되어 있고, 검사 완료 이후 child를 다시 실행하면 실패다.

```text
prepare: plan(inspect 진행) → task(inspector)
  inspector: read_file(/input.json) → Docker execute → JSON 결과
parent: plan(remember 진행) → write_file(/memories/result.json)
  → plan(report 진행) → 메모리 read 대기 상태를 동기 commit
  → 실제 SIGKILL(-9)
resume: 새 PID → 같은 todo와 대기 작업 복원 → read_file(/memories/result.json)
  → write_file(/report.json) → 모든 todo 완료 → DONE
```

정답과 memory/report 파일은 모두 `{"valid_count":2,"total":30,"invalid_ids":["C"]}`다.
Docker inspect에서 고정 이미지, network none, read-only rootfs, non-root 사용자,
cap-drop ALL, no-new-privileges, 128 MiB/1 CPU/32 PID를 확인했다. 실행 후 제거했다.
LangGraph는 graph update/도구 callback, Rig는 공식 AgentRun, 자체 엔진은 상태 머신
이벤트로 관측했다. 비공개 reasoning을 읽었다고 주장하지 않는다.

원래 재생 5쌍과 추가 CPU 고정 5쌍에서 **각 23개의 checkpoint history 전부가 일치**했다.
role, 전체 메시지, 대기 tool call, todo, 완료 결과와 순서를 비교했다. 단순히 최종 답만
비교하지 않았다. 각 history의 digest는
`e229c77916242d97c6162c355de0b1a06d01b35b384f8883d893e9de277d2dcb`다.
이는 이 시나리오의 상태 전이가 같다는 근거이며 전체 LangGraph API의 호환성 인증은 아니다.

## 실패 기록과 검증

- 최초 Deep Agents profile 결합에서 부모 도구가 숨겨져 실패했다. 제외 집합을 바로잡았다.
- 다음 시도는 결과 파일은 맞았지만 todo 상태가 없어 실패했다. 0.7.32에서 실제
  TodoListMiddleware를 명시적으로 추가했고 Markdown 체크박스는 plan으로 인정하지 않았다.
- Python 대조군의 pending list가 assistant message의 tool_calls와 같은 객체여서 과거 대화가
  변했다. 엄격한 replay가 다음 요청 차이를 잡았고 복사 후 같은 경로가 통과했다.
- pysqlite3 연결을 표준 SqliteSaver에 넣으면 예외 클래스가 달라 migration 처리가 실패했다.
  최종 측정은 그 adapter를 사용하지 않고 시스템 Python의 표준 SQLite를 사용했다.
- LangGraph 내부 stream 대기가 worker 하나를 차지해 한도 1에서 멈췄다. 두 Python 후보에
  한도 2를 동일 적용했다. 해당 중단과 stack trace도 보존했다.
- 의도적으로 첫 요청을 바꾼 negative case는 다섯 후보 모두 도구/실제 LLM 호출 전에
  실패했다. 이 다섯 실패를 정상 처리된 성능 trial로 세지 않는다.
- root tests의 첫 xdist 실행은 기존 임시 `.env` fixture의 병렬 생성/삭제 경쟁으로
  35 passed / 2 failed였다. 제품 코드를 고치거나 테스트를 생략하지 않고 직렬 실행하여
  **37 passed (10.47s)**를 확인했다. 최초 실패 로그도 로컬에 보존했다.

Rust 두 feature의 release build 및 clippy `-D warnings`가 shared build slot에서 통과했다.
root `make ci-lint`는 0 errors / 949 기존 warnings, 새 Python 7개 파일의 별도 타입 검사는
0 errors / 0 warnings였다. root Ruff/format도 통과했다. 집계기는 불완전한 suite와
negative 근거 누락을 실제로 거부한 후 완성된 자료에서 통과했다.
제품/UI/Docker image 변경이 없어 전체 앱 production build나 `make smoke`는 수행하지 않았다.
GitHub Actions 성공이나 전체 제품 테스트 suite 통과를 주장하지 않는다.

## 자료

- [기계 판독 집계](artifacts/summary.json): 모든 group의 중앙값/min/max와 개별 paired 비율.
- [전체 원시 자료](artifacts/evidence.json.gz): 50개 실행의 요청/공개 응답/이벤트/자원,
  두 언어의 history, 11개 초기·의도적 실패 기록과 stderr. gzip JSON이며 인증정보는 없다.
- archive SHA-256: `44f1eea1e972fa090758e1d1ad30e9d98a55b418ebf27d967c46f2bf94f69cf1`.

한 태스크의 작은 비교이므로 성숙도, 임의 도구 오류 복구, 병렬 처리, 압축/skills,
취소 전파, 임의 시점 장애나 전체 제품 부하의 결론은 포함하지 않는다.
Rust HTTP 서버를 추가할 때는 사용자 요청대로 Axum을 사용하도록 AGENTS.md에 기록했다.

## Post-measurement review

The archived50-case results above are preserved at measured source
`e308a34128dfaaa382398c699876f1fb779e9992`. The subsequent replay-mode guard
correction and its separate6regressions/5complete replay qualifications are
recorded in [the review notes](README.md#review-correction--fail-closed-replay-input).
No additional real model calls or replacement performance measurements were made.
