#!/bin/env python3
import os
import sys
import json
import docker
import requests
import datetime
import platform

DOCKER_CLIENT = None
CONFIG = None
SELF_IP = None

def init():
    global DOCKER_CLIENT
    global CONFIG
    global SELF_IP
    try:
        CONFIG = json.load(open(sys.argv[1]))
        DOCKER_CLIENT = docker.DockerClient(base_url='unix:/' + str(CONFIG["docker"]))
        SELF_IP = CONFIG["self_ip"]
        check_consul_connection()
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
    global SELF_IP
    for event in DOCKER_CLIENT.events(decode=True):
        if (event["Type"]=="container"):
            if event["status"] == "start" or event["status"] == "destroy":
                PAYLOAD = dict()
                PAYLOAD["ID"] = event["id"] # container ID
                PAYLOAD["Address"] = CONFIG["self_ip"]
                PAYLOAD["Check"] = dict()
                PAYLOAD["Check"]["DeregisterCriticalServiceAfter"] = "15s"
                PAYLOAD["Check"]["Interval"] = "10s"
                PAYLOAD["Check"]["Timeout"] = "3s"
                PAYLOAD["Tags"] = list()
                PAYLOAD["EnableTagOverride"] = False
                PAYLOAD["Node"] = platform.node()
                if "Attributes" in event["Actor"]:
                    ATTRS = event["Actor"]["Attributes"]
                    PAYLOAD["Service"] = ATTRS["name"]
                    PAYLOAD["IsService"] = False
                    if "com.docker.swarm.service.id" in ATTRS:
                        PAYLOAD["IsService"] = True
                        PAYLOAD["ServiceID"] = ATTRS["com.docker.swarm.service.id"]
                        PAYLOAD["Service"] = ATTRS["com.docker.swarm.service.name"]
                    PAYLOAD["CONTAINER_NAME"] = ATTRS["name"]
                    PAYLOAD["Tags"].append(ATTRS["image"])
                    #PAYLOAD["IMAGE_NAME"] = ATTRS["image"]
                if event["status"] == "start":
                    PAYLOAD["CMD"] = "register"
                    PAYLOAD["PORT_MAPPING"] = list()
                    if not PAYLOAD["IsService"]:
                        EXT_ATTRS = fetch_container_details(PAYLOAD["ID"]).attrs
                        for mapsrc,mapdst in EXT_ATTRS["HostConfig"]["PortBindings"].items():
                            PROTOCOL = "TCP"
                            if "udp" in mapsrc:
                                PROTOCOL = "UDP"
                            if mapdst[0]["HostIp"] != "":
                                IP = mapdst[0]["HostIp"]
                            else:
                                IP = SELF_IP
                            PAYLOAD["PORT_MAPPING"].append(dict({"PROTOCOL":PROTOCOL,"IP":IP,"Port":mapdst[0]["HostPort"]}))
                    else:
                        # this is a service
                        # get port mapping info from service definition
                        ATTRS = fetch_service_details(PAYLOAD["ServiceID"]).attrs
                        for mapping in ATTRS["Endpoint"]["Spec"]["Ports"]:
                            PAYLOAD["PORT_MAPPING"].append(dict({"PROTOCOL":str(mapping["Protocol"]),"IP":SELF_IP,"Port":mapping["PublishedPort"]}))
                    notify_consul(PAYLOAD)
                else:
                    PAYLOAD["CMD"] = "deregister"
                    notify_consul(PAYLOAD)

def fetch_container_details(id):
    return DOCKER_CLIENT.containers.get(id)

def fetch_service_details(id):
    return DOCKER_CLIENT.services.get(service_id=id)

def check_consul_connection():
    try:
        if requests.get(CONFIG["consul"],timeout=2).status_code != 200:
            print("Unable to contact consul service on " + str(CONFIG["consul"]))
            exit(1)
    except requests.exceptions.ConnectionError as err:
        print("Unable to contact consul service on " + str(CONFIG["consul"]) + ".\n" + str(err))
        exit(1)

def notify_consul(payload):
    headers = {"Content-type": "application/json"}
    data = dict()
    data["Check"] = payload["Check"]
    data["Tags"] = payload["Tags"]
    data["Name"] = payload["Service"]
    data["EnableTagOverride"] = payload["EnableTagOverride"]
    if "PORT_MAPPING" in payload:
        for mapping in payload["PORT_MAPPING"]:
            data["ID"] = payload["ID"] + "_" + str(mapping["PROTOCOL"]) + "_" + str(mapping["Port"])
            data["Address"] = mapping["IP"]
            data["Port"] = int(mapping["Port"])
            data["Check"][mapping["PROTOCOL"]] = str(data["Address"] + ":" + str(data["Port"]))
            print(json.dumps(data))
            resp = requests.put(url=CONFIG["consul"] + "/v1/agent/service/" + payload["CMD"],json=data,headers=headers)
            print(resp.reason)
    #print(json.dumps(payload))



if __name__ == "__main__":
    init()