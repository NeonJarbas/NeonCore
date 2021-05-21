from mycroft.configuration import *


def get_private_keys():
    return Configuration.get(remote=False).get("keys", {})

