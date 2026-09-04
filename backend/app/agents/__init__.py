"""The Story Engine agent network — declarations only, never an SDK.

:mod:`app.agents.network` declares the coordinator and its specialists as
provider-agnostic :class:`~app.adapters.adk.AgentSpec` trees,
:mod:`app.agents.tools` wraps the real pipeline functions as agent tools,
:mod:`app.agents.context` carries the per-run repository/project/LLM the model
must not choose, :mod:`app.agents.events` is the wire contract a live run
streams, and :mod:`app.agents.run` drives one run end to end.

Nothing in this package imports ``google.adk``: only
:mod:`app.adapters.adk` does, lazily, which is what keeps offline test
collection green with the SDK uninstalled.

This file also makes ``app.agents`` a regular package rather than a namespace
one, so ``[tool.setuptools.packages.find]`` (which does not scan for namespace
packages) ships it in an installed distribution.
"""
