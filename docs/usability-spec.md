# Phase 3: presentation and localization

The structural and content engines (`csv_validator.py` and `column_rules.py`) own validation decisions; their current limits and NUL handling are documented in [validation-spec.md](validation-spec.md). `services/issue_presentation.py` adapts their immutable issues to UI and download text. It makes no decisions about validity, counts, limits, ordering, uniqueness or source data.

## Columns and issue types

Read the header with the same encoding, separator, strict CSV reader, resource guard and BOM behavior. Reject NUL anywhere in the source before reading headers. Preserve the source bytes and store a header snapshot alongside the result. Display a stripped header as `2 — internal_reference`. Empty names appear as `2 — Unnamed column`; unresolved numeric positions as `2 — Unknown column`; file-wide and row-wide errors as `Column not identified`. Do not infer a column from a row-width mismatch or encoding failure. Missing configured columns remain identified in their message, without inventing a position. Physical start/end lines remain unchanged, including multiline records.

Older transient results can resolve names from their existing upload without rewriting it; this fallback is cached per wizard. Existing downloaded report binaries are preserved until the user validates again.

Standard Odoo badges distinguish **Structure** and **Content** by text and color. Profile configuration/resource issues belong to Content. Unknown trusted addon codes use Other and keep their original messages. Codes remain visible, raw diagnostics are an optional list column and appear in the issue detail form.

## Readable messages

Use short translated messages plus a separate Restrictions field. Numeric errors retain the optional sign, ASCII digits, maximum length, decimal separator and unsupported formats. Syntax errors retain the parser's actual field-size limit. Encoding and NUL errors retain zero-based byte offsets. NUL, column-limit and header-size issues are structural issues. Record/resource limits and minimum thresholds retain their values. Unknown message formats fall back to the original diagnostic, rather than losing details.

English literals are extracted through Odoo's translation API. The generated `i18n/odoo_csv_validator.pot` covers all module terms, and `i18n/ca.po` translates that catalog with checked formatting placeholders. A future `i18n/es.po` can be derived from the POT without code changes. No Spanish translation is claimed yet.

Live messages, unknown-column labels and selection labels use the current Odoo language. Generated report text is a snapshot in the language used when validating; changing language does not rewrite an existing binary download. Revalidate to generate a download in another language. Technical codes, raw diagnostics and machine-readable CSV header/type identifiers remain stable English values.

## CSV downloads

Odoo downloads now use ten columns, extending the first five diagnostic columns:

`code,start_line,end_line,column,message,column_name,issue_type,restrictions,technical_message,first_occurrence_line`

The `message` column uses the same readable message as the UI. `technical_message` preserves the original engine diagnostic. The summary row has the same width and retains the original completion/truncation/totals metadata. Empty-result and partial-result reports remain downloadable. All textual data, including header names, is escaped against spreadsheet formula prefixes; CSV quoting handles commas, quotes and newlines. UTF-8 BOM is preserved.

The low-level engine export function is unchanged. Consumers of Odoo's downloaded CSV should accept the additional columns by their stable header names.

The presentation layer adds no external dependencies and never corrects or imports source data.
