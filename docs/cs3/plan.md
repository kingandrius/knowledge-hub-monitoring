# CS3 – Project Plan (Knowledge Hub)

**Goal:** CS3 core + Flavor A, aiming for **Proficient** on LO1–LO4.
**Method:** one-person Scrum, 1-week sprints, as in CS2. Sprint review = check against this plan; changes are logged below.
**Rule for every task:** the simplest version that meets the requirement text. Reuse what exists. No new paid resources.
**Deadline:** 9 Dec 2026. **Own target: finished by ~2 Dec.**

## 1. Decisions

| # | Date | Decision | Why |
|---|---|---|---|
| D1 | 11 Nov | Azure resources stay in the Fontys subscription. App sign-in and Conditional Access go in tenant **IronWolfBV** (Entra ID P2, Global Admin). | In the Fontys tenant I am a student, not an admin, so Conditional Access isn't possible there. The app can run in one tenant and sign users in through another. |
| D2 | 11 Nov | All documentation in Markdown in the Git repo (`docs/cs3/`) | The deliverables say "submitted via Git repository"; less work than .docx; versioned |
| D3 | 11 Nov | Container registry = GitHub Container Registry (GHCR) | Free; next to the repo. ACR costs money. |
| D4 | 11 Nov | GitHub → Azure sign-in via OIDC (federated credential) | No client secret that expires (lesson from the Intune script) |

## 2. Requirements → tasks

| Requirement | In short | Task | LO |
|---|---|---|---|
| REQ-001 | Web interface | T1 Flask frontend | 1, 3 |
| REQ-002 | Entra ID access | T2 Entra sign-in (+ CA in T6) | 2 |
| REQ-003 | Independent units | T3 3 containers + Compose | 1, 3 |
| REQ-004 | Automated build/test/deploy | T4 GitHub Actions → GHCR → Container Apps | 3 |
| REQ-005 | Frontend on on-prem Docker | T4b Docker host VM in lab | 1 |
| REQ-006 | Monitor containers | T5 collector via Docker API | 3 |
| REQ-007 | Zero Trust in Azure | T6 ZTA changes + CA/MFA | 2 |
| REQ-008 | IPv6 | T7 / A.1 IPv6 on pfSense | 1 |
| REQ-009 | DNS security | T7 DNSSEC + DoT on pfSense | 2 |
| REQ-010 | YAML/JSON config | Compose, workflow, Ansible, script config | 3 |
| REQ-011 | Config management explored | T8b Ansible PoC playbook | 3 |
| REQ-012 | Backup/restore | T8 backup + restore test | 4 |
| REQ-013 | Incident/problem/change | T8 ITSM page | 4 |
| REQ-SEC-01 | Block bad IPs | A.2 pfBlockerNG | 2 |
| REQ-SEC-02 | Detect attacks | A.2 Suricata (IDS) | 2 |
| REQ-SEC-03 | Safe external access | A.2 HAProxy (TLS) → on-prem frontend | 1, 2 |
| Flavor A | Firewall as code | A.2 REST API Python script | 3 |

## 3. Sprints

| Sprint | Dates | Work | Done when |
|---|---|---|---|
| 1 | 11–17 Nov | Plan; use cases; Flask frontend; Entra sign-in; 3 containers + Compose (non-root, volume, .env); container metrics | **M1:** `docker compose up` locally → sign in → dashboard shows system + container data |
| 2 | 18–24 Nov | GitHub Actions (test → build → GHCR → Container Apps); Docker host VM in lab; ZTA changes + **CA screenshots**; IPv6 + DNS security on pfSense | **M2:** push → app live in Container Apps with sign-in; IPv6 ping + DNSSEC check work |
| 3 | 25 Nov–1 Dec | REST API script; pfBlockerNG; Suricata; HAProxy; Ansible PoC; backup + restore test; ITSM page | **M3:** evidence for every Flavor A requirement |
| 4 | 2–9 Dec | Architecture doc + diagram, guides, security report, reading guide per LO, reflection, presentation | **M4:** all deliverables in Git |

Note: the CS2 LO3/LO4 Show & Grow is in the week of 16 Nov. Reserve time for it in sprint 1–2.

## 4. Risks

| # | Risk | Measure |
|---|---|---|
| R1 | IronWolfBV trial ends 3 Dec, so Conditional Access stops | Build CA in sprint 2; screenshots early |
| R2 | pfSense REST API package doesn't support my pfSense version | Check compatibility before sprint 3 |
| R3 | Laptop RAM: GNS3 + pfSense packages (Suricata) + Docker host VM | Run only what is needed per test; Suricata on 1 interface |
| R4 | Fontys tenant blocks the GitHub → Azure setup | Use a user-assigned managed identity with a federated credential (an Azure resource, no app registration) |
| R5 | Container Apps costs | Consumption plan, scale to 0, budget alert stays on |
| R6 | Scope creep (as in CS2) | "Simplest version" rule; every addition logged as a decision |

## 5. Change log

| Date | Change |
|---|---|
| 11 Nov | Plan created |
