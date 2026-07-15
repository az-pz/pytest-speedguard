"""Test configuration for pytest-speedguard's own suite.

Enables the ``pytester`` fixture so the integration tests can spin up isolated
inner pytest sessions that exercise the installed plugin end to end.
"""

pytest_plugins = ["pytester"]
