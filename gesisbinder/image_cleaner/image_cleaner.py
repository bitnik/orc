"""
Some parts are taken from binderhub's image-cleaner
(https://github.com/jupyterhub/binderhub/tree/master/helm-chart/images/image-cleaner)
"""
from datetime import datetime, timedelta
from urllib.parse import unquote
from time import time, sleep

import shutil
import requests
import docker
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def get_available_disk_space():
    disk_usage = shutil.disk_usage("/ssd")
    available = (disk_usage.free * 100) / disk_usage.total
    return available


def clean_images():
    start_time = time()
    # TODO delete an image if built with older version
    # response = requests.get("https://notebooks.gesis.org/binder/versions", timeout=1)
    # r2d_version = response.json()["builder"]

    client = docker.from_env()
    prune_output = client.images.prune(filters={"dangling": True})
    logging.info(f"After prune: {prune_output}")

    image_prefix = "gesiscss/orc-binder-"  # TODO get as config
    for days in range(7, -1, -1):  # TODO get 7 as config
        # TODO get 50 as config
        available_low_limit = 50  # in percentage
        available = get_available_disk_space()
        if available > available_low_limit:
            # cleaning is done when there is enough free disk space
            logging.info(f"There is enough free disk space ({available}%). Stop deleting.")
            break
        logging.info(f"Free disk space: {available}%")

        if days == 1:
            logging.warning("Deleting everything not used in last 1 day.")
        elif days == 0:
            logging.error("Wanted to delete everything! Script is aborted.")
            break
        else:
            logging.info(f"Deleting everything not used in last {days} days.")

        from_dt = (datetime.now() - timedelta(days=days)).isoformat()
        url = f'https://notebooks.gesis.org/gallery/api/v1.0/launches/{from_dt}/'
        launches = []
        # because of pagination the api gives 100 results per page so for analysis you have to take data in all pages
        next_page = 1
        while next_page is not None:
            api_url = url + str('?page=') + str(next_page)
            r = requests.get(api_url)
            response = r.json()
            # check the limit of queries per second/minute,
            message = response.get("message", "")
            if message not in ["2 per 1 second", "100 per 1 minute"]:
                launches.extend(response['launches'])
                next_page = response['next_page']
            else:
                sleep(1)

        _launches = []
        for l in launches:
            # get launches on GESIS only
            if l["origin"] not in ["notebooks.gesis.org", "gesis.mybinder.org"]:
                continue
            if l["provider"] in ["Git", "GitLab"]:
                spec = l["spec"].rsplit("/", 1)[0]
                spec = unquote(spec)
            elif l["provider"] in ["Zenodo", "Figshare"]:
                spec = l["spec"]
            else:
                spec = l["spec"].rsplit("/", 1)[0]
            if spec.endswith(".git"):
                spec = spec.split(".git")[0]
            _launches.append((spec, l["provider"].lower()))

        delay = 5  # TODO get 5 as config
        deleted = 0
        launches = _launches
        for image in client.images.list():
            if not image.tags:
                # TODO why does this exist?
                continue
            tag = image.tags[0]
            if not tag.startswith(image_prefix):
                continue

            # image.labels["repo2docker.repo"] gives us repo url
            # image.labels["repo2docker.version"] gives us repo2docker version
            # image.labels["repo2docker.ref"] gives us resolved ref (image tag)
            # delete dockerfile repos always, because they dont have r2d labels (unless maintainer adds them)
            if "repo2docker.repo" not in image.labels or \
                not bool([True for spec, provider in launches
                          if spec in image.labels["repo2docker.repo"] and provider in image.labels["repo2docker.repo"]]):
                logging.info(f"Deleting {tag} with labels: {image.labels}")
                try:
                    client.images.remove(tag)
                    # client.images.remove(image=image.id, force=True)
                    # logging.info(f'Removed {name}')
                    # Delay between deletions.
                    # A sleep here avoids monopolizing the Docker API with deletions.
                    sleep(delay)
                except docker.errors.APIError as e:
                    if e.status_code == 409:
                        # This means the image can not be removed right now
                        logging.info(f'Failed to remove {tag}, skipping this image')
                        logging.info(str(e))
                    elif e.status_code == 404:
                        logging.info(f'{tag} not found, probably already deleted')
                    else:
                        raise
                except requests.exceptions.ReadTimeout:
                    logging.warning(f'Timeout removing {tag}')
                    # Delay longer after a timeout, which indicates that Docker is overworked
                    sleep(max(delay, 30))
                else:
                    deleted += 1
                    if available > 50:
                        # cleaning is done when there is enough free disk space
                        logging.info(f"While deleting everything not used in last {days} days, "
                                     f"reached to enough free disk space ({available}%). Stop deleting.")
                        break
            else:
                logging.info(f"Skipping {tag} with labels: {image.labels}")


        available = get_available_disk_space()
        logging.info(f"Deleted {deleted} images. Free disk space after deleting {days} days: {available}%")
    logging.info(f"Duration: {timedelta(seconds=time()-start_time)}\n\n")


if __name__ == '__main__':
    clean_images()