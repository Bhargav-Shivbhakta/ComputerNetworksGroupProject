/* dumbbell-sim.cc (SPRCC Step 1: ns-3 testbed + logging + simple predictive controller)
   - Dumbbell topology
   - UDP OnOff sender -> UDP PacketSink
   - Logs: throughput + queue occupancy + send_rate_mbps (CSV time-series)
   - Per-run outputs controlled by runTag
   - Controller modes:
       --controller=fixed : fixed sendRate (baseline)
       --controller=pred  : simple predictive controller using queue trend
*/

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"
#include "ns3/flow-monitor-module.h"

#include <fstream>
#include <string>
#include <iomanip>
#include <cctype>
#include <algorithm>
#include <cstdlib>   // system()

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("DumbbellSim");

// Real-time rx byte counter (trace callback)
static uint64_t g_rxBytes = 0;

// Current sender rate (Mbps) for logging
static double g_sendRateMbps = 0.0;

static void
OnPacketRx (Ptr<const Packet> p, const Address &addr)
{
  g_rxBytes += p->GetSize ();
}

// Parse "50p" (packets) -> bytes via pktSize, or "20000B"/"20000b" -> bytes, or fallback 0
static uint32_t
ParseQueueMaxBytes (const std::string& qMaxStr, uint32_t pktSize)
{
  if (qMaxStr.empty ())
    {
      return 0;
    }

  std::string s = qMaxStr;
  s.erase (std::remove_if (s.begin (), s.end (), ::isspace), s.end ());

  if (!s.empty () && (s.back () == 'p' || s.back () == 'P'))
    {
      std::string num = s.substr (0, s.size () - 1);
      try
        {
          uint32_t p = static_cast<uint32_t> (std::stoul (num));
          return p * pktSize;
        }
      catch (...)
        {
          return 0;
        }
    }

  // ends with B/b -> bytes
  if (!s.empty () && (s.back () == 'b' || s.back () == 'B'))
    {
      std::string num = s.substr (0, s.size () - 1);
      try
        {
          return static_cast<uint32_t> (std::stoul (num));
        }
      catch (...)
        {
          return 0;
        }
    }

  // pure number => treat as bytes
  try
    {
      return static_cast<uint32_t> (std::stoul (s));
    }
  catch (...)
    {
      return 0;
    }
}

static void
SetSenderRate (Ptr<OnOffApplication> app, double sendMbps)
{
  if (sendMbps < 0.0)
    {
      sendMbps = 0.0;
    }
  g_sendRateMbps = sendMbps;

  // OnOffApplication uses DataRate (bits/s)
  uint64_t bps = static_cast<uint64_t> (sendMbps * 1e6);
  app->SetAttribute ("DataRate", DataRateValue (DataRate (bps)));
}

// Time-series logger (every intervalSec)
static void
LogTimeseriesCsv (Ptr<Queue<Packet>> q,
                  std::ofstream* out,
                  double intervalSec,
                  uint32_t* sampleCount)
{
  static uint64_t lastRx = 0;

  uint64_t curRx = g_rxBytes;
  uint64_t delta = curRx - lastRx;
  lastRx = curRx;

  double kbps = (delta * 8.0) / (intervalSec * 1000.0);
  double t = Simulator::Now ().GetSeconds ();

  uint32_t qPackets = q ? q->GetNPackets () : 0;
  uint32_t qBytes   = q ? q->GetNBytes () : 0;

  (*out) << std::fixed << std::setprecision (3)
         << t << "," << curRx << "," << kbps << "," << qPackets << "," << qBytes << ","
         << std::setprecision (6) << g_sendRateMbps << "\n";

  (*sampleCount)++;
  if ((*sampleCount % 10) == 0)
    {
      NS_LOG_INFO ("t=" << t
                        << " rxBytes=" << curRx
                        << " instKbps=" << kbps
                        << " qPackets=" << qPackets
                        << " qBytes=" << qBytes
                        << " sendMbps=" << g_sendRateMbps);
    }

  Simulator::Schedule (Seconds (intervalSec),
                       &LogTimeseriesCsv,
                       q, out, intervalSec, sampleCount);
}

// Simple predictive trend controller:
// - Track queue_bytes(t) and queue_bytes(t-interval)
// - Predict queue_bytes at t+horizon via linear trend
// - Adjust send rate toward keeping predicted queue below target fraction of max queue
static void
ControlStep (Ptr<OnOffApplication> app,
             Ptr<Queue<Packet>> q,
             double intervalSec,
             double horizonSec,
             uint32_t qMaxBytes,
             double targetQFrac,
             double minSendMbps,
             double maxSendMbps)
{
  static bool hasPrev = false;
  static uint32_t prevQBytes = 0;

  uint32_t curQBytes = q ? q->GetNBytes () : 0;

  double predQ = static_cast<double> (curQBytes);
  if (hasPrev && intervalSec > 0.0)
    {
      double slope = (static_cast<double> (curQBytes) - static_cast<double> (prevQBytes)) / intervalSec; // bytes/s
      predQ = static_cast<double> (curQBytes) + slope * horizonSec;
      if (predQ < 0.0)
        {
          predQ = 0.0;
        }
    }

  prevQBytes = curQBytes;
  hasPrev = true;

  // Target queue level in bytes
  double targetQ = targetQFrac * static_cast<double> (qMaxBytes);

  // Control law:
  // err positive => increase, negative => decrease
  double err = targetQ - predQ;

  // Conservative gain (tunable)
  double gain = 0.00002; // Mbps per byte error
  double next = g_sendRateMbps + gain * err;

  if (next < minSendMbps) next = minSendMbps;
  if (next > maxSendMbps) next = maxSendMbps;

  SetSenderRate (app, next);

  Simulator::Schedule (Seconds (intervalSec),
                       &ControlStep,
                       app, q,
                       intervalSec, horizonSec,
                       qMaxBytes, targetQFrac,
                       minSendMbps, maxSendMbps);
}

int
main (int argc, char *argv[])
{
  Time::SetResolution (Time::NS);
  LogComponentEnable ("DumbbellSim", LOG_LEVEL_INFO);

  // Scenario params
  std::string bottleneckRate  = "5Mbps";
  std::string p2pDelay        = "2ms";
  std::string bottleneckDelay = "20ms";
  std::string queueMax        = "50p";
  std::string sendRate        = "6Mbps";
  uint32_t pktSize = 1200;
  double simTime = 10.0;

  // Logging params
  double intervalSec = 0.1;
  std::string runTag = "run0";
  std::string baseDir = "/home/bhargav/CNGP";

  // Controller params
  std::string controller = "fixed"; // fixed | pred
  double minSendMbps = 0.5;
  double maxSendMbps = 12.0;
  double targetQFrac = 0.8;
  double horizonSec  = 1.0;

  CommandLine cmd;
  cmd.AddValue ("runTag", "Unique run tag for outputs", runTag);
  cmd.AddValue ("bRate", "Bottleneck data rate (e.g., 5Mbps)", bottleneckRate);
  cmd.AddValue ("pDelay", "Edge link delay", p2pDelay);
  cmd.AddValue ("bDelay", "Bottleneck delay", bottleneckDelay);
  cmd.AddValue ("qMax", "Bottleneck queue MaxSize (e.g., 50p or 100p)", queueMax);
  cmd.AddValue ("sendRate", "Baseline sender rate (e.g., 6Mbps)", sendRate);
  cmd.AddValue ("pktSize", "Packet size bytes", pktSize);
  cmd.AddValue ("simTime", "Simulation time seconds", simTime);
  cmd.AddValue ("interval", "Logging interval seconds", intervalSec);

  cmd.AddValue ("controller", "Controller mode: fixed | pred", controller);
  cmd.AddValue ("minSendMbps", "Minimum send rate (Mbps)", minSendMbps);
  cmd.AddValue ("maxSendMbps", "Maximum send rate (Mbps)", maxSendMbps);
  cmd.AddValue ("targetQFrac", "Target queue fraction of max (0..1)", targetQFrac);
  cmd.AddValue ("horizonSec", "Prediction horizon (seconds)", horizonSec);

  cmd.Parse (argc, argv);

  // Output paths
  std::string csvPath  = baseDir + "/data/raw/" + runTag + ".csv";
  std::string flowPath = baseDir + "/results/"  + runTag + "_flow.txt";

  // Create output directories
  {
    int rc1 = std::system (("mkdir -p " + baseDir + "/data/raw").c_str ());
    if (rc1 != 0)
      {
        NS_LOG_WARN ("mkdir failed for " << baseDir << "/data/raw rc=" << rc1);
      }

    int rc2 = std::system (("mkdir -p " + baseDir + "/results").c_str ());
    if (rc2 != 0)
      {
        NS_LOG_WARN ("mkdir failed for " << baseDir << "/results rc=" << rc2);
      }
  }

  // Reset counters
  g_rxBytes = 0;

  // Nodes
  NodeContainer leftHost, rightHost, leftRouter, rightRouter;
  leftHost.Create (1);
  rightHost.Create (1);
  leftRouter.Create (1);
  rightRouter.Create (1);

  // Links
  PointToPointHelper edge;
  edge.SetDeviceAttribute ("DataRate", StringValue ("100Mbps"));
  edge.SetChannelAttribute ("Delay", StringValue (p2pDelay));

  PointToPointHelper bottleneck;
  bottleneck.SetDeviceAttribute ("DataRate", StringValue (bottleneckRate));
  bottleneck.SetChannelAttribute ("Delay", StringValue (bottleneckDelay));
  bottleneck.SetQueue ("ns3::DropTailQueue", "MaxSize", StringValue (queueMax));

  // Devices
  NetDeviceContainer d_h_l = edge.Install (leftHost.Get (0), leftRouter.Get (0));
  NetDeviceContainer d_lr  = bottleneck.Install (leftRouter.Get (0), rightRouter.Get (0));
  NetDeviceContainer d_r_r = edge.Install (rightRouter.Get (0), rightHost.Get (0));

  // Access bottleneck queue
  Ptr<PointToPointNetDevice> nd0 = DynamicCast<PointToPointNetDevice> (d_lr.Get (0));
  Ptr<Queue<Packet>> bottleneckQueue = nullptr;
  if (nd0)
    {
      bottleneckQueue = nd0->GetQueue ();
    }
  if (bottleneckQueue == nullptr)
    {
      NS_FATAL_ERROR ("Could not access bottleneck queue (queue is null).");
    }

  // Queue max bytes for controller
  uint32_t qMaxBytes = ParseQueueMaxBytes (queueMax, pktSize);
  if (qMaxBytes == 0)
    {
      qMaxBytes = 50 * pktSize;
      NS_LOG_WARN ("qMaxBytes parse failed; using fallback " << qMaxBytes);
    }

  // Internet
  InternetStackHelper internet;
  internet.InstallAll ();

  // IPs
  Ipv4AddressHelper ipv4;

  ipv4.SetBase ("10.1.1.0", "255.255.255.0");
  Ipv4InterfaceContainer i_h_l = ipv4.Assign (d_h_l);

  ipv4.SetBase ("10.1.2.0", "255.255.255.0");
  Ipv4InterfaceContainer i_lr = ipv4.Assign (d_lr);

  ipv4.SetBase ("10.1.3.0", "255.255.255.0");
  Ipv4InterfaceContainer i_r_r = ipv4.Assign (d_r_r);

  Ipv4GlobalRoutingHelper::PopulateRoutingTables ();

  // Sink
  uint16_t sinkPort = 8080;
  PacketSinkHelper sinkHelper ("ns3::UdpSocketFactory",
                               InetSocketAddress (Ipv4Address::GetAny (), sinkPort));
  ApplicationContainer sinkApps = sinkHelper.Install (rightHost.Get (0));
  sinkApps.Start (Seconds (0.0));
  sinkApps.Stop (Seconds (simTime + 1.0));

  Ptr<PacketSink> sink = DynamicCast<PacketSink> (sinkApps.Get (0));
  if (sink == nullptr)
    {
      NS_FATAL_ERROR ("PacketSink cast failed.");
    }
  sink->TraceConnectWithoutContext ("Rx", MakeCallback (&OnPacketRx));

  // Sender
  Address sinkAddress (InetSocketAddress (i_r_r.GetAddress (1), sinkPort));
  OnOffHelper onoff ("ns3::UdpSocketFactory", sinkAddress);
  onoff.SetAttribute ("DataRate", DataRateValue (DataRate (sendRate)));
  onoff.SetAttribute ("PacketSize", UintegerValue (pktSize));
  onoff.SetAttribute ("StartTime", TimeValue (Seconds (0.1)));
  onoff.SetAttribute ("StopTime", TimeValue (Seconds (simTime)));

  ApplicationContainer senderApps = onoff.Install (leftHost.Get (0));
  Ptr<OnOffApplication> sender = DynamicCast<OnOffApplication> (senderApps.Get (0));
  if (sender == nullptr)
    {
      NS_FATAL_ERROR ("OnOffApplication cast failed.");
    }

  // Init send rate from sendRate string
  g_sendRateMbps = 1.0;
  try
    {
      DataRate dr (sendRate);
      g_sendRateMbps = static_cast<double> (dr.GetBitRate ()) / 1e6;
    }
  catch (...)
    {
      NS_LOG_WARN ("Could not parse sendRate=" << sendRate << " starting at 1 Mbps");
    }
  SetSenderRate (sender, g_sendRateMbps);

  // Open CSV
  std::ofstream csv (csvPath, std::ios::out | std::ios::trunc);
  if (!csv.is_open ())
    {
      NS_FATAL_ERROR ("Could not open CSV: " << csvPath);
    }

  // Metadata header
  csv << "# runTag=" << runTag << "\n";
  csv << "# bRate=" << bottleneckRate
      << " pDelay=" << p2pDelay
      << " bDelay=" << bottleneckDelay
      << " qMax=" << queueMax
      << " sendRate=" << sendRate
      << " pktSize=" << pktSize
      << " simTime=" << simTime
      << " interval=" << intervalSec
      << " controller=" << controller
      << " minSendMbps=" << minSendMbps
      << " maxSendMbps=" << maxSendMbps
      << " targetQFrac=" << targetQFrac
      << " horizonSec=" << horizonSec
      << "\n";

  csv << "t_sec,rx_bytes_total,throughput_kbps,queue_packets,queue_bytes,send_rate_mbps\n";
  csv.flush ();

  // Start logger at t=interval
  uint32_t sampleCount = 0;
  Simulator::Schedule (Seconds (intervalSec),
                       &LogTimeseriesCsv,
                       bottleneckQueue, &csv, intervalSec, &sampleCount);

  // Start controller loop
  if (controller == "fixed")
    {
      // fixed: do nothing (rate already set)
    }
  else if (controller == "pred")
    {
      Simulator::Schedule (Seconds (0.2),
                           &ControlStep,
                           sender, bottleneckQueue,
                           intervalSec, horizonSec,
                           qMaxBytes, targetQFrac,
                           minSendMbps, maxSendMbps);
    }
  else
    {
      NS_FATAL_ERROR ("Unknown controller=" << controller << " (use fixed|pred)");
    }

  // FlowMonitor
  FlowMonitorHelper fmHelper;
  Ptr<FlowMonitor> flowmon = fmHelper.InstallAll ();

  Simulator::Stop (Seconds (simTime + 0.5));
  Simulator::Run ();

  // Flow stats -> file
  std::ofstream flowOut (flowPath, std::ios::out | std::ios::trunc);
  if (!flowOut.is_open ())
    {
      NS_FATAL_ERROR ("Could not open flow summary: " << flowPath);
    }

  flowmon->CheckForLostPackets ();
  Ptr<Ipv4FlowClassifier> classifier = DynamicCast<Ipv4FlowClassifier> (fmHelper.GetClassifier ());
  auto stats = flowmon->GetFlowStats ();

  for (auto &flow : stats)
    {
      auto t = classifier->FindFlow (flow.first);

      auto printOne = [&](std::ostream& os)
      {
        os << "Flow " << flow.first << " (" << t.sourceAddress << " -> " << t.destinationAddress << ")\n";
        os << "  Tx Packets: " << flow.second.txPackets << "  Rx Packets: " << flow.second.rxPackets << "\n";
        os << "  Throughput (kbps): " << (flow.second.rxBytes * 8.0 / (simTime * 1000.0)) << "\n";
        if (flow.second.rxPackets > 0)
          {
            os << "  Mean delay (s): " << (flow.second.delaySum.GetSeconds () / flow.second.rxPackets) << "\n";
          }
        os << "  Lost Packets: " << flow.second.lostPackets << "\n";
      };

      printOne (std::cout);
      printOne (flowOut);
    }

  csv.close ();
  flowOut.close ();

  Simulator::Destroy ();
  return 0;
}
