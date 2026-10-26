# AutoBot 재설계 — 에이전트 구동 OSS SIEM (탐지 → 조사 → 대응)

- Status: **진행 중** — Phase 1 핵심 착수 완료(§0 실행 갱신 참조), 레포 이전 결정(2026-10-26 사용자 승인: "온전 이전 — 완전 독립")
- Date: 2026-10-23 (실행 갱신: 2026-10-26)
- 선행 문서: `2026-10-23-ecosystem-split-intent.md`(의도), `2026-10-23-blue-cell-analysis.md`(현 Blue 전수 해부)
- 근거: 웹 딥리서치 4축(SIEM 아키텍처 / 수집·정규화 / 탐지엔지니어링 / SOAR·대응) + 사내 선례(`bugclaw-core-extraction.md`, `extension-contract.md`)
- 전제: 사용자 승인 — "기존 Blue Cell을 완전히 재설계해도 좋다"

> **한 줄 요약:** AutoBot = 사용자 환경에 꽂는 **OSS SIEM**. 수집→정규화→저장→
> 탐지→조사→대응을 **느슨하게 분리**하고, **커버리지/룰/대응을 플러그인**으로
> 확장하며, **LLM 에이전트가 정규화된 전 이벤트를 read-only로 질의**해
> 조사하고, 대응은 **등급제 가드레일 + 휴먼 승인**으로 닫는다.
>
> **방법론(확정):** 처음부터 자작하지 않는다. **의도 정의 레이어만 자작**
> (커버리지 플러그인·OCSF 정규화·에이전트·등급제 대응), **저장/질의/Sigma 탐지는
> OSS substrate 채택**(기본 **OpenSearch+Security Analytics**, 대안 ClickHouse,
> dev DuckDB/SQLite — 추상화 뒤 교체), **설정/케이스는 Postgres**, **Wazuh는
> 베이스 아닌 1급 소스/통합**. 기존 시스템 **호환성(§9b)**을 1급 기둥으로.

---

## 0. 실행 갱신 — 레포 이전 결정 (2026-10-26, 사용자 승인)

### 0.1 Phase 1에서 착지한 것 (Decepticon, 전부 머지·라이브 검증 완료)

| 작업 | PR | 본 문서 대응 |
|---|---|---|
| core 계약(OCSF envelope·EventStoreProtocol·CoverageAdapterProtocol) + source enum 제거 | #1101 | §11.1 #1·#3 착수 |
| Blue 피드 게이트(Blue 모드 전용) + 인라인 어세스먼트(영어 고정) | #1102 | 조사 에이전트 UX 계승 |
| 탐지 룰 파일화(`BLUE_RULES_FILE`, bootstrap/Sigma 부분집합 계약) + 에이전트 타겟 컨텍스트 | #1103 | §11.1 #7의 **부분 선행**(Sigma는 목표 유지) |
| Blue engagement 구성 설계·구현(`blue.json`, Blue 전용 워크스페이스 루트) | #1104·#1105 | §9b 하위호환 표면 확장 |
| 탐지 어댑터(rules/decision) + 인시던트 배치 + 생성 표면 blue 블록 | #1106 | §6 계층 관계 조정(§0.3), §11.1 #8 관련 |
| engagement 스코프 compose 명령 수정 + 실제 TUI PTY 실측 | #1107 | 클라이언트 어댑터 검증 |

### 0.2 오늘 승인된 이탈 — decision 어댑터 (§5 원칙과의 관계)

본 문서 원칙 5("LLM은 매칭 결정에 관여하지 않는다")에 대한 **사용자 승인 이탈**:
`BLUE_DETECTOR=decision`은 정규화 이벤트 전체에 대해 타입화 분류(attack/suspicious/benign)로
인시던트를 생성할 수 있다. 완화 조건: ① **옵트인**(기본은 결정적 rules 어댑터) ② 타입화 출력·
페이지 단위 호출·개별 판정 fail-closed·비배열 응답은 페이지 실패 ③ 매칭 결정 코드로 강제가
아니라 **어댑터 선택으로 분리** — OpenSearch+Sigma substrate(§5)가 착지하면 decision
어댑터는 §6의 ③ triage tier로 재편하고 rules 어댑터가 ① per-event 결정적 매처를 흡수한다.
bootstrap jsonl 룰 포맷은 Sigma 부분집합 계약이므로 룰팩 이동이 무손실이어야 한다.

### 0.3 레포 이전 실행 계획 — "온전 이전" (완전 독립)

**자산 분류**:

| 자산 | 이전 | 잔류(Decepticon) |
|---|---|---|
| core 계약(OCSF·EventStore·Coverage 프로토콜) | — | ✅ 잔류(공유 계약 홈, §9 경계 절) |
| `packages/autobot` WIP(OpenSearch 스토어·coverage 플러그인·Sigma 탐지·triage 경계, 23파일 2커밋 — 최신 main 대비 stale) | ✅ 2a | |
| `examples/blue-local-sensor/` 센서 스택(receiver·monitor·compose·Caddyfile — 오늘 완성형) | ✅ 2b | |
| `blue_cell` 패키지(rule_match·tap·engagement_config)·`blue_sensor_*` 도구·blue_cell 에이전트·LangGraph 연결 | ✅ 2b/2c | |
| 연산자 표면(TUI `/blue`·알림 훅 — URL 기반, 제품 불문) | | ✅ 잔류(씬 클라이언트) |
| engagement 생성 blue 블록(`blue.json` 상호운용) | | ✅ 잔류(Decepticon engagement ⇄ AutoBot 센서 연결 고리) |

**핵심 공백**: WIP는 데이터 평면만 보유. watch·조사·decision 분류가 Decepticon LangGraph
서버(blue_cell)에 HTTP 의존 — **온전 이전의 조건은 AutoBot 자체 에이전트 런타임**(blue-only
그래프+도구+서버)이며, 이것이 2b의 최대 공사다.

**단계**:

- **2a — 레포 부트스트랩** (즉시, 저위험): `BitterSecurity/AutoBot`(현재 README만 존재)에
  WIP 패키지를 **최신 main 기준 재기반**하여 이전, CI(ruff format 포함 — CI 미러 교훈),
  `decepticon-core` 의존(PyPI 2.0.0 배포본), 라이브 스택 검증 하네스 이식. 충돌 리스크 0.
- **2b — 런타임 이전**: 센서 스택 → AutoBot `examples/local-sensor`; **blue-only LangGraph
  에이전트 서버**(blue_cell 프롬프트·도구·그래프 이전) — 모니터의 `AGENT_URL`이 AutoBot
  서버를 가리킴. 오늘의 라이브 검증 하네스(stub 에이전트+PTY TUI)로 전 단계 재검증.
- **2c — Decepticon 정리**: `blue_cell` 패키지·`blue_sensor_*` 도구·blue_cell 에이전트·
  그래프 등록 제거(§11.1 #9·#10의 플러그인 게이팅을 **제거형**으로 실행), TUI·engagement
  blue 블록은 URL 기반 상호운용으로 잔류, 문서 피벗. 하위호환: receiver 질의 API·센서툴
  계약은 §9b대로 유지.

**회귀 방지 원칙**: 각 단계마다 오늘 확립한 실측 하네스(센서 스택 라이브 + headless CLI +
PTY TUI 구동)을 재실행한다 — 단위 테스트만으로는 engagement 스코프 compose 보간 같은
통합 결함을 잡지 못한다(#1107 교훈).

### 0.5 이전 후에도 Decepticon TUI/CLI에서 Blue 사용은 보장된다 (두 경로)

1. **URL 클라이언트 경로(임포트 불필요)** — TUI `/blue` 명령·알림 훅·blue 대화가 전부
   URL 기반(`BLUE_MONITOR_URL`, 에이전트 서버 URL). AutoBot 스택이 어느 호스트에 있든
   동작하며, 이 클라이언트 코드는 2c 이후에도 Decepticon에 잔류한다.
2. **import 경로 — `decepticon[blue]` extra(§9 계획)** — 같은 파이썬 환경에 `autobot`
   패키지 설치 시 entry-point로 blue_cell 그래프·도구가 Decepticon 서버에 등록되어
   설치만으로 오늘과 동일한 TUI blue 모드가 동작한다. 2c의 플러그인 게이팅(§11.1
   #9·#10)은 제거가 아니라 이 extra 경로로 흡수된다(§0.4).

2c 이후 상태 매트릭스: `decepticon` 단독 = red만(blue 모드 진입 시 capability probe가
"`decepticon[blue]` 설치 또는 AutoBot URL 지정"을 안내); `decepticon[blue]` = blue_cell
그래프가 Decepticon 서버에 등록되어 완전 동작; `decepticon` + 원격 AutoBot URL = 원격 blue
(blue 전용 에이전트 base URL 설정은 2b에서 확정할 항목). 이 보장은 이전 작업의 수용 기준에
포함된다 — 어느 단계에서도 오늘의 TUI blue UX가 기능 저하 없이 유지되어야 한다.

### 0.4 §11 Phase 계획과의 정렬

- §11.1 #1·#3(계약·enum 제거) — **완료**(#1101). #2(OpenSearch PoC) — WIP에 포함, 2a에서
  레포화. #4·#5(어댑터 계약·dispatch) — WIP에 프로토콜 존재, receiver dispatch 미착수(2b).
  #6(커버리지 레지스트리) — 미착수(2b). #7(Sigma 룰팩 교체) — **bootstrap jsonl이 선행**
  (§0.2), Sigma는 2b 목표. #8(triage 경계) — decision 어댑터가 §0.2 형태로 선행(2b 재편).
  #9·#10(플러그인 게이팅·capability probe) — **2c 제거형으로 흡수**.
- §11 Phase 2/3는 본 §0.3 단계로 **치환**된다(레포 이전이 먼저, substrate 전환은 이후).

---

## 1. 설계 원칙 (리서치에서 수렴한 것)

1. **단일 정규화 계약 위의 소스-비종속성.** 모든 소스를 수집 시점에 하나의
   envelope로 정규화해야 한 벌의 탐지·한 벌의 LLM 프롬프트가 모든 소스에
   일반화된다. (SIEM 4사 공통)
2. **수집 ↔ 탐지 ↔ 대응 분리.** 특히 에이전트와 탐지엔진이 **동일 이벤트
   집합**을 보도록 **query-over-store**(Elastic/Matano형)를 택한다. 지연민감
   룰만 얇은 스트리밍 tier.
3. **커버리지 = 자기완결 플러그인 폴더.** 입력+파서+스키마매핑+활성화를 버전
   관리되는 한 단위로 (Matano `log_sources/`, Elastic Fleet integration).
   명령형 XML 디코더+재시작(Wazuh)은 회피.
4. **정규화는 파이프라인에, 룰에 넣지 않는다.** (pySigma pipeline 교훈)
5. **LLM은 매칭 결정에 관여하지 않는다 — 조사·triage·초안만.** 결정적
   매칭/대응 실행과 LLM 판단 사이의 seam을 **코드로 강제**.
6. **대응은 가역성 기준 등급제 + 기본 휴먼 승인.** 자율은 조사·triage까지,
   상태 변경은 게이트. (AI-SOC 시장 합의: triage-first)
7. **"로그 없음 ≠ 공격 없음"을 1급 모델링.** 커버리지 공백을 침묵이 아니라
   gap 이벤트로.
8. **수집 이벤트는 신뢰하지 않는다.** 로그 인젝션 → 프롬프트 인젝션 경로 차단,
   에이전트 질의 크리덴셜은 read-only 정규화 뷰로 스코핑.
9. **기존 시스템과의 호환성은 1급 목표다.** (a) 기존 보안 생태계(Sigma 룰
   코퍼스·SIEM·ES API·EDR)와 interop, (b) 우리 이전 시스템(현 Blue Cell
   receiver API·센서툴·MCP툴)과 하위호환. **표준(OCSF/Sigma/OTel) + 다수 시스템
   어댑터**가 한 플랫폼 종속보다 호환적. §9b 참조.

### 1b. Build vs Adopt — 목표함수(의도 온전 구현) 최적화

원칙: **의도를 정의하는 레이어는 전부 자작(타협 불가), commodity 레이어만
교체 가능하게 채택, 기존 SIEM은 베이스가 아니라 소스/통합으로 흡수.**

| 레이어 | 결정 | 선택 |
|---|---|---|
| 수집 | 유지 | Fluent Bit |
| 커버리지 플러그인 | **자작(차별화)** | 선언형, OCSF emit |
| 정규화 스키마 | **자작(표준 위)** | **OCSF**(+Sigma용 ECS 매핑) |
| 저장/질의 + Sigma 탐지 | **채택(교체 가능)** | **기본 OpenSearch+Security Analytics** / 대안 ClickHouse / dev DuckDB·SQLite |
| 설정·케이스·커버리지 레지스트리 | 채택 | Postgres(+Timescale) |
| 에이전트(탐지→조사→대응) | **자작** | MCP-over-store 패턴 |
| 대응(등급제) | **자작** | OSS SIEM 미제공 |
| **Wazuh** | **소스/통합(베이스 아님)** | Wazuh→OCSF 커버리지 어댑터 + 기존 Wazuh MCP |

**왜 OpenSearch 기본**: 호환성(ES API·Dashboards·**Sigma 네이티브 Security
Analytics**·OCSF 인덱스 확인)이 목표함수의 1급 축이고 Apache-2.0(Linux
Foundation)로 안전. **대안 ClickHouse**는 대용량·비용 최우선 배포(추상화 뒤
교체, dev=DuckDB). **왜 Wazuh 통째 베이스 아님**: 자체 스키마(OCSF 아님)·명령형
XML 커버리지·무거운 올인원이 의도 두 축(선언형 커버리지·OCSF)과 충돌 →
베이스로 상속하면 의도를 거슬러 구현하게 됨. 대신 가장 많이 깔린 OSS SIEM이라
**소스로 흡수**하는 게 호환성·사용자층 양쪽에서 이득.
**라이선스 제외**: Elasticsearch(SSPL/ELv2/AGPL)·Graylog(SSPL)·Security
Onion(ELv2)·TheHive5(독점)·Matano(방치+ECS).

---

## 2. 타깃 아키텍처

```text
┌─ 수집(Collection) ──────────── 커버리지 플러그인 경계 ────────────┐
│  엣지: Fluent Bit (tail/syslog/http/kafka/otel…) — config-only 추가  │
│        · 소스별 cursor(offset DB) · drop/backpressure 메트릭         │
│  (옵션) OTel Collector filelog — OTLP 네이티브 환경                   │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼  (at-least-once)
┌─ 정규화/집계(Normalization) ──────────────────────────────────────┐
│  Vector (VRL) 또는 어댑터 normalizer:  raw → 정규화 envelope         │
│    envelope = OCSF 클래스 + metadata(original_time/logged_time,      │
│               product) + raw_data(원본) + unmapped(미매핑) 보존      │
│    전송 모델 = OTel log data model (Timestamp vs ObservedTimestamp)  │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─ 저장/질의(Store) ── 플러그블 백엔드, 계약 고정 ───────────────────┐
│  기본: OpenSearch + Security Analytics(ES API·Sigma 네이티브·OCSF)   │
│  대안: ClickHouse(대용량·비용)  ·  dev: DuckDB/SQLite                │
│  질의 계약(공통): events(after커서) · search(필드) · timeline(window)│
│                   · sources · metrics · bodies · stream(SSE)        │
└───────┬───────────────────────────────────────┬────────────────────┘
        │ 폴링                                    │ read-only 질의
        ▼                                         ▼
┌─ 탐지(Detection) 3-tier ──────────┐   ┌─ 조사(Investigation) ───────────┐
│ ① 결정적 per-event 매처           │   │ read-only ReAct 에이전트         │
│    (Sigma→pySigma 컴파일)         │   │  · 정규화 전 이벤트 질의          │
│ ② bounded-window 상관             │   │  · enrichment · body/timeline    │
│    (threshold/value/temporal)     │   │  · 커버리지-gap 인지 결론         │
│    → building-block → case        │   │  · 서명된 Investigation Ledger   │
│ ③ LLM triage (매칭된 알림만 읽음, │   └──────────────┬───────────────────┘
│    verdict·dedup·서사만 주석)     │                  │ 구조화 권고 객체
└───────────────┬───────────────────┘                  ▼
                ▼                          ┌─ 대응(Response) 등급제 ─────────┐
          인시던트/케이스 ───────────────▶│ G0 통보 / G1 탐지배포 / G2 차단  │
          (dedup·그룹핑·ATT&CK·RBA        │ G3 격리·계정비활 / G4 파괴       │
           위험스코어·MTTD·증거)          │  read/write 분리, dry-run,       │
                                          │  HITL interrupt, TTL 롤백,       │
                                          │  break-glass, 감사로그           │
                                          └──────────────────────────────────┘
```

---

## 3. 수집 — 커버리지 플러그인 (단기 핵심)

### 3.1 2-tier
- **엣지 = Fluent Bit**: 초경량(C), 최대 입력 카탈로그, `@INCLUDE` config
  fragment로 플러그인이 자기 입력+파서를 동봉, `/api/v2/metrics/prometheus`로
  `output_dropped_records_total`·`input_ingestion_paused`·`backpressure_wait`
  등 **드롭 가시성** 최강. (현 센서가 이미 Fluent Bit 기반 — 계승)
- **집계/정규화 = Vector(옵션)**: VRL 정규화 + **end-to-end ack** + 디스크
  버퍼 + ARC. 전달 보장이 필요한 배포에서 중앙 tier로. OSS local 기본은
  Fluent Bit → receiver 직결 유지.

### 3.2 Coverage Adapter 계약 (entry-point `autobot.coverage`)
Matano `log_sources/<name>/` 선언형 폴더를 플러그인 패키지로:

```text
CoverageAdapter
  source_id: str            # 안정 URN  vendor.product.channel
  signal_type: str          # http | log | process | auth | network | audit | cloud | edr
  version: str
  collection:               # 둘 중 하나
    fluentbit_fragment() -> yaml     # 엣지 입력 조각, 또는
    run(ingest_url)                  # 자체 수집기 → /ingest 직접 POST
  normalize(raw) -> Event   # raw → OCSF envelope. raw_data + unmapped 필수 보존
  cursor: {kind, key}       # offset/DB/checkpoint — 재개 명시
  heartbeat: {expected_cadence}      # "살아서 수집 중" 신호
  drop_counter: [metric_names]       # 이 소스의 손실을 보고하는 메트릭
  rule_pack: path|None      # 이 소스용 Sigma/Python 탐지 + 샘플이벤트(CI)
  coverage_manifest: [ATT&CK technique ids]   # 이 소스가 "본다"고 주장하는 범위
```

- 번들 기본 어댑터 2종(`http-ingress-proxy`, `target-log-file`)을 이 계약으로
  재작성 → 현 동작 보존 + 신규 소스 동형 추가.
- 환경별 커버리지(`autobot-coverage-{windows,syslog,cloudtrail,k8s-audit,...}`)는
  **별도 pip 패키지** → 설치만으로 발견.
- 미등록 소스는 `raw_data`+`unmapped` passthrough로 **절대 버리지 않음**.

### 3.3 현 하드코딩 제거 (blue-cell-analysis §3)
`normalize()` if/else → 어댑터 레지스트리 dispatch. source enum 4곳 제거 →
`/sources` 구동. Fluent Bit 고정 2입력 → fragment include. search 필드 고정 →
공통 상관필드 + 어댑터 선언 인덱스.

---

## 4. 정규화 Envelope

> ✅ **확정 (2026-10-23 사용자):** 스키마 방향 = **OCSF canonical(저장·에이전트
> 질의) + pySigma pipeline의 OCSF↔ECS 매핑(기존 Sigma 룰 재사용) + OTel log data
> model(전송/상관)**. 미래 표준(OCSF, Linux Foundation 중립·보안 네이티브·AI 친화)에
> 베팅하되 현행 생태계(ECS 기반 Sigma)와의 호환을 매핑 레이어로 유지. ECS는 독립
> 표준으로서 OTel semconv에 수렴 중이라 보안 도메인 표준은 OCSF로 수렴.

- **정규화 표준 = OCSF**: 보안 클래스 택소노미(auth/process/network/finding),
  `metadata`(version·product·original_time·logged_time·profiles), `severity_id`,
  `observables[]`, 그리고 **`raw_data`(원본)·`unmapped`(미매핑 필드)** —
  AutoBot이 필요로 하는 provenance/raw 보존이 스키마에 내장.
- **전송/상관 = OTel log data model**: `Body`(OCSF JSON or raw), `Attributes`/
  `Resource`(provenance), `Timestamp`(이벤트시각) vs `ObservedTimestamp`(수집시각),
  `SeverityNumber`, `TraceId/SpanId`(상관).
- ECS/OSSEM은 **매핑 레퍼런스**로만(ECS는 OTel semconv로 수렴 중).
- 불변식: 원본 바이트·소스 아이덴티티·두 타임스탬프를 **항상** 유지.

> ⚠️ 리서치 caveat: OCSF 버전별 클래스 수, "Matano가 OCSF-native"(실제론
> ECS+Iceberg), ECS→OTel 마일스톤 시점은 스키마 고정 전 schema.ocsf.io·semconv로
> **재확인** 필요.

---

## 5. 저장/질의 — 플러그블 백엔드, 계약 고정

> ✅ **확정 (2026-10-23 사용자, 호환성 반영):** **2-스토어**로 분리하고, 이벤트
> 스토어는 **질의+탐지 추상화 뒤에서 교체 가능**하게:
> - **설정·케이스·커버리지 레지스트리 = Postgres(+Timescale)** (저볼륨·관계형)
> - **이벤트 스토어 = 기본 OpenSearch+Security Analytics** (호환성 1급: ES API·
>   Dashboards·Sigma 네이티브·OCSF 인덱스 확인, Apache-2.0) / **대안 ClickHouse**
>   (대용량·비용 최우선) / **dev DuckDB·SQLite**(데몬 0).
> - SQLite 단독 기본은 철회(SIEM 볼륨·호환성 부적합). 현 SQLite receiver는
>   마이그레이션 중 **하위호환 표면**으로만 유지.

- **질의 계약을 고정**하고 백엔드를 교체 가능하게: `events/search/timeline/
  sources/metrics/bodies/stream`. (현 receiver API를 계약으로 승격 → 하위호환)
- **2-스토어 분리**(정석 — Graylog=Mongo+OpenSearch, Matano=제어plane+Iceberg,
  RunReveal=config+ClickHouse): 설정=Postgres, 이벤트=검색/컬럼형. 접근 패턴이
  정반대(트랜잭션·소행 갱신 vs append·대량 스캔)라 한 엔진에 몰면 한쪽이 희생됨.
- **이벤트 스토어 선택**(추상화 뒤 교체):
  - **기본 OpenSearch+Security Analytics** — Sigma 탐지·상관·알림 **내장**(자작
    최소), 기존 SIEM 도구와 호환, Apache-2.0/Linux Foundation.
  - **대안 ClickHouse** — 컬럼형, 대용량·압축·비용 최강(SIEM 벤더 채택 최다,
    RunReveal). 탐지는 pySigma→ClickHouse SQL로 자작.
  - **dev DuckDB/SQLite** — 데몬 0, 단일노드/데모.
- **OCSF 매핑(하이브리드)**: hot 질의키(`time·class_uid·severity_id·actor.user·
  src/dst.ip·metadata.product`)는 **타입 컬럼/인덱스 필드로 승격**, 전체 OCSF는
  **JSON 필드로 보존**(`raw_data`/`unmapped`). → 소스-비종속 질의 빠르게 +
  희귀필드 schema-on-read.
- **에이전트 전수 열람**: 동일 OCSF envelope 위 공통 필드로 **소스-비종속 질의**,
  필요 시만 `raw_data`/`unmapped` drill-down. **read-only 파라미터 질의**(임의
  엔진 접근 금지). 스토어는 **MCP tool 추상화 뒤**라 교체해도 에이전트 불변.
- **백엔드는 독립 설치 (no "install everything")**: 이벤트 스토어 백엔드는 이름으로
  선택되는 **레지스트리**(entry-point `decepticon.event_stores`)로 꽂는다 —
  `get_event_store("opensearch", ...)`. in-tree 백엔드는 자기 optional dep만 가진
  extra(`decepticon[opensearch]`는 opensearch-py만)로, 외부 백엔드(예
  `autobot-store-clickhouse`)는 별도 pip 패키지로 각각 설치. **모든 extra를 한꺼번에
  강제하는 번들은 없다**(`all`은 편의 메타일 뿐). 커버리지/응답 어댑터도 동일 —
  각 어댑터 = 독립 설치 가능한 패키지(§3.2, §8.2).

---

## 6. 탐지 — 3-tier (단기: ①②, LLM ③)

- **포맷 = Sigma(+correlation spec) + pySigma**. 정규화는 **pipeline**에서
  (룰에 넣지 않음). Sigma 불가 로직(멀티소스 조인·enrichment 의존)은 **Python
  detection-as-code 타입 클래스**(pypanther식) — 동일 메타데이터(ATT&CK 태그·
  risk_score·falsepositives·tests) 강제.
- **실행 엔진(스토어 따라)**: 기본 **OpenSearch Security Analytics가 Sigma를
  네이티브 실행**(상관·알림 포함 — 자작 최소). ClickHouse 백엔드에선 pySigma
  ClickHouse backend로 SQL 컴파일. 어느 쪽이든 **Sigma가 이식성 계약**이라
  스토어 교체 시 룰팩은 그대로.
- **룰팩 = 커버리지 플러그인마다 번들**(pipeline + 룰 + 샘플이벤트 CI). SigmaHQ
  릴리스형 배포.
- **① 결정적 per-event 매처**(스트리밍) → **② bounded-window 상관**
  (threshold/value_count/temporal_ordered, timespan ≤24h, building-block→case)
  → **③ LLM triage**: **이미 매칭된** 알림/케이스만 bounded window로 읽어
  구조화 verdict(심각도·dedup 클러스터·suppress 권고·조사 서사)만. **매칭
  결정 생성·변경 불가 — 코드로 차단.** LLM-생성 룰은 **LLM-as-judge + 휴먼
  게이트** 통과 후에만 룰팩 진입.

### 6.1 인시던트/케이스 모델
- **dedup**: 지문=(rule_id, 그룹 엔티티, 정규화 키필드) within suppression
  window → 동일 지문 count 합산.
- **그룹핑**: 공유 엔티티(user/host/ip)로 kill-chain window 내 케이스 묶음.
  building-block은 페이징 안 함, 누적만.
- **ATT&CK**: 룰 `tags`의 technique → 케이스 전파. 서로 다른 **tactic**이 걸칠수록
  케이스 스코어↑ (RBA).
- **위험 스코어**: per-alert risk × 자산중요도/식별자역할 → 케이스 위협스코어.
  **케이스 스코어로 페이징**(원시 알림 아님).
- **MTTD**: `detection_time − event_time` 기록, drift 경보.

---

## 7. 조사 — read-only 에이전트 (단기: 현 Blue agent 계승·강화)

- **read-only ReAct** 에이전트: 정규화 저장소 질의 + enrichment + body/timeline/
  search. bash·containment 없음(현 Blue Cell 계승).
- **커버리지-gap 인지**: 커버리지 레지스트리(heartbeat 생존 + drop 상태) +
  capability map(어댑터 `coverage_manifest` → ATT&CK). 에이전트는 **"증거 없음"
  vs "가시성 없음"**을 구분해 결론을 정규화 — 예: "lateral-movement 이벤트 없음,
  단 Windows auth 커버리지 DEGRADED(heartbeat 40분 정체, 3.1% drop) → clean이
  아니라 gap."
- **서명된 Investigation Ledger**: 모든 질의·결론을 감사 가능하게 기록.
- 로그 내용은 **untrusted**로 취급(프롬프트 인젝션 방어).

---

## 8. 대응 — 등급제 + 가드레일 (최종 목표)

### 8.1 등급 (가역성 → 가드레일)

| 등급 | 액션 예 | 가역성 | 기본 가드레일 |
|---|---|---|---|
| **G0 통보** | 티켓·알림·enrichment | N/A | 자동 허용 |
| **G1 탐지배포** | Sigma→SIEM 룰 push | 가역(버전·롤백) | dry-run+canary 자동 |
| **G2 차단** | FW/proxy/WAF IP·도메인 차단 | 가역(TTL) | HITL 승인; 티어 allowlist만 자동 |
| **G3 격리·비활성** | EDR 호스트 격리, 계정 비활성화 | 가역(명시 해제 필요) | **필수 HITL** + 스코프 + 자동롤백 타이머 |
| **G4 파괴** | 프로세스 kill, 파일 삭제/격리, 재이미지 | 비가역 | **HITL + break-glass**, 자율 절대 금지 |

- **reversible-by-default**: G2/G3은 TTL + 스케줄 자동 롤백(재확인 없으면 해제).
  (Defender 격리가 자동만료 안 되는 footgun 반복 금지)

### 8.2 Response Adapter 계약 (entry-point `autobot.response`)
Shuffle(OpenAPI-first)·Tracecat(`tools.<integration>.<action>` 레지스트리)·
Cortex(analyzer/responder 분리) 종합:

```text
ResponseAdapter
  action_grade: G0..G4
  d3fend / ocsf mapping
  target_schema: {asset_type: host|ip|account|file, selector}
  dry_run() -> blast_radius       # 필수. 변경 없이 영향 예측
  apply()   /  rollback()         # G1~G3 rollback 필수; G4는 reversible:false 선언
  scope: {allowed_tiers, deny_list}   # DC/핵심DB never-isolate
  credentials: {scopes}           # 최소권한 (예: Machine.Isolate)
  rate_limit: {per_min, per_hour}, idempotency_key
```

- **read/write 물리 분리**(Cortex식): 조사 LLM은 **mutating 툴을 아예 보유하지
  않음**.

### 8.3 탐지→조사→대응 바인딩
- **durable checkpointed graph**(LangGraph persistence; Tracecat-style 내구 실행).
- LLM은 조사 후 **구조화 권고 객체**(incident_id → 제안 등급·타깃·근거·증거)만
  생성. **mutating 툴 직접 호출 안 함.**
- G2+ 액션은 LangGraph `interrupt()` 게이트에서 제안 호출 + **dry-run blast
  radius**를 사람에게 제시, accept/edit/reject(`Command(resume=...)`).
- **아키텍처로 비파괴 보장**(프롬프트가 아니라): write 툴은 runtime/middleware에서
  게이팅, G4 툴은 에이전트 툴셋에서 **부재**, break-glass로만 사람이 실행.

### 8.4 MVP (최종 1차)
**G0+G1만 자율**(통보·enrichment·티켓 + dry-run/canary/rollback 포함 탐지배포).
**G2/G3는 recommend-only + 필수 HITL** + dry-run + TTL 자동롤백 + 불변 감사로그 +
rate limit + never-touch deny-list + break-glass kill-switch. **G4는 자율에서 제외.**

---

## 9. 생태계·패키징 적합 (extension-contract 준수)

- `autobot` 패키지는 `decepticon-core`만 필수 의존 → Red 없이 단독.
- `decepticon[blue]` extra로 Decepticon이 AutoBot을 선택 동봉(entry-point 자동
  발견) → "Decepticon만 설치해도 Blue 사용 가능" 충족.
- **core 경계 (현재 vs 추후):** 지금은 blue 계약(OCSF event·EventStoreProtocol·
  CoverageAdapterProtocol)을 실용상 `decepticon-core`에 뒀다(no-runtime-deps·단일
  계약 홈). 그러나 이들은 red가 쓰지 않는 **blue 전용**이므로, **추후 autobot(또는
  autobot-core)으로 이관**하고 `decepticon-core`에는 red/blue/purple이 실제로
  공유하는 타입(예: detection-gap on Finding, plugin_loader)만 남긴다. 결정 시점:
  Cybertron이 어떤 타입을 공유로 요구하는지 확정될 때(Phase 2/3). 이관해도 autobot은
  계속 core를 공유 의존성으로 소비(core에서 "독립"이 아니라 역할 재배치).
- **레포 최종형:** autobot → `BitterSecurity/AutoBot` 레포로 분리, `decepticon-core`를
  **PyPI 배포본**으로 소비. 세 레포(red/blue/purple) 공통 토대가 core.
- 소유권 레이어 준수: capability core(에이전트 롤·프롬프트) / reusable knowledge
  (SKILL.md) / business workflow(WORKFLOW.md: 탐지→조사→대응 프로세스) / client
  adapter(CLI·Web). Cybertron이 이 WORKFLOW를 **위에서 조율**.
- Cybertron은 AutoBot(방어) + Decepticon(공격)을 소비해 purple 루프 오케스트레이션.

---

## 9b. 호환성(Interoperability) — 아키텍처 1급 기둥

목표함수의 호환성 축(원칙 9)을 구현하는 레이어. 한 플랫폼에 종속되는 대신
**표준 + 다수 시스템 어댑터**로 interop.

- **Ingest-from (기존 시스템을 소스로 흡수)**: `autobot.coverage` 어댑터로
  **Wazuh·Elastic/OpenSearch·Splunk·syslog·OTLP**에서 수집 → OCSF 매핑. 특히
  **Wazuh는 1급 소스**(엔드포인트 텔레메트리·FIM·취약점·ATT&CK 탐지를 흡수;
  기존 Wazuh MCP 서버 패턴 활용). 베이스로 상속하지 않고 소스로 흡수해 Wazuh
  스키마·무게에 안 갇힘.
- **Push-to (기존 방어 스택으로 대응 전달)**: `autobot.response` 어댑터로
  **Splunk·Sentinel·Elastic·CrowdStrike/Defender**에 탐지 배포·봉쇄(§8 G1/G3).
  현 `tools/defense/*` 연결기를 이 계약으로 재작성.
- **표준 호환**: **Sigma** 룰 import(기존 코퍼스 재사용) · **OCSF↔ECS 양방향
  매핑**(ECS 기반 룰 import + ES 호환 도구용 ECS 뷰 export) · **OTel** 전송.
- **하위호환(우리 이전 시스템)**: 현 Blue Cell **receiver 질의 API·5 센서툴·8
  MCP툴**을 마이그레이션 중 호환 표면으로 유지 → 기존 통합이 깨지지 않게.

---

## 10. 현 Blue Cell → 신 아키텍처 매핑

| 현재 | 신 아키텍처 | 처리 |
|---|---|---|
| `blue_receiver.normalize()` if/else | CoverageAdapter.normalize dispatch | **재설계** |
| receiver SQLite + 질의 API | Store 질의 계약(추상화) + 기본 OpenSearch / dev DuckDB·SQLite | **계약 승격**, 백엔드 플러그블화·OpenSearch 채택 |
| receiver `normalize` + monitor 전체 | OpenSearch+Security Analytics 채택 | **substrate 채택**(저장·질의·Sigma 탐지·상관 재사용) |
| source enum(4곳 하드코딩) | `/sources` 구동 source-비종속 | **제거** |
| fluent-bit 2입력 고정 | fragment include 커버리지 | **재설계** |
| `blue_monitor.RULES` 상수 | Sigma+pySigma 룰팩 (플러그인별) | **교체** |
| monitor AI watch | LLM triage tier(③) — 매칭된 것만 | **계승·경계 강화** |
| 5 `blue_sensor_*` 툴 + 8 MCP 툴 | 조사 에이전트 read 툴(계약 유지) | **계승**(source enum 제거) |
| `agents/standard/blue_cell.py` | AutoBot 조사 에이전트 롤 | **이동·계승** |
| `tools/defense/{splunk,sentinel,elastic,edr}` | ResponseAdapter(G1/G3) | **이동·재작성** |
| `tools/defense/blue_cell.py`(KG), `tap.py`, vaccine | Cybertron (System A) | **잔류→추후 이전** |

---

## 11. Phase 계획

- **Phase 0** — 이 문서 합의 + §4 스키마/§5 저장 caveat 재확인(OCSF/semconv,
  OpenSearch Security Analytics의 Sigma-on-OCSF 동작 PoC).
- **Phase 1 (단기, 모노레포 내)** — System B를 "끌 수 있는 플러그인"으로 +
  substrate 채택 착수: source enum 제거, CoverageAdapter 계약 + 기본 2어댑터,
  Store 질의 계약(추상화) 승격, **OpenSearch 단일노드를 현 SQLite receiver 옆에
  세워 dual-write 검증**, 조사 에이전트 계승. `[blue]` extra 자리.
- **Phase 2** — monitor 로직을 **Sigma 룰 + Security Analytics detector로 포팅**,
  스토어 질의·탐지·알림을 **MCP tool 서버**로 래핑(에이전트가 구동). SQLite
  receiver 하위호환 유지하다 retire. `autobot` 패키지 추출(core 의존,
  entry-points), 룰엔진·어댑터 프로토콜 core 승격.
- **Phase 3** — AutoBot 레포 이관 + PyPI 배포. Decepticon in-tree Blue 제거.
  클라이언트 capability 게이팅. ClickHouse 대안 백엔드 PoC(대용량 배포용).
  Wazuh ingest-from 어댑터.
- **Phase 4 (최종)** — 대응 레이어: ResponseAdapter(push-to SIEM/EDR) + G0/G1
  자율 + G2/G3 recommend-only HITL + 가드레일. 탐지→조사→대응 E2E.

### 11.1 Phase 1 상세 작업 (단기 — 모노레포 내, 되돌리기 쉬움)

순서대로. 각 작업은 독립 머지 가능하도록 테스트 동반. 모두 `feat/*` 태스크
브랜치에서 수행(AGENTS.md: main 확인 후 분기, CHANGELOG Unreleased 기록).

| # | 작업 | 산출/수용 기준 |
|---|---|---|
| **1** | **OCSF Event 계약 정의** (`decepticon-core`에 `types/ocsf_event.py` 등): envelope(class·metadata·severity·observables·**raw_data·unmapped**·occurred/observed time) | pydantic 타입 + 스키마 테스트. core는 langchain 미의존 유지 |
| **2** | **Store 질의 계약 승격 + substrate PoC**: 현 `blue_receiver` HTTP API(`events/search/timeline/sources/metrics/bodies/stream`)를 추상 `EventStore` 프로토콜로 정의. 백엔드: DuckDB/SQLite(dev) + **OpenSearch 단일노드를 현 receiver 옆에 세워 dual-write 검증**. Sigma-on-OCSF 동작 확인 | 계약 테스트(백엔드 교체 가능 증명), OpenSearch로 기존 질의 재현, 하위호환 유지 |
| **3** | **source enum 하드코딩 제거** (분석 §3.2 — receiver `/timeline`·`metrics()`, `blue_sensor_timeline`, `decepticon_blue_timeline`): 2-소스 고정 → `/sources` 구동 source-비종속 | 신규 임의 source로 timeline/metrics 질의 통과. 회귀 테스트 |
| **4** | **CoverageAdapter 계약 정의** (`autobot.coverage` entry-point): source_id·signal_type·collection(fluentbit_fragment\|run)·normalize·cursor·heartbeat·drop_counter·rule_pack·coverage_manifest | 프로토콜 + 기본 2어댑터(`http-ingress-proxy`,`target-log-file`)를 이 계약으로 재작성, 기존 정규화 동작 보존 |
| **5** | **normalize() dispatch화**: receiver if/else → 어댑터 레지스트리. 미등록 소스는 raw_data+unmapped passthrough | 새 어댑터 추가 시 receiver 코드 수정 불요 |
| **6** | **커버리지 레지스트리 + capability map**: heartbeat 생존·drop 상태 집계, 어댑터 coverage_manifest→ATT&CK. `/coverage` 질의 추가 | "증거 없음 vs 가시성 없음" 질의 가능 |
| **7** | **탐지 엔진 교체**: `blue_monitor.RULES` 상수 → **Sigma 룰팩**(정규화는 pipeline OCSF↔ECS). 실행은 **OpenSearch Security Analytics detector**(①per-event ②상관 내장); ClickHouse 백엔드 시 pySigma→SQL | 샘플 룰팩으로 탐지 재현, MTTD 기록 유지, 스토어 교체해도 룰팩 불변 |
| **8** | **LLM triage 경계 강화**: monitor AI watch를 "매칭된 알림만 읽는 주석 tier"로 명시, 매칭 결정 불가(코드 레벨 차단) | triage가 매칭/케이스 생성 못 함을 테스트로 증명 |
| **9** | **Blue를 "끌 수 있는 플러그인"화** (분석 §3.8): `graph_registry` STANDARD_GRAPHS에서 blue_cell 제거→entry-point, `tools/__init__` DEFENSE_TOOLS 하드 import 제거, `mcp_server/server` 조건부, `[blue]` extra 자리 | `pip install decepticon`(extra 없이) = Blue 흔적 0·Red 테스트 green; `[blue]` = 복원 |
| **10** | **CLI/Web capability 게이팅**: `/mode blue`·blue.ts를 백엔드 capability probe 기반 노출 | AutoBot 미설치 시 안내, 설치 시 활성 |

**Phase 1 DoD:** 모노레포 안에서 Blue(탐지+조사)가 **끌 수 있는 플러그인**이 되고,
**커버리지가 어댑터로 추가 가능**하며, **source enum 없이 에이전트가 전 소스 열람**,
스키마는 **OCSF**, 탐지는 **Sigma+pySigma**. 대응(Phase 4)·레포 이관(Phase 3)은 이후.

---

## 12. 핵심 출처

SIEM: [Matano](https://github.com/matanolabs/matano) · [Elastic Security](https://www.elastic.co/docs/current/serverless/security/overview) · [OpenSearch SA](https://docs.opensearch.org/latest/security-analytics/sec-analytics-config/detectors-config/) · [Wazuh decoders](https://documentation.wazuh.com/current/user-manual/ruleset/decoders/custom.html) · [Graylog](https://www.stackhero.io/en-US/services/Graylog/documentations/Introduction/How-Graylog-works) · [Quickwit](https://github.com/quickwit-oss/quickwit)
수집·스키마: [Fluent Bit](https://docs.fluentbit.io/manual) · [Vector e2e ack](https://vector.dev/docs/architecture/end-to-end-acknowledgements/) · [OTel Collector](https://opentelemetry.io/docs/collector/configuration/) · [OTel log model](https://opentelemetry.io/docs/specs/otel/logs/data-model/) · [OCSF](https://schema.ocsf.io) · [ECS↔OTel](https://opentelemetry.io/blog/2023/ecs-otel-semconv-convergence/)
탐지: [pySigma backends](https://sigmahq-pysigma.readthedocs.io/en/latest/Backends.html) · [SigmaHQ](https://github.com/SigmaHQ/sigma) · [Sigma correlation](https://github.com/SigmaHQ/sigma-specification/blob/main/specification/sigma-correlation-rules-specification.md) · [pypanther](https://panther.com/blog/pypanther-code-driven-detection) · [Elastic EQL](https://www.elastic.co/docs/solutions/security/detect-and-alert/eql) · [Splunk RBA](https://www.splunk.com/en_us/pdfs/resources/solution-guide/embark-on-your-risk-based-alerting-journey-with-splunk.pdf)
대응·AI SOC: [Shuffle](https://github.com/frikky/shuffle-apps) · [Cortex](https://docs.strangebee.com/cortex/) · [Tracecat](https://docs.tracecat.com/integrations/overview) · [Defender isolate](https://learn.microsoft.com/en-us/defender-endpoint/api/isolate-machine) · [Darktrace Antigena](https://cdn.blueally.com/cyberaiworks/datasheets/ds-antigena-network.pdf) · [LangChain HITL](https://docs.langchain.com/oss/python/human-in-the-loop) · [MS Copilot agents](https://www.microsoft.com/en-us/security/blog/2025/03/24/microsoft-unveils-microsoft-security-copilot-agents-and-new-protections-for-ai/)
LLM 탐지 연구(정식 인용 전 arXiv 재확인): RuleGenie 2505.06701 · ThreatPilot 2412.10872 · CORTEX(NeurIPS 2025)
