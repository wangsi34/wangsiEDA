"""OpenROAD stage scripts (Nangate45, upstream OpenROAD build f12e2f47 from the cloud's conda env).

Each stage reads the previous stage's .odb checkpoint and writes its own, so any stage can be re-run
from a checkpoint (resume / rollback) without redoing upstream work.

Note: the OpenROAD-GRT build in the F2F evaluator rootfs was rejected for this demo: its global router
leaves nets without guides (even the upstream `gcd` regression fails with ANT-0008). See README.
"""

import os
FLOWDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "blocks")
from . import config as _C
PLAT = str(_C.NANGATE)   # Nangate45 平台目录，在 wangsi_config.json 中配置

PREAMBLE = f"""
read_lef {PLAT}/Nangate45_tech.lef
read_lef {PLAT}/Nangate45_stdcell.lef
read_liberty {PLAT}/Nangate45_typ.lib
"""

RC = f"""
source {PLAT}/Nangate45.rc
set_wire_rc -signal -layer metal3
set_wire_rc -clock -layer metal6
"""


def metric(name, expr):
    return f'puts "@@METRIC {name} [{expr}]"\n'


def timing_metrics(tag):
    return (metric(f"{tag}.wns_setup", "format %.4f [sta::worst_slack -max]")
            + metric(f"{tag}.tns_setup", "format %.4f [sta::total_negative_slack -max]")
            + metric(f"{tag}.wns_hold", "format %.4f [sta::worst_slack -min]"))


UNROUTED = """
set unrouted 0
foreach net [[ord::get_db_block] getNets] {
  set t [$net getSigType]
  if {$t == "POWER" || $t == "GROUND"} continue
  if {[llength [$net getITerms]] + [llength [$net getBTerms]] < 2} continue
  if {[$net getWire] == "NULL"} { incr unrouted }
}
"""


def stage_script(stage, rd, k, top="rv_alu"):
    """rd: run dir. k: knob dict (validated by guards before we get here)."""
    io = f"{rd}/pnr"
    s = PREAMBLE
    if stage == "floorplan":
        s += f"""
read_verilog {rd}/synth/netlist.v
link_design {top}
read_sdc {rd}/synth/constraints.sdc
initialize_floorplan -site FreePDK45_38x28_10R_NP_162NW_34O -utilization {k['core_utilization']} -aspect_ratio 1.0 -core_space 2.0
source {PLAT}/Nangate45.tracks
remove_buffers
tapcell -distance 120 -tapcell_master TAPCELL_X1 -endcap_master TAPCELL_X1
source {FLOWDIR}/pdn_nangate45.tcl
pdngen
place_pins -hor_layers metal3 -ver_layers metal2
""" + metric("floorplan.instances", "sta::network_instance_count") + \
            f"write_db {io}/1_floorplan.odb\n"
    elif stage == "place":
        s += f"read_db {io}/1_floorplan.odb\nread_sdc {rd}/synth/constraints.sdc\n" + RC + f"""
set_dont_use {{CLKBUF_* AOI211_X1 OAI211_X1}}
global_placement -routability_driven {'-timing_driven' if k['timing_driven'] else ''} -density {k['place_density']} -pad_left 2 -pad_right 2
estimate_parasitics -placement
repair_design
set_placement_padding -global -left 1 -right 1
detailed_placement
""" + timing_metrics("place") + metric("place.utilization_pct", "format %.1f [expr [rsz::utilization]*100]") + \
            f"write_db {io}/2_place.odb\n"
    elif stage == "cts":
        s += f"read_db {io}/2_place.odb\nread_sdc {rd}/synth/constraints.sdc\n" + RC + f"""
set_dont_use {{CLKBUF_* AOI211_X1 OAI211_X1}}
repair_clock_inverters
clock_tree_synthesis -root_buf BUF_X4 -buf_list BUF_X4 -sink_clustering_enable
repair_clock_nets
set_placement_padding -global -left 1 -right 1
detailed_placement
set_propagated_clock [all_clocks]
estimate_parasitics -placement
repair_timing -setup -slack_margin {k['setup_slack_margin_ps'] / 1000.0} -max_buffer_percent {k['max_buffer_percent']}
repair_timing -hold -slack_margin 0.02
detailed_placement
check_placement
""" + timing_metrics("cts") + metric("cts.utilization_pct", "format %.1f [expr [rsz::utilization]*100]") + \
            f"write_db {io}/3_cts.odb\n"
    elif stage == "route":
        s += f"read_db {io}/3_cts.odb\nread_sdc {rd}/synth/constraints.sdc\n" + RC + f"""
set_routing_layers -signal metal2-metal10 -clock metal6-metal10
set_global_routing_layer_adjustment metal2-metal10 {k['grt_layer_adjust']}
global_route -guide_file {io}/route.guide -congestion_iterations 50 -verbose
detailed_route -output_drc {io}/route_drc.rpt -droute_end_iter {k['droute_iters']} -verbose 1
""" + metric("route.drc_count", "detailed_route_num_drvs") + UNROUTED + metric("route.unrouted", "set unrouted") + f"""
filler_placement FILLCELL*
write_db {io}/4_route.odb
write_def {io}/4_route.def
"""
    elif stage == "signoff":
        # Independent of the stages above: fresh STA on the final checkpoint with RC extraction.
        # Reports go to stdout between @@BEGIN/@@END markers (this OpenSTA build ignores "> file").
        s += f"read_db {io}/4_route.odb\nread_sdc {rd}/synth/constraints.sdc\n" + RC + f"""
set_propagated_clock [all_clocks]
define_process_corner -ext_model_index 0 X
extract_parasitics -ext_model_file {PLAT}/Nangate45.rcx_rules
write_spef {io}/final.spef
read_spef {io}/final.spef
puts "@@BEGIN timing_setup.rpt"
report_checks -path_delay max -format full_clock_expanded -fields {{slew cap}} -digits 3
puts "@@END timing_setup.rpt"
puts "@@BEGIN timing_hold.rpt"
report_checks -path_delay min -digits 3
puts "@@END timing_hold.rpt"
puts "@@BEGIN drv.rpt"
report_check_types -max_slew -max_capacitance -max_fanout -violators
puts "@@END drv.rpt"
puts "@@BEGIN power.rpt"
report_power
puts "@@END power.rpt"
""" + timing_metrics("signoff") + UNROUTED + metric("signoff.unrouted", "set unrouted") + \
            metric("signoff.design_area_um2", "sta::format_area [rsz::design_area] 0")
    else:
        raise ValueError(stage)
    return s + "exit\n"
