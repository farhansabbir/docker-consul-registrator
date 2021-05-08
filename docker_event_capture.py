#!/bin/env python3
import os
import sys
import json
import docker
import requests
import datetime

DOCKER_CLIENT = None
CONFIG = None

def init():
    global DOCKER_CLIENT
    global CONFIG
    try:
        CONFIG = json.load(open(sys.argv[1]))
        DOCKER_CLIENT = docker.DockerClient(base_url='unix:/' + str(CONFIG["docker"]))
        for container in (DOCKER_CLIENT.containers.list(filters={"status":"running"})):
            print(container.attrs["Id"])
        
        event_loop()

    except docker.errors.DockerException as dockererror:
        print("Unable to connect to docker daemon. Reason: " + str(dockererror))
        exit(1)
    except KeyError as err:
        print("Config parsing error. Key not found in config: " + str(err))
        exit(1)

def event_loop():
    for event in DOCKER_CLIENT.events(decode=True):
        if (event["Type"]=="container"):
            if event["status"] == "start" or event["status"] == "destroy":
                PAYLOAD = dict()
                PAYLOAD["CONTAINER_ID"] = event["id"]
                if "Attributes" in event["Actor"]:
                    ATTRS = event["Actor"]["Attributes"]
                    PAYLOAD["SERVICE_NAME"] = ATTRS["name"]
                    PAYLOAD["SERVICE"] = False
                    if "com.docker.swarm.service.id" in ATTRS:
                        PAYLOAD["SERVICE"] = True
                        PAYLOAD["SERVICE_ID"] = ATTRS["com.docker.swarm.service.id"]
                        PAYLOAD["SERVICE_NAME"] = ATTRS["com.docker.swarm.service.name"]
                    PAYLOAD["CONTAINER_NAME"] = ATTRS["name"]
                    PAYLOAD["IMAGE_NAME"] = ATTRS["image"]
                if event["status"] == "start":
                    PAYLOAD["CMD"] = "register"
                    # print(json.dumps(PAYLOAD))
                    PAYLOAD["PORT_MAPPING"] = list()
                    if not PAYLOAD["SERVICE"]:
                        EXT_ATTRS = fetch_container_details(PAYLOAD["CONTAINER_ID"]).attrs
                        for mapsrc,mapdst in EXT_ATTRS["HostConfig"]["PortBindings"].items():
                            PROTOCOL = "tcp"
                            if "udp" in mapsrc:
                                PROTOCOL = "udp"
                            PAYLOAD["PORT_MAPPING"].append(dict({"PROTOCOL":PROTOCOL,mapdst[0]["HostIp"]:mapdst[0]["HostPort"]}))
                    else:
                        # this is a service
                        # get port mapping info from service definition
                        ATTRS = fetch_service_details(PAYLOAD["SERVICE_ID"]).attrs
                        for mapping in ATTRS["Endpoint"]["Spec"]["Ports"]:
                            PAYLOAD["PORT_MAPPING"].append(dict({"PROTOCOL":str(mapping["Protocol"]),"":mapping["PublishedPort"]}))
                        
                    print(json.dumps(PAYLOAD))
                else:
                    PAYLOAD["CMD"] = "deregister"
                    notify_consul(PAYLOAD)

def fetch_container_details(id):
    return DOCKER_CLIENT.containers.get(id)

def fetch_service_details(id):
    return DOCKER_CLIENT.services.get(service_id=id)

def notify_consul(payload):
    print(json.dumps(payload))



if __name__ == "__main__":
    init()