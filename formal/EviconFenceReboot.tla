------------------------------ MODULE EviconFenceReboot ------------------------------
(***************************************************************************)
(* Release 087 (PREREG_extra_087.md item B; reviewer comment M3): the fence *)
(* of a {F} firmware owner across owner reboots, with the authority's       *)
(* anchor and the reboot recovery REC-1 to REC-3 and the gateway seal GW-3.   *)
(*                                                                         *)
(* The owner is the cutoff firmware's decision rule (evc_core.c): a receipt *)
(* ring of the identities applied in this boot (no eviction in the model),  *)
(* a durable claim mark hw raised to s + Block before an effect above it,   *)
(* a durable cutoff, a RAM level; a reboot empties the ring, resets the     *)
(* level and sets the floor (evicted_max) to hw. On a delivery: an identity *)
(* in the ring is a duplicate (APPLIED again, no effect); one at or below   *)
(* the floor is refused (the eviction guard; with Guard = FALSE only at or  *)
(* below the cutoff); one at or below the cutoff is sealed (STOPPED); else  *)
(* it takes effect. The gateway forwards (a copy may be delivered up to     *)
(* MaxDeliveries times, late, or never; a reply may be lost), caches the    *)
(* replies it gets, and with Seal never forwards an identity at or below    *)
(* the floor it has not forwarded (GW-3: STOPPED). The fence answer lists     *)
(* every forwarded identity at or below the cutoff: APPLIED from the cache  *)
(* or the ring, STOPPED above the floor, else UNCERTAIN; and the level and  *)
(* the floor. The authority takes it in per identity (rule R5'); on a       *)
(* complete answer it commits an anchor:                                     *)
(*   AnchorMode = "owner"     the reported level (releases 061-085; with    *)
(*                            the recovery: the negative control of 087)    *)
(*   AnchorMode = "evidence"  the evidence anchor E = anchor + the APPLIED  *)
(*                            values above the old cutoff, after the check  *)
(*                            CheckMode "full" (086: level = E, else the    *)
(*                            domain is conflicted), "floor" (087 REC-3:    *)
(*                            level = the APPLIED values above the floor),  *)
(*                            or "none" (no check: safety without the       *)
(*                            detector).                                    *)
(* With Recovery the authority learns the floor (REC-1) and admits the next *)
(* identity above it (REC-2). The window (REC-4) is not modelled: the guard *)
(* here is the budget only, as in the R5 models.                            *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets

CONSTANTS MaxSeq, Bound, Block, MaxReboots, MaxDeliveries, Recovery, Seal, AnchorMode, CheckMode, Guard

ASSUME AnchorMode \in {"owner", "evidence"} /\ CheckMode \in {"full", "floor", "none"}
ASSUME Block >= 1 /\ Bound >= 1 /\ MaxSeq >= 1

Seqs == 1..MaxSeq
Units(S) == Cardinality(S)          \* every identity adds one unit

VARIABLES hw, cutoff, floor, level, ring, applied, dup, reboots,      \* the firmware (hw, cutoff durable)
          cached, sealedGw, sent, deliveries,                          \* the gateway (durable record)
          status, nextSeq, aFloor, anchor, acut, conflicted, anchoredAfterReboot   \* the authority

owner == <<hw, cutoff, floor, level, ring, applied, dup, reboots>>
gw == <<cached, sealedGw, sent, deliveries>>
auth == <<status, nextSeq, aFloor, anchor, acut, conflicted, anchoredAfterReboot>>
vars == <<owner, gw, auth>>

Init == /\ hw = 0 /\ cutoff = 0 /\ floor = 0 /\ level = 0 /\ ring = {} /\ applied = {} /\ dup = FALSE /\ reboots = 0
        /\ cached = {} /\ sealedGw = {} /\ sent = {} /\ deliveries = [s \in Seqs |-> 0]
        /\ status = [s \in Seqs |-> "NONE"] /\ nextSeq = 1 /\ aFloor = 0 /\ anchor = 0 /\ acut = 0
        /\ conflicted = FALSE /\ anchoredAfterReboot = FALSE

Reserved == {s \in Seqs : s > acut /\ status[s] \in {"PENDING", "APPLIED"}}
NextAdmit == IF Recovery /\ aFloor + 1 > nextSeq THEN aFloor + 1 ELSE nextSeq

\* the guard: anchor + every reservation above the cutoff + the new value within the bound
Admit == LET s == NextAdmit IN
         /\ ~conflicted /\ s \in Seqs
         /\ anchor + Units(Reserved) + 1 <= Bound
         /\ status' = [status EXCEPT ![s] = "PENDING"] /\ nextSeq' = s + 1
         /\ UNCHANGED <<owner, gw, aFloor, anchor, acut, conflicted, anchoredAfterReboot>>

\* the gateway forwards a pending identity (durably, before the first message); a resend is a further delivery
Forward(s) == /\ status[s] = "PENDING" /\ s \notin sealedGw /\ s \notin sent
              /\ ~(Seal /\ s <= floor)
              /\ sent' = sent \cup {s}
              /\ UNCHANGED <<owner, cached, sealedGw, deliveries, auth>>

\* GW-3: never forwarded, at or below the floor: sealed for good and answered STOPPED
GwSeal(s) == /\ Seal /\ status[s] = "PENDING" /\ s \notin sent /\ s \notin sealedGw /\ s <= floor
             /\ sealedGw' = sealedGw \cup {s} /\ status' = [status EXCEPT ![s] = "STOPPED"]
             /\ UNCHANGED <<owner, cached, sent, deliveries, nextSeq, aFloor, anchor, acut, conflicted, anchoredAfterReboot>>

Take(s, outcome, rep) == IF rep /\ status[s] = "PENDING" THEN [status EXCEPT ![s] = outcome] ELSE status

\* one delivery of a forwarded identity; rep: the reply reaches the gateway (and the authority)
Deliver(s, rep) ==
  /\ s \in sent /\ deliveries[s] < MaxDeliveries
  /\ deliveries' = [deliveries EXCEPT ![s] = @ + 1]
  /\ sealedGw' = sealedGw /\ sent' = sent
  /\ UNCHANGED <<cutoff, floor, reboots, nextSeq, aFloor, anchor, acut, conflicted, anchoredAfterReboot>>
  /\ CASE s \in ring ->                                   \* a duplicate in the ring: APPLIED again, no effect
            /\ cached' = (IF rep THEN cached \cup {s} ELSE cached)
            /\ status' = Take(s, "APPLIED", rep)
            /\ UNCHANGED <<hw, level, ring, applied, dup>>
       [] s <= floor /\ (Guard \/ s <= cutoff) ->         \* the eviction guard: UNCERTAIN, never executed
            /\ UNCHANGED <<hw, level, ring, applied, dup, cached, status>>
       [] s <= cutoff ->                                   \* sealed by the cutoff: STOPPED, never started
            /\ status' = Take(s, "STOPPED", rep)
            /\ UNCHANGED <<hw, level, ring, applied, dup, cached>>
       [] OTHER ->                                         \* the claim mark first, then the effect
            /\ hw' = (IF s > hw THEN s + Block ELSE hw)
            /\ dup' = (dup \/ s \in applied) /\ applied' = applied \cup {s}
            /\ ring' = ring \cup {s} /\ level' = level + 1
            /\ cached' = (IF rep THEN cached \cup {s} ELSE cached)
            /\ status' = Take(s, "APPLIED", rep)

\* the owner reboots (also between an effect and its reply: an orphan)
Reboot == /\ reboots < MaxReboots
          /\ level' = 0 /\ ring' = {} /\ floor' = hw /\ reboots' = reboots + 1
          /\ UNCHANGED <<hw, cutoff, applied, dup, gw, auth>>

\* REC-1: the authority learns the floor from any answer
LearnFloor == /\ Recovery /\ aFloor < floor
              /\ aFloor' = floor
              /\ UNCHANGED <<owner, gw, status, nextSeq, anchor, acut, conflicted, anchoredAfterReboot>>

Ans(s) == IF s \in cached \/ s \in ring THEN "APPLIED" ELSE IF s > floor THEN "STOPPED" ELSE "UNCERTAIN"

\* a fence at the highest admitted sequence, while an identity is pending
Fence ==
  LET c == nextSeq - 1
      st1 == [s \in Seqs |-> IF s <= c /\ status[s] = "PENDING"
                                THEN IF s \notin sent THEN "STOPPED"                 \* unlisted: sealed by the authority
                                     ELSE IF Ans(s) = "UNCERTAIN" THEN "PENDING" ELSE Ans(s)
                                ELSE status[s]]
      unc == {s \in sent : s <= c /\ st1[s] = "PENDING"}
      E == anchor + Units({s \in Seqs : acut < s /\ s <= c /\ st1[s] = "APPLIED"})
      Lf == Units({s \in Seqs : floor < s /\ s <= c /\ st1[s] = "APPLIED"})
      ok == CASE AnchorMode = "owner" -> TRUE
              [] CheckMode = "none" -> TRUE
              [] CheckMode = "full" -> level = E
              [] OTHER -> level = Lf
      new == IF AnchorMode = "owner" THEN level ELSE E
  IN
  /\ ~conflicted /\ c >= 1 /\ c > acut
  /\ \E s \in Seqs : s <= c /\ status[s] = "PENDING"
  /\ cutoff' = (IF c > cutoff THEN c ELSE cutoff)
  /\ status' = st1
  /\ IF unc = {} /\ ok
       THEN /\ anchor' = new /\ acut' = c /\ conflicted' = FALSE
            /\ anchoredAfterReboot' = (anchoredAfterReboot \/ reboots > 0)
       ELSE /\ anchor' = anchor /\ acut' = acut /\ anchoredAfterReboot' = anchoredAfterReboot
            /\ conflicted' = (unc = {} /\ ~ok)           \* a complete answer that fails the check: evidence conflict
  /\ UNCHANGED <<hw, floor, level, ring, applied, dup, reboots, gw, nextSeq, aFloor>>

Next == \/ Admit \/ Reboot \/ LearnFloor \/ Fence
        \/ \E s \in Seqs : Forward(s) \/ GwSeal(s) \/ Deliver(s, TRUE) \/ Deliver(s, FALSE)

Spec == Init /\ [][Next]_vars

\* ---- invariants ----
BoundSafety == Units(applied) <= Bound
AtMostOncePerIntent == ~dup
NoFalseStopped == \A s \in Seqs : status[s] = "STOPPED" => s \notin applied
NoFalseApplied == \A s \in Seqs : status[s] = "APPLIED" => s \in applied
FloorPartition == level = Units({s \in applied : s > floor})
\* witnesses (expected to be violated: the paths exist)
NoAnchorAfterReboot == ~anchoredAfterReboot
NeverConflicted == ~conflicted
=============================================================================
