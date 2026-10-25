# MinIO evidence contracts used by experiment C (release 073)

Byte-identical copies of the contracts that release 070 **measured** on the testbed's MinIO
(`python3 evicon_review.py measure minio`, review run `review-20261021T010856`, VM-C 192.168.107.150,
MinIO `evicon/minio-src:RELEASE.2025-04-22T22-12-26Z` on VM-B through docker compose, 3 repetitions):

| File | Call shape | Capabilities | Source run | SHA-256 |
|---|---|---|---|---|
| `meas-minio-cond-070.json` | `PUT object If-None-Match: *` (+ tombstone for a stop) | D_h (horizon >= 600 s), Q_A, Q_S: K3 | `measure/minio/runs/minio-conditional/20261021T022040/contract.json` | `dbe4797d7c73cf99049931ec1c5b97b1a657bd7d407b34e5f02b9049986017eb` |
| `meas-minio-plain-070.json` | `PUT object` (unconditional) | none: K0 | `measure/minio/runs/minio-unconditional/20261021T025224/contract.json` | `5215bebd11b54436b37063c8bcfd925327da389f9c75daf541bb68b0587d3d59` |

The owner gateway of experiment C (`conformance/reference/s3_owner_gateway.py`) advertises the file of
its mode in `/v1/capabilities`; the authority decodes it strictly and accepts only MEASURED or
RUNTIME_VERIFIED contracts (`cmd/evicon-authority`, `deviceOrFileContract`). Experiment C therefore runs
the runtime on the contract measured for the same system and the same call shape, with no hand-written
capability. Both contracts hold under the fault model "network loss/delay, client abandon; owner up";
every fault of experiment C stays inside it (see `evaluation/prereg/PREREG_expC_073.md`).

The files are frozen by the experiment C plan; do not edit them.
