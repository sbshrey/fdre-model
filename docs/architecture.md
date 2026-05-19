# FDRE Operations — Architecture Review

Audience: staff engineering review. Covers what's deployed today, the proposed at-scale shape, and three deployment variants (AWS / GCP / self-hosted) that all keep client infra in client scope and our code opaque.

## Scope split (commercial)

| Concern | Owner | Notes |
|---|---|---|
| **Application code** | **Digitised Energy** | Ships only as an opaque container image. Source never leaves DE control. |
| **Application servers** | **Digitised Energy** | Only cost item DE absorbs — runs the closed container. Sized per tenant. |
| Object storage (S3 / GCS / MinIO) | Client | Holds immutable cycle artifacts, raw inputs, audit JSON. |
| Index / metadata store (DynamoDB / Firestore / Postgres) | Client | Lookup table for cycles, versions, portfolios. |
| Identity provider (Auth0 tenant) | Client | OIDC; client manages users + Management API credentials. |
| Edge / CDN / WAF / TLS | Client | DNS + cert ownership stays client-side. |
| Observability (logs, metrics, traces) | Client | App emits structured events; client routes to their observability stack. |
| Backup, DR, retention policy | Client | Client controls retention rules on their bucket / table. |

DE delivers: a versioned container image, the bootstrap config it expects, and the operational runbook. Everything storage-side is client-credentialed via IAM/SA/service-key, mounted into the container at start.

---

## D1 — Current architecture (today)

Single-tenant deployment as it runs today.

```mermaid
flowchart LR
    classDef de fill:#DEF4F0,stroke:#00A884,stroke-width:2px,color:#003c2a;
    classDef client fill:#E3EBFA,stroke:#2E5EE6,color:#0c2a73;
    classDef ext fill:#FFF4D8,stroke:#9F6000,color:#5a3500;
    classDef store fill:#F5F5EF,stroke:#6F7480,color:#1a1a1a;

    User([Operator / Admin Browser]):::ext

    subgraph DE["Digitised Energy scope"]
        direction TB
        APP["Flask app (fdre-market-web)<br/>Gunicorn · WSGI<br/>web/app.py · auth.py"]:::de
        DOM["Domain layer<br/>market/{engine, aggregation, rules, metrics, models, registry}<br/>config.py · exports.py"]:::de
        STORE_ABS["storage/ abstraction<br/>local.py · hosted.py · scope.py"]:::de
        APP --> DOM --> STORE_ABS
    end

    subgraph CLI["Client scope"]
        direction TB
        AUTH0[(Auth0 tenant<br/>OIDC + Mgmt API)]:::ext
        S3[(S3 bucket<br/>immutable artifacts)]:::store
        DDB[(DynamoDB<br/>cycle + version index)]:::store
        WS[(".workspace/ on disk<br/>(local-only fallback)")]:::store
    end

    User -- HTTPS --> APP
    APP <-- OIDC login --> AUTH0
    STORE_ABS -- hosted backend --> S3
    STORE_ABS -- hosted backend --> DDB
    STORE_ABS -- local backend --> WS

    note["FDRE_STORAGE_BACKEND switches local ↔ hosted<br/>Single process; no worker pool; no queue."]:::ext
    APP -.- note
```

**What this gives us:** functional end-to-end advisory for a single client/workspace, with full audit. **What it doesn't:** horizontal scale, async recalc, fan-out across many projects, or live-feed ingestion concurrency.

---

## D2 — Proposed at-scale logical architecture

Cloud-neutral. Drawn so the same shape fits AWS / GCP / Hetzner with different concrete services. Note the scope split — DE owns only the app servers; everything stateful or networking is client-owned and client-billed.

```mermaid
flowchart TB
    classDef de fill:#DEF4F0,stroke:#00A884,stroke-width:2px,color:#003c2a;
    classDef client fill:#E3EBFA,stroke:#2E5EE6,color:#0c2a73;
    classDef store fill:#F5F5EF,stroke:#6F7480,color:#1a1a1a;
    classDef ext fill:#FFF4D8,stroke:#9F6000,color:#5a3500;

    User([Operators · Admins · Internal]):::ext

    subgraph CLIENT["Client-owned (client billing)"]
        direction TB
        EDGE["Edge / WAF / TLS<br/>(CDN, DNS, certs)"]:::client
        IDP[(Auth0 tenant<br/>OIDC + Mgmt API)]:::client
        OBJ[(Object store<br/>immutable artifacts · audit JSON)]:::store
        IDX[(Index DB<br/>cycles · versions · portfolios)]:::store
        QUEUE[/Async queue<br/>decision-cycle jobs/]:::client
        CACHE[(Cache<br/>live-board hot reads)]:::client
        OBS[(Observability<br/>logs · metrics · traces)]:::client
        FEED["Future: live feed connectors<br/>(plant SCADA · price API)"]:::client
    end

    subgraph DE["DE-owned (DE billing · code opaque)"]
        direction TB
        LB[Internal LB / ingress]:::de
        subgraph WEB["Web tier — stateless container fleet"]
            W1[fdre-market-web<br/>read + write API]:::de
            W2[fdre-market-web<br/>read + write API]:::de
            W3[fdre-market-web<br/>...horizontally scaled]:::de
        end
        subgraph WORK["Worker tier — stateless container fleet"]
            K1[recalc worker<br/>decision-cycle build]:::de
            K2[recalc worker<br/>compliance metrics]:::de
            K3[ingest worker<br/>future live-feed pull]:::de
        end
    end

    User --> EDGE --> LB
    LB --> WEB
    WEB <--> CACHE
    WEB --> QUEUE --> WORK
    WORK --> OBJ
    WORK --> IDX
    WEB --> OBJ
    WEB --> IDX
    WEB <-- OIDC --> IDP
    WORK -. structured events .-> OBS
    WEB -. structured events .-> OBS
    FEED --> QUEUE
```

**Key scale moves vs today:**

| Capability | Today | At scale |
|---|---|---|
| Decision recalc | Inline on HTTP request | Enqueued; worker pool consumes |
| Live-board reads | Re-read storage per poll | Cache-backed (TTL = interval cadence) |
| Live feeds | Manual CSV/paste | Connector workers pull on schedule |
| Multi-tenant isolation | Workspace prefix in keys | Per-tenant queue + key prefix + cache namespace |
| Identity | Single Auth0 tenant | Per-client Auth0 tenant or organization |
| Scale unit | Single process | Independent web + worker autoscaling |
| Code distribution | Source-installed locally | Signed container image only |

---

## D3 — Deployment variant A · AWS

Same logical shape, mapped to managed AWS services. DE app servers run as ECS/Fargate tasks; everything else is in the client AWS account with cross-account IAM trust.

```mermaid
flowchart TB
    classDef de fill:#DEF4F0,stroke:#00A884,stroke-width:2px,color:#003c2a;
    classDef client fill:#E3EBFA,stroke:#2E5EE6,color:#0c2a73;
    classDef store fill:#F5F5EF,stroke:#6F7480,color:#1a1a1a;

    User([User])
    User --> R53[Route 53 + ACM]:::client
    R53 --> ALB["ALB + WAF<br/>(client VPC)"]:::client

    subgraph DEACCT["DE app account"]
        direction TB
        ECR[ECR<br/>signed container image]:::de
        FARGATE_WEB["ECS Fargate — web tasks<br/>fdre-market-web"]:::de
        FARGATE_WORK["ECS Fargate — worker tasks<br/>recalc · ingest"]:::de
        ECR --> FARGATE_WEB
        ECR --> FARGATE_WORK
    end

    subgraph CLIENTACCT["Client AWS account"]
        direction TB
        AUTH0_AWS[(Auth0 tenant)]:::client
        S3_AWS[("S3<br/>cycle artifacts + audit JSON")]:::store
        DDB_AWS[("DynamoDB<br/>cycle/version index")]:::store
        SQS[/SQS<br/>recalc + ingest queue/]:::client
        ELASTICACHE[(ElastiCache Redis<br/>live-board cache)]:::client
        CW[(CloudWatch + X-Ray)]:::client
    end

    ALB --> FARGATE_WEB
    FARGATE_WEB <-- OIDC --> AUTH0_AWS
    FARGATE_WEB --> SQS --> FARGATE_WORK
    FARGATE_WEB <--> ELASTICACHE
    FARGATE_WORK --> S3_AWS
    FARGATE_WORK --> DDB_AWS
    FARGATE_WEB --> S3_AWS
    FARGATE_WEB --> DDB_AWS
    FARGATE_WEB -. logs/metrics .-> CW
    FARGATE_WORK -. logs/metrics .-> CW
```

**Trust model:** ECS task role in DE account assumes a client-side IAM role with `s3:*` and `dynamodb:*` scoped to the per-customer key prefix. No client credentials in DE's account; rotation handled by AWS STS.

---

## D4 — Deployment variant B · GCP

Same shape on GCP. Cloud Run replaces Fargate; GCS, Firestore (or Bigtable), Pub/Sub, Memorystore replace the AWS storage and queue tier.

```mermaid
flowchart TB
    classDef de fill:#DEF4F0,stroke:#00A884,stroke-width:2px,color:#003c2a;
    classDef client fill:#E3EBFA,stroke:#2E5EE6,color:#0c2a73;
    classDef store fill:#F5F5EF,stroke:#6F7480,color:#1a1a1a;

    User([User])
    User --> CDN["Cloud DNS + Cloud CDN"]:::client
    CDN --> LB[HTTPS Load Balancer + Cloud Armor]:::client

    subgraph DEPROJ["DE GCP project"]
        direction TB
        AR[Artifact Registry<br/>signed container image]:::de
        RUN_WEB[Cloud Run — web service<br/>fdre-market-web]:::de
        RUN_WORK[Cloud Run jobs / GKE<br/>recalc · ingest workers]:::de
        AR --> RUN_WEB
        AR --> RUN_WORK
    end

    subgraph CLIENTPROJ["Client GCP project"]
        direction TB
        AUTH0_GCP[(Auth0 tenant)]:::client
        GCS[("GCS bucket<br/>cycle artifacts + audit JSON")]:::store
        FS[("Firestore (or Bigtable)<br/>cycle/version index")]:::store
        PUBSUB[/Pub-Sub<br/>recalc + ingest topics/]:::client
        MEMSTORE[(Memorystore Redis<br/>live-board cache)]:::client
        OPS[(Cloud Logging + Monitoring + Trace)]:::client
    end

    LB --> RUN_WEB
    RUN_WEB <-- OIDC --> AUTH0_GCP
    RUN_WEB --> PUBSUB --> RUN_WORK
    RUN_WEB <--> MEMSTORE
    RUN_WORK --> GCS
    RUN_WORK --> FS
    RUN_WEB --> GCS
    RUN_WEB --> FS
    RUN_WEB -. logs/metrics .-> OPS
    RUN_WORK -. logs/metrics .-> OPS
```

**Trust model:** DE service account is granted client-side IAM roles (`roles/storage.objectAdmin`, `roles/datastore.user`) scoped to the per-customer document path / object prefix via conditional bindings. Workload identity federation removes the need for static keys.

---

## D5 — Deployment variant C · Self-hosted (Hetzner / Contabo / OVH / on-prem)

For clients that won't go to public cloud. Same logical shape, runs on plain Linux VMs they own. DE only ships the container image; client provides everything else.

```mermaid
flowchart TB
    classDef de fill:#DEF4F0,stroke:#00A884,stroke-width:2px,color:#003c2a;
    classDef client fill:#E3EBFA,stroke:#2E5EE6,color:#0c2a73;
    classDef store fill:#F5F5EF,stroke:#6F7480,color:#1a1a1a;

    User([User])
    User --> CADDY["Caddy / Traefik / Nginx<br/>(client VM · TLS + WAF)"]:::client

    subgraph DEFLEET["DE-managed runtime on client iron"]
        direction TB
        REG[Harbor or GHCR<br/>signed container image]:::de
        WEB_VM["fdre-market-web container<br/>(Docker / K3s / Nomad)"]:::de
        WORK_VM["worker container<br/>recalc · ingest"]:::de
        REG --> WEB_VM
        REG --> WORK_VM
    end

    subgraph CLIENTIRON["Client-owned VMs + services"]
        direction TB
        AUTH0_SELF[(Auth0 tenant<br/>or Authentik self-hosted)]:::client
        MINIO[("MinIO / SeaweedFS<br/>S3-compatible object store")]:::store
        PG[("PostgreSQL JSONB<br/>cycle/version index")]:::store
        NATS[/NATS or Redis Streams<br/>recalc + ingest queue/]:::client
        REDIS[(Redis<br/>live-board cache)]:::client
        OBS_SELF[(Grafana + Loki + Prometheus + Tempo)]:::client
    end

    CADDY --> WEB_VM
    WEB_VM <-- OIDC --> AUTH0_SELF
    WEB_VM --> NATS --> WORK_VM
    WEB_VM <--> REDIS
    WORK_VM --> MINIO
    WORK_VM --> PG
    WEB_VM --> MINIO
    WEB_VM --> PG
    WEB_VM -. logs/metrics .-> OBS_SELF
    WORK_VM -. logs/metrics .-> OBS_SELF
```

**Trust model:** Client mounts service credentials (MinIO access key, PG connection string) as Docker secrets / systemd-creds. Image only knows the configured endpoints; it never sees the client's other infrastructure.

**Why call this out:** clients in regulated geographies (e.g., India RBI / EU data-residency) often prefer Hetzner / Contabo / on-prem. The storage adapter already supports S3-compatible endpoints, so MinIO is a drop-in.

---

## D6 — Decision-cycle sequence (proposed at-scale)

End-to-end path of a single recalc, illustrating where the queue and worker fit.

```mermaid
sequenceDiagram
    autonumber
    participant U as Operator
    participant W as Web tier (DE)
    participant C as Cache (client)
    participant Q as Queue (client)
    participant K as Worker (DE)
    participant O as Object store (client)
    participant I as Index DB (client)
    participant A as Auth0 (client)

    U->>W: GET /live (cookie session)
    W->>A: validate session / refresh JWT
    W->>C: GET cache(live:{customer}:{project})
    alt Cache hit
        C-->>W: cached live-board payload
        W-->>U: render Live Board
    else Cache miss or stale
        W->>I: lookup latest cycle for project
        I-->>W: cycle metadata + artifact key
        W->>O: GET artifact (allocation JSON)
        O-->>W: artifact bytes
        W->>C: SET cache (TTL = interval cadence)
        W-->>U: render Live Board
    end

    U->>W: POST /recalculate (preset=intraday)
    W->>I: write new cycle (pending)
    W->>Q: enqueue {customer, project, window}
    W-->>U: 202 + cycle id
    K->>Q: pull job
    K->>O: GET active input versions
    K->>K: build_decisions() · evaluate_rules() · metrics
    K->>O: PUT allocation CSV / XLSX / audit JSON
    K->>I: update cycle (complete + summary + checksum)
    K->>C: invalidate cache live key
    U->>W: poll cycle status
    W->>I: read cycle status
    I-->>W: complete
    W-->>U: render updated Live Board
```

---

## Open questions for review

1. **Tenancy granularity.** Today: one process per workspace via env scoping. Proposal: per-tenant queue/cache namespace + shared web fleet. Acceptable, or do we want per-tenant fleets (and pay the cold-start cost)?
2. **Code opacity at the connector layer.** Future live-feed connectors may need client-specific drivers (SCADA, OEM API). Do those ship inside the DE container, or as a thin client-side adapter that publishes to the queue?
3. **Storage adapter contract.** `storage/scope.py` already abstracts local vs hosted; do we extend it to a generic OCI/GCS/MinIO driver and let `FDRE_STORAGE_BACKEND` pick at boot, or per-customer config?
4. **Identity boundary.** Single Auth0 tenant with organisations, or one tenant per client? Affects how Management API credentials are scoped.
5. **DR posture.** Each cycle is reproducible from inputs + rule versions. Do we still mandate point-in-time backups of the index DB, or rely on the audit JSON in object store as the source of truth?
6. **Observability.** App emits structured events; what's the canonical schema the client routes to their stack (OTLP? plain JSON to stdout? StatsD)?

---

*Source for every diagram above lives in this file (`docs/architecture.md`). Rendered PNG copies are in [`docs/architecture-images/`](architecture-images/).*
