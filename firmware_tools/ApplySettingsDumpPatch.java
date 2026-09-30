// Apply the native active-preset dump patch to an analyzed K-Board 1.2.2 image.
// The result is exported as a 64 KiB flat binary supplied as the sole argument.
//@category KBoard

import java.io.File;
import java.io.FileOutputStream;

import ghidra.app.plugin.assembler.Assembler;
import ghidra.app.plugin.assembler.Assemblers;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;

public class ApplySettingsDumpPatch extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: ApplySettingsDumpPatch <output.bin>");
        }

        // Firmware 1.2.2's undocumented 0x50/0x06 reply calls its factory-data
        // dumper here. Redirect only that call; ordinary playing and preset
        // writes do not pass through this path.
        Assembler assembler = Assemblers.getAssembler(currentProgram);
        assembler.assemble(toAddr(0x5d24), "LCALL 0x8e15");

        // Emit: F0, KMI K-Board identity, private marker 7D, then the 470-byte
        // slot-zero flash image through the firmware's own 8-to-7 bit encoder.
        // The encoder streams USB-MIDI packets as each seven-byte group closes.
        assembler.assemble(toAddr(0x8e15),
            "ANL 0xe6,#0xf7",
            "MOV R7,#0xf0", "LCALL 0x7d52",
            "MOV R7,#0x00", "LCALL 0x7d52",
            "MOV R7,#0x01", "LCALL 0x7d52",
            "MOV R7,#0x5f", "LCALL 0x7d52",
            "MOV R7,#0x7a", "LCALL 0x7d52",
            "MOV R7,#0x1a", "LCALL 0x7d52",
            "MOV R7,#0x00", "LCALL 0x7d52",
            "MOV R7,#0x7d", "LCALL 0x7d52",
            "CLR A",
            "MOV DPTR,#0x0879", "MOVX @DPTR,A", "INC DPTR", "MOVX @DPTR,A",
            "MOV DPTR,#0x0886", "MOV A,#0xf0", "MOVX @DPTR,A", "INC DPTR",
            "CLR A", "MOVX @DPTR,A", "INC DPTR", "MOV A,#0x01", "MOVX @DPTR,A",
            "INC DPTR", "MOV A,#0xd6", "MOVX @DPTR,A",
            // loop, address 0x8e58
            "MOV DPTR,#0x0886", "MOVX A,@DPTR", "MOV R6,A", "INC DPTR",
            "MOVX A,@DPTR", "MOV R7,A", "MOV DPH,R6", "MOV DPL,R7",
            "CLR A", "MOVC A,@A+DPTR", "MOV R7,A", "LCALL 0x7b61",
            "MOV DPTR,#0x0887", "MOVX A,@DPTR", "INC A", "MOVX @DPTR,A",
            "JNZ 0x8e78", "MOV DPTR,#0x0886", "MOVX A,@DPTR", "INC A", "MOVX @DPTR,A",
            "MOV DPTR,#0x0889", "MOVX A,@DPTR", "CLR CY", "SUBB A,#0x01",
            "MOVX @DPTR,A", "JNC 0x8e88", "MOV DPTR,#0x0888", "MOVX A,@DPTR", "DEC A",
            "MOVX @DPTR,A", "MOV DPTR,#0x0888", "MOVX A,@DPTR", "MOV R6,A",
            "INC DPTR", "MOVX A,@DPTR", "ORL A,R6", "JNZ 0x8e58",
            "LCALL 0x81fc", "MOV R7,#0xf7", "LCALL 0x7d52",
            "ORL 0xe6,#0x08", "RET"
        );

        // Refuse to export if this script was run against a different layout.
        byte[] hook = new byte[3];
        currentProgram.getMemory().getBytes(toAddr(0x5d24), hook);
        if ((hook[0] & 0xff) != 0x12 || (hook[1] & 0xff) != 0x8e ||
            (hook[2] & 0xff) != 0x15) {
            throw new IllegalStateException("settings-dump hook did not assemble as expected");
        }

        byte[] image = new byte[0x10000];
        currentProgram.getMemory().getBytes(toAddr(0), image);
        try (FileOutputStream output = new FileOutputStream(new File(args[0]))) {
            output.write(image);
        }
    }
}
