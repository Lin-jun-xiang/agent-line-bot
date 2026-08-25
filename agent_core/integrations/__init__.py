"""Third-party integrations the agent's tools are built on.

These live under agent_core because agent_core/tools.py is their only consumer;
keeping them in the LINE package meant the agent had to import the chat frontend.

Import the submodules directly:

    from agent_core.integrations.web_search import deep_web_search

Deliberately no re-exports here. `web_search` is both a module and a function in
it, so re-exporting the function rebinds the package attribute and
`from agent_core.integrations import web_search` then hands you the function
instead of the module — a confusing failure with no upside, since every consumer
imports from the submodule anyway.
"""
