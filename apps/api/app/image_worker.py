import logging
import time

from redis import Redis
from rq import Queue, Worker
from app.article_images import REDIS_URL, init_image_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    for attempt in range(30):
        try:
            init_image_db()
            connection=Redis.from_url(REDIS_URL)
            connection.ping()
            break
        except Exception:
            if attempt==29:
                raise
            logger.warning("Waiting for image database/Redis...")
            time.sleep(2)
    queue=Queue("images",connection=connection)
    Worker([queue],connection=connection).work()
