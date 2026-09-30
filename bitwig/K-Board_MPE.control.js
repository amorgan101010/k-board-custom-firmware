/*
 * Bitwig controller script for the original Keith McMillen K-Board.
 *
 * The K-Board sends MPE pressure as Channel Pressure on each member channel.
 * Marking the note input as expressive makes Bitwig associate those messages
 * with the note currently held on that channel.
 */

loadAPI(1);

host.defineController(
   "Keith McMillen",
   "K-Board MPE",
   "1.0",
   "5A0C64F0-7587-4C83-95C6-61FA53875966"
);
host.defineMidiPorts(1, 1);

var portNames = ["K-Board MIDI 1"];
host.addDeviceNameBasedDiscoveryPair(portNames, portNames);

var noteInput;

function init()
{
   noteInput = host.getMidiInPort(0).createNoteInput(
      "K-Board MPE",
      "8?????", // Note Off, all channels
      "9?????", // Note On, all channels
      "B?40??", // Sustain
      "B?4A??", // MPE timbre (CC 74)
      "D?????", // Channel Pressure, per member channel
      "E?????"  // Pitch Bend, per member channel
   );

   // Zero selects the standard lower MPE zone: master channel 1, member
   // channels beginning at channel 2.
   noteInput.setUseMultidimensionalPolyphonicExpression(true, 0);

   var bendRanges = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"];
   var bendRange = host.getPreferences().getEnumSetting(
      "Per-note pitch bend range",
      "MPE",
      bendRanges,
      "12"
   );

   bendRange.addValueObserver(function(selectedRange)
   {
      noteInput.setUseExpressiveMidi(true, 0, parseInt(selectedRange));
   });

   noteInput.setShouldConsumeEvents(false);
   println("K-Board MPE input enabled");
}

function exit()
{
}
