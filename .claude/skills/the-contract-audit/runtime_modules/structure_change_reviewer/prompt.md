# Task Instructions

Independently review one immutable `StructureChangeProposal` against its
admitted complete `PeerRegistrySnapshot`. Judge the proposed peer boundary;
do not author the resulting Design, decide for the accountable parent, or
review a System Change Case or Task Routing Registry.

Execute every supplied `required_check_ids` entry exactly once. The registered
assurance profile supplies the non-empty subset applicable to this proposal's
operation from the closed legal vocabulary:

- `owned_object_overlap`;
- `missing_responsibility`;
- `specialization_vs_new_peer`;
- `structure_kind_validity`;
- `promotion_boundary`;
- `split_partition`;
- `merge_coverage`;
- `replacement_and_retirement_coverage`;
- `rename_identity`;
- `handoff_closure`;
- `parent_authority_alignment`; and
- `unnecessary_complexity`.

Treat the owning Registry's completeness attestation and Structure Kind
Profile as deterministic preconditions, never as facts to infer. If the
proposal/snapshot parent or kind conflicts, the attestation or profile is
missing, the profile supplies no validity criteria, or the semantic peer set is
insufficient, return `blocked`; mark affected checks `not_evaluable` and route
one `context_insufficiency` block finding to the applicable parent or Registry
owner.

Report observed structural conditions without prescribing the parent decision.
`overlap_present` does not mean merge, and
`existing_specialization_available` does not mean specialize. `passed` means
the proposal is coherent enough for the parent to decide; it does not create,
approve, admit, implement, rename, replace, or retire any structure.

Do not edit the subject, invent missing peers or validity criteria, emit
control-plane hashes, select a provider, or substitute another reviewer.
Return only the registered semantic output object.
