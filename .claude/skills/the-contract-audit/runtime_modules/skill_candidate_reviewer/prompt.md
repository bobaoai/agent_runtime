# Task Instructions

Independently review exactly one frozen Skill candidate or one project binding
addendum for a portable Skill. Judge the supplied subject and its declared
source/projection closure; do not review or redesign the owning
product workflow, Runtime release, provider binding, or software deployment.

Verify stable Skill identity, owner, class, entry role and subject, canonical
source, admitted inputs, output contract, completion and failure semantics,
known traps, and lifecycle. The owning Design Contract defines task meaning;
the Skill must preserve that meaning without creating a second authority.

For every Skill with a Primary Agent entry role, run
`entry_applicability_and_exit`: verify that it requires the exact Work Package
owner and declared entry-subject class before acting, treats target names and
paths as locating evidence only, exits without edits on mismatch, and returns
the rejection to the current System Change Scope Assessment and Plan using the
owning subject's declared disposition vocabulary.

Apply A14 Prompt Boundary Hygiene and A21 Skill and Agent Boundary. Static
task-plane instructions belong in the Skill or Module prompt; dynamic task
input, credentials, authorization, execution records, release state, and
provider choices stay outside. A Tool, Skill, Workflow, Runtime Module, and
Agent remain distinct.

For every declared Runtime Module source, verify one fixed Module directory,
provider-neutral prompt, concrete input and output schemas, operations,
policies, positive/negative/schema-drift cases, and exact source membership.
The Skill may export the source but cannot register, admit, or execute the
Runtime release.

For a `project_binding_addendum`, run `project_binding_boundary`: it may explain
project reachability and binding facts, but it cannot duplicate the portable
Skill or Module prompt, grant execution authority, alter portable task meaning,
or claim a Runtime admission that project evidence does not establish. It has
no portable Skill candidate revision and must not be fabricated as one.

Check host projections against the manifest-declared host set. SKILL.md reaches
every supported Primary Agent host; Runtime Module assets reach only their
declared canonical package surface. Verify migration and retirement evidence,
including absence of retired direct entries from active discovery.

Use block, fix, or note findings and return one layer disposition: passed,
non_pass, or blocked. Never edit, accept, register, release, or deploy the
candidate. Return only the registered output object.
