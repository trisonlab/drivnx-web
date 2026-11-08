# 0003 — Contract internal schema: DrivnX-native rules, ODCS 3.2.0 as an export format

- **Status:** Accepted 2026-11-01
- **Date:** 2026-11-01
- **Source:** Direction doc §3 (artifacts, trust model), §9 (ODCS as "export vocabulary (small subset)"; visual editor),
  §11/§12 (open decisions, domain model, trust-state rules); Review Notes #5 and #7; ADR 0002 (revisions, tombstones,
  exports); [ODCS v3.2.0 docs](https://bitol-io.github.io/open-data-contract-standard/v3.2.0/) and its JSON Schema
  (`schema/odcs-json-schema-v3.2.0.json` at tag `v3.2.0`, sha256 `edb41f33…2b0d93`)

## Context

A Data Contract holds a dataset's purpose and limitations, plus rules of seven types: required, type, unique,
accepted values, range, identifier and relationship (§3, §12). The question is whether DrivnX stores contracts in its
own shape and exports ODCS, or stores ODCS documents directly.

What matters for this decision:

- **Each rule has its own history.** ADR 0002 keeps revisions of each rule as new rows. Provenance events point at those
  revisions. Demoting one Defined item must take exactly one rule out of the contract (§12).
- **The editor works rule by rule.** Users promote one confirmed fact into one rule through a visual editor, not raw
  YAML (§8, §9).
- **ODCS doesn't model a rule as a thing.** `required`, `unique` and `primaryKey` are booleans on a property, and
  `enum` and `logicalTypeOptions` are attributes of it. None of them has an id, a trust state, a history or an author.
- **The standard moves.** The team's source documents already disagreed between 3.0.1 and 3.2. `enum` only arrived in
  3.2.0.
- **Scope:** the §9 plan is a small subset used as export vocabulary. Validation against data, SLAs and import are all
  out of scope for Phase 1 (§2, Review Notes #5, ADR 0002).

## Decision

**1. Contracts are stored in a DrivnX-native shape. ODCS exists only in exported files.** Nothing in the workspace is
an ODCS document, and exports are never read back (ADR 0002).

**2. A rule is a row with a type, its target fields and typed parameters.** This is the shape, not final DDL:

```text
contract               id, dataset_id, current_version
contract_revision      contract_id, version, purpose, limitations
contract_rule          id, contract_id, rule_type
contract_rule_field    rule_id, role (subject | target), position, field_id
contract_rule_revision rule_id, revision, contract_version, state, is_tombstone, params (JSON)
```

- `params` is checked by a Pydantic discriminated union on `rule_type`, so `/api/contracts` and the generated TypeScript
  types share one definition (§12).
- A rule's target fields never change. Pointing a rule at other fields means removing it and creating a new one.
- **A rule can exist before it is part of the contract.** A ContractRule row's state is Inferred, Confirmed or Defined,
  and only Defined rules belong to the contract. An AI suggestion starts as Inferred and goes through normal review. A
  rule written in the Contract Editor goes New → Confirmed → Defined in one transaction, with both ProvenanceEvents.
  Demoting a Defined rule writes a revision in state Confirmed, which takes it out of the contract and keeps it as a
  candidate. A tombstone is for deleting a rule outright.
- **`Contract` has no `state` field.** Rules carry their own states, and a single contract-level state has no defined
  meaning. If AI comes to draft purpose or limitations, that text needs its own Inferred state, added when M8 needs it.
- Rule and field ids must be valid ODCS `StableId`s (no whitespace, `.`, `#`, `/`, `\`, `@`, `!`, `%`, `&` or `^`).
  UUIDs qualify. A field keeps its id across re-profiling while its dataset and column name stay the same.

**3. One contract covers one dataset, and every change to what it says creates a new contract version.** A rule becoming
Defined, a Defined rule being edited, demoted or deleted, and an edit to purpose or limitations each increment
`current_version` in the same transaction (ADR 0002 point 3). Those revisions record that version in `contract_version`.
Revisions of candidate rules leave it empty and create no version. The contract at version N is, for each rule, its
latest revision with a `contract_version` at or below N, if that revision is Defined and not a tombstone. Old versions can
therefore be re-exported without a snapshot table.

**4. Rules that would compete for one ODCS slot can't both be Defined.** Candidates can overlap, such as two AI
suggestions for one field. The domain layer rejects promoting a second one inside the `BEGIN IMMEDIATE` transaction, so
the exporter never has to merge or choose between rules.

| Rule type | Target | Parameters | At most one Defined rule per |
|---|---|---|---|
| required | 1 field | none | field |
| type | 1 field | `logical_type` | field |
| unique | 1+ fields | none | identical field set |
| accepted values | 1 field | list of unique scalars, at least one | field |
| range | 1 field | `logical_type`, inclusive `minimum` and/or `maximum` | field |
| identifier | 1+ fields, ordered | none | contract |
| relationship | subject and target fields, same count; targets all in one dataset | none | identical subject and target |

A range's `logical_type` must be `number`, `integer`, `date`, `timestamp` or `time`, and it must match the field's type
rule if there is one. ODCS rejects `minimum`/`maximum` on a property with no such `logicalType`. Storing the type on the
range rule means a user can set a range without also having to define a type rule.

**5. Export is a pure function from one contract version to one ODCS v3.2.0 document, in YAML or JSON.**

| DrivnX | ODCS |
|---|---|
| Contract id, version | `id`, `version` (the version number as a string, with no semantic-version meaning) |
| Purpose, limitations | `description.purpose`, `description.limitations` |
| Dataset | One `schema[]` object, with `id` and `name` |
| required | Property `required: true` |
| type | Property `logicalType` |
| unique, 1 field | Property `unique: true` |
| unique, 2+ fields | Schema-level `quality`: `metric: duplicateValues`, `mustBe: 0`, `arguments.properties` |
| accepted values | Property `enum: [{value: …}]` |
| range | Property `logicalType` and `logicalTypeOptions.minimum` / `maximum` |
| identifier | `primaryKey: true`, `primaryKeyPosition` on each field |
| relationship | Schema-level `relationships[]` with `id`, `type: foreignKey`, `from`, `to` |

The export rules:

- **Only rules in the contract (point 3) are exported, and only they make an expectation.** A property appears only if it
  has at least one rule, or is a relationship target (below). `logicalType` appears only from a type or range rule.
  Observed types and Inferred suggestions never go in, so an export can't present them as Defined.
- **`status` is left out.** It means lifecycle in ODCS (`draft`, `active`) and Phase 1 has no such lifecycle. DrivnX's
  trust state is a different idea.
- **Relationships always use fully qualified, id-based references, at schema level.** Shorthand (`customers.id`) fails
  validation on a column name with a space, and silently misreads a column name containing a dot. CSV headers can
  contain both.
- **A relationship's target dataset is included in the same document as a marked stub:** a `schema[]` object with only
  ids and names for the target fields, plus the custom property `drivnxReferenceOnly: true`. ODCS's other option,
  referencing another file, only accepts `.yaml`/`.yml` targets, so it fails for a JSON export and would require the
  target dataset to have its own contract. A consumer that ignores the marker may read the stub as being part of the
  contract, which the **Consequences** section accepts.
- **Provenance goes in `customProperties` with `vendor: drivnx`.** The definition of done requires exports to be
  traceable to their evidence and author (§2), and ODCS has no field for this. Property-level rules are listed in
  `drivnxRules` on the property. Schema-level rules carry theirs in `drivnxRule` on their own quality or relationship
  entry. Each record holds the rule id, rule type, revision, author, reviewer, timestamp and evidence references.
- **Provenance exports point at evidence; they don't copy it.** Previous values and AI prompt context are left out, as
  they may contain values taken from source data.

**6. Conformance is proved in tests, not at runtime.** The exporter builds plain dicts and writes them with PyYAML's
`safe_dump` or `json`. The JSON Schema from tag `v3.2.0` (Apache-2.0) is vendored under `tests/golden/`, and the tests
validate exports against it with `jsonschema` as a dev-only dependency. Moving to a newer ODCS version means changing
the exporter and re-running its tests, with no database migration.

## Alternatives considered

| Option | Why not |
|---|---|
| Store ODCS documents as the internal model | A document has no place for per-rule revisions, tombstones, state or provenance. DrivnX would have to diff whole documents to work out which rule changed, or hide all of that in `customProperties`. It also ties the database to the standard's changes, and works against §9's "small subset". |
| Native tables that copy ODCS field names and nesting | Looks cheaper to export, but ties the schema to ODCS 3.2.0 and still needs a rule entity on top. The mapping table above is short enough that this saves very little. |
| Express every rule as an ODCS `quality` entry | Every rule would then have an `id` and `customProperties`, but ODCS's metric library has no range metric, so range would need `sql` or `custom`. It also implies executable checks with thresholds, and Phase 1 has no validation. |
| Use the `open-data-contract-standard` PyPI models (3.2.0) in the exporter | Not needed for a small subset: a dict plus a schema test proves the same thing without another runtime dependency. Reconsider if the subset grows much. Not evaluated beyond its package metadata. |
| Reference relationship targets in other files | See the export rules: this fails schema validation for JSON targets and needs a contract for every target dataset. |

## Consequences

- **Easier:**
  - Promote, edit and demote each touch one rule, and ADR 0002's revisions, tombstones and provenance apply directly.
  - The UI and API work with seven typed rules, not the whole of ODCS.
  - Upgrading ODCS doesn't touch stored data.
  - Adding a rule type is additive.
- **Harder or accepted:**
  - The exporter's mapping has to be kept and tested.
  - Export loses information: ODCS consumers see the expectations, and DrivnX's trust detail only as custom properties.
  - No round trip. A contract edited outside DrivnX can't come back in Phase 1.
  - A consumer can't tell a stub from a real schema object unless it reads `drivnxReferenceOnly`.
  - `identifier` maps to ODCS `primaryKey`, which is narrower than "this identifies a customer": a business identifier
    needn't be the table's primary key. It's the closest ODCS field, and the rule's `ruleType` in provenance keeps the
    original meaning. An identifier rule doesn't add `required` or `unique`. Those stay separate rules (§3).
  - `params` history is append-only, so changes to a parameter's shape must stay backward compatible or come with a
    migration that the append-only tests explicitly allow.
- **Accepted values can be copied from source data.** Enum values exported here may include personal data, and so fall
  under the source-derived values finding in ADR 0002. That rule, due before M3, has to cover them too.
  **[ADR 0004](0004-profile-values-and-caching.md) covers profiling only, so this one is now due with the contract
  slice (2026-11-05).**
- **Finding to resolve, not decided here: what is `packages/contracts`?** §10 doesn't define it. It could be this
  exporter or API contracts. The domain rule types belong in `packages/domain`, and the exporter goes with the Export
  service.
- **Tests that ship with M7 (the rule types and invariants themselves ship with M1):**
  - A contract using all seven rule types exports to YAML and JSON, and both validate against the vendored schema.
  - Column names with spaces and dots produce valid relationships that resolve to the right fields.
  - Observed types and Inferred or Confirmed rules don't appear in an export.
  - A removed rule disappears from the new version, and re-exporting the older version still includes it.
  - Promoting a second rule into an occupied slot (point 4) is rejected, and nothing is written.
  - Changing a candidate rule creates no contract version. A Defined rule being edited, demoted or deleted creates one.
  - A demoted rule leaves the contract and remains as Confirmed. A rule written in the editor records both events.
  - Exporting the same contract version twice gives identical output.
  - No previous values or prompt context appear in an export.
- **Revisit when:** Phase 2 adds validation or import, or users need the exported files to work with a specific ODCS
  tool that this subset doesn't satisfy.
