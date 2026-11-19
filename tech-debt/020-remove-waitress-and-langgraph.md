# Remove the unused waitress and langgraph dependencies

pyproject.toml `dependencies`: `waitress` is not imported or referenced anywhere in the repository and is not used to serve the app; `langgraph` is never imported directly (only `from langchain.agents import create_agent` in src/optimization/explain_agent.py) and already comes in as a dependency of `langchain`. Remove both lines and regenerate poetry.lock with `poetry lock`.
