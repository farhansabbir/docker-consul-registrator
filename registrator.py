#!/bin/env python3
try:
    import sys
    import json
    import docker
    import requests
    import platform
except ModuleNotFoundError as err:
    print("Unable to load module. " + str(err) + ". Please activate/create virtualenv first.")
    exit(1)

DOCKER_CLIENT = None
CONFIG = None
SELF_IP = None



def fetch_container_details(id):
    return DOCKER_CLIENT.containers.get(id)



def fetch_service_details(id):
    return DOCKER_CLIENT.services.get(service_id=id)



def get_Service_Container_Details(container):
    return DOCKER_CLIENT.services.get(service_id=container.attrs["Config"]["Labels"]["com.docker.swarm.service.id"])



def is_A_Service_Container(container=None):
    if "Labels" in container.attrs["Config"]:
        if "com.docker.swarm.service.id" in container.attrs["Config"]["Labels"]:
            return True
    return False



def generate_Payload_For_Registration(container=None):
    if not container:
        return None
    payload = dict()
    payload["Tags"] = list()
    payload["Tags"].append(container.attrs["Config"]["Image"])
    payload["Check"] = dict()
    payload["Check"]["DeregisterCriticalServiceAfter"] = "5s"
    payload["Check"]["Interval"] = "2s"
    payload["Check"]["Timeout"] = "3s"
    payload["EnableTagOverride"] = False
    if CONFIG["sidecar_enable"] == 1:
        payload["Connect"] = {"SidecarService":{}}
    payload["ID"] = container.id
    if is_A_Service_Container(container=container):
        # print("Service container: " + str(container.id))
        svc = get_Service_Container_Details(container=container).attrs
        payload["Name"] = svc["Spec"]["Name"]
        payload["Meta"] = svc["Spec"]["Labels"]
        payload["Tags"].append(str(payload["Meta"]))
        if CONFIG["consul_registration_label"] not in payload["Meta"]:
            return None
        if len(svc["Endpoint"]["Ports"]) > 1:
            print("WARNING! Multiple port bindings found. Using single exposed port only. This may impact service registration.")
        payload["Check"][svc["Endpoint"]["Ports"][0]["Protocol"]] = CONFIG["self_ip"] + ":" + str(svc["Endpoint"]["Ports"][0]["PublishedPort"])
        payload["Address"] = CONFIG["self_ip"]
        payload["Port"] = int(svc["Endpoint"]["Ports"][0]["PublishedPort"])
    else:
        # print("Regular container: " + str(container.id))
        payload["Name"] = container.name
        payload["Meta"] = container.attrs["Config"]["Labels"]
        payload["Tags"].append(str(payload["Meta"]))
        payload["Tags"].append(container.attrs["Config"]["Image"] + "@" + container.attrs["Image"])
        if CONFIG["consul_registration_label"] not in payload["Meta"]:
            return None
        for portproto,mapping in container.attrs["HostConfig"]["PortBindings"].items():
            if len(mapping) > 1:
                print("Warning! Multiple ports exposed. Will select only first one: " + str(mapping[0]))
            IP = CONFIG["self_ip"]
            if mapping[0]["HostIp"] != "":
                IP = mapping[0]["HostIp"]
            payload["Check"][portproto[portproto.index("/")+1:]] = str(IP) + ":" + str(mapping[0]["HostPort"])
            payload["Port"] = int(mapping[0]["HostPort"])
            payload["Address"] = str(IP)
    return payload



def deregister_Service_From_Consul(id=None):
    headers = {"Content-type": "application/json"}
    resp = requests.put(CONFIG["consul"] + "/v1/agent/service/deregister/" + id, headers=headers)
    if resp.status_code == 200:
        print("Successfully deregistered container " + id +  " from consul.")
    else:
        print("Unable to deregister container " + id +  " from consul because " + str(resp.text))



def register_Service_To_Consul(container=None):
    data = generate_Payload_For_Registration(container=container)
    if not data:
        print("Container '" + str(container.name) + " (" + str(container.id) + ")' is not labelled to register with consul. Skipping.")
        return None
    headers = {"Content-type": "application/json"}
    resp = requests.put(CONFIG["consul"] + "/v1/agent/service/register",json=data, headers=headers)
    if resp.status_code == 200:
        print("Successfully registered container (" + str(container.name) + " (" + str(container.id) + ")' to consul.")
    else:
        print("Unable to register container '" + str(container.name) + " (" + str(container.id) + ")' to consul.")
        print(resp.text)



def get_Container_Attribs(container):
    return DOCKER_CLIENT.containers.get(container.id)



def get_Registered_Services_From_Consul(service=None):
    resp = None
    if not service:
        resp = requests.get(CONFIG["consul"] + "/v1/agent/services")
    else:
        resp = requests.get(CONFIG["consul"] + "/v1/agent/service/"+service)
    if resp.status_code==404:
        return 404
    return resp.json()



def is_Container_Registered_To_Consul(container=None):
    # payload = generate_Payload_For_Registration(container=container)
    for servicename, servicedef in get_Registered_Services_From_Consul().items():
        if "ingress-service" in servicename or "sidecar" in servicename.lower():
            continue
        if container.id == servicedef["ID"]:
            return True
    return False



def get_Container_List_From_Host(status="running"):
    return (DOCKER_CLIENT.containers.list(filters={"status":status}))



def cleanup():
    print("Running cleanup.")
    print("Validating with running containers first.")
    for container in DOCKER_CLIENT.containers.list():
        if not is_Container_Registered_To_Consul(container=container):
            register_Service_To_Consul(container)
        else:
            print("Container '" + str(container.name) + " (" + str(container.id) + ")' is already registered to consul. Skipping.")
    print("Cleaning up old stale/exited containers. Found total: " + str(len(get_Container_List_From_Host(status="exited"))))
    for container in get_Container_List_From_Host(status="exited"):
        if is_Container_Registered_To_Consul(container=container):
            print("Container is still registered with consul. Deregistering.")
            deregister_Service_From_Consul(container.id)
        else:
            print("Please remove old container " + container.id + " from system.")
            # container.remove()
    print("Cleaning up for registered containers from consul that don't exist in system anymore.")

    for svc_id in (get_Registered_Services_From_Consul()).keys():
        if svc_id in ["ingress-service","sidecar"]:
            continue
        if (svc_id not in [container.id for container in get_Container_List_From_Host()]):
            print("Deregistering non-existant container '" + svc_id + "' from consul")
            deregister_Service_From_Consul(svc_id)

    print("Initial cleanup complete.")
        

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
            if event["status"] == "start":
                if not (is_Container_Registered_To_Consul(fetch_container_details(event["id"]))):
                    cleanup()
                    register_Service_To_Consul(fetch_container_details(event["id"]))
            if event["status"] == "destroy":
                deregister_Service_From_Consul(event["id"])
                cleanup()



def init():
    global DOCKER_CLIENT
    global CONFIG
    global SELF_IP
    try:
        CONFIG = json.load(open(sys.argv[1]))
        DOCKER_CLIENT = docker.DockerClient(base_url='unix:/' + str(CONFIG["docker"]))
        SELF_IP = CONFIG["self_ip"]
        check_consul_connection()

    except IndexError:
        print("You need to provide a configuration file (config.json, typically) as an argument with this script. ")
        exit(1)

    except docker.errors.DockerException as dockererror:
        print("Unable to connect to docker daemon. Reason: " + str(dockererror))
        exit(1)
    except KeyError as err:
        print("Config parsing error. Key not found in config: " + str(err))
        exit(1)

    cleanup()
    event_loop()




if __name__ == "__main__":
    init()
