---------------------------- MODULE EviconTiming ----------------------------
(***************************************************************************)
(* EviCon release 085 (review M1): the timing boundary cases of Theorem    *)
(* 2(b) (owner expiry without D_h, X or retention) and Proposition 1(ii)   *)
(* (validity), for ONE identity "o" and, optionally, one queued re-issue   *)
(* "r" (a new identity for the same intent). Discrete time; no other       *)
(* mechanism: no deduplication, no query, no stop or fence (the STOPPED    *)
(* evidence a re-issue needs is abstract), no owner crash.                 *)
(*                                                                         *)
(* Clocks. The authority creates "o" at t0 = 0 and stamps the absolute     *)
(* expiry t0 + E on every copy (expires_at_ms). The owner compares it with *)
(* its own clock, now + skew, where the clock error skew is fixed per      *)
(* behaviour in -Eps..Eps: a copy delivered at time t takes effect iff the *)
(* owner enforces no expiry or t + skew <= created + E.                    *)
(* Delivery. A copy sent at s is delivered (and executed, or rejected) or  *)
(* lost at some time in s..s+D; time does not pass a copy's deadline.      *)
(* Replies may be lost. The authority's local view is its send history     *)
(* (sent) and the answers it received (answers).                           *)
(* Departure rule (the outbox). ValidityEveryAttempt = FALSE is the rule   *)
(* of the dispatcher of releases <= 085 (internal/outbox/dispatcher.go,    *)
(* RetryAllowedUntilFunc in internal/runtime/evicon.go): attempt 0 of an   *)
(* identity (its original, also a re-issue's) always leaves; a resend      *)
(* leaves only while now + D <= V. ValidityEveryAttempt = TRUE checks      *)
(* now + D <= V for every attempt, originals and re-issues included; an    *)
(* identity whose original cannot leave any more is suppressed: it was     *)
(* never sent, so it never takes effect (client-local evidence).           *)
(* Re-issue. After terminal STOPPED evidence for "o" (abstract: "o" then   *)
(* never takes effect) the client may re-issue the intent under "r" while  *)
(* now + D <= V (Compile's REISSUE_NEW_ID rule); "r" is queued and its     *)
(* original leaves the outbox at any later time the departure rule allows. *)
(*                                                                         *)
(* Invariants:                                                             *)
(*   NoEffect        no identity takes effect                              *)
(*   NoHiddenEffect  "o" never takes effect while the authority has no     *)
(*                   answer for it. A violation is a witness for Theorem   *)
(*                   2(b): an execution, indistinguishable to the client   *)
(*                   from one in which every copy was lost (same sends, no *)
(*                   answer), in which "o" took effect                     *)
(*   NoLateEffect    no effect after the validity V                        *)
(*   SuppressedNoEffect  a suppressed identity never takes effect          *)
(* Bounded model checking of this abstraction (formal/run_tlc_timing.sh),  *)
(* not a proof about the Go code.                                          *)
(***************************************************************************)
EXTENDS Naturals, Integers, FiniteSets

CONSTANTS
  E,                    \* owner expiry e, from the identity's creation
  Eps,                  \* bound on the owner's clock error epsilon
  D,                    \* bound on the delivery delay
  V,                    \* the intent's validity (an effect after V is late)
  Tmax,                 \* last time step explored
  FirstMin, FirstMax,   \* the original of "o" leaves in FirstMin..FirstMax
  MaxResends,           \* resends of "o" (same identity)
  OwnerExpiry,          \* the owner enforces T
  ValidityEveryAttempt, \* the departure rule (see above)
  Reissue               \* a re-issue "r" may follow STOPPED evidence for "o"

ASSUME /\ E \in Nat /\ Eps \in Nat /\ D \in Nat /\ V \in Nat /\ Tmax \in Nat
       /\ FirstMin \in Nat /\ FirstMax \in Nat /\ FirstMin <= FirstMax /\ MaxResends \in Nat
       /\ OwnerExpiry \in BOOLEAN /\ ValidityEveryAttempt \in BOOLEAN /\ Reissue \in BOOLEAN

Ids == {"o", "r"}
Copy == [id : Ids, at : 0..Tmax, n : 0..(MaxResends + 1)]

VARIABLES now, skew, sent, flight, effects, answers, stoppedO, rCreated, suppressed
vars == <<now, skew, sent, flight, effects, answers, stoppedO, rCreated, suppressed>>

Created(i) == IF i = "o" THEN 0 ELSE rCreated
SentOf(i) == {c \in sent : c.id = i}
Leaves(n) == IF ValidityEveryAttempt THEN now + D <= V ELSE (n = 0 \/ now + D <= V)
Accepts(c) == /\ ~(c.id = "o" /\ stoppedO)
              /\ (~OwnerExpiry \/ now + skew <= Created(c.id) + E)
AppliedSeen(i) == \E a \in answers : a.id = i /\ a.out = "APPLIED"

TypeOK == /\ now \in 0..Tmax /\ skew \in -Eps..Eps
          /\ sent \subseteq Copy /\ flight \subseteq sent
          /\ effects \subseteq [id : Ids, at : 0..Tmax]
          /\ answers \subseteq [id : Ids, out : {"APPLIED", "EXPIRED"}, at : 0..Tmax]
          /\ stoppedO \in BOOLEAN /\ rCreated \in -1..Tmax /\ suppressed \subseteq Ids

Init == /\ now = 0 /\ skew \in -Eps..Eps
        /\ sent = {} /\ flight = {} /\ effects = {} /\ answers = {}
        /\ stoppedO = FALSE /\ rCreated = -1 /\ suppressed = {}

Send(c) == /\ sent' = sent \cup {c} /\ flight' = flight \cup {c}
           /\ UNCHANGED <<now, skew, effects, answers, stoppedO, rCreated, suppressed>>

SendOriginal == /\ SentOf("o") = {} /\ "o" \notin suppressed /\ ~stoppedO
                /\ now \in FirstMin..FirstMax /\ Leaves(0)
                /\ Send([id |-> "o", at |-> now, n |-> 0])

Resend == /\ SentOf("o") # {} /\ ~stoppedO /\ ~AppliedSeen("o")
          /\ Cardinality(SentOf("o")) <= MaxResends /\ Leaves(Cardinality(SentOf("o")))
          /\ Send([id |-> "o", at |-> now, n |-> Cardinality(SentOf("o"))])

(* the original of an identity can no longer leave (validity rule): never sent, never an effect *)
Suppress(i) == /\ ValidityEveryAttempt /\ SentOf(i) = {} /\ i \notin suppressed
               /\ (i = "o" => (~stoppedO /\ now >= FirstMin)) /\ (i = "r" => rCreated >= 0)
               /\ ~Leaves(0)
               /\ suppressed' = suppressed \cup {i}
               /\ UNCHANGED <<now, skew, sent, flight, effects, answers, stoppedO, rCreated>>

(* abstract terminal STOPPED evidence for "o": it has not taken effect and never will *)
StopO == /\ Reissue /\ ~stoppedO /\ SentOf("o") # {}
         /\ ~\E x \in effects : x.id = "o"
         /\ ~\E c \in flight : c.id = "o"
         /\ stoppedO' = TRUE
         /\ UNCHANGED <<now, skew, sent, flight, effects, answers, rCreated, suppressed>>

CreateReissue == /\ Reissue /\ stoppedO /\ rCreated = -1 /\ now + D <= V
                 /\ rCreated' = now
                 /\ UNCHANGED <<now, skew, sent, flight, effects, answers, stoppedO, suppressed>>

SendReissue == /\ rCreated >= 0 /\ SentOf("r") = {} /\ "r" \notin suppressed /\ now >= rCreated
               /\ Leaves(0)
               /\ Send([id |-> "r", at |-> now, n |-> 0])

Deliver(c, replied) ==
  /\ c \in flight /\ now >= c.at
  /\ flight' = flight \ {c}
  /\ IF Accepts(c)
       THEN /\ effects' = effects \cup {[id |-> c.id, at |-> now]}
            /\ answers' = IF replied THEN answers \cup {[id |-> c.id, out |-> "APPLIED", at |-> now]} ELSE answers
       ELSE /\ effects' = effects
            /\ answers' = IF replied THEN answers \cup {[id |-> c.id, out |-> "EXPIRED", at |-> now]} ELSE answers
  /\ UNCHANGED <<now, skew, sent, stoppedO, rCreated, suppressed>>

Lose(c) == /\ c \in flight
           /\ flight' = flight \ {c}
           /\ UNCHANGED <<now, skew, sent, effects, answers, stoppedO, rCreated, suppressed>>

Tick == /\ now < Tmax
        /\ \A c \in flight : now + 1 <= c.at + D
        /\ now' = now + 1
        /\ UNCHANGED <<skew, sent, flight, effects, answers, stoppedO, rCreated, suppressed>>

Next == \/ SendOriginal \/ Resend \/ Suppress("o") \/ Suppress("r") \/ StopO \/ CreateReissue \/ SendReissue
        \/ \E c \in flight, rep \in BOOLEAN : Deliver(c, rep)
        \/ \E c \in flight : Lose(c)
        \/ Tick

Spec == Init /\ [][Next]_vars

NoEffect == effects = {}
NoHiddenEffect == (\E x \in effects : x.id = "o") => (\E a \in answers : a.id = "o")
NoLateEffect == \A x \in effects : x.at <= V
SuppressedNoEffect == \A i \in suppressed : ~\E x \in effects : x.id = i
=============================================================================
