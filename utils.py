import random

import docker
from sqlalchemy import select

from CTFd.models import db

from .models import ContainerInfoModelPort


def get_random_ports(
    number_of_port: int, range_min: int = 10000, range_max: int = 10500
) -> list[int]:
    out = []

    used_ports = list(db.session.scalars(select(ContainerInfoModelPort.port)).all())
    if len(used_ports) >= range_max - range_min:
        raise Exception("No port available in the range")

    for i in range(number_of_port):
        port = random.randint(range_min, range_max)
        if port in used_ports:
            port = ((port - range_min) + 1) % (range_max - range_min) + range_min
        used_ports.append(port)
        out.append(port)
    return out
