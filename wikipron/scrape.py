import re
import unicodedata
import pkg_resources

import requests
import requests_html
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

from wikipron.config import Config
from wikipron.typing import Iterator, WordPronPair


# Queries for the MediaWiki backend.
# Documentation here: https://www.mediawiki.org/wiki/API:Categorymembers
_CATEGORY_TEMPLATE = "Category:{language} terms with IPA pronunciation"
# Selects the content on the page.
_PAGE_TEMPLATE = "https://en.wiktionary.org/wiki/{word}"


def http_session() -> requests.Session:
    return _http_session(html=False)


def http_html_session() -> requests_html.HTMLSession:
    return _http_session(html=True)


def _http_session(*, html: bool):
    # 10 tries of {0.0, 0.4, 0.8, 1.6, ..., 102.4} seconds.
    # urllib3 has a BACKOFF_MAX of 120 seconds.
    retry = Retry(total=10, backoff_factor=0.2)
    adapter = HTTPAdapter(max_retries=retry)
    user_agent = (
        f"WikiPron/{pkg_resources.get_distribution('wikipron').version} "
        "(https://github.com/kylebgorman/wikipron) "
        f"python-requests/{requests.__version__}"
    )
    session = requests_html.HTMLSession() if html else requests.Session()
    session.headers.update({"User-Agent": user_agent})
    session.mount("https://", adapter)
    return session


def _skip_word(word: str, no_skip_spaces: bool) -> bool:
    # Skips multiword examples.
    if not no_skip_spaces and (" " in word or "\u00A0" in word):
        return True
    # Skips examples containing a dash.
    if "-" in word:
        return True
    # Skips examples containing digits.
    if re.search(r"\d", word):
        return True
    return False


def _skip_date(date_from_word: str, cut_off_date: str) -> bool:
    return date_from_word > cut_off_date


def _scrape_once(data, config: Config) -> Iterator[WordPronPair]:
    for member in data["query"]["categorymembers"]:
        word = member["title"]
        date = member["timestamp"]
        if _skip_word(word, config.no_skip_spaces_word) or _skip_date(
            date, config.cut_off_date
        ):
            continue
        with http_html_session() as session:
            request = session.get(_PAGE_TEMPLATE.format(word=word))
        for word, pron in config.extract_word_pron(word, request, config):
            # Pronunciation processing is done in NFD-space;
            # we convert back to NFC afterwards.
            yield word, unicodedata.normalize("NFC", pron)


def _language_name_for_scraping(language):
    """Handle cases where X is under a "macrolanguage" on Wiktionary."""
    if language == "Cantonese":
        return "Chinese"
    else:
        return language


def scrape(config: Config) -> Iterator[WordPronPair]:
    """Scrapes with a given configuration."""
    category = _CATEGORY_TEMPLATE.format(
        language=_language_name_for_scraping(config.language)
    )
    requests_params = {
        "action": "query",
        "format": "json",
        "list": "categorymembers",
        "cmtitle": category,
        "cmlimit": "500",
        "cmprop": "ids|title|timestamp",
    }
    while True:
        with http_session() as session:
            data = session.get(
                "https://en.wiktionary.org/w/api.php?",
                params=requests_params,
            ).json()
        yield from _scrape_once(data, config)
        if "continue" not in data:
            break
        continue_code = data["continue"]["cmcontinue"]
        requests_params.update({"cmcontinue": continue_code})
