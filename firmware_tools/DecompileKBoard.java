// Decompile selected K-Board firmware routines supplied as hexadecimal args.
//@category KBoard

import java.io.File;
import java.io.PrintWriter;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class DecompileKBoard extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            throw new IllegalArgumentException("usage: DecompileKBoard <output> <hex-address>...");
        }
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try (PrintWriter out = new PrintWriter(new File(args[0]))) {
            for (int index = 1; index < args.length; ++index) {
                long offset = Long.parseLong(args[index], 16);
                Address address = toAddr(offset);
                Function function = getFunctionContaining(address);
                out.printf("\n/* ===== requested %04X; %s ===== */%n", offset,
                           function == null ? "no function" : function.getName());
                if (function == null) continue;
                DecompileResults result = decompiler.decompileFunction(function, 60, monitor);
                if (result.decompileCompleted()) {
                    out.println(result.getDecompiledFunction().getC());
                } else {
                    out.println("/* " + result.getErrorMessage() + " */");
                }
            }
        } finally {
            decompiler.dispose();
        }
    }
}
