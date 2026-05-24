import requests


def local_session():
    session = requests.Session()
    session.trust_env = False
    return session


def local_get(url, **kwargs):
    with local_session() as session:
        return session.get(url, **kwargs)


def local_put(url, **kwargs):
    with local_session() as session:
        return session.put(url, **kwargs)


def local_post(url, **kwargs):
    with local_session() as session:
        return session.post(url, **kwargs)


def local_delete(url, **kwargs):
    with local_session() as session:
        return session.delete(url, **kwargs)
