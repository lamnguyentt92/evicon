--------------------------- MODULE MCEviconFenceFI ---------------------------
\* TLC model wrapper (release 084): EviconFenceFI with the intents and dose
\* values of MCEviconFence (2 and 1, U = 2).
EXTENDS EviconFenceFI
CONSTANTS i1, i2
MCIntents == {i1, i2}
MCValue == [i \in MCIntents |-> IF i = i1 THEN 2 ELSE 1]
=============================================================================
