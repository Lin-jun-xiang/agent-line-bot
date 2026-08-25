"""LINE integration layer.

Import `line_bot.routes` explicitly — importing it here would make the package
unusable whenever LINE credentials are absent, which is a supported mode (the
/agent API runs fine without them).
"""
