"""Mini Jarvis - tool package.

Every tool raises ToolFailure (with a human-readable message) when its
action cannot be completed. The registry catches these and reports them;
tools never crash the assistant.
"""


class ToolFailure(Exception):
    """A tool could not complete its action."""
