import atexit
import json
import time

import docker
import paramiko.ssh_exception
import requests
from apscheduler.schedulers import SchedulerNotRunningError
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from CTFd.models import db

from .models import ContainerInfoModel
from .utils import get_random_ports


class ContainerException(Exception):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        if args:
            self.message = args[0]
        else:
            self.message = None

    def __str__(self) -> str:
        if self.message:
            return self.message
        else:
            return "Unknown Container Exception"


class ContainerManager:
    def __init__(self, settings, app):
        self.settings = settings
        self.client = None
        self.app = app
        if (
            settings.get("docker_base_url") is None
            or settings.get("docker_base_url") == ""
        ):
            return

        # Connect to the docker daemon
        try:
            self.initialize_connection(settings, app)
            for job in self.expiration_scheduler.get_jobs():
                job.resume()
        except ContainerException:
            for job in self.expiration_scheduler.get_jobs():
                job.pause()
            print("Docker could not initialize or connect.")
            return

    def initialize_connection(self, settings, app) -> None:
        self.settings = settings
        self.app = app

        # Remove any leftover expiration schedulers
        try:
            self.expiration_scheduler.shutdown()
        except (SchedulerNotRunningError, AttributeError):
            # Scheduler was never running
            pass

        if settings.get("docker_base_url") is None:
            self.client = None
            return

        try:
            import os
            import stat

            wrapper_dir = "/tmp/fake_ssh_dir"
            os.makedirs(wrapper_dir, exist_ok=True)
            wrapper_path = os.path.join(wrapper_dir, "ssh")
            wrapper_script = """#!/bin/sh
        exec /usr/bin/ssh -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i /opt/CTFd/.ssh/ctfd "$@"
        """
            with open(wrapper_path, "w") as f:
                f.write(wrapper_script)

            st = os.stat(wrapper_path)
            os.chmod(wrapper_path, st.st_mode | stat.S_IEXEC)

            os.environ["PATH"] = (
                f"{wrapper_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"
            )
            self.client = docker.DockerClient(
                base_url=settings.get("docker_base_url"), use_ssh_client=True
            )
        except docker.errors.DockerException as e:
            self.client = None
            raise ContainerException("CTFd could not connect to Docker" + str(e))
        except TimeoutError as e:
            self.client = None
            raise ContainerException("CTFd timed out when connecting to Docker")
        except paramiko.ssh_exception.NoValidConnectionsError as e:
            self.client = None
            raise ContainerException(
                "CTFd timed out when connecting to Docker: " + str(e)
            )
        except paramiko.ssh_exception.AuthenticationException as e:
            self.client = None
            raise ContainerException(
                "CTFd had an authentication error when connecting to Docker: " + str(e)
            )

        # Set up expiration scheduler
        try:
            self.expiration_seconds = int(settings.get("container_expiration", 0)) * 60
        except (ValueError, AttributeError):
            self.expiration_seconds = 0

        EXPIRATION_CHECK_INTERVAL = 5

        if self.expiration_seconds > 0:
            self.expiration_scheduler = BackgroundScheduler()
            self.expiration_scheduler.add_job(
                func=self.kill_expired_containers,
                args=(app,),
                trigger="interval",
                seconds=EXPIRATION_CHECK_INTERVAL,
            )
            self.expiration_scheduler.start()

            # Shut down the scheduler when exiting the app
            atexit.register(lambda: self.expiration_scheduler.shutdown())

    # TODO: Fix this cause it doesn't work
    def run_command(func):
        def wrapper_run_command(self, *args, **kwargs):
            if self.client is None:
                try:
                    self.__init__(self.settings, self.app)
                except:
                    raise ContainerException("Docker is not connected")
            try:
                if self.client is None:
                    raise ContainerException("Docker is not connected")
                if self.client.ping():
                    return func(self, *args, **kwargs)
            except (
                paramiko.ssh_exception.SSHException,
                ConnectionError,
                requests.exceptions.ConnectionError,
            ) as e:
                # Try to reconnect before failing
                try:
                    self.__init__(self.settings, self.app)
                except:
                    pass
                raise ContainerException(
                    "Docker connection was lost. Please try your request again later."
                )

        return wrapper_run_command

    @run_command
    def kill_expired_containers(self, app: Flask):
        with app.app_context():
            containers: "list[ContainerInfoModel]" = ContainerInfoModel.query.all()

            for container in containers:
                delta_seconds = container.expires - int(time.time())
                if delta_seconds < 0:
                    try:
                        self.kill_container(container.container_id)
                    except ContainerException:
                        print(
                            "[Container Expiry Job] Docker is not initialized. Please check your settings."
                        )

                    db.session.delete(container)
                    db.session.commit()

    @run_command
    def is_container_running(self, service_id: str) -> bool:
        service = self.client.services.list(filters={"id": service_id})
        if len(service) == 0:
            return False
        return len(service[0].tasks(filters={"desired-state": "running"})) > 0

    @run_command
    def create_container(
        self, image: str, ports: list[str], command: str, volumes: str
    ):
        kwargs = {}

        # Set the memory and CPU limits for the container
        if self.settings.get("container_maxmemory"):
            try:
                mem_limit = int(self.settings.get("container_maxmemory"))
                if mem_limit > 0:
                    kwargs["mem_limit"] = f"{mem_limit}m"
            except ValueError:
                ContainerException(
                    "Configured container memory limit must be an integer"
                )
        if self.settings.get("container_maxcpu"):
            try:
                cpu_period = float(self.settings.get("container_maxcpu"))
                if cpu_period > 0:
                    kwargs["cpu_quota"] = int(cpu_period * 100000)
                    kwargs["cpu_period"] = 100000
            except ValueError:
                ContainerException("Configured container CPU limit must be a number")

        if volumes is not None and volumes != "":
            print("Volumes:", volumes)
            try:
                volumes_dict = json.loads(volumes)
                kwargs["volumes"] = volumes_dict
            except json.decoder.JSONDecodeError:
                raise ContainerException("Volumes JSON string is invalid")

        out_ports = get_random_ports(len(ports))

        port_json = json.loads("{}")
        for i in range(len(ports)):
            if len(ports[i].split("/udp")) == 2:
                port_json[out_ports[i]] = (int(ports[i].split("/udp")[0]), "udp")
            else:
                port_json[out_ports[i]] = int(ports[i])
        print(port_json)
        try:
            return self.client.services.create(
                image,
                command=command,
                endpoint_spec=docker.types.EndpointSpec(ports=port_json),
                **kwargs,
            )
        except docker.errors.ImageNotFound:
            raise ContainerException("Docker image not found")

    @run_command
    def get_container_ports(self, container_id: str) -> list[str]:
        ports = []
        try:
            for port in list(
                self.client.services.get(container_id).attrs["Endpoint"]["Ports"]
            ):
                if port is not None:
                    ports.append(port["PublishedPort"])
            return ports
        except (KeyError, IndexError) as e:
            return []

    @run_command
    def get_images(self) -> "list[str]|None":
        try:
            images = self.client.images.list()
        except (KeyError, IndexError):
            return []

        images_list = []
        for image in images:
            if len(image.tags) > 0:
                images_list.append(image.tags[0])

        images_list.sort()
        return images_list

    @run_command
    def kill_container(self, container_id: str):
        try:
            self.client.services.get(container_id).remove()
        except docker.errors.NotFound:
            pass

    def is_connected(self) -> bool:
        try:
            self.client.ping()
        except:
            return False
        return True
