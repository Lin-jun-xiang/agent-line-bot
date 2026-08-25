import os

from dotenv import load_dotenv

load_dotenv()

# LINE Bot
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

# Optional fallback for image search when the free crawler finds nothing.
SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY")
