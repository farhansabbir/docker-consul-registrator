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

def cleanup():
    my_services = get_Registered_Services_From_Consul()
    for service in my_services.keys():
        if "sidecar-proxy" in service or "ingress-service" in service:
            continue
        print(get_Registered_Services_From_Consul(service=service))
        #print(fetch_container_details(id=my_services["ID"]))


def get_Registered_Services_From_Consul(service=None):
    resp = None
    if not service:
        resp = requests.get(CONFIG["consul"] + "/v1/agent/services")
    else:
        resp = requests.get(CONFIG["consul"] + "/v1/agent/service/"+service)
    return resp.json()

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


def event_loop():
    global SELF_IP
    for event in DOCKER_CLIENT.events(decode=True):
        if (event["Type"]=="container"):
            if event["status"] == "start" or event["status"] == "destroy":
                PAYLOAD = dict()
                PAYLOAD["ID"] = event["id"] # container ID
                PAYLOAD["Address"] = CONFIG["self_ip"]
                PAYLOAD["Check"] = dict()
                PAYLOAD["Check"]["DeregisterCriticalServiceAfter"] = "5s"
                PAYLOAD["Check"]["Interval"] = "2s"
                PAYLOAD["Check"]["Timeout"] = "3s"
                PAYLOAD["Tags"] = list()
                PAYLOAD["EnableTagOverride"] = False
                PAYLOAD["Node"] = platform.node()
                if "Attributes" in event["Actor"]:
                    ATTRS = event["Actor"]["Attributes"]
                    PAYLOAD["Service"] = ATTRS["name"]
                    PAYLOAD["IsService"] = False
                    if "com.docker.swarm.service.id" in ATTRS:
                        PAYLOAD["ID"] = ATTRS["name"]
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
                        if "sidecar" in EXT_ATTRS["Config"]["Labels"]:
                            if str(EXT_ATTRS["Config"]["Labels"]["sidecar"]) != "": 
                                PAYLOAD["sidecar"] = str(EXT_ATTRS["Config"]["Labels"]["sidecar"])
                        if "consul" in EXT_ATTRS["Config"]["Labels"]:
                            if str(EXT_ATTRS["Config"]["Labels"]["consul"]).lower() == "yes":
                                PAYLOAD["labels"] = EXT_ATTRS["Config"]["Labels"]
                            else:
                                print("Service '" + PAYLOAD["Service"] + "' is not marked to register in consul. Ignoring.")
                                continue
                        else:
                            print("Service '" + PAYLOAD["Service"] + "' is not marked to register in consul. Ignoring.")
                            continue
                        for mapsrc,mapdst in EXT_ATTRS["NetworkSettings"]["Ports"].items():
                            PROTOCOL = "TCP"
                            if "udp" in mapsrc:
                                PROTOCOL = "UDP"
                            if mapdst[0]["HostIp"] != "0.0.0.0":
                                IP = mapdst[0]["HostIp"]
                            else:
                                IP = SELF_IP
                            PAYLOAD["PORT_MAPPING"].append(dict({"PROTOCOL":PROTOCOL,"IP":IP,"Port":mapdst[0]["HostPort"]}))
                    else:
                        # this is a service
                        # get port mapping info from service definition
                        ATTRS = fetch_service_details(PAYLOAD["ServiceID"]).attrs
                        if "sidecar" in ATTRS["Spec"]["Labels"]:
                            if str(ATTRS["Spec"]["Labels"]["sidecar"]) != "":
                                PAYLOAD["sidecar"] = ATTRS["Spec"]["Labels"]["sidecar"]

                        if "consul" in ATTRS["Spec"]["Labels"]:
                            if str(ATTRS["Spec"]["Labels"]["consul"]).lower() == "yes":
                                PAYLOAD["labels"] = ATTRS["Spec"]["Labels"]
                                for mapping in ATTRS["Endpoint"]["Ports"]:
                                    PAYLOAD["PORT_MAPPING"].append(dict({"PROTOCOL":str(mapping["Protocol"]),"IP":SELF_IP,"Port":mapping["PublishedPort"]}))
                            else:
                                print("Service '" + PAYLOAD["Service"] + "' is not marked to register in consul. Ignoring.")
                                continue
                        else:
                            print("Service '" + PAYLOAD["Service"] + "' is not marked to register in consul. Ignoring.")
                            continue    
                    notify_consul(PAYLOAD)
                else:
                    PAYLOAD["CMD"] = "deregister"
                    if PAYLOAD["IsService"]:
                        PAYLOAD["ID"]
                    notify_consul(PAYLOAD)

def notify_consul(payload):
    if payload["CMD"] == "register":
        headers = {"Content-type": "application/json"}
        data = dict()
        data["Check"] = payload["Check"]
        data["Tags"] = payload["Tags"]
        data["Name"] = payload["Service"]
        data["Meta"] = dict()
        if "labels" in payload:
            for key,value in payload["labels"].items():
                data["Meta"][key] = value
        data["EnableTagOverride"] = payload["EnableTagOverride"]
        if "PORT_MAPPING" in payload:
            for mapping in payload["PORT_MAPPING"]:
                data["ID"] = payload["ID"] + "_" + str(mapping["PROTOCOL"]) + "_" + str(mapping["Port"])
                data["Address"] = mapping["IP"]
                data["Port"] = int(mapping["Port"])
                data["Check"][mapping["PROTOCOL"]] = str(data["Address"] + ":" + str(data["Port"]))
                resp = requests.put(url=CONFIG["consul"] + "/v1/agent/service/" + payload["CMD"],json=data,headers=headers)
                if resp.status_code == 200:
                    print("Successfully registered service with payload: " + str(payload))
                else:
                    print("Unable to register service with payload " + str(data))
                    print(resp.reason)
    elif payload["CMD"] == "deregister":
        headers = {"Content-type": "application/json"}
        resp = requests.get(url=CONFIG["consul"] + "/v1/agent/services",headers=headers).json()
        for key in resp.keys():
            if payload["ID"] in key:
                resp = requests.put(url=CONFIG["consul"] + "/v1/agent/service/" + payload["CMD"] + "/" + str(key),headers=headers)
                if resp.status_code == 200:
                    print("Successfully deregistered service with payload " + str(payload))
                    break
        else:
            print("Unable to deregister. Consul is not aware of this service definition " + str(payload))

        # resp = requests.put(url=CONFIG["consul"] + "/v1/agent/service/" + payload["CMD"] + "/" + str(payload["ID"]),headers=headers)
        # if resp.status_code == 200:
        #     print("Successfully deregistered service with payload " + str(payload))



def init():
    global DOCKER_CLIENT
    global CONFIG
    global SELF_IP
    try:
        CONFIG = json.load(open(sys.argv[1]))
        DOCKER_CLIENT = docker.DockerClient(base_url='unix:/' + str(CONFIG["docker"]))
        SELF_IP = CONFIG["self_ip"]
        check_consul_connection()
        cleanup()
        # for container in (DOCKER_CLIENT.containers.list(filters={"status":"running"})):
        #     print(container.attrs["Id"])
        
        
    except IndexError:
        print("You need to provide a configuration file (config.json, typically) as an argument with this script. ")
        exit(1)

    except docker.errors.DockerException as dockererror:
        print("Unable to connect to docker daemon. Reason: " + str(dockererror))
        exit(1)
    except KeyError as err:
        print("Config parsing error. Key not found in config: " + str(err))
        exit(1)

    event_loop()




if __name__ == "__main__":
    init()
