# Remove seed_demo_planning, which clashes with seed_optimization_demo

ui/management/commands/seed_demo_planning.py and ui/tests/test_seed_demo_planning.py: the command seeds commesse OPT01–OPT03 with fixed August 2026 dates, the same commesse `seed_optimization_demo` owns and deletes in its `_clear_demo`, so each seed destroys the other's data; it is not mentioned in README.md and nothing but its own test calls it. Delete the command and its test; `seed_optimization_demo` (documented, relative dates) stays the demo for the optimization pages.
