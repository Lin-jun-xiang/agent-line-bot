import os
import sys
from typing import Dict, Optional

import cloudscraper
from bs4 import BeautifulSoup

from chatgpt_linebot.modules.chat import chat_completion
from chatgpt_linebot.prompts import cws_channel_template

sys.path.append(".")


class CWArticleScraper:
    """
    A scraper for retrieving the latest article from CW, bypassing Cloudflare protections.
    """
    BASE_URL = 'https://www.cw.com.tw'
    DATA_FILE = './data/cws_channel.txt'

    def __init__(self) -> None:
        """Initialize the scraper with retry logic."""
        self.scraper = cloudscraper.create_scraper()

    def _get_latest_article_url(self) -> Optional[str]:
        """
        Retrieve the latest article URL.

        Returns:
            The full URL of the latest article or None if not found.
        """
        list_url = f'{self.BASE_URL}/masterChannel.action?idMasterChannel=8'
        try:
            response = self.scraper.get(list_url)
            soup = BeautifulSoup(response.text, 'html.parser')

            latest_article_tag = soup.select_one('section.article h3 a')
            if latest_article_tag:
                href = latest_article_tag['href']
                latest_article_url = href if href.startswith('http') else 'https://www.cw.com.tw' + href
                return latest_article_url
        except Exception as e:
            print(f"Failed to fetch the latest article URL: {e}")
        return None

    def _get_article_details(self, article_url: str) -> Dict[str, str]:
        """
        Extract the article's title, time, and content.

        Args:
            article_url: The URL of the article.

        Returns:
            A dictionary with the article details.
        """
        try:
            response = self.scraper.get(article_url, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
        
            publish_date = 'Unknown'
            for tag in soup.select('time.pr20'):
                if '發布時間' in tag.get_text():
                    publish_date = tag.get_text(strip=True).replace('發布時間：', '')
                    break

            title_tag = soup.select_one('h1')
            title = title_tag.get_text(strip=True) if title_tag else 'No Title'

            content_section = soup.select_one('div.article__content')
            content = ''
            if content_section:
                paragraphs = content_section.find_all(['p', 'h2', 'blockquote'])
                content = '\n'.join(para.get_text(strip=True) for para in paragraphs)

            keywords = [tag.get_text(strip=True) for tag in soup.select('ul.article__keyword a.keywords')]

            return {
                'title': title,
                'time': publish_date,
                'content': content,
                'keywords': keywords
            }

        except Exception as e:
            print(f"Failed to extract article details: {e}")
            return {}

    def _is_url_already_processed(self, url: str) -> bool:
        """
        Check if the given URL already exists in the text file.

        Args:
            url: The article URL to check.

        Returns:
            True if the URL exists, False otherwise.
        """
        if os.path.exists(self.DATA_FILE):
            with open(self.DATA_FILE, 'r', encoding='utf-8') as file:
                existing_urls = {line.strip() for line in file}
                return url in existing_urls
        return False

    def _save_article_url(self, url: str) -> None:
        """
        Save the article URL to the text file.

        Args:
            url: The article URL to save.
        """
        os.makedirs(os.path.dirname(self.DATA_FILE), exist_ok=True)

        with open(self.DATA_FILE, 'a', encoding='utf-8') as file:
            file.write(f"{url}\n")

        print(f"📥 Article URL saved: {url}")

    def get_latest_article(self) -> Optional[Dict[str, str]]:
        """
        Scrape the latest article.

        Returns:
            A dictionary with article details or None if no article is found.
        """
        article_url = self._get_latest_article_url()
        if self._is_url_already_processed(article_url):
            print(f"✅ URL already exists: {article_url}")
            return None
        else:
            article_details = self._get_article_details(article_url)
            if article_details:
                self._save_article_url(article_url)
                return article_details

    def get_cws_channel_response(self, article_details: Dict) -> str:
        if article_details:
            response = chat_completion(
                memory=[{"role": "user", "content": cws_channel_template+str(article_details)}],
            )
            return response
