// rv_alu: RV32-style ALU/MUL execute unit (demo width W=16).
// Seed RTL handed to the agent. It contains one planted functional bug (see spec op=7).
module rv_alu #(parameter W = 16) (
    input              clk,
    input              rst_n,
    input              in_valid,
    input      [3:0]   op,
    input      [W-1:0] a,
    input      [W-1:0] b,
    output reg         out_valid,
    output reg [W-1:0] y
);
    reg         v_q;
    reg [3:0]   op_q;
    reg [W-1:0] a_q, b_q;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            v_q <= 1'b0; op_q <= 4'd0; a_q <= {W{1'b0}}; b_q <= {W{1'b0}};
        end else begin
            v_q <= in_valid; op_q <= op; a_q <= a; b_q <= b;
        end
    end

    wire [3:0]     shamt = b_q[3:0];
    wire [2*W-1:0] prod  = a_q * b_q;
    reg  [W-1:0]   res;

    always @(*) begin
        case (op_q)
            4'd0:  res = a_q + b_q;
            4'd1:  res = a_q - b_q;
            4'd2:  res = a_q & b_q;
            4'd3:  res = a_q | b_q;
            4'd4:  res = a_q ^ b_q;
            4'd5:  res = a_q << shamt;
            4'd6:  res = a_q >> shamt;
            4'd7:  res = a_q >> shamt;
            4'd8:  res = {{(W-1){1'b0}}, ($signed(a_q) < $signed(b_q))};
            4'd9:  res = {{(W-1){1'b0}}, (a_q < b_q)};
            4'd10: res = prod[W-1:0];
            4'd11: res = prod[2*W-1:W];
            default: res = {W{1'b0}};
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_valid <= 1'b0; y <= {W{1'b0}};
        end else begin
            out_valid <= v_q; y <= res;
        end
    end
endmodule
