This is the drop-in python for container registration in consul I used in my system.

Works with python3.9.4 (should work on any python3), docker 20.10.6, build 370c289 and consul 1.9.5

Usage: 
- Run as systemd service on the host machine.
- Start docker service or standalone container with --label consul='yes' flag to trigger the registration for that container
- If any service or container is not started using that flag, it's ignored to register

Excuse the stranded, unused objects. Will cleanup later
Excuse some of the bad codes. Had to get it work ASAP. 
Fork it and make it better. Peace!