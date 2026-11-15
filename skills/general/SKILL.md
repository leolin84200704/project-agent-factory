# General Problem Solving Skill

> Universal engineering triage and behavior-first implementation skill across domains

---

## Metadata
```yaml
name: general
type: core
agent: general
priority: medium
```

---

## Purpose

Handle cross-cutting engineering issues that do not belong to a single domain skill:
- Bug reports
- Payload and file size limits
- Performance bottlenecks
- Configuration drift
- Partner / webhook / API integration failures

---

## Problem-Solving Framework

### Step 1: Understand the Problem
- What is the exact error/issue?
- What are the symptoms?
- What is the expected behavior?
- What is the actual behavior?

### Step 2: Gather Context
- When did this start happening?
- What changed recently?
- Who is affected?
- What is the scope?

### Step 3: Investigate
- Check logs/error messages
- Review relevant code
- Check configuration
- Reproduce if possible

### Step 4: Root Cause Analysis
- Identify the root cause
- Understand why it happened
- Check for similar issues

### Step 5: Solution Design
- Propose solution options
- Evaluate trade-offs
- Choose best approach

### Step 6: Implementation
- Make code changes
- Test thoroughly
- Document changes

---

## Design for Testability

LLM-generated code drifts toward the most common shape in training data — and
that shape mixes I/O into logic (network calls inside transactions, `Date.now()`
inside domain functions, clients constructed inline). Untestable code is the
default outcome, not the exception. Counter it with these named patterns
(mainstream idioms — prefer them because the model already writes them well):

- **Functional core / imperative shell** — pure functions for decisions and
  transformations (no I/O, no clock, no randomness); a thin shell owns all I/O.
  The core is what gets exhaustive cheap tests; the shell stays thin enough to
  review by eye.
- **Parse, don't validate** — at every boundary, parse input into a type that
  cannot represent illegal states, instead of checking and passing the raw
  value along.
- **Inject the seams** — clock, ids, external clients, env come in as
  parameters/constructor args, never reached for globally from inside logic.
  If the test skeleton can't be written without a real DB or network, a seam
  is missing — fix the design, not the test.
- **Behavior-level assertions** — test what the caller observes, not internal
  call sequences; a test that mocks the shared path it is supposed to exercise
  is a false green (see ENGINEERING-LESSONS "Sync sibling encodings").

Sequencing rule (mirrors WORK-LOOP Step 5): write the test skeleton before the
implementation. The skeleton failing to materialize is the earliest, cheapest
signal that the design is untestable.

---

## ⚠️ CRITICAL EXECUTION RULES

### 1. READ CODE FIRST, WRITE SECOND

**Before writing any solution**:
```bash
# Find related service files
find src -name "*service*.ts" | grep -i "{domain_keyword}"

# Read the service to understand the correct flow
Read("src/modules/{domain}/services/{service}.ts")

# Look for existing methods
grep -r "function.*{action}" src/
```

**Example**: For "resend delivery", first read:
- `src/modules/delivery/services/payload-generation.service.ts`
- Look for `buildDeliveryPayload`, `fetchOrderSnapshot`, etc.

### 2. USE REAL DATA, NEVER FAKE DATA

**FORBIDDEN**:
- Generating fake payloads with synthetic customer records in live operations
- Hardcoding placeholder IDs like `"TEST_ACCOUNT"`
- Fabricating delivery receipts

**REQUIRED**:
- Call upstream RPC/HTTP services to fetch authoritative records
- Query the database for existing persisted records
- Download real artifacts from storage APIs
- Use actual tenant/order IDs from the ticket

### 3. ALWAYS VERIFY OUTPUT

**After every operation**:
```bash
# Database operations - verify record created/updated
echo "SELECT * FROM {table} WHERE {id} = '{value}';"

# Remote uploads - verify artifact exists on destination
# 1. List remote directory or object prefix
# 2. Locate uploaded artifact
# 3. Compare byte sizes / checksums
```

**If verification fails**: STOP, inspect root cause, repair or revert cleanly

### 4. QUERY CONFIGURATION, DON'T HARDCODE

**WRONG**:
```typescript
const uploadPath = '/deliveries/';  // Hardcoded!
```

**RIGHT**:
```typescript
const integration = await prisma.partnerIntegration.findFirst({
  where: { tenant_id: tenantId, partner_code: partnerCode },
});
const uploadPath = integration.remote_delivery_path;
```

### 5. UNDERSTAND "RESEND" vs "CREATE NEW"

| Term | Meaning |
|------|---------|
| **Resend** | Find EXISTING artifact, re-deliver to SAME destination |
| **Create New** | Generate NEW artifact from scratch (rare) |

**Default assumption**: When a ticket says "resend", locate the existing persisted artifact first.

---

## Example: Outbound Payload Size Limit

### Problem
- Partner ingress limit: 15MB
- Outbound artifact size: 28MB
- Result: Partner endpoint rejected delivery

### Investigation
```bash
# Check compression configuration
grep -r "ENABLE_ARTIFACT_COMPRESSION" .env deploy/

# Check compression threshold
grep -r "compressionThreshold" src/

# Check payload size validation
grep -r "maxPayloadBytes" src/
```

### Findings
1. Artifact compression disabled by default
2. Compression threshold mismatched against partner ingress cap
3. No pre-flight size validation before outbound transfer

### Solution
1. Enable artifact compression for oversized payloads
2. Add pre-flight byte-size validation with actionable 4xx/alerting
3. Configure partner-specific size threshold below the 15MB ceiling

---

## Common Patterns

### File Size Issues
- Check compression settings
- Validate before sending
- Add size limits per vendor
- Implement aggressive compression for large files

### Performance Issues
- Check database queries
- Look for N+1 problems
- Check for missing indexes
- Review caching strategy

### Configuration Issues
- Check environment variables
- Verify k8s configmaps
- Check for typos
- Validate format

### Integration Issues
- Check API endpoints
- Verify authentication
- Check rate limits
- Review error handling

---

## Questions to Ask

When analyzing any problem:

1. **What exactly is broken?** (Be specific)
2. **When did it start?** (Timeline)
3. **What changed?** (Recent changes)
4. **What are the symptoms?** (Observable behavior)
5. **What are the logs saying?** (Error messages)
6. **Can we reproduce it?** (Testing)

---

*Last Updated: 2026-04-08*
