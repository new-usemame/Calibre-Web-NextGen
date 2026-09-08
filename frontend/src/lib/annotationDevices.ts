export interface DeviceAttribution {
  annotation_id: string;
  origin_device_id: string | null;
  assigned_device_id: string | null;
}

export function assignmentOverride(row: DeviceAttribution, overrides: Record<string, string | null>) {
  return Object.prototype.hasOwnProperty.call(overrides, row.annotation_id) ? overrides[row.annotation_id] : row.assigned_device_id;
}

export function effectiveDevice(row: DeviceAttribution, override = row.assigned_device_id) {
  return override ?? row.origin_device_id;
}

export function annotationDeviceLabel(
  id: string | null, devices: Record<string, { label: string }>,
  unknown: string, deleted: string,
) {
  return id ? devices[id]?.label ?? deleted : unknown;
}

export interface AssignmentToast {
  text: string; failed?: number; undo?: Record<string, string | null>; target?: string | null;
}
export type ToastAction = { type: 'show'; toast: AssignmentToast } | { type: 'dismiss' };

export function assignmentToastReducer(_state: AssignmentToast | null, action: ToastAction): AssignmentToast | null {
  return action.type === 'show' ? action.toast : null;
}
