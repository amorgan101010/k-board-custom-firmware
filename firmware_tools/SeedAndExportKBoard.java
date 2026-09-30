// Seed the relocated K-Board application vectors, run analysis, and export a
// deterministic text listing for firmware research.
//@category KBoard

import java.io.File;
import java.io.PrintWriter;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.symbol.Reference;

public class SeedAndExportKBoard extends GhidraScript {
    private static final long[] VECTOR_ADDRESSES = {
        0x2400, 0x2403, 0x240B, 0x2413, 0x241B, 0x2423, 0x242B, 0x2433, 0x243B
    };

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: SeedAndExportKBoard <listing> <functions>");
        }

        for (long offset : VECTOR_ADDRESSES) {
            Address address = toAddr(offset);
            disassemble(address);
            Instruction instruction = getInstructionAt(address);
            if (instruction == null) {
                continue;
            }
            for (Reference reference : instruction.getReferencesFrom()) {
                if (reference.getReferenceType().isFlow()) {
                    Address target = reference.getToAddress();
                    disassemble(target);
                    if (getFunctionAt(target) == null) {
                        createFunction(target, null);
                    }
                }
            }
        }
        analyzeAll(currentProgram);

        try (PrintWriter out = new PrintWriter(new File(args[0]))) {
            InstructionIterator instructions = currentProgram.getListing().getInstructions(true);
            while (instructions.hasNext() && !monitor.isCancelled()) {
                Instruction instruction = instructions.next();
                StringBuilder refs = new StringBuilder();
                for (Reference reference : instruction.getReferencesFrom()) {
                    if (refs.length() == 0) refs.append(" ; refs ");
                    else refs.append(", ");
                    refs.append(reference.getReferenceType()).append("->")
                        .append(reference.getToAddress());
                }
                out.printf("%s  %-24s%s%n", instruction.getAddress(),
                           instruction.toString(), refs.toString());
            }
        }

        try (PrintWriter out = new PrintWriter(new File(args[1]))) {
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext() && !monitor.isCancelled()) {
                Function function = functions.next();
                out.printf("%s %s size=%d calls=%d%n", function.getEntryPoint(),
                           function.getName(), function.getBody().getNumAddresses(),
                           function.getCalledFunctions(monitor).size());
            }
        }
    }
}
