# Structural validation and result storage (18.0.2.2.0)

- The first logical record is the mandatory header; a blank first line is an empty header, not skipped.
- Compare stripped headers case-sensitively. Do not mutate headers or source bytes.
- UTF-8 with optional BOM by default; Windows-1252 only by explicit selection. No silent fallback.
- Explicit comma, semicolon or tab separator; no ambiguous automatic detection.
- Count all parsed nonblank data records, including wrong-width records. Empty physical records are counted separately; `,,` is data.
- Incidences refer to physical start/end lines, including the header. Encoding errors report a zero-based byte offset and physical line; no complete record count is possible.
- Stop on decoding/syntax errors and after 100,000 records, and mark analysis incomplete. A header-only file has zero records and valid structure.
- Maximum decoded upload 10 MiB, maximum detailed incidences 1,000. Count further issues without retaining details. Limits cannot be increased by RPC parameters.
- Maximum 1,000 columns per logical record and a 64 KiB header in the selected encoding, excluding the BOM and terminating newline but including quotes and embedded newlines. A constant-state guard checks each physical line before passing it to the CSV parser, respecting quoted delimiters, doubled quotes and multiline fields. Exceeding a limit stops analysis with `COLUMN_LIMIT` or `HEADER_SIZE_LIMIT` and an incomplete result. Header presentation uses the same guard.
- Python csv's field-size limit also applies, with a controlled incomplete result. The engine does not alter process-wide parser settings.
- Export UTF-8 BOM CSV with bounded details and a SUMMARY row containing totals, completion and truncation. Odoo downloads always use the ten-column layout in [usability-spec.md](usability-spec.md); the low-level engine exporter has its own five/six-column diagnostic layout. Spreadsheet formula prefixes in text are neutralized.
- No model-specific checks, source modification, automatic correction or import.
- Trusted addon extensions override `_validation_rules()` and supply pure callables `(headers, row, start_line, end_line)` yielding `Issue` instances. No user-supplied executable code.
- Internal users only. Transient records isolate users; no sudo or public upload/download controller. Binary fields use standard Odoo authenticated downloads and `attachment=False`.
- Uploads/reports are retained temporarily, subject to Odoo transient vacuum; the one-hour age is a cleanup threshold, not an exact deletion deadline. Backups can retain data longer.
- CSV syntax checks follow Python `csv.reader(strict=True)`, which can accept quotes inside unquoted fields; no universal CSV conformance is claimed.
- Expected failures are reported; unexpected failures expose only a generic message and log exception type and stack frames without exception text or file content.

## Result integrity and persistence limits

- Normal users can create/write only upload inputs (`filename`, `file_data`, `encoding`, `delimiter`, `profile_id`). State, counters, snapshots, reports and issue relations are server-managed; attempts to supply them in create/write are rejected. Non-input `default_*` context values are removed before creation.
- Issue creation, modification, reassignment and deletion are blocked for normal callers. Validation uses private, RPC-inaccessible persistence methods with the caller's existing ACLs and record rules; no context flag grants access and the process never uses `sudo()`.
- Changing validation inputs still clears results, revalidation replaces issues, and deleting an assistant still removes its dependent issues. Result replacement is atomic: persistence-limit failures preserve the previous result.
- Explicit superuser mode retains maintenance access. Membership of the Settings/administration group alone does not bypass the guards. Persistence limits also apply in superuser mode.
- Storage budgets: 10 MiB decoded report; 512 KiB UTF-8 serialized JSON per snapshot; 4,096 UTF-8 bytes for the applied profile name; 255 UTF-8 bytes for the report filename; 1,000 issues per assistant; 65,536 UTF-8 bytes per technical message; 256 UTF-8 bytes per issue code. Counters/line numbers must fit nonnegative PostgreSQL integers, and issue columns cannot exceed 1,000. Oversized results raise a controlled validation error rather than being truncated or partially persisted.

## NUL characters and persistence failures

- Before decoding or parsing, reject the first NUL byte with `NUL_CHARACTER`, its zero-based source byte offset and one-based physical line (CR, LF and CRLF supported). Analysis is incomplete with zero processed records. The message suggests checking encoding, since UTF-16 is not supported.
- Header presentation also rejects a file containing NUL, including a NUL in data after the header. No CSV text containing NUL is copied into a snapshot, issue or report. The original upload remains unchanged in its binary/base64 field.
- Result payloads are checked recursively for NUL before ORM persistence, including JSON keys and nested values. Unexpected persistence failures are converted to a generic user error only after rollback to the result savepoint; previous results and unrelated work remain intact. Expected user/access errors and PostgreSQL concurrency errors retain their normal Odoo behavior.
