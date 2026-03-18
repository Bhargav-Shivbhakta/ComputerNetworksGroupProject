/*
 * sprcc-sim.cc — placeholder ns-3 scratch file.
 * Replace with full simulation implementation (dumbbell topology, UDP sender, probes).
 *
 * This file is tracked; the full ns-3 distribution is left untracked.
 */

#include "ns3/core-module.h"
#include <iostream>

using namespace ns3;

int main (int argc, char *argv[])
{
  double bw_mbps = 5.0;
  double rtt_ms = 40.0;
  uint32_t queue_pct = 50;
  double sender_rate = 6.0;
  std::string trace_type = "step";
  std::string out_prefix = "logs/placeholder";

  CommandLine cmd;
  cmd.AddValue ("bw_mbps", "Bottleneck bandwidth (Mbps)", bw_mbps);
  cmd.AddValue ("rtt_ms", "RTT in ms", rtt_ms);
  cmd.AddValue ("queue_pct", "Queue size percent", queue_pct);
  cmd.AddValue ("sender_rate", "Sender offered rate (Mbps)", sender_rate);
  cmd.AddValue ("trace_type", "Trace type", trace_type);
  cmd.AddValue ("out_prefix", "Output prefix", out_prefix);
  cmd.Parse (argc, argv);

  std::cout << "SPRCC Placeholder: bw=" << bw_mbps << "Mbps, rtt=" << rtt_ms
            << "ms, q%=" << queue_pct << ", sender_rate=" << sender_rate
            << " Mbps, trace=" << trace_type << ", out=" << out_prefix << std::endl;

  // TODO: implement dumbbell topology, UDP app, tracing hooks, CSV logger
  return 0;
}
