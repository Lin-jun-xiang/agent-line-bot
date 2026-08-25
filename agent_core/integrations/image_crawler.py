import requests
from icrawler import ImageDownloader
from icrawler.builtin import GoogleImageCrawler


class CustomLinkPrinter(ImageDownloader):
    """Only get image urls instead of store
    
    References
    ----------
    [Issue#73](https://github.com/hellock/icrawler/issues/73)
    """
    file_urls = []

    def get_filename(self, task, default_ext):
        file_idx = self.fetched_num + self.file_idx_offset
        return '{:04d}.{}'.format(file_idx, default_ext)

    def download(self, task, default_ext, timeout=5, max_retry=3, overwrite=False, **kwargs):
        file_url = task['file_url']
        filename = self.get_filename(task, default_ext)

        task['success'] = True
        task['filename'] = filename

        if not self.signal.get('reach_max_num'):
            self.file_urls.append(file_url)

        self.fetched_num += 1

        if self.reach_max_num():
            self.signal.set(reach_max_num=True)

        return


class ImageCrawler:
    """Crawl the Image"""
    def __init__(
        self,
        engine: str = 'icrawler',
        nums: int = 1,
        api_key: str = None
    ) -> None:
        self.image_save_path = ("./")
        self.engine = engine
        self.nums = nums
        self.api_key = api_key

    def _is_img_url(self, url) -> bool:
        """Check the image url is valid or invalid"""
        try:
            response = requests.head(url)
            content_type = response.headers['content-type']
            return content_type.startswith('image/')
        except requests.RequestException:
            return False
        except Exception as e:
            return False

    def _serpapi(self, search_query: str) -> list[str]:
        """Serpapi for google search images"""
        try:
            from serpapi import GoogleSearch
        except:
            pass
        params = {
            "engine": "google",
            "q": search_query,
            "tbm": "isch",
            "api_key": self.api_key
        }

        search = GoogleSearch(params)
        results = search.get_dict()
        images = results['images_results']

        urls = []
        for image in images[:self.nums]:
            urls.append(image['original'])

        return urls

    def _icrawler(self, search_query: str, prefix_name: str = 'tmp') -> list[str]:
        """Icrawler for google search images (Free)"""
        google_crawler = GoogleImageCrawler(
            downloader_cls=CustomLinkPrinter,
            storage={'root_dir': self.image_save_path},
            parser_threads=4,
            downloader_threads=4
        )

        # TODO: https://github.com/hellock/icrawler/issues/40
        google_crawler.session.verify = False
        google_crawler.downloader.file_urls = []

        google_crawler.crawl(
            keyword=search_query,
            max_num=self.nums,
            file_idx_offset=0
        )
        img_urls = google_crawler.downloader.file_urls
        print(f'Get image urls: {img_urls}')

        return img_urls[:self.nums]

    def get_url(self, search_query: str) -> str:
        try:
            if self.engine == 'icrawler':
                urls = self._icrawler(search_query)
            elif self.engine == 'serpapi':
                urls = self._serpapi(search_query)

            for url in urls:
                if self._is_img_url(url):
                    return url

        except Exception as e:
            print(f'\033[31m{e}')
