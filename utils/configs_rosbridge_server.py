from string import Template

ROSBRIDGE_WS_IPADDR = 'localhost'
ROSBRIDGE_WS_PORT = 9090

ROSBRIDGE_WS_URL = Template("ws://$IPADDR:$PORT")

# rostopic that injects chat message from SIGNAL app
# DOWN into acoustic network
CHAT_DOWNLINK_TOPIC = "/chat_input"

# rostopic that extracts chat message from acoustic network
# UP into SIGNAL app
CHAT_UPLINK_TOPIC = "/chat_output"
CHAT_UPLINK_TYPE = "std_msgs/String"
