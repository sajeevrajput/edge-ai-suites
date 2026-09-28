// Copyright (C) 2025 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { VmsShimBase } from './VmsShimBase';

/**
 * MilestoneShim — UI shim for Milestone XProtect, reached via Milestone AI Bridge.
 *
 * Capabilities mirror MilestoneVmsShim in the backend. AI Bridge has no
 * write-back APIs for these operations:
 *   push_label        ❌  labels ride inside ONVIF metadata / analytics events
 *   set_bookmark      ❌  requires SOAP ServerCommandService on the XProtect server
 *   acknowledge_event ❌  acknowledge alarms in Smart Client / XProtect REST API
 *   trigger_recording ❌  raise an analytics event and bind a Management Client rule
 *
 * Camera IDs are "milestone:{deviceId}/{streamId}" — AI Bridge addresses video by
 * a composite device/stream pair.
 */
export class MilestoneShim extends VmsShimBase {
  vendor   = 'milestone';
  label    = 'Milestone XProtect';
  badgeCls = 'vms-badge vms-badge-purple';

  getCapabilities() {
    return {
      push_label:        false,
      set_bookmark:      false,
      acknowledge_event: false,
      trigger_recording: false,
    };
  }

  formatDeviceId(cameraId) {
    return cameraId.replace(/^milestone:/, '');
  }
}
