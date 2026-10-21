# Phase 2: implemented column rules

The original proposal remains a design record. This file describes the implemented behavior.

## Data and authorization

`csv.validation.profile` stores a name, immutable owner, whitespace policy, decimal separator and column rules. `csv.validation.profile.rule` stores one normalized header name, required/unique flags, a text/integer/decimal type and an optional minimum string. SQL uniqueness protects `(profile_id, column_name)` after trimming header names. The pure Python `ColumnRule` is an immutable configuration snapshot; `ColumnValidator` allocates fresh state for each analysis.

The explicit **CSV Validator: Manage own validation profiles** group implies internal-user access. Only the initial administrator is granted it automatically. Global record rules restrict profiles and lines to their owner, including administrators using ordinary access. Server-side checks prevent foreign-profile assignment and owner changes. Superuser operation follows Odoo's normal exception to record rules. No sharing or executable user configuration is supported.

Profiles are permanent until their owner deletes them. CSV uploads, results and applied configuration snapshots remain transient. Profile deletion clears the selection, while keeping earlier report snapshots. Subsequent validation with no selection is structural only. Selecting a different profile invalidates the earlier result; profile edits affect the next execution, not an existing report. Revoked profile permissions cause an access error, never a silent structural-only fallback.

## Semantics

- Header matching strips outer whitespace and is case-sensitive, like structural duplicate-header detection. Empty or repeated rule headers are rejected. Maximum 256 characters per configured header and 100 configured columns.
- Missing or duplicated target headers produce configuration issues on the header record and mark analysis incomplete. Column 0 means the target column cannot be located. Rules on other unambiguous columns can still run.
- By default trim outer whitespace for value comparison. With trimming disabled, whitespace is significant, including for requiredness. This never modifies the input.
- Requiredness rejects empty comparison values. Optional empty cells skip numeric, minimum and uniqueness checks. Physical blank lines retain phase 1 behavior and are skipped.
- Text uniqueness is case-sensitive, preserving leading zeroes. Numeric uniqueness compares exact Decimal values: in decimal columns `1`, `1.0` and `+01.00` coincide; zero and negative zero coincide. Each repeated value identifies the first physical line of the first record.
- Integers accept an optional `+` or `-` and ASCII digits only. Decimal columns accept integers or fractional numbers using the selected separator, including `.5` (or `,5`). Trailing decimal separators, scientific notation, NaN, infinities, thousands separators, underscores and non-ASCII digits are rejected. Maximum numeric input length: 1,024 characters.
- Minimums use exact finite Decimal strings with a point, regardless of CSV settings, and compare inclusively. A numeric type is mandatory. An integer column can have a fractional threshold. Numeric parse failures produce only `INVALID_NUMBER`; otherwise a below-minimum value can also participate in uniqueness.
- Content checks only run on nonblank rows matching the header width. Existing trusted callable extension hooks retain their previous behavior. Without a profile, only structural checks run. The Odoo download uses the ten-column presentation format documented in [usability-spec.md](usability-spec.md); the low-level engine exporter remains separate.

## Bounds and reports

Limits include 10 MiB files, 1,000 columns per record, a 64 KiB header, 100,000 data records, 1,000 detailed issues and Python's existing CSV field-size limit. NUL input is rejected before decoding or parsing. See [validation-spec.md](validation-spec.md) for byte accounting and persistence limits. Count additional detected issues even when details are truncated. Each profile allows at most 20 unique columns. Each execution stores at most 100,000 total unique keys with a conservative 32 MiB estimated key/entry storage budget; the process memory limit remains necessary. No collision-prone digest-only uniqueness. Exhaustion emits `RULE_RESOURCE_LIMIT`, stops analysis and marks the count partial, even if the issue-detail limit has already been reached.

Content issue codes: `REQUIRED`, `DUPLICATE_VALUE`, `INVALID_NUMBER`, `BELOW_MINIMUM`, `RULE_COLUMN_MISSING`, `RULE_CONFIGURATION`, `RULE_RESOURCE_LIMIT`. All share the existing physical line range, column and explanation. `first_line` supplies duplicate metadata in Odoo. In the low-level `export_report()` API, details containing a duplicate add `first_occurrence_line`; structural-only exports have five columns. The Odoo UI instead uses `export_presented_report()`, always with ten columns including `first_occurrence_line`, as documented in [usability-spec.md](usability-spec.md). Summary rows always match the report's column count. Raw cell values are not included in content error messages. Formula-prefix protection continues to apply to all exported textual details.

There is no automatic correction, source modification, import, product-specific validation or user-supplied expression execution.
