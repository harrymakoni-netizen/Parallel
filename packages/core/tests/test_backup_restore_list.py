"""Restore filtering: what the target server provides is skipped, everything else is restored."""

from parallel_core.backup import restore_list

TOC = """;
; Archive created at 2026-10-23 07:20:00 UTC
2; 3079 16385 EXTENSION - postgis
5821; 0 0 COMMENT - EXTENSION postgis
9; 2615 18213 SCHEMA - tiger parallel
10; 2615 18300 SCHEMA - topology parallel
5822; 0 0 COMMENT - SCHEMA topology parallel
11; 2615 18400 SCHEMA - analytics parallel
230; 1259 16700 TABLE public project parallel
5600; 0 16700 TABLE DATA public project parallel
5601; 0 18500 TABLE DATA tiger loader_platform parallel
5602; 0 16390 TABLE DATA public spatial_ref_sys parallel
"""


def test_restore_list_skips_what_the_target_provides():
    keep = restore_list(TOC, {"public", "tiger", "topology"}, {("tiger", "loader_platform"), ("public", "spatial_ref_sys")})
    body = "\n".join(keep)
    for gone in ("EXTENSION - postgis", "COMMENT - EXTENSION", "SCHEMA - tiger", "SCHEMA - topology",
                 "COMMENT - SCHEMA topology", "tiger loader_platform", "spatial_ref_sys"):
        assert gone not in body
    # application schema objects and data, and schemas the target lacks, are restored
    for kept in ("SCHEMA - analytics", "TABLE public project", "TABLE DATA public project"):
        assert kept in body
