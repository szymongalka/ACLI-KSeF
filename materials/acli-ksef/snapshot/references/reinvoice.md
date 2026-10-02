# Refaktura: source to verified preview

Use this procedure yourself when acting as the selected agent, including DeepSeek v4.1 Flash. A stronger model is not a required step. Read current user decisions from memory; model comparisons are optional, never the default. Use the existing ASEF CLI/library, not a replacement renderer, database or invoice generator.

## Delivery budget

For draft requests with a delivery-time target, start the clock at receipt of the request, not at the first CLI call. Keep one selected agent and one draft on the routine path; batch independent reads, reuse established decisions and version-matched evidence, and avoid optional comparisons, duplicate renders or broad history reads before delivery. Do not repeat a registry lookup solely for an unchanged buyer when its recorded evidence is sufficient for the current task; recheck stale, missing or contradictory evidence. Preserve source/freshness, numbering, arithmetic, FA(3), hash and preview checks. Deliver the verified attachment before extended analysis or documentation maintenance, then finish that maintenance in the same task. If missing facts or a dependency block readiness, report the specific blocker within the target window; this is not successful draft delivery. Record request/receipt timestamps and elapsed seconds in the manifest; report a missed target honestly and never claim measured performance from a procedure change alone.

## 1. Find and read the source

- Run `asef --json invoice list --category received --nip NIP --env ENV --limit 1000`; filter returned fields locally and emit only matching ID, number, seller, date and amount. Corrected documents are in the corrections category/table, not ordinary invoice tables.
- Names alone may miss a repair invoice. Search text of candidate XMLs for source item names/model (e.g. repair/naprawa/transport) and read the full matched XML. The local SQLite `documents` table contains XML; for large scans use Python sqlite3 read-only URI `file:ACTUAL_DB_PATH?mode=ro`, parameterized queries and an XML parser with entity/network resolution disabled. Never dump the whole database.
- Export exact evidence with `asef --json invoice xml SOURCE_ID --out SOURCE.xml`. Record source ID, invoice number/date, original KSeF number and hash, line items, VAT and totals. Verify rather than infer that it is the intended invoice.
- Obtain the refactoring seller from the selected profile/current firm evidence, not the source supplier. Reuse sufficient, current buyer evidence for the unchanged buyer; otherwise check through `asef --json registry check BUYER_NIP`; `--krs` is available when a verified KRS number is known. Record source and time. MF not-found is not proof of invalid NIP/nonexistence; use an authoritative own site when appropriate, distinguish registry status from name/address evidence, and flag unresolved contradictions.

Done: a small factual input packet identifies the original supplier, reinvoice seller and buyer separately.

## 2. Produce concise content

Preserve the agreed scope: full or partial cost, markup and VAT. Do not deduce tax treatment from “refaktura” alone. Do not import the original supplier's bank account, due date, paid status or service date by analogy. Source issue date is not evidence of the refactored service date.

For a simple agreed single-service/full-cost draft use one line with quantity 1, a short unit, exact decimal price and verified rate. Prefer <=100 characters: refactored service, identifying device/model, short source invoice number. Keep repeated sums, tax calculations, full supplier identity, source KSeF ID and project commentary out of the line description. Short wording does not exclude parts or transport from the agreed cost. Keep full provenance and unresolved facts in the project report. The current JSON/preview path has no supported `notes` field.

Payload required keys: `number, issue_date, currency, seller, buyer, lines`. Party keys: `nip,name,address1,address2` (address2 may be absent). Line keys: `description,quantity,unit,unit_price_net,vat_rate`; use decimal strings for prices/quantity and integer VAT 23/8/5, never `"23%"`. Actual supported optional header keys are `issue_place,sale_date,payment_due_date,payment_method,bank_account`; include only evidenced/explicitly supplied values.

Write a small EXPECTED JSON from facts, independent of model output:
- Required: `seller_nip,buyer_nip,currency,net_amount,vat_amount,gross_amount`.
- For this task also set `source_number,max_description_chars,description_terms,line_count` as applicable.
- Set `number` and `issue_date` when agreed; use exact `seller` and `buyer` objects to check addresses.
- Map any allowed optional header values in `optional_fields`; default is none. Do not move unknown data into the expectation merely to make validation pass.

Run the bundled checker with the existing ASEF Python. It builds and validates XML without touching the database or network. Exit 0/`ok:true` is required. Repair a format error locally; ask only for missing facts or required authority. Use `--xml-out DRAFT.xml` to save validated bytes once; an existing file is not overwritten.

Done: the payload matches independent expectations, short description and known data; technical validation is not tax/legal clearance.

## 3. Number and persist one version

List issued documents in the selected profile and inspect the relevant series, not the numerically largest unrelated series. Distinguish annual progression from monthly resetting by observing more than one month. Check the intended number across issued records/drafts. A local missing number is not proof that another accounting system has not allocated it. A user-approved proposal is usable for the authorized draft; do not silently choose a different series.

For a new draft: `asef --json invoice create --file PAYLOAD.json --nip NIP --env ENV`. For an existing draft: preserve old XML and preview, optionally create a private `asef --json db backup BACKUP.sqlite3`, then `asef --json invoice revise DOCUMENT_ID DRAFT.xml`. Inspect status/hash before revision to avoid overwriting a newer edit. Revising retains ID, resets approval and creates a new hash. For a number-only revision change only Fa/P_2 in the existing XML and validate it; preserve the rest of the content. Do not create a new database record for every wording change.

Model names and round labels belong in filenames/report, not the final accounting number. Store selected vs historical variants in the current report; do not delete unselected records unless asked.

Done: exact current bytes are in the selected record and the document remains draft until separately approved.

## 4. Verify and deliver

Run `asef --json invoice show DOCUMENT_ID`, `invoice xml DOCUMENT_ID --out OUTPUT.xml`, and `invoice preview DOCUMENT_ID --format pdf --theme light --out OUTPUT.pdf` (HTML optional). Compare expected amounts/parties/reference with XML and PDF; hash the exported bytes and compare stored SHA-256. Check PDF page count and every page visually if supported. `pdftotext -layout` may interleave other table columns into a wrapped description; use `-raw` for content-order assertions, then inspect the page.

Keep a manifest with ID, number, hash, source, amount, model provenance, file paths, status, actual approvals, pending business fields and delivery receipt. A PDF message needs the actual media attachment. Reuse a verified private channel target only within authorized delivery scope; do not guess destinations. Read receipt is not established by API success.

When comparing models, provide identical facts/constraints in independent contexts, preserve raw responses and actual provider/model receipts, and return errors to the author. Do not label ONYX-written text as another model's work. Prefer one selected output once the user chooses. A successful content-only model API call proves authorship, not autonomous tool use.

Done: the selected preview is delivered and current memory points to its new version; old reports are clearly historical.

## Synchronization

Local work does not require KSeF login. For OpenClaw Secret Store auth proof use the existing `openclaw asef-secretstore auth-check --nip NIP --env ENV` on Gateway. Never pass the egress sentinel into ASEF token encryption.

For an authorized refresh, use the current Gateway `asef` CLI with explicit NIP/environment and range from `asef sync --help`; verify the completed range and cursor. The old `asef_ksef` action or a dated runner is not a prerequisite. Do not change credentials, `allowSend`, or config to work around a read failure.

Verify imported counts, XML hashes, duplicates, integrity and role-specific high-water marks; give the user the range/date basis and cutoff. Imports may contain separate corrections. Do not claim current completeness beyond those cutoffs or create a recurring scheduler without scope.

Done: refresh evidence, not an attempted call, establishes freshness.
