---------------------------- MODULE MCEviconFence ----------------------------
\* TLC model wrapper: concrete intents and dose values for EviconFence.
\* Two intents with values 2 and 1 and U = 2: they cannot both be reserved,
\* so capacity (and therefore stranded reservations) matters.
EXTENDS EviconFence
CONSTANTS i1, i2
MCIntents == {i1, i2}
MCValue == [i \in MCIntents |-> IF i = i1 THEN 2 ELSE 1]
=============================================================================
