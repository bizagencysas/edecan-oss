export type DesktopPermissionStatus =
  | "granted"
  | "needs_action"
  | "unknown"
  | "not_required";

export interface DesktopPermission {
  id: string;
  title: string;
  description: string;
  level: "essential" | "recommended" | "on_demand" | "optional";
  status: DesktopPermissionStatus;
  action_label: string | null;
}

export interface DesktopPermissionsState {
  platform: "macos" | "windows" | "linux";
  application_name: string;
  application_path: string | null;
  permissions: DesktopPermission[];
}

export interface PermissionActionResult {
  permission_id: string;
  status: DesktopPermissionStatus;
  message: string;
}

export const PERMISSION_STATUS_COPY: Record<
  DesktopPermissionStatus,
  { label: string; tone: "success" | "warning" | "neutral" }
> = {
  granted: { label: "Permitido", tone: "success" },
  needs_action: { label: "Requiere atención", tone: "warning" },
  unknown: { label: "Se comprueba al usar", tone: "neutral" },
  not_required: { label: "Listo, no requiere permiso", tone: "success" },
};

export function mergePermissionAction(
  state: DesktopPermissionsState,
  result: PermissionActionResult,
): DesktopPermissionsState {
  return {
    ...state,
    permissions: state.permissions.map((permission) =>
      permission.id === result.permission_id
        ? { ...permission, status: result.status }
        : permission,
    ),
  };
}

export function readyPermissionCount(state: DesktopPermissionsState): number {
  return state.permissions.filter(
    (permission) => permission.status === "granted" || permission.status === "not_required",
  ).length;
}
