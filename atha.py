#!/bin/env python3
try:
    import sys
    import json
    import syslog
    import requests
except ModuleNotFoundError as err:
    print("Unable to load module. " + str(err) + ". Please activate/create virtualenv first.")
    exit(1)

CONFIG = None



def main():
    CONFIG = json.load(open(sys.argv[1]))
    syslog.syslog(syslog.LOG_INFO,"Post execution began")


if __name__ == '__main__':
    main()