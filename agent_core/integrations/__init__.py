"""Third-party integrations the agent's tools are built on.

These live under agent_core because agent_core/tools.py is their only consumer;
keeping them in the LINE package meant the agent had to import the chat frontend.
"""

from agent_core.integrations.image_crawler import ImageCrawler
from agent_core.integrations.web_search import deep_web_search, web_search

__all__ = ["ImageCrawler", "deep_web_search", "web_search"]
