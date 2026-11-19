# Technical debt

The entries live in [`tech-debt/`](tech-debt/), one file per entry. This file is the index: it holds the rules and is not edited when an entry is added or fixed.

## Rules

- One entry per file, named `NNN-short-slug.md`. The number only sets the order in which entries are picked up; a new entry takes the next free number.
- A file starts with a one-line `# Title`, followed by the affected file path and symbol, the concrete observable problem with its trigger condition, and the proposed fix.
- An entry must be understandable and fixable by someone who has not seen the original context.
- Small, concrete, low-risk issues only. Broad refactors, product ideas and anything that needs a decision or a long investigation go to a GitHub issue instead.
- Security vulnerabilities are never written here.
- An entry is fixed in a pull request of its own, on a branch named `tech-debt/<short-slug>`; the same pull request deletes the entry's file.

The full rules for agents are in [AGENTS.md](AGENTS.md), section "Technical debt".
