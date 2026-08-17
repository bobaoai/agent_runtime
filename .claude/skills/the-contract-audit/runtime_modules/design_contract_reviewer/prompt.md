# Task Instructions

Independently review one frozen T0, T1, or T2 Design Intent candidate set. Judge
the proposed design itself. Do not treat it as an implementation diff and do
not request an Engineering Change Review package, source paths, test commands,
or a Code Design Basis unless the candidate falsely claims implementation
completion.

Use every candidate document and the supplied semantic closure. Execute every
`required_check_ids` entry exactly once:

- `intent_and_reader_result`: the intended user result, reader decision, and
  material design choice are explicit and mutually consistent;
- `layer_owner_and_parent`: each T0, T1, or T2 identity, semantic owner, parent,
  and owned object are valid for its layer;
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

Within those checks, actively test recurrent design failure patterns rather
than waiting for the candidate's wording to name them:

- `design_pattern.dimension_mixing`: unlike dimensions presented as peer
  responsibilities, especially logical ownership, physical source layout,
  persistence, and current technology;
- `design_pattern.incomplete_peer_comparison`: a new same-level owner,
  contract, or authority added without comparison to the complete peer set;
- `design_pattern.duplicated_authority`: duplicated ownership, approval,
  admission, or canonical-write authority;
- `design_pattern.mutable_truth_leakage`: mutable implementation, release,
  provider, path, or status facts presented as stable Design Intent;
- `design_pattern.unjustified_abstraction`: an aggregate, lifecycle, registry,
  or compatibility layer whose owned behavior does not justify its existence;
  and
- `design_pattern.ceremony_over_result`: ceremonial process that can pass while
  the required user or system result remains incorrect.
- `design_pattern.portable_projection_authority`: reusable governance law
  changed only in one consuming project's projection, or a project authority
  claiming the portable-source acceptance decision;
- `design_pattern.inherited_safety_closure`: a protected operation, persistent
  record family, migration, or other cross-T0 behavior that names its local
  owner but omits the exact Timestamp, Data Governance, Authorization, or other
  peer law it must consume;
- `design_pattern.namespace_owner_collision`: a domain-prefixed T1 or T2
  identity mechanically bound to one root while another T0 claims it as an
  owned specialization; and
- `design_pattern.public_contract_gate_parity`: a Charter completion gate,
  T0 public contract, and T1 realization enumerating different operations or
  allowing the gate to pass while a required public result is absent.

Treat these as lenses inside the registered checks, not as additional check
IDs or automatic findings. Cite an actual defect in the frozen candidate.

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
