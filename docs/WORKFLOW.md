# EyeVen Workflow and Agentic Recovery

This document describes the workflow implemented by the backend, including the
bounded next-action agent, its permitted actions, and how people resume a case
after a follow-up.

## Overview

EyeVen combines deterministic orchestration, structured GLM-based assessments,
and a bounded planner. Predictable cases are handled deterministically; the
planner is reserved for ambiguous evidence, cross-stage interpretation, and
recovery after a bounded retry fails.

```text
Upload documents
       |
       v
Extraction -> Canonical form structuring -> Verification
                                                   |
                                                   v
                                      Policy evaluation -> Routing
                                                               |
                                                               v
                            Deterministic recovery / selective planner
                          /             |             |              \
                   Reprocess      Vendor draft    PIC upload     Human review
```

The agent does not operate as an unrestricted assistant. It receives a
case-specific snapshot, returns exactly one schema-constrained action, and the
backend validates and executes that action. It cannot send email, approve a
case by itself, or invoke arbitrary tools.

## 1. Start and case locking

The normal entry point is `POST /api/workflow/<case_id>/run`, handled by
`CaseService.run_workflow` and `WorkflowOrchestrator.run`.

The orchestrator acquires a per-case lock to prevent overlapping workflow
runs. If the case is already `completed`, `rejected`, or `flagged_high_risk`,
the run stops without changing it.

If there are no uploaded files, EyeVen does not attempt extraction or invoke
the planner. The action executor requests a PIC upload and persists
`awaiting_pic_upload`. Repeated workflow calls remain waiting; an accepted
upload changes the case to `intake_received`, allowing processing to resume.
When a follow-up draft already exists, repeated workflow calls also remain
waiting rather than planning another action.

## 2. Evidence processing stages

The normal pipeline follows the case's current state; it does not blindly rerun
every stage on every request.

### Extraction

`ExtractionOrchestrator` processes the case's uploaded files:

- CSV, XLSX, and XLS files are parsed by the deterministic parser.
- PDF and supported image files are processed by OCR.
- ZIP packages are unpacked into child uploaded-file records, subject to the
  configured ZIP limits.

PaddleOCR returns a recognition score for each text line. EyeVen summarizes
those scores and treats a line below `0.6` as low-confidence OCR. This measures
readability, not whether the vendor's value is factually correct. A technical
OCR error is recorded as an extraction failure and is eligible for one
deterministic retry when classified as transient. `ocr_unavailable`,
unsupported formats, and other non-retryable failures are not retried.
A completed OCR run with low-confidence or unreadable text is not treated as a
technical failure or proof that the vendor omitted the information; the case
waits for PIC correction or a clearer document instead.

During form structuring, OCR scores are provided with the text. When a field's
cited excerpt matches a low-scoring line, EyeVen marks that field
`low_confidence` and caps its field confidence to the OCR score, so the PIC can
correct it before requesting information from the vendor. Raw extracted
contents are not copied into the compact workflow trace.

### Canonical form structuring

`FormService.structure_form` reads parsed/OCR text and asks the canonical-form
agent to map it to EyeVen's known field names. Its structured response includes
values, statuses, confidence, and evidence references. The response is schema
validated before the form-field records are updated.

The case enters a form-aligned or incomplete state according to intake type and
the resulting completeness report.

### Verification

The verification agent compares available form and document evidence,
identifies matches, mismatches, and unresolved fields, and returns a structured
result. The workflow persists a verification run and updates the case's latest
verification status.

An internal demo-registry lookup is available only for the supported company
registration identifiers. It is not an external government or commercial
registry check. Exact field-level registry matches, including tax identifiers,
are reported as internally corroborated. EyeVen does not externally validate
tax identifiers or authenticate ISO certificates; that unavailable capability
does not by itself create a policy gap or block approval. Missing or conflicting
values, invalid local formats, and expired ISO dates remain actionable findings.

### Policy evaluation

The policy agent assesses completeness, risk flags, and blocking versus
recoverable issues. Only company registered name/number, website, main-office
phone, bank account name/number and bank name, latest annual revenue range,
contact email/mobile/person, ISO availability/number/expiry, SSM/ROC/ROB
number, and tax/SST/GST number are in scope. ISO number and expiry apply only
when certification is claimed. Other form fields, including outsourcing and
declaration details, are excluded from verification, policy risk, and
follow-up decisions.

### Routing

The routing agent recommends one of the following outcomes:

- `complete_verified`
- `follow_up_required`
- `flagged_high_risk`
- `rejected`
- `manual_review_required`

Its response is persisted as a routing-decision record and mapped to the case
state. For example, `follow_up_required` maps to `pending_follow_up`.

Routing is not solely an LLM decision. A deterministic safety gate can veto an
approval recommendation. It checks verification and policy results, unresolved
required fields, material evidence issues, and approval confidence. A populated
value is not automatically considered independently verified.

## 3. The bounded agentic loop

After the normal stages, simple missing-field follow-ups are created
deterministically only when all eligible unresolved fields are confirmed absent
from reviewed documents and no ambiguous evidence is present. Low-confidence or
unreadable OCR waits for PIC correction or a clearer upload instead of being
treated as a missing field.
`WorkflowOrchestrator._run_agentic_loop` is reserved for contextual recovery,
including conflicting evidence, evidence that spans stages, and a failed
bounded retry. Predictable failures do not invoke the planner unnecessarily.

The maximum number of actions is configured by `MAX_AGENTIC_ACTIONS` and
defaults to **3**. Each iteration works as follows.

### A. Observe: build the case snapshot

`NextActionPlanner.case_snapshot` assembles current case data, including:

- Case state and whether it is terminal.
- Field values, status, notes, and evidence references.
- Uploaded documents and whether new documents were added after structuring.
- Whether documents have been reviewed.
- Which fields are eligible for vendor follow-up and their availability.
- Verification, policy, and routing summaries.
- Failed prior actions and successful stages whose inputs have not changed.
- Persisted reprocessing attempts, including the result of any automatic retry.
- Existing follow-up-draft count.

The snapshot also classifies unresolved information. For example, it can
distinguish information found in evidence, documents not yet reviewed,
information not found in reviewed documents, and ambiguous or conflicting
evidence.

### B. Plan: choose exactly one action

`NextActionPlanner` sends the snapshot and prior actions to the next-action
planner prompt. The output must pass the JSON schema and contextual validation.
If a structurally valid plan is disallowed by the case context, EyeVen may use
its deterministic evidence-based fallback instead.

The permitted actions are:

| Action | Purpose and constraints |
| --- | --- |
| `targeted_verification` | Check one or both supported company-registration identifiers against the internal demo registry. |
| `reprocess_stage` | Retry or refresh one supported stage: extraction, form structuring, verification, policy evaluation, or routing. |
| `request_pic_upload` | Ask the PIC to upload case documents; only allowed when no documents are attached. |
| `request_information` | Generate a vendor follow-up draft after the evidence has been reviewed and the requested information was not found. |
| `manual_review` | Stop automation and route the case for a person to review. |
| `finish` | Stop planning. This action alone does not approve or reject a case. |

The planner is instructed to:

- Recover from available or corrected information before requesting it again.
- Process newly uploaded documents before requesting information about them.
- Retry a failed stage rather than repeating an unrelated check.
- Avoid rerunning successful stages when their inputs are unchanged.
- Use manual review for ambiguous or contradictory evidence.
- Request vendor information only when required information is absent from
  reviewed documents.
- Avoid requesting optional fields, including authorized signature and company
  chop.

Before planner invocation, explicitly retryable technical failures receive at
most one automatic retry per stage and input fingerprint. The attempt and its
outcome are stored in the existing reprocessing-run metadata, so the limit
survives later workflow invocations. A changed input fingerprint may be
processed as a new attempt; the same failed inputs cannot be retried again.

### C. Validate and guard the plan

Before execution, EyeVen verifies the action name, target fields, and stage.
Contextual guards also enforce conditions such as:

- Vendor follow-up needs unresolved follow-up-eligible fields and must not be
  used while evidence is available or documents remain unreviewed.
- PIC upload is only appropriate when the case has no uploaded documents.
- `finish` is not allowed while eligible fields remain unresolved.
- A successful stage with unchanged inputs cannot be selected for reprocessing
  again, unless the stage previously failed and is being retried.

The orchestrator fingerprints the case snapshot and checks it again before
execution. If the case changed after planning, the stale plan is not run.
Identical actions against an unchanged snapshot are blocked.

### D. Act: execute through a bounded backend operation

`ActionExecutor` dispatches the selected action to the corresponding service.
For planner-requested reprocessing, the reason is selected from context:
recoverable failure, newly uploaded document, manual correction, or stale
downstream results. The deterministic technical retry uses a distinct
`automatic_retry` reason and does not change the database schema.

Reprocessing fingerprints stage inputs. If inputs match a previous successful
run and this is not a failed-stage retry, EyeVen reports
`skipped_unchanged` instead of needlessly rerunning the stage. When a stage
does run successfully, the orchestrator runs its affected downstream stages.

A targeted registry result is recorded and passed back into the next planner
iteration. If it does not corroborate the identifier, EyeVen will not repeat
the same lookup; it will request clarification only when eligible or use manual
review otherwise.

### E. Observe again and stop safely

After execution, EyeVen reloads the snapshot, compares it with the prior
snapshot, and records whether progress occurred. The action and a sanitized
result are added to the in-memory prior-action history and execution trace.
If the loop continues, the next planner call receives that updated state and
action history.

The loop can stop because:

- The planner chose `finish`.
- A vendor follow-up draft was created.
- PIC upload is needed.
- A human review is needed.
- The case reached a terminal state.
- A planner or action failed.
- The plan was stale, duplicated, or made no meaningful progress.
- The action limit was reached.

Planner, validation, and execution failures fail closed to manual review rather
than being treated as successful actions. If the maximum is reached with
unresolved issues, the case is routed to manual review, except when it is
waiting for vendor follow-up.

## 4. Follow-up and vendor responses

Follow-up means a request for information that is not present in reviewed case
materials. EyeVen can create a draft email, but **does not send it
automatically** and does not monitor the vendor's inbox or detect replies.

When a reply arrives, the PIC can:

1. Enter the reply in the relevant field using **Edit** / **Enter Value**.
2. Mark it as supplied by the vendor when appropriate.
3. Optionally upload and select a supporting document if the reply included
   evidence.
4. Choose **Save and Reprocess**.

That action records the value as a PIC-entered correction and reprocesses
verification followed by policy evaluation and routing. A populated PIC-entered
value should not be requested again solely because its status remains
low-confidence. However, the low-confidence status still matters to the safety
gate: EyeVen may require manual review instead of automatically approving the
case.

Authorized signature and company chop are optional in the current workflow;
their absence alone must not create follow-up.

## 5. Reprocessing versus a new full run

Use reprocessing when information or evidence changed, a field was corrected,
or a stage needs a retry. The reprocessing orchestrator starts at the selected
stage and refreshes downstream stages. Unchanged successful inputs can be
skipped.

Use **Run Workflow** after uploading a document that EyeVen still needs to
extract and structure. Uploading a recovery document does not, by itself, start
processing.

## 6. Auditability and UI

The workflow response contains a stage trace and, when enabled, an agentic
action trace. The agentic trace includes the chosen action, reason, targets,
before/after states, outcome, whether progress was made, and sanitized action
result. Persisted verification, policy, routing, email-draft, and reprocessing
records are available through case panels and the audit trail.

The trace is an explanation of what the current workflow request did; it is
not a substitute for the persisted historical audit records.

## 7. Main implementation locations

- `backend/app/orchestrators/workflow_orchestrator.py` — fixed stage order,
  routing, safety-gate application, and bounded planner loop.
- `backend/app/orchestrators/extraction_orchestrator.py` — parser/OCR and ZIP
  processing.
- `backend/app/services/form_service.py` — canonical form structuring and
  field persistence.
- `backend/app/services/context_assembler.py` — prompt contexts for
  verification, policy, routing, and follow-up.
- `backend/app/services/next_action_planner.py` — case snapshot, plan
  validation, and evidence-based fallback.
- `backend/app/prompts/next_action_planner.py` — planner instructions.
- `backend/app/services/action_executor.py` — constrained action dispatch.
- `backend/app/orchestrators/reprocessing_orchestrator.py` — targeted
  reprocessing, fingerprints, and downstream refresh.
- `backend/app/services/safety_gate.py` — deterministic approval and
  follow-up eligibility rules.
- `backend/app/validators/agent_schemas.py` — structured-output schemas.
