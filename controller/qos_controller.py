"""
QoS SDN controller (Ryu, OpenFlow 1.3)

Three operating modes, chosen with the QOS_MODE environment variable:
    fifo      Baseline. Every flow goes to ONE shared queue (queue 0). No priority.
    static    Fixed priority. Priority queues are always on (HIGH->q1, MEDIUM->q2, LOW->q3).
    adaptive  Dynamic (default). The controller polls the switch every second, measures the
              load offered to the 2 Mbps bottleneck and switches priority queues ON only
              while the link is congested, and back OFF when the congestion is over.

Policy can also be changed while traffic is running through a small REST API (port 8080):
    GET  /qos/status                    current mode, congestion state, load and policy
    PUT  /qos/policy/<udp_port>/<class> move a flow to HIGH / MEDIUM / LOW / BEST_EFFORT
    e.g. curl -X PUT http://127.0.0.1:8080/qos/policy/5003/HIGH

Every load sample is appended to the CSV file in QOS_LOG (if set) so it can be plotted.
"""
import json, os, time

from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.ofproto import ofproto_v1_3
from webob import Response

SERVER_IP, SERVER_PORT = "10.0.0.4", 4          # receiver h4 is on switch port 4 (the bottleneck)
CAPACITY_KBPS = 2000                            # bottleneck rate (set in topo_run.py)
ON_LEVEL, OFF_LEVEL = 0.90, 0.75                # congestion ON above 90 %, OFF below 75 % of capacity
OFF_SAMPLES = 3                                 # need 3 calm samples in a row before switching OFF
POLL_S = 1.0                                    # monitoring interval (seconds)
QOS_RULE_PRIO = 100                             # priority of the per-class flow rules

CLASS_QUEUE = {"HIGH": 1, "MEDIUM": 2, "LOW": 3, "BEST_EFFORT": 0}
DEFAULT_POLICY = {5001: "HIGH", 5002: "MEDIUM", 5003: "LOW"}   # udp dst port -> class


class QoSController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode = os.environ.get("QOS_MODE", "adaptive")
        if self.mode not in ("fifo", "static", "adaptive"):
            raise ValueError("QOS_MODE must be fifo, static or adaptive")
        self.log_path = os.environ.get("QOS_LOG")
        self.mac_to_port = {}
        self.dp = None                       # the single switch s1
        self.reset_state()
        kwargs["wsgi"].register(QoSRest, {"qos_app": self})
        hub.spawn(self.monitor)
        self.logger.info("QoS controller started in %s mode", self.mode.upper())

    def reset_state(self):
        self.policy = dict(DEFAULT_POLICY)
        self.congested = False
        self.calm = 0                        # consecutive samples below OFF_LEVEL
        self.prev = {}                       # port -> (rx_bytes, time) from last poll
        self.load_kbps = 0.0
        self.port_kbps = {}

    # ------------------------------------------------------------------ flow rules
    def add_flow(self, dp, prio, match, actions):
        p = dp.ofproto_parser
        inst = [p.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=prio, match=match, instructions=inst))

    def queue_for(self, cls):
        """Which switch queue a class uses right now (this is the whole QoS decision)."""
        if self.mode == "fifo":
            return 0                                  # baseline: everyone shares queue 0
        if self.mode == "adaptive" and not self.congested:
            return 0                                  # no congestion: no need to prioritise
        return CLASS_QUEUE[cls]

    def install_qos_rules(self):
        """(Re)install one rule per traffic class: match UDP dst port -> set_queue -> port 4.
        Installing a rule with the same match+priority replaces the old one."""
        dp = self.dp
        if dp is None:
            return
        p = dp.ofproto_parser
        for port, cls in sorted(self.policy.items()):
            q = self.queue_for(cls)
            m = p.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_dst=SERVER_IP, udp_dst=port)
            self.add_flow(dp, QOS_RULE_PRIO, m, [p.OFPActionSetQueue(q), p.OFPActionOutput(SERVER_PORT)])
            self.logger.info("  rule: udp/%d (%s) -> queue %d", port, cls, q)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_features(self, ev):
        dp = ev.msg.datapath; ofp = dp.ofproto; p = dp.ofproto_parser
        self.dp = dp
        self.mac_to_port.clear()
        self.reset_state()
        self.add_flow(dp, 0, p.OFPMatch(), [p.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])
        self.logger.info("switch %s connected, mode=%s", dp.id, self.mode)
        self.install_qos_rules()

    # ------------------------------------------------------------------ monitoring (dynamic part)
    def monitor(self):
        while True:
            if self.dp is not None:
                p = self.dp.ofproto_parser
                self.dp.send_msg(p.OFPPortStatsRequest(self.dp, 0, self.dp.ofproto.OFPP_ANY))
            hub.sleep(POLL_S)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def on_port_stats(self, ev):
        now = time.time()
        load = 0.0
        for s in ev.msg.body:
            if s.port_no > 0xff00:               # skip the switch's LOCAL port
                continue
            old = self.prev.get(s.port_no)
            self.prev[s.port_no] = (s.rx_bytes, now)
            if old is None or now <= old[1]:
                continue
            kbps = max(s.rx_bytes - old[0], 0) * 8 / (now - old[1]) / 1000
            self.port_kbps[s.port_no] = kbps
            if s.port_no != SERVER_PORT:         # traffic entering from h1..h3 heads to h4
                load += kbps
        self.load_kbps = load
        if self.mode == "adaptive":
            self.update_congestion(load)
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(f"{now:.3f},{load:.1f},{int(self.congested)}\n")

    def update_congestion(self, load):
        """Hysteresis: turn ON fast (one sample), turn OFF slowly (OFF_SAMPLES calm samples)."""
        if not self.congested and load > ON_LEVEL * CAPACITY_KBPS:
            self.congested = True
            self.logger.info("CONGESTION DETECTED: load %.0f kbps (%.0f%% of link) -> priority queues ON",
                             load, load / CAPACITY_KBPS * 100)
            self.install_qos_rules()
        elif self.congested:
            self.calm = self.calm + 1 if load < OFF_LEVEL * CAPACITY_KBPS else 0
            if self.calm >= OFF_SAMPLES:
                self.congested = False; self.calm = 0
                self.logger.info("CONGESTION CLEARED: load %.0f kbps -> back to best effort (queue 0)", load)
                self.install_qos_rules()

    # ------------------------------------------------------------------ runtime policy change (REST)
    def set_policy(self, port, cls):
        self.policy[port] = cls
        self.logger.info("POLICY CHANGE via REST: udp/%d -> %s", port, cls)
        self.install_qos_rules()

    def status(self):
        return dict(mode=self.mode, congested=self.congested, load_kbps=round(self.load_kbps),
                    capacity_kbps=CAPACITY_KBPS,
                    port_rx_kbps={str(k): round(v) for k, v in sorted(self.port_kbps.items())},
                    policy={str(k): dict(cls=v, queue=self.queue_for(v)) for k, v in sorted(self.policy.items())})

    # ------------------------------------------------------------------ learning switch (all other traffic)
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def on_packet_in(self, ev):
        msg = ev.msg; dp = msg.datapath; ofp = dp.ofproto; p = dp.ofproto_parser
        in_port = msg.match["in_port"]
        eth = packet.Packet(msg.data).get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        self.mac_to_port.setdefault(dp.id, {})[eth.src] = in_port
        out = self.mac_to_port[dp.id].get(eth.dst, ofp.OFPP_FLOOD)
        actions = [p.OFPActionOutput(out)]
        if out != ofp.OFPP_FLOOD:
            self.add_flow(dp, 1, p.OFPMatch(in_port=in_port, eth_dst=eth.dst, eth_src=eth.src), actions)
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                   in_port=in_port, actions=actions, data=data))


def json_response(obj, status=200):
    return Response(status=status, content_type="application/json",
                    body=(json.dumps(obj, indent=2) + "\n").encode())


class QoSRest(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.app = data["qos_app"]

    @route("qos", "/qos/status", methods=["GET"])
    def get_status(self, req, **kwargs):
        return json_response(self.app.status())

    @route("qos", "/qos/policy/{port}/{cls}", methods=["PUT", "POST"])
    def put_policy(self, req, port, cls, **kwargs):
        cls = cls.upper()
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            return json_response({"error": "port must be 1-65535"}, 400)
        if cls not in CLASS_QUEUE:
            return json_response({"error": f"class must be one of {list(CLASS_QUEUE)}"}, 400)
        self.app.set_policy(int(port), cls)
        return json_response(self.app.status())
