# Spec: rv_alu — RV32-style ALU/MUL execute unit (W=16 demo width)

Interface (registered in → registered out, 1-cycle latency after input register):

| port | dir | width | meaning |
|---|---|---|---|
| clk | in | 1 | clock, target period from constraints.json |
| rst_n | in | 1 | async active-low reset |
| in_valid | in | 1 | operands valid |
| op | in | 4 | opcode (table below) |
| a, b | in | W | operands |
| out_valid | out | 1 | result valid (2 cycles after in_valid) |
| y | out | W | result |

| op | name | function |
|---|---|---|
| 0 | ADD | a + b |
| 1 | SUB | a - b |
| 2 | AND | a & b |
| 3 | OR | a \| b |
| 4 | XOR | a ^ b |
| 5 | SLL | a << b[3:0] |
| 6 | SRL | a >> b[3:0] (logical) |
| 7 | SRA | a >>> b[3:0] (arithmetic, sign-extended) |
| 8 | SLT | signed(a) < signed(b) |
| 9 | SLTU | a < b |
| 10 | MUL | low W bits of a*b |
| 11 | MULHU | high W bits of unsigned a*b |
| other | — | 0 |

Sign-off targets: functional sim PASS (frozen testbench), setup WNS >= 0, hold WNS >= 0,
DRC (DRT DRV) == 0, no unrouted nets. The clock period is a contract: agents may not relax it.
