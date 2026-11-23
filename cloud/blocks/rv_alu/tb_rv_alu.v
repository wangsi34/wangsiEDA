// FROZEN self-checking testbench. The harness verifies its SHA-256 before every sim;
// agents are not allowed to edit it (guard: tb_frozen).
`timescale 1ns/1ps
module tb_rv_alu;
    localparam W = 16;
    reg clk = 0, rst_n = 0, in_valid = 0;
    reg [3:0] op; reg [W-1:0] a, b;
    wire out_valid; wire [W-1:0] y;
    rv_alu #(.W(W)) dut(.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .op(op), .a(a), .b(b),
                        .out_valid(out_valid), .y(y));
    always #5 clk = ~clk;

    function [W-1:0] golden(input [3:0] o, input [W-1:0] x, input [W-1:0] z);
        reg [2*W-1:0] p;
        begin
            p = x * z;
            case (o)
                0: golden = x + z;           1: golden = x - z;
                2: golden = x & z;           3: golden = x | z;
                4: golden = x ^ z;           5: golden = x << z[3:0];
                6: golden = x >> z[3:0];     7: golden = $signed(x) >>> z[3:0];
                8: golden = ($signed(x) < $signed(z)) ? 1 : 0;
                9: golden = (x < z) ? 1 : 0;
                10: golden = p[W-1:0];       11: golden = p[2*W-1:W];
                default: golden = 0;
            endcase
        end
    endfunction

    reg [3:0] op_p[0:1]; reg [W-1:0] a_p[0:1], b_p[0:1]; reg v_p[0:1];
    integer i, errs = 0, checks = 0, seed = 32'h5eed;
    integer per_op_err[0:15];
    initial for (i = 0; i < 16; i = i + 1) per_op_err[i] = 0;

    always @(posedge clk) begin
        v_p[1] <= v_p[0]; op_p[1] <= op_p[0]; a_p[1] <= a_p[0]; b_p[1] <= b_p[0];
        v_p[0] <= in_valid; op_p[0] <= op; a_p[0] <= a; b_p[0] <= b;
        if (rst_n && out_valid && v_p[1]) begin
            checks = checks + 1;
            if (y !== golden(op_p[1], a_p[1], b_p[1])) begin
                errs = errs + 1; per_op_err[op_p[1]] = per_op_err[op_p[1]] + 1;
                if (errs <= 5)
                    $display("MISMATCH op=%0d a=%h b=%h got=%h exp=%h", op_p[1], a_p[1], b_p[1], y,
                             golden(op_p[1], a_p[1], b_p[1]));
            end
        end
    end

    initial begin
        v_p[0] = 0; v_p[1] = 0;
        repeat (3) @(posedge clk); rst_n = 1;
        for (i = 0; i < 2000; i = i + 1) begin
            @(negedge clk);
            in_valid = 1; op = i % 12; a = $random(seed); b = $random(seed);
            if (i % 7 == 0) a = {1'b1, a[W-2:0]};   // force negative operands for SRA/SLT
        end
        @(negedge clk) in_valid = 0;
        repeat (4) @(posedge clk);
        for (i = 0; i < 12; i = i + 1)
            if (per_op_err[i] != 0) $display("OPFAIL op=%0d errors=%0d", i, per_op_err[i]);
        if (errs == 0 && checks > 0) $display("RESULT PASS checks=%0d", checks);
        else $display("RESULT FAIL errors=%0d checks=%0d", errs, checks);
        $finish;
    end
endmodule
