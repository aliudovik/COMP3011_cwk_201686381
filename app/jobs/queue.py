import redis
from rq import Queue

def get_redis_connection(redis_url: str):
    return redis.from_url(redis_url)

def get_queue_names():
    return ["default"]

def enqueue(app, func, *args, **kwargs):
    conn = get_redis_connection(app.config["REDIS_URL"])
    q = Queue("default", connection=conn)
    return q.enqueue(func, *args, **kwargs)
