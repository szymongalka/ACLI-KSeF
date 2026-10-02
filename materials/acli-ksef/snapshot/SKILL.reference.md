> Materiał referencyjny ze źródłowego acli-ksef/ASEF. Treść poniżej opisuje historyczny projekt i nie jest instrukcją dla ACLT-KSeF. Zobacz [opis importu](../README.md).

---
name: "acli-ksef"
description: "KSeF i refaktury: znajdź faktury, przygotuj i sprawdź projekt XML/PDF, synchronizuj lub wyślij wyłącznie konkretny zatwierdzony dokument przez ACLI-KSeF."
---

# ACLI-KSeF

ASEF is the local Agencyjny System Elektronicznych Faktur. Run `/root/.openclaw/acli-skills/acli-ksef/.venv/bin/asef --json` on the OpenClaw Gateway host (use Gateway exec when the current runtime is elsewhere). The examples below abbreviate the selected executable as `asef`. Its single live SQLite database physically lives under `/root/.openclaw/acli-database/acli-ksef/`; the old ASEF data directory is only a compatibility symlink. Check `asef --json db path` and resolve the path before working. Use the operator's authenticated private conversation; do not expose invoice data or execute ASEF requests from a group or another sender. Do not set `ASEF_DATA_DIR` for operator work: the credentialed bridge intentionally rejects it. Use it only for an explicitly isolated offline test.

## Workflow

For timed draft delivery, follow [Delivery budget](references/reinvoice.md#delivery-budget); read the target from current user directives.

1. Establish the current task from the latest user decision and a small excerpt of the ASEF topic note found through memory search. Run `asef --json db path` and `asef --json profile list`; select NIP/environment explicitly. Carry forward existing scope and choices instead of asking again. Done: the database, profile, selected draft ID/version and allowed action are known.
2. Search the local database first. For reinvoicing or model comparisons, follow [references/reinvoice.md](references/reinvoice.md) from source lookup through delivery. When fresh data is requested or absent, use the authorized sync path in [references/reinvoice.md](references/reinvoice.md#synchronization); establish the requested start date. Done: source XML, counterparties, amounts and freshness are evidenced; unknown business facts remain explicit.
3. Prepare one concise draft unless comparison was requested. Use explicit seller, buyer, dates and VAT; distinguish source seller from reinvoice seller. The JSON generator supports ordinary PLN invoices at 23, 8 and 5; other FA(3) cases require validated XML. Use numeric VAT values without a percent sign. For JSON drafts, before database mutation run `/root/.openclaw/acli-skills/acli-ksef/.venv/bin/python scripts/check_draft.py PAYLOAD --expected EXPECTED` with absolute paths resolved from this skill directory. Done: machine validation passes against source-derived expectations and only unresolved facts, not routine implementation choices, remain for the user.
4. Inspect the existing issued series before proposing a number; check local collisions and source freshness. An approved choice/number authorizes that draft edit, not sending. For revisions preserve the old XML/preview, revise the existing selected ID and regenerate its preview/hash; keep unchosen alternatives historical without deleting them. Done: one current selected ID/version is identified and amounts/parties match before and after.
5. Render HTML/PDF from that exact stored XML. Verify arithmetic, FA(3), stored/file SHA-256, status, party data and visible content; extract text with `pdftotext -raw` and inspect every page when image tools are available. If visual inspection is unavailable, report that limitation. Deliver the attachment with document ID/hash using the user's authorized channel; a path or a send attempt alone is not delivery. Done: preview and receipt are verified, or the precise delivery/visual limitation is reported.
6. Approve only after explicit user consent to the reviewed document and XML version: `asef invoice approve DOCUMENT_ID --sha256 HASH --yes`. Keep selection/number acceptance separate from exact-version approval. After approval, send only on an explicit sending request; PROD requires separate consent to that document in PROD and the reviewed hash via `--confirm-prod`. Preserve disabled send settings until an authorized configuration change. Done: approval/send state is evidenced, never inferred from “prepare”, “choose” or generic “approved”.
7. After sending, check `asef invoice status DOCUMENT_ID`; report a KSeF number only when returned, then retrieve UPO. For a no-send task verify no new approval/submission session. Reconcile the current topic note and project report with IDs/hashes, chosen variant, unresolved fields and evidence paths; preserve dated history and refresh an existing derived wiki view. After completed local work run `python3 /root/.openclaw/acli-skills/_ops/acli_sync.py --slug acli-ksef --execute --json` and check upload/readback status; SMB failure means sync pending, not backup or success. Done: the next model can resume from a short current-state note without replaying the conversation.

Before reporting unknowns, reconcile the selected XML with current memory: do not reopen the established source or full-cost choice; check missing service date and payment fields separately. Identify the source by its exact referenced number, with amounts/model as supporting evidence.

For shadow reconstructions, render the XML produced by the checker as well as any stored-version preview. Record each preview's input XML hash separately; equivalent content or masked timestamps do not make two hashes interchangeable.


## Boundaries

Before credentials, migration, approval or PROD send read [boundaries.md](references/boundaries.md). Send only on explicit document-specific consent, matching reviewed SHA-256 and separate PROD confirmation; preserve `allowSend=false` unless explicitly enabled for that send.
