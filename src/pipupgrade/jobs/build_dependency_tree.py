import os.path as osp

# imports - standard imports
import requests as req
import grequests
from bs4 import BeautifulSoup

from tqdm import tqdm

from pipupgrade.config       import PATH
from pipupgrade._compat      import iterkeys
from pipupgrade.util.request import proxy_request, proxy_grequest, get_random_requests_proxies as get_rand_proxy
from pipupgrade.util.system  import read, write, make_temp_dir
from pipupgrade.util.string  import safe_decode
from pipupgrade.util.array   import chunkify
from pipupgrade import log, db

BASE_INDEX_URL  = "https://pypi.org/simple"
logger          = log.get_logger(level = log.DEBUG)
connection      = db.get_connection()

def exception_handler(request, exception):
    logger.warning("Unable to load request: %s", exception)

def run(*args, **kwargs):
    with make_temp_dir() as dir_path:
        chunk_size  = kwargs.get("chunk_size", 1000)
        index_url   = kwargs.get("index_url", BASE_INDEX_URL)

        logger.info("Fetching Package List...")

        res  = proxy_request("GET", index_url, stream = True)
        res.raise_for_status()

        html = ""
        for content in res.iter_content(chunk_size = 1024):
            html += safe_decode(content)

        soup = BeautifulSoup(html, 'html.parser')

        packages        = list(map(lambda x: x.text, soup.findAll('a')))
        logger.info("%s packages found." % len(packages))
        
        package_chunks  = list(chunkify(packages, chunk_size))

        for package_chunk in tqdm(package_chunks):
            requestsmap = (
                proxy_grequest("GET", "https://pypi.org/pypi/%s/json" % package)
                    for package in package_chunk
            )

            responses   = grequests.map(requestsmap,
                exception_handler = exception_handler)

            for response in responses:
                if response.ok:
                    data     = response.json()
                    package  = data["info"]["name"]
                    releases = list(iterkeys(data["releases"]))

                    release_chunks = chunkify(releases, 100)

                    for release_chunk in release_chunks:
                        requestsmap = (
                            proxy_grequest("GET", "https://pypi.org/pypi/%s/%s/json" % (package, release))
                                for release in release_chunk
                        )

                        responses = grequests.map(requestsmap,
                            exception_handler = exception_handler)

                        for response in responses:
                            if response.ok:
                                data     = response.json()
                                version  = data["info"]["version"]
                                requires = data["info"]["requires_dist"]

                                query    = """
                                    INSERT INTO `tabPackageDependency`
                                        (name, version, requires)
                                    VALUES
                                        (?, ?, ?)
                                """ 
                                values   = (package, version, ",".join(requires) if requires else "NULL")

                                connection.query(query, values)
                            else:
                                logger.info("Unable to load URL: %s" % response.url)
                else:
                    logger.info("Unable to load URL: %s" % response.url)