# Task Instructions

Independently review one frozen Charter, T0, T1, or T2 Design Intent candidate set. Judge
the proposed design itself. Do not treat it as an implementation diff and do
not request an Engineering Change Review package, source paths, test commands,
or a Code Design Basis unless the candidate falsely claims implementation
completion.

Use every candidate document and the supplied semantic closure. Execute every
`required_check_ids` entry exactly once:

- `intent_and_reader_result`: the intended user result, reader decision, and
  material design choice are explicit and mutually consistent;
- `layer_owner_and_parent`: each Charter, T0, T1, or T2 identity, semantic
  owner, applicable parent, and owned object are valid for its layer;
- `peer_authority_and_inheritance`: same-level responsibilities are complete
  without overlap, and inherited constraints and dependency directions close;
- `boundary_coherence`: semantic owner, author, operator, reviewer, persistence
  owner, implementation binding, and approval or admission authority remain
  distinct;
- `design_and_code_truth_separation`: stable intent is separated from mutable
  implementation inventory, current bindings, provider choices, paths, and
  delivery status;
- `failure_completion_and_rollback`: public handoffs, failure meaning,
  completion conditions, and rollback obligations are stated at the correct
  layer;
- `implementability_without_redesign`: an implementer can form a Code Design
  Basis without inventing authority, product behavior, or a missing peer;
- `review_approval_and_admission`: independent review, accountable-owner
  decision, contract admission, implementation completion, and software
  release remain separate decisions.

Report one evidence-bound finding for every material defect. Every finding must
name both the candidate whose decision is affected and the document that owns
the required correction. The correction target may be a supplied context
document only when the candidate cannot safely resolve the contradiction
itself. That does not make the context document a candidate or claim a complete
review of it. Use its supplied `owner_ref`; do not invent an owner or redirect
the correction to the candidate owner.

`check_results` records actionability, not the complete finding inventory. Set
a check to `finding` and cite its finding IDs only when that check has at least
one `block` or `fix` finding. A check with only `note` findings is `passed` with
an empty `finding_ids` list; keep those notes in the top-level `findings` array.
When a check has actionable and note findings together, cite only its
actionable finding IDs.

Do not edit or rewrite any document, invent control-plane metadata, select a
provider, or admit the result. A missing required context or unresolved
authority is a block, not an invitation to infer the answer.

Return only the registered semantic output object.
