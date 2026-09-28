# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""GraphQL documents used against the Milestone AI Bridge web service.

All documents target ``POST {graphql_url}`` with a JSON body of
``{"query": ..., "variables": ...}`` and ``Content-Type: application/json``,
matching the official Milestone sample
(``milestonesys/MIP-AIBridge-samples`` →
``apps/golang/connectivitysample/src/infrastructure/repositories/graphqlrepository.go``).
"""

from __future__ import annotations

# Lists the XProtect systems attached to this AI Bridge. The returned ``id`` is
# the ``vmsId`` required as ``register(input: { id: ... })``.
QUERY_VMS_IDS = "query { about { videoManagementSystems { id } } }"

# OIDC issuers of the attached XProtect Identity Providers. Needed later when
# validating/forwarding operator JWTs (``enforce-oauth=true`` deployments).
QUERY_IDP_ISSUERS = "query { about { videoManagementSystems { idp } } }"

# Camera discovery. ``videoStreams.id`` is the composite ``"{deviceId}/{streamId}"``
# and ``streamAvailability.rtsp`` is the ready-to-use live RTSP URL
# (``rtsp://<aib-host>:8554/{deviceId}/{streamId}``).
QUERY_CAMERAS = (
    "query { cameras { name videoStreams { id name streamAvailability { rtsp } } } }"
)

# Base64 JPEG snapshot for one stream. ``token`` is the operator's XProtect JWT.
QUERY_SNAPSHOT = (
    "query GetSnapshot($deviceID: ID!, $streamID: ID!, $max_width: Int!, "
    "$max_height: Int!, $token: String!) { "
    "cameras(deviceIDs: [$deviceID]) { videoStreams(streamID: $streamID) { "
    "snapshot(maxWidth: $max_width, maxHeight: $max_height, token: $token) "
    "{ jpegImage } } } }"
)

# Resolve the HTTP sink an IVA app POSTs metadata / events to. Only available
# after the app has been registered.
QUERY_METADATA_TOPIC_REST = (
    "query Query_Metadata_Topics_By_Name($topicName: String!) "
    "{ metadataTopics(topicName: $topicName) { topicAvailability { rest } } }"
)

QUERY_EVENT_TOPIC_REST = (
    "query Query_Event_Topics_By_Name($topicName: String!) "
    "{ eventTopics(topicName: $topicName) { topicAvailability { rest } } }"
)


def build_register_mutation(vms_id: str, apps_literal: str) -> str:
    """Assemble the ``register`` mutation.

    AI Bridge expects the app definition as inline GraphQL literal syntax (not
    JSON), because ``eventFormat`` / ``metadataFormat`` are unquoted enum values.
    ``apps_literal`` is produced by
    :func:`vms_shim.milestone.manifest.to_graphql_literal`.
    """
    return f'mutation {{ register(input: {{ id: "{vms_id}" {apps_literal} }}) {{ id }} }}'
