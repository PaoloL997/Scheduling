# Agent instructions

## Git
- Do not commit directly to `main`/`master`.
- Create a dedicated branch for every change.
- Do not merge pull requests.

## Technical debt
When working in an area of the codebase, look for small, concrete technical-debt issues as part of normal implementation and review. If you discover an issue that is:
- Clearly scoped and independently actionable;
- Low-risk enough for a focused PR;
- Described with an affected area, concrete problem, and desired outcome;
add it to TECH-DEBT.md unless it is fixed in the current PR.

Do not add broad refactors, speculative concerns, product ideas, or items that require significant investigation. Create a ticket instead for cross-cutting, risky, or large work, then link to that ticket from TECH-DEBT.md if useful.

### Entry format
- One entry per line, as a list item, in a flat list (no sections, no priorities, no owners, no dates).
- Start with the affected file path and symbol (function, class, template block, setting), then the concrete observable problem, then the proposed fix.
- An entry must be understandable and fixable by someone who has not seen the original context: name the files, the symbols and the trigger condition.
- Keep one blank line between entries.

### Adding entries
- Add an entry in the same commit as the work during which you noticed it; do not open a PR just to add an entry.
- Append at the end of the file.
- Before adding, check that no existing entry already covers it; extend that entry instead of duplicating it.

### Fixing entries
- Check for an open PR or a remote tech-debt/* branch that already addresses the entry before picking it up.
- One entry per PR, on a dedicated branch named tech-debt/<short-slug>, created from up-to-date main.
- Fix only that entry; do not touch unrelated files.
- Commit message: "tech-debt: <what changed>".
- In the same PR, delete the entry's text from TECH-DEBT.md but leave its blank line in place, so parallel PRs do not conflict on neighbouring lines.
- If an entry turns out to be larger or riskier than described, do not force it: rewrite the entry with what you learned, or move it to a ticket and link it.
- Do not merge the PR.
