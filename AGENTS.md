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
add it as a new file in tech-debt/ unless it is fixed in the current PR.

Do not add broad refactors, speculative concerns, product ideas, or items that require significant investigation. Create a ticket instead for cross-cutting, risky, or large work, then link to that ticket from a tech-debt/ entry if useful. Never write security vulnerabilities in tech-debt/.

TECH-DEBT.md is only the index with the rules: do not edit it when adding or fixing an entry.

### Entry format
- One entry per file in tech-debt/, named `NNN-short-slug.md` (no priorities, no owners, no dates). The number only sets the order in which entries are picked up.
- Start with a one-line `# Title`, then the affected file path and symbol (function, class, template block, setting), the concrete observable problem, and the proposed fix.
- An entry must be understandable and fixable by someone who has not seen the original context: name the files, the symbols and the trigger condition.

### Adding entries
- Add an entry in the same commit as the work during which you noticed it; do not open a PR just to add an entry.
- Use the next free number after the highest one in tech-debt/.
- Before adding, check that no existing entry already covers it; extend that entry instead of duplicating it.

### Fixing entries
- Check for an open PR or a remote tech-debt/* branch that already addresses the entry before picking it up.
- One entry per PR, on a dedicated branch named tech-debt/<short-slug>, created from up-to-date main.
- Fix only that entry; do not touch unrelated files.
- Commit message and PR title: "tech-debt: <what changed>".
- In the same PR, delete the entry's file from tech-debt/. Do not renumber the other files.
- If an entry turns out to be larger or riskier than described, do not force it: rewrite the entry with what you learned, or move it to a ticket and link it.
- Do not merge the PR.
