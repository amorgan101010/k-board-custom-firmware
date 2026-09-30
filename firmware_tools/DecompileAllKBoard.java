// Decompile every function in the current program to one C file.
//@category KBoard

import java.io.File;
import java.io.PrintWriter;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;

public class DecompileAllKBoard extends GhidraScript {
    @Override
    public void run() throws Exception {
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try (PrintWriter out = new PrintWriter(new File(getScriptArgs()[0]))) {
            for (Function function : currentProgram.getFunctionManager().getFunctions(true)) {
                out.printf("%n/* ===== %s ===== */%n", function.getEntryPoint());
                DecompileResults result = decompiler.decompileFunction(function, 60, monitor);
                out.println(result.decompileCompleted()
                    ? result.getDecompiledFunction().getC()
                    : "/* " + result.getErrorMessage() + " */");
            }
        } finally {
            decompiler.dispose();
        }
    }
}
