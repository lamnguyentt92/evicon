----------------------------- MODULE EviconFence -----------------------------
(***************************************************************************)
(* EviCon (release 062): safe continuation after ambiguous ADD effects.     *)
(*                                                                         *)
(* One effect owner with an upper bound U on a cumulative state (ADD >= 0,  *)
(* e.g. a dose budget). An authority admits intents, sends them under a    *)
(* fresh identity (attempt), and may lose requests or replies. Ambiguity is *)
(* resolved only by owner evidence: a receipt, a per-identity STOP (Q_S) or *)
(* an owner-certified FENCE (F, monotone cutoff). After terminal STOPPED    *)
(* evidence the intent may be re-issued under a NEW identity               *)
(* ("fence-then-retry"). With Dedup the authority may also resend the same  *)
(* identity (forward retry, contract D_h with an unbounded horizon).       *)
(*                                                                         *)
(* Release 062 adds an owner crash between effect and receipt (Crash): the  *)
(* effect happened but the owner lost its receipt (an "orphan"). An owner  *)
(* that cannot reconcile orphans (Reconcile = FALSE, like evicon-device and *)
(* the firmware) answers UNCERTAIN: a stop blocks the identity's future but *)
(* certifies nothing, and a fence over an orphan is ABORTED by the          *)
(* authority (nothing sealed, nothing anchored). SealOrphans = TRUE is the  *)
(* bug of review RV04 ("fence OK + no receipt => STOPPED"): TLC must find a *)
(* false STOPPED.                                                          *)
(*                                                                         *)
(* Scope: one owner, the intents and attempts of MCEviconFence, at most one *)
(* crash, no owner expiry, no liveness/fairness. A PASS is bounded model    *)
(* checking of this abstraction, not a proof about the Go/C code.          *)
(*                                                                         *)
(* Checked invariants (T1):                                                *)
(*   BoundSafety        owner state never exceeds U                         *)
(*   NoFalseStopped     authority STOPPED  =>  that identity never applied  *)
(*   AtMostOncePerIntent each intent takes effect at most once              *)
(*   Coverage           owner state <= reservations of non-STOPPED attempts *)
(* Negative controls (TLC must report a violation): Naive (release on       *)
(* timeout), UnsafeResend (resend without owner dedup), SealOrphans.        *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Intents, Value, U, MaxAttempts, UseFence, UseStop, Dedup, Naive, UnsafeResend,
          Crash, Reconcile, SealOrphans

Attempts == Intents \X (1..MaxAttempts)
Intent(a) == a[1]
N(a) == a[2]

VARIABLES seqOf, nextSeq, st, net, oState, oApplied, oSeen, cutoff, tomb,
          replies, fencing, fenceMsg, fenceOK, orphan, crashes

vars == <<seqOf, nextSeq, st, net, oState, oApplied, oSeen, cutoff, tomb,
          replies, fencing, fenceMsg, fenceOK, orphan, crashes>>

Reserved == LET R == {a \in Attempts : st[a] \in {"PENDING", "APPLIED"}}
                F[S \in SUBSET Attempts] ==
                   IF S = {} THEN 0
                   ELSE LET x == CHOOSE x \in S : TRUE IN Value[Intent(x)] + F[S \ {x}]
            IN F[R]

TypeOK ==
  /\ seqOf \in [Attempts -> Nat]
  /\ st \in [Attempts -> {"NONE", "PENDING", "APPLIED", "STOPPED"}]
  /\ net \subseteq Attempts /\ oApplied \subseteq Attempts /\ oSeen \subseteq Attempts
  /\ tomb \subseteq Attempts /\ oState \in Nat /\ cutoff \in Nat /\ fencing \in Nat
  /\ fenceMsg \subseteq Attempts /\ fenceOK \in BOOLEAN
  /\ orphan \subseteq oApplied /\ crashes \in 0..1

Init ==
  /\ seqOf = [a \in Attempts |-> 0]
  /\ nextSeq = 1
  /\ st = [a \in Attempts |-> "NONE"]
  /\ net = {}
  /\ oState = 0
  /\ oApplied = {}
  /\ oSeen = {}
  /\ cutoff = 0
  /\ tomb = {}
  /\ replies = {}
  /\ fencing = 0
  /\ fenceMsg = {}
  /\ fenceOK = FALSE
  /\ orphan = {}
  /\ crashes = 0

\* The owner can certify the outcome of an applied identity unless it is an
\* orphan of a crash and the owner cannot reconcile orphans.
Unknown(a) == a \in orphan /\ ~Reconcile

\* An intent is done when one of its attempts is APPLIED (authority view);
\* it may be (re-)issued only when no attempt is PENDING and the previous
\* attempt is terminal STOPPED.
CanIssue(a) ==
  /\ st[a] = "NONE"
  /\ fencing = 0
  /\ \A b \in Attempts : Intent(b) = Intent(a) => st[b] \notin {"PENDING", "APPLIED"}
  /\ IF N(a) = 1 THEN TRUE ELSE st[<<Intent(a), N(a) - 1>>] = "STOPPED"

Admit(a) ==
  /\ CanIssue(a)
  /\ Reserved + Value[Intent(a)] <= U          \* relational guard (ADD-only)
  /\ seqOf' = [seqOf EXCEPT ![a] = nextSeq]
  /\ nextSeq' = nextSeq + 1
  /\ st' = [st EXCEPT ![a] = "PENDING"]
  /\ net' = net \cup {a}
  /\ UNCHANGED <<oState, oApplied, oSeen, cutoff, tomb, replies, fencing, fenceMsg, fenceOK, orphan, crashes>>

\* Forward retry: resend the same identity (only with owner dedup, T4).
Resend(a) ==
  /\ Dedup \/ UnsafeResend
  /\ st[a] = "PENDING"
  /\ net' = net \cup {a}
  /\ UNCHANGED <<seqOf, nextSeq, st, oState, oApplied, oSeen, cutoff, tomb, replies, fencing, fenceMsg, fenceOK, orphan, crashes>>

Sealed(a) == a \in tomb \/ seqOf[a] <= cutoff

Deliver(a) ==
  /\ a \in net
  /\ net' = net \ {a}
  /\ IF a \in oApplied
       THEN /\ (IF Unknown(a) /\ (Dedup \/ Sealed(a))
                  THEN UNCHANGED <<oState, oApplied, replies>>  \* claim without receipt: UNCERTAIN
                ELSE IF Dedup \/ Sealed(a)
                  THEN /\ replies' = replies \cup {<<a, "APPLIED">>}
                       /\ UNCHANGED <<oState, oApplied>>
                  ELSE \* no dedup: a duplicate delivery executes again
                       /\ oState' = oState + Value[Intent(a)]
                       /\ replies' = replies \cup {<<a, "APPLIED">>}
                       /\ UNCHANGED oApplied)
            /\ UNCHANGED <<oSeen, tomb>>
       ELSE IF Sealed(a) \/ (Dedup /\ a \in oSeen)
         THEN /\ replies' = replies \cup {<<a, "STOPPED">>}
              /\ oSeen' = oSeen \cup {a}
              /\ UNCHANGED <<oState, oApplied, tomb>>
         ELSE /\ oState' = oState + Value[Intent(a)]
              /\ oApplied' = oApplied \cup {a}
              /\ oSeen' = oSeen \cup {a}
              /\ replies' = replies \cup {<<a, "APPLIED">>}
              /\ UNCHANGED tomb
  /\ UNCHANGED <<seqOf, nextSeq, st, cutoff, fencing, fenceMsg, fenceOK, orphan, crashes>>

\* Owner crash between effect and receipt: the effect happened, no reply,
\* and the owner keeps only a claim without receipt (an orphan).
CrashDeliver(a) ==
  /\ Crash /\ crashes = 0
  /\ a \in net /\ a \notin oSeen /\ ~Sealed(a)
  /\ net' = net \ {a}
  /\ oState' = oState + Value[Intent(a)]
  /\ oApplied' = oApplied \cup {a}
  /\ oSeen' = oSeen \cup {a}
  /\ orphan' = orphan \cup {a}
  /\ crashes' = crashes + 1
  /\ UNCHANGED <<seqOf, nextSeq, st, cutoff, tomb, replies, fencing, fenceMsg, fenceOK>>

LoseRequest(a) ==
  /\ a \in net
  /\ net' = net \ {a}
  /\ UNCHANGED <<seqOf, nextSeq, st, oState, oApplied, oSeen, cutoff, tomb, replies, fencing, fenceMsg, fenceOK, orphan, crashes>>

LoseReply(r) ==
  /\ r \in replies
  /\ replies' = replies \ {r}
  /\ UNCHANGED <<seqOf, nextSeq, st, net, oState, oApplied, oSeen, cutoff, tomb, fencing, fenceMsg, fenceOK, orphan, crashes>>

ReceiveReply(r) ==
  /\ r \in replies
  /\ replies' = replies \ {r}
  /\ st' = IF st[r[1]] = "PENDING" THEN [st EXCEPT ![r[1]] = r[2]] ELSE st
  /\ UNCHANGED <<seqOf, nextSeq, net, oState, oApplied, oSeen, cutoff, tomb, fencing, fenceMsg, fenceOK, orphan, crashes>>

\* Per-identity stop (Q_S): serialized with effects at the owner.
Stop(a) ==
  /\ UseStop
  /\ st[a] = "PENDING"
  /\ IF a \in oApplied
       THEN IF Unknown(a)
              THEN /\ tomb' = tomb \cup {a}       \* future blocked, past unknown: UNCERTAIN
                   /\ UNCHANGED replies
              ELSE /\ replies' = replies \cup {<<a, "APPLIED">>}
                   /\ UNCHANGED tomb
       ELSE /\ tomb' = tomb \cup {a}
            /\ replies' = replies \cup {<<a, "STOPPED">>}
  /\ UNCHANGED <<seqOf, nextSeq, st, net, oState, oApplied, oSeen, cutoff, fencing, fenceMsg, fenceOK, orphan, crashes>>

\* Owner-certified fence (F): authority pauses admission at frontier w.
StartFence ==
  /\ UseFence
  /\ fencing = 0
  /\ \E a \in Attempts : st[a] = "PENDING"
  /\ fencing' = nextSeq - 1
  /\ UNCHANGED <<seqOf, nextSeq, st, net, oState, oApplied, oSeen, cutoff, tomb, replies, fenceMsg, fenceOK, orphan, crashes>>

\* The owner installs the cutoff, then reports the identities it can certify.
\* If an orphan lies below the frontier, the authority aborts the fence
\* (release 062): admission resumes, nothing is sealed. SealOrphans models
\* the RV04 bug: the orphan is simply omitted and later sealed as STOPPED.
OwnerFence ==
  /\ fencing # 0
  /\ cutoff' = IF fencing > cutoff THEN fencing ELSE cutoff
  /\ IF (\E a \in orphan : seqOf[a] <= fencing) /\ ~Reconcile /\ ~SealOrphans
       THEN /\ fencing' = 0
            /\ fenceMsg' = {}
            /\ fenceOK' = FALSE
       ELSE /\ fenceMsg' = {a \in oApplied : seqOf[a] <= fencing /\ ~(Unknown(a) /\ SealOrphans)}
            /\ fenceOK' = TRUE
            /\ UNCHANGED fencing
  /\ UNCHANGED <<seqOf, nextSeq, st, net, oState, oApplied, oSeen, tomb, replies, orphan, crashes>>

LoseFenceReply ==
  /\ fenceOK
  /\ fenceOK' = FALSE
  /\ fenceMsg' = {}
  /\ UNCHANGED <<seqOf, nextSeq, st, net, oState, oApplied, oSeen, cutoff, tomb, replies, fencing, orphan, crashes>>

FenceReply ==
  /\ fencing # 0
  /\ fenceOK
  /\ st' = [a \in Attempts |->
              IF st[a] = "PENDING" /\ seqOf[a] <= fencing
                THEN (IF a \in fenceMsg THEN "APPLIED" ELSE "STOPPED")
                ELSE st[a]]
  /\ fencing' = 0
  /\ fenceMsg' = {}
  /\ fenceOK' = FALSE
  /\ UNCHANGED <<seqOf, nextSeq, net, oState, oApplied, oSeen, cutoff, tomb, replies, orphan, crashes>>

\* Negative control: a naive client releases a timed-out command.
NaiveRelease(a) ==
  /\ Naive
  /\ st[a] = "PENDING"
  /\ st' = [st EXCEPT ![a] = "STOPPED"]
  /\ UNCHANGED <<seqOf, nextSeq, net, oState, oApplied, oSeen, cutoff, tomb, replies, fencing, fenceMsg, fenceOK, orphan, crashes>>

Next ==
  \/ \E a \in Attempts : Admit(a) \/ Resend(a) \/ Deliver(a) \/ CrashDeliver(a) \/ LoseRequest(a) \/ Stop(a) \/ NaiveRelease(a)
  \/ \E r \in replies : LoseReply(r) \/ ReceiveReply(r)
  \/ StartFence \/ OwnerFence \/ LoseFenceReply \/ FenceReply

Spec == Init /\ [][Next]_vars

BoundSafety == oState <= U
NoFalseStopped == \A a \in Attempts : st[a] = "STOPPED" => a \notin oApplied
AtMostOncePerIntent == \A i \in Intents : Cardinality({a \in oApplied : Intent(a) = i}) <= 1
Coverage == oState <= Reserved
=============================================================================
