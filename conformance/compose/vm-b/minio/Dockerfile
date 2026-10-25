# MinIO server built from its official source (release 068).
#
# Why: MinIO no longer distributes images or binaries. minio/minio was removed from Docker
# Hub (September 2026), quay.io/minio/minio answers 401 and dl.min.io answers 410; the
# GitHub repository is archived but its source and release tags remain. The 067 testbed run
# failed with "pull access denied for minio/minio".
#
# What: the exact release EviCon measured against before (RELEASE.2025-04-22T22-12-26Z),
# cloned at its tag, checked against the tag's commit, built the way MinIO's Makefile builds
# release binaries (CGO off, -tags kqueue, the release ldflags so `minio --version` names the
# release). Built on VM-B by evicon_review.py (`measure minio`) or deploy/setup/vm-b.sh:
#   docker build --network=host -t evicon/minio-src:RELEASE.2025-04-22T22-12-26Z conformance/compose/vm-b/minio
# Needs Internet on VM-B (github.com, proxy.golang.org, Docker Hub for the two base images);
# about 10-20 minutes and 3 GB of disk the first time, cached afterwards.
ARG GO_IMAGE=golang:1.24-bookworm
ARG BASE_IMAGE=debian:bookworm-slim

FROM ${GO_IMAGE} AS build
ARG MINIO_TAG=RELEASE.2025-04-22T22-12-26Z
ARG MINIO_COMMIT=0d7408fc9969caf07de6a8c3a84f9fbb10a6739e
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOFLAGS=-trimpath
# 069: a failed clone (no network from the build) and a wrong commit are reported separately
# (068 printed "is not commit" for both); build with `docker build --network=host` on VM-B
RUN git clone --quiet --depth 1 --branch "${MINIO_TAG}" https://github.com/minio/minio /src \
 || { echo "cannot clone https://github.com/minio/minio at ${MINIO_TAG}: no network from the build? (use docker build --network=host)" >&2; exit 1; }
RUN got="$(git -C /src rev-parse HEAD)" && [ "$got" = "${MINIO_COMMIT}" ] \
 || { echo "MinIO ${MINIO_TAG} is commit ${got:-?}, expected ${MINIO_COMMIT}" >&2; exit 1; }
WORKDIR /src
RUN v="$(echo "${MINIO_TAG}" | sed -E 's/^RELEASE\.//; s/T([0-9]{2})-([0-9]{2})-([0-9]{2})Z$/T\1:\2:\3Z/')" \
 && ldflags="$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go "${v}")" \
 && go build -tags kqueue -ldflags "${ldflags}" -o /out/minio . \
 && /out/minio --version

FROM ${BASE_IMAGE}
ARG MINIO_TAG=RELEASE.2025-04-22T22-12-26Z
ARG MINIO_COMMIT=0d7408fc9969caf07de6a8c3a84f9fbb10a6739e
LABEL org.opencontainers.image.title="MinIO server (built from source by EviCon)" \
      org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.version="${MINIO_TAG}" \
      org.opencontainers.image.revision="${MINIO_COMMIT}" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY --from=build /out/minio /usr/bin/minio
EXPOSE 9000 9001
ENTRYPOINT ["/usr/bin/minio"]
