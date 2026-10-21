/**
 * A signed-in user's own reminders and schedules -- the personal, filtered
 * view behind the Schedules tab.
 *
 * Unlike operations.ts's scheduler_task_* wrappers (instance-wide, gated on
 * scheduler.manage), every call here only ever touches tasks owned by the
 * caller. That filter is enforced server-side per endpoint
 * (helpers/task_scheduler.assert_task_owner), not just by this module, which
 * is what makes it safe to open to any signed-in account.
 */

import { apiRequest } from "./client";
import { taskId, type ScheduledTask } from "./operations";

export type { ScheduledTask };
export { taskId };

export async function listMySchedules(timezone: string): Promise<ScheduledTask[]> {
  const result = await apiRequest<{ ok: boolean; tasks?: ScheduledTask[]; error?: string }>(
    "scheduler_my_tasks",
    { body: { timezone } },
  );
  if (!result.ok) throw new Error(result.error || "Could not load your schedules.");
  return result.tasks ?? [];
}

export function runMySchedule(id: string, timezone: string) {
  return apiRequest<{ success?: boolean; error?: string; message?: string }>(
    "scheduler_my_task_run",
    { body: { task_id: id, timezone } },
  );
}

export function pauseMySchedule(id: string, timezone: string) {
  return apiRequest<{ ok?: boolean; error?: string }>("scheduler_my_task_pause", {
    body: { task_id: id, timezone },
  });
}

export function resumeMySchedule(id: string, timezone: string) {
  return apiRequest<{ ok?: boolean; error?: string }>("scheduler_my_task_resume", {
    body: { task_id: id, timezone },
  });
}

export function deleteMySchedule(id: string, timezone: string) {
  return apiRequest<{ success?: boolean; error?: string }>("scheduler_my_task_delete", {
    body: { task_id: id, timezone },
  });
}
